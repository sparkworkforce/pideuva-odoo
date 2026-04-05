# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import logging

from odoo import _, api, fields, models

from .uva_api_client import UvaApiError

_logger = logging.getLogger(__name__)

# Uva Fleet status → Odoo uva.fleet.delivery state mapping
# TODO(uva-api): confirm exact status strings from Uva Fleet API docs
_UVA_STATUS_MAP = {
    'pending':    'pending',
    'assigned':   'assigned',
    'picked_up':  'in_transit',
    'in_transit': 'in_transit',
    'delivered':  'delivered',
    'cancelled':  'cancelled',
    'failed':     'failed',
}

# States that are terminal — no further polling needed
_TERMINAL_STATES = frozenset({'delivered', 'cancelled', 'failed'})

# Polling throttle: minimum seconds between status polls per delivery
_MIN_POLL_INTERVAL = 60  # seconds


class UvaFleetService(models.AbstractModel):
    _name = 'uva.fleet.service'
    _description = 'Uva Fleet Status Service'

    # ------------------------------------------------------------------
    # Main entry point — called by webhook and polling cron
    # ------------------------------------------------------------------

    @api.model
    def process_status_update(self, delivery_id, status, updated_at):
        """Process a Uva Fleet delivery status update.

        Finds the linked uva.fleet.delivery record, updates its state,
        and posts a chatter message to both the stock.picking and sale.order.

        Args:
            delivery_id (str): Uva Fleet delivery tracking ID
            status (str): Uva status string (e.g. 'in_transit', 'delivered')
            updated_at (datetime|str): Timestamp of the status update
        """
        fleet_delivery = self.env['uva.fleet.delivery'].search(
            [('uva_delivery_id', '=', delivery_id)], limit=1
        )
        if not fleet_delivery:
            _logger.warning(
                "[uva:%s] process_status_update: no fleet delivery found",
                delivery_id,
            )
            return

        odoo_state = self._map_uva_status(status)
        if not odoo_state:
            _logger.warning(
                "[uva:%s] process_status_update: unknown Uva status '%s'",
                delivery_id, status,
            )
            return

        # Update state and last_status_at (used by polling throttle)
        fleet_delivery.write({
            'state': odoo_state,
            'last_status_at': fields.Datetime.now(),
        })

        # Post chatter to picking (FR-06.3)
        if fleet_delivery.picking_id:
            self._post_chatter(fleet_delivery.picking_id, status, updated_at)

        # Post chatter to sale order (FR-06.3)
        if fleet_delivery.sale_order_id:
            self._post_chatter(fleet_delivery.sale_order_id, status, updated_at)

    # ------------------------------------------------------------------
    # Polling cron entry point (D-09: fast cron + per-record throttle)
    # ------------------------------------------------------------------

    @api.model
    def poll_active_deliveries(self):
        """Called by the fleet status polling cron every minute.

        Iterates all non-terminal uva.fleet.delivery records and polls
        Uva Fleet for status updates. Per-record throttle via last_status_at
        prevents hitting the API on every cron tick (D-09 pattern).
        """
        active_deliveries = self.env['uva.fleet.delivery'].search([
            ('state', 'not in', list(_TERMINAL_STATES)),
        ])

        ICP = self.env['ir.config_parameter'].sudo()
        api_key = ICP.get_param('uva.fleet.api_key', '')
        demo_mode_raw = ICP.get_param('uva.fleet.demo_mode', 'False')
        demo_mode = demo_mode_raw in ('True', '1', 'true')
        client = self.env['uva.api.client']

        for delivery in active_deliveries:
            if not self._is_poll_due(delivery):
                continue
            try:
                with self.env.cr.savepoint():
                    result = client.get_delivery_status(
                        api_key=api_key,
                        delivery_id=delivery.uva_delivery_id,
                        demo_mode=demo_mode,
                    )
                    self.process_status_update(
                        delivery_id=delivery.uva_delivery_id,
                        status=result.get('status', ''),
                        updated_at=result.get('updated_at', fields.Datetime.now()),
                    )
            except UvaApiError as exc:
                _logger.warning(
                    "[uva:%s] poll_active_deliveries: API error: %s",
                    delivery.uva_delivery_id, exc,
                )
            except Exception as exc:
                _logger.error(
                    "[uva:%s] poll_active_deliveries: unexpected error: %s",
                    delivery.uva_delivery_id, exc, exc_info=True,
                )

    def _is_poll_due(self, delivery):
        """Return True if enough time has elapsed since last_status_at."""
        if not delivery.last_status_at:
            return True
        elapsed = (fields.Datetime.now() - delivery.last_status_at).total_seconds()
        return elapsed >= _MIN_POLL_INTERVAL

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @api.model
    def _post_chatter(self, record, status, updated_at):
        """Post a formatted status update message to a record's chatter."""
        if not hasattr(record, 'message_post'):
            return
        label = self._status_label(status)
        body = _(
            "🛵 <b>Uva Fleet Update</b>: %(label)s<br/>"
            "Status: <code>%(status)s</code><br/>"
            "Updated at: %(updated_at)s",
            label=label,
            status=status,
            updated_at=str(updated_at),
        )
        record.message_post(
            body=body,
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )

    @api.model
    def _map_uva_status(self, uva_status):
        """Map a Uva Fleet status string to an Odoo uva.fleet.delivery state.

        Returns None if the status is unknown.
        # TODO(uva-api): confirm exact status strings from Uva Fleet API docs
        """
        return _UVA_STATUS_MAP.get(uva_status.lower() if uva_status else '', None)

    @api.model
    def _status_label(self, status):
        """Return a human-readable label for a Uva status string."""
        labels = {
            'pending':    'Awaiting Driver',
            'assigned':   'Driver Assigned',
            'picked_up':  'Package Picked Up',
            'in_transit': 'In Transit',
            'delivered':  'Delivered',
            'cancelled':  'Cancelled',
            'failed':     'Delivery Failed',
        }
        return labels.get(status.lower() if status else '', status)
