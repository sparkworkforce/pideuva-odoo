# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestUvaNotification(TransactionCase):
    """Tests for uva.notification SSRF protection and demo mode."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Notification = cls.env['uva.notification']

    def test_webhook_rejects_http(self):
        """Webhook URL must use HTTPS."""
        self.env['ir.config_parameter'].set_param('uva.notification.webhook_url', 'http://evil.com/hook')
        notif = self.Notification.create({
            'notification_type': 'order_accepted',
            'recipient_phone': '+17875551234',
        })
        notif.send_notification()
        self.assertEqual(notif.state, 'failed')
        self.assertIn('HTTPS', notif.error)

    def test_webhook_rejects_private_ip(self):
        """Webhook URL resolving to private IP is blocked."""
        self.env['ir.config_parameter'].set_param('uva.notification.webhook_url', 'https://localhost/hook')
        notif = self.Notification.create({
            'notification_type': 'delivery_assigned',
            'recipient_phone': '+17875551234',
        })
        with patch('socket.gethostbyname', return_value='127.0.0.1'):
            notif.send_notification()
        self.assertEqual(notif.state, 'failed')
        self.assertIn('blocked', notif.error.lower())

    def test_demo_mode_skips_webhook(self):
        """Demo mode marks notification as sent without network call."""
        self.env['ir.config_parameter'].set_param('uva.notification.webhook_url', 'https://real.api/hook')
        notif = self.Notification.create({
            'notification_type': 'order_accepted',
            'recipient_phone': '+17875551234',
        })
        # Force demo mode via linked store mock — no store means demo=True
        notif.send_notification()
        self.assertEqual(notif.state, 'sent')
