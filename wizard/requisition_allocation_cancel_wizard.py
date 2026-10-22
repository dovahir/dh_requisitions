# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class RequisitionAllocationCancelWizard(models.TransientModel):
    """
    Asistente interactivo para cancelar o ajustar asignaciones del libro mayor
    (requisition.allocation) permitiendo al usuario elegir si reintegrar la cantidad
    a la requisición o cerrarlo definitivamente con motivo obligatorio.
    """
    _name = 'requisition.allocation.cancel.wizard'
    _description = 'Asistente para Cancelar Asignaciones de Suministro'

    allocation_ids = fields.Many2many(
        'requisition.allocation',
        string='Asignaciones a Cancelar',
        required=True
    )
    cancel_action = fields.Selection(
        [
            ('reintegrate', 'Reintegrar a Pendiente (Permite volver a surtir en requisición)'),
            ('close', 'Cerrar Cantidad Definitivamente (La necesidad ya no se surtirá)'),
        ],
        string='Tratamiento de la Cantidad',
        default='reintegrate',
        required=True
    )
    reason = fields.Text(
        string='Motivo de Cancelación / Justificación',
        required=True,
        help="Indique las razones operativas por las cuales se cancela o cierra la asignación."
    )

    def action_confirm_cancel(self):
        self.ensure_one()
        if not self.allocation_ids:
            raise UserError(_("No hay asignaciones seleccionadas para cancelar."))

        reason = (self.reason or '').strip()
        if not reason:
            raise UserError(_("Debe proporcionar un motivo de justificación para la cancelación."))

        # Ejecutar acción de cancelación con la política seleccionada
        self.allocation_ids.action_cancel_allocation(
            cancel_action=self.cancel_action,
            reason=reason
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Asignaciones Canceladas'),
                'message': _('Se procesó la cancelación de %d asignación(es) con política: %s.') % (
                    len(self.allocation_ids),
                    _('Reintegrar') if self.cancel_action == 'reintegrate' else _('Cerrar')
                ),
                'type': 'success',
                'sticky': False,
            }
        }
