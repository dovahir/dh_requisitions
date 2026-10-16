# -*- coding: utf-8 -*-
from collections import defaultdict
from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class RequisitionSupplyRequest(models.Model):
    """
    Solicitud de Suministro
    Gestiona el flujo completo de transferencias internas desde un almacén a almacenes de obra
    vinculados a requisiciones de empleados y materiales.
    """
    _name = 'requisition.supply.request'
    _description = 'Solicitud de Suministro'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        string='Folio',
        required=True,
        copy=False,
        readonly=True,
        index=True,
        default=lambda self: _('Nuevo'),
        tracking=True
    )
    state = fields.Selection([
        ('draft', 'En proceso'),
        ('delivery_created', 'Entrega creada'),
        ('in_transit', 'En tránsito'),
        ('done_exact', 'Trasladado'),
        ('done_partial', 'Parcialmente trasladado'),
        ('done_adjusted', 'Trasladado con ajustes'),
        ('delivery_returned', 'Entrega devuelta'),
        ('receipt_cancelled', 'Recepción Cancelada'),
        ('cancel', 'Cancelado'),
    ], string='Estado', default='draft', tracking=True, index=True)

    return_type = fields.Selection([
        ('total', 'Devolución total'),
        ('partial', 'Devolución parcial'),
    ], string='Tipo de Devolución', readonly=True, copy=False)

    delivery_alert = fields.Selection([
        ('exact', 'Exacto'),
        ('less', 'Faltante/Retirado'),
        ('more', 'Excedente/Añadido'),
        ('backorder_pending', 'Orden parcial pendiente'),
        ('cancelled', 'Cancelado'),
        ('receipt_error', 'Recepción Rechazada'),
        ('returned', 'Devuelta'),
        ('cancelled_backorder', 'Backorder cancelado'),
    ], string='Alerta de Entrega', readonly=True, copy=False)

    is_blocked = fields.Boolean(
        string='¿Bloqueado para Corte?',
        default=False,
        copy=False,
        tracking=True,
        help="Indica si la solicitud está en corte de almacén para impedir que se agreguen más partidas desde requisiciones."
    )
    has_transferred_lines = fields.Boolean(
        string='Tiene líneas transferidas',
        default=False,
        copy=False
    )
    has_cancelled_backorder = fields.Boolean(
        string='Backorder cancelado',
        default=False,
        copy=False
    )

    request_uid = fields.Many2one(
        'res.users',
        string='Solicitado por',
        default=lambda self: self.env.user,
        readonly=True,
        tracking=True
    )
    rejected_user_id = fields.Many2one(
        'res.users',
        string='Cancelado por',
        readonly=True,
        copy=False
    )
    date_request = fields.Datetime(
        string='Fecha de Solicitud',
        default=fields.Datetime.now,
        required=True,
        tracking=True
    )
    scheduled_date = fields.Datetime(
        string='Fecha de Entrega Programada',
        default=fields.Datetime.now,
        required=True,
        tracking=True,
        help="Fecha programada para recibir los productos en el almacén destino."
    )
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
        index=True
    )

    def _default_warehouse_origin_id(self):
        company = self.env.company
        return company.requisition_default_warehouse_origin_id or self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)

    def _default_picking_type_id(self):
        wh = self._default_warehouse_origin_id()
        if wh:
            return self.env['stock.picking.type'].search([
                ('warehouse_id', '=', wh.id),
                ('code', '=', 'internal'),
                ('is_requisition_operation', '=', True),
            ], limit=1)
        return False

    def _default_location_id(self):
        wh = self._default_warehouse_origin_id()
        if wh:
            picking_type = self._default_picking_type_id()
            if picking_type and picking_type.default_location_src_id:
                return picking_type.default_location_src_id
        return False

    # --- Origen ---
    warehouse_origin_id = fields.Many2one(
        'stock.warehouse',
        string='Almacén Origen (Base)',
        default=_default_warehouse_origin_id,
        check_company=True,
        tracking=True,
        help="Almacén central / base desde el cual se enviarán los productos."
    )
    picking_type_id = fields.Many2one(
        'stock.picking.type',
        string='Tipo de Operación (Origen)',
        default=_default_picking_type_id,
        domain="[('code', '=', 'internal'), ('is_requisition_operation', '=', True), ('warehouse_id', '=?', warehouse_origin_id)]",
        check_company=True,
        tracking=True,
        help="Tipo de operación interna de origen configurado para Requisiciones. "
             "Requiere configuración previa marcando 'Operación para Requisiciones' en Tipos de Operación."
    )
    location_id = fields.Many2one(
        'stock.location',
        string='Ubicación de Origen',
        default=_default_location_id,
        check_company=True,
        tracking=True
    )

    # --- Destino ---
    warehouse_dest_id = fields.Many2one(
        'stock.warehouse',
        string='Almacén Destino (Obra)',
        required=True,
        check_company=True,
        tracking=True,
        help="Almacén de obra que requiere el abastecimiento."
    )
    picking_type_dest_id = fields.Many2one(
        'stock.picking.type',
        string='Tipo de Operación (Destino)',
        domain="[('code', '=', 'internal'), ('warehouse_id', '=?', warehouse_dest_id)]",
        check_company=True,
        tracking=True,
        help="Tipo de operación interna con el que se recibirá el material en el almacén de destino."
    )
    location_dest_id = fields.Many2one(
        'stock.location',
        string='Ubicación de Destino',
        check_company=True,
        tracking=True
    )

    requisition_ids = fields.Many2many(
        'requisitions',
        'requisition_supply_rel',
        'supply_id',
        'requisition_id',
        string='Requisiciones Origen',
        help="Requisiciones vinculadas que originaron este requerimiento de suministro."
    )
    line_ids = fields.One2many(
        'requisition.supply.request.line',
        'request_id',
        string='Partidas de Suministro',
        copy=True
    )
    lines_count = fields.Integer(
        string='Total Partidas',
        compute='_compute_lines_count'
    )
    picking_ids = fields.One2many(
        'stock.picking',
        'supply_request_id',
        string='Albaranes de Transferencia'
    )
    outgoing_count = fields.Integer(
        string='Entregas',
        compute='_compute_transfer_count'
    )
    incoming_count = fields.Integer(
        string='Recepciones',
        compute='_compute_transfer_count'
    )
    notes = fields.Text(
        string='Observaciones Generales'
    )

    # -------------------------------------------------------------------------
    # ONCHANGES LOGÍSTICOS
    # -------------------------------------------------------------------------
    @api.onchange('picking_type_id')
    def _onchange_picking_type_id(self):
        if self.picking_type_id:
            self.location_id = self.picking_type_id.default_location_src_id
            self.warehouse_origin_id = self.picking_type_id.warehouse_id
            if hasattr(self.picking_type_id, 'default_dest_picking_type') and self.picking_type_id.default_dest_picking_type:
                self.picking_type_dest_id = self.picking_type_id.default_dest_picking_type
                self.location_dest_id = self.picking_type_dest_id.default_location_dest_id
                self.warehouse_dest_id = self.picking_type_dest_id.warehouse_id
        else:
            self.location_id = False

    @api.onchange('picking_type_dest_id')
    def _onchange_picking_type_dest_id(self):
        if self.picking_type_dest_id:
            self.location_dest_id = self.picking_type_dest_id.default_location_dest_id
            self.warehouse_dest_id = self.picking_type_dest_id.warehouse_id
        else:
            self.location_dest_id = False

    @api.onchange('warehouse_origin_id')
    def _onchange_warehouse_origin_id(self):
        if self.warehouse_origin_id:
            picking_type = self.env['stock.picking.type'].search([
                ('warehouse_id', '=', self.warehouse_origin_id.id),
                ('code', '=', 'internal'),
                ('is_requisition_operation', '=', True),
            ], limit=1)
            if picking_type:
                self.picking_type_id = picking_type
                self.location_id = picking_type.default_location_src_id
                if picking_type.default_dest_picking_type:
                    self.picking_type_dest_id = picking_type.default_dest_picking_type
                    self.location_dest_id = self.picking_type_dest_id.default_location_dest_id
                    self.warehouse_dest_id = self.picking_type_dest_id.warehouse_id
            else:
                self.picking_type_id = False
                self.location_id = False

    @api.onchange('warehouse_dest_id')
    def _onchange_warehouse_dest_id(self):
        if self.warehouse_dest_id and not self.picking_type_dest_id:
            picking_type = self.env['stock.picking.type'].search([
                ('warehouse_id', '=', self.warehouse_dest_id.id),
                ('code', '=', 'internal'),
            ], limit=1)
            if picking_type:
                self.picking_type_dest_id = picking_type
                self.location_dest_id = picking_type.default_location_dest_id
            else:
                self.location_dest_id = False

    # -------------------------------------------------------------------------
    # COMPUTED FIELDS
    # -------------------------------------------------------------------------
    @api.depends('line_ids')
    def _compute_lines_count(self):
        for rec in self:
            rec.lines_count = len(rec.line_ids)

    @api.depends('picking_ids', 'picking_ids.location_id', 'picking_ids.location_dest_id', 'location_id', 'location_dest_id')
    def _compute_transfer_count(self):
        for rec in self:
            out_count = 0
            in_count = 0
            for p in rec.picking_ids:
                if rec.location_id and p.location_id.id == rec.location_id.id:
                    out_count += 1
                if rec.location_dest_id and p.location_dest_id.id == rec.location_dest_id.id:
                    in_count += 1
            rec.outgoing_count = out_count
            rec.incoming_count = in_count

    # -------------------------------------------------------------------------
    # CRUD Y COPIA
    # -------------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('requisition.supply.request') or _('Nuevo')
        return super().create(vals_list)

    def copy(self, default=None):
        default = dict(default or {})
        default.update({
            'requisition_ids': [(5, 0, 0)],
            'state': 'draft',
            'delivery_alert': False,
            'is_blocked': False,
            'return_type': False,
            'rejected_user_id': False,
        })
        return super().copy(default)

    # -------------------------------------------------------------------------
    # ACCIONES DE WORKFLOW Y ESTADOS
    # -------------------------------------------------------------------------
    def action_button_draft(self):
        """Permite regresar a borrador cuando la entrega fue cancelada o devuelta."""
        self.ensure_one()
        if self.state not in ('delivery_returned', 'cancel', 'receipt_cancelled'):
            raise UserError(_("Solo se puede restablecer a borrador si la solicitud está cancelada, devuelta o con recepción rechazada."))
        self.write({
            'state': 'draft',
            'delivery_alert': False,
            'rejected_user_id': False,
        })
        self.message_post(
            body=Markup(_("Solicitud de Suministro restablecida a estado <strong>Borrador</strong>.")),
            subtype_xmlid="mail.mt_note"
        )

    def action_button_is_blocked(self):
        """Conmuta el estado de bloqueo para corte de almacén."""
        for rec in self:
            rec.is_blocked = not rec.is_blocked
            status_txt = _("Bloqueada (Corte realizado)") if rec.is_blocked else _("Desbloqueada (Disponible para requisiciones)")
            rec.message_post(
                body=Markup(_("Estado de corte modificado: <strong>%s</strong>.")) % escape(status_txt),
                subtype_xmlid="mail.mt_note"
            )

    def action_open_block_wizard(self):
        """Abre el asistente de corte de almacén."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Hacer Corte de Almacén'),
            'res_model': 'requisition.supply.request.block.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_request_id': self.id},
        }

    def action_button_cancel(self):
        """Abre el asistente de cancelación con motivo."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cancelar Solicitud de Suministro'),
            'res_model': 'requisition.supply.request.cancel.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_request_id': self.id},
        }

    # -------------------------------------------------------------------------
    # GENERACIÓN Y GESTIÓN DE TRANSFERENCIAS DE STOCK
    # -------------------------------------------------------------------------
    def action_create_delivery(self):
        """
        Crea el albarán de entrega desde el Almacén Origen hacia la Ubicación de Tránsito.
        Maneja asignación de series/lotes seleccionados y enlaza con supply.chain.allocation.
        """
        self.ensure_one()
        if not self.line_ids:
            raise ValidationError(_("La solicitud de suministro no contiene partidas. Agregue productos antes de crear la entrega."))

        # 1. Resolver ubicación de tránsito
        transit_location = (
            self.picking_type_id.default_location_dest_id or
            self.picking_type_dest_id.default_location_src_id
        )
        if not transit_location:
            raise ValidationError(_(
                "No se ha definido una ubicación de tránsito válida.\n"
                "Configure 'Ubicación de destino predeterminada' en el tipo de operación de origen, "
                "o 'Ubicación de origen predeterminada' en el tipo de operación de recepción destino."
            ))

        if not self.location_id:
            raise ValidationError(_("No se ha definido la ubicación de origen en la solicitud de suministro."))
        if not self.location_dest_id:
            raise ValidationError(_("No se ha definido la ubicación de destino en la solicitud de suministro."))

        # 2. Construir valores para el picking de salida (Origen -> Tránsito)
        delivery_vals = {
            'picking_type_id': self.picking_type_id.id,
            'location_id': self.location_id.id,
            'location_dest_id': transit_location.id,
            'supply_request_id': self.id,
            'origin': self.name,
            'company_id': self.company_id.id,
            'move_ids': [],
        }

        for line in self.line_ids:
            if line.product_qty <= 0:
                continue
            move_val = {
                'description_picking': line.notes or line.product_id.display_name,
                'product_id': line.product_id.id,
                'product_uom_qty': line.product_qty,
                'product_uom': line.product_uom_id.id,
                'location_id': self.location_id.id,
                'location_dest_id': transit_location.id,
                'supply_request_line_id': line.id,
                'company_id': self.company_id.id,
            }
            if line.requisition_line_id:
                move_val['requisition_line_id'] = line.requisition_line_id.id
            delivery_vals['move_ids'].append((0, 0, move_val))

        if not delivery_vals['move_ids']:
            raise UserError(_("No hay partidas con cantidad mayor a cero para generar el albarán."))

        delivery_picking = self.env['stock.picking'].create(delivery_vals)

        # 3. Asignar números de serie / lotes preseleccionados
        self._assign_serial_numbers_to_delivery(delivery_picking)

        # 4. Confirmar el picking para reservar existencias
        delivery_picking.action_confirm()

        # 5. Sincronizar o crear asignaciones transversales en supply.chain.allocation
        self._sync_supply_chain_allocations(delivery_picking)

        self.write({
            'state': 'delivery_created',
            'has_cancelled_backorder': False,
            'date_request': fields.Datetime.now()
        })

        self.message_post(
            body=Markup(_(
                "Se generó el albarán de entrega interna <strong>%s</strong> hacia la ubicación de tránsito <em>%s</em>."
            )) % (escape(delivery_picking.name), escape(transit_location.display_name)),
            subtype_xmlid="mail.mt_note"
        )

        return self.action_view_outgoing()

    def _assign_serial_numbers_to_delivery(self, picking):
        """Asigna los lotes o números de serie seleccionados en la solicitud a los movimientos del picking."""
        for line in self.line_ids:
            if line.has_tracking not in ('serial', 'lot') or not line.lot_ids:
                continue

            move = picking.move_ids.filtered(lambda m: m.supply_request_line_id.id == line.id)
            if not move:
                continue
            move = move[0]

            move.move_line_ids.unlink()
            for lot in line.lot_ids:
                qty_line = 1.0 if line.has_tracking == 'serial' else (move.product_uom_qty / len(line.lot_ids))
                self.env['stock.move.line'].create({
                    'move_id': move.id,
                    'picking_id': picking.id,
                    'product_id': line.product_id.id,
                    'lot_id': lot.id,
                    'quantity': qty_line,
                    'location_id': move.location_id.id,
                    'location_dest_id': move.location_dest_id.id,
                    'company_id': self.company_id.id,
                })

    def _sync_supply_chain_allocations(self, delivery_picking):
        """Vincula los movimientos de entrega con la matriz supply.chain.allocation."""
        Allocation = self.env['supply.chain.allocation']
        for move in delivery_picking.move_ids:
            req_line = move.supply_request_line_id
            if not req_line:
                continue

            # Buscar asignación existente para esta línea de solicitud de suministro
            existing_alloc = Allocation.search([
                ('supply_request_line_id', '=', req_line.id),
                ('state', '!=', 'cancel'),
            ], limit=1)

            if existing_alloc:
                existing_alloc.write({
                    'stock_move_id': move.id,
                    'state': 'in_progress',
                })
            else:
                supply_type = 'consumption'
                if req_line.requisition_line_id and hasattr(req_line.requisition_line_id, 'supply_type'):
                    supply_type = req_line.requisition_line_id.supply_type or 'consumption'

                Allocation.create({
                    'company_id': self.company_id.id,
                    'product_id': req_line.product_id.id,
                    'product_uom_id': req_line.product_uom_id.id,
                    'requested_qty': req_line.product_qty,
                    'delivered_qty': 0.0,
                    'supply_type': supply_type,
                    'stock_move_id': move.id,
                    'supply_request_line_id': req_line.id,
                    'requisition_line_id': req_line.requisition_line_id.id if req_line.requisition_line_id else False,
                    'state': 'in_progress',
                })

    # -------------------------------------------------------------------------
    # AUTOMATIZACIÓN DE RECEPCIÓN Y MONITOREO DE ALBARANES
    # -------------------------------------------------------------------------
    def _process_picking_validation(self, picking):
        """
        Disparado al validarse un picking vinculado:
        - Si es una entrega a tránsito (no devolución), genera automáticamente la recepción en destino.
        - Recalcula el estado general de la Solicitud de Suministro.
        """
        self.ensure_one()
        is_delivery = (
            picking.location_id.id == self.location_id.id and
            picking.location_dest_id.id != self.location_dest_id.id
        )
        if is_delivery and not picking._is_return_picking():
            # Evitar recepciones duplicadas para el mismo envío
            existing_receipt = self.env['stock.picking'].search([
                ('supply_request_id', '=', self.id),
                ('origin', 'ilike', picking.name),
                ('location_id', '=', picking.location_dest_id.id),
                ('location_dest_id', '=', self.location_dest_id.id),
            ], limit=1)
            if not existing_receipt:
                self._create_receipt_picking(picking)

        self._compute_overall_state()

    def _create_receipt_picking(self, delivery_picking):
        """Crea el albarán de recepción (Tránsito -> Obra) con base en lo despachado en la entrega."""
        self.ensure_one()
        receipt_vals = {
            'picking_type_id': self.picking_type_dest_id.id,
            'location_id': delivery_picking.location_dest_id.id,
            'location_dest_id': self.location_dest_id.id,
            'supply_request_id': self.id,
            'source_transfer_id': delivery_picking.id,
            'origin': f"Recepción de: {delivery_picking.name} (Solicitud {self.name})",
            'company_id': self.company_id.id,
            'move_ids': [],
        }

        for move in delivery_picking.move_ids.filtered(lambda m: m.state == 'done'):
            qty_done = move.quantity
            if qty_done <= 0:
                continue

            receipt_vals['move_ids'].append((0, 0, {
                'description_picking': move.description_picking or move.product_id.display_name,
                'product_id': move.product_id.id,
                'product_uom_qty': qty_done,
                'product_uom': move.product_uom.id,
                'location_id': delivery_picking.location_dest_id.id,
                'location_dest_id': self.location_dest_id.id,
                'supply_request_line_id': move.supply_request_line_id.id if move.supply_request_line_id else False,
                'company_id': self.company_id.id,
            }))

        if not receipt_vals['move_ids']:
            return

        receipt_picking = self.env['stock.picking'].create(receipt_vals)

        # Traspaso de series o lotes completados de la entrega a la recepción
        for out_move in delivery_picking.move_ids.filtered(lambda m: m.state == 'done' and m.product_id.tracking in ('serial', 'lot')):
            in_move = receipt_picking.move_ids.filtered(
                lambda m: m.product_id == out_move.product_id and m.supply_request_line_id == out_move.supply_request_line_id
            )
            if not in_move:
                continue
            in_move = in_move[0]

            if out_move.move_line_ids:
                in_move.move_line_ids.unlink()
                for out_line in out_move.move_line_ids:
                    self.env['stock.move.line'].create({
                        'move_id': in_move.id,
                        'picking_id': receipt_picking.id,
                        'product_id': out_line.product_id.id,
                        'lot_id': out_line.lot_id.id if out_line.lot_id else False,
                        'quantity': out_line.quantity,
                        'location_id': in_move.location_id.id,
                        'location_dest_id': in_move.location_dest_id.id,
                        'company_id': self.company_id.id,
                    })

        receipt_picking.action_confirm()
        receipt_picking.action_assign()

        # Mensajes cruzados en los chatters de ambos albaranes
        msg_delivery = Markup(_(
            "Se ha generado el albarán de recepción en obra: "
            "<a href='#' data-oe-model='stock.picking' data-oe-id='%s'>%s</a>."
        )) % (receipt_picking.id, escape(receipt_picking.name))
        delivery_picking.message_post(body=msg_delivery)

        msg_receipt = Markup(_(
            "Este albarán fue originado automáticamente por el despacho desde almacén origen: "
            "<a href='#' data-oe-model='stock.picking' data-oe-id='%s'>%s</a>."
        )) % (delivery_picking.id, escape(delivery_picking.name))
        receipt_picking.message_post(body=msg_receipt)

        self.message_post(
            body=Markup(_(
                "Se creó automáticamente la recepción en destino <strong>%s</strong> para el albarán <em>%s</em>."
            )) % (escape(receipt_picking.name), escape(delivery_picking.name)),
            subtype_xmlid="mail.mt_note"
        )

    def _process_picking_cancel(self, picking):
        """Maneja la cancelación de transferencias vinculadas a la solicitud."""
        self.ensure_one()
        if picking._is_return_picking():
            picking.message_post(
                body=Markup(_(
                    "La devolución del albarán <strong>%s</strong> ha sido cancelada. La solicitud de suministro no se ve afectada."
                )) % escape(picking.name or '')
            )
        else:
            self.write({
                'has_cancelled_backorder': True,
                'state': 'done_adjusted',
                'delivery_alert': 'cancelled_backorder',
            })
            self.message_post(
                body=Markup(_("El albarán o backorder <strong>%s</strong> fue cancelado.")) % escape(picking.name or '')
            )
        self._compute_overall_state()

    def _compute_overall_state(self):
        """
        Recalcula el estado consolidado de la Solicitud de Suministro
        evaluando entregas, recepciones, devoluciones y cancelaciones de manera vectorizada.
        """
        for req in self:
            pickings = req.picking_ids

            if req.has_cancelled_backorder:
                req.state = 'done_adjusted'
                req.delivery_alert = 'cancelled_backorder'
                continue

            if not pickings:
                req.state = 'draft'
                req.delivery_alert = False
                continue

            deliveries = pickings.filtered(
                lambda p: p.location_id.id == req.location_id.id and p.location_dest_id.id != req.location_dest_id.id
            )
            receipts = pickings.filtered(
                lambda p: p.location_dest_id.id == req.location_dest_id.id
            )

            # 1. Entregas activas pendientes de validar
            active_deliveries = deliveries.filtered(lambda p: p.state not in ('done', 'cancel'))
            if active_deliveries:
                has_done_delivery = any(d.state == 'done' for d in deliveries)
                if has_done_delivery:
                    req.state = 'done_partial'
                    req.delivery_alert = 'backorder_pending'
                else:
                    req.state = 'delivery_created'
                    req.delivery_alert = False
                continue

            # 2. Entregas completadas
            done_deliveries = deliveries.filtered(lambda p: p.state == 'done')
            if done_deliveries:
                active_receipts = receipts.filtered(lambda p: p.state not in ('done', 'cancel'))
                if active_receipts:
                    req.state = 'in_transit'
                    req.delivery_alert = False
                    continue

                done_receipts = receipts.filtered(lambda p: p.state == 'done')
                if done_receipts:
                    total_requested = sum(req.line_ids.mapped('product_qty'))
                    total_received = sum(
                        done_receipts.mapped('move_ids').filtered(lambda m: m.state == 'done').mapped('quantity')
                    )
                    if total_received == total_requested:
                        req.state = 'done_exact'
                        req.delivery_alert = 'exact'
                    elif total_received < total_requested:
                        req.state = 'done_partial'
                        req.delivery_alert = 'less'
                    else:
                        req.state = 'done_adjusted'
                        req.delivery_alert = 'more'
                    continue

                if receipts and all(p.state == 'cancel' for p in receipts):
                    req.state = 'receipt_cancelled'
                    req.delivery_alert = 'receipt_error'
                    continue

                if not receipts:
                    req.state = 'in_transit'
                    req.delivery_alert = False
                    continue

            # 3. Todos los albaranes cancelados
            if all(p.state == 'cancel' for p in pickings):
                req.state = 'cancel'
                req.delivery_alert = 'cancelled'
                continue

            # 4. Devoluciones concluidas
            return_pickings = pickings.filtered(lambda p: p._is_return_picking() and p.state == 'done')
            if return_pickings:
                orig_qty = sum(deliveries.mapped('move_ids.product_uom_qty'))
                ret_qty = sum(return_pickings.mapped('move_ids').filtered(lambda m: m.state == 'done').mapped('quantity'))
                req.return_type = 'total' if ret_qty >= orig_qty else 'partial'
                req.state = 'delivery_returned'
                req.delivery_alert = 'returned'
                continue

    # -------------------------------------------------------------------------
    # SMART BUTTONS / ACCIONES DE VISTA
    # -------------------------------------------------------------------------
    def action_view_outgoing(self):
        """Abre la lista de albaranes de entrega de esta solicitud."""
        self.ensure_one()
        return {
            'name': _('Entregas de Suministro'),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('supply_request_id', '=', self.id), ('location_id', '=', self.location_id.id)],
            'context': {'default_supply_request_id': self.id, 'create': False},
        }

    def action_view_incoming(self):
        """Abre la lista de albaranes de recepción en destino de esta solicitud."""
        self.ensure_one()
        return {
            'name': _('Recepciones en Obra'),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('supply_request_id', '=', self.id), ('location_dest_id', '=', self.location_dest_id.id)],
            'context': {'default_supply_request_id': self.id, 'create': False},
        }

    @api.ondelete(at_uninstall=False)
    def _unlink_except_draft_or_cancel(self):
        for rec in self:
            if rec.state not in ('draft', 'cancel'):
                raise UserError(_(
                    "No se pueden eliminar solicitudes de suministro que ya han iniciado transferencias o están completadas. "
                    "Solo se permite eliminar solicitudes en estado Borrador o Cancelado."
                ))
