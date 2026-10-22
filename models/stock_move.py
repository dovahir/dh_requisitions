# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class StockMove(models.Model):
    """
    Extensión de stock.move para coordinar la trazabilidad y sincronización
    con la matriz de asignación de requisiciones requisition.allocation en Odoo 19.
    """
    _inherit = 'stock.move'

    supply_request_line_id = fields.Many2one(
        'requisition.supply.request.line',
        string='Línea de Solicitud de Suministro',
        index=True,
        copy=False,
        help="Partida de la solicitud de suministro asociada a este movimiento."
    )
    requisition_line_id = fields.Many2one(
        'requisitions.line',
        string='Línea de Requisición (Origen)',
        index=True,
        copy=False,
        help="Línea de requisición origen asociada a este movimiento."
    )
    requisition_id = fields.Many2one(
        'requisitions',
        related='requisition_line_id.requisition_id',
        string='Requisición',
        store=True,
        index=True,
    )

    allocation_ids = fields.One2many(
        'requisition.allocation',
        'stock_move_id',
        string='Asignaciones de Salida / Entrega'
    )
    purchase_allocation_ids = fields.One2many(
        'requisition.allocation',
        'purchase_stock_move_id',
        string='Asignaciones de Recepción de Compra'
    )

    def _prepare_merge_moves_distinct_fields(self):
        """Impide fusionar movimientos que provengan de distintas partidas de solicitud de suministro o requisición."""
        distinct_fields = super()._prepare_merge_moves_distinct_fields()
        distinct_fields.extend(['supply_request_line_id', 'requisition_line_id'])
        return distinct_fields

    def _action_done(self, cancel_backorder=False):
        """
        Gancho de sincronización al completarse transferencias:
        - Actualiza entregas en asignaciones de salida a requisición.
        - Gestiona retornos diferenciando consumo vs resguardo.
        - Registra recepciones de compra en bodega para despachos posteriores.
        """
        moves_done = super()._action_done(cancel_backorder=cancel_backorder)

        for move in moves_done:
            # 1. Movimiento de Salida / Entrega a Solicitante
            if move.allocation_ids:
                for alloc in move.allocation_ids.filtered(lambda a: a.state != 'cancel'):
                    move_qty_alloc = move.quantity
                    if move.product_uom and alloc.product_uom_id and move.product_uom != alloc.product_uom_id:
                        move_qty_alloc = move.product_uom._compute_quantity(move.quantity, alloc.product_uom_id)
                    alloc._on_move_delivered(move, move_qty_alloc)

            # 2. Devoluciones (stock.return.picking)
            if move.origin_returned_move_id:
                orig_move = move.origin_returned_move_id
                # 2A. Devolución de salida/despacho al solicitante o traslado
                if orig_move.allocation_ids:
                    for orig_alloc in orig_move.allocation_ids.filtered(lambda a: a.state != 'cancel'):
                        move_qty_orig = move.quantity
                        if move.product_uom and orig_alloc.product_uom_id and move.product_uom != orig_alloc.product_uom_id:
                            move_qty_orig = move.product_uom._compute_quantity(move.quantity, orig_alloc.product_uom_id)
                        orig_alloc._on_move_returned(move, move_qty_orig)
                # 2B. Devolución de compra rechazada retornada al proveedor
                p_allocs = orig_move.purchase_allocation_ids
                if not p_allocs and orig_move.purchase_line_id and hasattr(orig_move.purchase_line_id, 'allocation_ids'):
                    p_allocs = orig_move.purchase_line_id.allocation_ids
                if p_allocs:
                    for p_alloc in p_allocs.filtered(lambda a: a.state != 'cancel'):
                        move_qty_p = move.quantity
                        if move.product_uom and p_alloc.product_uom_id and move.product_uom != p_alloc.product_uom_id:
                            move_qty_p = move.product_uom._compute_quantity(move.quantity, p_alloc.product_uom_id)
                        p_alloc._on_purchase_returned(move, move_qty_p)

            # 3. Movimiento de Recepción de Compra (proveedor a bodega)
            if move.purchase_line_id and hasattr(move.purchase_line_id, 'allocation_ids') and move.purchase_line_id.allocation_ids:
                purchase_allocs = move.purchase_line_id.allocation_ids.filtered(lambda a: a.state != 'cancel')
                for p_alloc in purchase_allocs:
                    move_qty_p = move.quantity
                    if move.product_uom and p_alloc.product_uom_id and move.product_uom != p_alloc.product_uom_id:
                        move_qty_p = move.product_uom._compute_quantity(move.quantity, p_alloc.product_uom_id)
                    p_alloc._on_purchase_received(move, move_qty_p)

        return moves_done

    def _action_cancel(self):
        """Al cancelarse movimientos con asignaciones activas, sincroniza la cancelación y reintegra saldos."""
        res = super()._action_cancel()
        for move in self:
            allocs = move.allocation_ids.filtered(lambda a: a.state != 'cancel')
            if allocs:
                for alloc in allocs:
                    if alloc.state != 'cancel':
                        alloc.action_cancel_allocation(
                            cancel_action='reintegrate',
                            reason=_("Cancelación automática del movimiento de almacén %s") % (move.reference or move.name)
                        )
        return res

    def unlink(self):
        """Al eliminarse movimientos de almacén, cancela y reintegra asignaciones vinculadas."""
        allocs_to_notify = self.env['requisition.allocation']
        for move in self:
            active_allocs = move.allocation_ids.filtered(lambda a: a.state != 'cancel')
            if active_allocs:
                active_allocs.action_cancel_allocation(
                    cancel_action='reintegrate',
                    reason=_("Movimiento de almacén eliminado")
                )
                allocs_to_notify |= active_allocs
        res = super().unlink()
        for alloc in allocs_to_notify:
            alloc._sync_origin_document_status()
        return res

    def write(self, vals):
        res = super().write(vals)
        # Sincronización cuando se modifica la cantidad demandada en el movimiento (product_uom_qty)
        if 'product_uom_qty' in vals:
            for move in self:
                allocations = move.allocation_ids.filtered(lambda a: a.state in ('draft', 'in_progress'))
                if not allocations:
                    continue
                new_qty = vals['product_uom_qty']
                if len(allocations) == 1:
                    alloc = allocations[0]
                    target_qty = new_qty
                    if move.product_uom and alloc.product_uom_id and move.product_uom != alloc.product_uom_id:
                        target_qty = move.product_uom._compute_quantity(new_qty, alloc.product_uom_id)
                    final_qty = max(target_qty, alloc.delivered_qty)
                    if alloc.requested_qty != final_qty:
                        alloc.write({'requested_qty': final_qty})
                else:
                    current_total = sum(allocations.mapped('requested_qty'))
                    if current_total > 0 and current_total != new_qty:
                        ratio = new_qty / current_total
                        for alloc in allocations:
                            final_qty = max(alloc.requested_qty * ratio, alloc.delivered_qty)
                            if alloc.requested_qty != final_qty:
                                alloc.write({'requested_qty': final_qty})
                for alloc in allocations:
                    alloc._sync_origin_document_status()
        return res

    def _create_backorder(self):
        """
        Al generarse un backorder por entrega parcial, divide la asignación original
        entre la cantidad surtida y el remanente asignado al nuevo albarán pendiente.
        """
        backorder_moves = super()._create_backorder()

        for move in self:
            active_allocs = move.allocation_ids.filtered(lambda a: a.state != 'cancel')
            for alloc in active_allocs:
                qty_done_alloc = move.quantity
                if move.product_uom and alloc.product_uom_id and move.product_uom != alloc.product_uom_id:
                    qty_done_alloc = move.product_uom._compute_quantity(move.quantity, alloc.product_uom_id)

                if alloc.requested_qty > qty_done_alloc:
                    qty_rem = alloc.requested_qty - qty_done_alloc

                    # Ajustar asignación completada del albarán actual
                    alloc.write({
                        'requested_qty': qty_done_alloc,
                        'delivered_qty': qty_done_alloc,
                        'state': 'done'
                    })

                    # Vincular el saldo pendiente al nuevo movimiento del backorder
                    bm = backorder_moves.filtered(lambda m: m.product_id == move.product_id)
                    if bm:
                        new_alloc_vals = {
                            'company_id': alloc.company_id.id,
                            'product_id': alloc.product_id.id,
                            'product_uom_id': alloc.product_uom_id.id,
                            'requested_qty': qty_rem,
                            'delivered_qty': 0.0,
                            'supply_type': alloc.supply_type,
                            'stock_move_id': bm[0].id,
                            'state': 'in_progress',
                        }
                        extra_vals = alloc._get_split_extra_vals(bm[0], qty_rem)
                        if extra_vals:
                            new_alloc_vals.update(extra_vals)
                        self.env['requisition.allocation'].create(new_alloc_vals)

        return backorder_moves
