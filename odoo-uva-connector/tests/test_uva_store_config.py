# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
"""
Tests for uva.store.config and uva.product.mapping models.

Covers all 13 test cases specified in the Unit 2 code generation plan.
"""
from datetime import timedelta
from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


class TestUvaStoreConfigBase(TransactionCase):
    """Shared setup for store config tests."""

    def setUp(self):
        super().setUp()
        # Create a POS config to associate with store configs
        self.pos_config = self.env['pos.config'].create({'name': 'Test POS'})
        self.pos_config_2 = self.env['pos.config'].create({'name': 'Test POS 2'})

    def _make_store(self, pos_config=None, **kwargs):
        vals = {
            'name': 'Test Store',
            'pos_config_id': (pos_config or self.pos_config).id,
            'polling_interval': 120,
            'auto_accept_timeout': 120,
        }
        vals.update(kwargs)
        return self.env['uva.store.config'].create(vals)


class TestUvaStoreConfigCreation(TestUvaStoreConfigBase):

    def test_store_config_created_successfully(self):
        store = self._make_store()
        self.assertEqual(store.name, 'Test Store')
        self.assertEqual(store.pos_config_id, self.pos_config)
        self.assertTrue(store.active)
        self.assertFalse(store.demo_mode)
        self.assertEqual(store.polling_interval, 120)
        self.assertEqual(store.auto_accept_timeout, 120)

    def test_polling_interval_minimum_enforced(self):
        """Server-side constraint: polling_interval < 60 raises ValidationError."""
        with self.assertRaises(ValidationError):
            self._make_store(polling_interval=59, polling_enabled=True)

    def test_polling_interval_exactly_60_is_valid(self):
        store = self._make_store(polling_interval=60)
        self.assertEqual(store.polling_interval, 60)

    def test_auto_accept_timeout_zero_is_valid(self):
        """0 means manual-only mode — must be allowed."""
        store = self._make_store(auto_accept_timeout=0)
        self.assertEqual(store.auto_accept_timeout, 0)

    def test_auto_accept_timeout_negative_raises(self):
        with self.assertRaises(ValidationError):
            self._make_store(auto_accept_timeout=-1)

    @mute_logger('odoo.sql_db')
    def test_unique_pos_config_constraint(self):
        """Duplicate pos_config_id raises IntegrityError."""
        self._make_store()
        with self.assertRaises(IntegrityError):
            self._make_store()  # same pos_config


class TestUvaStoreConfigMethods(TestUvaStoreConfigBase):

    def test_get_active_config_for_pos_found(self):
        store = self._make_store()
        result = self.env['uva.store.config'].get_active_config_for_pos(self.pos_config.id)
        self.assertEqual(result, store)

    def test_get_active_config_for_pos_not_found(self):
        """Raises UserError when no active config exists for the given POS."""
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.env['uva.store.config'].get_active_config_for_pos(self.pos_config.id)

    def test_get_active_config_for_pos_inactive_not_returned(self):
        """Inactive store configs raise UserError (not returned)."""
        from odoo.exceptions import UserError
        self._make_store(active=False)
        with self.assertRaises(UserError):
            self.env['uva.store.config'].get_active_config_for_pos(self.pos_config.id)

    def test_poll_orders_if_due_too_soon(self):
        """Returns False when polling_interval has not elapsed."""
        store = self._make_store()
        # Set last_polled_at to 30 seconds ago (interval is 120s)
        store.write({'last_polled_at': fields.Datetime.now() - timedelta(seconds=30)})
        result = store.poll_orders_if_due()
        self.assertFalse(result)

    def test_poll_orders_if_due_ready(self):
        """Returns True and updates last_polled_at when interval has elapsed."""
        store = self._make_store()
        # Set last_polled_at to 130 seconds ago (interval is 120s)
        store.write({'last_polled_at': fields.Datetime.now() - timedelta(seconds=130)})
        before = fields.Datetime.now()
        result = store.poll_orders_if_due()
        self.assertTrue(result)
        store.invalidate_recordset()
        self.assertGreaterEqual(store.last_polled_at, before)

    def test_poll_orders_if_due_never_polled(self):
        """Returns True when last_polled_at is not set (first poll)."""
        store = self._make_store()
        self.assertFalse(store.last_polled_at)
        result = store.poll_orders_if_due()
        self.assertTrue(result)

    def test_poll_orders_if_due_disabled(self):
        """Returns False when polling is disabled."""
        store = self._make_store(polling_enabled=False)
        result = store.poll_orders_if_due()
        self.assertFalse(result)

    def test_poll_orders_if_due_inactive(self):
        """Returns False when store is inactive."""
        store = self._make_store(active=False)
        result = store.poll_orders_if_due()
        self.assertFalse(result)


class TestUvaStoreConfigCredentialAccess(TestUvaStoreConfigBase):

    def test_credential_fields_hidden_from_non_admin(self):
        """api_key and webhook_secret must not be readable by base.group_user."""
        store = self.env['uva.store.config'].sudo().create({
            'name': 'Secure Store',
            'pos_config_id': self.pos_config.id,
            'api_key': 'secret-key-123',
            'webhook_secret': 'secret-webhook-456',
        })
        # Read as a non-admin user (portal or internal without system group)
        non_admin = self.env.ref('base.user_demo')
        store_as_user = store.with_user(non_admin)
        # Fields with groups='base.group_system' raise AccessError for non-admins
        with self.assertRaises(AccessError):
            _ = store_as_user.api_key
        with self.assertRaises(AccessError):
            _ = store_as_user.webhook_secret


class TestUvaProductMappingBase(TransactionCase):

    def setUp(self):
        super().setUp()
        self.pos_config = self.env['pos.config'].create({'name': 'Test POS'})
        self.pos_config_2 = self.env['pos.config'].create({'name': 'Test POS 2'})
        self.store = self.env['uva.store.config'].create({
            'name': 'Store A',
            'pos_config_id': self.pos_config.id,
        })
        self.store_2 = self.env['uva.store.config'].create({
            'name': 'Store B',
            'pos_config_id': self.pos_config_2.id,
        })
        # Create a product to map to
        self.product = self.env['product.product'].create({
            'name': 'Test Product',
            'type': 'consu',
        })
        self.product_2 = self.env['product.product'].create({
            'name': 'Test Product 2',
            'type': 'consu',
        })

    def _make_mapping(self, uva_id='UVA-001', store=None, product=None):
        return self.env['uva.product.mapping'].create({
            'uva_product_id': uva_id,
            'store_id': (store or self.store).id,
            'odoo_product_id': (product or self.product).id,
        })


class TestUvaProductMappingConstraints(TestUvaProductMappingBase):

    @mute_logger('odoo.sql_db')
    def test_product_mapping_unique_per_store(self):
        """Duplicate (uva_product_id, store_id) raises IntegrityError."""
        self._make_mapping()
        with self.assertRaises(IntegrityError):
            self._make_mapping()  # same uva_id + same store

    def test_product_mapping_different_stores_same_uva_id(self):
        """Same Uva product ID can be mapped in different stores — no constraint violation."""
        m1 = self._make_mapping(store=self.store)
        m2 = self._make_mapping(store=self.store_2)
        self.assertTrue(m1.exists())
        self.assertTrue(m2.exists())


class TestUvaProductMappingMethods(TestUvaProductMappingBase):

    def test_get_odoo_product_found(self):
        self._make_mapping(uva_id='UVA-001')
        result = self.env['uva.product.mapping'].get_odoo_product('UVA-001', self.store.id)
        self.assertEqual(result, self.product)

    def test_get_odoo_product_not_found(self):
        """Returns None when no mapping exists — does NOT raise."""
        result = self.env['uva.product.mapping'].get_odoo_product('UNKNOWN-ID', self.store.id)
        self.assertIsNone(result)

    def test_get_odoo_product_inactive_not_returned(self):
        """Inactive mappings are not returned."""
        mapping = self._make_mapping(uva_id='UVA-002')
        mapping.write({'active': False})
        result = self.env['uva.product.mapping'].get_odoo_product('UVA-002', self.store.id)
        self.assertIsNone(result)

    def test_get_odoo_product_wrong_store(self):
        """Mapping for store A is not returned when querying store B."""
        self._make_mapping(uva_id='UVA-003', store=self.store)
        result = self.env['uva.product.mapping'].get_odoo_product('UVA-003', self.store_2.id)
        self.assertIsNone(result)
