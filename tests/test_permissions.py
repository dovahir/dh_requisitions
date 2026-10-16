# -*- coding: utf-8 -*-
from odoo import fields
from odoo.tests import common, tagged
from odoo.exceptions import UserError, AccessError


@tagged('post_install', '-at_install', 'dh_requisitions')
class TestRequisitionsPermissions(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

        # Grupos
        cls.group_user = cls.env.ref('dh_requisitions.group_requisitions_user')
        cls.group_approver = cls.env.ref('dh_requisitions.group_requisitions_approver')
        cls.group_manager = cls.env.ref('dh_requisitions.group_requisitions_manager')
        cls.group_stock = cls.env.ref('stock.group_stock_user')
        cls.group_purchase = cls.env.ref('purchase.group_purchase_user')

        # Almacén
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({
                'name': 'Almacén Test Permisos',
                'code': 'WH_PERM',
                'company_id': cls.company.id,
            })

        # Categoría y Producto
        cls.categ_material = cls.env['product.category'].create({
            'name': 'Materiales Permisos Test',
            'requisition_type': 'material',
            'allow_requisition': True,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Arena Fina Test',
            'type': 'consu',
            'categ_id': cls.categ_material.id,
        })

        # Empleados y Usuarios
        cls.user_solicitante = cls.env['res.users'].create({
            'name': 'Usuario Solicitante Test',
            'login': 'solicitante_test@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_user.id])],
        })
        cls.employee_solicitante = cls.env['hr.employee'].create({
            'name': 'Empleado Solicitante Test',
            'user_id': cls.user_solicitante.id,
            'company_id': cls.company.id,
        })

        # Usuario sin ficha de empleado
        cls.user_sin_empleado = cls.env['res.users'].create({
            'name': 'Usuario Sin Empleado Test',
            'login': 'sin_empleado_test@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_user.id])],
        })

        # Usuario Aprobador
        cls.user_aprobador = cls.env['res.users'].create({
            'name': 'Usuario Aprobador Test',
            'login': 'aprobador_test@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_approver.id])],
        })
        cls.employee_aprobador = cls.env['hr.employee'].create({
            'name': 'Empleado Aprobador Test',
            'user_id': cls.user_aprobador.id,
            'company_id': cls.company.id,
        })

    def test_01_user_visibility_post_draft(self):
        """Valida que el usuario solicitante pueda consultar su requisición en estado aprobado."""
        req = self.env['requisitions'].with_user(self.user_solicitante).create({
            'employee_id': self.employee_solicitante.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.user_aprobador.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 10.0,
                'line_type': 'material',
            })],
        })
        self.assertEqual(req.state, 'draft')

        # Enviar a aprobación
        req.action_confirm()
        self.assertEqual(req.state, 'to_approve')

        # Aprobar con el usuario autorizador
        req.with_user(self.user_aprobador).action_approve()
        self.assertEqual(req.state, 'approved')

        # El usuario solicitante DEBE poder leer su requisición y líneas aun cuando esté aprobada
        req_as_user = self.env['requisitions'].with_user(self.user_solicitante).search([('id', '=', req.id)])
        self.assertTrue(req_as_user, "El usuario debe poder consultar su requisición aprobada.")
        self.assertEqual(req_as_user.line_ids[0].product_qty, 10.0)

    def test_02_user_cannot_edit_approved_requisition(self):
        """Valida que un usuario estándar no pueda modificar una requisición fuera de borrador."""
        req = self.env['requisitions'].with_user(self.user_solicitante).create({
            'employee_id': self.employee_solicitante.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.user_aprobador.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 5.0,
                'line_type': 'material',
            })],
        })
        req.action_confirm()
        req.with_user(self.user_aprobador).action_approve()

        # Intentar modificar una requisición aprobada debe fallar con UserError
        with self.assertRaises(UserError):
            req.with_user(self.user_solicitante).write({'priority': '3'})

    def test_03_delivery_wizard_requires_stock_or_manager(self):
        """Valida que action_open_delivery_wizard requiera stock.group_stock_user o admin de requisiciones."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee_solicitante.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.user_aprobador.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 8.0,
                'line_type': 'material',
            })],
        })
        req.action_confirm()
        req.with_user(self.user_aprobador).action_approve()

        # Usuario solicitante (sin stock) no puede abrir asistente de entregas
        with self.assertRaises(UserError):
            req.with_user(self.user_solicitante).action_open_delivery_wizard()

        # Usuario con stock sí puede
        user_stock = self.env['res.users'].create({
            'name': 'Almacenista Test',
            'login': 'almacen_test@example.com',
            'company_id': self.company.id,
            'company_ids': [(6, 0, [self.company.id])],
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.group_stock.id, self.group_user.id])],
        })
        res = req.with_user(user_stock).action_open_delivery_wizard()
        self.assertEqual(res.get('res_model'), 'requisition.delivery.wizard')

    def test_04_supply_request_actions_restricted(self):
        """Valida que las acciones operativas de solicitud de suministro requieran permisos de inventario."""
        supply_req = self.env['requisition.supply.request'].create({
            'company_id': self.company.id,
            'warehouse_origin_id': self.warehouse.id,
            'warehouse_dest_id': self.warehouse.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 5.0,
            })],
        })
        # Usuario solicitante no puede crear entrega
        with self.assertRaises(UserError):
            supply_req.with_user(self.user_solicitante).action_create_delivery()

        with self.assertRaises(UserError):
            supply_req.with_user(self.user_solicitante).action_open_block_wizard()

    def test_05_purchase_request_actions_restricted(self):
        """Valida que añadir a RFQ requiera permisos de compras o admin de requisiciones."""
        pr_line = self.env['requisition.purchase.request.line'].create({
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'product_qty': 12.0,
            'company_id': self.company.id,
            'warehouse_id': self.warehouse.id,
        })
        with self.assertRaises(UserError):
            pr_line.with_user(self.user_solicitante).action_open_add_to_rfq_wizard()

        with self.assertRaises(UserError):
            pr_line.with_user(self.user_solicitante).action_cancel_line()
