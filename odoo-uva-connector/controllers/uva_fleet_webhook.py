# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import json
import logging
import time

from odoo import http, SUPERUSER_ID
from odoo.http import request, Response

_logger = logging.getLogger(__name__)

_RATE_LIMIT_MAX = 60
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX_KEYS = 1000
_rate_limit_counters = {}


def _check_rate_limit(key):
    now = time.monotonic()
    entry = _rate_limit_counters.get(key)
    if entry is None or (now - entry[1]) > _RATE_LIMIT_WINDOW:
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


class UvaFleetStatusWebhookController(http.Controller):

    @http.route(
        '/uva/webhook/fleet/<int:company_id>',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False,
    )
    def receive_status(self, company_id, **kwargs):
        """Receive a Uva Fleet delivery status update via webhook.

        Thin controller (D-03):
        1. Browse company by company_id — return 400 if not found
        2. Get Fleet webhook_secret from ir.config_parameter
        3. Validate HMAC — fail closed on any failure
        4. Parse JSON body
        5. Delegate to uva.fleet.service.process_status_update
        6. Return JSON response

        Security: auth='none' — HMAC is the authentication mechanism (D-05 addendum).
        """
        env = request.env(user=SUPERUSER_ID)

        # Rate limiting (SECURITY-11)
        if not _check_rate_limit(company_id):
            _logger.warning(
                "UvaFleetStatusWebhookController: rate limit exceeded for company_id=%s", company_id
            )
            return Response(
                json.dumps({'error': 'rate limit exceeded'}),
                status=429,
                mimetype='application/json',
            )

        # Step 1: Verify company exists
        company = env['res.company'].browse(company_id)
        if not company.exists():
            _logger.warning(
                "UvaFleetStatusWebhookController: company_id=%s not found", company_id
            )
            return Response(
                json.dumps({'error': 'company not found'}),
                status=400,
                mimetype='application/json',
            )

        # Step 2: Get Fleet webhook secret from ir.config_parameter
        ICP = env['ir.config_parameter'].sudo()
        webhook_secret = ICP.get_param('uva.fleet.webhook_secret', '')

        # Step 3: Validate HMAC — fail closed (SECURITY-15, BR-01)
        raw_body = request.httprequest.get_data()
        signature = request.httprequest.headers.get('X-Uva-Signature', '')

        if not env['uva.api.client'].validate_hmac(raw_body, signature, webhook_secret):
            _logger.warning(
                "UvaFleetStatusWebhookController: HMAC validation failed for company_id=%s",
                company_id,
            )
            return Response(
                json.dumps({'error': 'invalid signature'}),
                status=400,
                mimetype='application/json',
            )

        # Step 4: Parse JSON body
        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError) as exc:
            _logger.warning(
                "UvaFleetStatusWebhookController: invalid JSON for company_id=%s: %s",
                company_id, exc,
            )
            return Response(
                json.dumps({'error': 'invalid JSON'}),
                status=400,
                mimetype='application/json',
            )

        # Step 5: Delegate to service
        # TODO(uva-api): confirm exact payload field names from Uva Fleet webhook docs
        delivery_id = payload.get('delivery_id') or payload.get('id', '')
        status = payload.get('status', '')
        updated_at = payload.get('updated_at') or payload.get('timestamp', '')

        try:
            env['uva.fleet.service'].process_status_update(
                delivery_id=delivery_id,
                status=status,
                updated_at=updated_at,
            )
            return Response(
                json.dumps({'status': 'ok'}),
                status=200,
                mimetype='application/json',
            )
        except Exception as exc:
            _logger.error(
                "UvaFleetStatusWebhookController: error processing status for company_id=%s: %s",
                company_id, exc, exc_info=True,
            )
            return Response(
                json.dumps({'error': 'internal error'}),
                status=500,
                mimetype='application/json',
            )
