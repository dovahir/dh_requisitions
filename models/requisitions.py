# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from markupsafe import Markup, escape


class Requisitions(models.Model):
    _name = 'requisitions'
    _description = 'Requisición de Empleado'
    _inherit = ['mail.thread', 'mail.activity.mixin']
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
        ondelete='restrict',
        default=lambda self: self.env.user.employee_id.id or False
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
        """Filtra el campo autoriza para mostrar solo usuarios con rol Líder/Aprobador o Administrador en Odoo 19."""
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
            ('normal', 'Normal'),
            ('urgent', 'Urgente'),
        ],
        string="Prioridad",
        default='normal',
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
        ondelete='restrict',
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
    cancel_reason = fields.Text(
        string="Motivo de Cancelación",
        readonly=True,
        copy=False,
        help="Justificación operativa registrada al cancelar la requisición."
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
        compute_sudo=True,
        string='Albaranes de Entrega'
    )
    picking_count = fields.Integer(
        string='Entregas',
        compute='_compute_picking_ids',
        compute_sudo=True
    )

    purchase_order_ids = fields.Many2many(
        'purchase.order',
        compute='_compute_purchase_order_ids',
        compute_sudo=True,
        string='Órdenes de Compra / RFQ'
    )
    allocation_ids = fields.One2many(
        'requisition.allocation',
        'requisition_id',
        string='Asignaciones de Trazabilidad'
    )
    purchase_order_count = fields.Integer(
        string='Compras / RFQ',
        compute='_compute_purchase_order_ids',
        compute_sudo=True
    )

    @api.depends('purchase_request_line_ids')
    def _compute_purchase_request_count(self):
        for rec in self:
            rec.purchase_request_count = len(rec.purchase_request_line_ids)

    @api.depends('line_ids.allocation_ids.stock_move_id.picking_id')
    def _compute_picking_ids(self):
        for rec in self:
            pickings = rec.sudo().line_ids.allocation_ids.mapped('stock_move_id.picking_id')
            rec.picking_ids = pickings
            rec.picking_count = len(pickings)

    @api.depends(
        'line_ids.allocation_ids.purchase_line_id.order_id',
        'purchase_request_line_ids.purchase_line_id.order_id'
    )
    def _compute_purchase_order_ids(self):
        for rec in self:
            orders = (
                rec.sudo().line_ids.allocation_ids.mapped('purchase_line_id.order_id') |
                rec.sudo().purchase_request_line_ids.mapped('purchase_line_id.order_id')
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
        for rec in self:
            if not rec.warehouse_id:
                rec.is_not_base_warehouse = False
                continue
            base_wh = rec.company_id.requisition_default_warehouse_origin_id
            rec.is_not_base_warehouse = bool(base_wh and rec.warehouse_id.id != base_wh.id)

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
                vals['name'] = self.env['ir.sequence'].sudo().next_by_code('requisitions') or _('Nuevo')
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
            if rec.state not in ('draft', 'rejected'):
                raise UserError(_("Solo se pueden confirmar requisiciones en estado Borrador o Rechazada."))
            # Validar que la requisición contenga al menos una línea
            if not rec.line_ids:
                raise ValidationError(_("Debe agregar al menos un producto o servicio antes de solicitar aprobación."))
            rec.write({'state': 'to_approve'})
            if rec.company_id.requisition_allow_auto_approval and is_approver:
                rec.action_approve()

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

            in_progress_states = (
                'in_stock', 'in_purchase', 'in_mixed',
                'partial_stock', 'partial_purchase', 'partial_mixed'
            )
            if all(l.line_state == 'done' for l in active_lines):
                rec.action_done()
            elif any(l.line_state == 'done' for l in active_lines) or any(l.qty_done > 0 for l in active_lines):
                rec.action_partially_done()
            elif any(l.line_state in in_progress_states for l in active_lines):
                if rec.state != 'in_progress':
                    rec.action_in_progress()
            elif all(l.line_state == 'pending' for l in active_lines):
                if rec.state in ('in_progress', 'partially_done'):
                    rec.write({'state': 'approved'})

    def action_approve(self):
        if not (self.env.user.has_group('dh_requisitions.group_requisitions_approver') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')) :
            raise UserError(_("Solo los usuarios con rol Líder / Aprobador o Administrador pueden aprobar requisiciones."))
        is_admin = self.env.user.has_group('dh_requisitions.group_requisitions_manager') or self.env.is_admin()
        for rec in self:
            if rec.state != 'to_approve':
                raise UserError(_("Solo se pueden autorizar requisiciones en estado 'Por Aprobar'."))
            if not rec.line_ids:
                raise UserError(_("No se puede autorizar una requisición sin productos o servicios."))
            if not is_admin and rec.user_id and rec.user_id != self.env.user:
                raise UserError(_(
                    "Solo el usuario designado como autorizador (%s) o un Administrador pueden autorizar esta requisición."
                ) % rec.user_id.name)
            is_requester = (rec.create_uid == self.env.user) or (rec.employee_id.user_id and rec.employee_id.user_id == self.env.user)
            if is_requester and not is_admin and not rec.company_id.requisition_allow_auto_approval:
                raise UserError(_(
                    "La auto-aprobación está restringida por la política de la empresa. La requisición debe ser autorizada por un usuario diferente."
                ))
            rec.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'date_approved': fields.Datetime.now(),
            })

    def action_reject_wizard(self):
        """Abre el asistente interactivo para rechazar la requisición e indicar el motivo."""
        self.ensure_one()
        if not (self.env.user.has_group('dh_requisitions.group_requisitions_approver') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')):
            raise UserError(_("Solo los usuarios con rol Líder / Aprobador o Administrador pueden rechazar requisiciones."))
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

    def action_cancel(self, reason=None):
        """Cancela la requisición requiriendo justificación obligatoria. Si no se provee motivo, abre el asistente interactivo."""
        reason = reason or self.env.context.get('cancel_reason')
        if not reason:
            return self.action_open_cancel_wizard()
        for rec in self:
            wizard = self.env['requisition.cancel.wizard'].create({
                'requisition_id': rec.id,
                'reason': reason,
            })
            wizard.action_confirm_cancel()
        return True

    def action_open_cancel_wizard(self):
        """Abre el asistente interactivo para solicitar el motivo de cancelación en cualquier estado activo."""
        self.ensure_one()
        if not (self.env.user.has_group('dh_requisitions.group_requisitions_approver') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager') or
                self.env.is_admin()):
            raise UserError(_("Solo los Aprobadores / Líderes o Administradores de Requisiciones pueden cancelar requisiciones."))
        if self.state in ('cancel', 'rejected', 'done'):
            raise UserError(_("No se puede cancelar una requisición en estado %s.") % self.state)
        return {
            'name': _('Cancelar Requisición'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.cancel.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_requisition_id': self.id},
        }

    def action_draft(self):
        """Regresa la requisición a borrador desde estado por autorizar, cancelado, rechazado o aprobado sin flujo en proceso."""
        for rec in self:
            if rec.state in ('in_progress', 'partially_done', 'done'):
                raise UserError(_(
                    "No se puede regresar la requisición '%s' a borrador directamente mientras se encuentre en proceso. "
                    "Debe cancelarla primero mediante la opción 'Cancelar' y, una vez cancelada, podrá regresarla a borrador."
                ) % rec.name)

            if rec.state == 'approved' and not (
                self.env.user.has_group('dh_requisitions.group_requisitions_approver') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager') or
                self.env.is_admin()
            ):
                raise UserError(_("Solo los usuarios con rol Líder / Aprobador o Administrador pueden regresar a borrador una requisición autorizada."))

            rec.sudo().write({
                'state': 'draft',
                'approved_by_id': False,
                'date_approved': False,
                'rejected_by_id': False,
                'date_rejected': False,
                'cancelled_by_id': False,
                'date_cancelled': False,
            })

            # Si la requisición estaba cancelada, reactivar la cantidad reintegrando las asignaciones canceladas
            cancelled_allocs = rec.line_ids.allocation_ids.filtered(lambda a: a.state == 'cancel')
            if cancelled_allocs:
                cancelled_allocs.sudo().write({
                    'cancel_action': 'reintegrate',
                    'cancelled_qty': 0.0,
                })

            rec.line_ids._compute_quantities()
            rec.line_ids._compute_line_state()

            # Notificar en el chatter de documentos vinculados abiertos si existieran
            msg_reopen = Markup(_(
                "<strong>Aviso:</strong> La requisición <strong>%s</strong> ha sido regresada a borrador para modificaciones."
            )) % escape(rec.name)

            open_pickings = rec.sudo().picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))
            for p in open_pickings:
                p.message_post(body=msg_reopen, subtype_xmlid="mail.mt_note")

            open_pos = rec.sudo().purchase_order_ids.filtered(lambda o: o.state in ('draft', 'sent', 'to approve'))
            for po in open_pos:
                po.message_post(body=msg_reopen, subtype_xmlid="mail.mt_note")

            sr_requests = rec.sudo().line_ids.allocation_ids.mapped('supply_request_line_id.request_id').filtered(
                lambda r: r.state not in ('done_exact', 'done_adjusted', 'cancel')
            )
            for sr in sr_requests:
                sr.message_post(body=msg_reopen, subtype_xmlid="mail.mt_note")

    def action_create_service_rfq(self):
        """Genera una Solicitud de Cotización (RFQ de Compra) directa para las líneas de servicios pendientes."""
        self.ensure_one()
        if not (self.env.user.has_group('purchase.group_purchase_manager') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')):
            raise UserError(_("Se requiere pertenecer a Administradores de Compras o a Administradores de Requisiciones para generar cotizaciones de servicios."))

        service_lines = self.service_line_ids.filtered(
            lambda l: l.line_state not in ('done', 'cancel') and l.qty_pending > 0
        )
        if not service_lines:
            raise UserError(_("No hay líneas de servicios con cantidad pendiente para generar una solicitud de cotización."))

        supplier = service_lines[0].product_id.seller_ids[:1].partner_id
        if not supplier:
            raise UserError(_(
                "El servicio '%s' no cuenta con un proveedor asignado en su ficha de compras. "
                "Por favor configure el proveedor en el producto antes de generar la cotización de servicios."
            ) % service_lines[0].product_id.display_name)

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

            self.env['requisition.allocation'].create({
                'company_id': self.company_id.id,
                'product_id': line.product_id.id,
                'product_uom_id': line.product_uom_id.id,
                'requested_qty': line.qty_pending,
                'delivered_qty': 0.0,
                'supply_type': 'consumption',
                'stage': 'inbound',
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
        if not (self.env.user.has_group('dh_requisitions.group_stock_request_planta') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')):
            raise UserError(_("Se requiere pertenecer al personal de almacén de obra/base o a Administradores de Requisiciones para generar entregas de almacén."))
        if self.state not in ('approved', 'in_progress', 'partially_done'):
            raise UserError(_("Solo se pueden generar despachos para requisiciones aprobadas o en proceso."))
        eligible_lines = self.line_ids.filtered(
            lambda l: l.line_type != 'service' and l.qty_pending > 0 and l.line_state not in ('done', 'cancel')
        )
        if not eligible_lines:
            raise UserError(_("No hay partidas de materiales, herramientas o equipos con cantidad pendiente para despachar."))

        return {
            'name': _('Generar Despacho / Entrega'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.delivery.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_requisition_id': self.id, 'active_id': self.id},
        }

    def action_create_purchase_request(self):
        """Abre el asistente interactivo para solicitar compra de partidas con cantidad pendiente."""
        self.ensure_one()
        if not (self.env.user.has_group('purchase.group_purchase_user') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')):
            raise UserError(_("Se requiere pertenecer al área de Compras o a Administradores de Requisiciones para generar solicitudes de compra."))
        if self.state not in ('approved', 'in_progress', 'partially_done'):
            raise UserError(_("Solo se pueden generar solicitudes de compra para requisiciones aprobadas o en proceso."))

        lines_to_buy = self.line_ids.filtered(
            lambda l: l._get_qty_unpurchased() > 0 and l.line_state not in ('done', 'cancel')
        )
        if not lines_to_buy:
            raise UserError(_("No hay partidas con cantidad pendiente para enviar a compra."))

        return {
            'name': _('Solicitar Compra'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.purchase.request.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_requisition_id': self.id, 'active_id': self.id},
        }

    def _create_purchase_request_lines(self, lines_data=None):
        """
        Genera líneas de solicitud de compra y registra el ledger requisition.allocation.
        lines_data: Lista de diccionarios [{'line': requisitions.line, 'qty': float, 'priority': str, 'notes': str}]
        Si lines_data es None, procesa todas las partidas con cantidad pendiente por comprar.
        """
        self.ensure_one()
        if lines_data is None:
            lines_data = [
                {
                    'line': l,
                    'qty': l._get_qty_unpurchased(),
                    'priority': l.priority or 'normal',
                    'notes': l.note or '',
                }
                for l in self.line_ids.filtered(
                    lambda l: l._get_qty_unpurchased() > 0 and l.line_state not in ('done', 'cancel')
                )
            ]

        if isinstance(lines_data, dict):
            normalized = []
            for k, v in lines_data.items():
                l = self.line_ids.filtered(lambda x: x.id == k) if isinstance(k, int) else k
                normalized.append({'line': l, 'qty': v})
            lines_data = normalized

        if not lines_data:
            raise UserError(_("No hay partidas válidas con cantidad pendiente para enviar a compra."))

        created_lines = self.env['requisition.purchase.request.line']

        base_wh = self.company_id.requisition_default_warehouse_origin_id
        is_destination_obra = bool(base_wh and self.warehouse_id.id != base_wh.id)

        for item in lines_data:
            line = item['line']
            qty_to_buy = item['qty']
            if qty_to_buy <= 0:
                continue
            line_priority = item.get('priority') or line.priority or 'normal'
            line_notes = item.get('notes') or line.note or ''

            # Buscar asignaciones activas de suministro de la partida que aún no tienen compra
            existing_supply_allocs = line.allocation_ids.filtered(
                lambda a: a.supply_request_line_id and not a.purchase_request_line_id and not a.purchase_line_id and a.state not in ('cancel', 'done')
            )

            remaining_to_buy = qty_to_buy
            for s_alloc in existing_supply_allocs:
                if remaining_to_buy <= 0:
                    break
                alloc_qty = min(s_alloc.requested_qty, remaining_to_buy)
                pr_line = self.env['requisition.purchase.request.line'].create({
                    'requisition_id': self.id,
                    'requisition_line_id': line.id,
                    'product_id': line.product_id.id,
                    'product_qty': alloc_qty,
                    'priority': line_priority,
                    'notes': line_notes,
                    'state': 'pending',
                })
                created_lines |= pr_line
                s_alloc.write({
                    'purchase_request_line_id': pr_line.id,
                    'stage': 'inbound',
                    'state': 'in_progress',
                })
                pr_line.allocation_id = s_alloc.id
                remaining_to_buy -= alloc_qty

                if s_alloc.supply_request_line_id.request_id:
                    s_alloc.supply_request_line_id.request_id.message_post(
                        body=Markup(_("Se vinculó la Solicitud de Compra para la partida <strong>%s</strong> (Requisición %s).")) % (
                            escape(line.product_id.display_name),
                            escape(self.name)
                        ),
                        subtype_xmlid="mail.mt_note"
                    )

            if remaining_to_buy > 0:
                pr_line = self.env['requisition.purchase.request.line'].create({
                    'requisition_id': self.id,
                    'requisition_line_id': line.id,
                    'product_id': line.product_id.id,
                    'product_qty': remaining_to_buy,
                    'priority': line_priority,
                    'notes': line_notes,
                    'state': 'pending',
                })
                created_lines |= pr_line

                supply_line = False
                if is_destination_obra and line.line_type != 'service':
                    # Crear partida en el pool de Pendientes de Envío (request_id=False) sin duplicar allocation
                    supply_line = self.env['requisition.supply.request.line'].with_context(skip_auto_allocation=True).create({
                        'request_id': False,
                        'requisition_line_id': line.id,
                        'product_id': line.product_id.id,
                        'product_qty': remaining_to_buy,
                        'warehouse_dest_id': self.warehouse_id.id,
                        'warehouse_origin_id': base_wh.id if base_wh else False,
                        'notes': line_notes,
                    })

                supply_type = line.supply_type or ('resg' if line.line_type in ('tool', 'equipment') else 'consumption')
                alloc = self.env['requisition.allocation'].create({
                    'company_id': self.company_id.id,
                    'product_id': line.product_id.id,
                    'product_uom_id': line.product_uom_id.id,
                    'requested_qty': remaining_to_buy,
                    'delivered_qty': 0.0,
                    'supply_type': supply_type,
                    'stage': 'inbound',
                    'purchase_request_line_id': pr_line.id,
                    'supply_request_line_id': supply_line.id if supply_line else False,
                    'requisition_line_id': line.id,
                    'state': 'in_progress',
                })
                pr_line.allocation_id = alloc.id

        if created_lines and self.state == 'approved':
            self.action_in_progress()

        if created_lines:
            msg = Markup(_(
                "Se generaron <strong>%s</strong> partida(s) de Solicitud de Compra para seguimiento del área de compras."
            )) % len(created_lines)
            if is_destination_obra:
                msg += Markup(_("<br/>Se registraron las partidas en el pool de <strong>Pendientes de Envío</strong> para posterior traslado Base a Obra."))

            self.message_post(body=msg, subtype_xmlid="mail.mt_note")

        return created_lines

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
        if not (self.env.user.has_group('dh_requisitions.group_stock_request_planta') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')):
            raise UserError(_("Se requiere pertenecer al personal de almacén de obra/base o a Administradores de Requisiciones para solicitar suministros."))
        if self.state not in ('approved', 'in_progress', 'partially_done'):
            raise UserError(_("Solo se pueden generar solicitudes de suministro para requisiciones aprobadas o en proceso."))
        if not self.is_not_base_warehouse:
            raise UserError(_("La solicitud de suministro aplica únicamente cuando el almacén de la requisición es diferente al almacén origen."))
        eligible_lines = self.line_ids.filtered(
            lambda l: l.line_type != 'service' and l._get_qty_unsupplied() > 0 and l.line_state not in ('done', 'cancel')
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