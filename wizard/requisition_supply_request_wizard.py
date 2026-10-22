# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from markupsafe import Markup, escape


class RequisitionSupplyRequestWizard(models.TransientModel):
    """
    Asistente interactivo para seleccionar partidas de una requisición
    y agregarlas a una Solicitud de Suministro (SR) activa o nueva.
    """
    _name = 'requisition.supply.request.wizard'
    _description = 'Asistente de Solicitud de Suministro'

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
    warehouse_dest_id = fields.Many2one(
        'stock.warehouse',
        related='requisition_id.warehouse_id',
        string="Almacén Destino (Obra)",
        readonly=True
    )
    warehouse_origin_id = fields.Many2one(
        'stock.warehouse',
        string="Almacén Origen (Base)",
        required=True,
        check_company=True,
        help="Almacén base/central desde donde se surtirá el material a la obra."
    )
    supply_request_id = fields.Many2one(
        'requisition.supply.request',
        string="Solicitud de Suministro",
        required=True,
        domain="[('company_id', '=', company_id), ('warehouse_dest_id', '=', warehouse_dest_id), ('state', '=', 'draft'), ('is_blocked', '=', False)]",
        help="Seleccione una Solicitud de Suministro activa"
    )
    notes = fields.Char(
        string="Notas / Observaciones"
    )
    line_ids = fields.One2many(
        'requisition.supply.request.wizard.line',
        'wizard_id',
        string="Partidas a solicitar"
    )

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

        # Localizar almacén BASE central de la compañía
        base_wh = self.env['stock.warehouse'].search([
            ('company_id', '=', requisition.company_id.id),
            '|', ('code', '=', 'BASE'), ('name', 'ilike', 'base')
        ], limit=1)
        if base_wh:
            res['warehouse_origin_id'] = base_wh.id

            # Buscar si ya existe una SR activa en borrador y no bloqueada para esta ruta
            existing_sr = self.env['requisition.supply.request'].search([
                ('company_id', '=', requisition.company_id.id),
                ('warehouse_dest_id', '=', requisition.warehouse_id.id),
                ('warehouse_origin_id', '=', base_wh.id),
                ('state', '=', 'draft'),
                ('is_blocked', '=', False),
            ], limit=1, order='id desc')
            if existing_sr:
                res['supply_request_id'] = existing_sr.id

        # Precargar partidas físicas con cantidad pendiente de suministrar
        lines_data = []
        eligible_lines = requisition.line_ids.filtered(
            lambda l: l.line_type != 'service' and l._get_qty_unsupplied() > 0 and l.line_state not in ('done', 'cancel')
        )
        for line in eligible_lines:
            lines_data.append((0, 0, {
                'selected': True,
                'requisition_line_id': line.id,
                'product_qty': line._get_qty_unsupplied(),
                'notes': line.note or '',
            }))
        res['line_ids'] = lines_data
        return res

    def action_confirm(self):
        """Agrega las partidas seleccionadas a la Solicitud de Suministro activa y emite notificación informativa."""
        self.ensure_one()
        if not self.supply_request_id:
            raise UserError(_("Debe seleccionar una Solicitud de Suministro activa y abierta (en proceso y sin corte)."))

        if self.supply_request_id.is_blocked:
            raise UserError(_("La Solicitud de Suministro seleccionada se encuentra bloqueada por corte de almacén."))

        if self.supply_request_id.state != 'draft':
            raise UserError(_("Solo se pueden agregar partidas a solicitudes de suministro abiertas (en proceso)."))

        selected_lines = self.line_ids.filtered(lambda l: l.selected and l.product_qty > 0)
        if not selected_lines:
            raise UserError(_("Debe marcar y especificar una cantidad mayor a cero en al menos una partida."))

        for line in selected_lines:
            unsupplied = line.requisition_line_id._get_qty_unsupplied()
            if line.product_qty > unsupplied:
                raise ValidationError(
                    _("La cantidad solicitada para '%s' (%s) no puede ser mayor a la cantidad por suministrar (%s).") % (
                        line.product_id.display_name, line.product_qty, unsupplied
                    )
                )

        supply_request = self.supply_request_id
        if self.requisition_id.id not in supply_request.requisition_ids.ids:
            supply_request.requisition_ids = [(4, self.requisition_id.id)]

        for w_line in selected_lines:
            req_line = w_line.requisition_line_id
            self.env['requisition.supply.request.line'].create({
                'request_id': supply_request.id,
                'requisition_line_id': req_line.id,
                'product_id': req_line.product_id.id,
                'product_qty': w_line.product_qty,
                'notes': w_line.notes or req_line.note or '',
            })

        if self.requisition_id.state == 'approved':
            self.requisition_id.action_in_progress()

        self.requisition_id.message_post(
            body=Markup(_(
                "Se agregaron <strong>%s</strong> partida(s) a la Solicitud de Suministro <strong>%s</strong>."
            )) % (len(selected_lines), escape(supply_request.name)),
            subtype_xmlid="mail.mt_note"
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Partidas Agregadas'),
                'message': _('Se agregaron exitosamente %s partida(s) a la Solicitud de Suministro %s.') % (
                    len(selected_lines), supply_request.name
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }


class RequisitionSupplyRequestWizardLine(models.TransientModel):
    _name = 'requisition.supply.request.wizard.line'
    _description = 'Línea de Asistente de Solicitud de Suministro'

    wizard_id = fields.Many2one(
        'requisition.supply.request.wizard',
        string="Asistente",
        required=True,
        ondelete='cascade'
    )
    selected = fields.Boolean(
        string="Solicitar",
        default=True
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
        string="UdM",
        readonly=True
    )
    qty_pending = fields.Float(
        related='requisition_line_id.qty_pending',
        string="Cantidad Pendiente",
        readonly=True
    )
    product_qty = fields.Float(
        string="Cantidad a Solicitar",
        required=True,
        default=1.0,
        digits='Product Unit of Measure'
    )
    notes = fields.Char(
        string="Notas"
    )
