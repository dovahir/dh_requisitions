# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class StockPickingType(models.Model):
    """
    Extensión de stock.picking.type para identificar tipos de operación
    destinados a Solicitudes de Suministro (Requisiciones) y asegurar la
    asignación de su recepción interna predeterminada.
    """
    _inherit = 'stock.picking.type'

    is_requisition_operation = fields.Boolean(
        string='Operación para Requisiciones',
        default=False,
        help='Indica si este tipo de operación interna se utiliza para transferencias '
             'de solicitudes de suministro entre almacenes base y obras.'
    )

    @api.constrains('is_requisition_operation', 'default_dest_picking_type', 'code')
    def _check_requisition_operation_config(self):
        """
        Garantiza la consistencia básica del flujo logístico de requisiciones:
        - Solo aplica a transferencias internas (código 'internal').
        - Exige definir la recepción interna predeterminada en destino.
        """
        for record in self:
            if record.is_requisition_operation:
                if record.code != 'internal':
                    raise ValidationError(_(
                        "Solo los tipos de operación interna pueden ser configurados "
                        "como operación para Requisiciones."
                    ))
                if not record.default_dest_picking_type:
                    raise ValidationError(_(
                        "Al marcar un tipo de operación como exclusivo de Requisiciones, "
                        "es obligatorio definir el campo 'Recepción Predeterminada' con un tipo de operación interna."
                    ))
                if record.default_dest_picking_type.code != 'internal':
                    raise ValidationError(_(
                        "La 'Recepción Predeterminada' para requisiciones debe ser de tipo transferencia interna."
                    ))
