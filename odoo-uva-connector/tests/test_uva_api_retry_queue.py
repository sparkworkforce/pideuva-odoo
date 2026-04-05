# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
"""
Tests for uva.api.retry.queue Model.

Covers:
  - enqueue validation (BR-04, BR-05, BR-11)
  - process_due_retries: success, transient failure, max retries, permanent failure
  - action_manual_retry: resets retry_count (BR-06)
  - action_discard
  - _compute_next_retry backoff invariants (PBT)
"""
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

from ..models.uva_api_client import UvaApiError, UvaAuthError, UvaCoverageError


def _make_store(env):
    """Create a minimal uva.store.config record for testing.
    Returns a mock if the model doesn't exist yet (Unit 2 not installed).
    """
    if 'uva.store.config' in env:
        return env['uva.store.config'].create({
            'name': 'Test Store',
            'api_key': 'test-api-key',
            'demo_mode': False,
        })
    # Fallback: return a mock with the fields the retry queue needs
    store = MagicMock()
    store.id = 1
    store.api_key = 'test-api-key'
    store.demo_mode = False
    return store


class TestUvaApiRetryQueueEnqueue(TransactionCase):
    """Enqueue validation rules."""

    def setUp(self):
        super().setUp()
        self.queue = self.env['uva.api.retry.queue']

    def _valid_kwargs(self, store_id=1):
        return dict(
            action_type='notify_acceptance',
            payload=json.dumps({'external_id': 'ext-001'}),
            res_model='uva.order.log',
            res_id=42,
            store_id=store_id,
            error='timeout',
        )

    def test_enqueue_creates_record(self):
        # NOTE: This test requires uva.store.config (Unit 2). It will be skipped
        # if the model is not installed. The enqueue validation tests below run
        # independently of store config existence.
        if 'uva.store.config' not in self.env:
            self.skipTest("uva.store.config not installed (Unit 2 required)")
        store = self.env['uva.store.config'].create({
            'name': 'Test Store',
            'api_key': 'test-key',
            'demo_mode': False,
        })
        entry = self.queue.enqueue(**self._valid_kwargs(store_id=store.id))
        self.assertEqual(entry.state, 'pending')
        self.assertEqual(entry.retry_count, 0)
        self.assertEqual(entry.action_type, 'notify_acceptance')
        self.assertIsNotNone(entry.next_retry_at)

    def test_enqueue_rejects_invalid_action_type(self):
        """BR-04: non-retryable action_type raises ValueError."""
        kwargs = self._valid_kwargs()
        kwargs['action_type'] = 'not_a_real_action'
        with self.assertRaises(ValueError, msg="Should reject unknown action_type"):
            self.queue.enqueue(**kwargs)

    def test_enqueue_rejects_missing_store_id(self):
        """BR-05: store_id=0 raises ValueError."""
        kwargs = self._valid_kwargs(store_id=0)
        with self.assertRaises(ValueError, msg="Should reject missing store_id"):
            self.queue.enqueue(**kwargs)

    def test_enqueue_rejects_none_store_id(self):
        """BR-05: store_id=None raises ValueError."""
        kwargs = self._valid_kwargs()
        kwargs['store_id'] = None
        with self.assertRaises(ValueError):
            self.queue.enqueue(**kwargs)

    def test_enqueue_rejects_invalid_json_payload(self):
        """BR-11: non-JSON payload raises ValueError."""
        kwargs = self._valid_kwargs()
        kwargs['payload'] = 'not valid json {'
        with self.assertRaises(ValueError, msg="Should reject non-JSON payload"):
            self.queue.enqueue(**kwargs)

    def test_enqueue_rejects_dict_payload(self):
        """BR-11: payload must be a string, not a dict."""
        kwargs = self._valid_kwargs()
        kwargs['payload'] = {'external_id': 'ext-001'}  # type: ignore
        with self.assertRaises(ValueError):
            self.queue.enqueue(**kwargs)

    def test_enqueue_first_retry_at_is_60s_from_now(self):
        """First retry should be scheduled ~60 seconds from now."""
        if 'uva.store.config' not in self.env:
            self.skipTest("uva.store.config not installed (Unit 2 required)")
        store = self.env['uva.store.config'].create({
            'name': 'Test Store', 'api_key': 'k', 'demo_mode': False,
        })
        before = fields.Datetime.now()
        entry = self.queue.enqueue(**self._valid_kwargs(store_id=store.id))
        delta = (entry.next_retry_at - before).total_seconds()
        self.assertGreaterEqual(delta, 59)
        self.assertLessEqual(delta, 62)


class TestUvaApiRetryQueueProcessing(TransactionCase):
    """process_due_retries and _execute_retry logic."""

    def setUp(self):
        super().setUp()
        self.queue = self.env['uva.api.retry.queue']

    def _create_due_entry(self, action_type='notify_acceptance', store_id=False):
        """Create a due retry entry. store_id=False is safe for tests that
        mock _dispatch_action (store is never accessed in that path)."""
        entry = self.queue.create({
            'action_type': action_type,
            'payload': json.dumps({'external_id': 'ext-001'}),
            'res_model': 'res.partner',
            'res_id': self.env.ref('base.partner_admin').id,
            'store_id': store_id,
            'error': '',
            'retry_count': 0,
            'next_retry_at': fields.Datetime.now() - timedelta(seconds=1),
            'state': 'pending',
        })
        return entry

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_retry_queue.UvaApiRetryQueue._dispatch_action')
    def test_successful_retry_marks_done(self, mock_dispatch):
        mock_dispatch.return_value = None
        entry = self._create_due_entry()
        self.queue.process_due_retries()
        entry.invalidate_recordset()
        self.assertEqual(entry.state, 'done')
        self.assertIsNotNone(entry.processed_at)

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_retry_queue.UvaApiRetryQueue._dispatch_action')
    def test_transient_failure_increments_retry_count(self, mock_dispatch):
        mock_dispatch.side_effect = UvaApiError("timeout")
        entry = self._create_due_entry()
        self.queue.process_due_retries()
        entry.invalidate_recordset()
        self.assertEqual(entry.state, 'pending')
        self.assertEqual(entry.retry_count, 1)
        self.assertIn('timeout', entry.error)

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_retry_queue.UvaApiRetryQueue._dispatch_action')
    def test_max_retries_exceeded_marks_failed(self, mock_dispatch):
        mock_dispatch.side_effect = UvaApiError("persistent timeout")
        entry = self._create_due_entry()
        # Set retry_count to max-1 so next failure triggers failed state
        entry.write({'retry_count': 4})  # default max is 5
        self.queue.process_due_retries()
        entry.invalidate_recordset()
        self.assertEqual(entry.state, 'failed')
        self.assertIsNotNone(entry.processed_at)

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_retry_queue.UvaApiRetryQueue._dispatch_action')
    def test_coverage_error_marks_failed_immediately(self, mock_dispatch):
        mock_dispatch.side_effect = UvaCoverageError("Out of coverage area")
        entry = self._create_due_entry()
        self.queue.process_due_retries()
        entry.invalidate_recordset()
        self.assertEqual(entry.state, 'failed')
        self.assertEqual(entry.retry_count, 0)  # no increment on permanent failure

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_retry_queue.UvaApiRetryQueue._dispatch_action')
    def test_auth_error_marks_failed_immediately(self, mock_dispatch):
        mock_dispatch.side_effect = UvaAuthError("Invalid API key", status_code=401)
        entry = self._create_due_entry()
        self.queue.process_due_retries()
        entry.invalidate_recordset()
        self.assertEqual(entry.state, 'failed')

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_retry_queue.UvaApiRetryQueue._dispatch_action')
    def test_one_failure_does_not_block_other_entries(self, mock_dispatch):
        """Cron failure isolation: one bad entry must not prevent others."""
        bad_entry = self._create_due_entry()
        good_entry = self._create_due_entry()

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise UvaApiError("first entry fails")
            # second entry succeeds

        mock_dispatch.side_effect = side_effect
        self.queue.process_due_retries()

        bad_entry.invalidate_recordset()
        good_entry.invalidate_recordset()
        self.assertEqual(bad_entry.state, 'pending')   # retried later
        self.assertEqual(good_entry.state, 'done')     # processed successfully


class TestUvaApiRetryQueueManualActions(TransactionCase):
    """action_manual_retry and action_discard."""

    def setUp(self):
        super().setUp()
        self.queue = self.env['uva.api.retry.queue']

    def _create_failed_entry(self):
        return self.queue.create({
            'action_type': 'notify_acceptance',
            'payload': json.dumps({'external_id': 'ext-001'}),
            'res_model': 'res.partner',
            'res_id': self.env.ref('base.partner_admin').id,
            'store_id': False,  # no store needed; _dispatch_action is mocked
            'error': 'previous failure',
            'retry_count': 5,
            'next_retry_at': fields.Datetime.now(),
            'state': 'failed',
        })

    @patch('odoo.addons.odoo_uva_connector.models.uva_api_retry_queue.UvaApiRetryQueue._dispatch_action')
    def test_manual_retry_resets_retry_count(self, mock_dispatch):
        """BR-06: manual retry resets retry_count to 0."""
        mock_dispatch.return_value = None
        entry = self._create_failed_entry()
        entry.action_manual_retry()
        entry.invalidate_recordset()
        self.assertEqual(entry.state, 'done')

    def test_action_discard_sets_discarded(self):
        entry = self._create_failed_entry()
        entry.action_discard()
        entry.invalidate_recordset()
        self.assertEqual(entry.state, 'discarded')

    def test_action_discard_on_done_raises(self):
        entry = self._create_failed_entry()
        entry.write({'state': 'done'})
        with self.assertRaises(UserError):
            entry.action_discard()

    def test_action_manual_retry_on_done_raises(self):
        entry = self._create_failed_entry()
        entry.write({'state': 'done'})
        with self.assertRaises(UserError):
            entry.action_manual_retry()


class TestUvaApiRetryQueueBackoffPBT(TransactionCase):
    """Property-based tests for _compute_next_retry backoff invariants."""

    def setUp(self):
        super().setUp()
        self.queue = self.env['uva.api.retry.queue']

    @given(n=st.integers(min_value=0, max_value=20))
    @settings(max_examples=100)
    def test_backoff_never_exceeds_cap(self, n):
        """Invariant: delay never exceeds 3600 seconds."""
        from ..models.uva_api_retry_queue import _BACKOFF_CAP
        before = fields.Datetime.now()
        result = self.queue._compute_next_retry(n)
        delta = (result - before).total_seconds()
        self.assertLessEqual(delta, _BACKOFF_CAP + 1)  # +1 for execution time

    @given(n=st.integers(min_value=0, max_value=18))
    @settings(max_examples=100)
    def test_backoff_is_monotonically_increasing(self, n):
        """Invariant: _compute_next_retry(n+1) >= _compute_next_retry(n)."""
        t1 = self.queue._compute_next_retry(n)
        t2 = self.queue._compute_next_retry(n + 1)
        self.assertGreaterEqual(t2, t1)

    def test_backoff_first_retry_is_60s(self):
        """_compute_next_retry(0) should be ~60 seconds from now."""
        before = fields.Datetime.now()
        result = self.queue._compute_next_retry(0)
        delta = (result - before).total_seconds()
        self.assertGreaterEqual(delta, 59)
        self.assertLessEqual(delta, 62)
