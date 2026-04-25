# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
from datetime import timedelta

from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class UvaOrderLog(models.Model):
    _name = 'uva.order.log'
    _description = 'Uva PR Incoming Order Log'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'received_at desc'

    # ------------------------------------------------------------------
    # Fields (D-04 addendum: uva.order.log key fields)
    # ------------------------------------------------------------------

    external_id = fields.Char(
        string='Uva Order ID',
        required=True,
        index=True,
        help='Uva external order identifier. Used for deduplication.',
    )
    company_id = fields.Many2one(
        'res.company', related='store_id.company_id', store=True, index=True,
    )
    store_id = fields.Many2one(
        'uva.store.config',
        string='Store',
        required=True,
        ondelete='restrict',
        index=True,
    )
    pos_order_id = fields.Many2one(
        'pos.order',
        string='POS Order',
        readonly=True,
        help='Populated when the order transitions to done state.',
    )
    raw_payload = fields.Text(
        string='Raw Payload',
        readonly=True,
        groups='base.group_system',
        help='Original JSON payload from Uva. PII — visible to system admins only. '
             'Purged after 30 days by scheduled action.',
    )
    state = fields.Selection([
        ('draft',    'New'),
        ('pending',  'Awaiting Mapping'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('done',     'Processed'),
        ('error',    'Error'),
    ], string='State', default='draft', required=True, index=True, tracking=True)
    received_at = fields.Datetime(
        string='Received At',
        required=True,
        default=fields.Datetime.now,
    )
    processed_at = fields.Datetime(
        string='Processed At',
        readonly=True,
        help='Set when the order transitions to done or error state.',
    )

    # Tip handling (#3)
    tip_amount = fields.Float(string='Tip Amount', default=0.0)

    # Preparation time tracking (#4)
    prep_started_at = fields.Datetime(string='Preparation Started')
    prep_ready_at = fields.Datetime(string='Ready for Pickup')
    prep_time_minutes = fields.Float(
        string='Prep Time (min)', compute='_compute_prep_time', store=True,
    )

    # Convenience / display
    store_name = fields.Char(related='store_id.name', string='Store Name', readonly=True)
    uva_source = fields.Char(string='Order Source', default='')

    # Analytics computed fields
    processing_time = fields.Float(
        string='Processing Time (hours)',
        compute='_compute_processing_time',
        store=True,
        help='Hours between received_at and processed_at.',
    )
    date_received = fields.Date(
        string='Date Received',
        compute='_compute_date_received',
        store=True,
        help='Date portion of received_at, for grouping.',
    )

    @api.depends('received_at', 'processed_at')
    def _compute_processing_time(self):
        for rec in self:
            if rec.received_at and rec.processed_at:
                delta = rec.processed_at - rec.received_at
                rec.processing_time = delta.total_seconds() / 3600.0
            else:
                rec.processing_time = 0.0

    @api.depends('received_at')
    def _compute_date_received(self):
        for rec in self:
            rec.date_received = rec.received_at.date() if rec.received_at else False

    @api.depends('prep_started_at', 'prep_ready_at')
    def _compute_prep_time(self):
        for rec in self:
            if rec.prep_started_at and rec.prep_ready_at:
                rec.prep_time_minutes = (rec.prep_ready_at - rec.prep_started_at).total_seconds() / 60.0
            else:
                rec.prep_time_minutes = 0.0

    def action_start_preparing(self):
        self.ensure_one()
        if self.state != 'accepted':
            raise UserError(_('Can only start preparing accepted orders.'))
        if self.prep_started_at:
            return  # already started — idempotent
        self.write({'prep_started_at': fields.Datetime.now()})
        self.message_post(body=_('Preparation started.'), message_type='notification', subtype_xmlid='mail.mt_note')

    def action_mark_ready(self):
        self.ensure_one()
        if self.state != 'accepted':
            raise UserError(_('Can only mark accepted orders as ready.'))
        if not self.prep_started_at:
            raise UserError(_('Cannot mark ready before starting preparation.'))
        if self.prep_ready_at:
            return  # already marked — idempotent
        self.write({'prep_ready_at': fields.Datetime.now()})
        self.message_post(body=_('Order ready for pickup.'), message_type='notification', subtype_xmlid='mail.mt_note')

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    _sql_constraints = [
        ('external_id_unique', 'UNIQUE(external_id)',
         'An order with this Uva external ID already exists. Duplicate orders are rejected.'),
    ]

    # ------------------------------------------------------------------
    # State machine — action methods
    # All transitions are explicit methods; never write state directly from outside.
    # ------------------------------------------------------------------

    def action_accept(self, unavailable_items=None):
        """Transition draft/pending → accepted.

        Only manages state transition and chatter. Does NOT call _notify_uva_status —
        that is the responsibility of uva.order.service (D-03: service orchestrates model).
        Returns self for chaining.
        """
        self.ensure_one()
        if self.state not in ('draft', 'pending'):
            raise UserError(_(
                "Cannot accept an order in state '%(state)s'. "
                "Only 'New' or 'Awaiting Mapping' orders can be accepted.",
                state=self.state,
            ))
        self.write({'state': 'accepted'})
        self.message_post(
            body=_("Order accepted by staff."),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )
        return self

    def action_reject(self, reason=''):
        """Transition draft/pending/error → rejected.

        Only manages state transition and chatter. Does NOT call _notify_uva_status —
        that is the responsibility of uva.order.service (D-03: service orchestrates model).
        Returns self for chaining.
        """
        self.ensure_one()
        if self.state not in ('draft', 'pending', 'error'):
            raise UserError(_(
                "Cannot reject an order in state '%(state)s'.",
                state=self.state,
            ))
        self.write({'state': 'rejected'})
        if reason:
            body = _("Order rejected. Reason: %(reason)s", reason=escape(reason))
        else:
            body = _("Order rejected.")
        self.message_post(
            body=body,
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )
        return self

    def action_retry(self):
        """Transition error → accepted.

        Re-attempts POS order creation. The UNIQUE(external_id) SQL constraint
        is the safety net against duplicate POS orders — no programmatic dedup
        lookup needed here. The service layer handles POS order creation after
        this transition.

        Returns self for chaining.
        """
        self.ensure_one()
        if self.state != 'error':
            raise UserError(_(
                "Only orders in 'Error' state can be retried. Current state: %(state)s",
                state=self.state,
            ))
        self.write({'state': 'accepted'})
        self.message_post(
            body=_("Order retry initiated by merchant."),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )
        return self

    def action_mark_done(self, pos_order):
        """Transition accepted → done. Sets pos_order_id and processed_at."""
        self.ensure_one()
        if self.state not in ('accepted', 'error'):
            raise UserError(_(
                "Cannot mark as done from state '%(state)s'.", state=self.state
            ))
        self.write({
            'state': 'done',
            'pos_order_id': pos_order.id,
            'processed_at': fields.Datetime.now(),
        })

    def action_modify(self, modifications=None):
        """Transition accepted -> accepted (with modifications)."""
        self.ensure_one()
        if self.state != 'accepted':
            raise UserError(_('Can only modify accepted orders.'))
        self.message_post(
            body=_('Order modified: %s', escape(str(modifications or {}))),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )
        return self

    def action_mark_error(self, reason):
        """Transition accepted → error. Posts chatter with failure reason."""
        self.ensure_one()
        if self.state != 'accepted':
            raise UserError(_(
                "Cannot mark as error from state '%(state)s'.", state=self.state
            ))
        self.write({
            'state': 'error',
            'processed_at': fields.Datetime.now(),
        })
        self.message_post(
            body=_("POS order creation failed: %(reason)s", reason=escape(reason)),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )

    # ------------------------------------------------------------------
    # Dashboard stats
    # ------------------------------------------------------------------

    @api.model
    def get_dashboard_stats(self):
        """Return KPI data for the Uva dashboard client action."""
        today = fields.Date.today()
        domain_today = [('received_at', '>=', fields.Datetime.to_string(today))]
        today_count = self.search_count(domain_today)
        accepted_count = self.search_count(domain_today + [('state', '=', 'accepted')])
        done_count = self.search_count(domain_today + [('state', '=', 'done')])
        error_count = self.search_count(domain_today + [('state', '=', 'error')])

        # Acceptance rate
        acceptance_rate = 0.0
        if today_count:
            acceptance_rate = round((accepted_count + done_count) / today_count * 100, 1)

        # Avg processing minutes for done orders today
        done_today = self.search(domain_today + [('state', '=', 'done'), ('processing_time', '>', 0)])
        avg_processing_minutes = 0.0
        if done_today:
            avg_processing_minutes = round(
                sum(done_today.mapped('processing_time')) / len(done_today) * 60, 1
            )

        # Retry queue
        pending_retry_count = self.env['uva.api.retry.queue'].search_count(
            [('state', '=', 'pending')]
        )

        # Fleet stats
        FleetDel = self.env['uva.fleet.delivery']
        active_deliveries = FleetDel.search_count(
            [('state', 'not in', ['delivered', 'cancelled', 'failed'])]
        )
        fleet_today = [('create_date', '>=', fields.Datetime.to_string(today))]
        delivered_today = FleetDel.search_count(fleet_today + [('state', '=', 'delivered')])
        failed_today = FleetDel.search_count(fleet_today + [('state', '=', 'failed')])

        return {
            'today_count': today_count,
            'accepted_count': accepted_count,
            'done_count': done_count,
            'error_count': error_count,
            'acceptance_rate': acceptance_rate,
            'avg_processing_minutes': avg_processing_minutes,
            'pending_retry_count': pending_retry_count,
            'active_deliveries': active_deliveries,
            'delivered_today': delivered_today,
            'failed_today': failed_today,
            'store_stats': self._get_per_store_stats(today),
        }

    @api.model
    def _get_per_store_stats(self, today):
        """Per-store breakdown for dashboard."""
        stores = self.env['uva.store.config'].search([('active', '=', True)])
        result = []
        dt_today = fields.Datetime.to_string(today)
        for s in stores:
            domain = [('store_id', '=', s.id), ('received_at', '>=', dt_today)]
            total = self.search_count(domain)
            accepted = self.search_count(domain + [('state', 'in', ('accepted', 'done'))])
            errors = self.search_count(domain + [('state', '=', 'error')])
            done = self.search(domain + [('state', '=', 'done'), ('processing_time', '>', 0)])
            avg_min = round(sum(done.mapped('processing_time')) / len(done) * 60, 1) if done else 0.0
            result.append({
                'store_id': s.id,
                'store_name': s.name,
                'order_count': total,
                'acceptance_rate': round(accepted / total * 100, 1) if total else 0.0,
                'avg_minutes': avg_min,
                'error_rate': round(errors / total * 100, 1) if total else 0.0,
            })
        return result

    # ------------------------------------------------------------------
    # Cron: PII purge (L5 — moved from inline XML code to model method)
    # ------------------------------------------------------------------

    @api.model
    def get_error_orders(self, store_id):
        """Return error orders for a given store (POS error recovery)."""
        orders = self.search([
            ('store_id', '=', store_id),
            ('state', '=', 'error'),
        ], order='received_at desc', limit=50)
        return [{
            'id': o.id,
            'external_id': o.external_id,
            'received_at': fields.Datetime.to_string(o.received_at),
            'store_name': o.store_name,
        } for o in orders]

    @api.model
    def purge_raw_payloads(self, days=30):
        """Clear raw_payload on records older than `days` days (GDPR/PII)."""
        cutoff = fields.Datetime.now() - timedelta(days=days)
        records = self.sudo().search([
            ('received_at', '<', cutoff),
            ('raw_payload', '!=', False),
        ])
        records.write({'raw_payload': False})


