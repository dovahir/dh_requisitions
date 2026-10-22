# -*- coding: utf-8 -*-
from odoo.tests import common, tagged
from odoo.exceptions import UserError, ValidationError


@tagged('post_install', '-at_install', 'dh_requisitions')
class TestAuditEdgeCases(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.requisition_allow_auto_approval = False

        cls.base_warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        if not cls.base_warehouse:
            cls.base_warehouse = cls.env['stock.warehouse'].create({
                'name': 'Almacén Base Test',
                'code': 'WH_BASE',
                'company_id': cls.company.id,
            })

        cls.obra_warehouse = cls.env['stock.warehouse'].create({
            'name': 'Almacén Obra Test',
            'code': 'WH_OBRA',
            'company_id': cls.company.id,
        })

        cls.categ_material = cls.env['product.category'].create({
            'name': 'Materiales Test Audit',
            'requisition_type': 'material',
            'allow_requisition': True,
        })

        cls.categ_service = cls.env['product.category'].create({
            'name': 'Servicios Test Audit',
            'requisition_type': 'service',
            'allow_requisition': True,
        })

        cls.product_mat = cls.env['product.product'].create({
            'name': 'Varilla 3/8 Test',
            'type': 'consu',
            'categ_id': cls.categ_material.id,
        })

        cls.product_service = cls.env['product.product'].create({
            'name': 'Mantenimiento Preventivo Test',
            'type': 'service',
            'categ_id': cls.categ_service.id,
        })

        # Empleados y usuarios
        cls.group_user = cls.env.ref('dh_requisitions.group_requisitions_user')
        cls.group_approver = cls.env.ref('dh_requisitions.group_requisitions_approver')
        cls.group_manager = cls.env.ref('dh_requisitions.group_requisitions_manager')
        cls.group_stock = cls.env.ref('stock.group_stock_user')
        cls.group_purchase = cls.env.ref('purchase.group_purchase_user')

        cls.group_internal = cls.env.ref('base.group_user')
        cls.env.user.write({
            'group_ids': [
                (4, cls.group_approver.id),
                (4, cls.group_manager.id),
            ]
        })

        cls.user_requester = cls.env['res.users'].create({
            'name': 'Usuario Solicitante Test',
            'login': 'user_req_test',
            'email': 'user_req@test.com',
            'group_ids': [(6, 0, [cls.group_internal.id, cls.group_user.id])],
        })

        cls.employee_requester = cls.env['hr.employee'].create({
            'name': 'Empleado Solicitante Test',
            'user_id': cls.user_requester.id,
            'company_id': cls.company.id,
        })

        cls.user_approver_1 = cls.env['res.users'].create({
            'name': 'Aprobador Designado Test',
            'login': 'user_app1_test',
            'email': 'user_app1@test.com',
            'group_ids': [(6, 0, [cls.group_internal.id, cls.group_approver.id])],
        })

        cls.employee_approver_1 = cls.env['hr.employee'].create({
            'name': 'Empleado Aprobador 1 Test',
            'user_id': cls.user_approver_1.id,
            'company_id': cls.company.id,
        })

        cls.user_approver_2 = cls.env['res.users'].create({
            'name': 'Aprobador Secundario Test',
            'login': 'user_app2_test',
            'email': 'user_app2@test.com',
            'group_ids': [(6, 0, [cls.group_internal.id, cls.group_approver.id])],
        })

    def test_h01_h08_action_approve_security_and_state(self):
        """H-01 y H-08: No se puede aprobar en borrador ni vacías, y valida autorizador y auto-aprobación."""
        # 1. Requisición vacía no se puede confirmar
        req_empty = self.env['requisitions'].with_user(self.user_requester).create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.base_warehouse.id,
            'user_id': self.user_approver_1.id,
        })
        with self.assertRaises(ValidationError):
            req_empty.action_confirm()

        # 2. Requisición con líneas en draft no se puede aprobar directamente
        req = self.env['requisitions'].with_user(self.user_requester).create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.base_warehouse.id,
            'user_id': self.user_approver_1.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_mat.id,
                'product_qty': 10.0,
                'line_type': 'material',
            })]
        })
        self.assertEqual(req.state, 'draft')
        with self.assertRaises(UserError):
            req.with_user(self.user_approver_1).action_approve()

        # 3. Confirmar a to_approve
        req.action_confirm()
        self.assertEqual(req.state, 'to_approve')

        # 4. Aprobador no autorizado intenta aprobar (user_approver_2 no es user_id ni manager)
        with self.assertRaises(UserError):
            req.with_user(self.user_approver_2).action_approve()

        # 5. Aprobador designado aprueba correctamente
        req.with_user(self.user_approver_1).action_approve()
        self.assertEqual(req.state, 'approved')
        self.assertEqual(req.approved_by_id, self.user_approver_1)

    def test_h08_self_approval_blocked(self):
        """H-08: Auto-aprobación del solicitante/elaborador bloqueada si requisition_allow_auto_approval es False."""
        self.company.requisition_allow_auto_approval = False
        req = self.env['requisitions'].with_user(self.user_approver_1).create({
            'employee_id': self.employee_approver_1.id,
            'warehouse_id': self.base_warehouse.id,
            'user_id': self.user_approver_1.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_mat.id,
                'product_qty': 5.0,
                'line_type': 'material',
            })]
        })
        req.action_confirm()
        self.assertEqual(req.state, 'to_approve')

        # El solicitante intenta auto-aprobarse
        with self.assertRaises(UserError):
            req.with_user(self.user_approver_1).action_approve()

    def test_h02_action_draft_and_line_modification_sync(self):
        """H-02: Volver a borrador y modificar cantidad propaga cambios a allocations y chatters."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.base_warehouse.id,
            'user_id': self.user_approver_1.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_mat.id,
                'product_qty': 10.0,
                'line_type': 'material',
            })]
        })
        req.action_confirm()
        req.action_approve()

        # Crear una allocation para la línea simulando flujo
        line = req.line_ids[0]
        alloc = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'product_id': line.product_id.id,
            'product_uom_id': line.product_uom_id.id,
            'requested_qty': 10.0,
            'delivered_qty': 0.0,
            'supply_type': 'consumption',
            'requisition_line_id': line.id,
            'state': 'in_progress',
        })

        # Regresar a borrador
        req.action_draft()
        self.assertEqual(req.state, 'draft')

        # Modificar cantidad a 15.0
        line.write({'product_qty': 15.0})
        self.assertEqual(alloc.requested_qty, 15.0)

    def test_h03_cancel_wizard_cascades_pickings_and_prs(self):
        """H-03: Cancelar requisición cancela albaranes y solicitudes de compra asociadas."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.base_warehouse.id,
            'user_id': self.user_approver_1.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_mat.id,
                'product_qty': 20.0,
                'line_type': 'material',
            })]
        })
        req.action_confirm()
        req.action_approve()

        # Simular albarán abierto vinculado a allocation
        line = req.line_ids[0]
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.base_warehouse.out_type_id.id,
            'location_id': self.base_warehouse.lot_stock_id.id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
        })
        move = self.env['stock.move'].create({
            'product_id': line.product_id.id,
            'product_uom_qty': 20.0,
            'product_uom': line.product_uom_id.id,
            'picking_id': picking.id,
            'location_id': self.base_warehouse.lot_stock_id.id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
            'company_id': self.company.id,
        })
        picking.action_confirm()

        alloc = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'product_id': line.product_id.id,
            'product_uom_id': line.product_uom_id.id,
            'requested_qty': 20.0,
            'delivered_qty': 0.0,
            'supply_type': 'consumption',
            'requisition_line_id': line.id,
            'stock_move_id': move.id,
            'state': 'in_progress',
        })

        # Cancelar mediante el asistente
        wizard = self.env['requisition.cancel.wizard'].create({
            'requisition_id': req.id,
            'reason': 'Prueba de cancelación en cascada',
        })
        wizard.action_confirm_cancel()

        self.assertEqual(req.state, 'cancel')
        self.assertEqual(picking.state, 'cancel')
        self.assertEqual(alloc.state, 'cancel')

    def test_h04_action_create_purchase_request_single_allocation(self):
        """H-04: action_create_purchase_request no crea asignaciones redundantes en traslado Base a Obra."""
        self.env.user.write({
            'group_ids': [(4, self.group_purchase.id), (4, self.group_manager.id)]
        })
        req = self.env['requisitions'].create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.obra_warehouse.id,
            'user_id': self.user_approver_1.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_mat.id,
                'product_qty': 12.0,
                'line_type': 'material',
            })]
        })
        req.action_confirm()
        req.action_approve()

        line = req.line_ids[0]
        req.action_create_purchase_request()

        # Debe existir exactamente 1 allocation para la línea, no 2
        allocs = line.allocation_ids.filtered(lambda a: a.state != 'cancel')
        self.assertEqual(len(allocs), 1)
        self.assertEqual(allocs.requested_qty, 12.0)

    def test_h05_supply_request_line_unlink_cancellation(self):
        """H-05: Eliminar una partida de suministro invoca action_cancel_allocation sin crash."""
        sr = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.base_warehouse.id,
            'warehouse_dest_id': self.obra_warehouse.id,
            'company_id': self.company.id,
        })
        sr_line = self.env['requisition.supply.request.line'].create({
            'request_id': sr.id,
            'product_id': self.product_mat.id,
            'product_qty': 8.0,
            'warehouse_origin_id': self.base_warehouse.id,
            'warehouse_dest_id': self.obra_warehouse.id,
        })
        alloc = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'product_id': self.product_mat.id,
            'product_uom_id': self.product_mat.uom_id.id,
            'requested_qty': 8.0,
            'delivered_qty': 0.0,
            'supply_type': 'consumption',
            'supply_request_line_id': sr_line.id,
            'state': 'in_progress',
        })

        # Al borrar la partida, debe cancelar la allocation sin AttributeError
        sr_line.unlink()
        self.assertFalse(alloc.exists())

    def test_h09_split_wizard_draft_validation(self):
        """H-09: No se pueden dividir partidas si la solicitud de suministro no está en draft."""
        sr = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.base_warehouse.id,
            'warehouse_dest_id': self.obra_warehouse.id,
            'company_id': self.company.id,
        })
        sr_line = self.env['requisition.supply.request.line'].create({
            'request_id': sr.id,
            'product_id': self.product_mat.id,
            'product_qty': 10.0,
            'warehouse_origin_id': self.base_warehouse.id,
            'warehouse_dest_id': self.obra_warehouse.id,
        })
        # Pasar solicitud a delivery_created o similar
        sr.state = 'delivery_created'

        wizard = self.env['requisition.supply.request.line.split.wizard'].create({
            'line_id': sr_line.id,
            'qty_to_send': 4.0,
        })
        with self.assertRaises(UserError):
            wizard.action_confirm_split()

    def test_h11_header_returns_to_approved_when_all_lines_pending(self):
        """H-11: _update_state_from_lines regresa la requisición a approved si todas las líneas vuelven a pending."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.base_warehouse.id,
            'user_id': self.user_approver_1.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_mat.id,
                'product_qty': 5.0,
                'line_type': 'material',
            })]
        })
        req.action_confirm()
        req.action_approve()
        req.action_in_progress()
        self.assertEqual(req.state, 'in_progress')

        # La línea tiene line_state = pending
        line = req.line_ids[0]
        self.assertEqual(line.line_state, 'pending')

        req._update_state_from_lines()
        self.assertEqual(req.state, 'approved')

    def test_h12_service_rfq_requires_vendor(self):
        """H-12: action_create_service_rfq arroja UserError si el producto no tiene proveedor configurado."""
        self.env.user.write({
            'group_ids': [(4, self.group_purchase.id), (4, self.group_manager.id)]
        })
        req = self.env['requisitions'].create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.base_warehouse.id,
            'user_id': self.user_approver_1.id,
            'line_ids': [(0, 0, {
                'product_id': self.product_service.id,
                'product_qty': 1.0,
                'line_type': 'service',
            })]
        })
        req.action_confirm()
        req.action_approve()

        # El producto no tiene seller_ids, debe lanzar UserError
        with self.assertRaises(UserError):
            req.action_create_service_rfq()
