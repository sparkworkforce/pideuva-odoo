# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import json
import logging

from odoo import _, api, fields, models

from .uva_api_client import RETRYABLE_ACTIONS, UvaApiError

_logger = logging.getLogger(__name__)


class UvaOrderService(models.AbstractModel):
    _name = 'uva.order.service'
    _description = 'Uva PR Order Ingestion Service'

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    @api.model
    def ingest_order(self, raw_order, store_config):
        """Ingest a single incoming Uva order."""
        external_id = raw_order.get('id') or raw_order.get('external_id', '')
        if not external_id:
            _logger.warning("[uva:?] ingest_order: missing external_id in payload — skipping")
            return self.env['uva.order.log'].browse()

        # Step 1: Deduplicate
        existing = self._deduplicate(external_id)
        if existing:
            _logger.info(
                "[uva:%s] ingest_order: duplicate — returning existing log %s",
                external_id, existing.id,
            )
            return existing

        # Step 2: Validate product mappings
        order_lines = raw_order.get('items', [])
        mapped_lines, unmapped_ids = self._validate_product_mappings(order_lines, store_config)

        # Step 3: Create log record
        try:
            with self.env.cr.savepoint():
                order_log = self.env['uva.order.log'].create({
                    'external_id': external_id,
                    'store_id': store_config.id,
                    'raw_payload': json.dumps(raw_order),
                    'state': 'draft',
                    'received_at': fields.Datetime.now(),
                })
        except Exception:
            # Concurrent webhook + polling race: another worker already created this record.
            # The savepoint rolls back only the failed create; re-check deduplication.
            existing = self._deduplicate(external_id)
            if existing:
                _logger.info(
                    "[uva:%s] ingest_order: concurrent duplicate resolved — returning existing log %s",
                    external_id, existing.id,
                )
                return existing
            raise

        # Step 4: Handle unmapped products
        if unmapped_ids:
            order_log.write({'state': 'pending'})
            order_log.message_post(
                body=_(
                    "⚠️ Order placed in 'Awaiting Mapping' state. "
                    "The following Uva product IDs have no mapping: %(ids)s. "
                    "Please add mappings under Point of Sale > Configuration > Uva Product Mappings.",
                    ids=', '.join(unmapped_ids),
                ),
                message_type='notification',
                subtype_xmlid='mail.mt_note',
            )
            # Still notify POS so staff can see the pending order
            self._notify_pos(order_log)
            return order_log

        # Step 5: All products mapped — notify POS
        self._notify_pos(order_log)
        return order_log

    # ------------------------------------------------------------------
    # Staff action processing
    # ------------------------------------------------------------------

    @api.model
    def process_staff_action(self, order_id, action, unavailable_items=None):
        """Process a POS staff action on an order.

        Calls the appropriate action method on uva.order.log, then notifies
        Uva via _notify_uva_status. On transient API failure, enqueues retry.

        Args:
            order_id (int): ID of the uva.order.log record
            action (str): 'accept', 'reject', or 'modify'
            unavailable_items (list[str]|None): Uva item IDs marked unavailable
        """
        order_log = self.env['uva.order.log'].browse(order_id)
        if not order_log.exists():
            _logger.warning("process_staff_action: order_id=%s not found", order_id)
            return

        store = order_log.store_id
        if action in ('accept', 'modify'):
            order_log.action_accept(unavailable_items=unavailable_items)
        elif action == 'reject':
            order_log.action_reject()
        else:
            _logger.warning("process_staff_action: unknown action '%s'", action)
            return

        # Notify Uva — on transient failure, enqueue retry
        try:
            self._notify_uva_status(order_log, action, unavailable_items=unavailable_items)
        except UvaApiError as exc:
            _logger.warning(
                "[uva:%s] process_staff_action: API error notifying status — enqueuing retry: %s",
                order_log.external_id, exc,
            )
            action_type_map = {
                'accept': 'notify_acceptance',
                'reject': 'notify_rejection',
                'modify': 'notify_modification',
            }
            retry_action = action_type_map.get(action, 'notify_acceptance')
            self.env['uva.api.retry.queue'].enqueue(
                action_type=retry_action,
                payload=json.dumps({
                    'external_id': order_log.external_id,
                    'items': unavailable_items or [],
                }),
                res_model='uva.order.log',
                res_id=order_log.id,
                store_id=store.id,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Polling cron entry point (D-09: fast cron + per-record throttle)
    # ------------------------------------------------------------------

    @api.model
    def poll_all_stores(self):
        """Called by the order polling cron every minute.

        Iterates all active stores with polling enabled and calls
        poll_orders_if_due() on each. Only stores whose polling_interval
        has elapsed since last_polled_at are actually polled.
        """
        stores = self.env['uva.store.config'].search([
            ('active', '=', True),
            ('polling_enabled', '=', True),
        ])
        for store in stores:
            if store.poll_orders_if_due():
                try:
                    with self.env.cr.savepoint():
                        self._poll_store(store)
                except Exception as exc:
                    _logger.error(
                        "[uva:%s] poll_all_stores: unexpected error (savepoint rolled back): %s",
                        store.name, exc, exc_info=True,
                    )

    def _poll_store(self, store):
        """Poll the Uva Orders API for a single store and ingest any new orders."""
        client = self.env['uva.api.client']
        try:
            # TODO(uva-api): pass correct since timestamp once endpoint is confirmed
            raw_orders = client.get_orders(
                api_key=store.sudo().api_key,
                store_id=str(store.id),
                since=store.last_polled_at,
                demo_mode=store.demo_mode,
            )
            for raw_order in raw_orders:
                try:
                    self.ingest_order(raw_order, store)
                except Exception as exc:
                    _logger.error(
                        "_poll_store: error ingesting order from store %s: %s",
                        store.name, exc, exc_info=True,
                    )
        except UvaApiError as exc:
            _logger.warning(
                "[uva:%s] _poll_store: API error: %s", store.name, exc
            )
        except Exception as exc:
            _logger.error(
                "[uva:%s] _poll_store: unexpected error: %s",
                store.name, exc, exc_info=True,
            )

    # ------------------------------------------------------------------
    # Pure helper methods (PBT targets)
    # ------------------------------------------------------------------

    @api.model
    def _deduplicate(self, external_id):
        """Return existing uva.order.log for the given external_id, or None.

        Pure lookup — no side effects. Used by ingest_order and action_retry.

        PBT invariant (idempotence):
            _deduplicate(_deduplicate(x).external_id) == _deduplicate(x)
            i.e. calling twice with the same ID returns the same record.
        """
        record = self.env['uva.order.log'].search(
            [('external_id', '=', external_id)], limit=1
        )
        return record if record else None

    @api.model
    def _validate_product_mappings(self, order_lines, store_config):
        """Validate that all Uva product IDs in order_lines have Odoo mappings.

        Pure transformation — no side effects.

        PBT round-trip invariant:
            len(mapped_lines) + len(unmapped_ids) == len(order_lines)
            i.e. every input line appears in exactly one output bucket.

        Args:
            order_lines (list[dict]): Items from the Uva order payload.
                Each item must have a 'product_id' key (Uva product ID).
            store_config (uva.store.config): Store to look up mappings for.

        Returns:
            tuple(list[dict], list[str]):
                - mapped_lines: order lines with 'odoo_product_id' added
                - unmapped_ids: Uva product IDs with no mapping
        """
        mapping_model = self.env['uva.product.mapping']
        mapped_lines = []
        unmapped_ids = []

        for line in order_lines:
            uva_product_id = line.get('product_id', '')
            odoo_product = mapping_model.get_odoo_product(uva_product_id, store_config.id)
            if odoo_product:
                mapped_lines.append({**line, 'odoo_product_id': odoo_product.id})
            else:
                unmapped_ids.append(uva_product_id)

        return mapped_lines, unmapped_ids

    # ------------------------------------------------------------------
    # POS notification
    # ------------------------------------------------------------------

    @api.model
    def _notify_pos(self, order_log):
        """Push a new order notification to the POS session via bus.bus.

        Channel: pos.config-{pos_config_id}
        Message type: uva_new_order
        """
        store = order_log.store_id
        pos_config_id = store.pos_config_id.id
        channel = f'pos.config-{pos_config_id}'
        self.env['bus.bus']._sendone(
            channel,
            'uva_new_order',
            {
                'order_id': order_log.id,
                'external_id': order_log.external_id,
                'state': order_log.state,
                'store_name': store.name,
                'auto_accept_timeout': store.auto_accept_timeout,
            },
        )
        _logger.debug(
            "_notify_pos: sent uva_new_order to channel %s for order %s",
            channel, order_log.external_id,
        )

    # ------------------------------------------------------------------
    # Uva status callback (S-16 — TODO(uva-api) stub)
    # ------------------------------------------------------------------

    @api.model
    def _notify_uva_status(self, order_log, action, unavailable_items=None):
        """Notify Uva of a staff action on an order.

        # TODO(uva-api): implement once Uva order status callback endpoint is confirmed.
        # Expected: POST /orders/{external_id}/status
        # Payload: {"action": action, "unavailable_items": unavailable_items or []}

        In demo mode: logs the action and returns True without making any HTTP call.
        In production: raises NotImplementedError until endpoint is confirmed.

        This method IS called from action_accept and action_reject — the call site
        is fully implemented even though the endpoint is pending (S-16 implementation note).
        """
        store = order_log.store_id
        if store.demo_mode:
            _logger.info(
                "_notify_uva_status [DEMO]: order=%s action=%s items=%s",
                order_log.external_id, action, unavailable_items,
            )
            return True

        # TODO(uva-api): implement real callback
        raise NotImplementedError(
            "TODO(uva-api): _notify_uva_status endpoint not yet confirmed. "
            "Implement once Uva Orders API docs are received."
        )
