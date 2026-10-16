# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from markupsafe import Markup, escape


class Requisitions(models.Model):
    _name = 'requisitions'
    _description = 'Requisición de Empleado'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'supply.chain.cancel.audit.mixin']
    _order = 'id desc'

    name = fields.Char(
        string="Referencia",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('Nuevo'),
        tracking=True
    )
    state = fields.Selection(
        [
            ('draft', 'Borrador'),
            ('to_approve', 'Por Aprobar'),
            ('approved', 'Aprobado'),
            ('in_progress', 'En Proceso'),
            ('partially_done', 'Parcialmente Suministrado'),
            ('done', 'Suministrado'),
            ('cancel', 'Cancelado'),
            ('rejected', 'Rechazado'),
        ],
        default='draft',
        string="Estado",
        tracking=True
    )

    can_edit_lines = fields.Boolean(
        string="Puede Editar Líneas",
        compute='_compute_can_edit_lines'
    )

    @api.depends('state')
    def _compute_can_edit_lines(self):
        is_approver = (
            self.env.user.has_group('dh_requisitions.group_requisitions_approver') or
            self.env.user.has_group('dh_requisitions.group_requisitions_manager')
        )
        for rec in self:
            if rec.state == 'draft':
                rec.can_edit_lines = True
            elif rec.state == 'to_approve' and is_approver:
                rec.can_edit_lines = True
            else:
                rec.can_edit_lines = False

    # --- Grupo: Datos de Requisición ---
    employee_id = fields.Many2one(
        'hr.employee',
        string="Solicita",
        required=True,
        tracking=True,
        default=False
    )
    department_id = fields.Many2one(
        'hr.department',
        string="Departamento",
        related='employee_id.department_id',
        store=True,
        readonly=True,
        index=True
    )

    @api.model
    def _domain_user_id(self):
        """Filtra el campo autoriza para mostrar solo usuarios con rol Aprobador o Administrador en Odoo 19."""
        group_approver = self.env.ref('dh_requisitions.group_requisitions_approver', raise_if_not_found=False)
        group_manager = self.env.ref('dh_requisitions.group_requisitions_manager', raise_if_not_found=False)
        group_ids = [g.id for g in (group_approver, group_manager) if g]
        if group_ids:
            return [('all_group_ids', 'in', group_ids), ('share', '=', False)]
        return [('share', '=', False)]

    user_id = fields.Many2one(
        'res.users',
        string="Autoriza",
        required=True,
        tracking=True,
        domain=_domain_user_id,
        help="Usuario encargado de autorizar la requisición."
    )
    date_created = fields.Datetime(
        string="Fecha de Creación",
        default=fields.Datetime.now,
        readonly=True,
        tracking=True
    )
    date_required = fields.Date(
        string="Fecha Requerida",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
        help="Fecha en que se requieren los productos o servicios."
    )
    priority = fields.Selection(
        [
            ('0', 'Sin prioridad'),
            ('1', 'Baja'),
            ('2', 'Media'),
            ('3', 'Urgente'),
        ],
        string="Prioridad",
        default='1',
        tracking=True
    )

    # --- Grupo: Destino ---
    company_id = fields.Many2one(
        'res.company',
        string="Empresa",
        required=True,
        default=lambda self: self.env.company
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string="Almacén",
        required=True,
        check_company=True,
        tracking=True,
        help="Almacén de destino donde se requiere el producto."
    )

    # --- Grupo: Otra Información ---
    # Nota: El usuario elaborador se consulta nativamente mediante create_uid en la vista con string="Elaboró".
    approved_by_id = fields.Many2one(
        'res.users',
        string="Autorizado por",
        readonly=True,
        copy=False,
        tracking=True,
        help="Usuario que autorizó la requisición."
    )
    date_approved = fields.Datetime(
        string="Fecha de Aprobación",
        readonly=True,
        copy=False,
        tracking=True
    )
    rejected_by_id = fields.Many2one(
        'res.users',
        string="Rechazado por",
        readonly=True,
        copy=False,
        tracking=True,
        help="Usuario que rechazó la requisición."
    )
    date_rejected = fields.Datetime(
        string="Fecha de Rechazo",
        readonly=True,
        copy=False,
        tracking=True
    )
    cancelled_by_id = fields.Many2one(
        'res.users',
        string="Cancelado por",
        readonly=True,
        copy=False,
        tracking=True,
        help="Usuario que canceló la requisición."
    )
    date_cancelled = fields.Datetime(
        string="Fecha de Cancelación",
        readonly=True,
        copy=False,
        tracking=True
    )
    date_start_tracking = fields.Datetime(
        string="Inicio de Seguimiento",
        readonly=True,
        copy=False,
        tracking=True,
        help="Fecha y hora en la que alguna acción cambió el estado a En Proceso."
    )

    # --- Notebook: Líneas por Categoría (Modelo Unificado requisitions.line) ---
    line_ids = fields.One2many(
        'requisitions.line',
        'requisition_id',
        string="Líneas de Requisición"
    )
    material_line_ids = fields.One2many(
        'requisitions.line',
        'requisition_id',
        string="Materiales",
        domain=[('line_type', '=', 'material')]
    )
    equipment_line_ids = fields.One2many(
        'requisitions.line',
        'requisition_id',
        string="Equipos",
        domain=[('line_type', '=', 'equipment')]
    )
    tool_line_ids = fields.One2many(
        'requisitions.line',
        'requisition_id',
        string="Herramientas",
        domain=[('line_type', '=', 'tool')]
    )
    service_line_ids = fields.One2many(
        'requisitions.line',
        'requisition_id',
        string="Servicios",
        domain=[('line_type', '=', 'service')]
    )
    purchase_request_line_ids = fields.One2many(
        'requisition.purchase.request.line',
        'requisition_id',
        string='Líneas de Solicitud de Compra'
    )
    purchase_request_count = fields.Integer(
        string='Solicitudes de Compra',
        compute='_compute_purchase_request_count'
    )

    picking_ids = fields.Many2many(
        'stock.picking',
        compute='_compute_picking_ids',
        string='Albaranes de Entrega'
    )
    picking_count = fields.Integer(
        string='Entregas',
        compute='_compute_picking_ids'
    )

    purchase_order_ids = fields.Many2many(
        'purchase.order',
        compute='_compute_purchase_order_ids',
        string='Órdenes de Compra / RFQ'
    )
    purchase_order_count = fields.Integer(
        string='Compras / RFQ',
        compute='_compute_purchase_order_ids'
    )

    @api.depends('purchase_request_line_ids')
    def _compute_purchase_request_count(self):
        for rec in self:
            rec.purchase_request_count = len(rec.purchase_request_line_ids)

    @api.depends('line_ids.allocation_ids.stock_move_id.picking_id')
    def _compute_picking_ids(self):
        for rec in self:
            pickings = rec.line_ids.allocation_ids.mapped('stock_move_id.picking_id')
            rec.picking_ids = pickings
            rec.picking_count = len(pickings)

    @api.depends(
        'line_ids.allocation_ids.purchase_line_id.order_id',
        'purchase_request_line_ids.purchase_line_id.order_id'
    )
    def _compute_purchase_order_ids(self):
        for rec in self:
            orders = (
                rec.line_ids.allocation_ids.mapped('purchase_line_id.order_id') |
                rec.purchase_request_line_ids.mapped('purchase_line_id.order_id')
            )
            rec.purchase_order_ids = orders
            rec.purchase_order_count = len(orders)

    # --- Banderas reactivas de tipos de partida para visibilidad de acciones ---
    has_services = fields.Boolean(
        string="Tiene Servicios",
        compute='_compute_line_type_flags',
        store=True,
        help="Indica si la requisición cuenta con al menos una línea clasificada como servicio."
    )
    has_deliverable_products = fields.Boolean(
        string="Tiene Productos Entregables",
        compute='_compute_line_type_flags',
        store=True,
        help="Indica si la requisición contiene materiales, herramientas o equipos gestionables por almacén."
    )

    @api.depends('line_ids.line_type')
    def _compute_line_type_flags(self):
        for rec in self:
            rec.has_services = any(line.line_type == 'service' for line in rec.line_ids)
            rec.has_deliverable_products = any(
                line.line_type in ('material', 'tool', 'equipment') for line in rec.line_ids
            )

    is_not_base_warehouse = fields.Boolean(
        string="Destino Diferente a Base",
        compute='_compute_is_not_base_warehouse',
        store=True,
        help="Verdadero si el almacén destino de la requisición no es el almacén central BASE."
    )
    supply_request_ids = fields.Many2many(
        'requisition.supply.request',
        'requisition_supply_rel',
        'requisition_id',
        'supply_id',
        string='Solicitudes de Suministro',
        readonly=True
    )
    supply_request_count = fields.Integer(
        string='Solicitudes de Suministro',
        compute='_compute_supply_request_count'
    )

    @api.depends('warehouse_id', 'company_id', 'company_id.requisition_default_warehouse_origin_id')
    def _compute_is_not_base_warehouse(self):
        missing_company_ids = {
            rec.company_id.id for rec in self
            if rec.company_id and not rec.company_id.requisition_default_warehouse_origin_id
        }
        fallback_base_wh_by_company = {}
        if missing_company_ids:
            base_warehouses = self.env['stock.warehouse'].search([
                ('company_id', 'in', list(missing_company_ids)),
                '|', ('code', '=', 'BASE'), ('name', 'ilike', 'base')
            ])
            for wh in base_warehouses:
                if wh.company_id.id not in fallback_base_wh_by_company:
                    fallback_base_wh_by_company[wh.company_id.id] = wh.id

        for rec in self:
            if not rec.warehouse_id:
                rec.is_not_base_warehouse = False
                continue
            base_wh_id = (
                rec.company_id.requisition_default_warehouse_origin_id.id
                or fallback_base_wh_by_company.get(rec.company_id.id)
            )
            rec.is_not_base_warehouse = bool(base_wh_id and rec.warehouse_id.id != base_wh_id)

    @api.depends('supply_request_ids')
    def _compute_supply_request_count(self):
        for rec in self:
            rec.supply_request_count = len(rec.supply_request_ids)

    # --- Notas Generales ---
    notes = fields.Text(
        string="Notas",
        help="Observaciones o comentarios adicionales sobre la requisición."
    )

    # --- Validaciones y Restricciones ---
    @api.constrains('date_required', 'date_created')
    def _check_date_required(self):
        """Valida que la fecha requerida no sea anterior a la fecha de creación."""
        for rec in self:
            created_date = rec.date_created.date() if rec.date_created else False
            if rec.date_required and created_date and rec.date_required < created_date:
                raise ValidationError(_("La fecha requerida no puede ser anterior a la fecha de creación."))

    @api.constrains('line_ids')
    def _check_unique_products(self):
        """Valida que un producto no esté duplicado en ninguna de las pestañas de la requisición."""
        for rec in self:
            seen_products = set()
            for line in rec.line_ids:
                if not line.product_id:
                    continue
                if line.product_id.id in seen_products:
                    raise ValidationError(
                        _(
                            "El producto '%s' ya se encuentra agregado en la requisición.\n"
                            "Por favor, ajuste la cantidad en la línea existente en lugar de agregar una duplicada."
                        ) % line.product_id.display_name
                    )
                seen_products.add(line.product_id.id)

    @api.model
    def _inject_line_types(self, vals):
        """Garantiza que cualquier comando de creación de líneas en pestañas específicas conserve su line_type."""
        type_mapping = {
            'material_line_ids': 'material',
            'tool_line_ids': 'tool',
            'equipment_line_ids': 'equipment',
            'service_line_ids': 'service',
        }
        for field_name, l_type in type_mapping.items():
            if field_name in vals and isinstance(vals[field_name], (list, tuple)):
                for cmd in vals[field_name]:
                    if isinstance(cmd, (list, tuple)) and len(cmd) == 3 and cmd[0] == 0:
                        line_vals = cmd[2]
                        if isinstance(line_vals, dict) and not line_vals.get('line_type'):
                            line_vals['line_type'] = l_type

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('requisitions') or _('Nuevo')
            self._inject_line_types(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._inject_line_types(vals)
        if 'priority' in vals and not self.env.su:
            for rec in self:
                if not rec.can_edit_lines:
                    raise UserError(_("No puede modificar la prioridad de una requisición que ya no se encuentra en estado editable."))
        # Asignar automáticamente los datos de seguimiento según el estado alcanzado
        now = fields.Datetime.now()
        if vals.get('state') == 'approved':
            if 'approved_by_id' not in vals:
                vals['approved_by_id'] = self.env.user.id
            if 'date_approved' not in vals:
                vals['date_approved'] = now
        elif vals.get('state') == 'rejected':
            if 'rejected_by_id' not in vals:
                vals['rejected_by_id'] = self.env.user.id
            if 'date_rejected' not in vals:
                vals['date_rejected'] = now
        elif vals.get('state') == 'cancel':
            if 'cancelled_by_id' not in vals:
                vals['cancelled_by_id'] = self.env.user.id
            if 'date_cancelled' not in vals:
                vals['date_cancelled'] = now
        elif vals.get('state') == 'in_progress':
            if 'date_start_tracking' not in vals:
                # Solo si no tiene fecha previa de inicio de seguimiento
                for rec in self:
                    if not rec.date_start_tracking:
                        rec.date_start_tracking = now
        return super().write(vals)

    # --- Acciones de Cambio de Estado ---
    def action_confirm(self):
        is_approver = (
            self.env.user.has_group('dh_requisitions.group_requisitions_approver') or
            self.env.user.has_group('dh_requisitions.group_requisitions_manager')
        )
        for rec in self:
            # Validar que la requisición contenga al menos una línea
            if not rec.line_ids:
                raise ValidationError(_("Debe agregar al menos un producto o servicio antes de solicitar aprobación."))
            if rec.company_id.requisition_allow_auto_approval and is_approver:
                rec.action_approve()
            else:
                rec.write({'state': 'to_approve'})

    def _update_state_from_lines(self):
        """Actualiza automáticamente el estado cabecera según el avance de sus líneas de suministro."""
        for rec in self:
            if rec.state not in ('approved', 'in_progress', 'partially_done'):
                continue
            lines = rec.line_ids
            if not lines:
                continue
            active_lines = lines.filtered(lambda l: l.line_state != 'cancel')
            if not active_lines:
                continue

            if all(l.line_state == 'done' for l in active_lines):
                rec.action_done()
            elif any(l.line_state == 'done' for l in active_lines) or any(l.qty_done > 0 for l in active_lines):
                rec.action_partially_done()
            elif any(l.line_state in ('assigned', 'partial_assigned') for l in active_lines):
                if rec.state != 'in_progress':
                    rec.action_in_progress()

    def action_approve(self):
        if not (self.env.user.has_group('dh_requisitions.group_requisitions_approver') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')):
            raise UserError(_("Solo los usuarios con rol Aprobador o Administrador pueden aprobar requisiciones."))
        self.write({
            'state': 'approved',
            'approved_by_id': self.env.user.id,
            'date_approved': fields.Datetime.now(),
        })

    def action_reject_wizard(self):
        """Abre el asistente interactivo para rechazar la requisición e indicar el motivo."""
        self.ensure_one()
        if not (self.env.user.has_group('dh_requisitions.group_requisitions_approver') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')):
            raise UserError(_("Solo los usuarios con rol Aprobador o Administrador pueden rechazar requisiciones."))
        return {
            'name': _('Rechazar Requisición'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_requisition_id': self.id},
        }

    def action_in_progress(self):
        now = fields.Datetime.now()
        to_track = self.filtered(lambda r: not r.date_start_tracking)
        if to_track:
            to_track.write({'state': 'in_progress', 'date_start_tracking': now})
        (self - to_track).write({'state': 'in_progress'})

    def action_partially_done(self):
        self.write({'state': 'partially_done'})

    def action_done(self):
        self.write({'state': 'done'})

    def action_cancel(self):
        """Cancela directamente la requisición cuando se encuentra en estado Borrador."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Para cancelar una requisición autorizada, utilice la opción de cancelación con motivo."))
            active_allocs = rec.line_ids.allocation_ids.filtered(lambda a: a.state in ('draft', 'in_progress'))
            if active_allocs:
                active_allocs.action_cancel_allocation(
                    cancel_action='close',
                    reason=_("Requisición cancelada desde borrador")
                )
            rec.write({
                'state': 'cancel',
                'cancelled_by_id': self.env.user.id,
                'date_cancelled': fields.Datetime.now(),
            })

    def action_open_cancel_wizard(self):
        """Abre el asistente interactivo para solicitar el motivo de cancelación en requisiciones aprobadas o en proceso."""
        self.ensure_one()
        if self.state not in ('approved', 'in_progress', 'partially_done'):
            raise UserError(_("Solo se pueden cancelar requisiciones aprobadas o en proceso mediante este asistente."))
        return {
            'name': _('Cancelar Requisición'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.cancel.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_requisition_id': self.id},
        }

    def action_draft(self):
        """Regresa la requisición a borrador y limpia los datos de rechazo y cancelación previos."""
        self.write({
            'state': 'draft',
            'rejected_by_id': False,
            'date_rejected': False,
            'cancelled_by_id': False,
            'date_cancelled': False,
        })
        self.line_ids._compute_quantities()

    def action_create_service_rfq(self):
        """Genera una Solicitud de Cotización (RFQ de Compra) directa para las líneas de servicios pendientes."""
        self.ensure_one()
        if not self.env.user.has_group('purchase.group_purchase_manager'):
            raise UserError(_("Solo los usuarios con rol Administrador de Compras pueden generar cotizaciones de servicios."))

        service_lines = self.service_line_ids.filtered(
            lambda l: l.line_state not in ('done', 'cancel') and l.qty_pending > 0
        )
        if not service_lines:
            raise UserError(_("No hay líneas de servicios con saldo pendiente para generar una solicitud de cotización."))

        supplier = service_lines[0].product_id.seller_ids[:1].partner_id
        if not supplier:
            supplier = self.env['res.partner'].search([('supplier_rank', '>', 0)], limit=1)
        if not supplier:
            raise UserError(_("No se encontró ningún contacto proveedor registrado en el sistema para asociar a la cotización."))

        po_vals = {
            'partner_id': supplier.id,
            'company_id': self.company_id.id,
            'origin': self.name,
            'date_order': fields.Datetime.now(),
        }
        if self.notes:
            po_vals['req_note'] = self.notes
        po = self.env['purchase.order'].create(po_vals)

        for line in service_lines:
            po_line_vals = {
                'order_id': po.id,
                'product_id': line.product_id.id,
                'name': line.product_id.display_name,
                'product_qty': line.qty_pending,
                'product_uom_id': line.product_uom_id.id,
                'price_unit': line.product_id.standard_price or 0.0,
                'date_planned': fields.Datetime.now(),
            }
            if line.note:
                po_line_vals['note'] = line.note
            po_line = self.env['purchase.order.line'].create(po_line_vals)

            self.env['supply.chain.allocation'].create({
                'company_id': self.company_id.id,
                'product_id': line.product_id.id,
                'product_uom_id': line.product_uom_id.id,
                'requested_qty': line.qty_pending,
                'delivered_qty': 0.0,
                'supply_type': 'consumption',
                'purchase_line_id': po_line.id,
                'requisition_line_id': line.id,
                'state': 'in_progress',
            })

        if self.state == 'approved':
            self.action_in_progress()

        self.message_post(
            body=Markup(_(
                "Se generó la Solicitud de Cotización de Servicios <strong>%s</strong>."
            )) % escape(po.name),
            subtype_xmlid="mail.mt_note"
        )

        return {
            'name': _('Solicitud de Cotización'),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'form',
            'res_id': po.id,
        }

    def action_open_delivery_wizard(self):
        """Abre el asistente interactivo para generar despachos y entregas de almacén."""
        self.ensure_one()
        if not self.env.user.has_group('dh_requisitions.group_requisitions_manager'):
            raise UserError(_("Solo los administradores de requisiciones tienen autorización para generar entregas de almacén."))
        if self.state not in ('approved', 'in_progress', 'partially_done'):
            raise UserError(_("Solo se pueden generar despachos para requisiciones aprobadas o en proceso."))
        eligible_lines = self.line_ids.filtered(
            lambda l: l.line_type != 'service' and l.qty_pending > 0 and l.line_state not in ('done', 'cancel')
        )
        if not eligible_lines:
            raise UserError(_("No hay partidas de materiales, herramientas o equipos con saldo pendiente para despachar."))

        return {
            'name': _('Generar Despacho / Entrega'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.delivery.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_requisition_id': self.id, 'active_id': self.id},
        }

    def action_create_purchase_request(self):
        """Genera líneas de solicitud de compra para materiales y herramientas con saldo pendiente."""
        self.ensure_one()
        if self.state not in ('approved', 'in_progress', 'partially_done'):
            raise UserError(_("Solo se pueden generar solicitudes de compra para requisiciones aprobadas o en proceso."))

        lines_to_buy = self.line_ids.filtered(
            lambda l: l.line_type != 'service' and l.qty_pending > 0 and l.line_state not in ('done', 'cancel')
        )
        if not lines_to_buy:
            raise UserError(_("No hay partidas de materiales o herramientas con saldo pendiente para enviar a compra."))

        created_lines = self.env['requisition.purchase.request.line']

        base_wh = self.env['stock.warehouse'].search([
            ('company_id', '=', self.company_id.id),
            '|', ('code', '=', 'BASE'), ('name', 'ilike', 'base')
        ], limit=1)

        is_destination_obra = bool(base_wh and self.warehouse_id.id != base_wh.id)
        supply_request = False
        if is_destination_obra:
            supply_request = self.env['requisition.supply.request'].search([
                ('company_id', '=', self.company_id.id),
                ('warehouse_dest_id', '=', self.warehouse_id.id),
                ('warehouse_origin_id', '=', base_wh.id),
            ], limit=1, order='id desc')
            if not supply_request:
                supply_request = self.env['requisition.supply.request'].create({
                    'company_id': self.company_id.id,
                    'warehouse_origin_id': base_wh.id,
                    'warehouse_dest_id': self.warehouse_id.id,
                    'requisition_ids': [(4, self.id)],
                    'notes': _("Generado automáticamente desde la requisición %s") % self.name,
                })
            elif self.id not in supply_request.requisition_ids.ids:
                supply_request.requisition_ids = [(4, self.id)]

        for line in lines_to_buy:
            pr_line = self.env['requisition.purchase.request.line'].create({
                'requisition_id': self.id,
                'requisition_line_id': line.id,
                'product_id': line.product_id.id,
                'product_qty': line.qty_pending,
                'notes': line.note or '',
                'state': 'pending',
            })
            created_lines |= pr_line

            supply_line = False
            if supply_request:
                supply_line = self.env['requisition.supply.request.line'].create({
                    'request_id': supply_request.id,
                    'requisition_line_id': line.id,
                    'product_id': line.product_id.id,
                    'product_qty': line.qty_pending,
                    'notes': line.note or '',
                })

            supply_type = line.supply_type or ('resg' if line.line_type in ('tool', 'equipment') else 'consumption')
            alloc = self.env['supply.chain.allocation'].create({
                'company_id': self.company_id.id,
                'product_id': line.product_id.id,
                'product_uom_id': line.product_uom_id.id,
                'requested_qty': line.qty_pending,
                'delivered_qty': 0.0,
                'supply_type': supply_type,
                'purchase_request_line_id': pr_line.id,
                'supply_request_line_id': supply_line.id if supply_line else False,
                'requisition_line_id': line.id,
                'state': 'in_progress',
            })
            pr_line.allocation_id = alloc.id

        if self.state == 'approved':
            self.action_in_progress()

        msg = Markup(_(
            "Se generaron <strong>%s</strong> partida(s) de Solicitud de Compra para seguimiento del área de compras."
        )) % len(created_lines)
        if supply_request:
            msg += Markup(_("<br/>Se programó la partida en la Solicitud de Suministro <strong>%s</strong> (Base a Obra).")) % escape(supply_request.name)

        self.message_post(body=msg, subtype_xmlid="mail.mt_note")

        return {
            'name': _('Líneas de Solicitud de Compra'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.purchase.request.line',
            'view_mode': 'list,form',
            'domain': [('requisition_id', '=', self.id)],
            'context': {'default_requisition_id': self.id},
        }

    def action_view_lines_tracking(self):
        """Abre la vista de seguimiento interactiva para visualizar el avance de las líneas de la requisición."""
        self.ensure_one()
        view_list_id = self.env.ref('dh_requisitions.requisitions_line_view_list_tracking', raise_if_not_found=False)
        return {
            'name': _("Seguimiento de Líneas: %s") % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'requisitions.line',
            'view_mode': 'list,form',
            'views': [
                (view_list_id.id if view_list_id else False, 'list'),
                (False, 'form'),
            ],
            'domain': [('requisition_id', '=', self.id)],
            'context': {
                'default_requisition_id': self.id,
                'search_default_group_by_line_type': 1,
                'create': False,
            },
        }

    def action_view_purchase_request_lines(self):
        """Abre la lista de líneas de solicitud de compra originadas por esta requisición."""
        self.ensure_one()
        return {
            'name': _('Líneas de Solicitud de Compra'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.purchase.request.line',
            'view_mode': 'list,form',
            'domain': [('requisition_id', '=', self.id)],
            'context': {'default_requisition_id': self.id},
        }

    def action_open_supply_request_wizard(self):
        """Abre el asistente interactivo para seleccionar líneas de la requisición y programar el suministro desde almacén origen a obra."""
        self.ensure_one()
        if self.state not in ('approved', 'in_progress', 'partially_done'):
            raise UserError(_("Solo se pueden generar solicitudes de suministro para requisiciones aprobadas o en proceso."))
        if not self.is_not_base_warehouse:
            raise UserError(_("La solicitud de suministro aplica únicamente cuando el almacén de la requisición es diferente al almacén origen."))
        eligible_lines = self.line_ids.filtered(
            lambda l: l.line_type != 'service' and l.qty_pending > 0 and l.line_state not in ('done', 'cancel')
        )
        if not eligible_lines:
            raise UserError(_("No hay partidas pendientes de suministrar en esta requisición."))

        return {
            'name': _('Solicitar Suministro (Origen a Obra)'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.supply.request.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_requisition_id': self.id,
                'active_id': self.id,
            },
        }

    def action_view_supply_requests(self):
        """Abre las solicitudes de suministro vinculadas a esta requisición."""
        self.ensure_one()
        action = {
            'name': _('Solicitudes de Suministro'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.supply.request',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.supply_request_ids.ids)],
            'context': {'default_warehouse_dest_id': self.warehouse_id.id},
        }
        if len(self.supply_request_ids) == 1:
            action['view_mode'] = 'form'
            action['res_id'] = self.supply_request_ids.id
        return action

    def action_view_pickings(self):
        """Abre los albaranes de entrega vinculados a esta requisición."""
        self.ensure_one()
        action = {
            'name': _('Albaranes de Entrega'),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.picking_ids.ids)],
            'context': {'create': False},
        }
        if len(self.picking_ids) == 1:
            action['view_mode'] = 'form'
            action['res_id'] = self.picking_ids.id
        return action

    def action_view_purchase_orders(self):
        """Abre las cotizaciones y órdenes de compra vinculadas a esta requisición."""
        self.ensure_one()
        action = {
            'name': _('Cotizaciones y Órdenes de Compra'),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.purchase_order_ids.ids)],
            'context': {'create': False},
        }
        if len(self.purchase_order_ids) == 1:
            action['view_mode'] = 'form'
            action['res_id'] = self.purchase_order_ids.id
        return action

    @api.ondelete(at_uninstall=False)
    def _unlink_except_confirmed(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Solo se permite eliminar requisiciones en estado Borrador."))