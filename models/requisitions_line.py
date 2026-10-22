# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from markupsafe import Markup, escape


class RequisitionsLine(models.Model):
    _name = 'requisitions.line'
    _description = 'Línea de Requisición'
    _order = 'requisition_id, sequence, id'

    requisition_id = fields.Many2one(
        'requisitions',
        string="Requisición",
        required=True,
        ondelete='cascade',
        index=True
    )
    company_id = fields.Many2one(
        'res.company',
        string="Empresa",
        related='requisition_id.company_id',
        store=True,
        readonly=True
    )
    sequence = fields.Integer(
        string="Secuencia",
        default=10
    )
    sequence_number = fields.Integer(
        string="#",
        compute='_compute_sequence_number'
    )

    line_type = fields.Selection(
        [
            ('material', 'Material'),
            ('equipment', 'Equipo'),
            ('tool', 'Herramienta'),
            ('service', 'Servicio'),
        ],
        string="Tipo de Línea",
        required=True,
        default='material',
        index=True
    )

    supply_type = fields.Selection(
        [
            ('consumption', 'Consumo Directo'),
            ('resg', 'Resguardo'),
            ('custody', 'Préstamo / Resguardo'),
        ],
        string="Tipo de Suministro",
        compute='_compute_supply_type',
        store=True,
        readonly=False
    )

    custody_state = fields.Selection(
        [
            ('none', 'N/A'),
            ('in_custody', 'En Resguardo / Préstamo'),
            ('returned', 'Devuelto / Resguardo Concluido'),
        ],
        string="Estado de Resguardo",
        default='none'
    )

    product_id = fields.Many2one(
        'product.product',
        string="Producto",
        required=True,
        check_company=True,
        ondelete='restrict'
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string="Unidad de Medida",
        related='product_id.uom_id',
        readonly=True,
        store=True
    )
    product_qty = fields.Float(
        string="Cantidad",
        default=1.0,
        required=True,
        digits='Product Unit of Measure'
    )

    def _default_priority(self):
        """Hereda la prioridad de la requisición padre si está disponible en contexto, o '1' (Baja)."""
        return self.env.context.get('default_priority') or '1'

    priority = fields.Selection(
        [
            ('0', 'Sin prioridad'),
            ('1', 'Baja'),
            ('2', 'Media'),
            ('3', 'Urgente'),
        ],
        string="Prioridad",
        compute='_compute_priority',
        store=True,
        readonly=False,
        precompute=True,
        default=_default_priority
    )
    note = fields.Char(
        string="Notas",
        help="Observaciones breves sobre esta línea"
    )

    # --- Trazabilidad con supply.chain.allocation ---
    allocation_ids = fields.One2many(
        'supply.chain.allocation',
        'requisition_line_id',
        string="Asignaciones de Suministro"
    )

    qty_allocated_stock = fields.Float(
        string="Asignado Almacén",
        compute='_compute_quantities',
        store=True,
        digits='Product Unit of Measure'
    )
    qty_allocated_purchase = fields.Float(
        string="Asignado Compras",
        compute='_compute_quantities',
        store=True,
        digits='Product Unit of Measure'
    )
    qty_done = fields.Float(
        string="Suministrado",
        compute='_compute_quantities',
        store=True,
        digits='Product Unit of Measure'
    )
    qty_cancelled = fields.Float(
        string="Cancelado Definitivo",
        compute='_compute_quantities',
        store=True,
        digits='Product Unit of Measure',
        help="Cantidad cancelada definitivamente que ya no se requerirá surtir."
    )
    qty_pending = fields.Float(
        string="Pendiente",
        compute='_compute_quantities',
        store=True,
        digits='Product Unit of Measure'
    )

    line_state = fields.Selection(
        [
            ('draft', 'Borrador'),
            ('pending', 'Pendiente'),
            ('partial_assigned', 'Parcialmente Asignado'),
            ('assigned', 'Asignado'),
            ('partially_done', 'Parcialmente Suministrado'),
            ('done', 'Suministrado'),
            ('cancel', 'Cancelado'),
        ],
        string="Estado",
        compute='_compute_line_state',
        store=True,
        default='draft',
        index=True
    )

    @api.depends('line_type')
    def _compute_supply_type(self):
        for line in self:
            if not line.supply_type:
                line.supply_type = 'resg' if line.line_type in ('tool', 'equipment') else 'consumption'

    @api.depends('sequence', 'requisition_id', 'line_type')
    def _compute_sequence_number(self):
        for line in self:
            line.sequence_number = False

        for req in self.mapped('requisition_id'):
            for l_type in ('material', 'equipment', 'tool', 'service'):
                lines = req.line_ids.filtered(lambda l: l.line_type == l_type).sorted(key=lambda l: (l.sequence or 0, l.id or 0))
                seq = 1
                for l in lines:
                    if l in self:
                        l.sequence_number = seq
                    seq += 1

    @api.constrains('product_qty')
    def _check_product_qty(self):
        """Valida que la cantidad solicitada sea estrictamente mayor que cero y no menor a lo ya suministrado."""
        for line in self:
            if line.product_qty <= 0:
                raise ValidationError(
                    _("La cantidad solicitada para el producto '%s' debe ser mayor que cero.", line.product_id.display_name)
                )
            if line.qty_done > 0 and line.product_qty < line.qty_done:
                raise ValidationError(
                    _("No puede reducir la cantidad solicitada de '%s' a %.2f porque ya se han suministrado %.2f unidades físicamente.")
                    % (line.product_id.display_name, line.product_qty, line.qty_done)
                )

    @api.depends('requisition_id.priority')
    def _compute_priority(self):
        for line in self:
            if line.requisition_id and line.requisition_id.priority:
                line.priority = line.requisition_id.priority
            elif self.env.context.get('default_priority'):
                line.priority = self.env.context.get('default_priority')
            elif not line.priority:
                line.priority = '1'

    @api.depends(
        'allocation_ids.requested_qty',
        'allocation_ids.delivered_qty',
        'allocation_ids.open_qty',
        'allocation_ids.cancelled_qty',
        'allocation_ids.state',
        'allocation_ids.cancel_action',
        'allocation_ids.stock_move_id',
        'allocation_ids.supply_request_line_id',
        'allocation_ids.purchase_line_id',
        'allocation_ids.purchase_request_line_id',
        'product_qty',
        'requisition_id.state'
    )
    def _compute_quantities(self):
        for line in self:
            stock_allocs = line.allocation_ids.filtered(
                lambda a: bool(a.stock_move_id) or bool(a.supply_request_line_id)
            )
            purchase_allocs = line.allocation_ids.filtered(
                lambda a: bool(a.purchase_line_id) or bool(a.purchase_request_line_id)
            )

            # 1. Cantidad Suministrada (Física):
            # Suma todas las entregas reales sin importar el estado administrativo posterior
            stock_delivered = sum(stock_allocs.mapped('delivered_qty'))
            purchase_delivered = sum(
                purchase_allocs.filtered(
                    lambda a: not a.stock_move_id and (line.line_type == 'service' or not stock_allocs)
                ).mapped('delivered_qty')
            )
            line.qty_done = stock_delivered + purchase_delivered

            # 2. Cantidades Asignadas en Tránsito / Abiertas:
            active_stock = stock_allocs.filtered(lambda a: a.state in ('draft', 'in_progress'))
            active_purchase = purchase_allocs.filtered(lambda a: a.state in ('draft', 'in_progress'))

            line.qty_allocated_stock = sum(active_stock.mapped('open_qty'))
            line.qty_allocated_purchase = sum(active_purchase.mapped('open_qty'))

            # 3. Cantidad Cancelada y Pendiente:
            if line.requisition_id and line.requisition_id.state in ('cancel', 'rejected'):
                # Si la requisición se cancela o rechaza, todo lo no entregado queda cerrado definitivamente
                line.qty_cancelled = max(0.0, line.product_qty - line.qty_done)
                line.qty_pending = 0.0
            else:
                closed_allocs_qty = sum(
                    line.allocation_ids.filtered(lambda a: a.cancel_action == 'close').mapped('cancelled_qty')
                )
                line.qty_cancelled = closed_allocs_qty
                total_in_flight = line.qty_allocated_stock + line.qty_allocated_purchase
                line.qty_pending = max(0.0, line.product_qty - line.qty_done - total_in_flight - line.qty_cancelled)

    @api.depends(
        'requisition_id.state',
        'product_qty',
        'qty_done',
        'qty_pending',
        'qty_cancelled',
        'qty_allocated_stock',
        'qty_allocated_purchase'
    )
    def _compute_line_state(self):
        for line in self:
            req_state = line.requisition_id.state if line.requisition_id else 'draft'
            if req_state in ('draft', 'to_approve'):
                line.line_state = 'draft'
            elif req_state in ('cancel', 'rejected'):
                if line.product_qty > 0 and line.qty_done >= line.product_qty:
                    line.line_state = 'done'
                else:
                    line.line_state = 'cancel'
            elif line.product_qty > 0 and (line.qty_done + line.qty_cancelled) >= line.product_qty:
                if line.qty_done > 0:
                    line.line_state = 'done'
                else:
                    line.line_state = 'cancel'
            elif line.qty_done > 0:
                line.line_state = 'partially_done'
            elif line.qty_pending <= 0:
                line.line_state = 'assigned'
            elif (line.qty_allocated_stock + line.qty_allocated_purchase) > 0:
                line.line_state = 'partial_assigned'
            else:
                line.line_state = 'pending'

    # --- Restricciones de Modificación y Eliminación Backend/API ---
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('requisition_id'):
                req = self.env['requisitions'].browse(vals['requisition_id'])
                if req.state != 'draft':
                    raise UserError(
                        _("No se pueden agregar productos a la requisición '%s' porque no se encuentra en estado Borrador.")
                        % req.name
                    )
                if 'priority' not in vals and req.priority:
                    vals['priority'] = req.priority
                if 'line_type' not in vals:
                    if self.env.context.get('default_line_type'):
                        vals['line_type'] = self.env.context.get('default_line_type')
                    elif vals.get('product_id'):
                        product = self.env['product.product'].browse(vals['product_id'])
                        if product.categ_id.requisition_type:
                            vals['line_type'] = product.categ_id.requisition_type
        return super().create(vals_list)

    def write(self, vals):
        restricted_fields = {'product_id', 'product_qty', 'product_uom_id', 'line_type', 'requisition_id'}
        if restricted_fields.intersection(vals.keys()):
            is_approver = (
                self.env.user.has_group('dh_requisitions.group_requisitions_approver') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')
            )
            for line in self:
                req = line.requisition_id
                if not req:
                    continue
                can_edit = (req.state == 'draft') or (req.state == 'to_approve' and is_approver)
                if not can_edit:
                    raise UserError(
                        _("No se pueden modificar las líneas de la requisición '%s' porque no se encuentra en estado Borrador.")
                        % req.name
                    )

        # H-02: Capturar cambios de cantidad antes de guardar para sincronizar documentos dependientes
        qty_changes = {}
        if 'product_qty' in vals:
            new_qty = vals['product_qty']
            for line in self:
                if line.product_qty != new_qty:
                    qty_changes[line.id] = (line, line.product_qty, new_qty)

        res = super().write(vals)

        # H-02: Propagar cantidades actualizadas y registrar en el chatter de documentos vinculados
        if qty_changes:
            for line_id, (line, old_qty, new_qty) in qty_changes.items():
                req_name = line.requisition_id.name if line.requisition_id else ''
                prod_name = line.product_id.display_name
                uom_name = line.product_uom_id.name or ''

                chatter_msg = Markup(_(
                    "El producto <strong>%s</strong> de la requisición <strong>%s</strong> fue modificado a "
                    "<strong>%s %s</strong> (cantidad anterior: <strong>%s %s</strong>)."
                )) % (
                    escape(prod_name),
                    escape(req_name),
                    new_qty,
                    escape(uom_name),
                    old_qty,
                    escape(uom_name)
                )

                # 1. Actualizar asignaciones activas
                active_allocs = line.allocation_ids.filtered(lambda a: a.state not in ('done', 'cancel'))
                for alloc in active_allocs:
                    alloc.requested_qty = new_qty
                    alloc._sync_origin_document_status()

                # 2. Actualizar albaranes y movimientos de stock en borrador / espera / asignados
                open_moves = line.allocation_ids.mapped('stock_move_id').filtered(
                    lambda m: m.state in ('draft', 'waiting', 'confirmed', 'assigned')
                )
                for move in open_moves:
                    move.product_uom_qty = new_qty
                    if move.picking_id:
                        move.picking_id.message_post(body=chatter_msg, subtype_xmlid="mail.mt_note")

                # 3. Actualizar partidas en Solicitudes de Suministro abiertas
                sr_lines = self.env['requisition.supply.request.line'].search([
                    ('requisition_line_id', '=', line.id),
                    ('state', 'not in', ('cancelled', 'done_exact', 'done_adjusted'))
                ])
                for sr_line in sr_lines:
                    sr_line.product_qty = new_qty
                    if sr_line.request_id:
                        sr_line.request_id.message_post(body=chatter_msg, subtype_xmlid="mail.mt_note")

                # 4. Actualizar partidas en Solicitudes de Compra abiertas
                pr_lines = self.env['requisition.purchase.request.line'].search([
                    ('requisition_line_id', '=', line.id),
                    ('state', 'not in', ('cancel', 'purchased'))
                ])
                for pr_line in pr_lines:
                    pr_line.product_qty = new_qty
                    pr_line.message_post(body=chatter_msg, subtype_xmlid="mail.mt_note")

                # 5. Actualizar líneas de órdenes de compra (RFQ) en borrador / enviadas
                po_lines = line.allocation_ids.mapped('purchase_line_id').filtered(
                    lambda pol: pol.order_id.state in ('draft', 'sent', 'to approve')
                ) | pr_lines.mapped('purchase_lines').filtered(
                    lambda pol: pol.order_id.state in ('draft', 'sent', 'to approve')
                )
                for po_line in po_lines:
                    target_uom = getattr(po_line, 'product_uom_id', False) or getattr(po_line, 'product_uom', False) or line.product_uom_id
                    converted_qty = line.product_uom_id._compute_quantity(new_qty, target_uom)
                    po_line.product_qty = converted_qty
                    po_line.order_id.message_post(body=chatter_msg, subtype_xmlid="mail.mt_note")

                # 6. Registrar en el chatter de la propia requisición
                if line.requisition_id:
                    line.requisition_id.message_post(
                        body=Markup(_(
                            "Se modificó la cantidad de la partida <strong>%s</strong> de <strong>%s %s</strong> a <strong>%s %s</strong>."
                        )) % (
                            escape(prod_name),
                            old_qty,
                            escape(uom_name),
                            new_qty,
                            escape(uom_name)
                        ),
                        subtype_xmlid="mail.mt_note"
                    )

        return res

    @api.ondelete(at_uninstall=False)
    def _unlink_except_draft_requisition(self):
        for line in self:
            if line.requisition_id and line.requisition_id.state != 'draft':
                raise UserError(
                    _("No se pueden eliminar líneas de la requisición '%s' porque no se encuentra en estado Borrador.")
                    % line.requisition_id.name
                )
