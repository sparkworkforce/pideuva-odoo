# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
"""Tests for uva.store.config health check logic."""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class TestUvaHealthCheck(TransactionCase):

    def setUp(self):
        super().setUp()
        self.pos_config = self.env['pos.config'].create({'name': 'Health POS'})
        self.store = self.env['uva.store.config'].create({
            'name': 'Health Store',
            'pos_config_id': self.pos_config.id,
            'polling_enabled': True,
            'polling_interval': 120,
            'demo_mode': False,
        })

    def test_demo_mode_always_ok(self):
        self.store.write({'demo_mode': True})
        self.assertEqual(self.store.check_connection_health(), 'ok')

    def test_webhook_only_store_ok(self):
        self.store.write({'polling_enabled': False})
        self.assertEqual(self.store.check_connection_health(), 'ok')

    def test_recently_polled_ok(self):
        self.store.write({
            'last_polled_at': fields.Datetime.now() - timedelta(seconds=60),
        })
        self.assertEqual(self.store.check_connection_health(), 'ok')

    def test_stale_poll_degraded(self):
        # threshold = 120 * 3 = 360s; degraded when > 360s but <= 720s
        self.store.write({
            'last_polled_at': fields.Datetime.now() - timedelta(seconds=500),
        })
        self.assertEqual(self.store.check_connection_health(), 'degraded')

    def test_very_stale_poll_down(self):
        # > 720s (threshold * 2) → down
        self.store.write({
            'last_polled_at': fields.Datetime.now() - timedelta(seconds=800),
        })
        self.assertEqual(self.store.check_connection_health(), 'down')

    def test_never_polled_down(self):
        self.assertFalse(self.store.last_polled_at)
        self.assertEqual(self.store.check_connection_health(), 'down')

    def test_notify_health_issues_posts_chatter(self):
        # Make store unhealthy (never polled → down)
        self.env['uva.store.config'].action_notify_health_issues()
        messages = self.env['mail.message'].search([
            ('res_id', '=', self.store.id),
            ('model', '=', 'uva.store.config'),
        ])
        self.assertTrue(messages)
        self.assertIn('down', messages[0].body)

    def test_notify_health_issues_no_duplicate_activity(self):
        self.env['uva.store.config'].action_notify_health_issues()
        self.env['uva.store.config'].action_notify_health_issues()
        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'uva.store.config'),
            ('res_id', '=', self.store.id),
            ('summary', 'ilike', 'Uva connection'),
        ])
        self.assertEqual(len(activities), 1)
