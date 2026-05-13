# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import json
import logging
import time

from odoo import fields as odoo_fields, http, SUPERUSER_ID
from odoo.http import request, Response

_logger = logging.getLogger(__name__)
_security_logger = logging.getLogger('uva.security')

# Nonce cache for replay protection (request ID dedup within 5-min window)
# NOTE: In multi-worker deployments (Odoo.sh), each worker has its own cache.
# A nonce seen by worker A may be accepted by worker B. This is mitigated by:
# 1. Nginx sticky sessions (same client → same worker for short periods)
# 2. Timestamp-based replay protection as a secondary check
# 3. UNIQUE(external_id) constraint as the ultimate dedup safety net
_NONCE_TTL = 300  # seconds
_NONCE_MAX_SIZE = 10000
_nonce_cache = {}  # {nonce: expiry_ts}


def _check_nonce(nonce):
    """Return True if nonce is fresh (not seen before). False if replayed."""
    if not nonce:
        return True  # No nonce provided — fall through to timestamp check
    now = time.monotonic()
    # Evict expired entries periodically
    if len(_nonce_cache) >= _NONCE_MAX_SIZE:
        expired = [k for k, v in _nonce_cache.items() if v < now]
        for k in expired:
            del _nonce_cache[k]
        # If still full after eviction, evict oldest
        if len(_nonce_cache) >= _NONCE_MAX_SIZE:
            oldest = min(_nonce_cache, key=_nonce_cache.get)
            del _nonce_cache[oldest]
    if nonce in _nonce_cache and _nonce_cache[nonce] > now:
        return False  # Replay detected
    _nonce_cache[nonce] = now + _NONCE_TTL
    return True

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
                json.dumps({'error': 'rate limit exceeded', 'error_code': 'RATE_LIMITED'}),
                status=429,
                mimetype='application/json',
            )
        # IP-based rate limit (prevents exhausting store-keyed limit for legitimate traffic)
        ip = request.httprequest.remote_addr or 'unknown'
        if not _check_rate_limit(f'ip:{ip}'):
            return Response(
                json.dumps({'error': 'rate limit exceeded', 'error_code': 'RATE_LIMITED'}),
                status=429,
                mimetype='application/json',
            )

        # Step 1: Locate store config by its own ID (NOT pos_config_id)
        # The URL param store_id IS the uva.store.config record ID.
        env = request.env(user=SUPERUSER_ID)

        # Body size limit — reject oversized payloads before processing
        content_length = request.httprequest.content_length or 0
        if content_length > 1_048_576:  # 1 MB
            return Response(
                json.dumps({'error': 'payload too large', 'error_code': 'PAYLOAD_TOO_LARGE'}),
                status=413,
                mimetype='application/json',
            )

        store_config = env['uva.store.config'].browse(store_id)
        if not store_config.exists() or not store_config.active:
            _logger.warning(
                "UvaOrderWebhookController: store_id=%s not found or inactive", store_id
            )
            return Response(
                json.dumps({'error': 'store not found', 'error_code': 'STORE_NOT_FOUND'}),
                status=400,
                mimetype='application/json',
            )

        # Step 2: Validate HMAC — fail closed (SECURITY-15, BR-01)
        raw_body = request.httprequest.get_data()
        # Enforce body size limit (handles chunked encoding bypass of Content-Length check)
        if len(raw_body) > 1_048_576:
            return Response(
                json.dumps({'error': 'payload too large', 'error_code': 'PAYLOAD_TOO_LARGE'}),
                status=413,
                mimetype='application/json',
            )
        signature = request.httprequest.headers.get('X-Uva-Signature', '')
        webhook_secret = store_config.sudo().webhook_secret or ''

        if not webhook_secret:
            _logger.warning(
                "UvaOrderWebhookController: webhook_secret not configured for store_id=%s",
                store_id,
            )
            return Response(
                json.dumps({'error': 'webhook not configured', 'error_code': 'NOT_CONFIGURED'}),
                status=403,
                mimetype='application/json',
            )

        # Support dual secrets for rotation — accept current or previous
        webhook_secret_prev = store_config.sudo().webhook_secret_previous or ''
        hmac_valid = env['uva.api.client'].validate_hmac(raw_body, signature, webhook_secret)
        if not hmac_valid and webhook_secret_prev:
            hmac_valid = env['uva.api.client'].validate_hmac(raw_body, signature, webhook_secret_prev)
        if not hmac_valid:
            _security_logger.warning(
                "HMAC_FAIL store_id=%s ip=%s",
                store_id, request.httprequest.remote_addr,
            )
            return Response(
                json.dumps({'error': 'forbidden', 'error_code': 'HMAC_INVALID'}),
                status=403,
                mimetype='application/json',
            )

        # Nonce check — reject replayed requests within TTL window
        request_id = request.httprequest.headers.get('X-Uva-Request-Id', '')
        if not _check_nonce(request_id):
            _security_logger.warning(
                "NONCE_REPLAY store_id=%s request_id=%s ip=%s",
                store_id, request_id, request.httprequest.remote_addr,
            )
            return Response(
                json.dumps({'error': 'duplicate request', 'error_code': 'DUPLICATE_REQUEST'}),
                status=409,
                mimetype='application/json',
            )

        # Replay protection — reject payloads with stale timestamps (5-minute window)
        try:
            _payload_peek = json.loads(raw_body)
            ts = _payload_peek.get('timestamp') or _payload_peek.get('created_at')
            if not ts:
                _logger.warning(
                    "UvaOrderWebhookController: missing timestamp for store_id=%s", store_id
                )
                return Response(
                    json.dumps({'error': 'missing timestamp', 'error_code': 'MISSING_TIMESTAMP'}),
                    status=400,
                    mimetype='application/json',
                )
            ts_dt = odoo_fields.Datetime.to_datetime(ts)
            if ts_dt and abs((odoo_fields.Datetime.now() - ts_dt).total_seconds()) > 300:
                _logger.warning(
                    "UvaOrderWebhookController: stale timestamp for store_id=%s", store_id
                )
                return Response(
                    json.dumps({'error': 'stale request', 'error_code': 'STALE_TIMESTAMP'}),
                    status=400,
                    mimetype='application/json',
                )
        except (json.JSONDecodeError, ValueError, TypeError):
            return Response(
                json.dumps({'error': 'invalid timestamp', 'error_code': 'INVALID_TIMESTAMP'}),
                status=400,
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
                json.dumps({'error': 'invalid JSON', 'error_code': 'INVALID_JSON'}),
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
                json.dumps({'error': 'internal error', 'error_code': 'INTERNAL_ERROR'}),
                status=500,
                mimetype='application/json',
            )
