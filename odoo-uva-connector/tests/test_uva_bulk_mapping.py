# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
"""Tests for uva.bulk.mapping.wizard."""
import json

from odoo.tests.common import TransactionCase


class TestUvaBulkMapping(TransactionCase):

    def setUp(self):
        super().setUp()
        self.pos_config = self.env['pos.config'].create({'name': 'Bulk POS'})
        self.store = self.env['uva.store.config'].create({
            'name': 'Bulk Store',
            'pos_config_id': self.pos_config.id,
            'demo_mode': True,
        })
        self.product_chicken = self.env['product.product'].create({
            'name': 'Chicken Sandwich',
            'type': 'consu',
        })

    def _make_wizard(self):
        return self.env['uva.bulk.mapping.wizard'].create({
            'store_id': self.store.id,
        })

    def _make_order_log(self, items, external_id='UVA-001'):
        return self.env['uva.order.log'].create({
            'external_id': external_id,
            'store_id': self.store.id,
            'raw_payload': json.dumps({'id': external_id, 'items': items}),
        })

    def test_load_unmapped_finds_products_from_orders(self):
        self._make_order_log([
            {'product_id': 'PROD-1', 'name': 'Chicken Sandwich', 'qty': 1},
            {'product_id': 'PROD-2', 'name': 'Beef Burger', 'qty': 2},
        ])
        wiz = self._make_wizard()
        wiz.action_load_unmapped()
        self.assertEqual(len(wiz.line_ids), 2)
        uva_ids = set(wiz.line_ids.mapped('uva_product_id'))
        self.assertEqual(uva_ids, {'PROD-1', 'PROD-2'})

    def test_auto_match_finds_exact_name(self):
        self._make_order_log([
            {'product_id': 'PROD-1', 'name': 'Chicken Sandwich', 'qty': 1},
        ])
        wiz = self._make_wizard()
        wiz.action_load_unmapped()
        wiz.action_auto_match()
        line = wiz.line_ids[0]
        self.assertEqual(line.odoo_product_id, self.product_chicken)

    def test_auto_match_skips_ambiguous(self):
        self.env['product.product'].create({'name': 'Chicken Sandwich Deluxe', 'type': 'consu'})
        self._make_order_log([
            {'product_id': 'PROD-1', 'name': 'Chicken Sandwich', 'qty': 1},
        ])
        wiz = self._make_wizard()
        wiz.action_load_unmapped()
        wiz.action_auto_match()
        line = wiz.line_ids[0]
        # ilike 'Chicken Sandwich' matches both → ambiguous → skipped
        self.assertFalse(line.odoo_product_id)

    def test_apply_creates_mappings(self):
        self._make_order_log([
            {'product_id': 'PROD-1', 'name': 'Chicken Sandwich', 'qty': 1},
        ])
        wiz = self._make_wizard()
        wiz.action_load_unmapped()
        wiz.action_auto_match()
        wiz.action_apply()
        mapping = self.env['uva.product.mapping'].search([
            ('uva_product_id', '=', 'PROD-1'),
            ('store_id', '=', self.store.id),
        ])
        self.assertEqual(len(mapping), 1)
        self.assertEqual(mapping.odoo_product_id, self.product_chicken)

    def test_apply_skips_existing_mappings(self):
        self.env['uva.product.mapping'].create({
            'uva_product_id': 'PROD-1',
            'store_id': self.store.id,
            'odoo_product_id': self.product_chicken.id,
        })
        self._make_order_log([
            {'product_id': 'PROD-1', 'name': 'Chicken Sandwich', 'qty': 1},
        ])
        wiz = self._make_wizard()
        wiz.action_load_unmapped()
        # Force a match on the line manually (load_unmapped skips existing)
        # Since load_unmapped filters existing, line_ids should be empty
        self.assertEqual(len(wiz.line_ids), 0)
        wiz.action_apply()
        count = self.env['uva.product.mapping'].search_count([
            ('uva_product_id', '=', 'PROD-1'),
            ('store_id', '=', self.store.id),
        ])
        self.assertEqual(count, 1)
