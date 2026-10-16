# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

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
