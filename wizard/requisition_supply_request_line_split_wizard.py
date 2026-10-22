# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
from markupsafe import Markup, escape


class RequisitionSupplyRequestLineSplitWizard(models.TransientModel):
    """
    Asistente interactivo para dividir una partida de suministro:
    mantiene una cantidad a enviar en la solicitud actual y genera
    una nueva partida con la cantidad remanente en el pool de Pendientes de Envío.
    """
    _name = 'requisition.supply.request.line.split.wizard'
    _description = 'Asistente para Dividir Partida de Suministro'

    line_id = fields.Many2one(
        'requisition.supply.request.line',
        string="Partida Original",
        required=True,
        readonly=True
    )
    product_id = fields.Many2one(
        'product.product',
        related='line_id.product_id',
        string="Producto",
        readonly=True
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        related='line_id.product_uom_id',
        string="Unidad de Medida",
        readonly=True
    )
    total_qty = fields.Float(
        related='line_id.product_qty',
        string="Cantidad Total Solicitada",
        readonly=True
    )
    qty_to_send = fields.Float(
        string="Cantidad a Enviar en esta Solicitud",
        required=True,
        digits='Product Unit of Measure'
    )
    qty_remaining_pending = fields.Float(
        string="Cantidad Remanente Pendiente de Envío",
        compute='_compute_qty_remaining',
        digits='Product Unit of Measure'
    )

    @api.depends('total_qty', 'qty_to_send')
    def _compute_qty_remaining(self):
        for rec in self:
            rec.qty_remaining_pending = max(0.0, rec.total_qty - rec.qty_to_send)

    def action_confirm_split(self):
        self.ensure_one()
        if self.qty_to_send <= 0:
            raise ValidationError(_("La cantidad a enviar debe ser mayor a cero."))
        if self.qty_to_send >= self.total_qty:
            raise ValidationError(
                _("La cantidad a enviar (%s) debe ser estrictamente menor a la cantidad total (%s).\n"
                  "Si desea enviar todo, conserve la partida tal como está. Si no enviará nada, use la opción 'Dejar Pendiente'.")
                % (self.qty_to_send, self.total_qty)
            )

        orig_line = self.line_id
        if orig_line.request_id and orig_line.request_id.state != 'draft':
            raise UserError(_("Solo se pueden dividir partidas de solicitudes de suministro en estado borrador."))

        pending_qty = self.total_qty - self.qty_to_send

        # 1. Ajustar la línea actual con la cantidad que sí se enviará
        orig_line.product_qty = self.qty_to_send
        for alloc in orig_line.allocation_ids.filtered(lambda a: a.state != 'cancel'):
            alloc.requested_qty = self.qty_to_send
            alloc._sync_origin_document_status()

        # 2. Crear una nueva línea desacoplada en el pool de pendientes de envío
        new_line_vals = {
            'request_id': False,
            'requisition_line_id': orig_line.requisition_line_id.id if orig_line.requisition_line_id else False,
            'product_id': orig_line.product_id.id,
            'product_qty': pending_qty,
            'notes': orig_line.notes,
            'is_manual': orig_line.is_manual,
            'warehouse_dest_id': orig_line.warehouse_dest_id.id if orig_line.warehouse_dest_id else False,
            'warehouse_origin_id': orig_line.warehouse_origin_id.id if orig_line.warehouse_origin_id else False,
        }
        self.env['requisition.supply.request.line'].create(new_line_vals)

        # 3. Notificar en el chatter de la solicitud si está vinculada
        if orig_line.request_id:
            orig_line.request_id.message_post(
                body=Markup(_(
                    "Se dividió la partida de <strong>%s</strong>:<br/>"
                    "• <strong>%s %s</strong> se mantienen programadas en este envío.<br/>"
                    "• <strong>%s %s</strong> se separaron como cantidad <strong>Pendiente de Envío</strong>."
                )) % (
                    escape(orig_line.product_id.display_name),
                    self.qty_to_send,
                    escape(orig_line.product_uom_id.name or ''),
                    pending_qty,
                    escape(orig_line.product_uom_id.name or '')
                ),
                subtype_xmlid="mail.mt_note"
            )

        return {'type': 'ir.actions.act_window_close'}
