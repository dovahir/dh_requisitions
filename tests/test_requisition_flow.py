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

    def test_04_action_draft_blocked_in_progress_and_allowed_from_cancel(self):
        """Valida que no se permita volver a borrador si la requi está en proceso; debe cancelarse primero."""
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
        req.action_confirm()
        req.action_approve()
        req.action_in_progress()
        self.assertEqual(req.state, 'in_progress')

        # 1. Intentar regresar a borrador directamente en estado 'in_progress' debe arrojar UserError
        with self.assertRaises(UserError):
            req.action_draft()

        # 2. Cancelar mediante el asistente de cancelación
        wizard = self.env['requisition.cancel.wizard'].create({
            'requisition_id': req.id,
            'reason': 'Cancelación para corregir y regresar a borrador',
        })
        wizard.action_confirm_cancel()
        self.assertEqual(req.state, 'cancel')

        # 3. Estando en estado 'cancel', ahora SÍ se permite regresar a borrador
        req.action_draft()
        self.assertEqual(req.state, 'draft')
        self.assertEqual(req.line_ids[0].qty_pending, 10.0)
        self.assertEqual(req.line_ids[0].line_state, 'draft')

    def test_05_action_draft_from_to_approve(self):
        """Valida que se pueda regresar a borrador cuando la requisición está por autorizar (to_approve)."""
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
        self.assertEqual(req.state, 'to_approve', "El estado debe ser 'to_approve' tras solicitar aprobación.")

        # Regresar a borrador desde to_approve
        req.action_draft()
        self.assertEqual(req.state, 'draft', "La requisición debió regresar a borrador exitosamente.")
        self.assertFalse(req.approved_by_id)
        self.assertFalse(req.date_approved)

    def test_06_action_draft_from_approved(self):
        """Valida que una requisición aprobada sin flujo iniciado pueda regresar a borrador."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [
                (0, 0, {
                    'product_id': self.product.id,
                    'product_qty': 4.0,
                    'line_type': 'material',
                })
            ]
        })
        req.action_confirm()
        req.action_approve()
        self.assertEqual(req.state, 'approved')

        # Regresar a borrador desde approved
        req.action_draft()
        self.assertEqual(req.state, 'draft')
        self.assertFalse(req.approved_by_id)
        self.assertFalse(req.date_approved)

    def test_07_priority_normal_and_urgent(self):
        """Valida que el campo prioridad maneje 'normal' y 'urgent' y se propague a las líneas."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'priority': 'urgent',
            'line_ids': [
                (0, 0, {
                    'product_id': self.product.id,
                    'product_qty': 3.0,
                    'line_type': 'material',
                })
            ]
        })
        self.assertEqual(req.priority, 'urgent')
        self.assertEqual(req.line_ids[0].priority, 'urgent')

        req.write({'priority': 'normal'})
        self.assertEqual(req.priority, 'normal')
        self.assertEqual(req.line_ids[0].priority, 'normal')

    def test_08_line_state_by_channel_and_cancellation_chatter_notification(self):
        """Valida los estados de línea diferenciados por canal (in_stock, in_purchase, in_mixed, parciales) y la notificación en chatter."""
        # 1. Requisición inicial aprobada
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
        req.action_confirm()
        req.action_approve()
        line = req.line_ids[0]
        self.assertEqual(line.line_state, 'pending')

        # 2. Solicitud parcial a compras (5 de 10)
        req._create_purchase_request_lines({line.id: 5.0})
        self.assertEqual(line.line_state, 'partial_purchase')

        # 3. Solicitud a almacén por 5.0 (unifica con la compra de 5.0, cantidad restante por tramitar: 5.0)
        sr = self.env['requisition.supply.request'].create({
            'company_id': self.env.company.id,
            'warehouse_origin_id': self.warehouse.id,
            'warehouse_dest_id': self.warehouse.id,
        })
        wizard = self.env['requisition.supply.request.wizard'].with_context(
            active_id=req.id
        ).create({
            'requisition_id': req.id,
            'supply_request_id': sr.id,
            'warehouse_origin_id': self.warehouse.id,
            'line_ids': [
                (0, 0, {
                    'requisition_line_id': line.id,
                    'product_id': self.product.id,
                    'product_qty': 5.0,
                    'selected': True,
                })
            ]
        })
        wizard.action_confirm()
        self.assertEqual(line.line_state, 'partial_mixed')

        # Completar los 5.0 restantes en suministro para alcanzar 100% canalizado (in_mixed)
        wizard_rem = self.env['requisition.supply.request.wizard'].with_context(
            active_id=req.id
        ).create({
            'requisition_id': req.id,
            'supply_request_id': sr.id,
            'warehouse_origin_id': self.warehouse.id,
            'line_ids': [
                (0, 0, {
                    'requisition_line_id': line.id,
                    'product_id': self.product.id,
                    'product_qty': 5.0,
                    'selected': True,
                })
            ]
        })
        wizard_rem.action_confirm()
        self.assertEqual(line.line_state, 'in_mixed')

        # 4. Canal 100% compras
        req_purch = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [
                (0, 0, {
                    'product_id': self.product.id,
                    'product_qty': 6.0,
                    'line_type': 'material',
                })
            ]
        })
        req_purch.action_confirm()
        req_purch.action_approve()
        req_purch._create_purchase_request_lines({req_purch.line_ids[0].id: 6.0})
        self.assertEqual(req_purch.line_ids[0].line_state, 'in_purchase')

        # 5. Canal 100% almacén
        req_stock = self.env['requisitions'].create({
            'employee_id': self.employee.id,
            'warehouse_id': self.warehouse.id,
            'user_id': self.env.user.id,
            'line_ids': [
                (0, 0, {
                    'product_id': self.product.id,
                    'product_qty': 4.0,
                    'line_type': 'material',
                })
            ]
        })
        req_stock.action_confirm()
        req_stock.action_approve()
        wizard_stock = self.env['requisition.supply.request.wizard'].with_context(
            active_id=req_stock.id
        ).create({
            'requisition_id': req_stock.id,
            'supply_request_id': sr.id,
            'warehouse_origin_id': self.warehouse.id,
            'line_ids': [
                (0, 0, {
                    'requisition_line_id': req_stock.line_ids[0].id,
                    'product_id': self.product.id,
                    'product_qty': 4.0,
                    'selected': True,
                })
            ]
        })
        wizard_stock.action_confirm()
        self.assertEqual(req_stock.line_ids[0].line_state, 'in_stock')

        # 6. Notificación en chatter ante cancelación definitiva
        alloc = req_stock.line_ids[0].allocation_ids[0]
        alloc.action_cancel_allocation(
            cancel_action='close',
            reason='Cancelación de prueba para verificar chatter'
        )
        # Verificar que se publicó nota en chatter con el ajuste
        messages = self.env['mail.message'].search([
            ('model', '=', 'requisitions'),
            ('res_id', '=', req_stock.id)
        ])
        body_texts = " ".join([m.body or '' for m in messages])
        self.assertIn("Ajuste de Partida", body_texts)
        self.assertIn("Se canceló la cantidad pendiente", body_texts)

