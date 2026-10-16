# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from markupsafe import Markup, escape


class SupplyChainAllocation(models.Model):
    """
    Extensión satélite de supply.chain.allocation para vincular líneas de requisición
    y gestionar de forma desacoplada las notificaciones de chatter y la sincronización de estados.
    """
    _inherit = 'supply.chain.allocation'

    requisition_line_id = fields.Many2one(
        'requisitions.line',
        string='Línea de Requisición',
        ondelete='cascade',
        index=True
    )
    requisition_id = fields.Many2one(
        'requisitions',
        string='Requisición',
        related='requisition_line_id.requisition_id',
        store=True,
        index=True
    )
    purchase_request_line_id = fields.Many2one(
        'requisition.purchase.request.line',
        string='Línea de Solicitud de Compra',
        ondelete='cascade',
        index=True
    )
    supply_request_line_id = fields.Many2one(
        'requisition.supply.request.line',
        string='Línea de Solicitud de Suministro',
        ondelete='cascade',
        index=True
    )

    def _sync_origin_document_status(self):
        """Sincroniza el estado y cantidades de las requisiciones vinculadas."""
        super()._sync_origin_document_status()
        reqs = self.mapped('requisition_id')
        for req in reqs:
            if hasattr(req, '_update_state_from_lines'):
                req._update_state_from_lines()
            if hasattr(req, 'line_ids'):
                req.line_ids._compute_quantities()
        pr_lines = self.mapped('purchase_request_line_id')
        for pr in pr_lines:
            if hasattr(pr, '_compute_quantities'):
                pr._compute_quantities()
            if hasattr(pr, '_compute_state'):
                pr._compute_state()
        sr_lines = self.mapped('supply_request_line_id')
        for sr in sr_lines:
            if hasattr(sr, '_compute_quantities'):
                sr._compute_quantities()
            if hasattr(sr, '_compute_state'):
                sr._compute_state()

    def _notify_allocation_cancellation(self, cancel_action, reason):
        """Notifica en el chatter de la requisición vinculada sobre la cancelación y el motivo."""
        super()._notify_allocation_cancellation(cancel_action, reason)
        for rec in self:
            if rec.requisition_id:
                doc_name = ''
                if rec.stock_move_id:
                    doc_name = _("Albarán de Salida %s") % (rec.stock_move_id.reference or rec.stock_move_id.name)
                elif rec.purchase_line_id:
                    doc_name = _("Orden de Compra %s") % rec.purchase_line_id.order_id.name

                action_label = _("Reintegrado a pendiente para re-suministro") if cancel_action == 'reintegrate' else _("Cerrado definitivamente")
                motivo_text = reason or _("Sin motivo especificado")

                body = Markup(
                    "<strong>%s:</strong> %s<br/>"
                    "<strong>%s:</strong> %s<br/>"
                    "<strong>%s:</strong> %s %s<br/>"
                    "<strong>%s:</strong> %s<br/>"
                    "<strong>%s:</strong> %s"
                ) % (
                    _("Cancelación de Suministro"),
                    escape(doc_name),
                    _("Producto"),
                    escape(rec.product_id.display_name),
                    _("Cantidad Afectada"),
                    rec.requested_qty,
                    escape(rec.product_uom_id.name or ''),
                    _("Tratamiento del Saldo"),
                    escape(action_label),
                    _("Motivo"),
                    escape(motivo_text)
                )
                rec.requisition_id.message_post(body=body, subtype_xmlid="mail.mt_note")
                if hasattr(rec.requisition_id, '_update_state_from_lines'):
                    rec.requisition_id._update_state_from_lines()

    def _on_move_delivered(self, move, qty):
        """
        Extiende la entrega para refrescar estados en la requisición.
        En transferencias con ubicación de tránsito (doble tramo), el despacho inicial
        se conserva en tránsito ('in_progress') y la entrega definitiva se consolida
        al recibirse físicamente en la obra destino.
        """
        for rec in self:
            is_transit = move.location_dest_id and (
                move.location_dest_id.usage == 'transit' or
                (move.location_dest_id.company_id and move.location_dest_id == move.location_dest_id.company_id.internal_transit_location_id)
            )
            if rec.supply_request_line_id and is_transit:
                rec.state = 'in_progress'
                continue
            rec.delivered_qty = min(qty, rec.requested_qty)
        self._sync_origin_document_status()

    def _on_move_returned(self, move, qty):
        """Extiende la devolución para notificar en chatter y actualizar resguardo si aplica."""
        super()._on_move_returned(move, qty)
        for rec in self:
            if rec.supply_type == 'consumption':
                if rec.requisition_id:
                    rec.requisition_id.message_post(
                        body=Markup(_(
                            "<strong>Devolución de Material a Bodega:</strong> Se retornaron %s %s del producto '%s'. "
                            "Saldo pendiente restituido en la requisición."
                        )) % (move.quantity, escape(move.product_uom.name or ''), escape(move.product_id.display_name)),
                        subtype_xmlid="mail.mt_note"
                    )
            elif rec.supply_type in ('custody', 'resg'):
                if rec.requisition_line_id and hasattr(rec.requisition_line_id, 'custody_state'):
                    rec.requisition_line_id.custody_state = 'returned'
                if rec.requisition_id:
                    rec.requisition_id.message_post(
                        body=Markup(_(
                            "<strong>Retorno de Resguardo / Préstamo:</strong> El bien '%s' (%s %s) "
                            "ha sido devuelto a almacén. Resguardo concluido satisfactoriamente."
                        )) % (escape(move.product_id.display_name), move.quantity, escape(move.product_uom.name or '')),
                        subtype_xmlid="mail.mt_note"
                    )
        self._sync_origin_document_status()

    def _on_purchase_received(self, move, qty):
        """Extiende la recepción de compra para notificar en el chatter de la requisición."""
        super()._on_purchase_received(move, qty)
        for rec in self:
            if rec.requisition_id:
                rec.requisition_id.message_post(
                    body=Markup(_(
                        "<strong>Recepción de Compra en Bodega:</strong> Se recibieron %s %s del producto '%s' "
                        "(Orden de Compra: %s, Albarán: %s). Material disponible para despacho al solicitante."
                    )) % (
                        move.quantity,
                        escape(move.product_uom.name or ''),
                        escape(move.product_id.display_name),
                        escape(move.purchase_line_id.order_id.name or '') if move.purchase_line_id else '',
                        escape(move.reference or move.name or '')
                    ),
                    subtype_xmlid="mail.mt_note"
                )
        self._sync_origin_document_status()

    def _get_split_extra_vals(self, new_move, split_qty):
        """Propaga los campos de requisición al dividirse la asignación por backorders."""
        vals = super()._get_split_extra_vals(new_move, split_qty)
        if self.requisition_line_id:
            vals['requisition_line_id'] = self.requisition_line_id.id
        if self.purchase_request_line_id:
            vals['purchase_request_line_id'] = self.purchase_request_line_id.id
        if self.supply_request_line_id:
            vals['supply_request_line_id'] = self.supply_request_line_id.id
        return vals
