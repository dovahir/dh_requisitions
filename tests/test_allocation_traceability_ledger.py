# -*- coding: utf-8 -*-
from odoo.tests import common, tagged
from odoo.exceptions import UserError, ValidationError


@tagged('post_install', '-at_install', 'dh_requisitions')
class TestAllocationTraceabilityLedger(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.requisition_allow_auto_approval = False

        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({
                'name': 'Almacén Ledger Test',
                'code': 'WH_LEDGER',
                'company_id': cls.company.id,
            })
        cls.company.requisition_default_warehouse_origin_id = cls.warehouse.id

        cls.partner = cls.env['res.partner'].create({
            'name': 'Proveedor Ledger Test',
            'supplier_rank': 1,
            'company_id': cls.company.id,
        })

        cls.categ_material = cls.env['product.category'].create({
            'name': 'Materiales Ledger Test',
            'requisition_type': 'material',
            'allow_requisition': True,
        })

        cls.product = cls.env['product.product'].create({
            'name': 'Cemento Gris Test',
            'type': 'consu',
            'categ_id': cls.categ_material.id,
            'standard_price': 150.0,
        })

        cls.group_internal = cls.env.ref('base.group_user')
        cls.group_user = cls.env.ref('dh_requisitions.group_requisitions_requester')
        cls.group_approver = cls.env.ref('dh_requisitions.group_requisitions_approver')
        cls.group_manager = cls.env.ref('dh_requisitions.group_requisitions_manager')
        cls.group_stock = cls.env.ref('stock.group_stock_user')
        cls.group_purchase = cls.env.ref('purchase.group_purchase_user')

        cls.env.user.write({
            'group_ids': [
                (4, cls.group_approver.id),
                (4, cls.group_manager.id),
                (4, cls.group_stock.id),
                (4, cls.group_purchase.id),
            ]
        })

        cls.user_req = cls.env['res.users'].create({
            'name': 'Usuario Ledger Solicitante',
            'login': 'user_ledger_req',
            'email': 'user_ledger_req@test.com',
            'group_ids': [(6, 0, [cls.group_internal.id, cls.group_user.id])],
        })

        cls.employee = cls.env['hr.employee'].create({
            'name': 'Empleado Ledger Solicitante',
            'user_id': cls.user_req.id,
            'company_id': cls.company.id,
        })

    def test_partial_purchase_receipt_accumulation(self):
        """1. Acumulación correcta de recepciones de compra parciales en requisition.allocation."""
        po = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'company_id': self.company.id,
        })
        po_line = self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': self.product.id,
            'name': self.product.name,
            'product_qty': 10.0,
            'product_uom_id': self.product.uom_id.id,
            'price_unit': 150.0,
            'date_planned': '2026-11-01',
        })
        alloc = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'requested_qty': 10.0,
            'delivered_qty': 0.0,
            'supply_type': 'consumption',
            'stage': 'inbound',
            'purchase_line_id': po_line.id,
            'state': 'in_progress',
        })

        # Mock o movimiento de recepción 1 (4 unidades)
        move1 = self.env['stock.move'].create({
            'product_id': self.product.id,
            'product_uom_qty': 4.0,
            'quantity': 4.0,
            'product_uom': self.product.uom_id.id,
            'location_id': self.partner.property_stock_supplier.id,
            'location_dest_id': self.warehouse.lot_stock_id.id,
            'company_id': self.company.id,
            'purchase_line_id': po_line.id,
        })
        po_line.qty_received = 4.0
        alloc._on_purchase_received(move1, 4.0)

        self.assertEqual(alloc.delivered_qty, 4.0)
        self.assertEqual(alloc.state, 'in_progress')
        self.assertEqual(alloc.open_qty, 6.0)

        # Mock o movimiento de recepción 2 (6 unidades restantes)
        move2 = self.env['stock.move'].create({
            'product_id': self.product.id,
            'product_uom_qty': 6.0,
            'quantity': 6.0,
            'product_uom': self.product.uom_id.id,
            'location_id': self.partner.property_stock_supplier.id,
            'location_dest_id': self.warehouse.lot_stock_id.id,
            'company_id': self.company.id,
            'purchase_line_id': po_line.id,
        })
        po_line.qty_received = 10.0
        alloc._on_purchase_received(move2, 6.0)

        # La cantidad entregada debe ser 10.0 y no sobrescribirse con 6.0
        self.assertEqual(alloc.delivered_qty, 10.0)
        self.assertEqual(alloc.state, 'done')
        self.assertEqual(alloc.open_qty, 0.0)

    def test_rfq_line_copy_splits_allocation(self):
        """2. Duplicar una línea de compra con asignación fracciona equitativamente la asignación."""
        po = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'company_id': self.company.id,
        })
        po_line = self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': self.product.id,
            'name': self.product.name,
            'product_qty': 10.0,
            'product_uom_id': self.product.uom_id.id,
            'price_unit': 150.0,
            'date_planned': '2026-11-01',
        })
        alloc = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'requested_qty': 10.0,
            'delivered_qty': 0.0,
            'supply_type': 'consumption',
            'stage': 'inbound',
            'purchase_line_id': po_line.id,
            'state': 'in_progress',
        })

        # Duplicar la línea de pedido
        new_po_line = po_line.copy()

        # Cada línea debe tener 5.0 unidades y sus allocations correspondientes
        self.assertEqual(po_line.product_qty, 5.0)
        self.assertEqual(new_po_line.product_qty, 5.0)
        self.assertEqual(alloc.requested_qty, 5.0)

        self.assertEqual(len(new_po_line.allocation_ids), 1)
        sibling_alloc = new_po_line.allocation_ids[0]
        self.assertEqual(sibling_alloc.requested_qty, 5.0)
        self.assertEqual(sibling_alloc.stage, 'inbound')
        self.assertEqual(sibling_alloc.product_id, self.product)

    def test_end_to_end_traceability_chain(self):
        """3. Trazabilidad integral continua: Requisición -> Compra Inbound -> Despacho Outbound con filiación."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 10.0,
                'line_type': 'material',
            })],
        })
        req.action_confirm()
        req.action_approve()

        line = req.line_ids[0]
        req._create_purchase_request_lines()

        # Verificar que la allocation de abastecimiento es stage='inbound'
        self.assertEqual(len(line.allocation_ids), 1)
        inbound_alloc = line.allocation_ids[0]
        self.assertEqual(inbound_alloc.stage, 'inbound')
        self.assertEqual(line.qty_allocated_purchase, 10.0)
        self.assertEqual(line.qty_done, 0.0)
        self.assertEqual(line.qty_pending, 10.0)

        # Simular recepción en almacén de la compra
        inbound_alloc.delivered_qty = 10.0
        inbound_alloc.state = 'done'
        line._compute_quantities()

        # Material en bodega: qty_done aún es 0 (no se ha entregado al solicitante)
        self.assertEqual(line.qty_done, 0.0)
        self.assertEqual(line.qty_allocated_purchase, 0.0)
        self.assertEqual(line.qty_pending, 10.0)

        # Crear asistente de despacho (despacho final al solicitante)
        wiz = self.env['requisition.delivery.wizard'].with_context(active_id=req.id).create({})
        wiz.action_confirm()

        # Verificar que se creó la allocation de salida (stage='outbound') vinculada al inbound_alloc
        outbound_alloc = line.allocation_ids.filtered(lambda a: a.stage == 'outbound')
        self.assertTrue(outbound_alloc)
        self.assertEqual(outbound_alloc.parent_allocation_id, inbound_alloc)
        self.assertIn(outbound_alloc, inbound_alloc.child_allocation_ids)
        self.assertEqual(outbound_alloc.state, 'in_progress')

        # Completar la entrega física
        outbound_alloc.stock_move_id.state = 'done'
        outbound_alloc.delivered_qty = 10.0
        outbound_alloc.state = 'done'
        line._compute_quantities()
        req._update_state_from_lines()

        # La cantidad suministrada final al solicitante es 10.0 (sin doble conteo con la compra)
        self.assertEqual(line.qty_done, 10.0)
        self.assertEqual(line.qty_pending, 0.0)
        self.assertEqual(req.state, 'done')

    def _create_approved_requisition(self, qty=10.0):
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': qty,
                'line_type': 'material',
            })],
        })
        req.action_confirm()
        req.action_approve()
        return req

    def test_supply_request_then_purchase_request_unified_allocation(self):
        """
        Escenario solicitado: Si ya se solicitó suministro para una requisición (traslado Base -> Obra),
        se debe permitir solicitar compra para abastecer dicho suministro sin error ilógico de cantidad pendiente,
        vinculando ambos a la misma asignación (requisition.allocation unificada).
        """
        req = self._create_approved_requisition(qty=10.0)
        line = req.line_ids[0]

        # 1. Crear solicitud de suministro y su partida para los 10 items
        sr = self.env['requisition.supply.request'].create({
            'company_id': self.company.id,
            'warehouse_origin_id': self.warehouse.id,
            'warehouse_dest_id': self.warehouse.id,
        })
        sr_line = self.env['requisition.supply.request.line'].create({
            'request_id': sr.id,
            'requisition_line_id': line.id,
            'product_id': self.product.id,
            'product_qty': 10.0,
            'warehouse_dest_id': self.warehouse.id,
            'warehouse_origin_id': self.warehouse.id,
        })

        # Al crearse la línea de suministro, la requisición tiene asignado almacén y qty_pending = 10.0
        line._compute_quantities()
        self.assertEqual(line.qty_allocated_stock, 10.0)
        self.assertEqual(line.qty_pending, 10.0)
        self.assertEqual(len(line.allocation_ids), 1)
        alloc = line.allocation_ids[0]
        self.assertEqual(alloc.supply_request_line_id, sr_line)
        self.assertFalse(alloc.purchase_request_line_id)
        self.assertEqual(alloc.supply_request_id, sr)

        # 2. El usuario ahora hace clic en "Solicitar Compra" desde la Requisición
        # Esto NO debe arrojar UserError("No hay partidas con cantidad pendiente")
        action = req.action_create_purchase_request()
        self.assertTrue(action)
        wizard = self.env['requisition.purchase.request.wizard'].with_context(
            active_id=req.id, default_requisition_id=req.id
        ).create({})
        wizard.action_confirm()

        # 3. Validar que no se duplicó la asignación, sino que se vinculó a la existente
        self.assertEqual(len(line.allocation_ids), 1)
        pr_line = self.env['requisition.purchase.request.line'].search([
            ('requisition_line_id', '=', line.id)
        ], limit=1)
        self.assertTrue(pr_line)
        self.assertEqual(pr_line.product_qty, 10.0)
        self.assertEqual(alloc.purchase_request_line_id, pr_line)
        self.assertEqual(alloc.supply_request_line_id, sr_line)
        self.assertEqual(alloc.stage, 'inbound')
        self.assertEqual(alloc.supply_request_id, sr)

        # 4. Validar campos cruzados de trazabilidad bidireccional
        self.assertEqual(sr_line.purchase_request_line_id, pr_line)
        self.assertEqual(pr_line.supply_request_line_id, sr_line)
        self.assertEqual(pr_line.supply_request_id, sr)

        # 5. Validar que total_in_flight no genere doble cómputo
        line._compute_quantities()
        self.assertEqual(line.qty_allocated_stock, 10.0)
        self.assertEqual(line.qty_allocated_purchase, 10.0)
        self.assertEqual(line.qty_pending, 10.0)
        self.assertEqual(line.qty_done, 0.0)

    def test_purchase_request_then_supply_request_unified_allocation(self):
        """
        Escenario recíproco: Se crea primero la solicitud de compra, y posteriormente
        se programa la solicitud de suministro. Ambos deben converger en la misma asignación.
        """
        req = self._create_approved_requisition(qty=8.0)
        line = req.line_ids[0]

        # 1. Crear solicitud de compra primero
        req._create_purchase_request_lines()
        self.assertEqual(len(line.allocation_ids), 1)
        alloc = line.allocation_ids[0]
        pr_line = alloc.purchase_request_line_id
        self.assertTrue(pr_line)
        self.assertFalse(alloc.supply_request_line_id)

        # 2. Crear solicitud de suministro posteriormente
        sr = self.env['requisition.supply.request'].create({
            'company_id': self.company.id,
            'warehouse_origin_id': self.warehouse.id,
            'warehouse_dest_id': self.warehouse.id,
        })
        sr_line = self.env['requisition.supply.request.line'].create({
            'request_id': sr.id,
            'requisition_line_id': line.id,
            'product_id': self.product.id,
            'product_qty': 8.0,
            'warehouse_dest_id': self.warehouse.id,
            'warehouse_origin_id': self.warehouse.id,
        })

        # 3. Debe mantenerse en 1 sola asignación unificada
        self.assertEqual(len(line.allocation_ids), 1)
        self.assertEqual(alloc.supply_request_line_id, sr_line)
        self.assertEqual(alloc.purchase_request_line_id, pr_line)
        self.assertEqual(sr_line.purchase_request_line_id, pr_line)
        self.assertEqual(pr_line.supply_request_line_id, sr_line)
        self.assertEqual(pr_line.supply_request_id, sr)

    def test_inbound_transfer_arrival_does_not_double_count_outbound_delivery(self):
        """4. El arribo de un traslado Base -> Obra (inbound) no incrementa prematuramente qty_done ni duplica con el despacho (outbound)."""
        req = self._create_approved_requisition(qty=10.0)
        line = req.line_ids[0]

        sr = self.env['requisition.supply.request'].create({
            'company_id': self.company.id,
            'warehouse_origin_id': self.warehouse.id,
            'warehouse_dest_id': self.warehouse.id,
        })
        sr_line = self.env['requisition.supply.request.line'].create({
            'request_id': sr.id,
            'requisition_line_id': line.id,
            'product_id': self.product.id,
            'product_qty': 10.0,
            'warehouse_dest_id': self.warehouse.id,
            'warehouse_origin_id': self.warehouse.id,
        })

        inbound_alloc = line.allocation_ids.filtered(lambda a: a.stage == 'inbound')
        self.assertTrue(inbound_alloc)

        # Simular arribo del traslado al almacén de obra
        inbound_alloc.delivered_qty = 10.0
        inbound_alloc.state = 'done'
        line._compute_quantities()

        # En la requisición, el material está en bodega de obra pero NO entregado al solicitante
        self.assertEqual(line.qty_done, 0.0)
        self.assertEqual(line.qty_pending, 10.0)
        self.assertEqual(line.qty_allocated_stock, 0.0)

        # Despachar al solicitante mediante el asistente de entrega
        wiz = self.env['requisition.delivery.wizard'].with_context(active_id=req.id).create({})
        wiz.action_confirm()

        outbound_alloc = line.allocation_ids.filtered(lambda a: a.stage == 'outbound')
        self.assertTrue(outbound_alloc)
        self.assertEqual(outbound_alloc.parent_allocation_id, inbound_alloc)

        # Validar la entrega física al solicitante
        outbound_alloc.delivered_qty = 10.0
        outbound_alloc.state = 'done'
        line._compute_quantities()

        # qty_done debe ser exactamente 10.0 (NO 20.0)
        self.assertEqual(line.qty_done, 10.0)
        self.assertEqual(line.line_state, 'done')

    def test_purchase_return_to_vendor_reduces_delivered_qty(self):
        """5. La devolución a proveedor en albarán de compra reduce delivered_qty y reabre la asignación a in_progress."""
        po = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'company_id': self.company.id,
        })
        po_line = self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': self.product.id,
            'name': self.product.name,
            'product_qty': 10.0,
            'product_uom_id': self.product.uom_id.id,
            'price_unit': 150.0,
            'date_planned': '2026-11-01',
        })
        alloc = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'requested_qty': 10.0,
            'delivered_qty': 10.0,
            'supply_type': 'consumption',
            'stage': 'inbound',
            'purchase_line_id': po_line.id,
            'state': 'done',
        })

        # Simular albarán de recepción completado
        receipt_move = self.env['stock.move'].create({
            'product_id': self.product.id,
            'product_uom_qty': 10.0,
            'quantity': 10.0,
            'product_uom': self.product.uom_id.id,
            'location_id': self.partner.property_stock_supplier.id,
            'location_dest_id': self.warehouse.lot_stock_id.id,
            'company_id': self.company.id,
            'purchase_line_id': po_line.id,
            'state': 'done',
        })
        alloc.purchase_stock_move_id = receipt_move.id

        # Simular albarán de devolución a proveedor de 4 unidades
        return_move = self.env['stock.move'].create({
            'product_id': self.product.id,
            'product_uom_qty': 4.0,
            'quantity': 4.0,
            'product_uom': self.product.uom_id.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.partner.property_stock_supplier.id,
            'company_id': self.company.id,
            'origin_returned_move_id': receipt_move.id,
            'state': 'done',
        })

        # Invocar retorno
        alloc._on_purchase_returned(return_move, 4.0)

        # Cantidad en bodega debe reducirse a 6.0 y el estado debe reabrirse a in_progress
        self.assertEqual(alloc.delivered_qty, 6.0)
        self.assertEqual(alloc.state, 'in_progress')
        self.assertEqual(alloc.open_qty, 4.0)

    def test_partial_resguardo_return(self):
        """6. Devolución cuantitativa y estados de resguardo (in_resguardo, partially_returned, returned)."""
        categ_tool = self.env['product.category'].create({
            'name': 'Herramientas Resguardo Test',
            'requisition_type': 'tool',
            'allow_requisition': True,
        })
        product_tool = self.env['product.product'].create({
            'name': 'Taladro Percutor Test',
            'type': 'consu',
            'categ_id': categ_tool.id,
            'standard_price': 800.0,
        })

        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [(0, 0, {
                'product_id': product_tool.id,
                'product_qty': 3.0,
                'line_type': 'tool',
            })],
        })
        req.action_confirm()
        req.action_approve()

        line = req.line_ids[0]
        self.assertEqual(line.supply_type, 'resg')

        # Asignación outbound completada por 3 unidades
        alloc = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'product_id': product_tool.id,
            'product_uom_id': product_tool.uom_id.id,
            'requested_qty': 3.0,
            'delivered_qty': 3.0,
            'supply_type': 'resg',
            'stage': 'outbound',
            'requisition_line_id': line.id,
            'state': 'done',
        })
        line._compute_quantities()
        line._compute_resguardo_quantities()

        self.assertEqual(line.qty_done, 3.0)
        self.assertEqual(line.qty_in_resguardo, 3.0)
        self.assertEqual(line.qty_returned, 0.0)
        self.assertEqual(line.resguardo_state, 'in_resguardo')

        # Devolución parcial de 1 unidad
        mock_return_move = self.env['stock.move'].create({
            'product_id': product_tool.id,
            'product_uom_qty': 1.0,
            'quantity': 1.0,
            'product_uom': product_tool.uom_id.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.warehouse.lot_stock_id.id,
            'company_id': self.company.id,
        })
        alloc._on_move_returned(mock_return_move, 1.0)
        line._compute_resguardo_quantities()

        self.assertEqual(line.qty_returned, 1.0)
        self.assertEqual(line.qty_in_resguardo, 2.0)
        self.assertEqual(line.resguardo_state, 'partially_returned')

        # Devolución de las 2 unidades restantes
        mock_return_move2 = self.env['stock.move'].create({
            'product_id': product_tool.id,
            'product_uom_qty': 2.0,
            'quantity': 2.0,
            'product_uom': product_tool.uom_id.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.warehouse.lot_stock_id.id,
            'company_id': self.company.id,
        })
        alloc._on_move_returned(mock_return_move2, 2.0)
        line._compute_resguardo_quantities()

        self.assertEqual(line.qty_returned, 3.0)
        self.assertEqual(line.qty_in_resguardo, 0.0)
        self.assertEqual(line.resguardo_state, 'returned')

    def test_write_product_qty_with_prior_deliveries(self):
        """7. Modificar product_qty tras entregas previas ajusta únicamente la cantidad neta no surtida sin sobreasignar."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 10.0,
                'line_type': 'material',
            })],
        })
        line = req.line_ids[0]

        # 4 unidades ya entregadas físicamente
        alloc_delivered = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'requested_qty': 4.0,
            'delivered_qty': 4.0,
            'supply_type': 'consumption',
            'stage': 'outbound',
            'requisition_line_id': line.id,
            'state': 'done',
        })
        # 6 unidades activas en suministro
        alloc_active = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'requested_qty': 6.0,
            'delivered_qty': 0.0,
            'supply_type': 'consumption',
            'stage': 'inbound',
            'requisition_line_id': line.id,
            'state': 'in_progress',
        })
        line._compute_quantities()
        self.assertEqual(line.qty_done, 4.0)

        # Modificar requisición a product_qty = 8.0 (remanente neto necesario = 8.0 - 4.0 = 4.0)
        line.write({'product_qty': 8.0})

        # La asignación activa debe ajustarse a 4.0 (NO a 8.0)
        self.assertEqual(alloc_active.requested_qty, 4.0)
        line._compute_quantities()
        self.assertEqual(line.product_qty, 8.0)
        self.assertEqual(line.qty_done, 4.0)
        self.assertEqual(line.qty_allocated_stock, 4.0)
        self.assertEqual(line.qty_pending, 4.0)

    def test_service_po_confirmation_completes_allocation(self):
        """8. Confirmar orden de compra de servicios concluye la asignación y actualiza qty_done en requisición."""
        categ_service = self.env['product.category'].create({
            'name': 'Servicios Audit Test',
            'requisition_type': 'service',
            'allow_requisition': True,
        })
        product_svc = self.env['product.product'].create({
            'name': 'Servicio de Topografía Test',
            'type': 'service',
            'categ_id': categ_service.id,
            'standard_price': 5000.0,
        })

        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [(0, 0, {
                'product_id': product_svc.id,
                'product_qty': 1.0,
                'line_type': 'service',
            })],
        })
        req.action_confirm()
        req.action_approve()

        line = req.line_ids[0]

        po = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'company_id': self.company.id,
        })
        po_line = self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': product_svc.id,
            'name': product_svc.name,
            'product_qty': 1.0,
            'product_uom_id': product_svc.uom_id.id,
            'price_unit': 5000.0,
            'date_planned': '2026-11-01',
        })
        alloc = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'product_id': product_svc.id,
            'product_uom_id': product_svc.uom_id.id,
            'requested_qty': 1.0,
            'delivered_qty': 0.0,
            'supply_type': 'consumption',
            'stage': 'inbound',
            'purchase_line_id': po_line.id,
            'requisition_line_id': line.id,
            'state': 'in_progress',
        })
        line._compute_quantities()
        self.assertEqual(line.qty_done, 0.0)

        # Confirmar la orden de compra
        po.button_confirm()

        # Para servicios, la asignación debe pasar a done y qty_done de la requisición debe ser 1.0
        self.assertEqual(alloc.state, 'done')
        self.assertEqual(alloc.delivered_qty, 1.0)
        line._compute_quantities()
        self.assertEqual(line.qty_done, 1.0)
        self.assertEqual(line.qty_pending, 0.0)
        self.assertEqual(line.line_state, 'done')

    def test_allocation_cancel_wizard_close_vs_reintegrate(self):
        """9. El asistente de cancelación de asignaciones gestiona políticas close vs reintegrate con auditoría."""
        req = self._create_approved_requisition(qty=10.0)
        line = req.line_ids[0]

        alloc = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'requested_qty': 10.0,
            'delivered_qty': 0.0,
            'supply_type': 'consumption',
            'stage': 'inbound',
            'requisition_line_id': line.id,
            'state': 'in_progress',
        })
        line._compute_quantities()
        self.assertEqual(line.qty_pending, 10.0)

        # Cancelar con política 'close'
        wiz = self.env['requisition.allocation.cancel.wizard'].create({
            'allocation_ids': [(6, 0, alloc.ids)],
            'cancel_action': 'close',
            'reason': 'Cierre definitivo de cantidad no requerida',
        })
        wiz.action_confirm_cancel()

        self.assertEqual(alloc.state, 'cancel')
        self.assertEqual(alloc.cancelled_qty, 10.0)
        line._compute_quantities()
        self.assertEqual(line.qty_cancelled, 10.0)
        self.assertEqual(line.qty_pending, 0.0)
        self.assertEqual(line.line_state, 'cancel')

