# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from markupsafe import Markup, escape


class RequisitionAllocation(models.Model):
    """
    Asignación de Suministro de Requisición.
    Modelo soberano en dh_requisitions para el rastreo y conciliación
    entre la demanda originada en requisiciones y los documentos de
    cumplimiento físico (albaranes) y financiero (órdenes de compra).
    """
    _name = 'requisition.allocation'
    _description = 'Asignación de Suministro de Requisición'
    _order = 'id desc'

    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        required=True,
        default=lambda self: self.env.company,
        index=True
    )

    # --- Documentos Origen de la Demanda ---
    requisition_line_id = fields.Many2one(
        'requisitions.line',
        string='Línea de Requisición',
        required=False,
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
    supply_request_id = fields.Many2one(
        'requisition.supply.request',
        string='Solicitud de Suministro',
        related='supply_request_line_id.request_id',
        store=True,
        readonly=True,
        index=True,
        help='Folio de la Solicitud de Suministro a la que pertenece la partida asignada.'
    )

    # --- Documentos de Cumplimiento / Destino ---
    stock_move_id = fields.Many2one(
        'stock.move',
        string='Movimiento de Salida/Entrega',
        ondelete='set null',
        index=True,
        check_company=True
    )
    purchase_line_id = fields.Many2one(
        'purchase.order.line',
        string='Línea de Orden de Compra',
        ondelete='set null',
        index=True,
        check_company=True
    )
    purchase_stock_move_id = fields.Many2one(
        'stock.move',
        string='Movimiento de Recepción de Compra',
        ondelete='set null',
        index=True,
        check_company=True
    )

    # --- Producto y Cantidades ---
    product_id = fields.Many2one(
        'product.product',
        string='Producto',
        required=True,
        index=True,
        check_company=True
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='Unidad de Medida',
        required=True
    )
    requested_qty = fields.Float(
        string='Cantidad Asignada',
        required=True,
        digits='Product Unit of Measure'
    )
    delivered_qty = fields.Float(
        string='Cantidad Entregada / Recibida',
        default=0.0,
        digits='Product Unit of Measure'
    )
    qty_returned = fields.Float(
        string='Cantidad Devuelta',
        default=0.0,
        digits='Product Unit of Measure',
        help='Cantidad que fue devuelta a almacén tras un despacho o compra.'
    )
    open_qty = fields.Float(
        string='Cantidad en Tránsito / Abierta',
        compute='_compute_open_qty',
        store=True,
        digits='Product Unit of Measure',
        help='Cantidad activa en proceso pendiente de surtir o recibir en esta asignación.'
    )
    cancelled_qty = fields.Float(
        string='Cantidad Cancelada Definitiva',
        default=0.0,
        digits='Product Unit of Measure',
        help='Cantidad que no se surtirá y que queda cerrada definitivamente en la requisición.'
    )

    # --- Filiación y Etapas de la Cadena de Suministro ---
    stage = fields.Selection(
        [
            ('inbound', 'Abastecimiento (Compra / Traslado)'),
            ('outbound', 'Despacho al Solicitante'),
        ],
        string='Etapa de Asignación',
        default='outbound',
        required=True,
        index=True,
        help='Distingue si la asignación corresponde al abastecimiento hacia bodega (compra/traslado) '
             'o al despacho físico final al solicitante en la obra.'
    )
    parent_allocation_id = fields.Many2one(
        'requisition.allocation',
        string='Asignación de Abastecimiento Origen',
        ondelete='set null',
        index=True,
        help='Asignación previa de abastecimiento (compra o traslado) que proveyó el material para este despacho.'
    )
    child_allocation_ids = fields.One2many(
        'requisition.allocation',
        'parent_allocation_id',
        string='Despachos Derivados',
        help='Asignaciones de despacho físico generadas a partir de este abastecimiento.'
    )

    supply_type = fields.Selection(
        [
            ('consumption', 'Consumo Directo'),
            ('resg', 'Resguardo'),
            ('custody', 'Préstamo / Resguardo'),
        ],
        string='Tipo de Suministro',
        default='consumption',
        required=True
    )

    state = fields.Selection(
        [
            ('draft', 'Borrador'),
            ('in_progress', 'En Proceso'),
            ('done', 'Completado'),
            ('cancel', 'Cancelado'),
        ],
        string='Estado de Asignación',
        compute='_compute_state',
        store=True,
        readonly=False,
        default='draft',
        index=True
    )

    cancel_action = fields.Selection(
        [
            ('reintegrate', 'Reintegrar a Pendiente (Re-suministrar)'),
            ('close', 'Cerrar Cantidad (Ya no se requerirá más)'),
        ],
        string='Tratamiento de la Cantidad al Cancelar',
        default='reintegrate'
    )
    cancel_reason = fields.Text(
        string='Motivo de Cancelación'
    )

    # -------------------------------------------------------------------------
    # CÓMPUTOS
    # -------------------------------------------------------------------------
    @api.depends('requested_qty', 'delivered_qty', 'cancelled_qty', 'state')
    def _compute_open_qty(self):
        for rec in self:
            if rec.state in ('done', 'cancel'):
                rec.open_qty = 0.0
            else:
                rec.open_qty = max(0.0, rec.requested_qty - rec.delivered_qty - rec.cancelled_qty)

    @api.depends(
        'stock_move_id.state',
        'stock_move_id.location_dest_id.usage',
        'purchase_line_id.order_id.state',
        'delivered_qty',
        'requested_qty',
        'cancelled_qty',
        'stage'
    )
    def _compute_state(self):
        for rec in self:
            is_transit_move = bool(
                rec.stock_move_id and rec.stock_move_id.location_dest_id and
                (rec.stock_move_id.location_dest_id.usage == 'transit' or
                 (rec.stock_move_id.location_dest_id.company_id and
                  rec.stock_move_id.location_dest_id == rec.stock_move_id.location_dest_id.company_id.internal_transit_location_id))
            )
            # En transferencias a tránsito, se conserva en proceso hasta llegar a la obra destino
            if is_transit_move and rec.supply_request_line_id and rec.delivered_qty < rec.requested_qty:
                rec.state = 'in_progress'
            # 1. Si la entrega física ya se completó o cubrió la solicitud
            elif (rec.stock_move_id and rec.stock_move_id.state == 'done') or \
               (rec.requested_qty > 0 and rec.delivered_qty >= (rec.requested_qty - rec.cancelled_qty) and rec.delivered_qty > 0):
                rec.state = 'done'
            # 2. Si fue cancelada explícitamente o sus documentos vinculados están cancelados (y no hubo entregas)
            elif (rec.stock_move_id and rec.stock_move_id.state == 'cancel' and rec.delivered_qty == 0) or \
                 (rec.purchase_line_id and rec.purchase_line_id.order_id.state == 'cancel' and rec.delivered_qty == 0) or \
                 (rec.state == 'cancel' and rec.delivered_qty == 0):
                rec.state = 'cancel'
            # 3. Si el documento destino está confirmado / en proceso
            elif (rec.stock_move_id and rec.stock_move_id.state in ('waiting', 'confirmed', 'assigned')) or \
                 (rec.purchase_line_id and rec.purchase_line_id.order_id.state in ('draft', 'sent', 'to approve', 'purchase')):
                rec.state = 'in_progress'
            elif rec.state:
                pass
            else:
                rec.state = 'draft'

    # -------------------------------------------------------------------------
    # ACCIONES DE NEGOCIO Y CANCELACIÓN
    # -------------------------------------------------------------------------
    def action_cancel_allocation(self, cancel_action='reintegrate', reason=''):
        """
        Cancela o ajusta las asignaciones seleccionadas aplicando la política elegida:
        - Si ya está 'done' y delivered_qty >= requested_qty, se omite.
        - Si delivered_qty > 0 (entrega parcial):
          * 'close': la cantidad no entregada pasa a cancelled_qty y la asignación concluye en 'done'.
          * 'reintegrate': la cantidad no entregada se devuelve a la requisición y requested_qty = delivered_qty ('done').
        - Si delivered_qty == 0:
          * 'close': cancelled_qty = requested_qty, state = 'cancel'.
          * 'reintegrate': cancelled_qty = 0, state = 'cancel'.
        """
        notified_records = self.env['requisition.allocation']
        for rec in self:
            if rec.state == 'done' and rec.delivered_qty >= rec.requested_qty:
                continue

            unfulfilled = max(0.0, rec.requested_qty - rec.delivered_qty)
            vals = {
                'cancel_action': cancel_action,
                'cancel_reason': reason,
            }

            if rec.delivered_qty > 0:
                if cancel_action == 'close':
                    vals['cancelled_qty'] = unfulfilled
                else:
                    vals['cancelled_qty'] = 0.0
                    vals['requested_qty'] = rec.delivered_qty
                vals['state'] = 'done'
            else:
                vals['state'] = 'cancel'
                vals['cancelled_qty'] = unfulfilled if cancel_action == 'close' else 0.0

            rec.write(vals)
            notified_records |= rec

        if notified_records:
            notified_records._notify_allocation_cancellation(cancel_action, reason)
            notified_records._sync_origin_document_status()

    def _notify_allocation_cancellation(self, cancel_action, reason):
        """Notifica en el chatter de la requisición vinculada sobre la cancelación y el motivo."""
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
                    _("Tratamiento de la Cantidad"),
                    escape(action_label),
                    _("Motivo"),
                    escape(motivo_text)
                )

                line = rec.requisition_line_id
                if line and cancel_action == 'close':
                    uom_str = escape(line.product_uom_id.name or '')
                    body += Markup(
                        "<br/><strong>%s:</strong> %s"
                    ) % (
                        _("Ajuste de Partida"),
                        _("Se canceló la cantidad pendiente. Requerido original: %s %s, efectivamente suministrado: %s %s, cancelado: %s %s.") % (
                            line.product_qty, uom_str,
                            line.qty_done, uom_str,
                            line.qty_cancelled, uom_str
                        )
                    )

                rec.requisition_id.message_post(body=body, subtype_xmlid="mail.mt_note")
                if hasattr(rec.requisition_id, '_update_state_from_lines'):
                    rec.requisition_id._update_state_from_lines()

    def _sync_origin_document_status(self):
        """Sincroniza el estado y cantidades de las requisiciones vinculadas."""
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

    def _on_move_delivered(self, move, qty):
        """
        Extiende la entrega para refrescar estados en la requisición.
        En transferencias con ubicación de tránsito (doble tramo Base -> Obra),
        el despacho inicial se conserva en tránsito ('in_progress') y la entrega
        definitiva se consolida al recibirse físicamente en la obra destino.
        """
        for rec in self:
            alloc_qty = qty
            if move.product_uom and rec.product_uom_id and move.product_uom != rec.product_uom_id:
                alloc_qty = move.product_uom._compute_quantity(qty, rec.product_uom_id)

            is_transit = move.location_dest_id and (
                move.location_dest_id.usage == 'transit' or
                (move.location_dest_id.company_id and move.location_dest_id == move.location_dest_id.company_id.internal_transit_location_id)
            )
            if rec.supply_request_line_id and is_transit:
                rec.state = 'in_progress'
                continue
            rec.delivered_qty = min(alloc_qty, rec.requested_qty)
        self._sync_origin_document_status()

    def _on_move_returned(self, move, qty):
        """Extiende la devolución para notificar en chatter y actualizar resguardo si aplica."""
        for rec in self:
            alloc_qty = qty
            if move.product_uom and rec.product_uom_id and move.product_uom != rec.product_uom_id:
                alloc_qty = move.product_uom._compute_quantity(qty, rec.product_uom_id)

            rec.qty_returned += alloc_qty
            if rec.supply_type == 'consumption':
                rec.delivered_qty = max(0.0, rec.delivered_qty - alloc_qty)
                if rec.delivered_qty < rec.requested_qty:
                    rec.state = 'in_progress'
                if rec.requisition_id:
                    rec.requisition_id.message_post(
                        body=Markup(_(
                            "<strong>Devolución de Material a Bodega:</strong> Se retornaron %s %s del producto '%s'. "
                            "Cantidad pendiente restituida en la requisición."
                        )) % (alloc_qty, escape(rec.product_uom_id.name or ''), escape(rec.product_id.display_name)),
                        subtype_xmlid="mail.mt_note"
                    )
            elif rec.supply_type in ('custody', 'resg'):
                if rec.requisition_id:
                    rec.requisition_id.message_post(
                        body=Markup(_(
                            "<strong>Retorno de Resguardo / Préstamo:</strong> Se devolvieron %s %s del bien '%s' a almacén."
                        )) % (alloc_qty, escape(rec.product_uom_id.name or ''), escape(rec.product_id.display_name)),
                        subtype_xmlid="mail.mt_note"
                    )
        self._sync_origin_document_status()

    def _on_purchase_returned(self, move, qty):
        """
        Extiende la devolución de mercancía a proveedor (stock.return.picking de compra)
        para reducir delivered_qty, reabrir asignación a 'in_progress' y notificar en chatter.
        """
        for rec in self:
            alloc_qty = qty
            if move.product_uom and rec.product_uom_id and move.product_uom != rec.product_uom_id:
                alloc_qty = move.product_uom._compute_quantity(qty, rec.product_uom_id)

            rec.delivered_qty = max(0.0, rec.delivered_qty - alloc_qty)
            if rec.delivered_qty < rec.requested_qty:
                rec.state = 'in_progress'

            if rec.requisition_id:
                rec.requisition_id.message_post(
                    body=Markup(_(
                        "<strong>Devolución de Compra a Proveedor:</strong> Se retornaron %s %s del producto '%s' "
                        "(Albarán: %s). Total restante en bodega: %s / %s."
                    )) % (
                        alloc_qty,
                        escape(rec.product_uom_id.name or ''),
                        escape(rec.product_id.display_name),
                        escape(move.reference or move.name or ''),
                        rec.delivered_qty,
                        rec.requested_qty
                    ),
                    subtype_xmlid="mail.mt_note"
                )
        self._sync_origin_document_status()

    def _on_purchase_received(self, move, qty):
        """Extiende la recepción de compra para notificar en el chatter de la requisición y acumular recepciones parciales."""
        for rec in self:
            if not rec.purchase_stock_move_id or rec.purchase_stock_move_id.state == 'done':
                rec.purchase_stock_move_id = move.id

            alloc_qty = qty
            if move.product_uom and rec.product_uom_id and move.product_uom != rec.product_uom_id:
                alloc_qty = move.product_uom._compute_quantity(qty, rec.product_uom_id)

            if rec.purchase_line_id:
                sibling_allocs = rec.purchase_line_id.allocation_ids.filtered(lambda a: a.state != 'cancel')
                if len(sibling_allocs) <= 1:
                    received = rec.purchase_line_id.qty_received
                    uom_po = rec.purchase_line_id.product_uom_id or getattr(rec.purchase_line_id, 'product_uom', False)
                    if uom_po and uom_po != rec.product_uom_id:
                        received = uom_po._compute_quantity(received, rec.product_uom_id)
                    rec.delivered_qty = min(received, rec.requested_qty)
                else:
                    new_delivered = rec.delivered_qty + alloc_qty
                    rec.delivered_qty = min(new_delivered, rec.requested_qty)
            else:
                new_delivered = rec.delivered_qty + alloc_qty
                rec.delivered_qty = min(new_delivered, rec.requested_qty)

            if rec.stage == 'inbound' and rec.delivered_qty >= rec.requested_qty:
                rec.state = 'done'

            if rec.requisition_id:
                rec.requisition_id.message_post(
                    body=Markup(_(
                        "<strong>Recepción de Compra en Bodega:</strong> Se recibieron %s %s del producto '%s' "
                        "(Orden de Compra: %s, Albarán: %s). Total acumulado en bodega: %s / %s. "
                        "Material disponible para despacho al solicitante."
                    )) % (
                        alloc_qty,
                        escape(rec.product_uom_id.name or ''),
                        escape(rec.product_id.display_name),
                        escape(rec.purchase_line_id.order_id.name or '') if rec.purchase_line_id else '',
                        escape(move.reference or move.name or ''),
                        rec.delivered_qty,
                        rec.requested_qty
                    ),
                    subtype_xmlid="mail.mt_note"
                )
        self._sync_origin_document_status()

    def action_open_cancel_wizard(self):
        """Abre el asistente interactivo para cancelar asignaciones seleccionadas definiendo su tratamiento de cantidad."""
        return {
            'name': _('Cancelar Asignación de Suministro'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.allocation.cancel.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_allocation_ids': [(6, 0, self.ids)],
            },
        }

    def _get_split_extra_vals(self, new_move, split_qty):
        """Propaga los campos de requisición al dividirse la asignación por backorders."""
        vals = {
            'requisition_line_id': self.requisition_line_id.id if self.requisition_line_id else False,
            'purchase_request_line_id': self.purchase_request_line_id.id if self.purchase_request_line_id else False,
            'supply_request_line_id': self.supply_request_line_id.id if self.supply_request_line_id else False,
            'stage': self.stage,
            'parent_allocation_id': self.parent_allocation_id.id if self.parent_allocation_id else False,
        }
        return vals

    @api.ondelete(at_uninstall=False)
    def _unlink_except_in_progress_or_done(self):
        for rec in self:
            if rec.state in ('in_progress', 'done'):
                raise UserError(_(
                    "No se pueden eliminar asignaciones de suministro en proceso o completadas. "
                    "Cancele la asignación primero si desea reintegrar o cerrar la cantidad."
                ))

    def unlink(self):
        """Al eliminarse asignaciones, sincroniza el estado de los documentos de origen."""
        req_lines = self.mapped('requisition_line_id')
        res = super().unlink()
        if req_lines:
            req_lines._compute_quantities()
            for req in req_lines.mapped('requisition_id'):
                if hasattr(req, '_update_state_from_lines'):
                    req._update_state_from_lines()
        return res
