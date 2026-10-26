# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class RequisitionWarehouseSetupWizard(models.TransientModel):
    """
    Asistente para aprovisionar y encadenar automáticamente los tipos de operación
    y ubicaciones de tránsito dedicadas para el flujo de suministro entre un almacén base
    y uno o más almacenes de obra (destino).
    """
    _name = 'requisition.warehouse.setup.wizard'
    _description = 'Asistente de Configuración de Rutas de Suministro para Requisiciones'

    warehouse_origin_id = fields.Many2one(
        'stock.warehouse',
        string='Almacén Base (Origen)',
        required=True,
        readonly=True,
        help="Almacén base central desde donde se despacharán los suministros."
    )
    warehouse_dest_ids = fields.Many2many(
        'stock.warehouse',
        string='Almacenes de Obra (Destino)',
        required=True,
        domain="[('id', '!=', warehouse_origin_id)]",
        help="Seleccione los almacenes de obra que recibirán suministros desde este almacén base."
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'warehouse_origin_id' in fields_list and not res.get('warehouse_origin_id'):
            company = self.env.company
            wh = company.requisition_default_warehouse_origin_id
            if wh:
                res['warehouse_origin_id'] = wh.id
        return res

    def action_configure_routes(self):
        """
        Crea o actualiza para cada almacén destino seleccionado:
        1. Ubicación de tránsito dedicada: '{Nombre Base} - {Nombre Obra}'.
        2. Tipo de operación de recepción interna en obra (Tránsito -> Existencias Obra):
           - Nombre: 'Recepción desde {Nombre Base}'
           - Método de reservación: Manual ('manual')
           - Devolución predeterminada: Traslado interno del almacén de obra
        3. Tipo de operación de salida interna en base (Existencias Base -> Tránsito):
           - Nombre: 'Enviar a {Nombre Obra}'
           - Método de reservación: Manual ('manual')
           - Devolución predeterminada: Traslado interno del almacén base
           - is_requisition_operation=True
        4. Vinculación de recepción predeterminada (default_dest_picking_type).
        """
        self.ensure_one()
        origin_wh = self.warehouse_origin_id
        if not origin_wh:
            raise UserError(_("No se ha definido un almacén base. Por favor configure el Almacén Base Predeterminado en los Ajustes."))
        if not self.warehouse_dest_ids:
            raise UserError(_("Debe seleccionar al menos un almacén de obra."))

        company = origin_wh.company_id
        configured_dest_names = []

        for dest_wh in self.warehouse_dest_ids:
            # 1. Ubicación de tránsito dedicada
            transit_loc_name = f"{origin_wh.name} - {dest_wh.name}"
            transit_location = self.env['stock.location'].search([
                ('name', '=', transit_loc_name),
                ('usage', '=', 'transit'),
                ('company_id', 'in', [company.id, False]),
            ], limit=1)

            if not transit_location:
                parent_transit = company.internal_transit_location_id
                parent_id = parent_transit.location_id.id if parent_transit and parent_transit.location_id else (parent_transit.id if parent_transit else False)
                transit_location = self.env['stock.location'].create({
                    'name': transit_loc_name,
                    'usage': 'transit',
                    'location_id': parent_id,
                    'company_id': company.id,
                })

            # 2. Tipo de operación de recepción en almacén de obra
            picking_type_dest = self.env['stock.picking.type'].search([
                ('warehouse_id', '=', dest_wh.id),
                ('code', '=', 'internal'),
                ('default_location_src_id', '=', transit_location.id),
                ('default_location_dest_id', '=', dest_wh.lot_stock_id.id),
            ], limit=1)

            dest_vals = {
                'name': _("Recepción desde %s") % origin_wh.name,
                'use_existing_lots': True,
                'use_create_lots': False,
                'reservation_method': 'manual',
                'return_picking_type_id': dest_wh.int_type_id.id if dest_wh.int_type_id else False,
            }

            if not picking_type_dest:
                seq_code_dest = f"REC-{origin_wh.code[:3] or 'BAS'}".upper()
                dest_vals.update({
                    'code': 'internal',
                    'sequence_code': seq_code_dest,
                    'warehouse_id': dest_wh.id,
                    'company_id': dest_wh.company_id.id,
                    'default_location_src_id': transit_location.id,
                    'default_location_dest_id': dest_wh.lot_stock_id.id,
                })
                picking_type_dest = self.env['stock.picking.type'].create(dest_vals)
            else:
                picking_type_dest.write(dest_vals)

            # 3. Tipo de operación de salida en almacén base
            picking_type_origin = self.env['stock.picking.type'].search([
                ('warehouse_id', '=', origin_wh.id),
                ('code', '=', 'internal'),
                ('is_requisition_operation', '=', True),
                ('default_location_dest_id', '=', transit_location.id),
            ], limit=1)

            if not picking_type_origin:
                picking_type_origin = self.env['stock.picking.type'].search([
                    ('warehouse_id', '=', origin_wh.id),
                    ('code', '=', 'internal'),
                    ('default_dest_picking_type', '=', picking_type_dest.id),
                ], limit=1)

            seq_code_origin = f"SUM-{dest_wh.code[:3] or 'OBR'}".upper()
            origin_vals = {
                'name': _("Enviar a %s") % dest_wh.name,
                'is_requisition_operation': True,
                'default_location_src_id': origin_wh.lot_stock_id.id,
                'default_location_dest_id': transit_location.id,
                'default_dest_picking_type': picking_type_dest.id,
                'use_existing_lots': True,
                'use_create_lots': False,
                'reservation_method': 'manual',
                'return_picking_type_id': origin_wh.int_type_id.id if origin_wh.int_type_id else False,
            }

            if picking_type_origin:
                picking_type_origin.write(origin_vals)
            else:
                origin_vals.update({
                    'code': 'internal',
                    'sequence_code': seq_code_origin,
                    'warehouse_id': origin_wh.id,
                    'company_id': origin_wh.company_id.id,
                })
                picking_type_origin = self.env['stock.picking.type'].create(origin_vals)

            configured_dest_names.append(dest_wh.name)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Rutas de Suministro Configuradas"),
                'message': _(
                    "Se configuraron exitosamente las rutas de suministro entre el almacén base '%(base)s' "
                    "y las obras: %(obras)s.",
                    base=origin_wh.name,
                    obras=", ".join(configured_dest_names)
                ),
                'sticky': False,
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
