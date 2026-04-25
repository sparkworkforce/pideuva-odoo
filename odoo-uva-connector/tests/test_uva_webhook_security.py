# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
"""Tests for webhook security hardening and fleet state transitions."""
import hashlib
import hmac
import json
from datetime import timedelta

from odoo import fields
from odoo.tests.common import HttpCase, TransactionCase

from ..models.uva_api_client import UvaApiError


class TestWebhookSecurity(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pos_config = cls.env['pos.config'].create({'name': 'Sec POS'})
        cls.store = cls.env['uva.store.config'].sudo().create({
            'name': 'Sec Store',
            'pos_config_id': cls.pos_config.id,
            'webhook_secret': 'good-secret',
            'demo_mode': True,
        })
        cls.company = cls.env.company
        # Pre-create a store with empty secret for the 403 test
        cls.store_no_secret = cls.env['uva.store.config'].sudo().create({
            'name': 'No Secret Store',
            'pos_config_id': cls.env['pos.config'].create({'name': 'Sec POS 2'}).id,
            'webhook_secret': '',
            'demo_mode': True,
        })

    def _make_sig(self, body_bytes, secret='good-secret'):
        return hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()

    def _post_order(self, body_dict, secret='good-secret', store_id=None):
        url = f'/uva/webhook/orders/{store_id or self.store.id}'
        body = json.dumps(body_dict).encode()
        sig = self._make_sig(body, secret)
        return self.url_open(url, data=body, headers={
            'Content-Type': 'application/json',
            'X-Uva-Signature': sig,
        })

    def _post_fleet(self, body_dict, secret='fleet-secret'):
        url = f'/uva/webhook/fleet/{self.company.id}'
        body = json.dumps(body_dict).encode()
        sig = self._make_sig(body, secret)
        return self.url_open(url, data=body, headers={
            'Content-Type': 'application/json',
            'X-Uva-Signature': sig,
        })

    def test_order_webhook_empty_secret_returns_403(self):
        resp = self._post_order(
            {'id': 'SEC-001', 'items': []},
            store_id=self.store_no_secret.id,
        )
        self.assertEqual(resp.status_code, 403)

    def test_fleet_webhook_empty_secret_returns_403(self):
        # Ensure no fleet webhook secret is configured (class-level ICP)
        self.env['ir.config_parameter'].sudo().set_param('uva.fleet.webhook_secret', '')
        resp = self._post_fleet({
            'delivery_id': 'DEL-001',
            'status': 'in_transit',
            'timestamp': fields.Datetime.to_string(fields.Datetime.now()),
        })
        self.assertEqual(resp.status_code, 403)

    def test_fleet_webhook_stale_timestamp_returns_400(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'uva.fleet.webhook_secret', 'fleet-secret',
        )
        stale_ts = fields.Datetime.to_string(
            fields.Datetime.now() - timedelta(seconds=600),
        )
        resp = self._post_fleet({
            'delivery_id': 'DEL-002',
            'status': 'in_transit',
            'timestamp': stale_ts,
        })
        self.assertEqual(resp.status_code, 400)

    def test_fleet_webhook_missing_delivery_id_returns_400(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'uva.fleet.webhook_secret', 'fleet-secret',
        )
        resp = self._post_fleet({
            'delivery_id': '',
            'status': 'in_transit',
            'timestamp': fields.Datetime.to_string(fields.Datetime.now()),
        })
        self.assertEqual(resp.status_code, 400)


class TestFleetForwardTransition(TransactionCase):

    def setUp(self):
        super().setUp()
        self.delivery_product = self.env['product.product'].create({
            'name': 'Delivery Service',
            'type': 'service',
        })
        self.carrier = self.env['delivery.carrier'].create({
            'name': 'Uva Fleet Test',
            'delivery_type': 'uva',
            'product_id': self.delivery_product.id,
        })
        self.delivery = self.env['uva.fleet.delivery'].create({
            'uva_delivery_id': 'FWD-001',
            'carrier_id': self.carrier.id,
            'state': 'in_transit',
        })

    def test_fleet_forward_only_transition(self):
        """Backward transition (in_transit → assigned) is ignored."""
        self.env['uva.fleet.service'].process_status_update(
            delivery_id='FWD-001',
            status='assigned',
            updated_at=fields.Datetime.now(),
        )
        self.assertEqual(self.delivery.state, 'in_transit')


class TestBaseUrlHttps(TransactionCase):

    def test_https_required_for_base_url(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'uva.api.base_url', 'http://api.pideuva.com/v1',
        )
        with self.assertRaises(UvaApiError):
            self.env['uva.api.client']._get_base_url()
