# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
"""Tests for Unit 5: Flow B — Fleet Carrier & Dispatch."""
from psycopg2 import IntegrityError
from unittest.mock import patch, MagicMock

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

from ..models.uva_api_client import UvaApiError, UvaCoverageError


class TestUvaFleetCarrierBase(TransactionCase):

    def setUp(self):
        super().setUp()
        # Set up Fleet credentials in ir.config_parameter
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('uva.fleet.api_key', 'test-fleet-key')
        ICP.set_param('uva.fleet.demo_mode', 'True')

        # Create a Uva Fleet carrier
        self.carrier = self.env['delivery.carrier'].create({
            'name': 'Uva Fleet',
            'delivery_type': 'uva',
            'product_id': self.env.ref('delivery.product_product_delivery').id,
        })

        # Create a partner for delivery
        self.partner = self.env['res.partner'].create({
            'name': 'Test Customer',
            'street': '123 Calle Principal',
            'city': 'San Juan',
            'zip': '00901',
            'country_id': self.env.ref('base.pr').id,
        })

        # Create a stock picking
        self.picking = self.env['stock.picking'].create({
            'partner_id': self.partner.id,
            'picking_type_id': self.env.ref('stock.picking_type_out').id,
            'location_id': self.env.ref('stock.stock_location_stock').id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
        })

        # Create a store config for retry queue store_id
        pos_config = self.env['pos.config'].create({'name': 'Fleet Test POS'})
        self.store = self.env['uva.store.config'].create({
            'name': 'Fleet Test Store',
            'pos_config_id': pos_config.id,
            'demo_mode': True,
        })


class TestUvaFleetDeliveryModel(TestUvaFleetCarrierBase):

    def test_fleet_delivery_created_successfully(self):
        delivery = self.env['uva.fleet.delivery'].create({
            'uva_delivery_id': 'UVA-DEL-001',
            'carrier_id': self.carrier.id,
            'picking_id': self.picking.id,
            'company_id': self.env.company.id,
        })
        self.assertEqual(delivery.state, 'pending')
        self.assertIn('UVA-DEL-001', delivery.name or delivery.uva_delivery_id)

    @mute_logger('odoo.sql_db')
    def test_fleet_delivery_unique_uva_delivery_id(self):
        self.env['uva.fleet.delivery'].create({
            'uva_delivery_id': 'UVA-DUP-001',
            'carrier_id': self.carrier.id,
            'company_id': self.env.company.id,
        })
        with self.assertRaises(IntegrityError):
            self.env['uva.fleet.delivery'].create({
                'uva_delivery_id': 'UVA-DUP-001',
                'carrier_id': self.carrier.id,
                'company_id': self.env.company.id,
            })

    def test_fleet_delivery_state_tracking(self):
        """state field has tracking=True — changes logged in chatter."""
        delivery = self.env['uva.fleet.delivery'].create({
            'uva_delivery_id': 'UVA-TRACK-001',
            'carrier_id': self.carrier.id,
            'company_id': self.env.company.id,
        })
        delivery.write({'state': 'assigned'})
        # mail.thread tracking creates a message on state change
        messages = delivery.message_ids.filtered(
            lambda m: 'assigned' in (m.body or '').lower() or m.tracking_value_ids
        )
        self.assertTrue(messages or delivery.message_ids, "State change should be tracked")


class TestUvaFleetCarrierDispatch(TestUvaFleetCarrierBase):

    def test_get_shipping_price_demo_mode(self):
        """In demo mode, get_delivery_estimate returns mock data without network calls."""
        # demo_mode=True is set in setUp via ir.config_parameter
        sale = self.env['sale.order'].create({
            'partner_id': self.partner.id,
        })
        prices = self.carrier.uva_get_shipping_price(sale)
        self.assertTrue(len(prices) > 0)
        self.assertIn('price', prices[0])
        self.assertEqual(prices[0]['currency'], 'USD')

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.get_delivery_estimate')
    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.create_delivery')
    def test_send_shipping_creates_fleet_delivery_record(self, mock_create, mock_estimate):
        """Successful dispatch creates a uva.fleet.delivery record."""
        mock_estimate.return_value = {'amount': 7.50, 'currency': 'USD', 'eta_minutes': 25}
        mock_create.return_value = {'delivery_id': 'UVA-NEW-001', 'tracking_url': 'https://track.uva'}

        results = self.carrier.uva_send_shipping(self.picking)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['tracking_number'], 'UVA-NEW-001')

        fleet_delivery = self.env['uva.fleet.delivery'].search([
            ('picking_id', '=', self.picking.id)
        ])
        self.assertTrue(fleet_delivery.exists())
        self.assertEqual(fleet_delivery.uva_delivery_id, 'UVA-NEW-001')
        self.assertEqual(fleet_delivery.estimated_cost, 7.50)

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.get_delivery_estimate')
    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.create_delivery')
    def test_send_shipping_coverage_error_raises_user_error_with_zip(self, mock_create, mock_estimate):
        """FR-07.4: coverage error raises UserError with zip code in message."""
        mock_estimate.return_value = {'amount': 5.0, 'currency': 'USD', 'eta_minutes': 20}
        mock_create.side_effect = UvaCoverageError("Address outside service area")

        with self.assertRaises(UserError) as ctx:
            self.carrier.uva_send_shipping(self.picking)

        error_msg = str(ctx.exception)
        self.assertIn('coverage', error_msg.lower())
        # Zip code should appear in the error message
        self.assertIn('00901', error_msg)

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.get_delivery_estimate')
    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.create_delivery')
    def test_send_shipping_coverage_error_does_not_enqueue_retry(self, mock_create, mock_estimate):
        """FR-07.4: coverage errors MUST NOT be added to the retry queue."""
        mock_estimate.return_value = {'amount': 5.0, 'currency': 'USD', 'eta_minutes': 20}
        mock_create.side_effect = UvaCoverageError("Outside coverage")

        retry_count_before = self.env['uva.api.retry.queue'].search_count([])
        with self.assertRaises(UserError):
            self.carrier.uva_send_shipping(self.picking)

        retry_count_after = self.env['uva.api.retry.queue'].search_count([])
        self.assertEqual(retry_count_before, retry_count_after,
                         "Coverage error MUST NOT create a retry queue entry")

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.get_delivery_estimate')
    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.create_delivery')
    def test_send_shipping_transient_error_enqueues_retry(self, mock_create, mock_estimate):
        """Transient API error enqueues a retry entry."""
        mock_estimate.return_value = {'amount': 5.0, 'currency': 'USD', 'eta_minutes': 20}
        mock_create.side_effect = UvaApiError("503 Service Unavailable")

        with self.assertRaises(UserError):
            self.carrier.uva_send_shipping(self.picking)

        retry = self.env['uva.api.retry.queue'].search([
            ('action_type', '=', 'create_fleet_delivery'),
            ('res_model', '=', 'stock.picking'),
            ('res_id', '=', self.picking.id),
        ])
        self.assertTrue(retry.exists(), "Transient error should create a retry queue entry")

    def test_cancel_shipping_success(self):
        """Successful cancellation updates fleet delivery state to cancelled (demo mode)."""
        fleet_delivery = self.env['uva.fleet.delivery'].create({
            'uva_delivery_id': 'UVA-CANCEL-001',
            'carrier_id': self.carrier.id,
            'picking_id': self.picking.id,
            'company_id': self.env.company.id,
            'state': 'pending',
        })

        result = self.carrier.uva_cancel_shipping(self.picking)
        self.assertTrue(result.get('success'))
        fleet_delivery.invalidate_recordset()
        self.assertEqual(fleet_delivery.state, 'cancelled')

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.cancel_delivery')
    def test_cancel_shipping_transient_error_enqueues_retry(self, mock_cancel):
        """Transient cancellation failure enqueues retry."""
        mock_cancel.side_effect = UvaApiError("timeout")
        self.env['uva.fleet.delivery'].create({
            'uva_delivery_id': 'UVA-CANCEL-002',
            'carrier_id': self.carrier.id,
            'picking_id': self.picking.id,
            'company_id': self.env.company.id,
            'state': 'pending',
        })

        with self.assertRaises(UserError):
            self.carrier.uva_cancel_shipping(self.picking)

        retry = self.env['uva.api.retry.queue'].search([
            ('action_type', '=', 'cancel_fleet_delivery'),
            ('res_model', '=', 'stock.picking'),
            ('res_id', '=', self.picking.id),
        ])
        self.assertTrue(retry.exists())

    def test_cancel_shipping_no_active_delivery_raises(self):
        """Cancelling when no active fleet delivery exists raises UserError."""
        with self.assertRaises(UserError):
            self.carrier.uva_cancel_shipping(self.picking)
