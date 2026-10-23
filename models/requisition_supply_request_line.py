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
        required=False,
        ondelete='set null',
        index=True
    )
    company_id = fields.Many2one(
        'res.company',
        compute='_compute_company_id',
        string='Compañía',
        store=True,
        readonly=False,
        precompute=True,
        default=lambda self: self.env.company,
        index=True
    )
    warehouse_dest_id = fields.Many2one(
        'stock.warehouse',
        string='Almacén Destino (Obra)',
        compute='_compute_warehouses',
        store=True,
        readonly=False,
        precompute=True,
        index=True,
        help="Almacén de obra hacia donde se destina el suministro."
    )
    warehouse_origin_id = fields.Many2one(
        'stock.warehouse',
        string='Almacén Origen (Base)',
        compute='_compute_warehouses',
        store=True,
        readonly=False,
        precompute=True,
        index=True,
        help="Almacén base/central desde donde se surtirá el material a la obra."
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

    # --- Integración con requisition.allocation ---
    allocation_ids = fields.One2many(
        'requisition.allocation',
        'supply_request_line_id',
        string='Asignaciones de Requisición'
    )
    allocation_id = fields.Many2one(
        'requisition.allocation',
        string='Asignación Principal',
        compute='_compute_allocation_id',
        store=True,
        readonly=True,
        index=True
    )
    purchase_request_line_id = fields.Many2one(
        'requisition.purchase.request.line',
        string='Solicitud de Compra Vinculada',
        compute='_compute_purchase_request_line_id',
        store=True,
        readonly=True,
        help='Partida de Solicitud de Compra vinculada a través de la asignación unificada.'
    )
    is_in_purchase = fields.Boolean(
        string='En Proceso de Compra',
        compute='_compute_purchase_process_info',
        store=True,
        help="Indica si la partida cuenta con una solicitud de compra activa."
    )
    purchase_process_label = fields.Char(
        string='Proceso de Compra',
        compute='_compute_purchase_process_info',
        store=True,
    )
    purchase_request_line_state = fields.Selection(
        related='purchase_request_line_id.state',
        string='Estado de Compra',
        readonly=True,
        store=True,
    )
    qty_in_transit = fields.Float(
        string='En Tránsito',
        compute='_compute_quantities',
        store=True,
        digits='Product Unit of Measure',
        help='Cantidad activa despachada hacia tránsito pendiente de recibir en obra.'
    )
    qty_done = fields.Float(
        string='Suministrado / Recibido',
        compute='_compute_quantities',
        store=True,
        digits='Product Unit of Measure',
        help='Cantidad efectivamente recibida en el almacén de destino.'
    )
    qty_cancelled = fields.Float(
        string='Cancelado',
        compute='_compute_quantities',
        store=True,
        digits='Product Unit of Measure'
    )
    qty_base_available = fields.Float(
        string='Stock en Base',
        compute='_compute_base_stock',
        digits='Product Unit of Measure',
        help="Stock disponible en el almacén Base/Central de origen."
    )
    suggest_purchase = fields.Boolean(
        string='Sugerir Compra',
        compute='_compute_base_stock',
        store=True,
        help="Indica si el stock disponible en Base es menor que la cantidad requerida."
    )
    state = fields.Selection([
        ('pending_assignment', 'Pendiente de Envío'),
        ('draft', 'Asignado a Solicitud'),
        ('in_progress', 'En Tránsito / Proceso'),
        ('done', 'Suministrado'),
        ('cancel', 'Cancelado'),
    ], string='Estado de Partida', default='pending_assignment', compute='_compute_state', store=True, readonly=False, precompute=True, index=True)

    @api.depends('request_id.company_id', 'requisition_line_id.company_id')
    def _compute_company_id(self):
        for line in self:
            if line.request_id and line.request_id.company_id:
                line.company_id = line.request_id.company_id
            elif line.requisition_line_id and line.requisition_line_id.company_id:
                line.company_id = line.requisition_line_id.company_id
            else:
                line.company_id = line.company_id or self.env.company

    @api.depends('request_id.warehouse_dest_id', 'request_id.warehouse_origin_id', 'requisition_id.warehouse_id', 'company_id')
    def _compute_warehouses(self):
        for line in self:
            if line.request_id:
                line.warehouse_dest_id = line.request_id.warehouse_dest_id
                line.warehouse_origin_id = line.request_id.warehouse_origin_id
            elif line.requisition_id and line.requisition_id.warehouse_id:
                line.warehouse_dest_id = line.requisition_id.warehouse_id
            else:
                line.warehouse_dest_id = line.warehouse_dest_id or False

            if not line.request_id:
                if not line.warehouse_origin_id:
                    company = line.company_id or self.env.company
                    base_wh = self.env['stock.warehouse'].search([
                        ('company_id', '=', company.id),
                        '|', ('code', 'ilike', '%base%'), ('name', 'ilike', '%base%')
                    ], limit=1)
                    if not base_wh:
                        base_wh = self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1) or self.env['stock.warehouse'].search([], limit=1)
                    line.warehouse_origin_id = base_wh.id if base_wh else False
                else:
                    line.warehouse_origin_id = line.warehouse_origin_id

    @api.depends('product_id', 'warehouse_origin_id', 'product_qty')
    def _compute_base_stock(self):
        for line in self:
            if not line.product_id or not line.warehouse_origin_id:
                line.qty_base_available = 0.0
                line.suggest_purchase = False
                continue
            lot_loc = line.warehouse_origin_id.lot_stock_id
            if not lot_loc:
                line.qty_base_available = 0.0
                line.suggest_purchase = False
                continue
            quants = self.env['stock.quant'].search([
                ('product_id', '=', line.product_id.id),
                ('location_id', 'child_of', lot_loc.id),
            ])
            avail = sum(quants.mapped('quantity')) - sum(quants.mapped('reserved_quantity'))
            line.qty_base_available = max(0.0, avail)
            line.suggest_purchase = (line.qty_base_available < line.product_qty)

    @api.depends('allocation_ids')
    def _compute_allocation_id(self):
        for line in self:
            line.allocation_id = line.allocation_ids[:1]

    @api.depends('allocation_ids.purchase_request_line_id')
    def _compute_purchase_request_line_id(self):
        for line in self:
            linked_pr = line.allocation_ids.mapped('purchase_request_line_id')
            line.purchase_request_line_id = linked_pr[:1].id if linked_pr else False

    @api.depends('purchase_request_line_id', 'purchase_request_line_id.state')
    def _compute_purchase_process_info(self):
        for line in self:
            is_active = bool(
                line.purchase_request_line_id and line.purchase_request_line_id.state != 'cancel'
            )
            line.is_in_purchase = is_active
            line.purchase_process_label = _("En proceso de compra") if is_active else False

    @api.depends(
        'allocation_ids.requested_qty',
        'allocation_ids.delivered_qty',
        'allocation_ids.open_qty',
        'allocation_ids.cancelled_qty',
        'allocation_ids.state',
        'request_id.state'
    )
    def _compute_quantities(self):
        for line in self:
            active_allocs = line.allocation_ids.filtered(lambda a: a.state != 'cancel')
            line.qty_done = sum(active_allocs.mapped('delivered_qty'))
            line.qty_in_transit = sum(active_allocs.filtered(lambda a: a.state in ('draft', 'in_progress')).mapped('open_qty'))
            line.qty_cancelled = sum(line.allocation_ids.filtered(lambda a: a.state == 'cancel').mapped('cancelled_qty'))

    @api.depends('request_id', 'request_id.state', 'allocation_ids.state', 'qty_done', 'product_qty')
    def _compute_state(self):
        for line in self:
            if not line.request_id:
                cancelled_alloc = line.allocation_ids and all(a.state == 'cancel' for a in line.allocation_ids)
                line.state = 'cancel' if cancelled_alloc else 'pending_assignment'
            elif line.request_id.state == 'cancel':
                line.state = 'cancel'
            elif line.request_id.state in ('done_exact', 'done_partial', 'done_adjusted'):
                line.state = 'done' if (line.product_qty > 0 and line.qty_done >= line.product_qty) else 'in_progress'
            elif line.request_id.state in ('in_transit', 'delivery_created'):
                line.state = 'in_progress'
            else:
                line.state = 'draft'



    # -------------------------------------------------------------------------
    # CONSTRAINS
    # -------------------------------------------------------------------------
    @api.constrains('product_qty')
    def _check_product_qty(self):
        for line in self:
            if line.product_qty <= 0:
                raise ValidationError(_("La cantidad solicitada en la partida de suministro debe ser mayor a cero."))

    @api.constrains('lot_ids', 'product_qty')
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
            lambda l: l.product_id and l.product_id.tracking != 'none' and l.request_id and l.request_id.location_id
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
        consolidated_records = self.browse()
        for vals in vals_list:
            req_id = vals.get('request_id')
            prod_id = vals.get('product_id')
            req_line_id = vals.get('requisition_line_id')
            qty = vals.get('product_qty', 0.0)
            lot_ids = vals.get('lot_ids', [])

            # Solo consolidar si la partida pertenece a una Solicitud de Suministro específica.
            # Las partidas sin solicitud (pool de pendientes de envío) se tratan como registros independientes.
            if req_id and prod_id:
                domain = [('request_id', '=', req_id), ('product_id', '=', prod_id)]
                if req_line_id:
                    domain.append(('requisition_line_id', '=', req_line_id))
                else:
                    domain.append(('requisition_line_id', '=', False))

                existing = self.search(domain, limit=1)
                # Si el usuario es de planta y la línea existente pertenece a otro usuario o a Base, no alterar la línea ajena
                is_planta_only = (
                    self.env.user.has_group('dh_requisitions.group_stock_request_planta') and not (
                        self.env.user.has_group('dh_requisitions.group_stock_request_employee') or
                        self.env.user.has_group('dh_requisitions.group_requisitions_manager')
                    )
                )
                if existing and not (is_planta_only and existing.create_uid != self.env.user):
                    existing.product_qty += qty
                    for alloc in existing.allocation_ids.filtered(lambda a: a.state != 'cancel'):
                        alloc.requested_qty = existing.product_qty
                        alloc._sync_origin_document_status()
                    if lot_ids:
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
                    consolidated_records |= existing
                    continue

            if not vals.get('requisition_line_id'):
                vals['is_manual'] = True
            records_to_create.append(vals)

        created_records = super().create(records_to_create)
        created_records._compute_warehouses()
        created_records._compute_state()

        # Sincronizar asignaciones automáticas para líneas con requisición
        if not self.env.context.get('skip_auto_allocation'):
            for rec in created_records:
                if rec.requisition_line_id and not rec.allocation_ids:
                    # Buscar si ya existe una asignación activa en la requisición con Solicitud de Compra
                    # que aún no tenga asociada una partida de suministro para unificar la trazabilidad
                    existing_pr_alloc = rec.requisition_line_id.allocation_ids.filtered(
                        lambda a: (bool(a.purchase_request_line_id) or bool(a.purchase_line_id))
                        and not a.supply_request_line_id
                        and a.state not in ('cancel', 'done')
                    )
                    if existing_pr_alloc:
                        alloc = existing_pr_alloc[:1]
                        alloc.write({'supply_request_line_id': rec.id})
                    else:
                        supply_type = rec.requisition_line_id.supply_type or (
                            'resg' if rec.requisition_line_id.line_type in ('tool', 'equipment') else 'consumption'
                        )
                        company = rec.company_id or self.env.company
                        self.env['requisition.allocation'].create({
                            'company_id': company.id,
                            'product_id': rec.product_id.id,
                            'product_uom_id': rec.product_uom_id.id,
                            'requested_qty': rec.product_qty,
                            'delivered_qty': 0.0,
                            'supply_type': supply_type,
                            'stage': 'inbound',
                            'supply_request_line_id': rec.id,
                            'requisition_line_id': rec.requisition_line_id.id,
                            'state': 'draft',
                        })

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

        return created_records | consolidated_records

    def write(self, vals):
        if self.env.user.has_group('dh_requisitions.group_stock_request_planta') and not (
            self.env.user.has_group('dh_requisitions.group_stock_request_employee') or
            self.env.user.has_group('dh_requisitions.group_requisitions_manager')
        ):
            for line in self:
                if line.create_uid != self.env.user:
                    raise UserError(_("No tienes permisos para modificar líneas creadas por otros usuarios o derivadas del Almacén Base."))

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

        if 'product_qty' in vals:
            for rec in self:
                for alloc in rec.allocation_ids.filtered(lambda a: a.state != 'cancel'):
                    alloc.requested_qty = vals['product_qty']
                    alloc._sync_origin_document_status()

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
        if self.env.user.has_group('dh_requisitions.group_stock_request_planta') and not (
            self.env.user.has_group('dh_requisitions.group_stock_request_employee') or
            self.env.user.has_group('dh_requisitions.group_requisitions_manager')
        ):
            for line in self:
                if line.create_uid != self.env.user:
                    raise UserError(_("No tienes permisos para eliminar líneas creadas por otros usuarios o derivadas del Almacén Base."))

        allocations_to_clean = self.mapped('allocation_ids')
        for alloc in allocations_to_clean:
            if alloc.state in ('in_progress', 'done'):
                alloc.action_cancel_allocation(
                    cancel_action='reintegrate',
                    reason=_("Partida de suministro eliminada")
                )
        allocations_to_delete = allocations_to_clean.filtered(lambda a: a.state in ('draft', 'cancel'))
        if allocations_to_delete:
            allocations_to_delete.unlink()

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

    # -------------------------------------------------------------------------
    # ACCIONES OPERATIVAS: PENDIENTES DE ENVÍO Y DISPARO DE COMPRA
    # -------------------------------------------------------------------------
    def action_mark_as_pending(self):
        """Desvincula la partida de la Solicitud de Suministro actual para dejarla pendiente de envío."""
        if not (self.env.user.has_group('dh_requisitions.group_stock_request_employee') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')):
            raise UserError(_("Se requiere pertenecer a Empleados Almacén Base o Administrador para marcar partidas como pendientes de envío."))
        for line in self:
            sr = line.request_id
            if sr and sr.state not in ('draft', False):
                raise UserError(_("Solo se pueden desasignar partidas de solicitudes en estado Borrador."))
            line.write({
                'request_id': False,
                'lot_ids': [(5, 0, 0)],
            })
            if sr:
                body = Markup(
                    "<span class='badge text-bg-warning me-2'>Pendiente de Envío</span>"
                    "Partida <strong>%s</strong> (%s %s) desvinculada de la solicitud y devuelta a pendientes de envío."
                ) % (
                    escape(line.product_id.display_name),
                    line.product_qty,
                    escape(line.product_uom_id.name or '')
                )
                sr.message_post(body=body, subtype_xmlid="mail.mt_note")
        return True

    def action_open_split_wizard(self):
        """Abre el asistente interactivo para enviar una cantidad parcial y dejar la cantidad remanente pendiente."""
        self.ensure_one()
        if not (self.env.user.has_group('dh_requisitions.group_stock_request_employee') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')):
            raise UserError(_("Se requiere pertenecer a Empleados Almacén Base o Administrador para dividir partidas."))
        if self.state not in ('draft', 'pending_assignment'):
            raise UserError(_("Solo se pueden dividir partidas que se encuentren en Borrador o Pendientes de Envío."))
        return {
            'name': _('Dividir Partida / Dejar Cantidad Pendiente'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.supply.request.line.split.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_line_id': self.id,
                'default_qty_to_send': self.product_qty,
            },
        }

    def action_create_purchase_request_line(self):
        """Genera una línea en Solicitud de Compra a partir de esta partida de suministro sin stock suficiente en Base."""
        self.ensure_one()
        # Verificar permisos: Administrador, Base o Compras
        if not (self.env.user.has_group('dh_requisitions.group_stock_request_employee') or
                self.env.user.has_group('purchase.group_purchase_user') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')):
            raise UserError(_("Para disparar una solicitud de compra desde suministro debe pertenecer a Empleados Almacén Base, Compras o Administrador."))

        # Buscar si ya existe una solicitud de compra activa para esta misma necesidad
        existing_pr = self.env['requisition.purchase.request.line'].search([
            ('product_id', '=', self.product_id.id),
            ('requisition_line_id', '=', self.requisition_line_id.id if self.requisition_line_id else False),
            ('state', 'not in', ('cancel', 'purchased')),
        ], limit=1)
        if existing_pr:
            raise UserError(_("Ya existe una Solicitud de Compra activa (%s) para este requerimiento.") % existing_pr.display_name)

        qty_to_buy = max(0.0, self.product_qty - self.qty_base_available) or self.product_qty

        pr_vals = {
            'requisition_id': self.requisition_id.id if self.requisition_id else False,
            'requisition_line_id': self.requisition_line_id.id if self.requisition_line_id else False,
            'product_id': self.product_id.id,
            'product_qty': qty_to_buy,
            'notes': _("Disparado automáticamente desde Suministro por falta de stock en Base. %s") % (self.notes or ''),
            'state': 'pending',
        }
        pr_line = self.env['requisition.purchase.request.line'].create(pr_vals)

        # Vincular con allocation
        if self.allocation_id:
            self.allocation_id.write({
                'purchase_request_line_id': pr_line.id,
                'stage': 'inbound',
                'state': 'in_progress',
            })
            pr_line.allocation_id = self.allocation_id.id
        elif self.requisition_line_id:
            supply_type = self.requisition_line_id.supply_type or (
                'resg' if self.requisition_line_id.line_type in ('tool', 'equipment') else 'consumption'
            )
            company = self.company_id or self.env.company
            alloc = self.env['requisition.allocation'].create({
                'company_id': company.id,
                'product_id': self.product_id.id,
                'product_uom_id': self.product_uom_id.id,
                'requested_qty': self.product_qty,
                'delivered_qty': 0.0,
                'supply_type': supply_type,
                'stage': 'inbound',
                'supply_request_line_id': self.id,
                'purchase_request_line_id': pr_line.id,
                'requisition_line_id': self.requisition_line_id.id,
                'state': 'draft',
            })
            pr_line.allocation_id = alloc.id

        if self.request_id:
            self.request_id.message_post(
                body=Markup(_(
                    "Se disparó la Solicitud de Compra <strong>%s</strong> para el producto '%s' (Cantidad: %s %s) "
                    "por falta de existencias en el Almacén Base."
                )) % (
                    escape(pr_line.display_name),
                    escape(self.product_id.display_name),
                    qty_to_buy,
                    escape(self.product_uom_id.name or '')
                ),
                subtype_xmlid="mail.mt_note"
            )

        return {
            'name': _('Solicitud de Compra'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.purchase.request.line',
            'view_mode': 'form',
            'res_id': pr_line.id,
        }
