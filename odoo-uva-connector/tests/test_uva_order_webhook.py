# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
"""Tests for UvaOrderWebhookController."""
import hashlib
import hmac
import json

from odoo import fields
from odoo.tests.common import HttpCase


class TestUvaOrderWebhook(HttpCase):

    def setUp(self):
        super().setUp()
        self.pos_config = self.env['pos.config'].create({'name': 'Webhook POS'})
        self.store = self.env['uva.store.config'].sudo().create({
            'name': 'Webhook Store',
            'pos_config_id': self.pos_config.id,
            'webhook_secret': 'test-webhook-secret',
            'demo_mode': True,
        })
        self.url = f'/uva/webhook/orders/{self.store.id}'

    def _make_sig(self, body_bytes, secret='test-webhook-secret'):
        return hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()

    def _post(self, body_dict, secret='test-webhook-secret', store_id=None):
        url = f'/uva/webhook/orders/{store_id or self.store.id}'
        body = json.dumps(body_dict).encode()
        sig = self._make_sig(body, secret)
        return self.url_open(
            url,
            data=body,
            headers={
                'Content-Type': 'application/json',
                'X-Uva-Signature': sig,
            },
        )

    def test_valid_request_returns_200(self):
        resp = self._post({'id': 'EXT-WH-001', 'items': []})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get('status'), 'ok')

    def test_invalid_hmac_returns_400(self):
        resp = self._post({'id': 'EXT-WH-002'}, secret='wrong-secret')
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn('error', data)

    def test_unknown_store_id_returns_400(self):
        body = json.dumps({'id': 'EXT-WH-003'}).encode()
        sig = self._make_sig(body)
        resp = self.url_open(
            '/uva/webhook/orders/999999',
            data=body,
            headers={'Content-Type': 'application/json', 'X-Uva-Signature': sig},
        )
        self.assertEqual(resp.status_code, 400)

    def test_inactive_store_returns_400(self):
        self.store.sudo().write({'active': False})
        resp = self._post({'id': 'EXT-WH-004'})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_json_returns_400(self):
        body = b'not valid json {'
        sig = self._make_sig(body)
        resp = self.url_open(
            self.url,
            data=body,
            headers={'Content-Type': 'application/json', 'X-Uva-Signature': sig},
        )
        self.assertEqual(resp.status_code, 400)
