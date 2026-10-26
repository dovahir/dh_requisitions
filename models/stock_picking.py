# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class StockPicking(models.Model):
    """
    Extensión de stock.picking para sincronizar albaranes con Solicitudes de Suministro en Odoo 19.
    """
    _inherit = 'stock.picking'

    supply_request_id = fields.Many2one(
        'requisition.supply.request',
        string='Solicitud de Suministro',
        index=True,
        copy=False,
        help="Solicitud de suministro de obra que originó este movimiento de almacén."
    )
    source_transfer_id = fields.Many2one(
        'stock.picking',
        string='Envío de Origen',
        index=True,
        readonly=True,
        copy=False,
        help="Albarán de despacho desde el almacén base que originó esta recepción en obra."
    )
    reception_picking_ids = fields.One2many(
        'stock.picking',
        'source_transfer_id',
        string='Recepciones Vinculadas'
    )
    reception_picking_count = fields.Integer(
        string='Recepciones',
        compute='_compute_reception_picking_count'
    )

    @api.depends('reception_picking_ids')
    def _compute_reception_picking_count(self):
        for rec in self:
            rec.reception_picking_count = len(rec.reception_picking_ids)

    def action_open_source_transfer(self):
        """Redirige al formulario del albarán de despacho de base que originó esta recepción."""
        self.ensure_one()
        if not self.source_transfer_id:
            raise UserError(_("Este albarán no tiene un envío de origen vinculado."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Envío de Origen'),
            'res_model': 'stock.picking',
            'res_id': self.source_transfer_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_reception_pickings(self):
        """Muestra los albaranes de recepción en obra generados a partir de este envío."""
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Recepciones Vinculadas'),
            'res_model': 'stock.picking',
            'domain': [('source_transfer_id', '=', self.id)],
        }
        if len(self.reception_picking_ids) == 1:
            action['views'] = [(False, 'form')]
            action['res_id'] = self.reception_picking_ids.id
        else:
            action['views'] = [(False, 'list'), (False, 'form')]
        return action

    def action_open_supply_request(self):
        """Redirige al formulario de la Solicitud de Suministro vinculada."""
        self.ensure_one()
        if not self.supply_request_id:
            raise UserError(_("Este albarán no está vinculado a ninguna Solicitud de Suministro."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Solicitud de Suministro'),
            'res_model': 'requisition.supply.request',
            'res_id': self.supply_request_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_print_transfer_report(self):
        """Genera e imprime el reporte de traspaso / remisión desde el albarán."""
        self.ensure_one()
        return self.env.ref('dh_requisitions.action_report_supply_transfer').report_action(self)

    def _prepare_backorder_values(self, picking):
        """Conserva la relación con la Solicitud de Suministro al generarse backorders."""
        vals = super()._prepare_backorder_values(picking)
        if self.supply_request_id:
            vals['supply_request_id'] = self.supply_request_id.id
        return vals

    def _is_return_picking(self):
        """Determina si el albarán corresponde a una devolución."""
        self.ensure_one()
        if hasattr(self, 'return_id') and self.return_id:
            return True
        for move in self.move_ids:
            if move.origin_returned_move_id:
                return True
        return False

    def _action_done(self):
        """Interpola la validación para desencadenar recepciones o actualizar el estado de la solicitud."""
        res = super()._action_done()
        for picking in self:
            if picking.supply_request_id and picking.state == 'done':
                picking.supply_request_id._process_picking_validation(picking)
        return res

    def action_cancel(self):
        """Notifica la cancelación del albarán a la Solicitud de Suministro."""
        res = super().action_cancel()
        if isinstance(res, dict):
            return res
        for picking in self:
            if picking.supply_request_id and picking.state == 'cancel':
                picking.supply_request_id._process_picking_cancel(picking)
        return res
