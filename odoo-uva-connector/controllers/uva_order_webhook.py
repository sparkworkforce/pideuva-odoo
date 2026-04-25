# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import json
import logging
import time

from odoo import http, SUPERUSER_ID
from odoo.http import request, Response

_logger = logging.getLogger(__name__)

# Per-worker in-memory rate limiter.
# NOTE: In multi-worker deployments (Odoo.sh), each worker has its own counter.
# For strict rate limiting, configure limits at the Nginx/reverse-proxy level.
# This provides a best-effort defence against accidental flooding per worker.
_RATE_LIMIT_MAX = 60          # max requests per window
_RATE_LIMIT_WINDOW = 60       # seconds
_RATE_LIMIT_MAX_KEYS = 1000   # cap dict size to prevent unbounded growth
_rate_limit_counters = {}     # {key: (count, window_start_ts)}


def _check_rate_limit(key):
    """Return True if the request is within the rate limit, False if exceeded.

    Evicts the oldest entry when the dict exceeds _RATE_LIMIT_MAX_KEYS to
    prevent unbounded memory growth.
    """
    now = time.monotonic()
    entry = _rate_limit_counters.get(key)

    if entry is None or (now - entry[1]) > _RATE_LIMIT_WINDOW:
        # Evict oldest entry if at capacity
        if len(_rate_limit_counters) >= _RATE_LIMIT_MAX_KEYS:
            oldest_key = min(_rate_limit_counters, key=lambda k: _rate_limit_counters[k][1])
            del _rate_limit_counters[oldest_key]
        _rate_limit_counters[key] = (1, now)
        return True

    count, window_start = entry
    if count >= _RATE_LIMIT_MAX:
        return False
    _rate_limit_counters[key] = (count + 1, window_start)
    return True


class UvaOrderWebhookController(http.Controller):

    @http.route(
        '/uva/webhook/orders/<int:store_id>',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False,
    )
    def receive_order(self, store_id, **kwargs):
        """Receive an incoming Uva order via webhook.

        Thin controller (D-03):
        1. Browse store config by store_id (the URL param IS the uva.store.config ID)
        2. Validate HMAC — fail closed on any failure
        3. Parse JSON body
        4. Delegate to uva.order.service.ingest_order
        5. Return JSON response

        Security: auth='none' — HMAC is the authentication mechanism (D-05 addendum).
        """
        # Rate limiting (SECURITY-11)
        if not _check_rate_limit(store_id):
            _logger.warning(
                "UvaOrderWebhookController: rate limit exceeded for store_id=%s", store_id
            )
            return Response(
                json.dumps({'error': 'rate limit exceeded'}),
                status=429,
                mimetype='application/json',
            )

        # Step 1: Locate store config by its own ID (NOT pos_config_id)
        # The URL param store_id IS the uva.store.config record ID.
        env = request.env(user=SUPERUSER_ID)
        store_config = env['uva.store.config'].browse(store_id)
        if not store_config.exists() or not store_config.active:
            _logger.warning(
                "UvaOrderWebhookController: store_id=%s not found or inactive", store_id
            )
            return Response(
                json.dumps({'error': 'store not found'}),
                status=400,
                mimetype='application/json',
            )

        # Step 2: Validate HMAC — fail closed (SECURITY-15, BR-01)
        raw_body = request.httprequest.get_data()
        signature = request.httprequest.headers.get('X-Uva-Signature', '')
        webhook_secret = store_config.sudo().webhook_secret or ''

        if not webhook_secret:
            _logger.warning(
                "UvaOrderWebhookController: webhook_secret not configured for store_id=%s",
                store_id,
            )
            return Response(
                json.dumps({'error': 'webhook not configured'}),
                status=403,
                mimetype='application/json',
            )

        if not env['uva.api.client'].validate_hmac(raw_body, signature, webhook_secret):
            _logger.warning(
                "UvaOrderWebhookController: HMAC validation failed for store_id=%s", store_id
            )
            return Response(
                json.dumps({'error': 'forbidden'}),
                status=403,
                mimetype='application/json',
            )

        # Store hours check — after HMAC so unauthenticated callers can't probe hours
        if not store_config.is_store_open():
            return Response(
                json.dumps({'status': 'store_closed'}),
                status=200,
                mimetype='application/json',
            )

        # Step 3: Parse JSON body
        try:
            raw_order = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError) as exc:
            _logger.warning(
                "UvaOrderWebhookController: invalid JSON body for store_id=%s: %s",
                store_id, exc,
            )
            return Response(
                json.dumps({'error': 'invalid JSON'}),
                status=400,
                mimetype='application/json',
            )

        # Step 4: Delegate to service
        try:
            order_log = env['uva.order.service'].ingest_order(raw_order, store_config)
            return Response(
                json.dumps({
                    'status': 'ok',
                }),
                status=200,
                mimetype='application/json',
            )
        except Exception as exc:
            _logger.error(
                "UvaOrderWebhookController: error ingesting order for store_id=%s: %s",
                store_id, exc, exc_info=True,
            )
            # Return generic error — never expose internal details (SECURITY-09)
            return Response(
                json.dumps({'error': 'internal error'}),
                status=500,
                mimetype='application/json',
            )
