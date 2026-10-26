# -*- coding: utf-8 -*-
from odoo.tests import common, tagged


@tagged('post_install', '-at_install', 'dh_requisitions')
class TestRequisitionDashboard(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

        # Grupos
        cls.group_requester = cls.env.ref('dh_requisitions.group_requisitions_requester')
        cls.group_approver = cls.env.ref('dh_requisitions.group_requisitions_approver')
        cls.group_manager = cls.env.ref('dh_requisitions.group_requisitions_manager')

        # Almacén
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({
                'name': 'Almacén Test Dashboard',
                'code': 'WH_DASH',
                'company_id': cls.company.id,
            })

        # Departamento
        cls.department = cls.env['hr.department'].create({
            'name': 'Depto Pruebas Dashboard',
            'company_id': cls.company.id,
        })

        # Usuario y Empleado Solicitante
        cls.user_requester = cls.env['res.users'].create({
            'name': 'Usuario Solicitante Dash',
            'login': 'solicitante_dash@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_requester.id])],
        })
        cls.employee_requester = cls.env['hr.employee'].create({
            'name': 'Empleado Solicitante Dash',
            'user_id': cls.user_requester.id,
            'department_id': cls.department.id,
            'company_id': cls.company.id,
        })

        # Usuario y Empleado Aprobador
        cls.user_approver = cls.env['res.users'].create({
            'name': 'Usuario Aprobador Dash',
            'login': 'aprobador_dash@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_approver.id])],
        })
        cls.employee_approver = cls.env['hr.employee'].create({
            'name': 'Empleado Aprobador Dash',
            'user_id': cls.user_approver.id,
            'department_id': cls.department.id,
            'company_id': cls.company.id,
        })

        # Usuario Gestor / Administrador
        cls.user_manager = cls.env['res.users'].create({
            'name': 'Usuario Gestor Dash',
            'login': 'gestor_dash@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_manager.id])],
        })

        # Categoría y Producto
        cls.category = cls.env['product.category'].create({
            'name': 'Categoría Dash Test',
            'requisition_type': 'material',
            'allow_requisition': True,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Insumo Dash Test',
            'type': 'consu',
            'categ_id': cls.category.id,
        })

    def test_01_dashboard_requester(self):
        """Valida que el solicitante vea sus tarjetas correspondientes y conteos correctos."""
        # Crear una requisición en borrador para el solicitante
        req_draft = self.env['requisitions'].with_user(self.user_requester).create({
            'employee_id': self.employee_requester.id,
            'user_id': self.user_approver.id,
            'warehouse_id': self.warehouse.id,
            'priority': 'normal',
        })
        self.env['requisitions.line'].create({
            'requisition_id': req_draft.id,
            'product_id': self.product.id,
            'note': 'Línea Test',
            'product_qty': 10.0,
            'line_type': 'material',
        })

        data = self.env['requisition.dashboard.kpi'].with_user(self.user_requester).get_dashboard_data()
        self.assertEqual(data.get('role_code'), 'requester')
        self.assertTrue(data.get('can_create'))

        card_keys = [c['key'] for c in data.get('cards', [])]
        self.assertIn('draft', card_keys)
        self.assertIn('to_approve', card_keys)
        self.assertIn('approved_in_progress', card_keys)
        self.assertIn('done', card_keys)
        self.assertIn('rejected', card_keys)

        draft_card = next(c for c in data['cards'] if c['key'] == 'draft')
        self.assertGreaterEqual(draft_card['value'], 1)

    def test_02_dashboard_approver(self):
        """Valida que el líder/aprobador vea sus tarjetas de autorización y seguimiento."""
        # Crear una requisición en to_approve
        req = self.env['requisitions'].with_user(self.user_requester).create({
            'employee_id': self.employee_requester.id,
            'user_id': self.user_approver.id,
            'warehouse_id': self.warehouse.id,
            'priority': 'urgent',
        })
        self.env['requisitions.line'].create({
            'requisition_id': req.id,
            'product_id': self.product.id,
            'note': 'Línea Aprobador Test',
            'product_qty': 5.0,
            'line_type': 'material',
        })
        req.action_confirm()
        self.assertEqual(req.state, 'to_approve')

        data = self.env['requisition.dashboard.kpi'].with_user(self.user_approver).get_dashboard_data()
        self.assertEqual(data.get('role_code'), 'approver')

        card_keys = [c['key'] for c in data.get('cards', [])]
        self.assertIn('to_approve', card_keys)
        self.assertIn('urgent_to_approve', card_keys)
        self.assertIn('my_approved_in_progress', card_keys)
        self.assertIn('my_rejected', card_keys)
        self.assertIn('total_managed', card_keys)

        to_approve_card = next(c for c in data['cards'] if c['key'] == 'to_approve')
        self.assertGreaterEqual(to_approve_card['value'], 1)

        urgent_card = next(c for c in data['cards'] if c['key'] == 'urgent_to_approve')
        self.assertGreaterEqual(urgent_card['value'], 1)

    def test_03_dashboard_manager(self):
        """Valida que el administrador/gestor vea sus tarjetas de surtido y abastecimiento."""
        # Crear requisición y aprobarla
        req = self.env['requisitions'].with_user(self.user_requester).create({
            'employee_id': self.employee_requester.id,
            'user_id': self.user_approver.id,
            'warehouse_id': self.warehouse.id,
            'priority': 'urgent',
        })
        self.env['requisitions.line'].create({
            'requisition_id': req.id,
            'product_id': self.product.id,
            'note': 'Línea Gestor Test',
            'product_qty': 8.0,
            'line_type': 'material',
        })
        req.action_confirm()
        req.with_user(self.user_approver).action_approve()
        self.assertEqual(req.state, 'approved')

        data = self.env['requisition.dashboard.kpi'].with_user(self.user_manager).get_dashboard_data()
        self.assertEqual(data.get('role_code'), 'manager')

        card_keys = [c['key'] for c in data.get('cards', [])]
        self.assertIn('approved_pending', card_keys)
        self.assertIn('urgent_active', card_keys)
        self.assertIn('to_approve', card_keys)
        self.assertIn('in_progress', card_keys)
        self.assertIn('done_month', card_keys)
        self.assertIn('total_active', card_keys)

        approved_card = next(c for c in data['cards'] if c['key'] == 'approved_pending')
        self.assertGreaterEqual(approved_card['value'], 1)
