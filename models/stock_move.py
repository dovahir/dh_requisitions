# -*- coding: utf-8 -*-
from odoo import fields, models


class StockMove(models.Model):
    """
    Extensión de stock.move para asociar líneas de Solicitud de Suministro
    y preservar la trazabilidad granular.
    """
    _inherit = 'stock.move'

    supply_request_line_id = fields.Many2one(
        'requisition.supply.request.line',
        string='Línea de Solicitud de Suministro',
        index=True,
        copy=False,
        help="Partida de la solicitud de suministro asociada a este movimiento."
    )
    requisition_line_id = fields.Many2one(
        'requisitions.line',
        string='Línea de Requisición (Origen)',
        index=True,
        copy=False,
        help="Línea de requisición origen asociada a este movimiento."
    )
    requisition_id = fields.Many2one(
        'requisitions',
        related='requisition_line_id.requisition_id',
        string='Requisición',
        store=True,
        index=True,
    )

    def _prepare_merge_moves_distinct_fields(self):
        """Impide fusionar movimientos que provengan de distintas partidas de solicitud de suministro."""
        distinct_fields = super()._prepare_merge_moves_distinct_fields()
        distinct_fields.extend(['supply_request_line_id', 'requisition_line_id'])
        return distinct_fields
