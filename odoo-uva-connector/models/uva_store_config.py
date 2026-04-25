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
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    # ------------------------------------------------------------------
    # Fields (D-04 addendum: uva.store.config key fields)
    # ------------------------------------------------------------------

    name = fields.Char(string='Store Name', required=True)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True,
    )
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
    notification_enabled = fields.Boolean(
        string='Enable Customer Notifications',
        default=False,
        help='Send notifications to customers on order/delivery events.',
    )
    demo_mode = fields.Boolean(
        string='Demo Mode',
        default=True,
        help='When enabled, all Uva API calls return mock responses. '
             'Enabled by default — disable only after configuring real API credentials.',
    )
    sandbox_mode = fields.Boolean(
        string='Sandbox Mode',
        default=False,
        help='Route API calls to the Uva sandbox environment for testing.',
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
    mapping_confidence_threshold = fields.Float(
        string='Auto-Map Confidence (%)',
        default=90.0,
        help='Fuzzy matches above this confidence are auto-applied during bulk mapping. Range: 0-100.',
    )
    last_polled_at = fields.Datetime(
        string='Last Polled At',
        readonly=True,
        help='Timestamp of the last successful poll. Used for per-record throttle.',
    )

    # Delivery zone (Feature #5)
    store_lat = fields.Float(string='Latitude', default=18.4655)
    store_lng = fields.Float(string='Longitude', default=-66.1057)
    delivery_zone_radius = fields.Float(string='Delivery Zone Radius (km)', default=5.0)

    # Menu sync (Feature #6)
    menu_sync_enabled = fields.Boolean(string='Enable Menu Sync')

    # Performance alerts (Feature #8)
    alert_enabled = fields.Boolean(string='Enable Performance Alerts')
    alert_acceptance_threshold = fields.Float(string='Acceptance Rate Threshold (%)', default=80.0)

    # Store hours (#7)
    store_hours_enabled = fields.Boolean(string='Enforce Store Hours', default=False)
    opening_time = fields.Float(string='Opening Time', default=8.0, help='24h format, e.g. 8.0 = 08:00')
    closing_time = fields.Float(string='Closing Time', default=22.0, help='24h format, e.g. 22.0 = 22:00')
    uva_order_count_today = fields.Integer(compute='_compute_store_stats')
    uva_acceptance_rate = fields.Float(compute='_compute_store_stats')
    uva_avg_processing_minutes = fields.Float(compute='_compute_store_stats')
    uva_error_rate = fields.Float(compute='_compute_store_stats')

    # Computed display
    pos_session_state = fields.Selection(
        related='pos_config_id.current_session_state',
        string='POS Session State',
        readonly=True,
    )

    connection_health = fields.Selection(
        [('ok', 'OK'), ('degraded', 'Degraded'), ('down', 'Down')],
        string='Connection Health',
        compute='_compute_connection_health',
    )

    webhook_url = fields.Char(
        string='Webhook URL',
        compute='_compute_webhook_url',
    )

    @api.depends('id')
    def _compute_webhook_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for rec in self:
            rec.webhook_url = f"{base}/uva/webhook/orders/{rec.id}" if rec.id else ''

    uva_order_count = fields.Integer(
        string='Uva Orders', compute='_compute_uva_revenue', store=False,
    )
    uva_revenue = fields.Float(
        string='Uva Revenue', compute='_compute_uva_revenue', store=False,
    )

    def _compute_uva_revenue(self):
        """Compute order count and revenue using read_group to avoid N+1."""
        if not self.ids:
            return
        OrderLog = self.env['uva.order.log']
        data = OrderLog.read_group(
            [('store_id', 'in', self.ids), ('state', '=', 'done')],
            ['store_id'], ['store_id'],
        )
        count_map = {d['store_id'][0]: d['store_id_count'] for d in data}
        # Revenue: aggregate via pos_order_id
        revenue_map = {}
        done_logs = OrderLog.search([
            ('store_id', 'in', self.ids), ('state', '=', 'done'),
            ('pos_order_id', '!=', False),
        ])
        for log in done_logs:
            sid = log.store_id.id
            revenue_map[sid] = revenue_map.get(sid, 0) + (log.pos_order_id.amount_total or 0)
        for rec in self:
            rec.uva_order_count = count_map.get(rec.id, 0)
            rec.uva_revenue = revenue_map.get(rec.id, 0.0)
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

    @api.constrains('opening_time', 'closing_time', 'store_hours_enabled')
    def _check_store_hours(self):
        for rec in self:
            if rec.store_hours_enabled and rec.opening_time == rec.closing_time:
                raise ValidationError(
                    _('Opening and closing times cannot be the same when store hours are enabled.')
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

    def is_store_open(self):
        """Check if the store is currently open based on configured hours."""
        self.ensure_one()
        if not self.store_hours_enabled:
            return True
        from pytz import timezone as pytz_tz
        tz = pytz_tz(self.company_id.partner_id.tz or 'America/Puerto_Rico')
        now = fields.Datetime.now().replace(tzinfo=pytz_tz('UTC')).astimezone(tz)
        current_hour = now.hour + now.minute / 60.0
        if self.opening_time <= self.closing_time:
            return self.opening_time <= current_hour < self.closing_time
        else:  # overnight (e.g. 22:00 - 06:00)
            return current_hour >= self.opening_time or current_hour < self.closing_time

    def get_api_client(self):
        """Return the uva.api.client AbstractModel (convenience accessor)."""
        return self.env['uva.api.client']

    def action_test_connection(self):
        """Test the Uva API connection using the configured credentials."""
        self.ensure_one()
        if self.demo_mode:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Test'),
                    'message': _('Connection successful (demo mode).'),
                    'type': 'success',
                },
            }
        client = self.env['uva.api.client']
        try:
            client.health_check(api_key=self.sudo().api_key)
        except Exception as exc:
            raise UserError(_('Connection failed: %s', str(exc))) from exc
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Connection Test'),
                'message': _('Connection successful!'),
                'type': 'success',
            },
        }

    def action_register_webhooks(self):
        """Register webhook URLs with the Uva API."""
        self.ensure_one()
        if self.demo_mode:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Demo Mode'),
                    'message': _('Webhook registration simulated in demo mode.'),
                    'type': 'success',
                },
            }
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        order_url = f"{base_url}/uva/webhook/orders/{self.id}"
        fleet_url = f"{base_url}/uva/webhook/fleet/{self.env.company.id}"
        client = self.env['uva.api.client']
        try:
            client._request('POST', '/webhooks/register', self.sudo().api_key,
                            json={
                                'order_webhook_url': order_url,
                                'fleet_webhook_url': fleet_url,
                                'secret': self.sudo().webhook_secret,
                            })
        except Exception as exc:
            raise UserError(_('Webhook registration failed: %s', str(exc))) from exc
        self.message_post(
            body=_('Webhooks registered: %(order)s, %(fleet)s',
                   order=order_url, fleet=fleet_url),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Webhooks registered with Uva.'),
                'type': 'success',
            },
        }

    # ------------------------------------------------------------------
    # Computed stats (Feature #8)
    # ------------------------------------------------------------------

    def _compute_store_stats(self):
        today = fields.Datetime.to_string(fields.Date.today())
        OrderLog = self.env['uva.order.log']
        if not self.ids:
            return
        # Batch: get counts per store+state in 1 query
        data = OrderLog.read_group(
            [('store_id', 'in', self.ids), ('received_at', '>=', today)],
            ['store_id', 'state'], ['store_id', 'state'], lazy=False,
        )
        stats = {}  # {store_id: {state: count}}
        for d in data:
            sid = d['store_id'][0]
            stats.setdefault(sid, {})[d['state']] = d['__count']
        # Avg processing time per store in 1 query
        avg_data = OrderLog.read_group(
            [('store_id', 'in', self.ids), ('received_at', '>=', today),
             ('state', '=', 'done'), ('processing_time', '>', 0)],
            ['store_id', 'processing_time:avg'], ['store_id'],
        )
        avg_map = {d['store_id'][0]: d['processing_time'] for d in avg_data}
        for rec in self:
            s = stats.get(rec.id, {})
            total = sum(s.values())
            accepted = s.get('accepted', 0) + s.get('done', 0)
            errors = s.get('error', 0)
            rec.uva_order_count_today = total
            rec.uva_acceptance_rate = round(accepted / total * 100, 1) if total else 0.0
            rec.uva_error_rate = round(errors / total * 100, 1) if total else 0.0
            rec.uva_avg_processing_minutes = round(avg_map.get(rec.id, 0) * 60, 1)

    # ------------------------------------------------------------------
    # Menu sync (Feature #6)
    # ------------------------------------------------------------------

    def action_sync_menu(self):
        """Trigger a full menu sync for this store."""
        self.ensure_one()
        self.env['uva.menu.sync'].push_menu_update(self, 'full')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Menu Sync'),
                'message': _('Full menu sync triggered for %s.', self.name),
                'type': 'success',
            },
        }

    # ------------------------------------------------------------------
    # Performance alerts cron (Feature #8)
    # ------------------------------------------------------------------

    @api.model
    def check_performance_alerts(self):
        """Cron: check acceptance rate and post alerts for underperforming stores."""
        stores = self.search([('active', '=', True), ('alert_enabled', '=', True)])
        for store in stores:
            store.invalidate_recordset(['uva_order_count_today', 'uva_acceptance_rate'])
            if store.uva_order_count_today == 0:
                continue
            if store.uva_acceptance_rate < store.alert_acceptance_threshold:
                store.message_post(
                    body=_(
                        "⚠️ Acceptance rate is <b>%(rate)s%%</b> (threshold: %(threshold)s%%). "
                        "Orders today: %(count)s.",
                        rate=store.uva_acceptance_rate,
                        threshold=store.alert_acceptance_threshold,
                        count=store.uva_order_count_today,
                    ),
                    message_type='notification',
                    subtype_xmlid='mail.mt_note',
                )
                existing = self.env['mail.activity'].search([
                    ('res_model', '=', self._name),
                    ('res_id', '=', store.id),
                    ('summary', 'ilike', 'acceptance rate'),
                ], limit=1)
                if not existing:
                    store.activity_schedule(
                        'mail.mail_activity_data_todo',
                        summary=_("Low acceptance rate: %s%%", store.uva_acceptance_rate),
                        note=_("Store '%s' acceptance rate is below threshold.", store.name),
                    )
                # Bus notification for dashboard
                self.env['bus.bus']._sendone(
                    'uva_dashboard',
                    'uva_performance_alert',
                    {'store_id': store.id, 'store_name': store.name,
                     'acceptance_rate': store.uva_acceptance_rate},
                )

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    @api.depends('name', 'pos_config_id', 'demo_mode', 'active')
    def _compute_display_name(self):
        for rec in self:
            pos_name = rec.pos_config_id.name if rec.pos_config_id else '?'
            label = f"{rec.name} ({pos_name})"
            if rec.demo_mode:
                label += ' [DEMO]'
            if not rec.active:
                label += ' [inactive]'
            rec.display_name = label

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    @api.depends('last_polled_at', 'polling_interval', 'demo_mode', 'polling_enabled')
    def _compute_connection_health(self):
        for rec in self:
            rec.connection_health = rec.check_connection_health()

    def check_connection_health(self):
        """Return 'ok', 'degraded', or 'down' based on polling freshness."""
        self.ensure_one()
        if self.demo_mode:
            return 'ok'
        if not self.polling_enabled:
            return 'ok'
        if not self.last_polled_at:
            return 'down'
        elapsed = (fields.Datetime.now() - self.last_polled_at).total_seconds()
        threshold = self.polling_interval * 3
        if elapsed <= threshold:
            return 'ok'
        if elapsed <= threshold * 2:
            return 'degraded'
        return 'down'

    @api.model
    def action_notify_health_issues(self):
        """Cron: post chatter message and schedule activity for unhealthy stores."""
        stores = self.search([('active', '=', True)])
        for store in stores:
            status = store.check_connection_health()
            if status == 'ok':
                continue
            store.message_post(
                body=_(
                    "⚠️ Connection health is <b>%(status)s</b>. "
                    "Last polled: %(last)s.",
                    status=status,
                    last=store.last_polled_at or _('never'),
                ),
                message_type='notification',
                subtype_xmlid='mail.mt_note',
            )
            self.env['uva.order.service']._notify_pos_health(store, status)
            existing_activity = self.env['mail.activity'].search([
                ('res_model', '=', self._name),
                ('res_id', '=', store.id),
                ('summary', 'ilike', 'Uva connection'),
            ], limit=1)
            if not existing_activity:
                store.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=_("Uva connection %(status)s", status=status),
                    note=_("Store '%(name)s' health is %(status)s. Check API connectivity.",
                            name=store.name, status=status),
                )