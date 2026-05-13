# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
from odoo.tests.common import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestUvaTracking(HttpCase):
    """Tests for the public delivery tracking controller."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Uva Fleet Test',
            'delivery_type': 'uva',
            'product_id': cls.env.ref('delivery.product_product_delivery').id,
        })
        cls.delivery = cls.env['uva.fleet.delivery'].create({
            'uva_delivery_id': 'TEST-TRACK-001',
            'carrier_id': cls.carrier.id,
            'company_id': cls.env.company.id,
            'state': 'in_transit',
        })

    def test_tracking_page_by_token(self):
        """Tracking page accessible via tracking_token."""
        token = self.delivery.tracking_token
        resp = self.url_open(f'/uva/track/{token}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Content-Security-Policy', resp.headers)
        self.assertIn('X-Content-Type-Options', resp.headers)
        self.assertIn('Referrer-Policy', resp.headers)

    def test_tracking_page_404_invalid_token(self):
        """Invalid token returns 404."""
        resp = self.url_open('/uva/track/nonexistent-token-xyz')
        self.assertEqual(resp.status_code, 404)

    def test_tracking_status_json(self):
        """Status endpoint returns JSON with rounded GPS."""
        self.delivery.write({'driver_lat': 18.465512345, 'driver_lng': -66.105712345})
        token = self.delivery.tracking_token
        resp = self.url_open(f'/uva/track/{token}/status')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['state'], 'in_transit')
        # GPS rounded to 3 decimal places
        self.assertEqual(data['driver_lat'], 18.466)

    def test_rate_limit(self):
        """Rate limit returns 429 after threshold."""
        token = self.delivery.tracking_token
        for _ in range(31):
            resp = self.url_open(f'/uva/track/{token}/status')
        self.assertEqual(resp.status_code, 429)
