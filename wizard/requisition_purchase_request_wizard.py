# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class RequisitionPurchaseRequestWizard(models.TransientModel):
    """
    Asistente interactivo para seleccionar partidas de una requisición
    y generar líneas de Solicitud de Compra (Insumos) con registro de allocation.
    """
    _name = 'requisition.purchase.request.wizard'
    _description = 'Asistente de Solicitud de Compra'

    requisition_id = fields.Many2one(
        'requisitions',
        string="Requisición",
        required=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        'res.company',
        related='requisition_id.company_id',
        string="Empresa",
        readonly=True,
    )
    line_ids = fields.One2many(
        'requisition.purchase.request.wizard.line',
        'wizard_id',
        string="Partidas por Solicitar",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id') or self.env.context.get('default_requisition_id')
        if not active_id:
            return res

        requisition = self.env['requisitions'].browse(active_id)
        if not requisition.exists():
            return res

        res['requisition_id'] = requisition.id

        # Precargar partidas con saldo pendiente de comprar
        eligible_lines = requisition.line_ids.filtered(
            lambda l: l._get_qty_unpurchased() > 0 and l.line_state not in ('done', 'cancel')
        )
        lines_data = []
        for line in eligible_lines:
            unpurchased = line._get_qty_unpurchased()
            lines_data.append((0, 0, {
                'selected': True,
                'requisition_line_id': line.id,
                'qty_unpurchased': unpurchased,
                'product_qty': unpurchased,
                'priority': line.priority or 'normal',
                'notes': line.note or '',
            }))
        res['line_ids'] = lines_data
        return res

    def action_confirm(self):
        """Genera líneas de solicitud de compra para las partidas seleccionadas."""
        self.ensure_one()
        selected_lines = self.line_ids.filtered(lambda l: l.selected and l.product_qty > 0)
        if not selected_lines:
            raise UserError(_("Debe marcar y especificar una cantidad mayor a cero en al menos una partida."))

        for w_line in selected_lines:
            unpurchased = w_line.requisition_line_id._get_qty_unpurchased()
            if w_line.product_qty > unpurchased:
                raise ValidationError(
                    _("La cantidad a comprar para '%s' (%s) no puede exceder el saldo pendiente (%s).") % (
                        w_line.product_id.display_name, w_line.product_qty, unpurchased
                    )
                )

        lines_to_process = [
            {
                'line': w_line.requisition_line_id,
                'qty': w_line.product_qty,
                'priority': w_line.priority or 'normal',
                'notes': w_line.notes or w_line.requisition_line_id.note or '',
            }
            for w_line in selected_lines
        ]

        created_lines = self.requisition_id._create_purchase_request_lines(lines_to_process)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Solicitud de Compra Generada'),
                'message': _('Se generaron exitosamente %s partida(s) de Solicitud de Compra para la requisición %s.') % (
                    len(created_lines), self.requisition_id.name
                ),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }


class RequisitionPurchaseRequestWizardLine(models.TransientModel):
    _name = 'requisition.purchase.request.wizard.line'
    _description = 'Línea de Asistente de Solicitud de Compra'

    wizard_id = fields.Many2one(
        'requisition.purchase.request.wizard',
        string="Asistente",
        required=True,
        ondelete='cascade',
    )
    selected = fields.Boolean(
        string="Solicitar",
        default=True,
    )
    requisition_line_id = fields.Many2one(
        'requisitions.line',
        string="Partida de Requisición",
        required=True,
    )
    product_id = fields.Many2one(
        'product.product',
        related='requisition_line_id.product_id',
        string="Producto",
        readonly=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        related='requisition_line_id.product_uom_id',
        string="UdM",
        readonly=True,
    )
    qty_unpurchased = fields.Float(
        string="Saldo por Comprar",
        readonly=True,
        digits='Product Unit of Measure',
    )
    product_qty = fields.Float(
        string="Cantidad a Comprar",
        required=True,
        default=1.0,
        digits='Product Unit of Measure',
    )
    priority = fields.Selection(
        [
            ('normal', 'Normal'),
            ('urgent', 'Urgente'),
        ],
        string="Prioridad",
        default='normal',
    )
    notes = fields.Char(
        string="Notas / Observaciones",
    )
