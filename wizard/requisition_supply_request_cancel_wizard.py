# -*- coding: utf-8 -*-
from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class RequisitionSupplyRequestCancelWizard(models.TransientModel):
    """
    Asistente para cancelar una Solicitud de Suministro y sus albaranes asociados
    registrando obligatoriamente el motivo en la bitácora de auditoría (Chatter).
    """
    _name = 'requisition.supply.request.cancel.wizard'
    _description = 'Asistente para Cancelar Solicitud de Suministro'

    request_id = fields.Many2one(
        'requisition.supply.request',
        string='Solicitud a Cancelar',
        required=True,
        default=lambda self: self.env.context.get('active_id') or self.env.context.get('default_request_id')
    )
    cancellation_reason = fields.Text(
        string='Motivo de Cancelación',
        required=True,
        help="Justifique las razones por las cuales se cancela la solicitud y sus operaciones logísticas."
    )
    cancel_stock_moves = fields.Boolean(
        string='Cancelar transferencias y movimientos de almacén vinculados',
        default=True,
        help="Si está activado, cancelará los albaranes y movimientos que aún no hayan sido completados."
    )

    def action_confirm_cancel(self):
        self.ensure_one()
        request = self.request_id
        if not request:
            raise UserError(_("No se ha seleccionado una solicitud de suministro válida."))

        if request.state in ('done_exact', 'done_adjusted', 'cancel'):
            raise UserError(_("No es posible cancelar una solicitud que ya está completada o cancelada."))

        reason = self.cancellation_reason.strip()
        if not reason:
            raise UserError(_("Debe proporcionar un motivo de cancelación."))

        # 1. Cancelar transferencias de almacén asociadas si se solicitó
        related_pickings = request.picking_ids.filtered(
            lambda p: p.state in ('draft', 'waiting', 'confirmed', 'assigned')
        )
        if related_pickings and not self.cancel_stock_moves:
            raise UserError(_(
                "Existen transferencias de almacén pendientes asociadas a esta solicitud. "
                "Debe marcar la opción para cancelarlas y evitar salidas de stock inconsistentes."
            ))

        cancelled_pickings = []
        if self.cancel_stock_moves:
            for picking in related_pickings:
                picking.with_context(mail_notrack=True).action_cancel()
                picking_body = Markup(
                    "<strong>Albarán cancelado desde Solicitud de Suministro %s</strong><br/>"
                    "<strong>Motivo:</strong> %s"
                ) % (escape(request.name), escape(reason))
                picking.message_post(body=picking_body, subtype_xmlid="mail.mt_note")
                cancelled_pickings.append(picking.name)

        # 2. Cancelar asignaciones en supply.chain.allocation
        allocations = self.env['supply.chain.allocation'].search([
            ('supply_request_line_id', 'in', request.line_ids.ids),
            ('state', 'not in', ('done', 'cancel')),
        ])
        if allocations:
            allocations.action_cancel_allocation(
                cancel_action='reintegrate',
                reason=_("Cancelación de Solicitud de Suministro %s: %s") % (request.name, reason)
            )

        # 3. Cancelar la Solicitud de Suministro
        request.write({
            'state': 'cancel',
            'delivery_alert': 'cancelled',
            'rejected_user_id': self.env.uid,
        })

        # 4. Publicar en Chatter de la Solicitud
        request_body = Markup(
            "<div class='alert alert-danger mb-0'>"
            "<strong>SOLICITUD DE SUMINISTRO CANCELADA</strong><br/>"
            "<strong>Motivo:</strong> %s"
            "</div>"
        ) % escape(reason)

        if cancelled_pickings:
            request_body += Markup(
                "<br/><em>Se cancelaron las siguientes transferencias de inventario:</em> %s"
            ) % escape(", ".join(cancelled_pickings))

        request.message_post(body=request_body, subtype_xmlid="mail.mt_note")

        # 5. Limpiar actividades pendientes
        request.activity_unlink(['mail.mail_activity_data_todo'])

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Solicitud Cancelada'),
                'message': _('La solicitud de suministro %s ha sido cancelada satisfactoriamente.') % request.name,
                'type': 'info',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }
