# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import json
import time

from odoo import http
from odoo.http import request

_TRACKING_STRINGS = {
    'en': {
        'title': 'Delivery Tracking',
        'status': 'Status',
        'last_updated': 'Last Updated',
        'driver': 'Driver',
        'eta': 'Estimated Arrival',
        'updating': 'Updating live...',
        'final': 'Final status',
        'notify_btn': 'Notify me when arriving',
        'notify_on': 'Notifications enabled',
        'pending': 'Order Placed',
        'assigned': 'Driver Assigned',
        'in_transit': 'On the Way',
        'delivered': 'Delivered',
        'cancelled': 'Cancelled',
        'failed': 'Failed',
        'footer': 'Powered by',
    },
    'es': {
        'title': 'Rastreo de Entrega',
        'status': 'Estado',
        'last_updated': 'Última Actualización',
        'driver': 'Conductor',
        'eta': 'Llegada Estimada',
        'updating': 'Actualizando en vivo...',
        'final': 'Estado final',
        'notify_btn': 'Notificarme cuando llegue',
        'notify_on': 'Notificaciones activadas',
        'pending': 'Pedido Recibido',
        'assigned': 'Conductor Asignado',
        'in_transit': 'En Camino',
        'delivered': 'Entregado',
        'cancelled': 'Cancelado',
        'failed': 'Fallido',
        'footer': 'Impulsado por',
    },
}

_RATE_LIMIT_MAX = 30
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX_KEYS = 500
_rate_limit_counters = {}


def _check_rate_limit(key):
    now = time.monotonic()
    entry = _rate_limit_counters.get(key)
    if entry is None or (now - entry[1]) > _RATE_LIMIT_WINDOW:
        if len(_rate_limit_counters) >= _RATE_LIMIT_MAX_KEYS:
            oldest = min(_rate_limit_counters, key=lambda k: _rate_limit_counters[k][1])
            del _rate_limit_counters[oldest]
        _rate_limit_counters[key] = (1, now)
        return True
    count, start = entry
    if count >= _RATE_LIMIT_MAX:
        return False
    _rate_limit_counters[key] = (count + 1, start)
    return True


class UvaTrackingController(http.Controller):

    def _get_tracking_lang(self):
        accept = request.httprequest.headers.get('Accept-Language', '')
        first_lang = accept.lower().split(',')[0].strip()
        return 'es' if first_lang.startswith('es') else 'en'

    @http.route('/uva/track/<string:tracking_id>', type='http', auth='public', website=False)
    def tracking_page(self, tracking_id, **kwargs):
        ip = request.httprequest.remote_addr or 'unknown'
        if not _check_rate_limit(f'track:{ip}'):
            return request.make_response('Too many requests', status=429)

        delivery = request.env['uva.fleet.delivery'].sudo().search(
            [('uva_delivery_id', '=', tracking_id)], limit=1,
        )
        if not delivery:
            return request.not_found()
        lang = self._get_tracking_lang()
        lang_strings = _TRACKING_STRINGS.get(lang, _TRACKING_STRINGS['en'])
        # Pass data as JSON in a data attribute to avoid XSS via JS string interpolation
        tracking_data = json.dumps({
            'tracking_id': delivery.uva_delivery_id,
            'state': delivery.state,
            'eta_minutes': delivery.eta_minutes or 0,
            'driver_lat': delivery.driver_lat or 0,
            'driver_lng': delivery.driver_lng or 0,
            'delivery_lat': delivery.delivery_lat or 0,
            'delivery_lng': delivery.delivery_lng or 0,
            'lang': lang_strings,
            # Intentionally omit driver_name and driver_phone from public page
        })
        return request.render('odoo_uva_connector.tracking_page', {
            'delivery': delivery,
            'tracking_data_json': tracking_data,
            'lang': lang_strings,
        })

    @http.route('/uva/track/<string:tracking_id>/status', type='http', auth='public',
                methods=['GET'], csrf=False)
    def tracking_status_json(self, tracking_id, **kwargs):
        ip = request.httprequest.remote_addr or 'unknown'
        if not _check_rate_limit(f'track:{ip}'):
            return request.make_response(
                json.dumps({'error': 'rate limit exceeded'}),
                status=429,
                headers=[('Content-Type', 'application/json')],
            )

        delivery = request.env['uva.fleet.delivery'].sudo().search(
            [('uva_delivery_id', '=', tracking_id)], limit=1,
        )
        if not delivery:
            return request.not_found()
        # Do NOT expose driver_name, driver_phone, or precise driver GPS to public
        data = {
            'state': delivery.state,
            'eta_minutes': delivery.eta_minutes or 0,
            'driver_lat': round(delivery.driver_lat, 3) if delivery.driver_lat else 0,
            'driver_lng': round(delivery.driver_lng, 3) if delivery.driver_lng else 0,
            'updated_at': str(delivery.last_status_at or delivery.write_date or ''),
        }
        return request.make_response(
            json.dumps(data),
            headers=[('Content-Type', 'application/json')],
        )
