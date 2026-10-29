# -*- coding: utf-8 -*-
from odoo.tests import common, tagged
from odoo.exceptions import UserError


@tagged('post_install', '-at_install', 'dh_requisitions')
class TestSupplyRequest(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.wh_origin = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        if not cls.wh_origin:
            cls.wh_origin = cls.env['stock.warehouse'].create({
                'name': 'Almacén Base Test',
                'code': 'WH_BASE',
                'company_id': cls.company.id,
            })
        cls.company.requisition_default_warehouse_origin_id = cls.wh_origin.id

        cls.product = cls.env['product.product'].create({
            'name': 'Pintura Blanca 19L Test',
            'type': 'consu',
        })

        # Asignar grupos necesarios al usuario de pruebas
        manager_grp = cls.env.ref('dh_requisitions.group_requisitions_manager')
        purchase_grp = cls.env.ref('purchase.group_purchase_user')
        stock_grp = cls.env.ref('stock.group_stock_user')
        cls.env.user.group_ids |= (manager_grp | purchase_grp | stock_grp)

    def test_01_supply_request_creation_and_deletion_protection(self):
        """Valida que una solicitud en curso no pueda eliminarse físicamente gracias a @api.ondelete."""
        supply_req = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_origin.id,
            'warehouse_dest_id': self.wh_origin.id,
            'location_id': self.wh_origin.lot_stock_id.id,
            'line_ids': [
                (0, 0, {
                    'product_id': self.product.id,
                    'product_qty': 4.0,
                })
            ]
        })
        self.assertEqual(supply_req.state, 'draft')
        self.assertEqual(len(supply_req.line_ids), 1)

        line = supply_req.line_ids[0]
        # Al simular estado activo
        supply_req.write({'state': 'in_transit'})
        with self.assertRaises(UserError):
            supply_req.unlink()

        with self.assertRaises(UserError):
            line.unlink()

    def test_02_pending_assignment_lifecycle(self):
        """Valida creación directa de línea en pool de pendientes y sus defaults seguros."""
        pending_line = self.env['requisition.supply.request.line'].create({
            'product_id': self.product.id,
            'product_qty': 5.0,
            'warehouse_dest_id': self.wh_origin.id,
            'warehouse_origin_id': self.wh_origin.id,
        })
        self.assertFalse(pending_line.request_id)
        self.assertEqual(pending_line.state, 'pending_assignment')
        self.assertEqual(pending_line.company_id, self.company)
        self.assertEqual(pending_line.warehouse_origin_id, self.wh_origin)

    def test_03_action_mark_as_pending(self):
        """Valida desacoplar una línea de una SR activa para enviarla al pool de pendientes."""
        supply_req = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_origin.id,
            'warehouse_dest_id': self.wh_origin.id,
            'location_id': self.wh_origin.lot_stock_id.id,
            'line_ids': [
                (0, 0, {
                    'product_id': self.product.id,
                    'product_qty': 10.0,
                })
            ]
        })
        line = supply_req.line_ids[0]
        self.assertEqual(line.state, 'draft')

        line.action_mark_as_pending()
        self.assertFalse(line.request_id)
        self.assertEqual(line.state, 'pending_assignment')
        self.assertEqual(len(supply_req.line_ids), 0)

    def test_04_split_wizard(self):
        """Valida dividir una línea de SR para dejar una porción como pendiente de envío."""
        supply_req = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_origin.id,
            'warehouse_dest_id': self.wh_origin.id,
            'location_id': self.wh_origin.lot_stock_id.id,
            'line_ids': [
                (0, 0, {
                    'product_id': self.product.id,
                    'product_qty': 10.0,
                })
            ]
        })
        line = supply_req.line_ids[0]

        wizard = self.env['requisition.supply.request.line.split.wizard'].with_context(
            active_id=line.id,
            active_model='requisition.supply.request.line'
        ).create({
            'line_id': line.id,
            'qty_to_send': 7.0,
        })
        wizard.action_confirm_split()

        self.assertEqual(line.product_qty, 7.0)
        self.assertEqual(line.request_id, supply_req)

        # Buscar la nueva línea creada como pendiente
        pending_lines = self.env['requisition.supply.request.line'].search([
            ('product_id', '=', self.product.id),
            ('request_id', '=', False),
        ])
        self.assertTrue(pending_lines)
        new_pending = pending_lines.filtered(lambda l: l.product_qty == 3.0)
        self.assertEqual(len(new_pending), 1)
        self.assertEqual(new_pending.state, 'pending_assignment')
        self.assertEqual(new_pending.warehouse_dest_id, self.wh_origin)

    def test_05_add_pending_wizard(self):
        """Valida que una SR pueda incorporar líneas pendientes de su misma obra."""
        # Crear 2 líneas pendientes
        line1 = self.env['requisition.supply.request.line'].create({
            'product_id': self.product.id,
            'product_qty': 2.0,
            'warehouse_dest_id': self.wh_origin.id,
            'warehouse_origin_id': self.wh_origin.id,
        })
        line2 = self.env['requisition.supply.request.line'].create({
            'product_id': self.product.id,
            'product_qty': 4.0,
            'warehouse_dest_id': self.wh_origin.id,
            'warehouse_origin_id': self.wh_origin.id,
        })

        supply_req = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_origin.id,
            'warehouse_dest_id': self.wh_origin.id,
            'location_id': self.wh_origin.lot_stock_id.id,
        })

        wizard_cls = self.env['requisition.supply.request.add.pending.wizard'].with_context(
            active_id=supply_req.id,
            active_model='requisition.supply.request'
        )
        wizard_vals = wizard_cls.default_get(['supply_request_id', 'line_ids'])
        wizard = wizard_cls.create(wizard_vals)

        # Seleccionar line1 y deseleccionar line2
        w_line1 = wizard.line_ids.filtered(lambda l: l.supply_line_id == line1)
        w_line2 = wizard.line_ids.filtered(lambda l: l.supply_line_id == line2)
        if w_line1:
            w_line1.selected = True
        if w_line2:
            w_line2.selected = False
        wizard.action_confirm()

        self.assertEqual(line1.request_id, supply_req)
        self.assertEqual(line1.state, 'draft')
        self.assertFalse(line2.request_id)
        self.assertEqual(line2.state, 'pending_assignment')
        self.assertIn(line1, supply_req.line_ids)

    def test_06_assign_pending_wizard(self):
        """Valida asignación masiva desde la vista de pendientes a una nueva SR."""
        line1 = self.env['requisition.supply.request.line'].create({
            'product_id': self.product.id,
            'product_qty': 5.0,
            'warehouse_dest_id': self.wh_origin.id,
            'warehouse_origin_id': self.wh_origin.id,
        })
        line2 = self.env['requisition.supply.request.line'].create({
            'product_id': self.product.id,
            'product_qty': 3.0,
            'warehouse_dest_id': self.wh_origin.id,
            'warehouse_origin_id': self.wh_origin.id,
        })

        wizard_cls = self.env['requisition.supply.request.assign.pending.wizard'].with_context(
            active_ids=[line1.id, line2.id],
            active_model='requisition.supply.request.line'
        )
        wizard_vals = wizard_cls.default_get([
            'action_type', 'company_id', 'warehouse_dest_id',
            'warehouse_origin_id', 'supply_request_id', 'pending_line_ids'
        ])
        wizard_vals.update({
            'action_type': 'new',
            'warehouse_origin_id': self.wh_origin.id,
            'warehouse_dest_id': self.wh_origin.id,
        })
        wizard = wizard_cls.create(wizard_vals)
        action = wizard.action_confirm()
        new_sr = self.env['requisition.supply.request'].browse(action['res_id'])

        self.assertEqual(line1.request_id, new_sr)
        self.assertEqual(line2.request_id, new_sr)
        self.assertEqual(line1.state, 'draft')
        self.assertEqual(len(new_sr.line_ids), 2)

    def test_07_action_create_purchase_request_line(self):
        """Valida disparar solicitud de compra desde una línea de suministro sin existencias en base."""
        line = self.env['requisition.supply.request.line'].create({
            'product_id': self.product.id,
            'product_qty': 8.0,
            'warehouse_dest_id': self.wh_origin.id,
        })
        action = line.action_create_purchase_request_line()
        wizard = self.env[action['res_model']].with_context(action['context']).create({
            'qty_to_purchase': 8.0,
            'qty_to_send': 0.0,
            'create_pending_supply': True,
        })
        wizard.action_confirm()
        # Verificar que se creó requisition.purchase.request.line vinculada
        prl = self.env['requisition.purchase.request.line'].search([
            ('product_id', '=', self.product.id),
        ])
        self.assertTrue(prl)
        self.assertIn(line.product_qty, prl.mapped('product_qty'))
        self.assertEqual(prl.origin_supply_request_line_id, line)

    def test_08_delivery_wizard_warning(self):
        """Valida que el wizard de despacho advierta falta de stock sin bloquear la generación del albarán."""
        categ = self.env['product.category'].create({
            'name': 'Materiales Wizard Test',
            'requisition_type': 'material',
            'allow_requisition': True,
        })
        product = self.env['product.product'].create({
            'name': 'Cemento Gris Test',
            'type': 'consu',
            'categ_id': categ.id,
        })
        employee = self.env['hr.employee'].create({
            'name': 'Empleado Wizard Test',
            'user_id': self.env.user.id,
            'company_id': self.company.id,
        })
        req = self.env['requisitions'].create({
            'name': 'REQ-WIZARD-001',
            'employee_id': employee.id,
            'user_id': self.env.user.id,
            'warehouse_id': self.wh_origin.id,
            'line_ids': [
                (0, 0, {
                    'product_id': product.id,
                    'product_qty': 15.0,
                    'line_type': 'material',
                })
            ]
        })
        req.action_confirm()
        req.action_approve()

        wizard = self.env['requisition.delivery.wizard'].with_context(
            active_id=req.id,
            active_model='requisitions'
        ).create({
            'requisition_id': req.id,
            'location_id': self.wh_origin.lot_stock_id.id,
            'location_dest_id': self.wh_origin.lot_stock_id.id,
            'picking_type_id': self.wh_origin.out_type_id.id,
            'line_ids': [
                (0, 0, {
                    'requisition_line_id': req.line_ids[0].id,
                    'qty_to_deliver': 15.0,
                })
            ]
        })
        self.assertTrue(wizard.has_stock_warnings)
        self.assertTrue(wizard.line_ids[0].has_insufficient_stock)

        # Generar entrega sin bloquear
        res = wizard.action_confirm()
        self.assertEqual(res.get('res_model'), 'stock.picking')

    def test_09_supply_request_wizard_adds_to_existing_sr_with_notification(self):
        """Valida que el wizard de suministro solo agregue a SR existentes no bloqueadas y retorne notificación."""
        categ = self.env['product.category'].create({
            'name': 'Materiales SR Test',
            'requisition_type': 'material',
            'allow_requisition': True,
        })
        product = self.env['product.product'].create({
            'name': 'Arena Fina Test',
            'type': 'consu',
            'categ_id': categ.id,
        })
        employee = self.env['hr.employee'].create({
            'name': 'Empleado SR Test',
            'user_id': self.env.user.id,
            'company_id': self.company.id,
        })
        req = self.env['requisitions'].create({
            'name': 'REQ-SR-WIZ-001',
            'employee_id': employee.id,
            'user_id': self.env.user.id,
            'warehouse_id': self.wh_origin.id,
            'line_ids': [
                (0, 0, {
                    'product_id': product.id,
                    'product_qty': 10.0,
                    'line_type': 'material',
                })
            ]
        })
        req.action_confirm()
        req.action_approve()

        sr = self.env['requisition.supply.request'].create({
            'warehouse_origin_id': self.wh_origin.id,
            'warehouse_dest_id': self.wh_origin.id,
            'location_id': self.wh_origin.lot_stock_id.id,
            'company_id': self.company.id,
            'state': 'draft',
            'is_blocked': False,
        })

        wizard = self.env['requisition.supply.request.wizard'].with_context(
            active_id=req.id
        ).create({
            'supply_request_id': sr.id,
        })
        action = wizard.action_confirm()

        # Debe retornar notificación client action, no abrir formulario de SR
        self.assertEqual(action.get('type'), 'ir.actions.client')
        self.assertEqual(action.get('tag'), 'display_notification')
        self.assertEqual(action.get('params', {}).get('type'), 'success')

        # La línea debió agregarse a la SR seleccionada
        sr_lines = sr.line_ids.filtered(lambda l: l.requisition_line_id == req.line_ids[0])
        self.assertTrue(sr_lines)
        self.assertEqual(sr_lines[0].product_qty, 10.0)
        self.assertFalse(sr_lines[0].is_in_purchase)

        # Si se vincula a una solicitud de compra, se activa is_in_purchase
        pr_line = self.env['requisition.purchase.request.line'].create({
            'requisition_id': req.id,
            'requisition_line_id': req.line_ids[0].id,
            'product_id': product.id,
            'product_qty': 10.0,
            'state': 'pending',
        })
        sr_lines[0].allocation_ids[0].write({'purchase_request_line_id': pr_line.id})
        sr_lines[0].invalidate_recordset()
        self.assertTrue(sr_lines[0].is_in_purchase)
        self.assertEqual(sr_lines[0].purchase_process_label, "En proceso de compra")
        self.assertEqual(sr_lines[0].purchase_request_line_state, 'pending')

