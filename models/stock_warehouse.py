# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class StockWarehouse(models.Model):
    """
    Extensión de stock.warehouse para facilitar la configuración de rutas
    de suministro hacia obras y monitorear los tipos de operación asociados.
    """
    _inherit = 'stock.warehouse'

    requisition_routes_count = fields.Integer(
        string='Rutas de Suministro',
        compute='_compute_requisition_routes_count',
        help='Cantidad de rutas de suministro configuradas desde este almacén hacia obras.'
    )
    is_default_requisition_warehouse = fields.Boolean(
        string='Es Almacén Base Predeterminado',
        compute='_compute_is_default_requisition_warehouse',
        help='Indica si este almacén está designado como el almacén base predeterminado de la compañía para requisiciones.'
    )

    @api.depends('company_id.requisition_default_warehouse_origin_id')
    def _compute_is_default_requisition_warehouse(self):
        for wh in self:
            default_wh = wh.company_id.requisition_default_warehouse_origin_id
            wh.is_default_requisition_warehouse = bool(default_wh and wh.id == default_wh.id)

    def _compute_requisition_routes_count(self):
        """Agregación optimizada por lote vía _read_group para erradicar consultas N+1."""
        active_routes = self.env['stock.picking.type']._read_group(
            domain=[
                ('warehouse_id', 'in', self.ids),
                ('is_requisition_operation', '=', True),
            ],
            groupby=['warehouse_id'],
            aggregates=['__count']
        )
        routes_map = {warehouse.id: count for warehouse, count in active_routes}
        for wh in self:
            wh.requisition_routes_count = routes_map.get(wh.id, 0)

    def action_open_requisition_warehouse_setup(self):
        """Abre el asistente interactivo para aprovisionar rutas de suministro hacia obras."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Configurar Rutas de Suministro para Requisiciones'),
            'res_model': 'requisition.warehouse.setup.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_warehouse_origin_id': self.id,
            },
        }

    def action_view_requisition_picking_types(self):
        """Muestra los tipos de operación interna configurados para requisiciones de este almacén."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Rutas de Suministro - %s') % self.name,
            'res_model': 'stock.picking.type',
            'view_mode': 'list,form',
            'domain': [
                ('warehouse_id', '=', self.id),
                ('is_requisition_operation', '=', True),
            ],
            'context': {
                'default_warehouse_id': self.id,
                'default_is_requisition_operation': True,
                'default_code': 'internal',
            },
        }
