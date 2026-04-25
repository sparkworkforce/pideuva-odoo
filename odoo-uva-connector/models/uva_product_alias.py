# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
from odoo import api, fields, models


class UvaProductAlias(models.Model):
    _name = 'uva.product.alias'
    _description = 'Uva Product Alias'
    _order = 'alias_name'

    alias_name = fields.Char(required=True, index=True)
    canonical_name = fields.Char(required=True)
    product_id = fields.Many2one('product.product', string='Product')
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('alias_name_unique', 'UNIQUE(alias_name)',
         'Each alias name must be unique.'),
    ]

    @api.model
    def resolve(self, name):
        """Return canonical name (or original name) and optional product for a given name."""
        alias = self.search([('alias_name', '=ilike', name)], limit=1)
        if alias:
            return alias.canonical_name, alias.product_id
        return name, self.env['product.product']
