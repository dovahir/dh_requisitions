# -*- coding: utf-8 -*-
from odoo import fields
from odoo.tests.common import TransactionCase
from odoo.tests import tagged
from odoo.exceptions import UserError, AccessError


@tagged('post_install', '-at_install', 'dh_requisitions')
class TestCascadeCancellationAndWarehouseConfig(TransactionCase):
    """
    Pruebas unitarias para:
    1. Erradicación de hardcodeo: el almacén base debe provenir exclusivamente de la configuración del módulo.
    2. Cancelación unificada de requisición: justificación siempre obligatoria mediante wizard,
       disponible para Líder/Aprobador y Administrador, con cascada total a pickings, solicitudes
       de suministro y sus partidas, solicitudes de compra, RFQ/PO y allocations.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.requisition_allow_auto_approval = False

        # Almacenes de prueba con nombres/códigos arbitrarios (sin la palabra BASE)
        cls.wh_central = cls.env['stock.warehouse'].create({
            'name': 'Centro de Distribución Norte',
            'code': 'CDN_01',
            'company_id': cls.company.id,
        })
        cls.wh_obra = cls.env['stock.warehouse'].create({
            'name': 'Bodega Obra Torre Reforma',
            'code': 'TORRE_REF',
            'company_id': cls.company.id,
        })

        # Configurar almacén base en la compañía
        cls.company.requisition_default_warehouse_origin_id = cls.wh_central.id

        # Usuarios con roles
        cls.group_requester = cls.env.ref('dh_requisitions.group_requisitions_requester')
        cls.group_approver = cls.env.ref('dh_requisitions.group_requisitions_approver')
        cls.group_manager = cls.env.ref('dh_requisitions.group_requisitions_manager')
        cls.group_purchase = cls.env.ref('purchase.group_purchase_user')
        cls.group_stock = cls.env.ref('stock.group_stock_user')

        cls.user_solicitante = cls.env['res.users'].create({
            'name': 'Juan Solicitante Test',
            'login': 'jsolicitante_test',
            'email': 'jsolicitante@test.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_requester.id])],
        })
        cls.user_lider = cls.env['res.users'].create({
            'name': 'Carlos Líder Aprobador Test',
            'login': 'clider_test',
            'email': 'clider@test.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_approver.id])],
        })
        cls.user_admin = cls.env['res.users'].create({
            'name': 'Ana Administradora Test',
            'login': 'aadmin_test',
            'email': 'aadmin@test.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_manager.id, cls.group_purchase.id, cls.group_stock.id])],
        })

        cls.employee = cls.env['hr.employee'].create({
            'name': 'Empleado Operativo Test',
            'user_id': cls.user_solicitante.id,
            'company_id': cls.company.id,
        })

        cls.categ = cls.env['product.category'].create({
            'name': 'Materiales Construcción Cascade',
            'requisition_type': 'material',
            'allow_requisition': True,
        })

        cls.product_a = cls.env['product.product'].create({
            'name': 'Varilla Corrugada 1/2 Cascade',
            'type': 'consu',
            'categ_id': cls.categ.id,
        })
        cls.product_b = cls.env['product.product'].create({
            'name': 'Cemento Estructural 50kg Cascade',
            'type': 'consu',
            'categ_id': cls.categ.id,
        })

    def test_01_warehouse_base_configuration_required(self):
        """Valida que no existan búsquedas al tanteo ni hardcodeo de BASE y que se exija configuración."""
        # Desconfigurar el almacén base de la compañía
        self.company.requisition_default_warehouse_origin_id = False

        req = self.env['requisitions'].with_user(self.user_solicitante).create({
            'employee_id': self.employee.id,
            'warehouse_id': self.wh_obra.id,
            'user_id': self.user_lider.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_a.id,
                'product_qty': 10.0,
                'line_type': 'material',
            })],
        })
        req.action_confirm()
        req.with_user(self.user_lider).action_approve()

        # Intentar abrir wizard de suministro sin almacén base configurado -> UserError
        with self.assertRaises(UserError):
            self.env['requisition.supply.request.wizard'].with_user(self.user_admin).with_context(active_id=req.id).default_get(['warehouse_origin_id', 'requisition_id'])

        # Restaurar la configuración canónica
        self.company.requisition_default_warehouse_origin_id = self.wh_central.id
        self.assertEqual(req.company_id.requisition_default_warehouse_origin_id, self.wh_central)

    def test_02_unified_cancellation_cascade_from_draft(self):
        """Valida que en borrador la cancelación requiera justificación obligatoria y esté disponible para Líder y Admin."""
        req = self.env['requisitions'].with_user(self.user_solicitante).create({
            'employee_id': self.employee.id,
            'warehouse_id': self.wh_central.id,
            'user_id': self.user_lider.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_a.id,
                'product_qty': 5.0,
                'line_type': 'material',
            })],
        })
        self.assertEqual(req.state, 'draft')

        # 1. El Solicitante NO puede abrir el asistente de cancelación
        with self.assertRaises(UserError):
            req.with_user(self.user_solicitante).action_open_cancel_wizard()

        # 2. El Líder / Aprobador SÍ puede abrir el asistente en borrador
        action = req.with_user(self.user_lider).action_open_cancel_wizard()
        self.assertEqual(action.get('res_model'), 'requisition.cancel.wizard')

        # 3. Intentar confirmar sin justificación -> UserError
        wizard = self.env['requisition.cancel.wizard'].with_user(self.user_lider).create({
            'requisition_id': req.id,
            'reason': '   ',
        })
        with self.assertRaises(UserError):
            wizard.action_confirm_cancel()

        # 4. Confirmar con justificación válida
        wizard.reason = 'Requisición cancelada por cancelación de proyecto'
        wizard.action_confirm_cancel()

        self.assertEqual(req.state, 'cancel')
        self.assertEqual(req.line_ids.line_state, 'cancel')

    def test_03_full_cascade_cancellation_with_all_linked_documents(self):
        """Valida que al cancelar una requisición en proceso se cancelen en cascada pickings, SRs, PRs, RFQs y allocations."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.wh_obra.id,
            'user_id': self.user_lider.id,
            'line_ids': [
                (0, 0, {
                    'product_id': self.product_a.id,
                    'product_qty': 20.0,
                    'line_type': 'material',
                }),
                (0, 0, {
                    'product_id': self.product_b.id,
                    'product_qty': 15.0,
                    'line_type': 'material',
                }),
            ],
        })
        req.action_confirm()
        req.with_user(self.user_lider).action_approve()
        self.assertEqual(req.state, 'approved')

        # A. Crear Solicitud de Compra para product_a
        req._create_purchase_request_lines(lines_data=[{
            'line': req.line_ids[0],
            'qty': 20.0,
            'priority': 'normal',
        }])
        pr_line = req.purchase_request_line_ids.filtered(lambda l: l.product_id == self.product_a)
        self.assertTrue(pr_line)
        self.assertEqual(pr_line.state, 'pending')

        # B. Crear RFQ de Compra vinculada a pr_line
        partner = self.env['res.partner'].create({'name': 'Proveedor General Test', 'company_id': self.company.id})
        po = self.env['purchase.order'].create({
            'partner_id': partner.id,
            'company_id': self.company.id,
            'order_line': [(0, 0, {
                'product_id': self.product_a.id,
                'name': self.product_a.name,
                'product_qty': 20.0,
                'product_uom_id': self.product_a.uom_id.id,
                'price_unit': 150.0,
                'date_planned': fields.Datetime.now(),
            })],
        })
        # Vincular allocation a la PO line
        alloc_po = req.line_ids[0].allocation_ids.filtered(lambda a: a.purchase_request_line_id == pr_line)
        if alloc_po:
            alloc_po.purchase_line_id = po.order_line[0].id
        self.assertIn(po, req.purchase_order_ids)

        # C. Crear Solicitud de Suministro para product_b
        sr = self.env['requisition.supply.request'].create({
            'company_id': self.company.id,
            'warehouse_origin_id': self.wh_central.id,
            'warehouse_dest_id': self.wh_obra.id,
            'line_ids': [(0, 0, {
                'requisition_line_id': req.line_ids[1].id,
                'product_id': self.product_b.id,
                'product_qty': 15.0,
            })],
        })
        sr_line = sr.line_ids[0]

        # Crear albarán de entrega simulado en la requisición
        picking_type = self.env['stock.picking.type'].search([
            ('warehouse_id', '=', self.wh_obra.id),
            ('code', '=', 'internal'),
        ], limit=1)
        if not picking_type:
            picking_type = self.env['stock.picking.type'].search([('code', '=', 'internal')], limit=1)

        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'supply_request_id': sr.id,
            'move_ids': [(0, 0, {
                'description_picking': 'Entrega varilla',
                'product_id': self.product_a.id,
                'product_uom_qty': 5.0,
                'product_uom': self.product_a.uom_id.id,
                'location_id': picking_type.default_location_src_id.id,
                'location_dest_id': picking_type.default_location_dest_id.id,
                'requisition_line_id': req.line_ids[0].id,
            })],
        })
        if req.line_ids[0].allocation_ids:
            req.line_ids[0].allocation_ids[0].stock_move_id = picking.move_ids[0].id

        # D. Ejecutar Cancelación por el Líder / Aprobador
        wiz = self.env['requisition.cancel.wizard'].with_user(self.user_lider).create({
            'requisition_id': req.id,
            'reason': 'Cancelación general de obra por orden ejecutiva',
        })
        wiz.action_confirm_cancel()

        # E. Verificaciones Exhaustivas de Cascada:
        # 1. Requisición en cancel
        self.assertEqual(req.state, 'cancel')
        self.assertTrue(all(l.line_state == 'cancel' for l in req.line_ids))

        # 2. Allocations en cancel con saldo cerrado definitivamente
        active_allocs_left = req.line_ids.allocation_ids.filtered(lambda a: a.state in ('draft', 'in_progress'))
        self.assertFalse(active_allocs_left)
        closed_allocs = req.line_ids.allocation_ids.filtered(lambda a: a.state == 'cancel')
        self.assertTrue(closed_allocs)

        # 3. Solicitud de Compra cancelada
        self.assertEqual(pr_line.state, 'cancel')

        # 4. Solicitud de Suministro cancelada
        self.assertEqual(sr_line.state, 'cancel')
        self.assertEqual(sr.state, 'cancel')

        # 5. Orden de Compra / RFQ en borrador cancelada
        self.assertEqual(po.state, 'cancel')

        # 6. Albarán y movimientos cancelados
        self.assertEqual(picking.state, 'cancel')
