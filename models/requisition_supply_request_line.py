# -*- coding: utf-8 -*-
from collections import defaultdict
from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
from odoo.tools import format_date, format_datetime


class RequisitionSupplyRequestLine(models.Model):
    """
    Partida de Solicitud de Suministro para envíos Base -> Obra.
    Gestiona trazabilidad con la requisición, selección de números de serie/lote,
    auditoría en Chatter y verificación de dotaciones EPP.
    """
    _name = 'requisition.supply.request.line'
    _description = 'Línea de Solicitud de Suministro'
    _order = 'request_id desc, id'

    request_id = fields.Many2one(
        'requisition.supply.request',
        string='Solicitud de Suministro',
        required=True,
        ondelete='cascade',
        index=True
    )
    company_id = fields.Many2one(
        'res.company',
        related='request_id.company_id',
        string='Compañía',
        store=True,
        readonly=True,
        index=True
    )
    requisition_line_id = fields.Many2one(
        'requisitions.line',
        string='Línea de Requisición',
        index=True
    )
    requisition_id = fields.Many2one(
        'requisitions',
        related='requisition_line_id.requisition_id',
        string='Requisición Origen',
        store=True,
        readonly=True,
        index=True
    )
    requisition_deadline = fields.Date(
        string='Fecha Requerida Requisición',
        related='requisition_id.date_required',
        store=True,
        readonly=True
    )
    product_id = fields.Many2one(
        'product.product',
        string='Producto',
        required=True,
        check_company=True
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='Unidad de Medida',
        related='product_id.uom_id',
        readonly=True,
        store=True
    )
    product_qty = fields.Float(
        string='Cantidad Solicitada',
        required=True,
        default=1.0,
        digits='Product Unit of Measure'
    )
    request_qty = fields.Float(
        string='Demanda en Requisición',
        related='requisition_line_id.product_qty',
        readonly=True,
        store=True
    )
    requester_name = fields.Char(
        string='Solicitado por',
        compute='_compute_requester_name',
        store=True,
        readonly=False
    )
    notes = fields.Char(
        string='Notas / Observaciones'
    )
    is_manual = fields.Boolean(
        string='Ingreso Manual',
        default=False,
        help="Indica si la partida fue añadida directamente sin una línea de requisición."
    )

    # --- Gestión de Series / Lotes ---
    has_tracking = fields.Selection(
        related='product_id.tracking',
        readonly=True
    )
    available_lot_ids = fields.Many2many(
        'stock.lot',
        compute='_compute_available_lot_ids',
        string='Series/Lotes Disponibles en Origen'
    )
    lot_ids = fields.Many2many(
        'stock.lot',
        'requisition_supply_line_lot_rel',
        'line_id',
        'lot_id',
        string='Números de Serie / Lotes',
        domain="[('id', 'in', available_lot_ids)]",
        help="Seleccione los números de serie o lote específicos a transferir desde el almacén origen."
    )

    # --- Historial de EPP / Dotaciones ---
    is_dotation = fields.Boolean(
        string='Aplica Historial EPP',
        compute='_compute_is_dotation',
        store=True
    )
    dotation_display = fields.Char(
        string='Historial',
        compute='_compute_dotation_display'
    )

    # -------------------------------------------------------------------------
    # CONSTRAINS
    # -------------------------------------------------------------------------
    @api.constrains('product_qty')
    def _check_product_qty(self):
        for line in self:
            if line.product_qty <= 0:
                raise ValidationError(_("La cantidad solicitada en la partida de suministro debe ser mayor a cero."))

    @api.constrains('lot_ids', 'product_qty', 'has_tracking')
    def _check_lots_quantity(self):
        for line in self:
            if line.has_tracking == 'serial' and line.lot_ids:
                if len(line.lot_ids) > line.product_qty:
                    raise ValidationError(_(
                        "No puede seleccionar más números de serie (%s) que la cantidad solicitada (%s) para el producto '%s'."
                    ) % (len(line.lot_ids), line.product_qty, line.product_id.display_name))

    # -------------------------------------------------------------------------
    # COMPUTED METHODS
    # -------------------------------------------------------------------------
    @api.depends('requisition_line_id', 'requisition_id.employee_id')
    def _compute_requester_name(self):
        for line in self:
            if line.requisition_id and line.requisition_id.employee_id:
                line.requester_name = line.requisition_id.employee_id.name
            elif not line.requester_name:
                line.requester_name = self.env.user.name

    @api.depends('product_id', 'product_id.tracking', 'request_id.location_id')
    def _compute_available_lot_ids(self):
        """Calcula los lotes/series disponibles en la ubicación origen de la solicitud de suministro de forma vectorizada."""
        trackable_lines = self.filtered(
            lambda l: l.product_id and l.product_id.tracking != 'none' and l.request_id.location_id
        )
        (self - trackable_lines).available_lot_ids = False
        if not trackable_lines:
            return

        product_ids = trackable_lines.mapped('product_id').ids
        location_ids = trackable_lines.mapped('request_id.location_id').ids

        all_quants = self.env['stock.quant'].search([
            ('product_id', 'in', product_ids),
            ('location_id', 'child_of', location_ids),
            ('quantity', '>', 0),
            ('lot_id', '!=', False),
        ])

        quants_by_product = {}
        for q in all_quants:
            quants_by_product.setdefault(q.product_id.id, self.env['stock.quant'])
            quants_by_product[q.product_id.id] |= q

        for line in trackable_lines:
            prod_quants = quants_by_product.get(line.product_id.id, self.env['stock.quant'])
            loc_id = line.request_id.location_id.id
            matching_lots = prod_quants.filtered(
                lambda q: q.location_id.id == loc_id or (q.location_id.parent_path and f"/{loc_id}/" in f"/{q.location_id.parent_path}")
            ).mapped('lot_id')
            line.available_lot_ids = matching_lots

    @api.depends('product_id', 'product_id.categ_id')
    def _compute_is_dotation(self):
        for line in self:
            if not line.product_id or not line.product_id.categ_id:
                line.is_dotation = False
                continue
            categ_name = line.product_id.categ_id.name or ''
            line.is_dotation = (categ_name.upper() == 'EPP' or 'EPP' in categ_name.upper())

    def _compute_dotation_display(self):
        for line in self:
            line.dotation_display = ''

    def get_last_dotations(self):
        """Obtiene las últimas 2 entregas registradas para el empleado asignado y este producto."""
        self.ensure_one()
        employee = self.requisition_id.employee_id if self.requisition_id else False
        if not employee:
            return []

        moves = self.env['stock.move'].search([
            ('product_id', '=', self.product_id.id),
            ('state', '=', 'done'),
            ('picking_id.partner_id', '=', employee.work_contact_id.id if employee.work_contact_id else False),
        ], order='date desc', limit=2)

        result = []
        for move in moves:
            result.append({
                'picking_name': move.picking_id.name or move.reference or '',
                'date': move.date.strftime('%d/%m/%Y %H:%M') if move.date else '',
            })
        return result

    # -------------------------------------------------------------------------
    # CRUD Y FUSIÓN DE LÍNEAS CON AUDITORÍA EN CHATTER
    # -------------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records_to_create = []
        for vals in vals_list:
            req_id = vals.get('request_id')
            prod_id = vals.get('product_id')
            req_line_id = vals.get('requisition_line_id')
            qty = vals.get('product_qty', 0.0)
            lot_ids = vals.get('lot_ids', [])

            # Buscar si existe una partida compatible para consolidar cantidades
            domain = [('request_id', '=', req_id), ('product_id', '=', prod_id)]
            if req_line_id:
                domain.append(('requisition_line_id', '=', req_line_id))
            else:
                domain.append(('requisition_line_id', '=', False))

            existing = self.search(domain, limit=1)
            if existing:
                existing.product_qty += qty
                if lot_ids:
                    # Extraer IDs de lotes
                    new_lot_ids = []
                    if isinstance(lot_ids, list):
                        for cmd in lot_ids:
                            if isinstance(cmd, (tuple, list)):
                                if cmd[0] == 6:
                                    new_lot_ids.extend(cmd[2])
                                elif cmd[0] == 4:
                                    new_lot_ids.append(cmd[1])
                            elif isinstance(cmd, int):
                                new_lot_ids.append(cmd)
                    merged = list(set(existing.lot_ids.ids + new_lot_ids))
                    existing.write({'lot_ids': [(6, 0, merged)]})
                if 'notes' in vals and vals['notes']:
                    existing.notes = (existing.notes or '') + (" | " if existing.notes else "") + vals['notes']
                continue

            records_to_create.append(vals)

        created_records = super().create(records_to_create)

        # Notificación en Chatter padre con Markup seguro
        for rec in created_records:
            if rec.request_id:
                body = Markup(
                    "<div class='d-flex align-items-center mb-1'>"
                    "<span class='badge text-bg-success me-2'>+ Partida</span>"
                    "<strong>%s</strong> &nbsp;|&nbsp; Cantidad: <strong>%s %s</strong>"
                    "</div>"
                ) % (
                    escape(rec.product_id.display_name),
                    rec.product_qty,
                    escape(rec.product_uom_id.name or '')
                )
                rec.request_id.message_post(body=body, subtype_xmlid="mail.mt_note")

        return created_records

    def write(self, vals):
        tracked = {'product_id', 'product_qty', 'lot_ids', 'notes'}
        has_tracked = any(f in vals for f in tracked)
        old_data = {}
        if has_tracked:
            for rec in self:
                old_data[rec.id] = {
                    'product': rec.product_id.display_name,
                    'qty': rec.product_qty,
                    'lots': ', '.join(rec.lot_ids.mapped('name')),
                    'notes': rec.notes,
                }

        res = super().write(vals)

        if has_tracked:
            for rec in self:
                if not rec.request_id:
                    continue
                old = old_data.get(rec.id, {})
                changes = []
                if 'product_id' in vals and old.get('product') != rec.product_id.display_name:
                    changes.append(_("Producto: %s → %s") % (old.get('product'), rec.product_id.display_name))
                if 'product_qty' in vals and old.get('qty') != rec.product_qty:
                    changes.append(_("Cantidad: %s → %s") % (old.get('qty'), rec.product_qty))
                if 'lot_ids' in vals:
                    new_lots = ', '.join(rec.lot_ids.mapped('name'))
                    if old.get('lots') != new_lots:
                        changes.append(_("Series/Lotes: [%s] → [%s]") % (old.get('lots') or _('Ninguno'), new_lots or _('Ninguno')))

                if changes:
                    items_html = "".join([f"<li>{escape(c)}</li>" for c in changes])
                    body = Markup(
                        "<strong>Partida Actualizada: %s</strong>"
                        "<ul>%s</ul>"
                    ) % (escape(rec.product_id.display_name), Markup(items_html))
                    rec.request_id.message_post(body=body, subtype_xmlid="mail.mt_note")

        return res

    @api.ondelete(at_uninstall=False)
    def _unlink_except_draft_request(self):
        for rec in self:
            if rec.request_id and rec.request_id.state not in ('draft', 'cancel'):
                raise UserError(_(
                    "No se pueden eliminar partidas de una solicitud de suministro en curso ('%s')."
                ) % rec.request_id.name)

    def unlink(self):
        messages = []
        for rec in self:
            if rec.request_id:
                messages.append((
                    rec.request_id,
                    rec.product_id.display_name,
                    rec.product_qty,
                    rec.product_uom_id.name or ''
                ))
        res = super().unlink()
        for request, prod_name, qty, uom_name in messages:
            body = Markup(
                "<span class='badge text-bg-danger me-2'>- Partida Eliminada</span>"
                "Producto: <strong>%s</strong> | Cantidad: %s %s"
            ) % (escape(prod_name), qty, escape(uom_name))
            request.message_post(body=body, subtype_xmlid="mail.mt_note")
        return res
