# License: OPL-1
from odoo import _, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_ship_with_uva_fleet(self):
        self.ensure_one()
        carrier = self.env['delivery.carrier'].search(
            [('delivery_type', '=', 'uva')], limit=1,
        )
        if not carrier:
            raise UserError(_('No Uva Fleet carrier configured. Go to Inventory > Configuration > Shipping Methods.'))
        prices = carrier.uva_get_shipping_price(self)
        if not prices:
            raise UserError(_('Could not get delivery estimate.'))
        est = prices[0]
        picking = self.picking_ids.filtered(
            lambda p: p.state not in ('done', 'cancel') and p.picking_type_code == 'outgoing'
        )[:1]
        if not picking:
            raise UserError(_('No outgoing delivery order found. Confirm the sale order first.'))
        wizard = self.env['uva.fleet.estimate.wizard'].create({
            'picking_id': picking.id,
            'carrier_id': carrier.id,
            'estimated_amount': est.get('price', 0),
            'estimated_currency': est.get('currency', 'USD'),
            'eta_minutes': est.get('eta_minutes', 0),
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'uva.fleet.estimate.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'name': _('Uva Fleet Delivery Estimate'),
        }
