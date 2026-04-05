# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
from odoo import _, fields, models
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

    # Convenience / display
    store_name = fields.Char(related='store_id.name', string='Store Name', readonly=True)

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
        body = _("Order rejected.")
        if reason:
            body += f" Reason: {reason}"
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
            body=_("POS order creation failed: %(reason)s", reason=reason),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )


