# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from markupsafe import Markup, escape


class RequisitionSupplyRequestAddPendingWizard(models.TransientModel):
    """
    Asistente interactivo en Solicitud de Suministro para consultar y seleccionar
    partidas del pool de 'Pendientes de Envío' destinadas a la misma obra e incorporarlas.
    """
    _name = 'requisition.supply.request.add.pending.wizard'
    _description = 'Asistente para Agregar Pendientes de Envío a Solicitud'

    supply_request_id = fields.Many2one(
        'requisition.supply.request',
        string="Solicitud de Suministro",
        required=True,
        readonly=True
    )
    warehouse_dest_id = fields.Many2one(
        'stock.warehouse',
        related='supply_request_id.warehouse_dest_id',
        string="Almacén Destino (Obra)",
        readonly=True
    )
    line_ids = fields.One2many(
        'requisition.supply.request.add.pending.wizard.line',
        'wizard_id',
        string="Partidas Pendientes Disponibles"
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        if not active_id:
            return res

        sr = self.env['requisition.supply.request'].browse(active_id)
        if not sr.exists():
            return res

        res['supply_request_id'] = sr.id

        # Buscar partidas en el pool de pendientes destinadas a este mismo almacén de obra
        pending_lines = self.env['requisition.supply.request.line'].search([
            ('request_id', '=', False),
            ('warehouse_dest_id', '=', sr.warehouse_dest_id.id),
            ('state', 'not in', ('cancel', 'done')),
        ])

        lines_data = []
        for line in pending_lines:
            lines_data.append((0, 0, {
                'selected': True,
                'supply_line_id': line.id,
            }))
        res['line_ids'] = lines_data
        return res

    def action_confirm(self):
        self.ensure_one()
        selected_wlines = self.line_ids.filtered('selected')
        if not selected_wlines:
            raise UserError(_("Debe seleccionar al menos una partida para incorporar a la solicitud de suministro."))

        lines_to_assign = selected_wlines.mapped('supply_line_id')
        for line in lines_to_assign:
            line.write({
                'request_id': self.supply_request_id.id,
            })
            for alloc in line.allocation_ids.filtered(lambda a: a.state != 'cancel'):
                alloc._sync_origin_document_status()

        self.supply_request_id.message_post(
            body=Markup(_(
                "Se incorporaron <strong>%d</strong> partida(s) desde el pool de <strong>Pendientes de Envío</strong>."
            )) % len(lines_to_assign),
            subtype_xmlid="mail.mt_note"
        )
        return {'type': 'ir.actions.act_window_close'}


class RequisitionSupplyRequestAddPendingWizardLine(models.TransientModel):
    _name = 'requisition.supply.request.add.pending.wizard.line'
    _description = 'Línea de Partida Pendiente a Incorporar'

    wizard_id = fields.Many2one(
        'requisition.supply.request.add.pending.wizard',
        string="Asistente",
        required=True,
        ondelete='cascade'
    )
    supply_line_id = fields.Many2one(
        'requisition.supply.request.line',
        string="Partida Pendiente",
        required=True
    )
    selected = fields.Boolean(
        string="Seleccionar",
        default=True
    )
    product_id = fields.Many2one(
        'product.product',
        related='supply_line_id.product_id',
        string="Producto",
        readonly=True
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        related='supply_line_id.product_uom_id',
        string="UdM",
        readonly=True
    )
    product_qty = fields.Float(
        related='supply_line_id.product_qty',
        string="Cantidad",
        readonly=True
    )
    requester_name = fields.Char(
        related='supply_line_id.requester_name',
        string="Solicitante",
        readonly=True
    )
    requisition_id = fields.Many2one(
        'requisitions',
        related='supply_line_id.requisition_id',
        string="Requisición",
        readonly=True
    )
    notes = fields.Char(
        related='supply_line_id.notes',
        string="Notas",
        readonly=True
    )
