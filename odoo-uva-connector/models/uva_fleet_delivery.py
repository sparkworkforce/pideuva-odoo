# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class UvaFleetDelivery(models.Model):
    """Tracks a Uva Fleet delivery dispatched from Odoo.

    Created when a merchant dispatches via uva_send_shipping.
    Updated by Unit 6 (status tracking) as Uva pushes status updates.
    """
    _name = 'uva.fleet.delivery'
    _description = 'Uva Fleet Delivery'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    # ------------------------------------------------------------------
    # Fields (D-10)
    # ------------------------------------------------------------------

    name = fields.Char(
        string='Reference',
        compute='_compute_name',
        store=True,
    )
    uva_delivery_id = fields.Char(
        string='Uva Delivery ID',
        required=True,
        index=True,
        help='Tracking ID returned by the Uva Fleet API on dispatch.',
    )
    carrier_id = fields.Many2one(
        'delivery.carrier',
        string='Carrier',
        required=True,
        ondelete='restrict',
    )
    picking_id = fields.Many2one(
        'stock.picking',
        string='Delivery Order',
        ondelete='cascade',
        index=True,
    )
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        index=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
    )
    state = fields.Selection([
        ('pending',    'Pending'),
        ('assigned',   'Driver Assigned'),
        ('in_transit', 'In Transit'),
        ('delivered',  'Delivered'),
        ('cancelled',  'Cancelled'),
        ('failed',     'Failed'),
    ], string='Status', default='pending', required=True, index=True, tracking=True)
    last_status_at = fields.Datetime(
        string='Last Status Update',
        help='Used by the polling cron throttle (D-09 pattern).',
    )
    estimated_cost = fields.Float(
        string='Estimated Cost (USD)',
        help='Cost estimate confirmed by merchant at dispatch time.',
    )
    tracking_url = fields.Char(
        string='Tracking URL',
        readonly=True,
    )
    pickup_lat = fields.Float(string='Pickup Latitude', digits=(10, 7))
    pickup_lng = fields.Float(string='Pickup Longitude', digits=(10, 7))
    delivery_lat = fields.Float(string='Delivery Latitude', digits=(10, 7))
    delivery_lng = fields.Float(string='Delivery Longitude', digits=(10, 7))
    map_url = fields.Char(string='View on Map', compute='_compute_map_url')
    eta_minutes = fields.Integer(string='ETA (minutes)')
    driver_name = fields.Char(string='Driver Name')
    driver_phone = fields.Char(string='Driver Phone')
    driver_lat = fields.Float(string='Driver Latitude', digits=(10, 7))
    driver_lng = fields.Float(string='Driver Longitude', digits=(10, 7))
    proof_photo_url = fields.Char(string='Proof of Delivery Photo')
    delivery_signature = fields.Binary(string='Delivery Signature')

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    _sql_constraints = [
        ('uva_delivery_id_unique', 'UNIQUE(uva_delivery_id)',
         'A Uva Fleet delivery with this tracking ID already exists.'),
    ]

    @api.constrains('proof_photo_url')
    def _check_proof_photo_url(self):
        for rec in self:
            if rec.proof_photo_url and not rec.proof_photo_url.startswith('https://'):
                raise ValidationError(_('Proof photo URL must use HTTPS.'))

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    @api.depends('uva_delivery_id', 'picking_id', 'sale_order_id')
    def _compute_name(self):
        for rec in self:
            if rec.picking_id:
                rec.name = f"Uva/{rec.picking_id.name}"
            elif rec.sale_order_id:
                rec.name = f"Uva/{rec.sale_order_id.name}"
            else:
                rec.name = f"Uva/{rec.uva_delivery_id or 'New'}"

    @api.depends('delivery_lat', 'delivery_lng')
    def _compute_map_url(self):
        for rec in self:
            if rec.delivery_lat and rec.delivery_lng:
                lat, lng = rec.delivery_lat, rec.delivery_lng
                delta = 0.005
                bbox = f"{lng - delta},{lat - delta},{lng + delta},{lat + delta}"
                rec.map_url = (
                    f"https://www.openstreetmap.org/export/embed.html"
                    f"?bbox={bbox}&layer=mapnik&marker={lat},{lng}"
                )
            else:
                rec.map_url = False
