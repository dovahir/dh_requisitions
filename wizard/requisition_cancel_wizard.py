# -*- coding: utf-8 -*-
from odoo import fields, models, _
from odoo.exceptions import UserError
from markupsafe import Markup, escape


class RequisitionCancelWizard(models.TransientModel):
    """
    Asistente interactivo para solicitar el motivo de cancelación de una requisición
    en cualquier estado activo y registrarlo en el chatter ejecutando la cascada integral:
    albaranes de almacén, solicitudes de suministro y sus partidas, solicitudes de compra,
    órdenes de compra / RFQ y libro mayor de asignaciones.
    """
    _name = 'requisition.cancel.wizard'
    _description = 'Asistente de Cancelación de Requisición'

    requisition_id = fields.Many2one(
        'requisitions',
        string="Requisición",
        required=True,
        readonly=True
    )
    reason = fields.Text(
        string="Motivo de Cancelación",
        required=True,
        help="Indique la justificación detallada por la cual se cancela la requisición."
    )

    def action_confirm_cancel(self):
        """Cancela la requisición con justificación obligatoria y ejecuta la cascada sobre todos los documentos vinculados."""
        self.ensure_one()
        req = self.requisition_id

        if not (self.env.user.has_group('dh_requisitions.group_requisitions_approver') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager') or
                self.env.is_admin()):
            raise UserError(_("Solo los Aprobadores / Líderes o Administradores de Requisiciones pueden cancelar requisiciones."))

        if req.state in ('cancel', 'rejected', 'done'):
            raise UserError(_("No se puede cancelar una requisición en estado %s.") % req.state)

        reason_text = (self.reason or '').strip()
        if not reason_text:
            raise UserError(_("Debe proporcionar un motivo de justificación para la cancelación."))

        # 1. Cancelar albaranes de almacén abiertos vinculados y sus movimientos
        open_pickings = req.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))
        supply_pickings = (
            req.line_ids.allocation_ids.mapped('supply_request_line_id.request_id.picking_ids') |
            self.env['requisition.supply.request.line'].sudo().search([
                ('requisition_line_id', 'in', req.line_ids.ids)
            ]).mapped('request_id.picking_ids')
        ).filtered(lambda p: p.state not in ('done', 'cancel'))
        move_pickings = self.env['stock.move'].sudo().search([
            ('requisition_line_id', 'in', req.line_ids.ids)
        ]).mapped('picking_id').filtered(lambda p: p.state not in ('done', 'cancel'))
        all_pickings = open_pickings | supply_pickings | move_pickings
        # 1. Cancelar albaranes de almacén abiertos vinculados y propagar auditoría
        audit_vals = {
            'cancelled_by_id': self.env.user.id,
            'date_cancelled': fields.Datetime.now(),
            'cancel_reason': reason_text,
        }
        for picking in all_pickings.sudo():
            picking.with_context(
                mail_notrack=True,
                cancel_reason=reason_text,
                cancelled_by_id=self.env.user.id
            ).action_cancel()
            if 'cancel_reason' in picking._fields:
                picking.write(audit_vals)
            picking.message_post(
                body=Markup(_("<strong>Albarán cancelado por cancelación de la Requisición %s</strong><br/><strong>Motivo:</strong> %s")) % (
                    escape(req.name),
                    escape(reason_text)
                ),
                subtype_xmlid="mail.mt_note"
            )

        # Cancelar movimientos de stock independientes en asignaciones que no estén concluidos
        linked_moves = (
            req.line_ids.allocation_ids.mapped('stock_move_id') |
            req.line_ids.allocation_ids.mapped('purchase_stock_move_id') |
            self.env['stock.move'].sudo().search([('requisition_line_id', 'in', req.line_ids.ids)])
        ).filtered(lambda m: m.state not in ('done', 'cancel'))
        if linked_moves:
            linked_moves.sudo()._action_cancel()

        # 2. Cancelar partidas de Solicitud de Suministro vinculadas y actualizar la Solicitud de Suministro
        supply_lines = (
            self.env['requisition.supply.request.line'].sudo().search([('requisition_line_id', 'in', req.line_ids.ids)]) |
            req.line_ids.allocation_ids.mapped('supply_request_line_id')
        )
        affected_supply_requests = supply_lines.mapped('request_id')

        # Si una Solicitud de Suministro ya no tiene líneas activas ajenas a esta requisición, cancelarla completamente
        for sr in affected_supply_requests:
            other_active_lines = sr.line_ids.filtered(
                lambda l: l.id not in supply_lines.ids and l.state not in ('done', 'cancel')
            )
            if not other_active_lines:
                sr.sudo().write({
                    'state': 'cancel',
                    'delivery_alert': 'cancelled',
                    'rejected_user_id': self.env.uid,
                })
                sr.message_post(
                    body=Markup(_("<strong>Solicitud de Suministro cancelada en su totalidad debido a la cancelación de la Requisición %s</strong><br/><strong>Motivo:</strong> %s")) % (
                        escape(req.name),
                        escape(reason_text)
                    ),
                    subtype_xmlid="mail.mt_comment"
                )

        for s_line in supply_lines:
            s_line.sudo().write({'state': 'cancel'})
            if s_line.request_id and s_line.request_id.state != 'cancel':
                s_line.request_id.message_post(
                    body=Markup(_("Partida <strong>%s</strong> cancelada por cancelación de la Requisición %s.<br/><strong>Motivo:</strong> %s")) % (
                        escape(s_line.product_id.display_name),
                        escape(req.name),
                        escape(reason_text)
                    ),
                    subtype_xmlid="mail.mt_note"
                )

        # 3. Cancelar partidas de Solicitud de Compra pendientes vinculadas
        pr_lines = (
            req.purchase_request_line_ids |
            req.line_ids.allocation_ids.mapped('purchase_request_line_id')
        ).filtered(lambda l: l.state not in ('cancel', 'purchased'))
        for pr_line in pr_lines:
            try:
                pr_line.sudo().action_cancel_line(cancel_action='close', reason=reason_text)
            except Exception:
                pr_line.sudo().write({'state': 'cancel'})

        # 4. Afectar Órdenes de Compra / RFQ vinculadas
        purchase_orders = (
            req.sudo().purchase_order_ids |
            req.line_ids.allocation_ids.mapped('purchase_line_id.order_id')
        )
        for po in purchase_orders.sudo():
            if po.state in ('draft', 'sent', 'to approve'):
                # Identificar líneas vinculadas a esta requisición
                lines_to_cancel = po.order_line.filtered(
                    lambda pol: (
                        pol.allocation_ids.filtered(lambda a: a.requisition_id == req or a.requisition_line_id.requisition_id == req) or
                        pol.purchase_request_lines.filtered(lambda prl: prl.requisition_id == req)
                    )
                )
                other_active_lines = po.order_line - lines_to_cancel
                if not other_active_lines:
                    # Toda la orden pertenecía a esta requisición: cancelar orden completa con auditoría
                    po.sudo().with_context(
                        cancel_reason=reason_text,
                        cancelled_by_id=self.env.user.id
                    ).button_cancel()
                    if 'cancel_reason' in po._fields:
                        po.sudo().write(audit_vals)
                    po.message_post(
                        body=Markup(_("<strong>Orden de Compra / RFQ cancelada por cancelación de la Requisición %s</strong><br/><strong>Motivo:</strong> %s")) % (
                            escape(req.name),
                            escape(reason_text)
                        ),
                        subtype_xmlid="mail.mt_comment"
                    )
                elif lines_to_cancel:
                    # Cancelar o retirar las líneas asociadas a esta requisición
                    lines_to_cancel.sudo().unlink()
                    po.message_post(
                        body=Markup(_("<strong>Partidas retiradas de la orden por cancelación de la Requisición %s</strong><br/><strong>Motivo:</strong> %s")) % (
                            escape(req.name),
                            escape(reason_text)
                        ),
                        subtype_xmlid="mail.mt_note"
                    )
            elif po.state in ('purchase', 'done'):
                # Notificar apunte de auditoría en la orden confirmada
                po.message_post(
                    body=Markup(_("<strong>Aviso de Auditoría:</strong> La requisición de origen <strong>%s</strong> fue cancelada por %s.<br/><strong>Motivo:</strong> %s")) % (
                        escape(req.name),
                        escape(self.env.user.name),
                        escape(reason_text)
                    ),
                    subtype_xmlid="mail.mt_comment"
                )

        # 5. Cancelar únicamente asignaciones en proceso o borrador en el ledger (cierre definitivo)
        active_allocs = req.line_ids.allocation_ids.filtered(lambda a: a.state in ('draft', 'in_progress'))
        if active_allocs:
            active_allocs.sudo().action_cancel_allocation(
                cancel_action='close',
                reason=reason_text
            )

        # 6. Actualizar líneas y cabecera de la Requisición
        req.line_ids.sudo().write({'line_state': 'cancel'})
        req.sudo().write({
            'state': 'cancel',
            'cancelled_by_id': self.env.user.id,
            'date_cancelled': fields.Datetime.now(),
            'cancel_reason': reason_text,
        })

        req.message_post(
            body=Markup(
                "<strong>%s</strong><br/>"
                "<strong>%s:</strong> %s<br/>"
                "<strong>%s:</strong> %s"
            ) % (
                _("Requisición Cancelada"),
                _("Cancelado por"),
                escape(self.env.user.name),
                _("Motivo"),
                escape(reason_text),
            ),
            subtype_xmlid='mail.mt_comment'
        )
        return {'type': 'ir.actions.act_window_close'}
