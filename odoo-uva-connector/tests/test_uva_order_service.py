# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
"""Tests for uva.order.service AbstractModel — including PBT and cron isolation."""
import json
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from odoo import fields
from odoo.tests.common import TransactionCase

from ..models.uva_api_client import UvaApiError


class TestUvaOrderServiceBase(TransactionCase):

    def setUp(self):
        super().setUp()
        self.pos_config = self.env['pos.config'].create({'name': 'Test POS'})
        self.store = self.env['uva.store.config'].create({
            'name': 'Test Store',
            'pos_config_id': self.pos_config.id,
            'demo_mode': True,
            'polling_enabled': True,
            'polling_interval': 60,
        })
        self.product = self.env['product.product'].create({
            'name': 'Test Product', 'type': 'consu',
        })
        self.mapping = self.env['uva.product.mapping'].create({
            'uva_product_id': 'UVA-PROD-1',
            'odoo_product_id': self.product.id,
            'store_id': self.store.id,
        })
        self.service = self.env['uva.order.service']

    def _raw_order(self, ext_id='EXT-001', items=None):
        return {
            'id': ext_id,
            'items': items or [{'product_id': 'UVA-PROD-1', 'qty': 1}],
        }


class TestUvaOrderServiceIngest(TestUvaOrderServiceBase):

    @patch('odoo.addons.odoo_uva_connector.models.uva_order_service.UvaOrderService._notify_pos')
    def test_ingest_order_new(self, mock_notify):
        log = self.service.ingest_order(self._raw_order(), self.store)
        self.assertTrue(log.exists())
        self.assertEqual(log.external_id, 'EXT-001')
        self.assertIn(log.state, ('draft', 'accepted'))
        mock_notify.assert_called_once()

    @patch('odoo.addons.odoo_uva_connector.models.uva_order_service.UvaOrderService._notify_pos')
    def test_ingest_order_duplicate_returns_existing(self, mock_notify):
        """Duplicate external_id returns existing record without creating a new one."""
        log1 = self.service.ingest_order(self._raw_order('EXT-DUP'), self.store)
        log2 = self.service.ingest_order(self._raw_order('EXT-DUP'), self.store)
        self.assertEqual(log1.id, log2.id)
        # _notify_pos called only once (first ingest)
        self.assertEqual(mock_notify.call_count, 1)

    @patch('odoo.addons.odoo_uva_connector.models.uva_order_service.UvaOrderService._notify_pos')
    def test_ingest_order_unmapped_products_sets_pending(self, mock_notify):
        """Orders with unmapped products go to PENDING state."""
        raw = self._raw_order(items=[{'product_id': 'UNKNOWN-ID', 'qty': 1}])
        log = self.service.ingest_order(raw, self.store)
        self.assertEqual(log.state, 'pending')
        # POS still notified so staff can see the pending order
        mock_notify.assert_called_once()

    @patch('odoo.addons.odoo_uva_connector.models.uva_order_service.UvaOrderService._notify_pos')
    def test_ingest_order_all_mapped_notifies_pos(self, mock_notify):
        """All-mapped orders notify POS."""
        log = self.service.ingest_order(self._raw_order(), self.store)
        self.assertIsNotNone(log)
        mock_notify.assert_called_once_with(log)


class TestUvaOrderServiceStaffActions(TestUvaOrderServiceBase):

    def _make_log(self, state='draft'):
        return self.env['uva.order.log'].create({
            'external_id': f'EXT-{state}-{fields.Datetime.now()}',
            'store_id': self.store.id,
            'state': state,
            'received_at': fields.Datetime.now(),
        })

    def test_process_staff_action_accept(self):
        log = self._make_log('draft')
        self.service.process_staff_action(log.id, 'accept')
        log.invalidate_recordset()
        self.assertEqual(log.state, 'accepted')

    def test_process_staff_action_reject(self):
        log = self._make_log('draft')
        self.service.process_staff_action(log.id, 'reject')
        log.invalidate_recordset()
        self.assertEqual(log.state, 'rejected')

    @patch('odoo.addons.odoo_uva_connector.models.uva_order_service.UvaOrderService._notify_uva_status')
    def test_process_staff_action_enqueues_retry_on_api_error(self, mock_notify):
        """On UvaApiError from _notify_uva_status, a retry queue entry is created."""
        mock_notify.side_effect = UvaApiError("timeout")
        log = self._make_log('draft')
        self.service.process_staff_action(log.id, 'accept')
        # Retry queue entry should exist
        retry = self.env['uva.api.retry.queue'].search([
            ('res_model', '=', 'uva.order.log'),
            ('res_id', '=', log.id),
        ])
        self.assertTrue(retry.exists())
        self.assertEqual(retry.action_type, 'notify_acceptance')


class TestUvaOrderServicePollIsolation(TestUvaOrderServiceBase):
    """NFR-03.3: failure of one store MUST NOT stop processing of other stores."""

    def setUp(self):
        super().setUp()
        self.pos_config_2 = self.env['pos.config'].create({'name': 'POS 2'})
        self.pos_config_3 = self.env['pos.config'].create({'name': 'POS 3'})
        self.store_2 = self.env['uva.store.config'].create({
            'name': 'Store 2', 'pos_config_id': self.pos_config_2.id,
            'demo_mode': True, 'polling_enabled': True, 'polling_interval': 60,
        })
        self.store_3 = self.env['uva.store.config'].create({
            'name': 'Store 3', 'pos_config_id': self.pos_config_3.id,
            'demo_mode': True, 'polling_enabled': True, 'polling_interval': 60,
        })

    @patch('odoo.addons.odoo_uva_connector.models.uva_order_service.UvaOrderService._notify_pos')
    @patch('odoo.addons.odoo_uva_connector.models.uva_api_client.UvaApiClient.get_orders')
    def test_poll_all_stores_isolates_failures(self, mock_get_orders, mock_notify):
        """Store 2 API failure MUST NOT prevent Store 3 from being processed."""
        store1_orders = [
            {'id': 'S1-001', 'items': [{'product_id': 'UVA-PROD-1', 'qty': 1}]},
            {'id': 'S1-002', 'items': [{'product_id': 'UVA-PROD-1', 'qty': 2}]},
        ]
        store3_orders = [
            {'id': 'S3-001', 'items': [{'product_id': 'UVA-PROD-1', 'qty': 1}]},
        ]

        def get_orders_side_effect(api_key, store_id, since, demo_mode=False):
            sid = int(store_id)
            if sid == self.store.id:
                return store1_orders
            elif sid == self.store_2.id:
                raise UvaApiError("Store 2 API 500")
            elif sid == self.store_3.id:
                return store3_orders
            return []

        mock_get_orders.side_effect = get_orders_side_effect

        self.service.poll_all_stores()

        # Store 1: 2 orders created
        s1_logs = self.env['uva.order.log'].search([('store_id', '=', self.store.id)])
        self.assertEqual(len(s1_logs), 2, "Store 1 should have 2 orders")

        # Store 2: 0 orders (API failed)
        s2_logs = self.env['uva.order.log'].search([('store_id', '=', self.store_2.id)])
        self.assertEqual(len(s2_logs), 0, "Store 2 should have 0 orders (API failed)")

        # Store 3: 1 order — KEY ASSERTION: Store 2 failure did not stop Store 3
        s3_logs = self.env['uva.order.log'].search([('store_id', '=', self.store_3.id)])
        self.assertEqual(len(s3_logs), 1, "Store 3 should have 1 order despite Store 2 failure")


class TestUvaOrderServicePBT(TestUvaOrderServiceBase):
    """Property-based tests for pure helper methods."""

    def test_deduplicate_idempotence_example(self):
        """Example-based: dedup(dedup(x)) == dedup(x)."""
        log = self.env['uva.order.log'].create({
            'external_id': 'EXT-IDEM',
            'store_id': self.store.id,
            'state': 'draft',
            'received_at': fields.Datetime.now(),
        })
        result1 = self.service._deduplicate('EXT-IDEM')
        result2 = self.service._deduplicate('EXT-IDEM')
        self.assertEqual(result1, result2)
        self.assertEqual(result1, log)

    def test_deduplicate_missing_returns_none(self):
        result = self.service._deduplicate('DOES-NOT-EXIST')
        self.assertIsNone(result)

    @given(
        uva_ids=st.lists(
            st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))),
            min_size=0, max_size=10, unique=True,
        )
    )
    @settings(max_examples=100)
    def test_validate_product_mappings_round_trip(self, uva_ids):
        """PBT round-trip: len(mapped) + len(unmapped) == len(input)."""
        order_lines = [{'product_id': uid, 'qty': 1} for uid in uva_ids]
        mapped, unmapped = self.service._validate_product_mappings(order_lines, self.store)
        self.assertEqual(
            len(mapped) + len(unmapped), len(order_lines),
            f"Round-trip failed: {len(mapped)} mapped + {len(unmapped)} unmapped != {len(order_lines)} input"
        )

    @given(
        uva_ids=st.lists(
            st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))),
            min_size=1, max_size=10, unique=True,
        )
    )
    @settings(max_examples=100)
    def test_validate_product_mappings_no_overlap(self, uva_ids):
        """PBT: no product ID appears in both mapped and unmapped."""
        order_lines = [{'product_id': uid, 'qty': 1} for uid in uva_ids]
        mapped, unmapped = self.service._validate_product_mappings(order_lines, self.store)
        mapped_ids = {line['product_id'] for line in mapped}
        unmapped_set = set(unmapped)
        self.assertEqual(
            mapped_ids & unmapped_set, set(),
            "A product ID appeared in both mapped and unmapped buckets"
        )
