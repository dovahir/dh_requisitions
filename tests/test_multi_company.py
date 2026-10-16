from odoo import fields
from odoo.tests import common, tagged
from odoo.exceptions import AccessError


@tagged('post_install', '-at_install', 'dh_requisitions')
class TestRequisitionMultiCompany(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env['res.company'].create({'name': 'Segunda Compañía Test SCM'})

        cls.user_b = cls.env['res.users'].create({
            'name': 'Usuario Empresa B',
            'login': 'user_b_test@example.com',
            'company_id': cls.company_b.id,
            'company_ids': [(6, 0, [cls.company_b.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref('dh_requisitions.group_requisitions_user').id])],
        })

        cls.employee_a = cls.env['hr.employee'].create({
            'name': 'Empleado Empresa A',
            'company_id': cls.company_a.id,
        })
        cls.wh_a = cls.env['stock.warehouse'].search([('company_id', '=', cls.company_a.id)], limit=1)
        if not cls.wh_a:
            cls.wh_a = cls.env['stock.warehouse'].create({
                'name': 'Almacén Base Empresa A',
                'code': 'WH_A_TEST',
                'company_id': cls.company_a.id,
            })

    def test_01_multi_company_isolation(self):
        """Valida que un usuario de la compañía B no pueda acceder a las requisiciones de la compañía A."""
        req_a = self.env['requisitions'].create({
            'employee_id': self.employee_a.id,
            'warehouse_id': self.wh_a.id if self.wh_a else False,
            'company_id': self.company_a.id,
            'user_id': self.env.user.id,
            'date_required': fields.Date.today(),
        })

        # Consultar desde el contexto de usuario B
        reqs_user_b = self.env['requisitions'].with_user(self.user_b).search([('id', '=', req_a.id)])
        self.assertNotIn(req_a, reqs_user_b)
