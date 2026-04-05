# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
"""Tests for Unit 6: Flow B — Status Tracking."""
import hashlib
import hmac
import json

from odoo import fields
from odoo.tests.common import TransactionCase, HttpCase


class TestUvaFleetServiceBase(TransactionCase):

    def setUp(self):
        super().setUp()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('uva.fleet.api_key', 'test-fleet-key')
        ICP.set_param('uva.fleet.demo_mode', 'True')
        ICP.set_param('uva.fleet.webhook_secret', 'fleet-webhook-secret')

        self.carrier = self.env['delivery.carrier'].create({
            'name': 'Uva Fleet',
            'delivery_type': 'uva',
            'product_id': self.env.ref('delivery.product_product_delivery').id,
        })
        self.partner = self.env['res.partner'].create({
            'name': 'Test Customer',
            'street': '123 Calle Sol',
            'city': 'San Juan',
            'zip': '00901',
        })
        self.picking = self.env['stock.picking'].create({
            'partner_id': self.partner.id,
            'picking_type_id': self.env.ref('stock.picking_type_out').id,
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
        })
        self.sale_order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
        })
        self.fleet_delivery = self.env['uva.fleet.delivery'].create({
            'uva_delivery_id': 'UVA-STATUS-001',
            'carrier_id': self.carrier.id,
            'picking_id': self.picking.id,
            'sale_order_id': self.sale_order.id,
            'company_id': self.env.company.id,
            'state': 'pending',
        })
        self.service = self.env['uva.fleet.service']


class TestUvaFleetServiceStatusUpdate(TestUvaFleetServiceBase):

    def test_process_status_update_updates_fleet_delivery_state(self):
        self.service.process_status_update(
            delivery_id='UVA-STATUS-001',
            status='in_transit',
            updated_at=fields.Datetime.now(),
        )
        self.fleet_delivery.invalidate_recordset()
        self.assertEqual(self.fleet_delivery.state, 'in_transit')

    def test_process_status_update_posts_to_picking_chatter(self):
        msg_count_before = len(self.picking.message_ids)
        self.service.process_status_update(
            delivery_id='UVA-STATUS-001',
            status='assigned',
            updated_at=fields.Datetime.now(),
        )
        self.picking.invalidate_recordset()
        self.assertGreater(
            len(self.picking.message_ids), msg_count_before,
            "Status update should post a chatter message to the picking"
        )

    def test_process_status_update_posts_to_sale_order_chatter(self):
        msg_count_before = len(self.sale_order.message_ids)
        self.service.process_status_update(
            delivery_id='UVA-STATUS-001',
            status='delivered',
            updated_at=fields.Datetime.now(),
        )
        self.sale_order.invalidate_recordset()
        self.assertGreater(
            len(self.sale_order.message_ids), msg_count_before,
            "Status update should post a chatter message to the sale order"
        )

    def test_process_status_update_unknown_delivery_id_logs_warning(self):
        """Unknown delivery_id should log a warning and not raise."""
        import logging
        with self.assertLogs(
            'odoo.addons.odoo_uva_connector.models.uva_fleet_service',
            level=logging.WARNING,
        ) as cm:
            self.service.process_status_update(
                delivery_id='DOES-NOT-EXIST',
                status='delivered',
                updated_at=fields.Datetime.now(),
            )
        self.assertTrue(any('DOES-NOT-EXIST' in line for line in cm.output))

    def test_process_status_update_updates_last_status_at(self):
        """last_status_at is updated on each status update (used by polling throttle)."""
        before = fields.Datetime.now()
        self.service.process_status_update(
            delivery_id='UVA-STATUS-001',
            status='assigned',
            updated_at=fields.Datetime.now(),
        )
        self.fleet_delivery.invalidate_recordset()
        self.assertGreaterEqual(self.fleet_delivery.last_status_at, before)

    def test_process_status_update_unknown_status_logs_warning(self):
        """Unknown Uva status string logs a warning and does not update state."""
        original_state = self.fleet_delivery.state
        import logging
        with self.assertLogs(
            'odoo.addons.odoo_uva_connector.models.uva_fleet_service',
            level=logging.WARNING,
        ) as cm:
            self.service.process_status_update(
                delivery_id='UVA-STATUS-001',
                status='UNKNOWN_STATUS_XYZ',
                updated_at=fields.Datetime.now(),
            )
        self.fleet_delivery.invalidate_recordset()
        self.assertEqual(self.fleet_delivery.state, original_state,
                         "Unknown status should not change the delivery state")


class TestUvaFleetServicePolling(TestUvaFleetServiceBase):

    def test_poll_active_deliveries_skips_terminal_states(self):
        """Delivered/cancelled/failed deliveries are not polled — state unchanged."""
        self.fleet_delivery.write({'state': 'delivered'})
        # Record state before poll
        state_before = self.fleet_delivery.state
        # poll_active_deliveries should not process terminal deliveries
        self.service.poll_active_deliveries()
        self.fleet_delivery.invalidate_recordset()
        # State should remain 'delivered' — no update attempted
        self.assertEqual(self.fleet_delivery.state, state_before)

    def test_poll_active_deliveries_skips_recently_polled(self):
        """Deliveries polled within _MIN_POLL_INTERVAL seconds are skipped."""
        self.fleet_delivery.write({
            'state': 'pending',
            'last_status_at': fields.Datetime.now(),  # just polled
        })
        # In demo mode, get_delivery_status returns {'status': 'pending'}.
        # If the throttle works, last_status_at should NOT be updated again.
        last_polled = self.fleet_delivery.last_status_at
        self.service.poll_active_deliveries()
        self.fleet_delivery.invalidate_recordset()
        # last_status_at should be unchanged (throttle prevented the poll)
        self.assertEqual(
            self.fleet_delivery.last_status_at, last_polled,
            "Throttle should prevent polling a recently-polled delivery"
        )


class TestUvaFleetWebhook(HttpCase):

    def setUp(self):
        super().setUp()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('uva.fleet.webhook_secret', 'fleet-webhook-secret')
        self.company_id = self.env.company.id
        self.url = f'/uva/webhook/fleet/{self.company_id}'

    def _make_sig(self, body_bytes, secret='fleet-webhook-secret'):
        return hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()

    def _post(self, body_dict, secret='fleet-webhook-secret', company_id=None):
        url = f'/uva/webhook/fleet/{company_id or self.company_id}'
        body = json.dumps(body_dict).encode()
        sig = self._make_sig(body, secret)
        return self.url_open(
            url, data=body,
            headers={'Content-Type': 'application/json', 'X-Uva-Signature': sig},
        )

    def test_webhook_valid_request_returns_200(self):
        resp = self._post({'delivery_id': 'UVA-WH-001', 'status': 'in_transit'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get('status'), 'ok')

    def test_webhook_invalid_hmac_returns_400(self):
        resp = self._post({'delivery_id': 'UVA-WH-002'}, secret='wrong-secret')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('error', resp.json())

    def test_webhook_unknown_company_returns_400(self):
        body = json.dumps({'delivery_id': 'UVA-WH-003'}).encode()
        sig = self._make_sig(body)
        resp = self.url_open(
            '/uva/webhook/fleet/999999', data=body,
            headers={'Content-Type': 'application/json', 'X-Uva-Signature': sig},
        )
        self.assertEqual(resp.status_code, 400)
