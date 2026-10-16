# -*- coding: utf-8 -*-
from odoo import fields, models, _
from odoo.exceptions import UserError
from markupsafe import Markup, escape


class RequisitionRejectWizard(models.TransientModel):
    """
    Asistente interactivo para solicitar el motivo de rechazo de una requisición
    y registrarlo exclusivamente en el chatter.
    """
    _name = 'requisition.reject.wizard'
    _description = 'Asistente de Rechazo de Requisición'

    requisition_id = fields.Many2one(
        'requisitions',
        string="Requisición",
        required=True,
        readonly=True
    )
    reason = fields.Text(
        string="Motivo del Rechazo",
        required=True,
        help="Indique la justificación por la cual se rechaza la requisición."
    )

    def action_confirm_reject(self):
        """Rechaza la requisición y publica el motivo en el chatter."""
        self.ensure_one()
        req = self.requisition_id
        if req.state != 'to_approve':
            raise UserError(_("Solo se pueden rechazar requisiciones en estado 'Por Aprobar'."))

        req.write({
            'state': 'rejected',
            'rejected_by_id': self.env.user.id,
            'date_rejected': fields.Datetime.now(),
        })

        # Cancelar asignaciones activas con motivo de rechazo
        active_allocs = req.line_ids.allocation_ids.filtered(lambda a: a.state != 'cancel')
        if active_allocs:
            active_allocs.action_cancel_allocation(cancel_action='close', reason=self.reason)

        # Registrar el motivo exclusivamente en el chatter (chatter message)
        req.message_post(
            body=Markup(
                "<strong>%s</strong><br/>"
                "<strong>%s:</strong> %s<br/>"
                "<strong>%s:</strong> %s"
            ) % (
                _("Requisición Rechazada"),
                _("Rechazado por"),
                escape(self.env.user.name),
                _("Motivo"),
                escape(self.reason),
            ),
            subtype_xmlid='mail.mt_comment'
        )
        return {'type': 'ir.actions.act_window_close'}
