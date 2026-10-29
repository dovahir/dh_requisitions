# -*- coding: utf-8 -*-
from odoo import fields
from odoo.tests import common, tagged
from odoo.exceptions import UserError, ValidationError


@tagged('post_install', '-at_install', 'dh_requisitions')
class TestSupplyPurchaseFixes(common.TransactionCase):
    """
    Suite de pruebas integral para validar las correcciones y requerimientos:
    1. Requisiciones: qty_pending no disminuye por pedidos de compra o suministro; entregas de traspaso no cuentan para la requisición.
    2. Suministro: auto-configuración por almacén destino, qty_in_transit solo tras validación, reporte de traslado bloqueado sin entrega, qty_to_send vs product_qty, asistente interactivo de compra.
    3. Compras: origin_supply_request_line_id, qty_done inmune a traspasos de almacén, cancelación con justificación obligatoria y cascada a RFQ.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

        # Almacenes: Base y Obra
        cls.wh_base = cls.env['stock.warehouse'].create({
            'name': 'Almacén Base Central',
            'code': 'WH_B_CENT',
            'company_id': cls.company.id,
        })
        cls.wh_obra = cls.env['stock.warehouse'].create({
            'name': 'Almacén Obra Norte',
            'code': 'WH_OB_NOR',
            'company_id': cls.company.id,
        })
        cls.company.requisition_default_warehouse_origin_id = cls.wh_base.id

        # Configurar rutas entre Base y Obra usando el wizard
        setup_wizard = cls.env['requisition.warehouse.setup.wizard'].create({
            'warehouse_origin_id': cls.wh_base.id,
            'warehouse_dest_ids': [(6, 0, [cls.wh_obra.id])],
        })
        setup_wizard.action_configure_routes()

        cls.partner_supplier = cls.env['res.partner'].create({
            'name': 'Proveedor Materiales de Construcción SA',
            'supplier_rank': 1,
            'company_id': cls.company.id,
        })

        cls.categ_material = cls.env['product.category'].create({
            'name': 'Materiales de Obra',
            'requisition_type': 'material',
            'allow_requisition': True,
        })

        cls.product_cemento = cls.env['product.product'].create({
            'name': 'Cemento Especial Obra',
            'type': 'consu',
            'is_storable': True,
            'categ_id': cls.categ_material.id,
        })
        cls.env['stock.quant'].create({
            'product_id': cls.product_cemento.id,
            'location_id': cls.wh_base.lot_stock_id.id,
            'quantity': 500.0,
        })

        cls.employee = cls.env['hr.employee'].create({
            'name': 'Ing. Juan Residente',
            'company_id': cls.company.id,
        })

        # Permisos
        cls.env.user.write({
            'group_ids': [
                (4, cls.env.ref('dh_requisitions.group_requisitions_approver').id),
                (4, cls.env.ref('dh_requisitions.group_requisitions_manager').id),
                (4, cls.env.ref('dh_requisitions.group_stock_request_employee').id),
                (4, cls.env.ref('purchase.group_purchase_user').id),
                (4, cls.env.ref('purchase.group_purchase_manager').id),
                (4, cls.env.ref('stock.group_stock_user').id),
                (4, cls.env.ref('stock.group_stock_manager').id),
            ]
        })

    def test_01_qty_pending_not_reduced_by_purchase_or_supply(self):
        """Verifica que solicitar compra o suministro NO reduzca el campo qty_pending de la requisición."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.wh_obra.id,
            'user_id': self.env.user.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_cemento.id,
                'product_qty': 100.0,
                'line_type': 'material',
            })]
        })
        req.action_confirm()
        req.action_approve()

        line = req.line_ids[0]
        self.assertEqual(line.qty_pending, 100.0, "Al aprobar, lo pendiente debe ser 100.0")

        # 1. Solicitar compra por 40 unidades
        req._create_purchase_request_lines({line.id: 40.0})
        line.invalidate_recordset()
        self.assertEqual(line.qty_pending, 100.0, "qty_pending no debe reducirse al solicitar compra")
        self.assertEqual(line.line_state, 'partial_purchase', "line_state refleja avance logístico sin alterar qty_pending")

        # 2. Solicitar suministro por 60 unidades
        sr = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_base.id,
            'warehouse_dest_id': self.wh_obra.id,
        })
        wizard_stock = self.env['requisition.supply.request.wizard'].with_context(
            active_id=req.id
        ).create({
            'requisition_id': req.id,
            'supply_request_id': sr.id,
            'warehouse_origin_id': self.wh_base.id,
            'line_ids': [
                (0, 0, {
                    'requisition_line_id': line.id,
                    'product_id': self.product_cemento.id,
                    'product_qty': 60.0,
                    'selected': True,
                })
            ]
        })
        wizard_stock.action_confirm()

        line.invalidate_recordset()
        self.assertEqual(line.qty_pending, 100.0, "qty_pending debe seguir en 100.0 tras solicitar suministro")
        self.assertEqual(line.line_state, 'in_mixed', "line_state debe ser in_mixed (40 compras + 60 suministro)")

    def test_02_supply_transfer_does_not_count_towards_requisition_deliveries(self):
        """Verifica que las entregas de traspaso Base -> Obra NO cuenten como entregas de la requisición."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.wh_obra.id,
            'user_id': self.env.user.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_cemento.id,
                'product_qty': 50.0,
                'line_type': 'material',
            })]
        })
        req.action_confirm()
        req.action_approve()
        req_line = req.line_ids[0]

        sr = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_base.id,
            'warehouse_dest_id': self.wh_obra.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_cemento.id,
                'product_qty': 50.0,
                'qty_to_send': 50.0,
                'requisition_line_id': req_line.id,
            })]
        })
        sr.action_create_delivery()

        req.invalidate_recordset()
        self.assertEqual(req.picking_count, 0, "Albaranes de suministro no deben contarse en la requisición")
        self.assertEqual(len(req.picking_ids), 0, "picking_ids de requisición solo contiene salidas a solicitante")

    def test_03_warehouse_dest_auto_configures_routes(self):
        """Verifica que al seleccionar el almacén destino configurado, se auto-configuren tipos y ubicaciones."""
        sr = self.env['requisition.supply.request'].new({
            'warehouse_origin_id': self.wh_base.id,
            'warehouse_dest_id': self.wh_obra.id,
        })
        sr._onchange_warehouse_dest_id()

        self.assertTrue(sr.picking_type_id, "Debe auto-asignar el tipo de operación de origen")
        self.assertTrue(sr.picking_type_dest_id, "Debe auto-asignar el tipo de operación de recepción destino")
        self.assertEqual(sr.picking_type_id.warehouse_id, self.wh_base)
        self.assertEqual(sr.picking_type_dest_id.warehouse_id, self.wh_obra)
        self.assertEqual(sr.location_id, self.wh_base.lot_stock_id)

    def test_04_qty_in_transit_only_when_dispatch_done(self):
        """Verifica que qty_in_transit sea 0 en borrador y solo se compute al validar la entrega desde Base."""
        sr = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_base.id,
            'warehouse_dest_id': self.wh_obra.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_cemento.id,
                'product_qty': 30.0,
                'qty_to_send': 30.0,
            })]
        })
        line = sr.line_ids[0]
        self.assertEqual(line.qty_in_transit, 0.0, "En borrador, qty_in_transit debe ser 0.0")

        sr.action_create_delivery()
        line.invalidate_recordset()
        self.assertEqual(line.qty_in_transit, 0.0, "Con entrega creada sin validar, qty_in_transit debe seguir en 0.0")

        # Validar entrega desde Base
        out_picking = sr.picking_ids.filtered(lambda p: p.location_id == sr.location_id)
        for move in out_picking.move_ids:
            move.quantity = 30.0
        out_picking.with_context(skip_backorder=True).button_validate()

        line.invalidate_recordset()
        self.assertEqual(out_picking.state, 'done')
        self.assertEqual(line.qty_in_transit, 30.0, "Al validarse la salida de base, qty_in_transit debe ser 30.0")
        self.assertEqual(line.qty_done, 0.0, "qty_done aún no debe registrarse hasta la recepción en obra")

    def test_05_transfer_report_blocked_without_validated_delivery(self):
        """Verifica que el reporte de traslado esté bloqueado antes de validar una entrega."""
        sr = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_base.id,
            'warehouse_dest_id': self.wh_obra.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_cemento.id,
                'product_qty': 10.0,
            })]
        })
        self.assertFalse(sr.has_validated_delivery)
        with self.assertRaises(UserError):
            sr.action_print_transfer_report()

    def test_06_add_pending_button_visibility_flag(self):
        """Verifica que has_pending_lines_for_destination sea True solo cuando existan pendientes para esa obra."""
        sr = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_base.id,
            'warehouse_dest_id': self.wh_obra.id,
        })
        self.assertFalse(sr.has_pending_lines_for_destination)

        # Crear línea pendiente para la obra
        self.env['requisition.supply.request.line'].create({
            'product_id': self.product_cemento.id,
            'product_qty': 15.0,
            'warehouse_dest_id': self.wh_obra.id,
            'warehouse_origin_id': self.wh_base.id,
        })
        sr._compute_has_pending_lines_for_destination()
        self.assertTrue(sr.has_pending_lines_for_destination, "Debe ser True al existir líneas pendientes para la obra")

    def test_07_qty_to_send_differentiated_from_product_qty(self):
        """Verifica que la entrega despache qty_to_send y no necesariamente la demanda solicitada."""
        sr = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_base.id,
            'warehouse_dest_id': self.wh_obra.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_cemento.id,
                'product_qty': 100.0,
                'qty_to_send': 65.0,
            })]
        })
        sr.action_create_delivery()
        picking = sr.picking_ids[0]
        self.assertEqual(picking.move_ids[0].product_uom_qty, 65.0, "La cantidad demandada en el albarán debe ser qty_to_send (65.0)")

    def test_08_trigger_purchase_wizard_from_supply_line(self):
        """Verifica el wizard interactivo para pedir compra desde suministro y crear partida pendiente opcional."""
        sr = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_base.id,
            'warehouse_dest_id': self.wh_obra.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_cemento.id,
                'product_qty': 80.0,
                'qty_to_send': 20.0,
            })]
        })
        line = sr.line_ids[0]

        action = line.action_create_purchase_request_line()
        wizard = self.env[action['res_model']].with_context(action['context']).create({
            'qty_to_purchase': 60.0,
            'qty_to_send': 20.0,
            'create_pending_supply': True,
            'notes': 'Compra urgente para colado',
        })
        res = wizard.action_confirm()

        pr_line = self.env['requisition.purchase.request.line'].browse(res['res_id'])
        self.assertEqual(pr_line.product_qty, 60.0)
        self.assertEqual(pr_line.origin_supply_request_line_id, line, "Debe guardar la partida de suministro que disparó la compra")

        # Verificar que se creó la partida en pendientes de envío
        pending_lines = self.env['requisition.supply.request.line'].search([
            ('product_id', '=', self.product_cemento.id),
            ('request_id', '=', False),
            ('warehouse_dest_id', '=', self.wh_obra.id),
        ])
        self.assertTrue(pending_lines)
        self.assertIn(60.0, pending_lines.mapped('product_qty'))

    def test_09_purchase_qty_done_ignores_warehouse_transfers(self):
        """Verifica que qty_done en compras sea inmune a traslados de suministro y solo responda a recepciones de compra."""
        pr_line = self.env['requisition.purchase.request.line'].create({
            'product_id': self.product_cemento.id,
            'product_qty': 50.0,
            'warehouse_id': self.wh_obra.id,
            'state': 'pending',
        })

        # Simular asignación a la que se le asiente entrega por un traslado de almacén
        alloc = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'product_id': self.product_cemento.id,
            'product_uom_id': self.product_cemento.uom_id.id,
            'requested_qty': 50.0,
            'delivered_qty': 50.0,  # Asentado erróneamente por recepción de traslado
            'supply_type': 'consumption',
            'stage': 'inbound',
            'purchase_request_line_id': pr_line.id,
            'state': 'done',
        })
        pr_line.invalidate_recordset()
        # REQUERIMIENTO CLAVE: qty_done DEBE SER 0.0 PORQUE NO HUBO RECEPCIÓN DE ORDEN DE COMPRA
        self.assertEqual(pr_line.qty_done, 0.0, "qty_done en compra no debe afectarse por entregas de traslados")

        # Ahora crear y recibir una orden de compra real
        po = self.env['purchase.order'].create({
            'partner_id': self.partner_supplier.id,
            'company_id': self.company.id,
            'order_line': [(0, 0, {
                'name': self.product_cemento.name,
                'product_id': self.product_cemento.id,
                'product_qty': 50.0,
                'product_uom_id': self.product_cemento.uom_id.id,
                'price_unit': 150.0,
                'date_planned': fields.Datetime.now(),
            })]
        })
        po_line = po.order_line[0]
        alloc.write({'purchase_line_id': po_line.id})
        po.button_confirm()

        # Simular recepción de 30 unidades en la orden de compra
        po_line.qty_received = 30.0
        pr_line.invalidate_recordset()
        self.assertEqual(pr_line.qty_done, 30.0, "qty_done en compra debe reflejar exactamente la recepción de la OC (30.0)")

    def test_10_purchase_cancellation_requires_reason_and_cascades_to_rfq(self):
        """Verifica que cancelar partida de compra requiera justificación obligatoria y cancele RFQ vinculada."""
        pr_line = self.env['requisition.purchase.request.line'].create({
            'product_id': self.product_cemento.id,
            'product_qty': 25.0,
            'warehouse_id': self.wh_obra.id,
            'state': 'pending',
        })

        # Asociar a una RFQ
        po = self.env['purchase.order'].create({
            'partner_id': self.partner_supplier.id,
            'company_id': self.company.id,
            'order_line': [(0, 0, {
                'name': self.product_cemento.name,
                'product_id': self.product_cemento.id,
                'product_qty': 25.0,
                'product_uom_id': self.product_cemento.uom_id.id,
                'price_unit': 100.0,
                'date_planned': fields.Datetime.now(),
            })]
        })
        alloc = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'product_id': self.product_cemento.id,
            'product_uom_id': self.product_cemento.uom_id.id,
            'requested_qty': 25.0,
            'purchase_request_line_id': pr_line.id,
            'purchase_line_id': po.order_line[0].id,
            'stage': 'inbound',
            'state': 'in_progress',
        })
        pr_line.invalidate_recordset()

        # Cancelar sin motivo a través del wizard debe fallar
        with self.assertRaises(UserError):
            wizard_fail = self.env['requisition.purchase.request.cancel.wizard'].create({
                'purchase_request_line_ids': [(6, 0, [pr_line.id])],
                'reason': '   ',
            })
            wizard_fail.action_confirm()

        # Cancelar con motivo exitoso
        wizard = self.env['requisition.purchase.request.cancel.wizard'].create({
            'purchase_request_line_ids': [(6, 0, [pr_line.id])],
            'reason': 'Cancelado por cambio en especificación técnica',
            'cancel_action': 'close',
        })
        wizard.action_confirm()

        pr_line.invalidate_recordset()
        po.invalidate_recordset()
        self.assertEqual(pr_line.state, 'cancel')
        self.assertEqual(pr_line.cancel_reason, 'Cancelado por cambio en especificación técnica')
        self.assertEqual(po.state, 'cancel', "La RFQ vinculada debe cancelarse en cascada")
