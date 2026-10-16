# -*- coding: utf-8 -*-
from odoo.tests import common, tagged
from odoo.exceptions import UserError


@tagged('post_install', '-at_install', 'dh_requisitions')
class TestSupplyRequest(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.wh_origin = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        if not cls.wh_origin:
            cls.wh_origin = cls.env['stock.warehouse'].create({
                'name': 'Almacén Base Test',
                'code': 'WH_BASE',
                'company_id': cls.company.id,
            })

        cls.product = cls.env['product.product'].create({
            'name': 'Pintura Blanca 19L Test',
            'type': 'consu',
        })

    def test_01_supply_request_creation_and_deletion_protection(self):
        """Valida que una solicitud en curso no pueda eliminarse físicamente gracias a @api.ondelete."""
        supply_req = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_origin.id,
            'warehouse_dest_id': self.wh_origin.id,
            'location_id': self.wh_origin.lot_stock_id.id,
            'line_ids': [
                (0, 0, {
                    'product_id': self.product.id,
                    'product_qty': 4.0,
                })
            ]
        })
        self.assertEqual(supply_req.state, 'draft')
        self.assertEqual(len(supply_req.line_ids), 1)

        # En estado borrador sí se permite borrar
        line = supply_req.line_ids[0]
        # Al simular estado activo
        supply_req.write({'state': 'in_transit'})
        with self.assertRaises(UserError):
            supply_req.unlink()

        with self.assertRaises(UserError):
            line.unlink()
