# -*- coding: utf-8 -*-
from odoo import fields, models, _
from odoo.exceptions import UserError
from markupsafe import Markup, escape


class RequisitionCancelWizard(models.TransientModel):
    """
    Asistente interactivo para solicitar el motivo de cancelación de una requisición
    aprobada o en proceso y registrarlo en el chatter cerrando las asignaciones.
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
        """Cancela la requisición, anula las asignaciones activas y registra el motivo en el chatter."""
        self.ensure_one()
        req = self.requisition_id
        if req.state in ('cancel', 'rejected', 'done'):
            raise UserError(_("No se puede cancelar una requisición en estado %s.") % req.state)

        # Cancelar únicamente asignaciones en proceso o borrador; las ya completadas ('done')
        # permanecen intactas para preservar el registro histórico de las entregas físicas.
        active_allocs = req.line_ids.allocation_ids.filtered(lambda a: a.state in ('draft', 'in_progress'))
        if active_allocs:
            active_allocs.action_cancel_allocation(
                cancel_action='close',
                reason=self.reason
            )

        req.write({
            'state': 'cancel',
            'cancelled_by_id': self.env.user.id,
            'date_cancelled': fields.Datetime.now(),
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
                escape(self.reason),
            ),
            subtype_xmlid='mail.mt_comment'
        )
        return {'type': 'ir.actions.act_window_close'}
