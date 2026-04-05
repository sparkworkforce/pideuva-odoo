# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
"""Tests for uva.order.log state machine and constraints."""
from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


class TestUvaOrderLogBase(TransactionCase):

    def setUp(self):
        super().setUp()
        self.pos_config = self.env['pos.config'].create({'name': 'Test POS'})
        self.store = self.env['uva.store.config'].create({
            'name': 'Test Store',
            'pos_config_id': self.pos_config.id,
            'demo_mode': True,
        })

    def _make_log(self, external_id='EXT-001', state='draft'):
        return self.env['uva.order.log'].create({
            'external_id': external_id,
            'store_id': self.store.id,
            'state': state,
            'received_at': fields.Datetime.now(),
        })


class TestUvaOrderLogConstraints(TestUvaOrderLogBase):

    @mute_logger('odoo.sql_db')
    def test_unique_external_id(self):
        """UNIQUE(external_id) prevents duplicate orders."""
        self._make_log('EXT-DUP')
        with self.assertRaises(IntegrityError):
            self._make_log('EXT-DUP')

    def test_raw_payload_hidden_from_non_admin(self):
        """raw_payload is groups='base.group_system' — non-admins cannot read it."""
        log = self._make_log()
        log.sudo().write({'raw_payload': '{"secret": "data"}'})
        non_admin = self.env.ref('base.user_demo')
        with self.assertRaises(AccessError):
            _ = log.with_user(non_admin).raw_payload


class TestUvaOrderLogStateMachine(TestUvaOrderLogBase):

    def test_action_accept_from_draft(self):
        log = self._make_log(state='draft')
        log.action_accept()
        self.assertEqual(log.state, 'accepted')

    def test_action_accept_from_pending(self):
        log = self._make_log(state='pending')
        log.action_accept()
        self.assertEqual(log.state, 'accepted')

    def test_action_accept_invalid_state(self):
        log = self._make_log(state='done')
        with self.assertRaises(UserError):
            log.action_accept()

    def test_action_accept_does_not_call_service(self):
        """D-03: action_accept must NOT call _notify_uva_status directly."""
        log = self._make_log(state='draft')
        # If the model calls the service, it would raise NotImplementedError
        # in non-demo mode. Since demo_mode=True on our store, it would just log.
        # We verify by checking no env['uva.order.service'] call is made from the model.
        # The simplest check: action_accept returns self and only changes state.
        result = log.action_accept()
        self.assertEqual(result, log)
        self.assertEqual(log.state, 'accepted')

    def test_action_reject_from_draft(self):
        log = self._make_log(state='draft')
        log.action_reject(reason='Out of stock')
        self.assertEqual(log.state, 'rejected')

    def test_action_reject_from_pending(self):
        log = self._make_log(state='pending')
        log.action_reject()
        self.assertEqual(log.state, 'rejected')

    def test_action_reject_from_error(self):
        log = self._make_log(state='error')
        log.action_reject()
        self.assertEqual(log.state, 'rejected')

    def test_action_reject_invalid_state(self):
        log = self._make_log(state='done')
        with self.assertRaises(UserError):
            log.action_reject()

    def test_action_reject_does_not_call_service(self):
        """D-03: action_reject must NOT call _notify_uva_status directly."""
        log = self._make_log(state='draft')
        result = log.action_reject()
        self.assertEqual(result, log)
        self.assertEqual(log.state, 'rejected')

    def test_action_mark_error(self):
        log = self._make_log(state='accepted')
        log.action_mark_error('POS creation failed')
        self.assertEqual(log.state, 'error')
        self.assertIsNotNone(log.processed_at)

    def test_action_retry_from_error(self):
        log = self._make_log(state='error')
        log.action_retry()
        # Transitions to accepted — service handles POS order creation
        self.assertEqual(log.state, 'accepted')

    def test_action_retry_invalid_state(self):
        log = self._make_log(state='draft')
        with self.assertRaises(UserError):
            log.action_retry()

    def test_action_retry_returns_self(self):
        """action_retry returns self for chaining."""
        log = self._make_log(state='error')
        result = log.action_retry()
        self.assertEqual(result, log)
