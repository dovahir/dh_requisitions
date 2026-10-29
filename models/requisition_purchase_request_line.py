# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare
from markupsafe import Markup, escape


_LINE_STATES = [
    ('pending', 'Pendiente'),
    ('in_progress', 'En Cotización'),
    ('to_receive', 'Por Recepcionar'),
    ('partially_purchased', 'Parcialmente Comprado'),
    ('purchased', 'Comprado'),
    ('cancel', 'Cancelado'),
]

_PURCHASE_STATES = [
    ('draft', 'RFQ'),
    ('sent', 'RFQ Enviado'),
    ('to approve', 'Por Aprobar'),
    ('purchase', 'Orden de Compra'),
    ('done', 'Bloqueado'),
    ('cancel', 'Cancelado'),
]


class RequisitionPurchaseRequestLine(models.Model):
    """
    Línea de Solicitud de Compra generada desde una requisición de empleado
    para seguimiento por el departamento de compras, enlazada a supply.chain.allocation.
    """
    _name = 'requisition.purchase.request.line'
    _description = 'Línea de Solicitud de Compra'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    active = fields.Boolean(
        string='Activo',
        default=True,
        tracking=True
    )
    name = fields.Char(
        string='Descripción / Referencia',
        compute='_compute_name',
        store=True,
        readonly=False
    )
    requisition_id = fields.Many2one(
        'requisitions',
        string='Requisición Origen',
        store=True,
        readonly=True,
        index=True
    )
    requisition_line_id = fields.Many2one(
        'requisitions.line',
        string='Línea de Requisición',
        store=True,
        ondelete='cascade',
        index=True
    )
    product_id = fields.Many2one(
        'product.product',
        string='Producto',
        required=True,
        check_company=True,
        tracking=True
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='Unidad de Medida',
        related='product_id.uom_id',
        store=True,
        readonly=True
    )
    product_qty = fields.Float(
        string='Cantidad Solicitada',
        required=True,
        digits='Product Unit of Measure',
        tracking=True
    )
    date_required = fields.Date(
        string='Fecha Requerida',
        related='requisition_id.date_required',
        store=True,
        readonly=True
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Almacén Destino',
        related='requisition_id.warehouse_id',
        store=True,
        readonly=True
    )
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        related='requisition_id.company_id',
        store=True,
        readonly=True
    )
    priority = fields.Selection(
        [
            ('normal', 'Normal'),
            ('urgent', 'Urgente'),
        ],
        string='Prioridad',
        default='normal',
        tracking=True
    )
    requester_name = fields.Char(
        string='Solicitado por',
        compute='_compute_requester_name',
        store=True,
        readonly=False,
        index=True
    )
    notes = fields.Text(
        string='Notas / Especificaciones'
    )
    requisition_notes = fields.Text(
        related='requisition_id.notes',
        string='Notas de la Requisición',
        readonly=True,
        store=True,
        help="Notas generales heredadas de la requisición de origen."
    )
    requisition_line_notes = fields.Char(
        related='requisition_line_id.note',
        string='Notas de la Partida',
        readonly=True,
        store=True,
        help="Notas u observaciones específicas heredadas de la partida de la requisición."
    )

    state = fields.Selection(
        _LINE_STATES,
        string='Estado',
        default='pending',
        tracking=True,
        index=True,
        compute='_compute_state',
        store=True,
        readonly=False
    )

    # =========================================================================
    # INTEGRACIÓN CON LA MATRIZ DE ASIGNACIÓN REQUISITION.ALLOCATION
    # =========================================================================
    allocation_ids = fields.One2many(
        'requisition.allocation',
        'purchase_request_line_id',
        string='Asignaciones de Requisición'
    )
    allocation_id = fields.Many2one(
        'requisition.allocation',
        string='Asignación Principal',
        compute='_compute_allocation_id',
        store=True,
        readonly=True,
        index=True
    )
    origin_supply_request_line_id = fields.Many2one(
        'requisition.supply.request.line',
        string='Partida de Suministro que Disparó la Compra',
        readonly=True,
        copy=False,
        help="Partida de la solicitud de suministro de origen desde la cual se solicitó la compra por falta de existencias."
    )
    supply_request_line_id = fields.Many2one(
        'requisition.supply.request.line',
        string='Partida de Suministro Vinculada (Envío)',
        compute='_compute_supply_request_line_id',
        store=True,
        readonly=True,
        help='Partida de Solicitud de Suministro enlazada a través de la asignación unificada para envío a obra.'
    )
    supply_request_id = fields.Many2one(
        'requisition.supply.request',
        string='Solicitud de Suministro',
        related='supply_request_line_id.request_id',
        store=True,
        readonly=True,
        help='Folio de la Solicitud de Suministro vinculada.'
    )
    cancel_reason = fields.Text(
        string='Motivo de Cancelación',
        readonly=True,
        copy=False,
        tracking=True,
        help="Justificación de la cancelación de esta partida de compra."
    )
    cancelled_by_id = fields.Many2one(
        'res.users',
        string='Cancelado por',
        readonly=True,
        copy=False,
        help="Usuario que realizó la cancelación de esta partida."
    )
    date_cancelled = fields.Datetime(
        string='Fecha de Cancelación',
        readonly=True,
        copy=False,
        help="Fecha y hora en que se canceló la partida."
    )

    # Relaciones con Órdenes y Líneas de Compra
    purchase_lines = fields.Many2many(
        'purchase.order.line',
        string='Líneas de Orden de Compra',
        compute='_compute_purchase_lines',
        store=True,
        readonly=True
    )
    purchase_line_id = fields.Many2one(
        'purchase.order.line',
        string='Línea de Compra Reciente',
        compute='_compute_purchase_line_id',
        store=True,
        readonly=True,
        index=True
    )
    purchase_order_ids = fields.Many2many(
        'purchase.order',
        string='Órdenes de Compra',
        compute='_compute_purchase_order_ids',
        store=True,
        readonly=True
    )
    purchase_state = fields.Selection(
        _PURCHASE_STATES,
        string='Estado de Compra',
        compute='_compute_purchase_state',
        store=True
    )

    # Cantidades en Flujo
    qty_in_progress = fields.Float(
        string='Cant. en Cotización',
        digits='Product Unit of Measure',
        compute='_compute_quantities',
        store=True,
        help='Cantidad activa asignada a solicitudes de cotización (RFQs en borrador o enviadas)'
    )
    qty_to_receive = fields.Float(
        string='Pendiente Recepción',
        digits='Product Unit of Measure',
        compute='_compute_quantities',
        store=True,
        help='Cantidad en órdenes de compra confirmadas pendiente de ingresar al almacén'
    )
    qty_done = fields.Float(
        string='Cant. Recibida',
        digits='Product Unit of Measure',
        compute='_compute_quantities',
        store=True,
        help='Cantidad efectivamente recibida en almacén o entregada para esta solicitud'
    )
    qty_cancelled = fields.Float(
        string='Cant. Cancelada',
        digits='Product Unit of Measure',
        default=0.0,
        readonly=True,
        help='Cantidad que ha sido cancelada definitivamente en compras o requisiciones'
    )
    pending_qty_to_buy = fields.Float(
        string='Pendiente Comprar',
        digits='Product Unit of Measure',
        compute='_compute_quantities',
        store=True,
        help='Cantidad aún no colocada en cotización ni comprada'
    )

    picking_count = fields.Integer(
        string='Recepciones',
        compute='_compute_picking_count'
    )
    purchase_order_count = fields.Integer(
        string='N° RFQs/POs',
        compute='_compute_purchase_order_count'
    )

    # =========================================================================
    # CÓMPUTOS Y DEPENDENCIAS
    # =========================================================================
    @api.depends('product_id', 'requisition_id')
    def _compute_name(self):
        for rec in self:
            prod_name = rec.product_id.display_name or _('Producto')
            req_name = rec.requisition_id.name or _('Requisición')
            rec.name = f"{req_name} - {prod_name}"

    @api.depends('requisition_id.employee_id', 'requisition_id.create_uid')
    def _compute_requester_name(self):
        for rec in self:
            if rec.requisition_id and rec.requisition_id.employee_id:
                rec.requester_name = rec.requisition_id.employee_id.name
            elif rec.requisition_id and rec.requisition_id.create_uid:
                rec.requester_name = rec.requisition_id.create_uid.name
            elif not rec.requester_name:
                rec.requester_name = self.env.user.name

    @api.depends('allocation_ids')
    def _compute_allocation_id(self):
        for rec in self:
            rec.allocation_id = rec.allocation_ids[:1].id if rec.allocation_ids else False

    @api.depends('allocation_ids.supply_request_line_id')
    def _compute_supply_request_line_id(self):
        for rec in self:
            sr_lines = rec.allocation_ids.mapped('supply_request_line_id').filtered(bool)
            rec.supply_request_line_id = sr_lines[:1].id if sr_lines else False

    @api.depends('allocation_ids.purchase_line_id')
    def _compute_purchase_lines(self):
        for rec in self:
            po_lines = rec.allocation_ids.mapped('purchase_line_id').filtered(bool)
            rec.purchase_lines = [(6, 0, po_lines.ids)]

    @api.depends('purchase_lines')
    def _compute_purchase_line_id(self):
        for rec in self:
            rec.purchase_line_id = rec.purchase_lines[:1].id if rec.purchase_lines else False

    @api.depends('purchase_lines.order_id')
    def _compute_purchase_order_ids(self):
        for rec in self:
            orders = rec.purchase_lines.mapped('order_id').filtered(bool)
            rec.purchase_order_ids = [(6, 0, orders.ids)]

    @api.depends('purchase_order_ids')
    def _compute_purchase_order_count(self):
        for rec in self:
            rec.purchase_order_count = len(rec.purchase_order_ids)

    @api.depends('purchase_lines.state', 'purchase_lines.order_id.state')
    def _compute_purchase_state(self):
        for rec in self:
            if not rec.purchase_lines:
                rec.purchase_state = False
                continue
            states = rec.purchase_lines.mapped('state')
            if 'done' in states:
                rec.purchase_state = 'done'
            elif 'purchase' in states:
                rec.purchase_state = 'purchase'
            elif 'to approve' in states:
                rec.purchase_state = 'to approve'
            elif 'sent' in states:
                rec.purchase_state = 'sent'
            elif 'draft' in states:
                rec.purchase_state = 'draft'
            else:
                rec.purchase_state = states[0] if states else False

    @api.depends(
        'purchase_lines.qty_received',
        'purchase_lines.product_qty',
        'purchase_lines.state',
        'purchase_lines.order_id.state',
        'product_qty',
        'qty_cancelled'
    )
    def _compute_quantities(self):
        for rec in self:
            active_po_lines = rec.purchase_lines.filtered(
                lambda l: l.state != 'cancel' and l.order_id.state not in ('cancel',)
            )

            def _po_line_qty(po_line, qty):
                po_uom = getattr(po_line, 'product_uom_id', False) or getattr(po_line, 'product_uom', False)
                if po_uom and rec.product_uom_id and po_uom != rec.product_uom_id:
                    return po_uom._compute_quantity(qty, rec.product_uom_id)
                return qty

            # 1. Cantidad recibida físicamente ÚNICAMENTE desde recepciones reales de órdenes de compra
            rec.qty_done = sum(
                _po_line_qty(l, l.qty_received)
                for l in active_po_lines
            )

            # 2. Cantidad en órdenes confirmadas pendiente de recepción
            confirmed_po_lines = active_po_lines.filtered(
                lambda l: l.order_id.state in ('purchase', 'done')
            )
            rec.qty_to_receive = sum(
                _po_line_qty(l, max(0.0, l.product_qty - l.qty_received))
                for l in confirmed_po_lines
            )

            # 3. Cantidad en cotización / RFQ abierta (draft, sent, to approve)
            rfq_lines = active_po_lines.filtered(
                lambda l: l.order_id.state in ('draft', 'sent', 'to approve')
            )
            rec.qty_in_progress = sum(
                _po_line_qty(l, l.product_qty)
                for l in rfq_lines
            )

            # 4. Cantidad pendiente de comprar
            rec.pending_qty_to_buy = max(
                0.0,
                rec.product_qty - rec.qty_done - rec.qty_to_receive - rec.qty_in_progress - rec.qty_cancelled
            )

    @api.depends('allocation_ids.stock_move_id.picking_id', 'allocation_ids.purchase_stock_move_id.picking_id')
    def _compute_picking_count(self):
        for rec in self:
            pickings = rec.allocation_ids.mapped('stock_move_id.picking_id') | \
                       rec.allocation_ids.mapped('purchase_stock_move_id.picking_id')
            rec.picking_count = len(pickings.filtered(bool))

    @api.depends('qty_done', 'qty_to_receive', 'qty_in_progress', 'qty_cancelled', 'product_qty')
    def _compute_state(self):
        for rec in self:
            if rec.state == 'cancel':
                continue

            rounding = rec.product_uom_id.rounding or 0.01
            is_done = float_compare(rec.qty_done, rec.product_qty, precision_rounding=rounding) >= 0
            is_partial = float_compare(rec.qty_done, 0.0, precision_rounding=rounding) > 0 and not is_done
            has_to_receive = float_compare(rec.qty_to_receive, 0.0, precision_rounding=rounding) > 0
            has_in_progress = float_compare(rec.qty_in_progress, 0.0, precision_rounding=rounding) > 0 or bool(rec.purchase_lines)

            # Para servicios, si todas las órdenes de compra asociadas están confirmadas o bloqueadas
            if rec.product_id.type == 'service' and rec.purchase_lines:
                active_po_lines = rec.purchase_lines.filtered(lambda l: l.state != 'cancel')
                if active_po_lines and all(l.order_id.state in ('purchase', 'done') for l in active_po_lines):
                    is_done = True

            if is_done:
                rec.state = 'purchased'
            elif is_partial:
                rec.state = 'partially_purchased'
            elif has_to_receive:
                rec.state = 'to_receive'
            elif has_in_progress:
                rec.state = 'in_progress'
            else:
                rec.state = 'pending'

    # =========================================================================
    # RESTRICCIONES Y INTEGRIDAD
    # =========================================================================
    @api.constrains('product_qty')
    def _check_product_qty(self):
        for rec in self:
            if rec.product_qty <= 0:
                raise ValidationError(_("La cantidad a comprar debe ser mayor a cero."))

    @api.ondelete(at_uninstall=False)
    def _unlink_except_confirmed_or_purchased(self):
        for rec in self:
            if rec.state not in ('pending', 'cancel') and rec.purchase_lines:
                raise UserError(
                    _("No se puede eliminar la solicitud '%s' porque ya cuenta con órdenes de compra asociadas.")
                    % rec.display_name
                )

    # =========================================================================
    # ACCIONES DE NEGOCIO Y SMART BUTTONS
    # =========================================================================
    def action_open_purchase_orders(self):
        """Abre las órdenes de compra / cotizaciones vinculadas a esta solicitud."""
        self.ensure_one()
        orders = self.purchase_order_ids
        action = self.env['ir.actions.actions']._for_xml_id('purchase.purchase_rfq')
        if len(orders) > 1:
            action['domain'] = [('id', 'in', orders.ids)]
        elif orders:
            action['views'] = [(self.env.ref('purchase.purchase_order_form').id, 'form')]
            action['view_mode'] = 'form'
            action['res_id'] = orders.id
        return action

    def action_open_pickings(self):
        """Abre los albaranes de recepción o entrega asociados a través de asignaciones."""
        self.ensure_one()
        pickings = self.allocation_ids.mapped('stock_move_id.picking_id') | \
                   self.allocation_ids.mapped('purchase_stock_move_id.picking_id')
        pickings = pickings.filtered(bool)
        action = self.env['ir.actions.actions']._for_xml_id('stock.action_picking_tree_all')
        if len(pickings) > 1:
            action['domain'] = [('id', 'in', pickings.ids)]
        elif pickings:
            action['views'] = [(self.env.ref('stock.view_picking_form').id, 'form')]
            action['view_mode'] = 'form'
            action['res_id'] = pickings.id
        return action

    has_pending_supply = fields.Boolean(
        string="Registrado en Suministro",
        compute='_compute_has_pending_supply',
        help="Indica si esta partida ya cuenta con una línea de suministro o pendiente de envío."
    )

    @api.depends('allocation_ids.supply_request_line_id')
    def _compute_has_pending_supply(self):
        for rec in self:
            rec.has_pending_supply = bool(rec.allocation_ids.mapped('supply_request_line_id'))

    def _check_purchase_request_permissions(self):
        """Verifica que el usuario pertenezca al área de compras o a Administrador."""
        is_purchase = (self.env.user.has_group('purchase.group_purchase_user') or
                       self.env.user.has_group('purchase.group_purchase_manager'))
        is_req_admin = self.env.user.has_group('dh_requisitions.group_requisitions_manager')
        if not (is_purchase or is_req_admin):
            raise UserError(_("Se requiere pertenecer al área de Compras o a Administrador para gestionar solicitudes de compra."))

    def _check_purchase_manager_permissions(self):
        """Verifica que el usuario pertenezca a Administradores de Compras o Administrador."""
        is_purchase_manager = (
            self.env.user.has_group('purchase.group_purchase_manager') or
            self.env.user.has_group('dh_requisitions.group_requisitions_manager')
        )
        if not is_purchase_manager:
            raise UserError(_("Se requiere pertenecer a Administradores de Compras o Administrador para realizar esta acción."))

    def unlink(self):
        """Impide la eliminación de partidas de compra a usuarios que no sean administradores."""
        self._check_purchase_manager_permissions()
        for rec in self:
            if rec.state != 'pending':
                raise UserError(_("Solo se pueden eliminar solicitudes de compra en estado 'Pendiente'."))
        return super().unlink()

    def action_add_to_pending_supply(self):
        """Permite al personal de almacén base registrar la necesidad de traslado intermedio Base -> Obra en Pendientes de Envío."""
        self.ensure_one()
        if not (self.env.user.has_group('dh_requisitions.group_stock_request_employee') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')):
            raise UserError(_("Se requiere pertenecer a Empleados Almacén Base o Administrador para registrar pendientes de envío."))

        if self.product_id.type == 'service':
            raise UserError(_("Las partidas de servicios no aplican para traslados físicos de suministro."))

        base_wh = self.company_id.requisition_default_warehouse_origin_id
        if not base_wh:
            raise UserError(_("Debe configurar el Almacén Base Predeterminado en los Ajustes del módulo de Requisiciones."))

        if self.warehouse_id == base_wh:
            raise UserError(_("Esta solicitud ya tiene como destino el Almacén Base configurado."))

        existing_sr_line = self.env['requisition.supply.request.line'].search([
            ('requisition_line_id', '=', self.requisition_line_id.id if self.requisition_line_id else False),
            ('product_id', '=', self.product_id.id),
            ('state', 'not in', ('cancel', 'done')),
        ], limit=1)
        if existing_sr_line:
            raise UserError(_("Esta partida ya se encuentra registrada en Suministro / Pendientes de Envío."))

        sr_line = self.env['requisition.supply.request.line'].create({
            'request_id': False,
            'requisition_line_id': self.requisition_line_id.id if self.requisition_line_id else False,
            'product_id': self.product_id.id,
            'product_qty': self.product_qty,
            'warehouse_dest_id': self.warehouse_id.id,
            'warehouse_origin_id': base_wh.id,
            'notes': _("Vinculado desde Solicitud de Compra: %s") % self.display_name,
        })
        if self.allocation_id:
            self.allocation_id.supply_request_line_id = sr_line.id

        self.message_post(
            body=Markup(_("Se incorporó la partida a <strong>Pendientes de Envío</strong> para traslado a la obra %s.")) % escape(self.warehouse_id.name),
            subtype_xmlid="mail.mt_note"
        )
        return True

    def action_open_add_to_rfq_wizard(self):
        """Abre el asistente para agregar las líneas seleccionadas a una RFQ."""
        self._check_purchase_manager_permissions()
        action = self.env['ir.actions.actions']._for_xml_id('dh_requisitions.action_requisition_purchase_request_add_to_rfq')
        action['context'] = {
            'active_model': 'requisition.purchase.request.line',
            'active_ids': self.ids,
        }
        return action

    def action_cancel_line(self, cancel_action='reintegrate', reason=None):
        """Cancela la línea de solicitud y actualiza o cancela las asignaciones activas."""
        self.ensure_one()
        self._check_purchase_manager_permissions()
        if self.state in ('cancel', 'purchased'):
            raise UserError(_("No se puede cancelar una línea en estado '%s'.") % dict(_LINE_STATES).get(self.state, self.state))

        if not reason:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Cancelar Partida de Compra'),
                'res_model': 'requisition.purchase.request.cancel.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {
                    'default_purchase_request_line_ids': [self.id],
                    'default_cancel_action': cancel_action,
                },
            }

        pending_balance = self.pending_qty_to_buy
        if pending_balance > 0:
            self.qty_cancelled += pending_balance

        # Capturar las líneas y órdenes de compra vinculadas ANTES de cancelar las asignaciones
        po_lines = (self.purchase_lines | self.allocation_ids.mapped('purchase_line_id')).filtered(bool)
        if not po_lines:
            allocs = self.env['requisition.allocation'].search([('purchase_request_line_id', '=', self.id)])
            po_lines = allocs.mapped('purchase_line_id').filtered(bool)

        active_allocs = self.allocation_ids.filtered(lambda a: a.state != 'cancel')
        if active_allocs:
            for alloc in active_allocs:
                alloc.action_cancel_allocation(
                    cancel_action=cancel_action,
                    reason=reason
                )

        # Cancelar o desvincular líneas de RFQ / Orden de compra asociadas
        for po_line in po_lines:
            po = po_line.order_id
            if po.state in ('draft', 'sent', 'to approve'):
                other_lines = po.order_line - po_line
                if not other_lines:
                    po.with_context(
                        cancel_reason=reason,
                        cancelled_by_id=self.env.user.id
                    ).button_cancel()
                    po.message_post(
                        body=Markup(_("Orden de Compra / RFQ cancelada al cancelarse la partida de compra <strong>%s</strong>.<br/><strong>Motivo:</strong> %s")) % (
                            escape(self.display_name),
                            escape(reason)
                        )
                    )
                else:
                    po_line.unlink()
                    po.message_post(
                        body=Markup(_("Partida <strong>%s</strong> retirada de la orden al cancelarse su solicitud de compra vinculada.<br/><strong>Motivo:</strong> %s")) % (
                            escape(self.product_id.display_name),
                            escape(reason)
                        )
                    )

        self.write({
            'state': 'cancel',
            'cancel_reason': reason,
            'cancelled_by_id': self.env.user.id,
            'date_cancelled': fields.Datetime.now(),
        })

        msg = Markup(_("<strong>Solicitud de Compra Cancelada:</strong> Tratamiento de cantidad: %s.<br/><strong>Motivo:</strong> %s")) % (
            _("Reintegrado a requisición") if cancel_action == 'reintegrate' else _("Cerrado definitivamente"),
            escape(reason)
        )
        self.message_post(body=msg, subtype_xmlid="mail.mt_note")
        return True

    def action_cancel_multiple(self):
        """Abre el asistente para cancelar múltiples partidas de compra con motivo obligatorio."""
        self._check_purchase_manager_permissions()
        if not self:
            raise UserError(_("No hay líneas seleccionadas."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cancelar Partidas de Compra'),
            'res_model': 'requisition.purchase.request.cancel.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_purchase_request_line_ids': self.ids,
            },
        }
