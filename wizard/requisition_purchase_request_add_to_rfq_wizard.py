# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare
from markupsafe import Markup, escape


class RequisitionPurchaseRequestAddToRfqWizard(models.TransientModel):
    _name = 'requisition.purchase.request.add.to.rfq.wizard'
    _description = 'Asistente para Agregar Solicitudes de Compra a RFQ'

    supplier_id = fields.Many2one(
        'res.partner',
        string='Proveedor',
        check_company=True,
        domain="['|', ('supplier_rank', '>', 0), ('is_company', '=', True)]",
        help='Proveedor para la nueva cotización (RFQ).'
    )
    purchase_order_id = fields.Many2one(
        'purchase.order',
        string='Cotización Existente (RFQ)',
        check_company=True,
        domain="[('state', 'in', ['draft']), ('company_id', '=', company_id)]",
        help='Opcional: Seleccione una solicitud de cotización en borrador para anexar las partidas.'
    )
    purchaser_id = fields.Many2one(
        'res.users',
        string='Comprador',
        required=True,
        default=lambda self: self.env.user,
        help='Comprador asignado a la cotización.'
    )
    picking_type_id = fields.Many2one(
        'stock.picking.type',
        string='Entregar en (Operación)',
        required=True,
        default=lambda self: self._default_picking_type(),
        help='Tipo de operación de recepción para la orden de compra.'
    )
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        required=True,
        default=lambda self: self.env.company
    )
    line_ids = fields.One2many(
        'requisition.purchase.request.add.to.rfq.wizard.line',
        'wizard_id',
        string='Partidas de Solicitud'
    )

    @api.model
    def _default_picking_type(self):
        company_id = self.env.company.id
        types = self.env['stock.picking.type'].search([
            ('code', '=', 'incoming'),
            ('warehouse_id.company_id', '=', company_id)
        ], limit=1)
        if not types:
            types = self.env['stock.picking.type'].search([
                ('code', '=', 'incoming')
            ], limit=1)
        return types.id if types else False

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids', [])

        if active_model == 'requisition.purchase.request.line' and active_ids:
            lines = self.env['requisition.purchase.request.line'].browse(active_ids)
            valid_lines = lines.filtered(lambda l: l.state not in ('cancel', 'purchased'))
            if not valid_lines:
                raise UserError(_("Las líneas seleccionadas ya han sido compradas o están canceladas."))

            companies = valid_lines.mapped('company_id')
            if len(companies) > 1:
                raise UserError(_("No puede consolidar en una misma cotización partidas pertenecientes a distintas compañías."))

            if companies:
                defaults['company_id'] = companies[0].id

            line_vals = []
            for line in valid_lines:
                qty = line.pending_qty_to_buy if line.pending_qty_to_buy > 0 else line.product_qty
                line_vals.append((0, 0, {
                    'request_line_id': line.id,
                    'selected': True,
                    'product_qty': qty,
                }))
            defaults['line_ids'] = line_vals

        return defaults

    def add_to_rfq(self):
        self.ensure_one()
        selected_lines = self.line_ids.filtered('selected')
        if not selected_lines:
            raise UserError(_("Debe seleccionar al menos una partida para transferir a la cotización."))

        for line in selected_lines:
            if line.product_qty <= 0:
                raise UserError(
                    _("La cantidad a cotizar para el producto '%s' debe ser mayor a cero.")
                    % line.product_id.display_name
                )
            if float_compare(line.product_qty, line.request_line_id.pending_qty_to_buy, precision_rounding=line.uom_id.rounding or 0.01) > 0:
                raise UserError(
                    _("La cantidad indicada (%.2f) excede el saldo pendiente por comprar (%.2f) para '%s'.")
                    % (line.product_qty, line.request_line_id.pending_qty_to_buy, line.product_id.display_name)
                )

        order = self.purchase_order_id
        if not order:
            if not self.supplier_id:
                raise UserError(_("Debe seleccionar un proveedor para crear una nueva solicitud de cotización."))

            po_vals = {
                'partner_id': self.supplier_id.id,
                'company_id': self.company_id.id,
                'picking_type_id': self.picking_type_id.id,
                'date_order': fields.Date.today(),
                'user_id': self.purchaser_id.id,
                'currency_id': self.supplier_id.property_purchase_currency_id.id or self.company_id.currency_id.id,
            }
            order = self.env['purchase.order'].create(po_vals)

        added_po_lines = self.env['purchase.order.line']
        affected_request_lines = self.env['requisition.purchase.request.line']

        for wizard_line in selected_lines:
            req_line = wizard_line.request_line_id
            product = req_line.product_id
            po_uom = getattr(product, 'uom_po_id', False) or req_line.product_uom_id
            po_qty = req_line.product_uom_id._compute_quantity(wizard_line.product_qty, po_uom)

            # Preservar separación estricta: buscar si esta partida específica ya está vinculada a una línea de la PO
            existing_alloc = req_line.allocation_ids.filtered(
                lambda a: a.purchase_line_id and a.purchase_line_id.order_id == order and a.state != 'cancel'
            )

            if existing_alloc and existing_alloc[0].purchase_line_id:
                po_line = existing_alloc[0].purchase_line_id
                target_uom = getattr(po_line, 'product_uom_id', False) or getattr(po_line, 'product_uom', False) or req_line.product_uom_id
                added_po_qty = req_line.product_uom_id._compute_quantity(wizard_line.product_qty, target_uom)
                po_line.product_qty += added_po_qty
                # Incrementar cantidad en la asignación existente
                existing_alloc[0].requested_qty += wizard_line.product_qty
            else:
                # Crear nueva línea independiente de orden de compra
                name_parts = [product.display_name]
                if req_line.requisition_id:
                    name_parts.append(_("Requisición: %s") % req_line.requisition_id.name)
                if req_line.notes:
                    name_parts.append(_("Notas: %s") % req_line.notes)

                po_line_vals = {
                    'order_id': order.id,
                    'product_id': product.id,
                    'product_uom_id': po_uom.id,
                    'product_qty': po_qty,
                    'name': "\n".join(name_parts),
                    'price_unit': 0.0,
                    'date_planned': req_line.date_required or fields.Date.today(),
                }
                po_line = self.env['purchase.order.line'].create(po_line_vals)
                added_po_lines |= po_line

                # Vincular o dividir asignación en supply.chain.allocation
                free_alloc = req_line.allocation_ids.filtered(lambda a: not a.purchase_line_id and a.state != 'cancel')[:1]
                rounding = req_line.product_uom_id.rounding or 0.01

                if free_alloc:
                    diff = float_compare(free_alloc.requested_qty, wizard_line.product_qty, precision_rounding=rounding)
                    if diff <= 0:
                        free_alloc.write({'purchase_line_id': po_line.id})
                    else:
                        remainder = free_alloc.requested_qty - wizard_line.product_qty
                        free_alloc.write({
                            'requested_qty': wizard_line.product_qty,
                            'purchase_line_id': po_line.id
                        })
                        self.env['supply.chain.allocation'].create({
                            'company_id': free_alloc.company_id.id,
                            'product_id': free_alloc.product_id.id,
                            'product_uom_id': free_alloc.product_uom_id.id,
                            'requested_qty': remainder,
                            'delivered_qty': 0.0,
                            'supply_type': free_alloc.supply_type,
                            'purchase_request_line_id': req_line.id,
                            'requisition_line_id': free_alloc.requisition_line_id.id if free_alloc.requisition_line_id else False,
                            'supply_request_line_id': free_alloc.supply_request_line_id.id if free_alloc.supply_request_line_id else False,
                            'state': 'draft',
                        })
                else:
                    supply_type = req_line.requisition_line_id.supply_type if req_line.requisition_line_id else 'consumption'
                    self.env['supply.chain.allocation'].create({
                        'company_id': req_line.company_id.id,
                        'product_id': req_line.product_id.id,
                        'product_uom_id': req_line.product_uom_id.id,
                        'requested_qty': wizard_line.product_qty,
                        'delivered_qty': 0.0,
                        'supply_type': supply_type,
                        'purchase_request_line_id': req_line.id,
                        'requisition_line_id': req_line.requisition_line_id.id if req_line.requisition_line_id else False,
                        'purchase_line_id': po_line.id,
                        'state': 'in_progress',
                    })

            affected_request_lines |= req_line

            req_line.message_post(
                body=Markup(_("Partida añadida a la solicitud de cotización <strong>%s</strong> por una cantidad de %.2f %s.")) % (
                    escape(order.name),
                    wizard_line.product_qty,
                    escape(req_line.product_uom_id.name or '')
                ),
                subtype_xmlid="mail.mt_note"
            )

        for req in affected_request_lines:
            req._compute_quantities()
            req._compute_state()

        order.message_post(
            body=Markup(_("Se integraron <strong>%d</strong> partida(s) desde Solicitudes de Compra.")) % len(selected_lines),
            subtype_xmlid="mail.mt_note"
        )

        return {
            'name': _('Cotización'),
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.order',
            'res_id': order.id,
            'view_mode': 'form',
            'target': 'current',
        }


class RequisitionPurchaseRequestAddToRfqWizardLine(models.TransientModel):
    _name = 'requisition.purchase.request.add.to.rfq.wizard.line'
    _description = 'Línea del Asistente para Agregar Solicitudes a RFQ'

    wizard_id = fields.Many2one(
        'requisition.purchase.request.add.to.rfq.wizard',
        required=True,
        ondelete='cascade'
    )
    request_line_id = fields.Many2one(
        'requisition.purchase.request.line',
        string='Línea de Solicitud',
        required=True
    )
    selected = fields.Boolean(
        string='Seleccionar',
        default=True
    )
    product_id = fields.Many2one(
        'product.product',
        string='Producto',
        related='request_line_id.product_id',
        readonly=True
    )
    requisition_id = fields.Many2one(
        'requisitions',
        string='Requisición',
        related='request_line_id.requisition_id',
        readonly=True
    )
    notes = fields.Text(
        string='Notas',
        related='request_line_id.notes',
        readonly=True
    )
    pending_qty_to_buy = fields.Float(
        string='Saldo Pendiente',
        related='request_line_id.pending_qty_to_buy',
        readonly=True
    )
    product_qty = fields.Float(
        string='Cantidad a Cotizar',
        required=True,
        digits='Product Unit of Measure'
    )
    uom_id = fields.Many2one(
        'uom.uom',
        string='Unidad',
        related='request_line_id.product_uom_id',
        readonly=True
    )
