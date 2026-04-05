# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

_MIN_POLLING_INTERVAL = 60  # seconds — NFR-04.2


class UvaStoreConfig(models.Model):
    _name = 'uva.store.config'
    _description = 'Uva PR Store Configuration'
    _order = 'name'

    # ------------------------------------------------------------------
    # Fields (D-04 addendum: uva.store.config key fields)
    # ------------------------------------------------------------------

    name = fields.Char(string='Store Name', required=True)
    pos_config_id = fields.Many2one(
        'pos.config', string='POS Configuration',
        required=True, ondelete='restrict',
        help='POS session this store config is associated with.',
    )

    # Credentials — system admin only (SECURITY-08, SECURITY-12)
    api_key = fields.Char(
        string='Uva API Key',
        groups='base.group_system',
        help='Uva Orders API key. Visible to system administrators only.',
    )
    webhook_secret = fields.Char(
        string='Webhook Secret',
        groups='base.group_system',
        help='Shared secret for HMAC validation of incoming Uva webhooks.',
    )

    # Behaviour
    auto_accept_timeout = fields.Integer(
        string='Auto-Accept Timeout (seconds)',
        default=120,
        help='Seconds before an unactioned order is auto-accepted. '
             'Set to 0 to require manual acceptance.',
    )
    active = fields.Boolean(default=True)
    demo_mode = fields.Boolean(
        string='Demo Mode',
        default=True,
        help='When enabled, all Uva API calls return mock responses. '
             'Enabled by default — disable only after configuring real API credentials.',
    )

    # Polling config (D-09: fast cron + per-record throttle)
    polling_enabled = fields.Boolean(
        string='Enable Polling Fallback',
        default=True,
        help='Poll the Uva Orders API as a fallback when webhooks are unavailable.',
    )
    polling_interval = fields.Integer(
        string='Polling Interval (seconds)',
        default=120,
        help='How often to poll the Uva Orders API. Minimum: 60 seconds.',
    )
    last_polled_at = fields.Datetime(
        string='Last Polled At',
        readonly=True,
        help='Timestamp of the last successful poll. Used for per-record throttle.',
    )

    # Computed display
    pos_session_state = fields.Selection(
        related='pos_config_id.current_session_state',
        string='POS Session State',
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    _sql_constraints = [
        ('pos_config_unique', 'UNIQUE(pos_config_id)',
         'Each POS configuration can only be linked to one Uva store config.'),
    ]

    @api.constrains('polling_interval')
    def _check_polling_interval(self):
        for rec in self:
            if rec.polling_enabled and rec.polling_interval < _MIN_POLLING_INTERVAL:
                raise ValidationError(
                    _('Polling interval must be at least %(min)s seconds to avoid '
                      'rate-limiting by the Uva API. Got: %(val)s seconds.',
                      min=_MIN_POLLING_INTERVAL, val=rec.polling_interval)
                )

    @api.constrains('auto_accept_timeout')
    def _check_auto_accept_timeout(self):
        for rec in self:
            if rec.auto_accept_timeout < 0:
                raise ValidationError(
                    _('Auto-accept timeout cannot be negative. Use 0 for manual-only mode.')
                )

    # ------------------------------------------------------------------
    # Business methods
    # ------------------------------------------------------------------

    def get_active_config_for_pos(self, pos_config_id):
        """Return the active store config for the given POS config ID.

        Raises UserError if no active config is found — callers (Unit 3 order
        routing, webhook controller) should not need to handle a None return.
        """
        config = self.search([
            ('pos_config_id', '=', pos_config_id),
            ('active', '=', True),
        ], limit=1)
        if not config:
            raise UserError(_(
                "No active Uva store configuration found for this POS session. "
                "Please configure a Uva store under Point of Sale > Configuration > Uva Stores."
            ))
        return config

    def poll_orders_if_due(self):
        """Check whether this store is due for a polling cycle and execute if so.

        Implements the D-09 per-record throttle: only calls the API if
        polling_interval seconds have elapsed since last_polled_at.
        Called by the order polling cron (Unit 3).
        """
        self.ensure_one()
        if not self.polling_enabled or not self.active:
            return False

        now = fields.Datetime.now()
        if self.last_polled_at:
            elapsed = (now - self.last_polled_at).total_seconds()
            if elapsed < self.polling_interval:
                return False  # not due yet

        # Mark as polled immediately to prevent concurrent cron overlap
        self.sudo().write({'last_polled_at': now})
        return True  # caller should proceed with API poll

    def get_api_client(self):
        """Return the uva.api.client AbstractModel (convenience accessor)."""
        return self.env['uva.api.client']

    def action_test_connection(self):
        """Test the Uva API connection using the configured credentials.

        # TODO(uva-api): implement once a suitable test/ping endpoint is confirmed.
        """
        self.ensure_one()
        raise NotImplementedError(
            "TODO(uva-api): connection test endpoint not yet confirmed. "
            "Implement once Uva API docs are received."
        )

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def name_get(self):
        result = []
        for rec in self:
            pos_name = rec.pos_config_id.name if rec.pos_config_id else '?'
            label = f"{rec.name} ({pos_name})"
            if rec.demo_mode:
                label += ' [DEMO]'
            if not rec.active:
                label += ' [inactive]'
            result.append((rec.id, label))
        return result
