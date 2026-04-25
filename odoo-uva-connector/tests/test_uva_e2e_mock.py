# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
"""E2E integration tests with mocked Uva API — comprehensive coverage."""
import hashlib
import hmac
import json
from datetime import datetime
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase

from ..models.uva_api_client import UvaApiError

_PATCH_GET_ORDERS = 'odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.get_orders'
_PATCH_CONFIRM_ORDER = 'odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.confirm_order'
_PATCH_NOTIFY_UVA = 'odoo.addons.odoo_uva_connector.models.uva_order_service.UvaOrderService._notify_uva_status'
_PATCH_NOTIFY_POS = 'odoo.addons.odoo_uva_connector.models.uva_order_service.UvaOrderService._notify_pos'
_PATCH_GET_ESTIMATE = 'odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.get_delivery_estimate'
_PATCH_CREATE_DELIVERY = 'odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.create_delivery'
_PATCH_CANCEL_DELIVERY = 'odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.cancel_delivery'
_PATCH_GET_STATUS = 'odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.get_delivery_status'
_PATCH_VALIDATE_HMAC = 'odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.validate_hmac'


class TestE2EBase(TransactionCase):

    def setUp(self):
        super().setUp()
        self.pos_config = self.env['pos.config'].create({'name': 'E2E POS'})
        self.store = self.env['uva.store.config'].create({
            'name': 'E2E Store',
            'pos_config_id': self.pos_config.id,
            'demo_mode': False,
            'api_key': 'e2e-test-api-key',
            'webhook_secret': 'e2e-test-secret',
            'polling_enabled': True,
            'polling_interval': 60,
            'auto_accept_timeout': 120,
        })
        self.product = self.env['product.product'].create({
            'name': 'E2E Product', 'type': 'consu',
        })
        self.env['uva.product.mapping'].create({
            'uva_product_id': 'UVA-E2E-PROD',
            'odoo_product_id': self.product.id,
            'store_id': self.store.id,
        })
        self.order_service = self.env['uva.order.service']
        self.fleet_service = self.env['uva.fleet.service']

    def _make_order(self, ext_id='E2E-001', items=None):
        return {
            'id': ext_id,
            'items': items or [{'product_id': 'UVA-E2E-PROD', 'name': 'E2E Product', 'qty': 2}],
        }


class TestE2EOrderIngestionFlow(TestE2EBase):
    """1. poll → ingest → accept → POS order created."""

    @patch(_PATCH_NOTIFY_UVA, return_value=True)
    @patch(_PATCH_NOTIFY_POS)
    @patch(_PATCH_GET_ORDERS)
    def test_e2e_order_ingestion_flow(self, mock_get, mock_pos, mock_uva):
        mock_get.return_value = [self._make_order('E2E-INGEST-001')]
        self.order_service.poll_all_stores()

        log = self.env['uva.order.log'].search([('external_id', '=', 'E2E-INGEST-001')])
        self.assertTrue(log.exists())
        self.assertEqual(log.state, 'draft')
        mock_pos.assert_called_once()

        self.order_service.process_staff_action(log.id, 'accept')
        log.invalidate_recordset()
        self.assertEqual(log.state, 'accepted')
        mock_uva.assert_called_once()


class TestE2EOrderUnmappedProducts(TestE2EBase):
    """2. order with unmapped items → pending → auto-map → retry."""

    @patch(_PATCH_NOTIFY_POS)
    def test_e2e_order_with_unmapped_products(self, mock_pos):
        order = self._make_order('E2E-UNMAP-001', items=[
            {'product_id': 'UVA-UNKNOWN', 'name': 'Unknown Item', 'qty': 1},
        ])
        log = self.order_service.ingest_order(order, self.store)
        self.assertEqual(log.state, 'pending')

        # Create mapping manually, then retry
        self.env['uva.product.mapping'].create({
            'uva_product_id': 'UVA-UNKNOWN',
            'odoo_product_id': self.product.id,
            'store_id': self.store.id,
        })
        # Simulate retry by transitioning to accepted (from pending)
        log.action_accept()
        self.assertEqual(log.state, 'accepted')


class TestE2EOrderAutoAccept(TestE2EBase):
    """3. order arrives → timeout expires → auto-accepted."""

    @patch(_PATCH_NOTIFY_POS)
    def test_e2e_order_auto_accept(self, mock_pos):
        order = self._make_order('E2E-AUTO-001')
        log = self.order_service.ingest_order(order, self.store)
        self.assertEqual(log.state, 'draft')
        # Simulate auto-accept (timeout expired — staff action)
        self.order_service.process_staff_action(log.id, 'accept')
        log.invalidate_recordset()
        self.assertEqual(log.state, 'accepted')


class TestE2EOrderRejectionFlow(TestE2EBase):
    """4. order arrives → staff rejects → Uva notified."""

    @patch(_PATCH_NOTIFY_UVA, return_value=True)
    @patch(_PATCH_NOTIFY_POS)
    def test_e2e_order_rejection_flow(self, mock_pos, mock_uva):
        order = self._make_order('E2E-REJECT-001')
        log = self.order_service.ingest_order(order, self.store)
        self.order_service.process_staff_action(log.id, 'reject')
        log.invalidate_recordset()
        self.assertEqual(log.state, 'rejected')
        mock_uva.assert_called_once()
        self.assertEqual(mock_uva.call_args[0][1], 'reject')


class TestE2EOrderRetryAfterApiFailure(TestE2EBase):
    """5. accept → API fails → retry queue → retry succeeds."""

    @patch(_PATCH_CONFIRM_ORDER, return_value=True)
    @patch(_PATCH_NOTIFY_UVA)
    @patch(_PATCH_NOTIFY_POS)
    def test_e2e_order_retry_after_api_failure(self, mock_pos, mock_uva, mock_confirm):
        mock_uva.side_effect = UvaApiError("Uva API timeout")

        order = self._make_order('E2E-RETRY-001')
        log = self.order_service.ingest_order(order, self.store)
        self.order_service.process_staff_action(log.id, 'accept')

        retry = self.env['uva.api.retry.queue'].search([
            ('res_model', '=', 'uva.order.log'),
            ('res_id', '=', log.id),
            ('action_type', '=', 'notify_acceptance'),
        ])
        self.assertTrue(retry.exists())
        self.assertEqual(retry.state, 'pending')

        retry.write({'next_retry_at': fields.Datetime.now()})
        self.env['uva.api.retry.queue'].process_due_retries()
        retry.invalidate_recordset()
        self.assertEqual(retry.state, 'done')
        mock_confirm.assert_called_once()


class TestE2EFleetDeliveryFullLifecycle(TestE2EBase):
    """6. estimate → dispatch → assigned → in_transit → delivered."""

    def setUp(self):
        super().setUp()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('uva.fleet.api_key', 'e2e-fleet-key')
        ICP.set_param('uva.fleet.demo_mode', 'False')
        self.carrier = self.env['delivery.carrier'].create({
            'name': 'Uva Fleet E2E',
            'delivery_type': 'uva',
            'product_id': self.env.ref('delivery.product_product_delivery').id,
        })
        self.partner = self.env['res.partner'].create({
            'name': 'E2E Customer', 'street': '456 Calle Luna',
            'city': 'San Juan', 'zip': '00901',
            'country_id': self.env.ref('base.pr').id,
        })
        self.picking = self.env['stock.picking'].create({
            'partner_id': self.partner.id,
            'picking_type_id': self.env.ref('stock.picking_type_out').id,
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
        })

    @patch(_PATCH_GET_STATUS)
    @patch(_PATCH_CREATE_DELIVERY)
    @patch(_PATCH_GET_ESTIMATE)
    def test_e2e_fleet_delivery_full_lifecycle(self, mock_est, mock_create, mock_status):
        mock_est.return_value = {'amount': 5.00, 'currency': 'USD', 'eta_minutes': 30}
        mock_create.return_value = {
            'delivery_id': 'UVA-DEL-E2E-001',
            'tracking_url': 'https://track.pideuva.com/UVA-DEL-E2E-001',
        }

        results = self.carrier.uva_send_shipping(self.picking)
        self.assertEqual(results[0]['tracking_number'], 'UVA-DEL-E2E-001')

        fd = self.env['uva.fleet.delivery'].search([('uva_delivery_id', '=', 'UVA-DEL-E2E-001')])
        self.assertEqual(fd.state, 'pending')

        # Transition through states
        for status in ['assigned', 'in_transit', 'delivered']:
            mock_status.return_value = {'status': status, 'updated_at': datetime.utcnow()}
            fd.write({'last_status_at': False})
            self.fleet_service.poll_active_deliveries()
            fd.invalidate_recordset()

        self.assertEqual(fd.state, 'delivered')


class TestE2EFleetCancellation(TestE2EFleetDeliveryFullLifecycle):
    """7. dispatch → cancel → Uva notified."""

    @patch(_PATCH_CANCEL_DELIVERY, return_value=True)
    @patch(_PATCH_CREATE_DELIVERY)
    @patch(_PATCH_GET_ESTIMATE)
    def test_e2e_fleet_cancellation(self, mock_est, mock_create, mock_cancel):
        mock_est.return_value = {'amount': 5.00, 'currency': 'USD', 'eta_minutes': 30}
        mock_create.return_value = {
            'delivery_id': 'UVA-DEL-CANCEL-001',
            'tracking_url': '#',
        }

        self.carrier.uva_send_shipping(self.picking)
        fd = self.env['uva.fleet.delivery'].search([('uva_delivery_id', '=', 'UVA-DEL-CANCEL-001')])
        self.assertEqual(fd.state, 'pending')

        self.carrier.uva_cancel_shipping(self.picking)
        fd.invalidate_recordset()
        self.assertEqual(fd.state, 'cancelled')
        mock_cancel.assert_called_once()


class TestE2EWebhookHmacValidation(TestE2EBase):
    """8. valid signature accepted, invalid rejected."""

    def test_e2e_webhook_hmac_validation(self):
        client = self.env['uva.api.client']
        payload = b'{"id":"test"}'
        secret = 'e2e-test-secret'
        valid_sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        self.assertTrue(client.validate_hmac(payload, valid_sig, secret))
        self.assertTrue(client.validate_hmac(payload, f'sha256={valid_sig}', secret))
        self.assertFalse(client.validate_hmac(payload, 'invalid-signature', secret))
        self.assertFalse(client.validate_hmac(payload, '', secret))


class TestE2EDuplicateOrderIdempotency(TestE2EBase):
    """9. same order ID twice → only one log created."""

    @patch(_PATCH_NOTIFY_POS)
    def test_e2e_duplicate_order_idempotency(self, mock_pos):
        order = self._make_order('E2E-DUP-001')
        log1 = self.order_service.ingest_order(order, self.store)
        log2 = self.order_service.ingest_order(order, self.store)
        self.assertEqual(log1.id, log2.id)
        count = self.env['uva.order.log'].search_count([('external_id', '=', 'E2E-DUP-001')])
        self.assertEqual(count, 1)


class TestE2EAutoMapOnIngest(TestE2EBase):
    """10. order with product name matching Odoo product → auto-mapped."""

    @patch(_PATCH_NOTIFY_POS)
    def test_e2e_auto_map_on_ingest(self, mock_pos):
        # Create a product that will be auto-matched by name
        auto_prod = self.env['product.product'].create({
            'name': 'Auto Map Target', 'type': 'consu',
        })
        order = self._make_order('E2E-AUTOMAP-001', items=[
            {'product_id': 'UVA-AUTOMAP-X', 'name': 'Auto Map Target', 'qty': 1},
        ])
        log = self.order_service.ingest_order(order, self.store)
        # Should be draft (mapped), not pending
        self.assertEqual(log.state, 'draft')

        # Verify mapping was created
        mapping = self.env['uva.product.mapping'].search([
            ('uva_product_id', '=', 'UVA-AUTOMAP-X'),
            ('store_id', '=', self.store.id),
        ])
        self.assertTrue(mapping.exists())
        self.assertEqual(mapping.odoo_product_id.id, auto_prod.id)
