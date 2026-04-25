# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import hashlib
import hmac
import logging
import uuid
from datetime import datetime

import requests

from odoo import models

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exception hierarchy
# ---------------------------------------------------------------------------

class UvaApiError(Exception):
    """Transient Uva API error — eligible for retry queue."""
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class UvaCoverageError(Exception):
    """Permanent error — destination outside Uva Fleet service area.
    Must NOT be added to the retry queue; requires merchant action."""


class UvaAuthError(Exception):
    """Permanent error — invalid or expired API key.
    Must NOT be added to the retry queue; requires admin action."""
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Known retryable action types (used by uva.api.retry.queue.enqueue)
# ---------------------------------------------------------------------------

RETRYABLE_ACTIONS = frozenset({
    'notify_acceptance',
    'notify_rejection',
    'notify_modification',
    'create_fleet_delivery',
    'cancel_fleet_delivery',
})

# ---------------------------------------------------------------------------
# Default timeouts (seconds) — overridable via ir.config_parameter
# ---------------------------------------------------------------------------

_DEFAULT_CONNECT_TIMEOUT = 10
_DEFAULT_READ_TIMEOUT = 10


def _mask_key(api_key):
    """Return a masked version of an API key safe for logging."""
    if not api_key:
        return '(empty)'
    return api_key[:4] + '****'


class UvaApiClient(models.AbstractModel):
    _name = 'uva.api.client'
    _description = 'Uva PR API Client'

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_timeout(self):
        """Return (connect_timeout, read_timeout) from config or defaults."""
        ICP = self.env['ir.config_parameter'].sudo()
        try:
            connect = int(ICP.get_param('uva.api.connect_timeout', _DEFAULT_CONNECT_TIMEOUT))
        except (ValueError, TypeError):
            connect = _DEFAULT_CONNECT_TIMEOUT
        try:
            read = int(ICP.get_param('uva.api.read_timeout', _DEFAULT_READ_TIMEOUT))
        except (ValueError, TypeError):
            read = _DEFAULT_READ_TIMEOUT
        return (connect, read)

    def _get_base_url(self, sandbox_mode=False):
        """Return Uva API base URL. See doc/api_compatibility.md.

        Args:
            sandbox_mode: If True, use sandbox URL. Callers pass the
                          store-level sandbox_mode field value.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        if sandbox_mode or ICP.get_param('uva.api.sandbox_mode', 'False') in ('True', '1', 'true'):
            return ICP.get_param('uva.api.sandbox_url', 'https://sandbox.pideuva.com/v1')
        url = ICP.get_param('uva.api.base_url', 'https://api.pideuva.com/v1')
        if not url.startswith('https://'):
            raise UvaApiError("Uva API base URL must use HTTPS")
        return url

    def _request(self, method, path, api_key, demo_mode=False, sandbox_mode=False, **kwargs):
        """Execute an authenticated HTTP request to the Uva API.

        Raises:
            UvaAuthError: on 401/403
            UvaCoverageError: on coverage-specific error response
            UvaApiError: on any other non-2xx or connection failure
        """
        if demo_mode:
            # Demo mode: never reaches the network
            return None

        base_url = self._get_base_url(sandbox_mode=sandbox_mode)
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        timeout = self._get_timeout()
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        _logger.debug(
            "Uva API %s %s (key=%s)", method.upper(), url, _mask_key(api_key)
        )
        try:
            response = requests.request(
                method, url, headers=headers, timeout=timeout, verify=True, **kwargs
            )
        except requests.exceptions.Timeout as exc:
            raise UvaApiError(f"Uva API timeout: {exc}") from exc
        except requests.exceptions.ConnectionError as exc:
            raise UvaApiError(f"Uva API connection error: {exc}") from exc
        except Exception as exc:
            raise UvaApiError(f"Uva API unexpected error: {exc}") from exc

        if response.status_code in (401, 403):
            raise UvaAuthError(
                f"Uva API auth error {response.status_code}", status_code=response.status_code
            )
        # Coverage error — see doc/api_compatibility.md
        if response.status_code == 422:
            try:
                body = response.json()
            except Exception:
                body = {}
            if body.get('error_code') == 'COVERAGE_ERROR':
                raise UvaCoverageError(
                    body.get('message', 'Destination outside Uva Fleet service area')
                )
        if not response.ok:
            raise UvaApiError(
                f"Uva API error {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )
        return response

    def _request_json(self, method, path, api_key, demo_mode=False, **kwargs):
        """Execute request and parse JSON response safely.

        Raises UvaApiError if response is not valid JSON.
        """
        resp = self._request(method, path, api_key, demo_mode=demo_mode, **kwargs)
        if resp is None:
            return {}  # demo mode
        try:
            return resp.json()
        except (ValueError, AttributeError) as exc:
            raise UvaApiError(
                f"Uva API returned non-JSON response: {resp.text[:200]}"
            ) from exc

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self, api_key: str, demo_mode: bool = False) -> bool:
        """Ping the Uva API to verify connectivity and credentials."""
        if demo_mode:
            return True
        self._request('GET', '/health', api_key)
        return True

    # ------------------------------------------------------------------
    # Product catalog
    # ------------------------------------------------------------------

    def get_products(self, api_key: str, store_id: str, demo_mode: bool = False) -> list:
        """Fetch product catalog from Uva for a store."""
        if demo_mode:
            return []
        data = self._request_json('GET', '/products', api_key, params={'store_id': store_id})
        return data.get('products', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

    # ------------------------------------------------------------------
    # HMAC validation (pure — no network, no ORM)
    # ------------------------------------------------------------------

    def validate_hmac(self, payload: bytes, signature: str, secret: str) -> bool:
        """Validate HMAC-SHA256 signature on an incoming webhook payload.

        Normalizes the signature header — Uva may send "sha256=<hex>" or plain "<hex>".

        Uses constant-time comparison to prevent timing attacks.
        Returns True if valid, False otherwise — never raises.
        """
        try:
            if not signature:
                return False
            # Normalize: strip "sha256=" prefix if present
            sig = signature.removeprefix('sha256=') if signature.startswith('sha256=') else signature
            computed = hmac.new(
                secret.encode('utf-8'), payload, hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(computed, sig)
        except Exception as exc:
            _logger.warning("HMAC validation error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Flow A — Orders API
    # ------------------------------------------------------------------

    def get_orders(self, api_key: str, store_id: str, since: datetime,
                   demo_mode: bool = False) -> list:
        """Poll Uva Orders API for new orders since the given timestamp.

        Returns list of raw order dicts.
        """
        if demo_mode:
            _logger.debug("Uva demo mode: get_orders returning []")
            return []

        params = {'store_id': store_id}
        if since:
            params['since'] = since.isoformat()
        data = self._request_json('GET', '/orders', api_key, params=params)
        return data.get('orders', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

    def confirm_order(self, api_key: str, external_id: str, action: str,
                      items: list = None, demo_mode: bool = False) -> bool:
        """Notify Uva of a staff action on an order.

        action: one of 'accept', 'reject', 'modify'
        items: list of unavailable item IDs (required when action='modify')
        """
        if demo_mode:
            _logger.info(
                "Uva demo mode: confirm_order external_id=%s action=%s items=%s",
                external_id, action, items,
            )
            return True

        resp = self._request('POST', f'/orders/{external_id}/status', api_key,
                             json={'action': action, 'unavailable_items': items or []})
        return resp.status_code == 200

    # ------------------------------------------------------------------
    # Flow B — Fleet API
    # ------------------------------------------------------------------

    def get_delivery_estimate(self, api_key: str, pickup: dict,
                               destination: dict, demo_mode: bool = False) -> dict:
        """Request a delivery cost estimate from Uva Fleet.

        Returns: {'amount': float, 'currency': str, 'eta_minutes': int}
        """
        if demo_mode:
            return {'amount': 5.00, 'currency': 'USD', 'eta_minutes': 30}

        return self._request_json('POST', '/fleet/estimate', api_key,
                                  json={'pickup': pickup, 'destination': destination})

    def create_delivery(self, api_key: str, pickup: dict, destination: dict,
                        reference: str, demo_mode: bool = False) -> dict:
        """Create a Uva Fleet delivery order.

        Returns: {'delivery_id': str, 'tracking_url': str}
        """
        if demo_mode:
            return {
                'delivery_id': f'DEMO-{uuid.uuid4().hex[:8].upper()}',
                'tracking_url': '#',
            }

        return self._request_json('POST', '/fleet/deliveries', api_key,
                                  json={'pickup': pickup, 'destination': destination,
                                        'reference': reference})

    def cancel_delivery(self, api_key: str, delivery_id: str,
                        demo_mode: bool = False) -> bool:
        """Cancel a Uva Fleet delivery.

        Returns True on success.
        Raises UvaCoverageError if cancellation is rejected for coverage reasons.
        Raises UvaApiError on transient failure.
        """
        if demo_mode:
            _logger.info("Uva demo mode: cancel_delivery delivery_id=%s", delivery_id)
            return True

        resp = self._request('DELETE', f'/fleet/deliveries/{delivery_id}', api_key)
        return resp.status_code in (200, 204)

    def get_delivery_status(self, api_key: str, delivery_id: str,
                             demo_mode: bool = False) -> dict:
        """Poll the status of a Uva Fleet delivery.

        Returns: {'status': str, 'updated_at': datetime}
        """
        if demo_mode:
            return {'status': 'pending', 'updated_at': datetime.utcnow()}

        return self._request_json('GET', f'/fleet/deliveries/{delivery_id}/status', api_key)
