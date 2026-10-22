# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html_escape
from markupsafe import Markup, escape


class RequisitionDeliveryWizard(models.TransientModel):
    """
    Asistente interactivo para generar despachos y entregas de almacén
    a partir de líneas aprobadas de una requisición, vinculando supply.chain.allocation.
    """
    _name = 'requisition.delivery.wizard'
    _description = 'Asistente de Despacho y Entrega de Requisición'

    requisition_id = fields.Many2one(
        'requisitions',
        string="Requisición",
        required=True,
        readonly=True
    )
    company_id = fields.Many2one(
        'res.company',
        related='requisition_id.company_id',
        string="Empresa",
        readonly=True
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        related='requisition_id.warehouse_id',
        string="Almacén",
        readonly=True
    )
    location_id = fields.Many2one(
        'stock.location',
        string="Ubicación Origen",
        required=True,
        check_company=True
    )
    location_dest_id = fields.Many2one(
        'stock.location',
        string="Ubicación Destino",
        required=True,
        check_company=True
    )
    picking_type_id = fields.Many2one(
        'stock.picking.type',
        string="Tipo de Operación",
        required=True,
        check_company=True
    )
    line_ids = fields.One2many(
        'requisition.delivery.wizard.line',
        'wizard_id',
        string="Partidas a Despachar"
    )
    has_stock_warnings = fields.Boolean(
        string="Tiene Faltantes de Stock",
        compute='_compute_has_stock_warnings'
    )

    @api.depends('line_ids.has_insufficient_stock')
    def _compute_has_stock_warnings(self):
        for wiz in self:
            wiz.has_stock_warnings = any(l.has_insufficient_stock for l in wiz.line_ids)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        if not active_id:
            return res

        requisition = self.env['requisitions'].browse(active_id)
        if not requisition.exists():
            return res

        res['requisition_id'] = requisition.id
        wh = requisition.warehouse_id
        if wh:
            res['location_id'] = wh.lot_stock_id.id
            out_type = wh.out_type_id
            if out_type:
                res['picking_type_id'] = out_type.id
                dest_loc = out_type.default_location_dest_id
                if not dest_loc or dest_loc.usage == 'internal':
                    customer_loc = self.env.ref('stock.stock_location_customers', raise_if_not_found=False)
                    dest_loc = customer_loc or dest_loc
                res['location_dest_id'] = dest_loc.id if dest_loc else False

        # Precargar líneas de materiales, herramientas y equipos con saldo pendiente
        lines_data = []
        eligible_lines = requisition.line_ids.filtered(
            lambda l: l.line_type != 'service' and l.qty_pending > 0 and l.line_state not in ('done', 'cancel')
        )
        for line in eligible_lines:
            lines_data.append((0, 0, {
                'requisition_line_id': line.id,
                'qty_to_deliver': line.qty_pending,
            }))
        res['line_ids'] = lines_data
        return res

    def action_confirm(self):
        """Genera el albarán de entrega física con sus movimientos y asignaciones vinculadas."""
        self.ensure_one()
        lines_to_deliver = self.line_ids.filtered(lambda l: l.qty_to_deliver > 0)
        if not lines_to_deliver:
            raise UserError(_("Debe especificar una cantidad a surtir mayor a cero en al menos una línea."))

        for line in lines_to_deliver:
            if line.qty_to_deliver > line.qty_pending:
                raise ValidationError(
                    _("La cantidad a surtir para '%s' (%s) supera el saldo pendiente (%s).") % (
                        line.product_id.display_name, line.qty_to_deliver, line.qty_pending
                    )
                )

        emp = self.requisition_id.employee_id
        partner = emp.work_contact_id or emp.user_id.partner_id if emp else False

        # Traspasar notas generales de la requisición al albarán en formato HTML nativo
        note_html = f"<p>{html_escape(self.requisition_id.notes)}</p>" if self.requisition_id.notes else False

        picking_vals = {
            'picking_type_id': self.picking_type_id.id,
            'location_id': self.location_id.id,
            'location_dest_id': self.location_dest_id.id,
            'origin': self.requisition_id.name,
            'company_id': self.company_id.id,
            'partner_id': partner.id if partner else False,
            'note': note_html,
        }
        picking = self.env['stock.picking'].create(picking_vals)

        moves_vals = []
        lines_mapping = []
        for w_line in lines_to_deliver:
            req_line = w_line.requisition_line_id
            move_val = {
                'product_id': req_line.product_id.id,
                'product_uom_qty': w_line.qty_to_deliver,
                'product_uom': req_line.product_uom_id.id,
                'picking_id': picking.id,
                'location_id': self.location_id.id,
                'location_dest_id': self.location_dest_id.id,
                'company_id': self.company_id.id,
            }
            if req_line.note:
                move_val['note'] = req_line.note
            moves_vals.append(move_val)
            lines_mapping.append((w_line, req_line))

        moves = self.env['stock.move'].create(moves_vals)

        # Creación en lote en la matriz de asignación requisition.allocation
        alloc_vals_list = []
        for (w_line, req_line), move in zip(lines_mapping, moves):
            supply_type = req_line.supply_type or ('resg' if req_line.line_type in ('tool', 'equipment') else 'consumption')
            # Vincular con la asignación previa de abastecimiento (inbound) si existió
            inbound_alloc = req_line.allocation_ids.filtered(
                lambda a: a.stage == 'inbound' and a.state in ('in_progress', 'done')
            )[:1]
            alloc_vals_list.append({
                'company_id': self.company_id.id,
                'product_id': req_line.product_id.id,
                'product_uom_id': req_line.product_uom_id.id,
                'requested_qty': w_line.qty_to_deliver,
                'delivered_qty': 0.0,
                'supply_type': supply_type,
                'stage': 'outbound',
                'parent_allocation_id': inbound_alloc.id if inbound_alloc else False,
                'stock_move_id': move.id,
                'requisition_line_id': req_line.id,
                'state': 'in_progress',
            })
        self.env['requisition.allocation'].create(alloc_vals_list)

        picking.action_confirm()

        # Preasignar las cantidades a entregar en cada movimiento
        for (w_line, _req_line), move in zip(lines_mapping, moves):
            move.quantity = w_line.qty_to_deliver

        # Ejecutar verificación de disponibilidad y asignación nativa de Odoo
        picking.action_assign()

        if self.requisition_id.state == 'approved':
            self.requisition_id.action_in_progress()

        self.requisition_id.message_post(
            body=Markup(_(
                "Se generó la entrega <strong>%s</strong> para el solicitante "
                "con <strong>%s</strong> partida(s)."
            )) % (escape(picking.name), len(lines_to_deliver)),
            subtype_xmlid="mail.mt_note"
        )

        if self.has_stock_warnings:
            self.requisition_id.message_post(
                body=Markup(_(
                    "<strong>Aviso Informativo de Disponibilidad:</strong> La entrega <strong>%s</strong> fue generada "
                    "con partidas que exceden el stock disponible actual en la ubicación. El albarán queda registrado en espera "
                    "de disponibilidad física sin bloquear el flujo."
                )) % escape(picking.name),
                subtype_xmlid="mail.mt_note"
            )

        return {
            'name': _('Albarán de Despacho'),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': picking.id,
        }


class RequisitionDeliveryWizardLine(models.TransientModel):
    _name = 'requisition.delivery.wizard.line'
    _description = 'Línea del Asistente de Despacho y Entrega'

    wizard_id = fields.Many2one(
        'requisition.delivery.wizard',
        string="Asistente",
        required=True,
        ondelete='cascade'
    )
    requisition_line_id = fields.Many2one(
        'requisitions.line',
        string="Línea de Requisición",
        required=True
    )
    product_id = fields.Many2one(
        'product.product',
        related='requisition_line_id.product_id',
        string="Producto",
        readonly=True
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        related='requisition_line_id.product_uom_id',
        string="Unidad de Medida",
        readonly=True
    )
    qty_requested = fields.Float(
        related='requisition_line_id.product_qty',
        string="Solicitado",
        readonly=True
    )
    qty_pending = fields.Float(
        related='requisition_line_id.qty_pending',
        string="Saldo Pendiente",
        readonly=True
    )
    qty_available_stock = fields.Float(
        string="Stock Disponible",
        compute='_compute_stock_availability',
        digits='Product Unit of Measure'
    )
    has_insufficient_stock = fields.Boolean(
        string="Sin Stock Suficiente",
        compute='_compute_stock_availability'
    )
    qty_to_deliver = fields.Float(
        string="Cantidad a Surtir",
        required=True,
        default=0.0
    )

    @api.depends('product_id', 'wizard_id.location_id', 'qty_to_deliver')
    def _compute_stock_availability(self):
        for line in self:
            loc = line.wizard_id.location_id
            if line.product_id and loc:
                quants = self.env['stock.quant'].search([
                    ('product_id', '=', line.product_id.id),
                    ('location_id', 'child_of', loc.id),
                ])
                avail = sum(quants.mapped('quantity')) - sum(quants.mapped('reserved_quantity'))
                line.qty_available_stock = max(0.0, avail)
            else:
                line.qty_available_stock = 0.0
            line.has_insufficient_stock = (line.qty_to_deliver > line.qty_available_stock)
