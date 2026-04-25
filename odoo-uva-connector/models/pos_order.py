# License: OPL-1
from odoo import fields, models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    is_uva_order = fields.Boolean(string='Uva Order', default=False, index=True)
