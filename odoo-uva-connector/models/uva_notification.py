# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import json
import logging

import requests

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

_NOTIFICATION_MESSAGES = {
    'order_accepted': 'Your order has been accepted!',
    'order_rejected': 'Your order has been rejected.',
    'delivery_assigned': 'A driver has been assigned to your delivery.',
    'delivery_in_transit': 'Your delivery is on the way!',
    'delivery_delivered': 'Your delivery has arrived!',
}


class UvaNotification(models.Model):
    _name = 'uva.notification'
    _description = 'Uva Customer Notification'
    _order = 'create_date desc'

    notification_type = fields.Selection([
        ('order_accepted', 'Order Accepted'),
        ('order_rejected', 'Order Rejected'),
        ('delivery_assigned', 'Driver Assigned'),
        ('delivery_in_transit', 'In Transit'),
        ('delivery_delivered', 'Delivered'),
    ], required=True)
    channel = fields.Selection([
        ('sms', 'SMS'), ('whatsapp', 'WhatsApp'), ('email', 'Email'),
    ], default='whatsapp')
    recipient_phone = fields.Char()
    recipient_name = fields.Char()
    message = fields.Text()
    state = fields.Selection([
        ('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed'),
    ], default='pending')
    order_log_id = fields.Many2one('uva.order.log', ondelete='set null')
    fleet_delivery_id = fields.Many2one('uva.fleet.delivery', ondelete='set null')
    error = fields.Text()
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    def send_notification(self):
        """Send the notification via webhook or mark as sent in demo mode."""
        for rec in self:
            if not rec.message:
                rec.message = _NOTIFICATION_MESSAGES.get(rec.notification_type, '')
            # Check demo mode on linked store
            store = (rec.order_log_id.store_id if rec.order_log_id
                     else rec.fleet_delivery_id.carrier_id if rec.fleet_delivery_id
                     else None)
            demo = store.demo_mode if store and hasattr(store, 'demo_mode') else True
            if demo:
                rec.write({'state': 'sent'})
                rec._post_chatter()
                continue
            webhook_url = self.env['ir.config_parameter'].sudo().get_param(
                'uva.notification.webhook_url', ''
            )
            if not webhook_url:
                rec.write({'state': 'sent'})
                rec._post_chatter()
                continue
            # SSRF protection: only allow HTTPS URLs
            from urllib.parse import urlparse
            parsed = urlparse(webhook_url)
            if parsed.scheme != 'https':
                rec.write({'state': 'failed', 'error': 'Webhook URL must use HTTPS'})
                rec._post_chatter()
                continue
            try:
                resp = requests.post(webhook_url, json={
                    'type': rec.notification_type,
                    'channel': rec.channel,
                    'phone': rec.recipient_phone,
                    'name': rec.recipient_name,
                    'message': rec.message,
                }, timeout=10)
                resp.raise_for_status()
                rec.write({'state': 'sent'})
            except Exception as exc:
                rec.write({'state': 'failed', 'error': str(exc)[:500]})
            rec._post_chatter()

    def _post_chatter(self):
        """Post notification status to linked record's chatter."""
        self.ensure_one()
        body = _("📱 Notification (%(ntype)s): %(state)s",
                 ntype=self.notification_type, state=self.state)
        if self.order_log_id:
            self.order_log_id.message_post(
                body=body, message_type='notification', subtype_xmlid='mail.mt_note')
        if self.fleet_delivery_id:
            self.fleet_delivery_id.message_post(
                body=body, message_type='notification', subtype_xmlid='mail.mt_note')

    @api.model
    def _send_order_notification(self, order_log, notification_type):
        """Create and send a notification for an order event."""
        store = order_log.store_id
        if not getattr(store, 'notification_enabled', False):
            return
        payload = {}
        try:
            payload = json.loads(order_log.raw_payload or '{}')
        except Exception:
            pass
        vals = {
            'notification_type': notification_type,
            'recipient_phone': payload.get('customer_phone', ''),
            'recipient_name': payload.get('customer_name', ''),
            'order_log_id': order_log.id,
            'company_id': store.company_id.id,
        }
        notif = self.sudo().create(vals)
        notif.send_notification()
        return notif

    @api.model
    def _send_delivery_notification(self, fleet_delivery, notification_type):
        """Create and send a notification for a delivery event."""
        vals = {
            'notification_type': notification_type,
            'fleet_delivery_id': fleet_delivery.id,
            'company_id': fleet_delivery.company_id.id,
        }
        notif = self.sudo().create(vals)
        notif.send_notification()
        return notif
