# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from markupsafe import Markup, escape


class RequisitionSupplyRequestAssignPendingWizard(models.TransientModel):
    """
    Asistente para asignar partidas seleccionadas del pool de Pendientes de Envío
    a una Solicitud de Suministro existente o a una nueva solicitud.
    """
    _name = 'requisition.supply.request.assign.pending.wizard'
    _description = 'Asignar Pendientes de Envío a Solicitud de Suministro'

    action_type = fields.Selection([
        ('existing', 'Asignar a Solicitud Existente (Borrador)'),
        ('new', 'Generar Nueva Solicitud de Suministro'),
    ], string="Acción", default='existing', required=True)

    company_id = fields.Many2one(
        'res.company',
        string="Empresa",
        required=True,
        default=lambda self: self.env.company
    )
    warehouse_dest_id = fields.Many2one(
        'stock.warehouse',
        string="Almacén Destino (Obra)",
        required=True,
        readonly=True,
        help="Almacén de obra de las partidas seleccionadas."
    )
    warehouse_origin_id = fields.Many2one(
        'stock.warehouse',
        string="Almacén Origen (Base)",
        required=True,
        check_company=True
    )
    supply_request_id = fields.Many2one(
        'requisition.supply.request',
        string="Solicitud de Suministro Destino",
        domain="[('warehouse_dest_id', '=', warehouse_dest_id), ('warehouse_origin_id', '=', warehouse_origin_id), ('state', '=', 'draft'), ('is_blocked', '=', False)]"
    )
    notes = fields.Char(
        string="Notas / Observaciones"
    )
    @api.model
    def _default_pending_lines(self):
        active_ids = self.env.context.get('active_ids', [])
        if active_ids:
            lines = self.env['requisition.supply.request.line'].browse(active_ids)
            return lines.filtered(lambda l: not l.request_id and l.state not in ('cancel', 'done')).ids
        return []

    pending_line_ids = fields.Many2many(
        'requisition.supply.request.line',
        'req_supply_assign_pending_line_rel',
        'wizard_id',
        'line_id',
        string="Partidas Pendientes Seleccionadas",
        default=_default_pending_lines
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_ids = self.env.context.get('active_ids', [])
        if not active_ids:
            return res

        lines = self.env['requisition.supply.request.line'].browse(active_ids)
        valid_lines = lines.filtered(lambda l: not l.request_id and l.state not in ('cancel', 'done'))
        if not valid_lines:
            raise UserError(_("No hay partidas pendientes válidas seleccionadas."))

        dest_warehouses = valid_lines.mapped('warehouse_dest_id').filtered(bool)
        if len(dest_warehouses) > 1:
            raise UserError(_(
                "Las partidas seleccionadas corresponden a distintos almacenes destino de obra (%s).\n"
                "Por favor, filtre o seleccione partidas que compartan el mismo destino."
            ) % ', '.join(dest_warehouses.mapped('name')))

        dest_wh = dest_warehouses[:1]
        res['warehouse_dest_id'] = dest_wh.id if dest_wh else False
        res['pending_line_ids'] = [(6, 0, valid_lines.ids)]

        company = valid_lines[0].company_id or self.env.company
        res['company_id'] = company.id

        base_wh = self.env['stock.warehouse'].search([
            ('company_id', '=', company.id),
            '|', ('code', '=', 'BASE'), ('name', 'ilike', 'base')
        ], limit=1)
        if base_wh:
            res['warehouse_origin_id'] = base_wh.id
            existing_sr = self.env['requisition.supply.request'].search([
                ('company_id', '=', company.id),
                ('warehouse_dest_id', '=', dest_wh.id),
                ('warehouse_origin_id', '=', base_wh.id),
                ('state', '=', 'draft'),
                ('is_blocked', '=', False),
            ], limit=1, order='id desc')
            if existing_sr:
                res['supply_request_id'] = existing_sr.id

        return res

    def action_confirm(self):
        self.ensure_one()
        if not self.pending_line_ids:
            raise UserError(_("No hay partidas seleccionadas para asignar."))

        if self.action_type == 'existing':
            if not self.supply_request_id:
                raise UserError(_("Debe seleccionar una Solicitud de Suministro existente o cambiar a la opción 'Generar Nueva Solicitud'."))
            sr = self.supply_request_id
        else:
            sr_vals = {
                'company_id': self.company_id.id,
                'warehouse_origin_id': self.warehouse_origin_id.id,
                'warehouse_dest_id': self.warehouse_dest_id.id,
                'notes': self.notes or _("Generado desde asignación masiva de Pendientes de Envío"),
            }
            # Vincular requisiciones asociadas
            req_ids = self.pending_line_ids.mapped('requisition_id').ids
            if req_ids:
                sr_vals['requisition_ids'] = [(6, 0, req_ids)]
            sr = self.env['requisition.supply.request'].create(sr_vals)

        # Asignar a la SR
        self.pending_line_ids.write({'request_id': sr.id})
        for alloc in self.pending_line_ids.mapped('allocation_ids').filtered(lambda a: a.state != 'cancel'):
            alloc._sync_origin_document_status()

        sr.message_post(
            body=Markup(_(
                "Se asignaron <strong>%d</strong> partida(s) desde <strong>Pendientes de Envío</strong>."
            )) % len(self.pending_line_ids),
            subtype_xmlid="mail.mt_note"
        )

        return {
            'name': _('Solicitud de Suministro'),
            'type': 'ir.actions.act_window',
            'res_model': 'requisition.supply.request',
            'view_mode': 'form',
            'res_id': sr.id,
        }
