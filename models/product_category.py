# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ProductCategory(models.Model):
    _inherit = 'product.category'

    requisition_type = fields.Selection(
        [
            ('material', 'Materiales'),
            ('equipment', 'Equipos'),
            ('tool', 'Herramientas'),
            ('service', 'Servicios'),
        ],
        string="Tipo de Requisición",
        compute='_compute_requisition_type',
        store=True,
        readonly=False,
        precompute=True,
        recursive=True,
        help="Clasifica la categoría para el filtrado en las pestañas de requisición. "
             "Hereda automáticamente de la categoría padre si no se especifica."
    )

    allow_requisition = fields.Boolean(
        string="Permitir en Requisiciones",
        compute='_compute_allow_requisition',
        store=True,
        readonly=False,
        precompute=True,
        recursive=True,
        default=False,
        help="Habilita los productos de esta categoría para ser solicitados en requisiciones. "
             "Hereda la configuración del padre pero permite modificarse individualmente sin alterar a otros registros."
    )

    @api.depends('parent_id', 'parent_id.requisition_type')
    def _compute_requisition_type(self):
        for category in self:
            if not category.requisition_type and category.parent_id and category.parent_id.requisition_type:
                category.requisition_type = category.parent_id.requisition_type

    @api.depends('requisition_type', 'parent_id.allow_requisition', 'parent_id.requisition_type')
    def _compute_allow_requisition(self):
        for category in self:
            if not category.requisition_type:
                # Si no tiene tipo de requisición, por defecto debe ser False
                category.allow_requisition = False
            elif category.parent_id and category.parent_id.requisition_type:
                # Si tiene categoría padre con tipo configurado, hereda la configuración del padre
                category.allow_requisition = category.parent_id.allow_requisition
            else:
                # Si tiene tipo de requisición y es categoría raíz o sin padre configurado, se activa True por defecto
                category.allow_requisition = True

    @api.onchange('requisition_type')
    def _onchange_requisition_type(self):
        """Al seleccionar un tipo en la interfaz, inicializa allow_requisition según la regla de herencia o True por defecto."""
        if self.requisition_type:
            if self.parent_id and self.parent_id.requisition_type == self.requisition_type:
                self.allow_requisition = self.parent_id.allow_requisition
            else:
                self.allow_requisition = True
        else:
            self.allow_requisition = False

    def write(self, vals):
        if 'requisition_type' in vals and not vals['requisition_type']:
            vals['allow_requisition'] = False

        res = super().write(vals)

        # Si se actualizó el tipo de requisición, propagar a subcategorías descendientes sin tipo asignado
        if 'requisition_type' in vals and not self.env.context.get('skip_requisition_type_propagation'):
            new_type = vals['requisition_type']
            descendants = self.search([
                ('id', 'child_of', self.ids),
                ('id', 'not in', self.ids),
                '|', ('requisition_type', '=', False), ('requisition_type', '!=', new_type),
            ])
            if descendants:
                descendants.with_context(skip_requisition_type_propagation=True).write({
                    'requisition_type': new_type,
                    'allow_requisition': bool(new_type),
                })

        # Si se actualizó allow_requisition, propagar a subcategorías descendientes sin alterar a padres
        if 'allow_requisition' in vals and not self.env.context.get('skip_allow_requisition_propagation'):
            self._cascade_field_to_descendants(
                'allow_requisition', vals['allow_requisition'], 'skip_allow_requisition_propagation'
            )

        return res

    def _cascade_field_to_descendants(self, field_name, new_val, skip_context_key, update_product_template=False):
        """
        Propaga el valor del campo a todas las subcategorías descendientes sin afectar ancestros.
        Opcionalmente actualiza plantillas de producto vinculadas si el campo existe en product.template.
        """
        if not self:
            return

        descendants = self.search([
            ('id', 'child_of', self.ids),
            ('id', 'not in', self.ids),
            (field_name, '!=', new_val),
        ])
        if descendants:
            descendants.with_context(**{skip_context_key: True}).write({
                field_name: new_val
            })

        if update_product_template and hasattr(self.env['product.template'], field_name):
            all_affected_categ_ids = self.ids + descendants.ids
            products = self.env['product.template'].search([
                ('categ_id', 'in', all_affected_categ_ids),
                (field_name, '!=', new_val),
            ])
            if products:
                products.write({field_name: new_val})
