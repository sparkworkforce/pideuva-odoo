# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from markupsafe import escape

from .uva_api_client import RETRYABLE_ACTIONS, UvaApiError, UvaAuthError, UvaCoverageError

_logger = logging.getLogger(__name__)

_DEFAULT_MAX_RETRIES = 5
_BACKOFF_BASE = 60       # seconds
_BACKOFF_CAP = 3600      # seconds (1 hour)


class UvaApiRetryQueue(models.Model):
    _name = 'uva.api.retry.queue'
    _description = 'Uva API Retry Queue'
    _order = 'next_retry_at asc'

    _ALLOWED_RES_MODELS = frozenset({
        'uva.order.log', 'uva.fleet.delivery', 'stock.picking',
    })

    # ------------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------------

    action_type = fields.Char(
        string='Action Type', required=True, index=True,
        help='Identifier for the operation to retry (e.g. notify_acceptance)',
    )
    company_id = fields.Many2one(
        'res.company', related='store_id.company_id', store=True,
    )
    payload = fields.Text(
        string='Payload', required=True,
        groups='base.group_system',
        help='JSON-serialized request body',
    )
    res_model = fields.Char(
        string='Related Model', required=True,
        help='Odoo model name of the triggering record',
    )
    res_id = fields.Integer(
        string='Related Record ID', required=True,
        help='ID of the triggering record',
    )
    store_id = fields.Many2one(
        'uva.store.config', string='Store Config',
        ondelete='restrict', index=True,
        help='Store config used to retrieve api_key at retry time. '
             'Cannot be deleted while pending retry entries exist (BR-05).',
    )
    error = fields.Text(string='Last Error', readonly=True)
    retry_count = fields.Integer(string='Retry Count', default=0, readonly=True)
    next_retry_at = fields.Datetime(
        string='Next Retry At', index=True,
        help='When the cron should next attempt this entry',
    )
    state = fields.Selection([
        ('pending',    'Pending'),
        ('done',       'Done'),
        ('failed',     'Failed'),
        ('discarded',  'Discarded'),
    ], string='State', default='pending', required=True, index=True, readonly=True)
    processed_at = fields.Datetime(string='Processed At', readonly=True)

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    def enqueue(self, action_type, payload, res_model, res_id, store_id, error=''):
        """Create a retry queue entry for a failed outbound API call.

        Raises ValueError for non-retryable errors (BR-04, BR-05, BR-11).
        """
        # BR-04: only retryable action types
        if action_type not in RETRYABLE_ACTIONS:
            raise ValueError(
                f"action_type '{action_type}' is not retryable. "
                f"Valid types: {sorted(RETRYABLE_ACTIONS)}"
            )
        # BR-05: store_id is mandatory
        if not store_id:
            raise ValueError(
                "store_id is required for retry queue entries — "
                "needed to retrieve api_key at retry time."
            )
        # BR-11: payload must be valid JSON string
        try:
            json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"payload must be a valid JSON string: {exc}") from exc

        # Validate res_model against allowlist
        if res_model not in self._ALLOWED_RES_MODELS:
            raise ValueError(
                f"res_model '{res_model}' is not allowed. "
                f"Valid models: {sorted(self._ALLOWED_RES_MODELS)}"
            )

        first_retry_at = fields.Datetime.now() + timedelta(seconds=_BACKOFF_BASE)
        entry = self.create({
            'action_type': action_type,
            'payload': payload,
            'res_model': res_model,
            'res_id': res_id,
            'store_id': store_id,
            'error': error,
            'retry_count': 0,
            'next_retry_at': first_retry_at,
            'state': 'pending',
        })
        _logger.info(
            "Uva retry queue: enqueued %s for %s(%s), next retry at %s",
            action_type, res_model, res_id, first_retry_at,
        )
        return entry

    # ------------------------------------------------------------------
    # Backoff computation (pure — PBT target)
    # ------------------------------------------------------------------

    def _compute_next_retry(self, retry_count):
        """Return next retry datetime using exponential backoff with cap.

        Invariants (PBT):
          - result(n+1) >= result(n) for all n >= 0
          - delay never exceeds _BACKOFF_CAP seconds
          - delay(0) == _BACKOFF_BASE seconds
        """
        delay = min(_BACKOFF_BASE * (2 ** retry_count), _BACKOFF_CAP)
        return fields.Datetime.now() + timedelta(seconds=delay)

    # ------------------------------------------------------------------
    # Cron processor (BR-10: process in chronological order)
    # ------------------------------------------------------------------

    @api.model
    def process_due_retries(self):
        """Process all pending retry entries whose next_retry_at <= now.

        Called by ir.cron every minute. Each entry is processed in its own
        savepoint so a failure on one entry does not affect others.
        """
        now = fields.Datetime.now()
        due_entries = self.search([
            ('state', '=', 'pending'),
            ('next_retry_at', '<=', now),
        ], limit=200)  # M5: batch limit to prevent cron worker timeout

        ICP = self.env['ir.config_parameter'].sudo()
        max_retries = int(ICP.get_param('uva.retry.max_attempts', _DEFAULT_MAX_RETRIES))

        for entry in due_entries:
            try:
                with self.env.cr.savepoint():
                    entry._execute_retry(max_retries)
            except Exception as exc:
                _logger.error(
                    "Uva retry queue: unexpected error processing entry %s: %s",
                    entry.id, exc, exc_info=True,
                )

    def _execute_retry(self, max_retries):
        """Execute a single retry attempt for this entry."""
        store = self.store_id
        if not store:
            _logger.warning(
                "Uva retry queue entry %s has no store_id — marking failed.", self.id
            )
            self._mark_failed("Store config no longer exists; cannot retrieve credentials.")
            return

        api_key = store.sudo().api_key
        demo_mode = store.demo_mode
        client = self.env['uva.api.client']

        try:
            self._dispatch_action(client, api_key, demo_mode)
            # Success
            self.write({
                'state': 'done',
                'processed_at': fields.Datetime.now(),
                'error': '',
            })
            _logger.info("Uva retry queue entry %s succeeded.", self.id)

        except (UvaCoverageError, UvaAuthError) as exc:
            # Permanent failure — do not retry
            _logger.warning(
                "Uva retry queue entry %s permanent failure: %s", self.id, exc
            )
            self._mark_failed(str(exc))

        except UvaApiError as exc:
            # Transient failure
            new_count = self.retry_count + 1
            if new_count >= max_retries:
                _logger.warning(
                    "Uva retry queue entry %s max retries (%s) exceeded: %s",
                    self.id, max_retries, exc,
                )
                self._mark_failed(str(exc))
            else:
                next_at = self._compute_next_retry(new_count)
                self.write({
                    'retry_count': new_count,
                    'next_retry_at': next_at,
                    'error': str(exc),
                })
                _logger.info(
                    "Uva retry queue entry %s retry %s/%s, next at %s",
                    self.id, new_count, max_retries, next_at,
                )

    def _dispatch_action(self, client, api_key, demo_mode):
        """Re-execute the stored operation via uva.api.client."""
        payload = json.loads(self.payload)
        action = self.action_type

        if action == 'notify_acceptance':
            client.confirm_order(
                api_key, payload['external_id'], 'accept',
                items=payload.get('items'), demo_mode=demo_mode,
            )
        elif action == 'notify_rejection':
            client.confirm_order(
                api_key, payload['external_id'], 'reject', demo_mode=demo_mode,
            )
        elif action == 'notify_modification':
            client.confirm_order(
                api_key, payload['external_id'], 'modify',
                items=payload.get('items'), demo_mode=demo_mode,
            )
        elif action == 'create_fleet_delivery':
            result = client.create_delivery(
                api_key, payload['pickup'], payload['destination'],
                payload['reference'], demo_mode=demo_mode,
            )
            # Create tracking record that the original dispatch would have created
            delivery_id = result.get('delivery_id', '')
            tracking_url = result.get('tracking_url', '')
            if delivery_id and self.res_model == 'stock.picking':
                picking = self.env['stock.picking'].browse(self.res_id)
                if picking.exists():
                    carrier = self.env['delivery.carrier'].search(
                        [('delivery_type', '=', 'uva')], limit=1,
                    )
                    if carrier:
                        self.env['uva.fleet.delivery'].create({
                            'uva_delivery_id': delivery_id,
                            'carrier_id': carrier.id,
                            'picking_id': picking.id,
                            'sale_order_id': picking.sale_id.id if picking.sale_id else False,
                            'company_id': picking.company_id.id,
                            'tracking_url': tracking_url,
                            'state': 'pending',
                        })
        elif action == 'cancel_fleet_delivery':
            client.cancel_delivery(api_key, payload['delivery_id'], demo_mode=demo_mode)
        else:
            raise UvaApiError(f"Unknown action_type in retry dispatch: {action}")

    def _mark_failed(self, reason):
        """Transition entry to failed state and notify merchant via chatter."""
        self.write({
            'state': 'failed',
            'processed_at': fields.Datetime.now(),
            'error': reason,
        })
        # BR-07: post chatter notification on linked record
        try:
            if self.res_model not in self._ALLOWED_RES_MODELS:
                _logger.warning(
                    "Uva retry queue entry %s has unexpected res_model '%s'",
                    self.id, self.res_model,
                )
                return
            record = self.env[self.res_model].browse(self.res_id)
            if record.exists() and hasattr(record, 'message_post'):
                record.message_post(
                    body=_(
                        "⚠️ Uva API retry failed permanently for action <b>%(action)s</b>.<br/>"
                        "Reason: %(reason)s<br/>"
                        "Retry attempts: %(count)s<br/>"
                        "Please check the <a href='/web#model=uva.api.retry.queue&amp;id=%(id)s'>"
                        "retry queue entry</a> for details.",
                        action=escape(self.action_type),
                        reason=escape(reason),
                        count=self.retry_count,
                        id=self.id,
                    ),
                    message_type='notification',
                    subtype_xmlid='mail.mt_note',
                )
                # Also schedule a mail.activity so the failure appears in the activity view
                if hasattr(record, 'activity_schedule'):
                    try:
                        record.activity_schedule(
                            'mail.mail_activity_data_warning',
                            summary=_("Uva API retry failed: %(action)s", action=self.action_type),
                            note=_(
                                "Permanent failure after %(count)s retries. "
                                "Last error: %(reason)s",
                                count=self.retry_count,
                                reason=escape(reason),
                            ),
                        )
                    except Exception:
                        pass  # activity scheduling is best-effort
        except Exception as exc:
            _logger.warning(
                "Uva retry queue: could not post chatter on %s(%s): %s",
                self.res_model, self.res_id, exc,
            )

    # ------------------------------------------------------------------
    # Manual actions (button handlers)
    # ------------------------------------------------------------------

    def action_manual_retry(self):
        """Immediately retry this entry, resetting the retry count."""
        self.ensure_one()
        if self.state not in ('pending', 'failed'):
            raise UserError(_("Only pending or failed entries can be retried."))
        # Reset retry count for a fresh backoff sequence (BR-06)
        self.write({'retry_count': 0, 'state': 'pending', 'next_retry_at': fields.Datetime.now()})
        ICP = self.env['ir.config_parameter'].sudo()
        max_retries = int(ICP.get_param('uva.retry.max_attempts', _DEFAULT_MAX_RETRIES))
        self._execute_retry(max_retries)

    def action_discard(self):
        """Discard this entry — no further retries."""
        self.ensure_one()
        if self.state not in ('pending', 'failed'):
            raise UserError(_("Only pending or failed entries can be discarded."))
        self.write({'state': 'discarded', 'processed_at': fields.Datetime.now()})

    # ------------------------------------------------------------------
    # Cron: PII purge (I1 — clear payload on completed entries)
    # ------------------------------------------------------------------

    @api.model
    def purge_done_payloads(self, days=30):
        """Clear payload on done/failed/discarded entries older than `days` days."""
        cutoff = fields.Datetime.now() - timedelta(days=days)
        records = self.sudo().search([
            ('state', 'in', ('done', 'failed', 'discarded')),
            ('processed_at', '<', cutoff),
            ('payload', '!=', False),
        ])
        records.write({'payload': False})
