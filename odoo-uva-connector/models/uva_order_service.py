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

        # Step 2: Apply routing rules
        rule_result = self.env['uva.order.rule'].apply_rules(raw_order, store_config)
        if rule_result and rule_result['action_type'] == 'auto_reject':
            order_log = self.env['uva.order.log'].create({
                'external_id': external_id,
                'store_id': store_config.id,
                'raw_payload': json.dumps(raw_order),
                'state': 'rejected',
                'uva_source': 'uva',
                'received_at': fields.Datetime.now(),
            })
            order_log.message_post(
                body=_("Auto-rejected by routing rule."),
                message_type='notification', subtype_xmlid='mail.mt_note',
            )
            return order_log

        effective_store = store_config
        if rule_result and rule_result['action_type'] == 'route_pos' and rule_result.get('target_pos_config_id'):
            alt_store = self.env['uva.store.config'].search([
                ('pos_config_id', '=', rule_result['target_pos_config_id']),
                ('active', '=', True),
            ], limit=1)
            if alt_store:
                effective_store = alt_store

        # Step 3: Validate product mappings
        order_lines = raw_order.get('items', [])
        mapped_lines, unmapped_ids = self._validate_product_mappings(order_lines, effective_store)

        # Parse tip (#3)
        try:
            tip = max(0.0, float(raw_order.get('tip', 0) or raw_order.get('tip_amount', 0) or 0))
        except (ValueError, TypeError):
            tip = 0.0

        # Step 4: Create log record
        try:
            with self.env.cr.savepoint():
                order_log = self.env['uva.order.log'].create({
                    'external_id': external_id,
                    'store_id': effective_store.id,
                    'raw_payload': json.dumps(raw_order),
                    'state': 'draft',
                    'uva_source': 'uva',
                    'received_at': fields.Datetime.now(),
                    'tip_amount': tip,
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

        # Step 5: Handle unmapped products
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

        # Step 6: All products mapped — notify POS
        self._notify_pos(order_log)
        # Auto-accept if routing rule says so
        if rule_result and rule_result['action_type'] == 'auto_accept':
            self.process_staff_action(order_log.id, 'accept')
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
            self.env['uva.api.retry.queue'].sudo().enqueue(
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

        # After successful accept, create POS order
        if action in ('accept', 'modify') and order_log.state == 'accepted':
            self._create_pos_order(order_log)

        # Send customer notification only after successful processing
        order_log.invalidate_recordset(['state'])
        if action == 'reject' or order_log.state == 'done':
            ntype = 'order_accepted' if action in ('accept', 'modify') else 'order_rejected'
            self.env['uva.notification'].sudo()._send_order_notification(order_log, ntype)

    # ------------------------------------------------------------------
    # Order modification (Feature #7)
    # ------------------------------------------------------------------

    @api.model
    def process_modification(self, order_id, modifications):
        """Process an order modification from POS."""
        order_log = self.env['uva.order.log'].browse(order_id)
        if not order_log.exists() or order_log.state != 'accepted':
            return False
        order_log.action_modify(modifications)
        try:
            self._notify_uva_status(order_log, 'modify',
                unavailable_items=modifications.get('removed_items', []))
        except UvaApiError as exc:
            self.env['uva.api.retry.queue'].sudo().enqueue(
                action_type='notify_modification',
                payload=json.dumps({'external_id': order_log.external_id,
                                   'items': modifications.get('removed_items', [])}),
                res_model='uva.order.log', res_id=order_log.id,
                store_id=order_log.store_id.id, error=str(exc))
        return True

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
        if not store.is_store_open():
            _logger.debug("[uva:%s] _poll_store: store closed — skipping", store.name)
            return
        client = self.env['uva.api.client']
        try:
            # See doc/api_compatibility.md for endpoint details
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
    # Auto-accept cron
    # ------------------------------------------------------------------

    @api.model
    def auto_accept_expired_orders(self):
        """Cron: auto-accept orders past their auto_accept_timeout.

        Skips orders received within the last 10 seconds to avoid racing
        with ingest_order's mapping validation.
        """
        from datetime import timedelta
        now = fields.Datetime.now()
        grace = now - timedelta(seconds=10)
        drafts = self.env['uva.order.log'].search([
            ('state', '=', 'draft'),
            ('received_at', '<', grace),
        ])
        for order in drafts:
            timeout = order.store_id.auto_accept_timeout
            if timeout <= 0:
                continue
            deadline = order.received_at + timedelta(seconds=timeout)
            if now >= deadline:
                _logger.info(
                    "[uva:%s] auto-accepting expired order (timeout=%ss)",
                    order.external_id, timeout,
                )
                order.action_accept()
                try:
                    self._notify_uva_status(order, 'accept')
                except UvaApiError as exc:
                    # Enqueue retry — don't silently swallow
                    self.env['uva.api.retry.queue'].sudo().enqueue(
                        action_type='notify_acceptance',
                        payload=json.dumps({
                            'external_id': order.external_id,
                            'items': [],
                        }),
                        res_model='uva.order.log',
                        res_id=order.id,
                        store_id=order.store_id.id,
                        error=str(exc),
                    )
                self._create_pos_order(order)

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
            uva_product_name = line.get('name') or line.get('product_name') or ''
            odoo_product = mapping_model.get_odoo_product(uva_product_id, store_config.id)
            if not odoo_product and uva_product_name:
                odoo_product = self._try_auto_map(uva_product_id, uva_product_name, store_config)
            if odoo_product:
                mapped_lines.append({**line, 'odoo_product_id': odoo_product.id})
            else:
                unmapped_ids.append(uva_product_id)

        return mapped_lines, unmapped_ids

    @api.model
    def _try_auto_map(self, uva_product_id, uva_product_name, store_config):
        """Auto-create mapping when exactly one product matches by name.

        Uses savepoint + IntegrityError catch to handle concurrent race conditions
        on the UNIQUE(uva_product_id, store_id) constraint.
        """
        Product = self.env['product.product']
        matches = Product.search([('name', '=ilike', uva_product_name)], limit=2)
        if len(matches) != 1:
            return None
        try:
            with self.env.cr.savepoint():
                self.env['uva.product.mapping'].create({
                    'uva_product_id': uva_product_id,
                    'odoo_product_id': matches.id,
                    'store_id': store_config.id,
                })
        except Exception:
            # Race condition: another worker already created this mapping
            existing = self.env['uva.product.mapping'].get_odoo_product(
                uva_product_id, store_config.id,
            )
            return existing
        store_config.message_post(
            body=_(
                "🔗 Auto-mapped Uva product '%(uva_name)s' (%(uva_id)s) → %(odoo_name)s",
                uva_name=uva_product_name,
                uva_id=uva_product_id,
                odoo_name=matches.display_name,
            ),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )
        return matches

    # ------------------------------------------------------------------
    # POS error recovery (Improvement 7)
    # ------------------------------------------------------------------

    @api.model
    def action_retry_from_pos(self, order_id):
        """Retry an error order from POS — re-creates the POS order."""
        order_log = self.env['uva.order.log'].browse(order_id)
        if order_log.exists() and order_log.state == 'error':
            order_log.action_retry()
            self._create_pos_order(order_log)
            self._notify_pos(order_log)
            return True
        return False

    @api.model
    def action_reject_from_pos(self, order_id, reason=''):
        """Reject an error order from POS."""
        order_log = self.env['uva.order.log'].browse(order_id)
        if order_log.exists() and order_log.state == 'error':
            order_log.action_reject(reason=reason)
            self._notify_pos(order_log)
            return True
        return False

    # ------------------------------------------------------------------
    # POS order creation pipeline
    # ------------------------------------------------------------------

    @api.model
    def _create_pos_order(self, order_log):
        """Create a pos.order from an accepted uva.order.log record."""
        try:
            payload = json.loads(order_log.raw_payload or '{}')
            items = payload.get('items', [])
            store = order_log.store_id
            mapping_model = self.env['uva.product.mapping']

            # Find open POS session inside savepoint to handle race
            session = self.env['pos.session'].search([
                ('config_id', '=', store.pos_config_id.id),
                ('state', '=', 'opened'),
            ], limit=1)
            if not session:
                order_log.action_mark_error('No open POS session found')
                return

            mapped_items = []
            total = 0.0
            for item in items:
                uva_pid = item.get('product_id', '')
                product = mapping_model.get_odoo_product(uva_pid, store.id)
                if not product:
                    order_log.action_mark_error(f'Unmapped product: {uva_pid}')
                    return
                try:
                    qty = float(item.get('quantity') or item.get('qty') or 1)
                    price = float(item.get('price') or item.get('price_unit') or 0)
                except (ValueError, TypeError):
                    order_log.action_mark_error(
                        f'Invalid quantity/price for product {uva_pid}'
                    )
                    return
                if price <= 0:
                    order_log.action_mark_error(
                        f'Zero or negative price for product {uva_pid}'
                    )
                    return
                mapped_items.append((product, qty, price))
                total += qty * price

            if not mapped_items:
                order_log.action_mark_error('No valid order lines')
                return

            # Compute tax-inclusive totals
            lines_vals = []
            total_incl = 0.0
            total_tax = 0.0
            for p, q, pr in mapped_items:
                subtotal = q * pr
                taxes = p.taxes_id.compute_all(pr, quantity=q)
                incl = taxes['total_included']
                tax_amount = incl - subtotal
                total_incl += incl
                total_tax += tax_amount
                lines_vals.append((0, 0, {
                    'product_id': p.id,
                    'full_product_name': p.display_name,
                    'qty': q,
                    'price_unit': pr,
                    'price_subtotal': subtotal,
                    'price_subtotal_incl': incl,
                    'tax_ids': [(6, 0, p.taxes_id.ids)],
                }))

            # Tip line item (#3)
            if order_log.tip_amount > 0:
                tip_product = self.env['product.product'].search([('name', '=', 'Tip')], limit=1)
                if not tip_product:
                    tip_product = self.env['product.product'].create({'name': 'Tip', 'type': 'service', 'list_price': 0})
                lines_vals.append((0, 0, {
                    'product_id': tip_product.id,
                    'full_product_name': 'Tip',
                    'qty': 1,
                    'price_unit': order_log.tip_amount,
                    'price_subtotal': order_log.tip_amount,
                    'price_subtotal_incl': order_log.tip_amount,
                    'tax_ids': [(5, 0, 0)],
                }))
                total_incl += order_log.tip_amount

            # Create POS order inside savepoint to handle session-close race
            try:
                with self.env.cr.savepoint():
                    # Re-verify session is still open
                    session.invalidate_recordset(['state'])
                    if session.state != 'opened':
                        order_log.action_mark_error('POS session closed during processing')
                        return

                    pos_order = self.env['pos.order'].create({
                        'session_id': session.id,
                        'company_id': session.company_id.id,
                        'pricelist_id': session.config_id.pricelist_id.id,
                        'partner_id': False,
                        'lines': lines_vals,
                        'amount_total': total_incl,
                        'amount_tax': total_tax,
                        'amount_paid': total_incl,
                        'amount_return': 0,
                    })
                    # Register payment so the order is valid
                    payment_method = session.payment_method_ids[:1]
                    if payment_method:
                        self.env['pos.payment'].create({
                            'pos_order_id': pos_order.id,
                            'amount': total_incl,
                            'payment_method_id': payment_method.id,
                            'session_id': session.id,
                        })
                    # Revenue attribution tag
                    pos_order.write({'note': f'UVA:{order_log.external_id}', 'is_uva_order': True})
            except Exception as exc:
                order_log.action_mark_error(f'POS order creation failed: {exc}')
                return

            order_log.action_mark_done(pos_order)
        except Exception as exc:
            _logger.error(
                "[uva:%s] _create_pos_order failed: %s",
                order_log.external_id, exc, exc_info=True,
            )
            if order_log.state == 'accepted':
                order_log.action_mark_error(str(exc))

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
        items = []
        try:
            payload = json.loads(order_log.raw_payload or '{}')
            items = payload.get('items', [])
        except Exception:
            pass
        # Enrich items with product image URLs (#1)
        mapping_model = self.env['uva.product.mapping']
        for item in items:
            uva_pid = item.get('product_id', '')
            product = mapping_model.get_odoo_product(uva_pid, store.id)
            if product and product.image_128:
                item['image_url'] = f'/web/image/product.product/{product.id}/image_128'
        self.env['bus.bus']._sendone(
            channel,
            'uva_new_order',
            {
                'order_id': order_log.id,
                'external_id': order_log.external_id,
                'state': order_log.state,
                'store_name': store.name,
                'auto_accept_timeout': store.auto_accept_timeout,
                'items': items,
            },
        )
        _logger.debug(
            "_notify_pos: sent uva_new_order to channel %s for order %s",
            channel, order_log.external_id,
        )

    # ------------------------------------------------------------------
    # Uva status callback (S-16)
    # ------------------------------------------------------------------

    @api.model
    def _notify_uva_status(self, order_log, action, unavailable_items=None):
        """Notify Uva of a staff action on an order.

        See doc/api_compatibility.md for endpoint details (POST /orders/{id}/status).

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

        # See doc/api_compatibility.md for endpoint details
        client = self.env['uva.api.client']
        return client.confirm_order(
            api_key=store.sudo().api_key,
            external_id=order_log.external_id,
            action=action,
            items=unavailable_items,
        )

    # ------------------------------------------------------------------
    # POS health notification
    # ------------------------------------------------------------------

    @api.model
    def _notify_pos_health(self, store_config, status):
        """Push connection health status to the POS session via bus.bus."""
        channel = f'pos.config-{store_config.pos_config_id.id}'
        self.env['bus.bus']._sendone(channel, 'uva_health_status', {'status': status})