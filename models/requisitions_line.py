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
            ('partially_returned', 'Parcialmente Devuelto'),
            ('returned', 'Devuelto / Resguardo Concluido'),
        ],
        string="Estado de Resguardo",
        compute='_compute_custody_quantities',
        store=True,
        default='none'
    )
    qty_in_custody = fields.Float(
        string="En Resguardo",
        compute='_compute_custody_quantities',
        store=True,
        digits='Product Unit of Measure'
    )
    qty_returned = fields.Float(
        string="Devuelto",
        compute='_compute_custody_quantities',
        store=True,
        digits='Product Unit of Measure'
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
        """Hereda la prioridad de la requisición padre si está disponible en contexto, o 'normal'."""
        return self.env.context.get('default_priority') or 'normal'

    priority = fields.Selection(
        [
            ('normal', 'Normal'),
            ('urgent', 'Urgente'),
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

    # --- Trazabilidad con requisition.allocation ---
    allocation_ids = fields.One2many(
        'requisition.allocation',
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
            ('in_stock', 'Solicitado a Almacén'),
            ('in_purchase', 'Solicitado a Compra'),
            ('in_mixed', 'Solicitud Mixta'),
            ('partial_stock', 'Solicitado Parcial (Almacén)'),
            ('partial_purchase', 'Solicitado Parcial (Compra)'),
            ('partial_mixed', 'Solicitado Parcial (Mixto)'),
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
                line.priority = 'normal'

    def _get_qty_unpurchased(self):
        """
        Retorna la cantidad neta de la línea que aún no ha sido solicitada en compra
        ni pedida en una orden de compra activa.
        """
        self.ensure_one()
        if self.line_state in ('done', 'cancel') or self.product_qty <= 0:
            return 0.0
        purchased_allocs = self.allocation_ids.filtered(
            lambda a: (bool(a.purchase_line_id) or bool(a.purchase_request_line_id)) and a.state != 'cancel'
        )
        purchased_qty = sum(purchased_allocs.mapped('requested_qty'))
        return max(0.0, self.product_qty - self.qty_cancelled - self.qty_done - purchased_qty)

    def _get_qty_unsupplied(self):
        """
        Retorna la cantidad neta de la línea que aún no ha sido asignada a una
        partida de Solicitud de Suministro o entrega directa activa.
        """
        self.ensure_one()
        if self.line_state in ('done', 'cancel') or self.product_qty <= 0 or self.line_type == 'service':
            return 0.0
        supplied_allocs = self.allocation_ids.filtered(
            lambda a: (bool(a.supply_request_line_id) or bool(a.stock_move_id)) and a.state != 'cancel'
        )
        supplied_qty = sum(supplied_allocs.mapped('requested_qty'))
        return max(0.0, self.product_qty - self.qty_cancelled - self.qty_done - supplied_qty)

    @api.depends(
        'allocation_ids.requested_qty',
        'allocation_ids.delivered_qty',
        'allocation_ids.open_qty',
        'allocation_ids.cancelled_qty',
        'allocation_ids.state',
        'allocation_ids.stage',
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
            purchase_allocs = line.allocation_ids.filtered(
                lambda a: bool(a.purchase_line_id) or bool(a.purchase_request_line_id)
            )
            stock_allocs = line.allocation_ids.filtered(
                lambda a: bool(a.stock_move_id) or bool(a.supply_request_line_id) or (not a.purchase_line_id and not a.purchase_request_line_id)
            )

            # 1. Cantidad Suministrada (Física al solicitante u obra destino):
            # - Asignaciones de despacho al solicitante (stage == 'outbound')
            # - Compras de servicios (donde la recepción en OC completa directamente el servicio)
            outbound_delivered = sum(
                line.allocation_ids.filtered(lambda a: a.stage == 'outbound').mapped('delivered_qty')
            )
            service_purchase_delivered = 0.0
            if line.line_type == 'service':
                service_purchase_delivered = sum(
                    purchase_allocs.filtered(lambda a: a.stage == 'inbound').mapped('delivered_qty')
                )
            line.qty_done = outbound_delivered + service_purchase_delivered

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
                # Calcular saldo en tránsito unificado para evitar doble cómputo si una asignación tiene suministro y compra
                active_allocs = line.allocation_ids.filtered(lambda a: a.state in ('draft', 'in_progress'))
                total_in_flight = sum(active_allocs.mapped('open_qty'))
                line.qty_pending = max(0.0, line.product_qty - line.qty_done - total_in_flight - line.qty_cancelled)

    @api.depends('allocation_ids.qty_returned', 'allocation_ids.delivered_qty', 'supply_type', 'qty_done')
    def _compute_custody_quantities(self):
        for line in self:
            if line.supply_type not in ('resg', 'custody'):
                line.qty_returned = 0.0
                line.qty_in_custody = 0.0
                line.custody_state = 'none'
            else:
                outbound_allocs = line.allocation_ids.filtered(lambda a: a.stage == 'outbound')
                line.qty_returned = sum(outbound_allocs.mapped('qty_returned'))
                line.qty_in_custody = max(0.0, line.qty_done - line.qty_returned)
                if line.qty_done <= 0:
                    line.custody_state = 'none'
                elif line.qty_in_custody <= 0 and line.qty_returned >= line.qty_done:
                    line.custody_state = 'returned'
                elif line.qty_returned > 0 and line.qty_in_custody > 0:
                    line.custody_state = 'partially_returned'
                else:
                    line.custody_state = 'in_custody'

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
                has_stock = line.qty_allocated_stock > 0
                has_purchase = line.qty_allocated_purchase > 0
                if has_stock and has_purchase:
                    line.line_state = 'in_mixed'
                elif has_purchase:
                    line.line_state = 'in_purchase'
                else:
                    line.line_state = 'in_stock'
            elif (line.qty_allocated_stock + line.qty_allocated_purchase) > 0:
                has_stock = line.qty_allocated_stock > 0
                has_purchase = line.qty_allocated_purchase > 0
                if has_stock and has_purchase:
                    line.line_state = 'partial_mixed'
                elif has_purchase:
                    line.line_state = 'partial_purchase'
                else:
                    line.line_state = 'partial_stock'
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

                # 1. Calcular la demanda neta pendiente no surtida
                net_needed = max(0.0, new_qty - line.qty_done)
                active_allocs = line.allocation_ids.filtered(lambda a: a.state not in ('done', 'cancel'))

                if active_allocs:
                    if net_needed == 0.0:
                        for alloc in active_allocs:
                            if alloc.delivered_qty > 0:
                                alloc.write({'requested_qty': alloc.delivered_qty, 'state': 'done'})
                            else:
                                alloc.action_cancel_allocation(
                                    cancel_action='close',
                                    reason=_("Demanda cerrada por reducción de cantidad en requisición a %.2f %s") % (new_qty, uom_name)
                                )
                    elif len(active_allocs) == 1:
                        alloc = active_allocs[0]
                        target_qty = max(alloc.delivered_qty, net_needed)
                        alloc.write({'requested_qty': target_qty})
                        alloc._sync_origin_document_status()
                    else:
                        current_total = sum(active_allocs.mapped('requested_qty'))
                        if current_total > 0 and current_total != net_needed:
                            ratio = net_needed / current_total
                            allocated_so_far = 0.0
                            for idx, alloc in enumerate(active_allocs):
                                if idx == len(active_allocs) - 1:
                                    target_qty = max(alloc.delivered_qty, net_needed - allocated_so_far)
                                else:
                                    target_qty = max(alloc.delivered_qty, round(alloc.requested_qty * ratio, 2))
                                    allocated_so_far += target_qty
                                alloc.write({'requested_qty': target_qty})
                                alloc._sync_origin_document_status()

                # 2. Sincronizar documentos dependientes ajustando a las asignaciones correspondientes
                for alloc in line.allocation_ids.filtered(lambda a: a.state not in ('done', 'cancel')):
                    if alloc.stock_move_id and alloc.stock_move_id.state in ('draft', 'waiting', 'confirmed', 'assigned'):
                        alloc.stock_move_id.product_uom_qty = alloc.requested_qty
                        if alloc.stock_move_id.picking_id:
                            alloc.stock_move_id.picking_id.message_post(body=chatter_msg, subtype_xmlid="mail.mt_note")

                    if alloc.supply_request_line_id and alloc.supply_request_line_id.state not in ('cancelled', 'done_exact', 'done_adjusted'):
                        alloc.supply_request_line_id.product_qty = alloc.requested_qty
                        if alloc.supply_request_line_id.request_id:
                            alloc.supply_request_line_id.request_id.message_post(body=chatter_msg, subtype_xmlid="mail.mt_note")

                    if alloc.purchase_request_line_id and alloc.purchase_request_line_id.state not in ('cancel', 'purchased'):
                        alloc.purchase_request_line_id.product_qty = alloc.requested_qty
                        alloc.purchase_request_line_id.message_post(body=chatter_msg, subtype_xmlid="mail.mt_note")

                    if alloc.purchase_line_id and alloc.purchase_line_id.order_id.state in ('draft', 'sent', 'to approve'):
                        target_uom = getattr(alloc.purchase_line_id, 'product_uom_id', False) or getattr(alloc.purchase_line_id, 'product_uom', False) or line.product_uom_id
                        converted_qty = line.product_uom_id._compute_quantity(alloc.requested_qty, target_uom)
                        alloc.purchase_line_id.product_qty = converted_qty
                        alloc.purchase_line_id.order_id.message_post(body=chatter_msg, subtype_xmlid="mail.mt_note")

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
