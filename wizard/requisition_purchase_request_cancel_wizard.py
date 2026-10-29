# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class RequisitionPurchaseRequestCancelWizard(models.TransientModel):
    """
    Asistente interactivo para solicitar el motivo obligatorio de cancelación
    de una o más partidas de Solicitud de Compra, aplicar auditoría y coordinar
    la cascada con órdenes de compra / RFQs vinculadas.
    """
    _name = 'requisition.purchase.request.cancel.wizard'
    _description = 'Asistente para Cancelar Partidas de Solicitud de Compra'

    purchase_request_line_ids = fields.Many2many(
        'requisition.purchase.request.line',
        'req_pr_cancel_wiz_rel',
        'wizard_id',
        'line_id',
        string="Partidas de Compra a Cancelar",
        required=True
    )
    reason = fields.Text(
        string="Motivo de Cancelación",
        required=True,
        help="Indique detalladamente la razón por la cual se cancela la partida de compra."
    )
    cancel_action = fields.Selection([
        ('reintegrate', 'Reintegrar cantidad pendiente a la requisición'),
        ('close', 'Cerrar demanda definitivamente (no reintegrar)'),
    ], string="Tratamiento de Demanda", default='reintegrate', required=True)

    def action_confirm(self):
        """Ejecuta la cancelación de las partidas de compra seleccionadas con el motivo proporcionado."""
        self.ensure_one()
        reason_text = (self.reason or '').strip()
        if not reason_text:
            raise UserError(_("Debe proporcionar un motivo obligatorio para la cancelación."))

        if not self.purchase_request_line_ids:
            raise UserError(_("No hay partidas de compra seleccionadas para cancelar."))

        cancelled_count = 0
        errors = []
        for line in self.purchase_request_line_ids:
            try:
                line.action_cancel_line(cancel_action=self.cancel_action, reason=reason_text)
                cancelled_count += 1
            except UserError as e:
                errors.append(f"{line.display_name}: {str(e)}")

        if errors:
            err_text = "\n".join(errors)
            if cancelled_count == 0:
                raise UserError(_("No se pudo cancelar ninguna partida:\n\n%s") % err_text)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Cancelación Parcial"),
                    'message': _("Se cancelaron %d partidas. Ocurrieron advertencias:\n%s") % (cancelled_count, err_text),
                    'type': 'warning',
                    'sticky': True,
                    'next': {'type': 'ir.actions.act_window_close'},
                }
            }

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Cancelación Exitosa"),
                'message': _("Se cancelaron correctamente %d partida(s) de compra.") % cancelled_count,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }
