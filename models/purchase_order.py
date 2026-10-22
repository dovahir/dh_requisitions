# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from markupsafe import Markup, escape


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    req_note = fields.Text(
        string="Notas de Requisición",
        readonly=True,
        help="Notas generales transferidas desde la requisición de origen."
    )
    purchase_request_line_ids = fields.Many2many(
        'requisition.purchase.request.line',
        string='Solicitudes de Compra Origen',
        compute='_compute_purchase_request_line_ids',
        help='Líneas de solicitud de compra vinculadas a través de asignaciones transversales'
    )
    purchase_request_line_count = fields.Integer(
        string='Líneas de Solicitud',
        compute='_compute_purchase_request_line_count'
    )

    @api.depends('order_line.allocation_ids.purchase_request_line_id')
    def _compute_purchase_request_line_ids(self):
        for order in self:
            req_lines = order.order_line.allocation_ids.mapped('purchase_request_line_id').filtered(bool)
            order.purchase_request_line_ids = [(6, 0, req_lines.ids)]

    @api.depends('purchase_request_line_ids')
    def _compute_purchase_request_line_count(self):
        for order in self:
            order.purchase_request_line_count = len(order.purchase_request_line_ids)

    def action_open_purchase_request_lines(self):
        """Abre la lista de líneas de solicitud vinculadas a esta orden de compra."""
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id('dh_requisitions.action_requisition_purchase_request_line')
        action['domain'] = [('id', 'in', self.purchase_request_line_ids.ids)]
        action['context'] = {'default_company_id': self.company_id.id}
        return action

    def button_confirm(self):
        res = super().button_confirm()
        for order in self:
            # 1. Enlazar movimientos entrantes o acreditar servicios
            for line in order.order_line:
                if line.allocation_ids:
                    if line.product_id.type == 'service':
                        # Los servicios completan su entrega en la confirmación de compra
                        for alloc in line.allocation_ids.filtered(lambda a: a.state != 'cancel'):
                            alloc.delivered_qty = alloc.requested_qty
                            alloc.state = 'done'
                            alloc._sync_origin_document_status()
                    else:
                        incoming_moves = line.move_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
                        if incoming_moves:
                            allocs_to_link = line.allocation_ids.filtered(
                                lambda a: not a.purchase_stock_move_id and a.state != 'cancel'
                            )
                            for alloc in allocs_to_link:
                                alloc.purchase_stock_move_id = incoming_moves[0].id

            # 2. Notificar en solicitudes de compra vinculadas
            req_lines = order.purchase_request_line_ids
            for line in req_lines:
                line._compute_quantities()
                line._compute_state()
                line.message_post(
                    body=_("La orden de compra <strong>%s</strong> ha sido confirmada. Partida en espera de recepción en bodega.") % order.name,
                    subtype_xmlid="mail.mt_note"
                )
        return res

    def button_cancel(self):
        res = super().button_cancel()
        for order in self:
            active_allocs = order.order_line.allocation_ids.filtered(lambda a: a.state != 'cancel')
            if active_allocs:
                active_allocs.action_cancel_allocation(
                    cancel_action='reintegrate',
                    reason=_("Orden de compra %s cancelada") % order.name
                )

            req_lines = order.purchase_request_line_ids
            for line in req_lines:
                line._compute_quantities()
                line._compute_state()
                line.message_post(
                    body=_("La orden de compra <strong>%s</strong> fue cancelada. Saldos recalculados en la solicitud.") % order.name,
                    subtype_xmlid="mail.mt_note"
                )
        return res


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    allocation_ids = fields.One2many(
        'requisition.allocation',
        'purchase_line_id',
        string='Asignaciones de Requisición'
    )
    purchase_request_lines = fields.Many2many(
        'requisition.purchase.request.line',
        string='Solicitudes Origen',
        compute='_compute_purchase_request_lines',
        help='Líneas de solicitud asociadas a esta partida de compra'
    )

    @api.depends('allocation_ids.purchase_request_line_id')
    def _compute_purchase_request_lines(self):
        for line in self:
            req_lines = line.allocation_ids.mapped('purchase_request_line_id').filtered(bool)
            line.purchase_request_lines = [(6, 0, req_lines.ids)]

    def action_open_request_line(self):
        self.ensure_one()
        req_lines = self.purchase_request_lines
        if not req_lines:
            return {'type': 'ir.actions.act_window_close'}
        action = self.env['ir.actions.actions']._for_xml_id('dh_requisitions.action_requisition_purchase_request_line')
        action['domain'] = [('id', 'in', req_lines.ids)]
        return action

    def write(self, vals):
        res = super().write(vals)
        # Sincronización cuando se modifica la cantidad en la línea de compra (product_qty)
        if 'product_qty' in vals:
            for line in self:
                allocations = line.allocation_ids.filtered(lambda a: a.state != 'cancel')
                if not allocations:
                    continue
                new_qty = vals['product_qty']
                if len(allocations) == 1:
                    alloc = allocations[0]
                    target_qty = new_qty
                    uom = line.product_uom_id or getattr(line, 'product_uom', False)
                    if uom and alloc.product_uom_id and uom != alloc.product_uom_id:
                        target_qty = uom._compute_quantity(new_qty, alloc.product_uom_id)
                    final_qty = max(target_qty, alloc.delivered_qty)
                    if alloc.requested_qty != final_qty:
                        alloc.write({'requested_qty': final_qty})
                else:
                    current_total = sum(allocations.mapped('requested_qty'))
                    if current_total > 0 and current_total != new_qty:
                        ratio = new_qty / current_total
                        for alloc in allocations:
                            final_qty = max(alloc.requested_qty * ratio, alloc.delivered_qty)
                            if alloc.requested_qty != final_qty:
                                alloc.write({'requested_qty': final_qty})
                for alloc in allocations:
                    alloc._sync_origin_document_status()

        # Sincronización para servicios: cuando cambia qty_received se actualiza la asignación
        if 'qty_received' in vals:
            for line in self:
                if line.product_id.type == 'service':
                    for alloc in line.allocation_ids.filtered(lambda a: a.state != 'cancel'):
                        alloc.delivered_qty = min(line.qty_received, alloc.requested_qty)
                        alloc._sync_origin_document_status()
    def copy(self, default=None):
        if default is None:
            default = {}
        # Identificar si la línea original tiene asignaciones activas de requisición
        active_allocs = self.allocation_ids.filtered(lambda a: a.state != 'cancel' and a.open_qty > 0)

        new_line = super().copy(default=default)

        if active_allocs and not self.env.context.get('skip_allocation_copy'):
            for alloc in active_allocs:
                open_qty = alloc.open_qty
                if open_qty > 1.0:
                    split_qty = round(open_qty / 2.0, 2)
                    orig_alloc_qty = alloc.requested_qty - split_qty
                    alloc.write({'requested_qty': orig_alloc_qty})

                    if 'product_qty' not in default:
                        orig_line_qty = self.product_qty - split_qty
                        self.with_context(skip_allocation_copy=True).write({'product_qty': orig_line_qty})
                        new_line.with_context(skip_allocation_copy=True).write({'product_qty': split_qty})

                    self.env['requisition.allocation'].create({
                        'company_id': alloc.company_id.id,
                        'product_id': alloc.product_id.id,
                        'product_uom_id': alloc.product_uom_id.id,
                        'requested_qty': split_qty,
                        'delivered_qty': 0.0,
                        'supply_type': alloc.supply_type,
                        'stage': alloc.stage or 'inbound',
                        'parent_allocation_id': alloc.parent_allocation_id.id if alloc.parent_allocation_id else False,
                        'purchase_request_line_id': alloc.purchase_request_line_id.id if alloc.purchase_request_line_id else False,
                        'requisition_line_id': alloc.requisition_line_id.id if alloc.requisition_line_id else False,
                        'supply_request_line_id': alloc.supply_request_line_id.id if alloc.supply_request_line_id else False,
                        'purchase_line_id': new_line.id,
                        'state': 'in_progress' if new_line.order_id.state in ('draft', 'sent', 'to approve', 'purchase') else 'draft',
                    })

                    uom = self.product_uom_id or getattr(self, 'product_uom', False)
                    uom_name = uom.name if uom else ''
                    if self.order_id:
                        self.order_id.message_post(
                            body=Markup(_(
                                "<strong>Línea duplicada:</strong> La asignación de requisición para el producto <em>%s</em> "
                                "se dividió entre la línea original (<strong>%s %s</strong>) y la nueva línea duplicada (<strong>%s %s</strong>)."
                            )) % (
                                escape(self.product_id.display_name),
                                self.product_qty, escape(uom_name),
                                new_line.product_qty, escape(uom_name)
                            ),
                            subtype_xmlid="mail.mt_note"
                        )
                else:
                    uom = self.product_uom_id or getattr(self, 'product_uom', False)
                    uom_name = uom.name if uom else ''
                    if self.order_id:
                        self.order_id.message_post(
                            body=Markup(_(
                                "<strong>Línea duplicada:</strong> La nueva línea para el producto <em>%s</em> (%s %s) "
                                "se generó sin asignación a requisición (considerada compra excedente o adicional)."
                            )) % (
                                escape(self.product_id.display_name),
                                new_line.product_qty, escape(uom_name)
                            ),
                            subtype_xmlid="mail.mt_note"
                        )
        return new_line

    def unlink(self):
        """Al eliminar una línea de compra, cancela las asignaciones activas reintegrando el saldo."""
        allocs_to_notify = self.env['requisition.allocation']
        for line in self:
            active_allocs = line.allocation_ids.filtered(lambda a: a.state != 'cancel')
            if active_allocs:
                active_allocs.action_cancel_allocation(
                    cancel_action='reintegrate',
                    reason=_("Línea de compra eliminada")
                )
                allocs_to_notify |= active_allocs
        res = super().unlink()
        for alloc in allocs_to_notify:
            alloc._sync_origin_document_status()
        return res
