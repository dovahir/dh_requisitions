# -*- coding: utf-8 -*-
from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class RequisitionSupplyTriggerPurchaseWizard(models.TransientModel):
    """
    Asistente interactivo para configurar y disparar una Solicitud de Compra
    desde una partida de Solicitud de Suministro con déficit de existencias en Base.
    """
    _name = 'requisition.supply.trigger.purchase.wizard'
    _description = 'Asistente para Solicitar Compra desde Suministro'

    supply_line_id = fields.Many2one(
        'requisition.supply.request.line',
        string="Partida de Suministro",
        required=True,
        readonly=True
    )
    product_id = fields.Many2one(
        'product.product',
        related='supply_line_id.product_id',
        string="Producto",
        readonly=True
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        related='supply_line_id.product_uom_id',
        string="Unidad de Medida",
        readonly=True
    )
    product_qty = fields.Float(
        related='supply_line_id.product_qty',
        string="Demanda en Suministro",
        readonly=True
    )
    qty_base_available = fields.Float(
        related='supply_line_id.qty_base_available',
        string="Stock Disponible en Base",
        readonly=True
    )
    qty_to_send = fields.Float(
        string="Cantidad a Enviar en esta Entrega",
        digits='Product Unit of Measure',
        help="Cantidad que se mantendrá en esta partida para enviar desde el almacén base en el albarán actual."
    )
    qty_to_purchase = fields.Float(
        string="Cantidad a Solicitar en Compra",
        required=True,
        default=1.0,
        digits='Product Unit of Measure',
        help="Cantidad exacta que se solicitará adquirir al departamento de compras."
    )
    create_pending_supply = fields.Boolean(
        string="Crear Partida en 'Pendientes de Envío' para Obra",
        default=True,
        help="Si se marca, se generará una partida en Pendientes de Envío para la obra destino vinculada a esta compra, "
             "permitiendo despacharla en un traslado posterior cuando la mercancía llegue a Base."
    )
    notes = fields.Text(
        string="Justificación / Observaciones de Compra"
    )

    def action_confirm(self):
        """Ejecuta la creación de la partida de compra y opcionalmente la de pendientes de envío."""
        self.ensure_one()
        line = self.supply_line_id
        if not line:
            raise UserError(_("No se encontró la partida de suministro de origen."))

        if not (self.env.user.has_group('dh_requisitions.group_stock_request_employee') or
                self.env.user.has_group('purchase.group_purchase_user') or
                self.env.user.has_group('dh_requisitions.group_requisitions_manager')):
            raise UserError(_("Para disparar una solicitud de compra desde suministro debe pertenecer a Empleados Almacén Base, Compras o Administrador."))

        if self.qty_to_purchase <= 0:
            raise ValidationError(_("La cantidad a solicitar en compra debe ser mayor a cero."))

        if self.qty_to_send < 0:
            raise ValidationError(_("La cantidad a enviar no puede ser negativa."))

        if self.qty_to_send > line.product_qty:
            raise ValidationError(_("La cantidad a enviar no puede ser mayor que la cantidad solicitada en la partida."))

        # Validar si ya existe partida de compra activa
        existing_pr = self.env['requisition.purchase.request.line'].search([
            ('product_id', '=', line.product_id.id),
            ('requisition_line_id', '=', line.requisition_line_id.id if line.requisition_line_id else False),
            ('state', 'not in', ('cancel', 'purchased')),
        ], limit=1)
        if existing_pr:
            raise UserError(_("Ya existe una Solicitud de Compra activa (%s) para este requerimiento.") % existing_pr.display_name)

        # 1. Crear la partida en Solicitud de Compra
        pr_vals = {
            'requisition_id': line.requisition_id.id if line.requisition_id else False,
            'requisition_line_id': line.requisition_line_id.id if line.requisition_line_id else False,
            'product_id': line.product_id.id,
            'product_qty': self.qty_to_purchase,
            'origin_supply_request_line_id': line.id,
            'notes': self.notes or _("Disparado desde Suministro %s por falta de existencias en Base.") % (line.request_id.name if line.request_id else ''),
            'state': 'pending',
        }
        pr_line = self.env['requisition.purchase.request.line'].create(pr_vals)

        # 2. Manejar creación de partida en Pendientes de Envío si fue solicitada
        pending_sr_line = False
        if self.create_pending_supply:
            pending_sr_line = self.env['requisition.supply.request.line'].with_context(skip_auto_allocation=True).create({
                'request_id': False,
                'warehouse_dest_id': line.warehouse_dest_id.id,
                'warehouse_origin_id': line.warehouse_origin_id.id,
                'product_id': line.product_id.id,
                'product_qty': self.qty_to_purchase,
                'qty_to_send': self.qty_to_purchase,
                'requisition_line_id': line.requisition_line_id.id if line.requisition_line_id else False,
                'notes': _("Partida pendiente generada desde compra vinculada a solicitud %s") % (line.request_id.name if line.request_id else ''),
                'state': 'pending_assignment',
            })

        # 3. Asignación unificada (requisition.allocation)
        supply_type = line.requisition_line_id.supply_type if line.requisition_line_id else 'consumption'
        company = line.company_id or self.env.company
        alloc = self.env['requisition.allocation'].create({
            'company_id': company.id,
            'product_id': line.product_id.id,
            'product_uom_id': line.product_uom_id.id,
            'requested_qty': self.qty_to_purchase,
            'delivered_qty': 0.0,
            'supply_type': supply_type,
            'stage': 'inbound',
            'supply_request_line_id': pending_sr_line.id if pending_sr_line else False,
            'purchase_request_line_id': pr_line.id,
            'requisition_line_id': line.requisition_line_id.id if line.requisition_line_id else False,
            'state': 'draft',
        })
        pr_line.allocation_id = alloc.id

        # 4. Actualizar cantidad a enviar en la partida actual de suministro
        if self.qty_to_send != line.qty_to_send:
            line.write({'qty_to_send': self.qty_to_send})

        # 5. Notificar en el Chatter de la solicitud de suministro
        if line.request_id:
            pending_info = _("Sí (creada en 'Pendientes de Envío')") if self.create_pending_supply else _("No")
            line.request_id.message_post(
                body=Markup(_(
                    "Se disparó la Solicitud de Compra <strong>%s</strong> para el producto '<strong>%s</strong>'.<br/>"
                    "• <strong>Cantidad a comprar:</strong> %.2f %s<br/>"
                    "• <strong>Cantidad a enviar en esta entrega:</strong> %.2f %s<br/>"
                    "• <strong>Partida para envío futuro:</strong> %s"
                )) % (
                    escape(pr_line.display_name),
                    escape(line.product_id.display_name),
                    self.qty_to_purchase,
                    escape(line.product_uom_id.name or ''),
                    self.qty_to_send,
                    escape(line.product_uom_id.name or ''),
                    escape(pending_info)
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
