# -*- coding: utf-8 -*-
from odoo.tests import common, tagged
from odoo.exceptions import UserError, ValidationError


@tagged('post_install', '-at_install', 'dh_requisitions')
class TestPurchaseRequest(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({
                'name': 'Almacén Central Test',
                'code': 'WH_PR_TEST',
                'company_id': cls.company.id,
            })

        cls.partner_supplier = cls.env['res.partner'].create({
            'name': 'Proveedor Ferretero SA de CV',
            'supplier_rank': 1,
            'company_id': cls.company.id,
        })

        cls.categ_material = cls.env['product.category'].create({
            'name': 'Ferretería y Materiales Test',
            'requisition_type': 'material',
            'allow_requisition': True,
        })

        cls.product_cemento = cls.env['product.product'].create({
            'name': 'Cemento CPC 30R Test',
            'type': 'consu',
            'categ_id': cls.categ_material.id,
        })

        cls.product_varilla = cls.env['product.product'].create({
            'name': 'Varilla Corrugada 3/8 Test',
            'type': 'consu',
            'categ_id': cls.categ_material.id,
        })

        cls.employee = cls.env['hr.employee'].create({
            'name': 'Ing. Roberto Gómez Test',
            'company_id': cls.company.id,
        })

        cls.env.user.write({
            'group_ids': [
                (4, cls.env.ref('dh_requisitions.group_requisitions_approver').id),
                (4, cls.env.ref('dh_requisitions.group_requisitions_manager').id),
                (4, cls.env.ref('purchase.group_purchase_user').id),
                (4, cls.env.ref('purchase.group_purchase_manager').id),
            ]
        })

    def test_01_create_purchase_request_from_requisition(self):
        """Valida la generación de Solicitudes de Compra y asignaciones transversales desde requisición."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [
                (0, 0, {
                    'product_id': self.product_cemento.id,
                    'product_qty': 15.0,
                    'line_type': 'material',
                    'note': 'Colado de losa zapata 1',
                }),
                (0, 0, {
                    'product_id': self.product_varilla.id,
                    'product_qty': 30.0,
                    'line_type': 'material',
                    'note': 'Armado de columnas',
                }),
            ]
        })
        req.action_confirm()
        req.action_approve()

        # Generar solicitudes de compra mediante el asistente interactivo
        action = req.action_create_purchase_request()
        self.assertEqual(action.get('res_model'), 'requisition.purchase.request.wizard')

        wizard = self.env['requisition.purchase.request.wizard'].with_context(
            active_id=req.id, default_requisition_id=req.id
        ).create({})
        self.assertEqual(len(wizard.line_ids), 2, "El wizard debe precargar 2 partidas con cantidad por comprar")
        wizard.action_confirm()

        pr_lines = self.env['requisition.purchase.request.line'].search([
            ('requisition_id', '=', req.id)
        ])
        self.assertEqual(len(pr_lines), 2, "Deben haberse creado 2 partidas de solicitud de compra")

        for line in pr_lines:
            self.assertEqual(line.state, 'pending', "El estado inicial debe ser 'pending'")
            self.assertEqual(line.pending_qty_to_buy, line.product_qty, "La cantidad pendiente inicial debe igualar la cantidad solicitada")
            self.assertTrue(line.allocation_ids, "Debe existir al menos una asignación transversal en supply.chain.allocation")
            self.assertFalse(line.allocation_ids[0].purchase_line_id, "La asignación inicial no debe tener aún línea de compra")
            self.assertTrue(line.requisition_line_notes, "Debe heredar las notas específicas de la partida")

    def test_02_add_to_rfq_wizard_maintains_line_separation(self):
        """Valida que el wizard cree partidas de compra separadas por solicitud/requisición para conservar trazabilidad."""
        # Creamos 2 requisiciones distintas con el mismo producto para validar que NO se fusionen
        req1 = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_cemento.id,
                'product_qty': 10.0,
                'line_type': 'material',
                'note': 'Lote para Frente 1',
            })]
        })
        req1.action_confirm()
        req1.action_approve()
        req1._create_purchase_request_lines()

        req2 = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_cemento.id,
                'product_qty': 20.0,
                'line_type': 'material',
                'note': 'Lote para Frente 2',
            })]
        })
        req2.action_confirm()
        req2.action_approve()
        req2._create_purchase_request_lines()

        pr_line1 = self.env['requisition.purchase.request.line'].search([('requisition_id', '=', req1.id)], limit=1)
        pr_line2 = self.env['requisition.purchase.request.line'].search([('requisition_id', '=', req2.id)], limit=1)

        # Ejecutar asistente con ambas solicitudes
        wizard = self.env['requisition.purchase.request.add.to.rfq.wizard'].with_context(
            active_model='requisition.purchase.request.line',
            active_ids=[pr_line1.id, pr_line2.id]
        ).create({
            'supplier_id': self.partner_supplier.id,
            'company_id': self.company.id,
        })

        action_res = wizard.add_to_rfq()
        po_id = action_res.get('res_id')
        self.assertTrue(po_id, "El wizard debe retornar una orden de compra creada")

        po = self.env['purchase.order'].browse(po_id)
        self.assertEqual(po.partner_id, self.partner_supplier)
        # REQUERIMIENTO CLAVE: Las líneas deben permanecer separadas por requisición/solicitud
        self.assertEqual(len(po.order_line), 2, "Deben existir 2 líneas de orden de compra independientes (separación por requisición)")

        # Verificar notas y referencias individuales
        descriptions = po.order_line.mapped('name')
        self.assertTrue(any('Lote para Frente 1' in desc for desc in descriptions), "Debe conservar las notas de la solicitud 1")
        self.assertTrue(any('Lote para Frente 2' in desc for desc in descriptions), "Debe conservar las notas de la solicitud 2")

        # Verificar vinculación en supply.chain.allocation
        alloc1 = pr_line1.allocation_ids.filtered(lambda a: a.purchase_line_id)
        alloc2 = pr_line2.allocation_ids.filtered(lambda a: a.purchase_line_id)
        self.assertTrue(alloc1, "La solicitud 1 debe tener su asignación vinculada a la PO line")
        self.assertTrue(alloc2, "La solicitud 2 debe tener su asignación vinculada a la PO line")
        self.assertNotEqual(alloc1.purchase_line_id.id, alloc2.purchase_line_id.id, "Cada asignación debe apuntar a una PO line distinta")

        # Verificar transición de estados a 'in_progress'
        self.assertEqual(pr_line1.state, 'in_progress')
        self.assertEqual(pr_line2.state, 'in_progress')
        self.assertEqual(pr_line1.qty_in_progress, 10.0)
        self.assertEqual(pr_line2.qty_in_progress, 20.0)
        self.assertEqual(pr_line1.pending_qty_to_buy, 0.0)

    def test_03_po_confirmation_updates_state(self):
        """Valida que confirmar la orden de compra actualice el estado a 'to_receive'."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_cemento.id,
                'product_qty': 8.0,
                'line_type': 'material',
            })]
        })
        req.action_confirm()
        req.action_approve()
        req._create_purchase_request_lines()

        pr_line = self.env['requisition.purchase.request.line'].search([('requisition_id', '=', req.id)], limit=1)

        wizard = self.env['requisition.purchase.request.add.to.rfq.wizard'].with_context(
            active_model='requisition.purchase.request.line',
            active_ids=[pr_line.id]
        ).create({
            'supplier_id': self.partner_supplier.id,
            'company_id': self.company.id,
        })
        action = wizard.add_to_rfq()
        po = self.env['purchase.order'].browse(action['res_id'])

        # Confirmar orden de compra
        po.button_confirm()

        pr_line.invalidate_recordset()
        self.assertEqual(pr_line.purchase_state, 'purchase')
        self.assertEqual(pr_line.qty_to_receive, 8.0, "La cantidad debe computarse en qty_to_receive")
        self.assertEqual(pr_line.state, 'to_receive', "El estado debe evolucionar a 'to_receive'")

    def test_04_cancel_line_action(self):
        """Valida la cancelación interactiva de solicitudes de compra y actualización de cantidades."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_varilla.id,
                'product_qty': 12.0,
                'line_type': 'material',
            })]
        })
        req.action_confirm()
        req.action_approve()
        req._create_purchase_request_lines()

        pr_line = self.env['requisition.purchase.request.line'].search([('requisition_id', '=', req.id)], limit=1)
        self.assertEqual(pr_line.state, 'pending')

        # Cancelar directamente
        pr_line.action_cancel_line(cancel_action='reintegrate')
        self.assertEqual(pr_line.state, 'cancel')
        self.assertEqual(pr_line.qty_cancelled, 12.0)
        self.assertEqual(pr_line.pending_qty_to_buy, 0.0)
