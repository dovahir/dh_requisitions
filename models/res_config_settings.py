# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ResCompany(models.Model):
    """
    Extensión de res.company para almacenar las parametrizaciones
    y políticas del módulo de Requisiciones de Personal y Materiales.
    """
    _inherit = 'res.company'

    def _default_requisition_default_warehouse_origin_id(self):
        """Asigna automáticamente el primer almacén de la compañía como almacén base predeterminado si existe."""
        if not self or not self.id:
            return False
        return self.env['stock.warehouse'].search([('company_id', '=', self.id)], limit=1)

    requisition_default_warehouse_origin_id = fields.Many2one(
        'stock.warehouse',
        string='Almacén Base Predeterminado',
        default=_default_requisition_default_warehouse_origin_id,
        check_company=True,
        help='Almacén base / central sugerido automáticamente en las solicitudes de suministro hacia obras.'
    )
    requisition_allow_auto_approval = fields.Boolean(
        string='Auto-aprobación para Aprobadores',
        default=False,
        help='Si está activo, cuando un usuario con rol de Aprobador o Administrador confirme una requisición, '
             'esta pasará directamente a estado Aprobado sin requerir un paso adicional.'
    )


class ResConfigSettings(models.TransientModel):
    """
    Extensión de los ajustes generales del sistema para exponer
    las configuraciones específicas de Requisiciones en su propia sección.
    """
    _inherit = 'res.config.settings'

    requisition_default_warehouse_origin_id = fields.Many2one(
        'stock.warehouse',
        related='company_id.requisition_default_warehouse_origin_id',
        string='Almacén Origen Predeterminado',
        readonly=False
    )
    requisition_allow_auto_approval = fields.Boolean(
        related='company_id.requisition_allow_auto_approval',
        string='Auto-aprobación para Aprobadores',
        readonly=False
    )

    @api.model
    def default_get(self, fields_list):
        """Asegura que el Almacén Base Predeterminado cargue automáticamente el almacén de la compañía si aún no se ha guardado."""
        res = super().default_get(fields_list)
        if 'requisition_default_warehouse_origin_id' in fields_list and not res.get('requisition_default_warehouse_origin_id'):
            company = self.env.company
            wh = company.requisition_default_warehouse_origin_id or self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
            if wh:
                res['requisition_default_warehouse_origin_id'] = wh.id
        return res

    def action_open_requisition_warehouse_setup(self):
        """Abre el asistente de configuración de rutas desde el Almacén Base Predeterminado."""
        self.ensure_one()
        warehouse = self.requisition_default_warehouse_origin_id
        if not warehouse:
            raise UserError(_("Debe configurar y guardar un Almacén Base Predeterminado antes de configurar las rutas."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Configurar Rutas de Suministro para Requisiciones'),
            'res_model': 'requisition.warehouse.setup.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_warehouse_origin_id': warehouse.id,
            },
        }
