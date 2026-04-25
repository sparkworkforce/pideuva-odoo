# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
"""Tests for uva.setup.wizard."""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestUvaSetupWizard(TransactionCase):

    def setUp(self):
        super().setUp()
        self.pos_config = self.env['pos.config'].create({'name': 'Wizard POS'})

    def _make_wizard(self, **kwargs):
        vals = {'demo_mode': True}
        vals.update(kwargs)
        return self.env['uva.setup.wizard'].create(vals)

    def test_step_credentials_to_store(self):
        wiz = self._make_wizard()
        self.assertEqual(wiz.step, 'credentials')
        wiz.action_next()
        self.assertEqual(wiz.step, 'store')

    def test_step_credentials_requires_api_key_when_not_demo(self):
        wiz = self._make_wizard(demo_mode=False)
        with self.assertRaises(UserError):
            wiz.action_next()

    def test_step_store_to_done_creates_config(self):
        wiz = self._make_wizard()
        wiz.action_next()  # credentials → store
        wiz.write({'name': 'My Store', 'pos_config_id': self.pos_config.id})
        wiz.action_next()  # store → done
        self.assertEqual(wiz.step, 'done')
        self.assertTrue(wiz.store_config_id)
        self.assertEqual(wiz.store_config_id.name, 'My Store')

    def test_back_forward_updates_not_duplicates(self):
        wiz = self._make_wizard()
        wiz.action_next()  # → store
        wiz.write({'name': 'Store V1', 'pos_config_id': self.pos_config.id})
        wiz.action_next()  # → done (creates config)
        config_id = wiz.store_config_id.id
        wiz.action_prev()  # → store
        wiz.write({'name': 'Store V2'})
        wiz.action_next()  # → done (updates config)
        self.assertEqual(wiz.store_config_id.id, config_id)
        self.assertEqual(wiz.store_config_id.name, 'Store V2')

    def test_test_connection_demo_returns_notification(self):
        wiz = self._make_wizard(demo_mode=True)
        result = wiz.action_test_connection()
        self.assertEqual(result['type'], 'ir.actions.client')
        self.assertEqual(result['tag'], 'display_notification')
        self.assertEqual(result['params']['type'], 'success')

    def test_test_connection_non_demo_raises(self):
        wiz = self._make_wizard(demo_mode=False)
        with self.assertRaises(UserError):
            wiz.action_test_connection()

    def test_action_open_product_mapping_without_store_raises(self):
        wiz = self._make_wizard()
        with self.assertRaises(UserError):
            wiz.action_open_product_mapping()
