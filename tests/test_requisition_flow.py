# -*- coding: utf-8 -*-
from odoo.tests import common, tagged
from odoo.exceptions import UserError, ValidationError


@tagged('post_install', '-at_install', 'dh_requisitions')
class TestRequisitionFlow(common.TransactionCase):
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

        cls.categ_material = cls.env['product.category'].create({
            'name': 'Materiales Test',
            'requisition_type': 'material',
            'allow_requisition': True,
        })

        cls.product = cls.env['product.product'].create({
            'name': 'Cemento Gris 50kg Test',
            'type': 'consu',
            'categ_id': cls.categ_material.id,
        })

        cls.employee = cls.env['hr.employee'].create({
            'name': 'Juan Pérez Test',
            'company_id': cls.company.id,
        })

        cls.env.user.write({
            'group_ids': [(4, cls.env.ref('dh_requisitions.group_requisitions_approver').id)]
        })

    def test_01_requisition_lifecycle_happy_path(self):
        """Valida el ciclo de vida: Creación -> Confirmación -> Aprobación."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [
                (0, 0, {
                    'product_id': self.product.id,
                    'product_qty': 10.0,
                    'line_type': 'material',
                })
            ]
        })
        self.assertEqual(req.state, 'draft')
        self.assertEqual(len(req.line_ids), 1)

        # Solicitar aprobación
        req.action_confirm()
        self.assertEqual(req.state, 'to_approve')

        # Aprobar
        req.action_approve()
        self.assertEqual(req.state, 'approved')

    def test_02_requisition_protected_deletion(self):
        """Valida que @api.ondelete impida borrar requisiciones en estados confirmados o aprobados."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [
                (0, 0, {
                    'product_id': self.product.id,
                    'product_qty': 5.0,
                    'line_type': 'material',
                })
            ]
        })
        req.action_confirm()
        req.action_approve()

        # Debe lanzar UserError al intentar eliminar
        with self.assertRaises(UserError):
            req.unlink()

    def test_03_requisition_zero_qty_validation(self):
        """Valida que no se permitan partidas con cantidad menor o igual a cero."""
        with self.assertRaises(ValidationError):
            self.env['requisitions'].create({
                'employee_id': self.employee.id,
                'warehouse_id': self.warehouse.id,
                'user_id': self.env.user.id,
                'line_ids': [
                    (0, 0, {
                        'product_id': self.product.id,
                        'product_qty': 0.0,
                        'line_type': 'material',
                    })
                ]
            })
