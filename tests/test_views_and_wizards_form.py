# -*- coding: utf-8 -*-
from odoo.tests import common, tagged, Form
from odoo.exceptions import ValidationError, UserError


@tagged('post_install', '-at_install', 'dh_requisitions')
class TestViewsAndWizardsForm(common.TransactionCase):
    """
    Pruebas de interfaz y asistentes (wizards) con odoo.tests.Form.
    Verifica que las vistas XML de formulario y asistentes TransientModel
    con listas editables procesen correctamente los campos requeridos técnicos
    marcados como column_invisible="1", evitando fallos de validación en OWL/RPC.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({
                'name': 'Almacén Base Test',
                'code': 'WH_TEST',
                'company_id': cls.company.id,
            })
        cls.company.requisition_default_warehouse_origin_id = cls.warehouse.id

        cls.categ_material = cls.env['product.category'].create({
            'name': 'Materiales Test Form',
            'requisition_type': 'material',
            'allow_requisition': True,
        })

        cls.product = cls.env['product.product'].create({
            'name': 'Varilla Corrugada Test',
            'type': 'consu',
            'categ_id': cls.categ_material.id,
        })

        cls.employee = cls.env['hr.employee'].create({
            'name': 'Ing. Roberto Gómez',
            'company_id': cls.company.id,
        })

        cls.supplier = cls.env['res.partner'].create({
            'name': 'Proveedor Aceros del Norte SA',
            'supplier_rank': 1,
            'company_id': cls.company.id,
        })

        cls.env.user.write({
            'group_ids': [
                (4, cls.env.ref('dh_requisitions.group_requisitions_manager').id),
                (4, cls.env.ref('dh_requisitions.group_stock_request_planta').id),
                (4, cls.env.ref('purchase.group_purchase_user').id),
            ]
        })

    def _create_approved_requisition(self):
        """Crea y aprueba una requisición para pruebas de asistentes."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [
                (0, 0, {
                    'product_id': self.product.id,
                    'product_qty': 20.0,
                    'line_type': 'material',
                })
            ]
        })
        req.action_confirm()
        req.action_approve()
        return req

    def test_01_requisition_form_simulation(self):
        """Simula la captura de una requisición mediante la vista XML de formulario (Form)."""
        form = Form(self.env['requisitions'])
        form.employee_id = self.employee
        form.warehouse_id = self.warehouse
        form.user_id = self.env.user
        with form.material_line_ids.new() as line:
            line.product_id = self.product
            line.product_qty = 15.0
        req = form.save()

        self.assertEqual(req.state, 'draft')
        self.assertEqual(len(req.material_line_ids), 1)
        self.assertEqual(req.material_line_ids[0].product_qty, 15.0)

    def test_02_delivery_wizard_form(self):
        """Valida que el wizard de despacho opere correctamente con Form y column_invisible="1"."""
        req = self._create_approved_requisition()
        ctx = {
            'active_id': req.id,
            'active_ids': [req.id],
            'active_model': 'requisitions',
        }
        wizard_form = Form(self.env['requisition.delivery.wizard'].with_context(ctx))
        self.assertEqual(wizard_form.requisition_id, req)
        self.assertTrue(len(wizard_form.line_ids) > 0)

        # Modificar cantidad a entregar en la línea editable
        with wizard_form.line_ids.edit(0) as line:
            line.qty_to_deliver = 10.0

        wizard = wizard_form.save()
        self.assertEqual(wizard.line_ids[0].qty_to_deliver, 10.0)
        self.assertEqual(wizard.line_ids[0].requisition_line_id, req.line_ids[0])

    def test_03_purchase_request_wizard_form(self):
        """Valida que el wizard de solicitud de compra opere con Form y conserve campos requeridos."""
        req = self._create_approved_requisition()
        ctx = {
            'active_id': req.id,
            'active_ids': [req.id],
            'active_model': 'requisitions',
        }
        wizard_form = Form(self.env['requisition.purchase.request.wizard'].with_context(ctx))
        self.assertEqual(wizard_form.requisition_id, req)
        self.assertTrue(len(wizard_form.line_ids) > 0)

        with wizard_form.line_ids.edit(0) as line:
            line.product_qty = 8.0

        wizard = wizard_form.save()
        self.assertEqual(wizard.line_ids[0].product_qty, 8.0)
        self.assertEqual(wizard.line_ids[0].requisition_line_id, req.line_ids[0])

    def test_04_purchase_request_add_to_rfq_wizard_form(self):
        """Valida el wizard para anexar solicitudes de compra a RFQ usando Form."""
        req = self._create_approved_requisition()
        # Generar solicitud de compra
        pr_line = self.env['requisition.purchase.request.line'].create({
            'requisition_id': req.id,
            'requisition_line_id': req.line_ids[0].id,
            'product_id': self.product.id,
            'product_qty': 10.0,
            'product_uom_id': self.product.uom_id.id,
            'warehouse_id': self.warehouse.id,
            'state': 'pending',
        })

        ctx = {
            'active_id': pr_line.id,
            'active_ids': [pr_line.id],
            'active_model': 'requisition.purchase.request.line',
        }
        wizard_form = Form(self.env['requisition.purchase.request.add.to.rfq.wizard'].with_context(ctx))
        wizard_form.supplier_id = self.supplier
        self.assertTrue(len(wizard_form.line_ids) > 0)

        wizard = wizard_form.save()
        self.assertEqual(wizard.line_ids[0].request_line_id, pr_line)
        self.assertEqual(wizard.supplier_id, self.supplier)

    def test_05_supply_trigger_purchase_wizard_form(self):
        """Valida el asistente interactivo para disparar compra desde partida de suministro usando Form."""
        supply_line = self.env['requisition.supply.request.line'].create({
            'product_id': self.product.id,
            'product_qty': 25.0,
            'warehouse_dest_id': self.warehouse.id,
        })
        ctx = {
            'default_supply_line_id': supply_line.id,
            'default_qty_to_send': 5.0,
            'default_qty_to_purchase': 20.0,
        }
        form = Form(self.env['requisition.supply.trigger.purchase.wizard'].with_context(ctx))
        form.qty_to_purchase = 18.0
        form.notes = 'Compra vía Form'
        wizard = form.save()
        self.assertEqual(wizard.qty_to_purchase, 18.0)
        self.assertEqual(wizard.supply_line_id, supply_line)

    def test_06_purchase_request_cancel_wizard_form(self):
        """Valida el asistente interactivo de cancelación de partida de compra usando Form."""
        pr_line = self.env['requisition.purchase.request.line'].create({
            'product_id': self.product.id,
            'product_qty': 10.0,
            'product_uom_id': self.product.uom_id.id,
            'warehouse_id': self.warehouse.id,
            'state': 'pending',
        })
        ctx = {
            'default_purchase_request_line_ids': [pr_line.id],
        }
        form = Form(self.env['requisition.purchase.request.cancel.wizard'].with_context(ctx))
        form.reason = 'Cancelado vía Form test'
        wizard = form.save()
        self.assertEqual(wizard.reason, 'Cancelado vía Form test')
        self.assertIn(pr_line, wizard.purchase_request_line_ids)

