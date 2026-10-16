# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class RequisitionPurchaseRequestLine(models.Model):
    """
    Línea de Solicitud de Compra generada desde una requisición de empleado
    para seguimiento por el departamento de compras, enlazada a supply.chain.allocation.
    """
    _name = 'requisition.purchase.request.line'
    _description = 'Línea de Solicitud de Compra'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        string='Descripción / Referencia',
        compute='_compute_name',
        store=True,
        readonly=False
    )
    requisition_id = fields.Many2one(
        'requisitions',
        string='Requisición Origen',
        store=True,
        readonly=True,
        index=True
    )
    requisition_line_id = fields.Many2one(
        'requisitions.line',
        string='Línea de Requisición',
        store=True,
        ondelete='cascade',
        index=True
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
        store=True,
        readonly=True
    )
    product_qty = fields.Float(
        string='Cantidad Solicitada',
        required=True,
        digits='Product Unit of Measure'
    )
    date_required = fields.Date(
        string='Fecha Requerida',
        related='requisition_id.date_required',
        store=True,
        readonly=True
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Almacén Destino',
        related='requisition_id.warehouse_id',
        store=True,
        readonly=True
    )
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        related='requisition_id.company_id',
        store=True,
        readonly=True
    )
    state = fields.Selection(
        [
            ('draft', 'Pendiente'),
            ('in_progress', 'En Cotización/Compra'),
            ('purchased', 'Comprado'),
            ('cancel', 'Cancelado'),
        ],
        string='Estado',
        default='draft',
        tracking=True,
        index=True
    )
    purchase_line_id = fields.Many2one(
        'purchase.order.line',
        string='Línea de Orden de Compra',
        readonly=True,
        index=True
    )
    allocation_id = fields.Many2one(
        'supply.chain.allocation',
        string='Asignación de Suministro',
        readonly=True,
        index=True
    )
    notes = fields.Text(
        string='Notas / Especificaciones'
    )

    @api.depends('product_id', 'requisition_id')
    def _compute_name(self):
        for rec in self:
            prod_name = rec.product_id.display_name or _('Producto')
            req_name = rec.requisition_id.name or _('Requisición')
            rec.name = f"{req_name} - {prod_name}"

    @api.constrains('product_qty')
    def _check_product_qty(self):
        for rec in self:
            if rec.product_qty <= 0:
                raise ValidationError(_("La cantidad a comprar debe ser mayor a cero."))
