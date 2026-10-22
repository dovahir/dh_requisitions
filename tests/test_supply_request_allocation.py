# -*- coding: utf-8 -*-
from odoo.tests import common, tagged
from odoo.exceptions import UserError, ValidationError


@tagged('post_install', '-at_install', 'dh_requisitions')
class TestSupplyRequestAllocation(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

        # Almacenes Base y Obra
        cls.wh_base = cls.env['stock.warehouse'].create({
            'name': 'Almacén Base Central Test',
            'code': 'BASE_T',
            'company_id': cls.company.id,
        })
        cls.wh_obra = cls.env['stock.warehouse'].create({
            'name': 'Almacén Obra Norte Test',
            'code': 'OBRA_T',
            'company_id': cls.company.id,
        })

        # Ubicación de tránsito
        cls.transit_loc = cls.company.internal_transit_location_id or cls.env['stock.location'].create({
            'name': 'Ubicación Tránsito Test',
            'usage': 'transit',
            'company_id': cls.company.id,
        })

        # Tipos de operación
        cls.picking_type_out = cls.env['stock.picking.type'].create({
            'name': 'Traspaso a Tránsito Test',
            'sequence_code': 'TRA_OUT',
            'code': 'internal',
            'warehouse_id': cls.wh_base.id,
            'default_location_src_id': cls.wh_base.lot_stock_id.id,
            'default_location_dest_id': cls.transit_loc.id,
            'company_id': cls.company.id,
        })
        cls.picking_type_in = cls.env['stock.picking.type'].create({
            'name': 'Recepción en Obra Test',
            'sequence_code': 'TRA_IN',
            'code': 'internal',
            'warehouse_id': cls.wh_obra.id,
            'default_location_src_id': cls.transit_loc.id,
            'default_location_dest_id': cls.wh_obra.lot_stock_id.id,
            'company_id': cls.company.id,
        })
        cls.picking_type_out.default_dest_picking_type = cls.picking_type_in

        # Empleado y Producto
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Juan Pérez Residente Test',
            'company_id': cls.company.id,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Cemento Gris 50kg Test',
            'type': 'consu',
            'is_storable': True,
            'company_id': cls.company.id,
        })
        cls.env.user.write({
            'group_ids': [
                (4, cls.env.ref('dh_requisitions.group_requisitions_approver').id),
                (4, cls.env.ref('dh_requisitions.group_requisitions_manager').id),
                (4, cls.env.ref('stock.group_stock_user').id),
            ]
        })
        cls.env['stock.quant'].create({
            'product_id': cls.product.id,
            'location_id': cls.wh_base.lot_stock_id.id,
            'quantity': 100.0,
        })

    def test_01_preventive_reservation_in_draft_supply_request(self):
        """Valida que una solicitud de suministro en borrador descuenta el saldo pendiente en la requisición."""
        requisition = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'user_id': self.env.user.id,
            'warehouse_id': self.wh_obra.id,
            'company_id': self.company.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 10.0,
                'line_type': 'material',
            })],
        })
        requisition.action_confirm()
        requisition.action_approve()
        req_line = requisition.line_ids[0]
        self.assertEqual(req_line.qty_pending, 10.0)
        self.assertEqual(req_line.qty_allocated_stock, 0.0)

        # Generar solicitud de suministro mediante el wizard para 6.0 unidades
        wizard = self.env['requisition.supply.request.wizard'].with_context(
            active_id=requisition.id
        ).create({
            'requisition_id': requisition.id,
            'warehouse_origin_id': self.wh_base.id,
            'line_ids': [(0, 0, {
                'selected': True,
                'requisition_line_id': req_line.id,
                'product_qty': 6.0,
            })]
        })
        action = wizard.action_confirm()
        supply_req = self.env['requisition.supply.request'].browse(action['res_id'])
        self.assertEqual(supply_req.state, 'draft')
        self.assertEqual(len(supply_req.line_ids), 1)

        sr_line = supply_req.line_ids[0]
        self.assertEqual(sr_line.product_qty, 6.0)
        self.assertTrue(bool(sr_line.allocation_ids))

        # Verificar reserva preventiva: qty_allocated_stock = 6.0, qty_pending = 4.0
        req_line._compute_quantities()
        self.assertEqual(req_line.qty_allocated_stock, 6.0)
        self.assertEqual(req_line.qty_pending, 4.0)
        self.assertEqual(req_line.qty_done, 0.0)

    def test_02_two_leg_lifecycle_and_physical_reception(self):
        """Valida el ciclo de dos tramos: en tránsito (open_qty) y entrega efectiva en obra (delivered_qty)."""
        requisition = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'user_id': self.env.user.id,
            'warehouse_id': self.wh_obra.id,
            'company_id': self.company.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 8.0,
                'line_type': 'material',
            })],
        })
        requisition.action_confirm()
        requisition.action_approve()
        req_line = requisition.line_ids[0]

        supply_req = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_base.id,
            'warehouse_dest_id': self.wh_obra.id,
            'picking_type_id': self.picking_type_out.id,
            'picking_type_dest_id': self.picking_type_in.id,
            'location_id': self.picking_type_out.default_location_src_id.id,
            'location_dest_id': self.picking_type_in.default_location_dest_id.id,
            'requisition_ids': [(4, requisition.id)],
            'line_ids': [(0, 0, {
                'requisition_line_id': req_line.id,
                'product_id': self.product.id,
                'product_qty': 8.0,
            })]
        })
        sr_line = supply_req.line_ids[0]
        self.assertTrue(bool(sr_line.allocation_ids))
        alloc = sr_line.allocation_ids[0]

        # 1. Crear Entrega hacia Tránsito
        supply_req.action_create_delivery()
        self.assertEqual(supply_req.state, 'delivery_created')
        self.assertEqual(supply_req.outgoing_count, 1)

        out_picking = supply_req.picking_ids.filtered(lambda p: p.location_id.id == supply_req.location_id.id)
        self.assertTrue(bool(out_picking))

        # Validar salida de almacén base
        for move in out_picking.move_ids:
            move.quantity = 8.0
        out_picking.button_validate()

        # Al validarse salida a tránsito: estado in_transit y recepciones = 1
        supply_req._compute_overall_state()
        self.assertEqual(supply_req.state, 'in_transit')
        self.assertEqual(supply_req.incoming_count, 1)

        # En la asignación, la mercancía sigue en tránsito (open_qty = 8.0, delivered_qty = 0.0)
        self.assertEqual(alloc.delivered_qty, 0.0)
        self.assertEqual(alloc.open_qty, 8.0)
        self.assertEqual(alloc.state, 'in_progress')

        # En la requisición, la cantidad sigue en tránsito / asignada
        req_line._compute_quantities()
        self.assertEqual(req_line.qty_done, 0.0)
        self.assertEqual(req_line.qty_allocated_stock, 8.0)

        # 2. Recepción Física en Obra
        in_picking = supply_req.picking_ids.filtered(lambda p: p.location_dest_id.id == supply_req.location_dest_id.id)
        self.assertTrue(bool(in_picking))

        for move in in_picking.move_ids:
            move.quantity = 8.0
        in_picking.button_validate()

        # Al validarse recepción en obra: se asienta delivered_qty en allocation y en requisición
        supply_req._compute_overall_state()
        self.assertEqual(supply_req.state, 'done_exact')

        alloc.invalidate_recordset()
        self.assertEqual(alloc.delivered_qty, 8.0)
        self.assertEqual(alloc.state, 'done')

        req_line.invalidate_recordset()
        req_line._compute_quantities()
        self.assertEqual(req_line.qty_done, 8.0)
        self.assertEqual(req_line.qty_allocated_stock, 0.0)
        self.assertEqual(req_line.qty_pending, 0.0)

    def test_03_transfer_report_action_generation(self):
        """Valida que el reporte de traspaso puede emitirse tanto desde la solicitud como desde el albarán."""
        supply_req = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_base.id,
            'warehouse_dest_id': self.wh_obra.id,
            'picking_type_id': self.picking_type_out.id,
            'picking_type_dest_id': self.picking_type_in.id,
            'location_id': self.picking_type_out.default_location_src_id.id,
            'location_dest_id': self.picking_type_in.default_location_dest_id.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 5.0,
            })]
        })
        # 1. Emisión desde Solicitud
        action_from_req = supply_req.action_print_transfer_report()
        report_data = action_from_req if action_from_req.get('type') == 'ir.actions.report' else action_from_req.get('context', {}).get('report_action', {})
        self.assertIn(action_from_req.get('type'), ('ir.actions.report', 'ir.actions.act_window'))
        self.assertEqual(report_data.get('report_name'), 'dh_requisitions.report_supply_transfer_document')

        # 2. Emisión desde Picking
        supply_req.action_create_delivery()
        out_picking = supply_req.picking_ids[0]
        action_from_pick = out_picking.action_print_transfer_report()
        report_data_pick = action_from_pick if action_from_pick.get('type') == 'ir.actions.report' else action_from_pick.get('context', {}).get('report_action', {})
        self.assertIn(action_from_pick.get('type'), ('ir.actions.report', 'ir.actions.act_window'))
        self.assertEqual(report_data_pick.get('report_name'), 'dh_requisitions.report_supply_transfer_document')

    def test_04_supply_request_line_crud_allocation_sync(self):
        """Valida que modificar cantidades o borrar líneas en borrador sincroniza supply.chain.allocation."""
        requisition = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'user_id': self.env.user.id,
            'warehouse_id': self.wh_obra.id,
            'company_id': self.company.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 15.0,
                'line_type': 'material',
            })],
        })
        requisition.action_confirm()
        requisition.action_approve()
        req_line = requisition.line_ids[0]

        supply_req = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_base.id,
            'warehouse_dest_id': self.wh_obra.id,
            'picking_type_id': self.picking_type_out.id,
            'picking_type_dest_id': self.picking_type_in.id,
            'location_id': self.picking_type_out.default_location_src_id.id,
            'location_dest_id': self.picking_type_in.default_location_dest_id.id,
            'requisition_ids': [(4, requisition.id)],
            'line_ids': [(0, 0, {
                'requisition_line_id': req_line.id,
                'product_id': self.product.id,
                'product_qty': 5.0,
            })]
        })
        sr_line = supply_req.line_ids[0]
        alloc = sr_line.allocation_ids[0]
        self.assertEqual(alloc.requested_qty, 5.0)

        # Modificar cantidad en borrador
        sr_line.write({'product_qty': 9.0})
        self.assertEqual(alloc.requested_qty, 9.0)
        req_line._compute_quantities()
        self.assertEqual(req_line.qty_allocated_stock, 9.0)
        self.assertEqual(req_line.qty_pending, 6.0)

        # Eliminar línea en borrador: libera la asignación y restaura saldo en la requisición
        sr_line.unlink()
        self.assertFalse(alloc.exists())
        req_line._compute_quantities()
        self.assertEqual(req_line.qty_allocated_stock, 0.0)
        self.assertEqual(req_line.qty_pending, 15.0)
