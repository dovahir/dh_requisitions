# -*- coding: utf-8 -*-
from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class RequisitionSupplyRequestBlockWizard(models.TransientModel):
    """
    Asistente para realizar corte de almacén en una Solicitud de Suministro.
    Oculta o bloquea la solicitud para que no reciba más líneas desde requisiciones
    y opcionalmente inicializa una nueva solicitud para continuar la recepción de materiales.
    """
    _name = 'requisition.supply.request.block.wizard'
    _description = 'Asistente de Corte de Almacén'

    request_id = fields.Many2one(
        'requisition.supply.request',
        string='Solicitud de Suministro',
        required=True,
        default=lambda self: self.env.context.get('active_id') or self.env.context.get('default_request_id')
    )
    create_new = fields.Boolean(
        string='¿Generar nueva solicitud consecutiva?',
        default=False,
        help="Si se activa, se creará de inmediato una nueva solicitud de suministro con los mismos almacenes y ubicaciones."
    )
    scheduled_date = fields.Datetime(
        string='Fecha de Entrega Programada',
        default=fields.Datetime.now
    )

    def action_confirm(self):
        self.ensure_one()
        request = self.request_id
        if not request:
            raise UserError(_("No se ha seleccionado una solicitud de suministro válida."))

        # 1. Bloquear la solicitud actual
        request.write({'is_blocked': True})

        new_request = False
        new_name = ''
        if self.create_new:
            new_request = self.env['requisition.supply.request'].create({
                'picking_type_id': request.picking_type_id.id,
                'location_id': request.location_id.id,
                'warehouse_origin_id': request.warehouse_origin_id.id if request.warehouse_origin_id else False,
                'picking_type_dest_id': request.picking_type_dest_id.id,
                'location_dest_id': request.location_dest_id.id,
                'warehouse_dest_id': request.warehouse_dest_id.id,
                'company_id': request.company_id.id,
                'scheduled_date': self.scheduled_date or fields.Datetime.now(),
                'state': 'draft',
                'is_blocked': False,
                'notes': _("Generada a partir del corte de la solicitud %s") % request.name,
            })
            new_name = new_request.name

        msg = Markup(_("<strong>Corte de Almacén Realizado:</strong> La solicitud ha sido bloqueada para nuevas partidas."))
        if new_name:
            msg += Markup(_("<br/>Se generó la nueva solicitud <strong>%s</strong>.")) % escape(new_name)
        request.message_post(body=msg, subtype_xmlid="mail.mt_note")

        if new_request:
            return {
                'name': _('Solicitud de Suministro (Nueva)'),
                'type': 'ir.actions.act_window',
                'res_model': 'requisition.supply.request',
                'res_id': new_request.id,
                'view_mode': 'form',
                'target': 'current',
            }

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Corte Realizado'),
                'message': _('La solicitud %s ha sido bloqueada exitosamente.') % request.name,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }
