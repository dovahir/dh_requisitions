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

        # Grupos de Requisiciones
        cls.group_user = cls.env.ref('dh_requisitions.group_requisitions_requester')
        cls.group_approver = cls.env.ref('dh_requisitions.group_requisitions_approver')
        cls.group_manager = cls.env.ref('dh_requisitions.group_requisitions_manager')

        # Grupos de Suministro
        cls.group_stock_read = cls.env.ref('dh_requisitions.group_stock_request_only_read')
        cls.group_stock_planta = cls.env.ref('dh_requisitions.group_stock_request_planta')
        cls.group_stock_base = cls.env.ref('dh_requisitions.group_stock_request_employee')

        # Grupos de Compras (Nativos de Odoo)
        cls.group_purchase = cls.env.ref('purchase.group_purchase_user')
        cls.group_purchase_manager = cls.env.ref('purchase.group_purchase_manager')

        # Almacenes (Base y Obra)
        cls.warehouse_base = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        if not cls.warehouse_base:
            cls.warehouse_base = cls.env['stock.warehouse'].create({
                'name': 'Almacén Base Test',
                'code': 'WH_BASE',
                'company_id': cls.company.id,
            })
        cls.warehouse_obra = cls.env['stock.warehouse'].create({
            'name': 'Almacén Obra Test',
            'code': 'WH_OBRA',
            'company_id': cls.company.id,
        })
        cls.company.requisition_default_warehouse_origin_id = cls.warehouse_base.id

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
        cls.product_extra = cls.env['product.product'].create({
            'name': 'Cemento Gris Test',
            'type': 'consu',
            'categ_id': cls.categ_material.id,
        })

        # Empleados y Usuarios
        # 1. Solicitante
        cls.user_requester = cls.env['res.users'].create({
            'name': 'Usuario Solicitante Test',
            'login': 'solicitante_test@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_user.id])],
        })
        cls.employee_requester = cls.env['hr.employee'].create({
            'name': 'Empleado Solicitante Test',
            'user_id': cls.user_requester.id,
            'company_id': cls.company.id,
        })

        # 2. Líder / Aprobador
        cls.user_aprobador = cls.env['res.users'].create({
            'name': 'Usuario Líder Aprobador Test',
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

        # 3. Empleado Almacén Obra
        cls.user_obra = cls.env['res.users'].create({
            'name': 'Usuario Almacén Obra Test',
            'login': 'obra_test@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_stock_planta.id])],
        })

        # 4. Empleado Almacén Base
        cls.user_base = cls.env['res.users'].create({
            'name': 'Usuario Almacén Base Test',
            'login': 'base_test@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_stock_base.id])],
        })

        # 5. Usuario Compras (Nativo)
        cls.user_compras = cls.env['res.users'].create({
            'name': 'Usuario Compras Test',
            'login': 'compras_test@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_purchase.id])],
        })

        # 6. Administrador de Compras (Nativo)
        cls.user_compras_mgr = cls.env['res.users'].create({
            'name': 'Administrador Compras Test',
            'login': 'compras_mgr_test@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_purchase_manager.id])],
        })

        # 7. Administrador de Requisiciones
        cls.user_manager = cls.env['res.users'].create({
            'name': 'Administrador Requisiciones Test',
            'login': 'manager_req_test@example.com',
            'company_id': cls.company.id,
            'company_ids': [(6, 0, [cls.company.id])],
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, cls.group_manager.id])],
        })

    def test_01_user_visibility_post_draft(self):
        """Valida que el usuario solicitante pueda consultar su requisición en estado aprobado."""
        req = self.env['requisitions'].with_user(self.user_requester).create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.warehouse_base.id,
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

        # Aprobar con el usuario autorizador (Líder / Aprobador)
        req.with_user(self.user_aprobador).action_approve()
        self.assertEqual(req.state, 'approved')

        # El usuario solicitante DEBE poder leer su requisición y líneas aun cuando esté aprobada
        req_as_user = self.env['requisitions'].with_user(self.user_requester).search([('id', '=', req.id)])
        self.assertTrue(req_as_user, "El usuario debe poder consultar su requisición aprobada.")
        self.assertEqual(req_as_user.line_ids[0].product_qty, 10.0)

    def test_02_user_cannot_edit_approved_requisition(self):
        """Valida que un usuario estándar no pueda modificar una requisición fuera de borrador."""
        req = self.env['requisitions'].with_user(self.user_requester).create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.warehouse_base.id,
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
            req.with_user(self.user_requester).write({'priority': 'urgent'})

    def test_03_delivery_wizard_permissions(self):
        """Valida que action_open_delivery_wizard permita acceso a personal de almacén obra/base y lo bloquee al solicitante."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.warehouse_base.id,
            'user_id': self.user_aprobador.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 8.0,
                'line_type': 'material',
            })],
        })
        req.action_confirm()
        req.with_user(self.user_aprobador).action_approve()

        # Usuario solicitante no puede abrir asistente de entregas
        with self.assertRaises(UserError):
            req.with_user(self.user_requester).action_open_delivery_wizard()

        # Usuario de Obra (group_stock_request_planta) SÍ puede abrir el asistente de entrega
        res_obra = req.with_user(self.user_obra).action_open_delivery_wizard()
        self.assertEqual(res_obra.get('res_model'), 'requisition.delivery.wizard')

        # Usuario de Base (group_stock_request_employee) también SÍ puede
        res_base = req.with_user(self.user_base).action_open_delivery_wizard()
        self.assertEqual(res_base.get('res_model'), 'requisition.delivery.wizard')

    def test_04_supply_request_actions_restricted(self):
        """Valida que las operaciones de corte y entrega a tránsito sean exclusivas de Base."""
        supply_req = self.env['requisition.supply.request'].create({
            'company_id': self.company.id,
            'warehouse_origin_id': self.warehouse_base.id,
            'warehouse_dest_id': self.warehouse_obra.id,
            'location_id': self.warehouse_base.lot_stock_id.id,
            'location_dest_id': self.warehouse_obra.lot_stock_id.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 5.0,
            })],
        })
        # Usuario solicitante no puede crear entrega ni hacer corte
        with self.assertRaises(UserError):
            supply_req.with_user(self.user_requester).action_create_delivery()

        with self.assertRaises(UserError):
            supply_req.with_user(self.user_requester).action_open_block_wizard()

        # Usuario de Obra NO puede hacer corte ni salida a tránsito (operación de Base)
        with self.assertRaises(UserError):
            supply_req.with_user(self.user_obra).action_create_delivery()

        with self.assertRaises(UserError):
            supply_req.with_user(self.user_obra).action_open_block_wizard()

        # Usuario de Base SÍ puede operar corte y bloqueo
        res_block = supply_req.with_user(self.user_base).action_open_block_wizard()
        self.assertEqual(res_block.get('res_model'), 'requisition.supply.request.block.wizard')

    def test_05_purchase_request_actions(self):
        """Valida que los usuarios de compra puedan ver y crear líneas, pero solo los administradores puedan añadir a RFQ, cancelar o eliminar."""
        # Usuario regular de compras (purchase.group_purchase_user) puede crear líneas
        pr_line = self.env['requisition.purchase.request.line'].with_user(self.user_compras).create({
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'product_qty': 12.0,
            'company_id': self.company.id,
            'warehouse_id': self.warehouse_base.id,
        })
        self.assertTrue(pr_line.id, "El usuario de compras debe poder crear líneas de solicitud de compra.")

        # 1. Usuario regular de compras NO puede abrir el wizard de RFQ
        with self.assertRaises(UserError):
            pr_line.with_user(self.user_compras).action_open_add_to_rfq_wizard()

        # 2. Solicitante sin compras NO puede abrir el wizard de RFQ
        with self.assertRaises(UserError):
            pr_line.with_user(self.user_requester).action_open_add_to_rfq_wizard()

        # 3. Usuario regular de compras NO puede cancelar la partida
        with self.assertRaises(UserError):
            pr_line.with_user(self.user_compras).action_cancel_line()

        # 4. Usuario regular de compras NO puede eliminar la partida
        with self.assertRaises(UserError):
            pr_line.with_user(self.user_compras).unlink()

        # 5. Administrador de Compras (purchase.group_purchase_manager) SÍ puede abrir el wizard de RFQ
        res_pr = pr_line.with_user(self.user_compras_mgr).action_open_add_to_rfq_wizard()
        self.assertEqual(res_pr.get('res_model'), 'requisition.purchase.request.add.to.rfq.wizard')

        # 6. Administrador de Compras SÍ puede cancelar la partida
        pr_line.with_user(self.user_compras_mgr).action_cancel_line(cancel_action='close', reason='Cancelación por prueba de permisos')
        self.assertEqual(pr_line.state, 'cancel')

        # 7. Administrador de Compras puede eliminar partidas en estado pendiente
        pr_line_to_delete = self.env['requisition.purchase.request.line'].with_user(self.user_compras_mgr).create({
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'product_qty': 5.0,
            'company_id': self.company.id,
            'warehouse_id': self.warehouse_base.id,
        })
        pr_line_to_delete.with_user(self.user_compras_mgr).unlink()
        self.assertFalse(pr_line_to_delete.exists())

    def test_06_planta_manual_lines_restriction(self):
        """Valida que personal de Obra pueda agregar líneas manuales pero no editar ni eliminar líneas de otros/Base."""
        # Solicitud de suministro creada con una línea por Almacén Base
        supply_req = self.env['requisition.supply.request'].with_user(self.user_base).create({
            'company_id': self.company.id,
            'warehouse_origin_id': self.warehouse_base.id,
            'warehouse_dest_id': self.warehouse_obra.id,
            'location_id': self.warehouse_base.lot_stock_id.id,
            'location_dest_id': self.warehouse_obra.lot_stock_id.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 10.0,
            })],
        })
        base_line = supply_req.line_ids[0]

        # 1. Almacén Obra intenta modificar la línea de Base -> Debe ser rechazado
        with self.assertRaises(UserError):
            base_line.with_user(self.user_obra).write({'product_qty': 20.0})

        # 2. Almacén Obra intenta eliminar la línea de Base -> Debe ser rechazado
        with self.assertRaises(UserError):
            base_line.with_user(self.user_obra).unlink()

        # 3. Almacén Obra agrega una línea manual
        manual_line = self.env['requisition.supply.request.line'].with_user(self.user_obra).create({
            'request_id': supply_req.id,
            'product_id': self.product_extra.id,
            'product_qty': 5.0,
        })
        self.assertTrue(manual_line.is_manual, "La línea manual debe quedar identificada.")
        self.assertEqual(manual_line.create_uid, self.user_obra)

        # 4. Almacén Obra SÍ puede modificar su propia línea manual
        manual_line.with_user(self.user_obra).write({'product_qty': 7.0})
        self.assertEqual(manual_line.product_qty, 7.0)

        # 5. Almacén Obra SÍ puede eliminar su propia línea manual
        manual_line.with_user(self.user_obra).unlink()
        self.assertFalse(manual_line.exists())

    def test_07_purchase_user_reads_requisition_no_access_error(self):
        """Valida que un comprador nativo pueda leer requisiciones sin que Odoo lance AccessError."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.warehouse_base.id,
            'user_id': self.user_aprobador.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 15.0,
                'line_type': 'material',
            })],
        })
        req.action_confirm()
        req.with_user(self.user_aprobador).action_approve()

        # Usuario de compras consulta la requisición
        req_as_purchase = self.env['requisitions'].with_user(self.user_compras).browse(req.id)
        # Acceder a campos de cabecera y líneas no debe arrojar AccessError
        self.assertEqual(req_as_purchase.name, req.name)
        self.assertEqual(len(req_as_purchase.line_ids), 1)
        self.assertEqual(req_as_purchase.line_ids[0].product_qty, 15.0)

    def test_08_privileges_declaration_and_groups_orthogonality(self):
        """Valida que exactamente 2 privilegios existan en Cadena de Suministro y sean ortogonales (sin cómputos indeseados)."""
        priv_req = self.env.ref('dh_requisitions.res_groups_privilege_requisitions', raise_if_not_found=False)
        priv_stock = self.env.ref('dh_requisitions.res_groups_privilege_stock_request', raise_if_not_found=False)
        priv_purchase = self.env.ref('dh_requisitions.res_groups_privilege_purchase_request', raise_if_not_found=False)

        self.assertTrue(priv_req, "El privilegio de Requisiciones debe existir.")
        self.assertTrue(priv_stock, "El privilegio de Suministro debe existir.")
        self.assertFalse(priv_purchase, "No debe existir privilegio de compra en Cadena de Suministro (se usan grupos nativos legacy).")

        # Verificar que el privilegio de Requisiciones contiene Solicitante, Aprobador y Administrador
        req_group_ids = priv_req.group_ids.ids
        self.assertIn(self.group_user.id, req_group_ids)
        self.assertEqual(self.group_user.name, "Solicitante")
        self.assertEqual(self.group_manager.name, "Administrador")

        # Verificar que el privilegio de Suministro contiene Consulta, Planta y Base
        stock_group_ids = priv_stock.group_ids.ids
        self.assertIn(self.group_stock_read.id, stock_group_ids)
        self.assertIn(self.group_stock_planta.id, stock_group_ids)
        self.assertIn(self.group_stock_base.id, stock_group_ids)

        # Verificar que group_requisitions_manager NO implica group_stock_request_employee
        self.assertNotIn(
            self.group_stock_base.id,
            self.group_manager.implied_ids.ids,
            "El Administrador no debe implicar almacén base para no podar la UI de Odoo 19."
        )

    def test_09_requester_can_return_rejected_to_draft_and_resubmit(self):
        """Valida que un solicitante pueda regresar a borrador su requisición rechazada sin AccessError y re-enviarla."""
        req = self.env['requisitions'].with_user(self.user_requester).create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.warehouse_base.id,
            'user_id': self.user_aprobador.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 5.0,
                'line_type': 'material',
            })],
        })
        req.action_confirm()
        self.assertEqual(req.state, 'to_approve')

        # Líder rechaza la requisición con motivo
        wizard = self.env['requisition.reject.wizard'].with_user(self.user_aprobador).create({
            'requisition_id': req.id,
            'reason': 'Cantidad excesiva para el inventario actual',
        })
        wizard.action_confirm_reject()
        self.assertEqual(req.state, 'rejected')
        self.assertEqual(req.rejected_by_id, self.user_aprobador)

        # El Solicitante DEBE poder regresar a borrador sin AccessError de ir.rule
        req.with_user(self.user_requester).action_draft()
        self.assertEqual(req.state, 'draft')
        self.assertFalse(req.rejected_by_id)

        # En estado borrador puede ajustar la partida y volver a solicitar aprobación
        req.line_ids[0].with_user(self.user_requester).write({'product_qty': 3.0})
        self.assertEqual(req.line_ids[0].product_qty, 3.0)
        req.with_user(self.user_requester).action_confirm()
        self.assertEqual(req.state, 'to_approve')

    def test_10_cancel_wizard_permissions(self):
        """Valida que la cancelación interactiva con justificación esté disponible para Líder/Aprobador y Administrador, y bloqueada para Solicitante."""
        req = self.env['requisitions'].with_user(self.user_requester).create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.warehouse_base.id,
            'user_id': self.user_aprobador.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 4.0,
                'line_type': 'material',
            })],
        })
        # 1. En borrador, el Solicitante no puede abrir el wizard de cancelación
        with self.assertRaises(UserError):
            req.with_user(self.user_requester).action_open_cancel_wizard()

        # 2. En borrador, el Aprobador SÍ puede abrir el wizard de cancelación
        res_draft = req.with_user(self.user_aprobador).action_open_cancel_wizard()
        self.assertEqual(res_draft.get('res_model'), 'requisition.cancel.wizard')

        req.action_confirm()
        req.with_user(self.user_aprobador).action_approve()
        self.assertEqual(req.state, 'approved')

        # 3. Solicitante no puede cancelar requisición aprobada
        with self.assertRaises(UserError):
            req.with_user(self.user_requester).action_open_cancel_wizard()

        # 4. Aprobador (Líder) SÍ puede abrir el asistente de cancelación
        res_aprobador = req.with_user(self.user_aprobador).action_open_cancel_wizard()
        self.assertEqual(res_aprobador.get('res_model'), 'requisition.cancel.wizard')

        # 5. Administrador de requisiciones SÍ puede
        res = req.with_user(self.user_manager).action_open_cancel_wizard()
        self.assertEqual(res.get('res_model'), 'requisition.cancel.wizard')

        # 6. Ejecutar cancelación interactiva por el Aprobador
        wiz = self.env['requisition.cancel.wizard'].with_user(self.user_aprobador).create({
            'requisition_id': req.id,
            'reason': 'Cancelado por cambio de prioridades en obra',
        })
        wiz.action_confirm_cancel()
        self.assertEqual(req.state, 'cancel')
        self.assertEqual(req.line_ids.line_state, 'cancel')

    def test_11_allocation_cancel_wizard_permissions(self):
        """Valida que solo Administradores de Requisiciones puedan confirmar la cancelación de asignaciones en el libro mayor."""
        req = self.env['requisitions'].create({
            'employee_id': self.employee_requester.id,
            'warehouse_id': self.warehouse_base.id,
            'user_id': self.user_aprobador.id,
            'line_ids': [(0, 0, {
                'product_id': self.product.id,
                'product_qty': 10.0,
                'line_type': 'material',
            })],
        })
        req.action_confirm()
        req.with_user(self.user_aprobador).action_approve()

        alloc = self.env['requisition.allocation'].create({
            'company_id': self.company.id,
            'requisition_line_id': req.line_ids[0].id,
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'requested_qty': 10.0,
            'delivered_qty': 0.0,
            'stage': 'inbound',
            'state': 'in_progress',
        })

        # Usuario solicitante no puede crear wizard de cancelación de asignación (bloqueado por ACLs)
        with self.assertRaises(AccessError):
            self.env['requisition.allocation.cancel.wizard'].with_user(self.user_requester).create({
                'allocation_ids': [(6, 0, [alloc.id])],
                'cancel_action': 'close',
                'reason': 'Intento no autorizado por usuario',
            })

        # Usuario Almacén Base tampoco puede crear wizard de cancelación de asignación
        with self.assertRaises(AccessError):
            self.env['requisition.allocation.cancel.wizard'].with_user(self.user_base).create({
                'allocation_ids': [(6, 0, [alloc.id])],
                'cancel_action': 'close',
                'reason': 'Intento por almacén base',
            })

        # Validación en action_confirm_cancel si un usuario sin rol manager intentara ejecutar la acción
        wiz_sudo = self.env['requisition.allocation.cancel.wizard'].create({
            'allocation_ids': [(6, 0, [alloc.id])],
            'cancel_action': 'close',
            'reason': 'Test confirm',
        })
        with self.assertRaises(UserError):
            wiz_sudo.with_user(self.user_requester).action_confirm_cancel()

        # Administrador de Requisiciones SÍ puede confirmar la cancelación
        wiz_manager = self.env['requisition.allocation.cancel.wizard'].with_user(self.user_manager).create({
            'allocation_ids': [(6, 0, [alloc.id])],
            'cancel_action': 'close',
            'reason': 'Cancelación autorizada por Gerencia',
        })
        wiz_manager.action_confirm_cancel()
        self.assertEqual(alloc.state, 'cancel')
        self.assertEqual(alloc.cancelled_qty, 10.0)

