# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
from odoo import _, fields, models
from odoo.exceptions import UserError


class UvaFleetEstimateWizard(models.TransientModel):
    """Cost estimate confirmation wizard for Uva Fleet dispatch.

    Shown to the merchant before dispatch is confirmed (FR-05.3, FR-05.4).
    The merchant MUST explicitly confirm the estimated cost before the delivery
    is created in Uva Fleet.
    """
    _name = 'uva.fleet.estimate.wizard'
    _description = 'Uva Fleet Delivery Cost Estimate'

    picking_id = fields.Many2one(
        'stock.picking',
        string='Delivery Order',
        required=True,
        readonly=True,
    )
    carrier_id = fields.Many2one(
        'delivery.carrier',
        string='Carrier',
        required=True,
        readonly=True,
    )
    estimated_amount = fields.Float(
        string='Estimated Cost',
        readonly=True,
    )
    estimated_currency = fields.Char(
        string='Currency',
        default='USD',
        readonly=True,
    )
    eta_minutes = fields.Integer(
        string='Estimated Delivery Time (minutes)',
        readonly=True,
    )

    def action_confirm(self):
        """Merchant confirms the estimate — proceed with dispatch."""
        self.ensure_one()
        if not self.env.user.has_group('stock.group_stock_user'):
            raise UserError(_("You do not have permission to dispatch deliveries."))
        if not self.picking_id or not self.carrier_id:
            raise UserError(_("Missing picking or carrier information."))

        result = self.carrier_id.uva_send_shipping(self.picking_id)
        return {'type': 'ir.actions.act_window_close'}

    def action_cancel(self):
        """Merchant cancels — close wizard without dispatching."""
        return {'type': 'ir.actions.act_window_close'}
