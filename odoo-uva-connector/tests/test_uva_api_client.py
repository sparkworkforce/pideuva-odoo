# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
"""
Tests for uva.api.client AbstractModel.

Covers:
  - HMAC validation (example-based + PBT)
  - Demo mode mock responses
  - Error classification (UvaApiError, UvaCoverageError, UvaAuthError)
  - API key masking in log output
"""
import hashlib
import hmac
import logging
from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from odoo.tests.common import TransactionCase

from ..models.uva_api_client import (
    UvaApiError,
    UvaAuthError,
    UvaCoverageError,
    _mask_key,
)


class TestUvaApiClientHmac(TransactionCase):
    """Example-based tests for validate_hmac."""

    def setUp(self):
        super().setUp()
        self.client = self.env['uva.api.client']
        self.secret = 'test-secret-key'

    def _make_sig(self, payload, secret=None):
        s = secret or self.secret
        return hmac.new(s.encode(), payload, hashlib.sha256).hexdigest()

    # --- Valid signatures ---

    def test_valid_hmac_plain_hex(self):
        payload = b'{"order_id": "123"}'
        sig = self._make_sig(payload)
        self.assertTrue(self.client.validate_hmac(payload, sig, self.secret))

    def test_valid_hmac_with_prefix(self):
        """Uva may send 'sha256=<hex>' — must be normalized."""
        payload = b'{"order_id": "456"}'
        sig = 'sha256=' + self._make_sig(payload)
        self.assertTrue(self.client.validate_hmac(payload, sig, self.secret))

    def test_valid_hmac_empty_body(self):
        """Empty body is a valid HMAC input (BR-08)."""
        payload = b''
        sig = self._make_sig(payload)
        self.assertTrue(self.client.validate_hmac(payload, sig, self.secret))

    # --- Invalid signatures ---

    def test_tampered_payload_fails(self):
        payload = b'{"order_id": "123"}'
        sig = self._make_sig(payload)
        tampered = b'{"order_id": "999"}'
        self.assertFalse(self.client.validate_hmac(tampered, sig, self.secret))

    def test_wrong_secret_fails(self):
        payload = b'{"order_id": "123"}'
        sig = self._make_sig(payload, secret='correct-secret')
        self.assertFalse(self.client.validate_hmac(payload, sig, 'wrong-secret'))

    def test_empty_signature_fails(self):
        payload = b'{"order_id": "123"}'
        self.assertFalse(self.client.validate_hmac(payload, '', self.secret))

    def test_garbage_signature_fails(self):
        payload = b'{"order_id": "123"}'
        self.assertFalse(self.client.validate_hmac(payload, 'not-a-valid-sig', self.secret))

    def test_validate_hmac_never_raises(self):
        """validate_hmac must return bool, never raise (BR-01 fail-safe)."""
        result = self.client.validate_hmac(b'body', None, self.secret)  # type: ignore
        self.assertIsInstance(result, bool)


class TestUvaApiClientHmacPBT(TransactionCase):
    """Property-based tests for validate_hmac using Hypothesis."""

    def setUp(self):
        super().setUp()
        self.client = self.env['uva.api.client']

    @given(payload=st.binary(), secret=st.text(min_size=1, max_size=64))
    @settings(max_examples=200)
    def test_valid_sig_always_passes(self, payload, secret):
        """Invariant: a correctly computed HMAC always validates."""
        sig = hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()
        self.assertTrue(self.client.validate_hmac(payload, sig, secret))

    @given(
        payload=st.binary(min_size=1),
        secret=st.text(min_size=1, max_size=64),
        noise=st.binary(min_size=1),
    )
    @settings(max_examples=200)
    def test_tampered_sig_always_fails(self, payload, secret, noise):
        """Invariant: a tampered payload never validates against the original sig."""
        sig = hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()
        tampered = payload + noise
        # Only assert when tampered != payload (noise is non-empty so always true here)
        if tampered != payload:
            self.assertFalse(self.client.validate_hmac(tampered, sig, secret))


class TestUvaApiClientDemoMode(TransactionCase):
    """Demo mode returns correct mock responses without network calls."""

    def setUp(self):
        super().setUp()
        self.client = self.env['uva.api.client']
        self.api_key = 'demo-key'

    def test_get_orders_demo_returns_empty_list(self):
        from datetime import datetime
        result = self.client.get_orders(self.api_key, 'store-1', datetime.utcnow(), demo_mode=True)
        self.assertEqual(result, [])

    def test_confirm_order_demo_returns_true(self):
        result = self.client.confirm_order(self.api_key, 'ext-123', 'accept', demo_mode=True)
        self.assertTrue(result)

    def test_get_delivery_estimate_demo(self):
        result = self.client.get_delivery_estimate(
            self.api_key, {'address': 'A'}, {'address': 'B'}, demo_mode=True
        )
        self.assertEqual(result['currency'], 'USD')
        self.assertIsInstance(result['amount'], float)
        self.assertIsInstance(result['eta_minutes'], int)

    def test_create_delivery_demo_returns_tracking_id(self):
        result = self.client.create_delivery(
            self.api_key, {'address': 'A'}, {'address': 'B'}, 'REF-001', demo_mode=True
        )
        self.assertIn('delivery_id', result)
        self.assertTrue(result['delivery_id'].startswith('DEMO-'))
        self.assertIn('tracking_url', result)

    def test_cancel_delivery_demo_returns_true(self):
        result = self.client.cancel_delivery(self.api_key, 'DEMO-ABC', demo_mode=True)
        self.assertTrue(result)

    def test_get_delivery_status_demo(self):
        result = self.client.get_delivery_status(self.api_key, 'DEMO-ABC', demo_mode=True)
        self.assertEqual(result['status'], 'pending')
        self.assertIn('updated_at', result)

    def test_validate_hmac_demo_mode_true(self):
        """In demo mode validate_hmac still works (it's a pure function)."""
        payload = b'test'
        import hashlib as hl
        sig = hmac.new(b'secret', payload, hl.sha256).hexdigest()
        self.assertTrue(self.client.validate_hmac(payload, sig, 'secret'))


class TestUvaApiClientErrorClassification(TransactionCase):
    """Error classification from HTTP responses."""

    def setUp(self):
        super().setUp()
        self.client = self.env['uva.api.client']

    def _mock_response(self, status_code, json_body=None, text=''):
        resp = MagicMock()
        resp.status_code = status_code
        resp.ok = status_code < 400
        resp.text = text
        resp.json.return_value = json_body or {}
        return resp

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.requests.request')
    def test_500_raises_uva_api_error(self, mock_req):
        mock_req.return_value = self._mock_response(500, text='Internal Server Error')
        with self.assertRaises(UvaApiError):
            self.client._request('GET', '/test', api_key='key', demo_mode=False)

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.requests.request')
    def test_401_raises_uva_auth_error(self, mock_req):
        mock_req.return_value = self._mock_response(401)
        with self.assertRaises(UvaAuthError):
            self.client._request('GET', '/test', api_key='key', demo_mode=False)

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.requests.request')
    def test_403_raises_uva_auth_error(self, mock_req):
        mock_req.return_value = self._mock_response(403)
        with self.assertRaises(UvaAuthError):
            self.client._request('GET', '/test', api_key='key', demo_mode=False)

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.requests.request')
    def test_422_coverage_error_raises_uva_coverage_error(self, mock_req):
        mock_req.return_value = self._mock_response(
            422, json_body={'error_code': 'COVERAGE_ERROR', 'message': 'Out of range'}
        )
        with self.assertRaises(UvaCoverageError):
            self.client._request('GET', '/test', api_key='key', demo_mode=False)

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.requests.request')
    def test_422_non_coverage_raises_uva_api_error(self, mock_req):
        mock_req.return_value = self._mock_response(
            422, json_body={'error_code': 'VALIDATION_ERROR'}
        )
        with self.assertRaises(UvaApiError):
            self.client._request('GET', '/test', api_key='key', demo_mode=False)

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.requests.request')
    def test_timeout_raises_uva_api_error(self, mock_req):
        import requests as req_lib
        mock_req.side_effect = req_lib.exceptions.Timeout()
        with self.assertRaises(UvaApiError):
            self.client._request('GET', '/test', api_key='key', demo_mode=False)


class TestUvaApiClientKeyMasking(TransactionCase):
    """API key must be masked in log output (BR-02)."""

    def test_mask_key_normal(self):
        self.assertEqual(_mask_key('abcd1234efgh'), 'abcd****')

    def test_mask_key_short(self):
        # A 2-char key: first 4 chars is the whole key, still append ****
        result = _mask_key('ab')
        self.assertTrue(result.endswith('****'))

    def test_mask_key_empty(self):
        self.assertEqual(_mask_key(''), '(empty)')

    def test_mask_key_none(self):
        self.assertEqual(_mask_key(None), '(empty)')

    def test_request_logs_masked_key(self):
        """Verify that the api_key does not appear unmasked in log records."""
        client = self.env['uva.api.client']
        secret_key = 'super-secret-api-key-12345'
        # Use assertLogs with WARNING level — demo mode emits at least a debug log.
        # We capture all levels and verify the secret never appears.
        import logging as _logging
        with self.assertLogs(
            'odoo.addons.odoo_uva_connector.models.uva_api_client',
            level=_logging.DEBUG,
        ) as cm:
            import datetime as _dt
            client.get_orders(secret_key, 'store-1', _dt.datetime.utcnow(), demo_mode=True)
        for line in cm.output:
            self.assertNotIn(secret_key, line, f"API key leaked in log: {line}")
