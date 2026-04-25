# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class UvaOrderRule(models.Model):
    _name = 'uva.order.rule'
    _description = 'Uva Order Routing Rule'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    store_id = fields.Many2one('uva.store.config', required=True, ondelete='cascade')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', related='store_id.company_id', store=True)

    condition_type = fields.Selection([
        ('amount_min', 'Minimum Amount'),
        ('amount_max', 'Maximum Amount'),
        ('time_after', 'After Time (HH:MM)'),
        ('time_before', 'Before Time (HH:MM)'),
        ('product_category', 'Product Category Contains'),
    ], required=True)
    condition_value = fields.Char(required=True)

    action_type = fields.Selection([
        ('route_pos', 'Route to POS'),
        ('auto_accept', 'Auto-Accept'),
        ('auto_reject', 'Auto-Reject'),
    ], required=True)
    target_pos_config_id = fields.Many2one('pos.config', string='Target POS')

    @api.constrains('condition_type', 'condition_value')
    def _check_condition_value(self):
        import re
        for rec in self:
            val = rec.condition_value
            if rec.condition_type in ('amount_min', 'amount_max'):
                try:
                    v = float(val)
                    if v < 0:
                        raise ValueError
                except (ValueError, TypeError):
                    raise models.ValidationError(
                        _("Condition value must be a non-negative number for amount rules. Got: '%s'", val)
                    )
            elif rec.condition_type in ('time_after', 'time_before'):
                if not re.match(r'^([01]\d|2[0-3]):[0-5]\d$', val or ''):
                    raise models.ValidationError(
                        _("Condition value must be in HH:MM format (00:00-23:59). Got: '%s'", val)
                    )

    def evaluate(self, raw_order, store_config):
        """Return True if this rule matches the given order."""
        self.ensure_one()
        items = raw_order.get('items', [])
        total = sum(
            float(i.get('price', 0)) * float(i.get('quantity', 1))
            for i in items
        )
        val = self.condition_value
        try:
            if self.condition_type == 'amount_min':
                return total >= float(val)
            if self.condition_type == 'amount_max':
                return total <= float(val)
            if self.condition_type == 'time_after':
                from pytz import timezone as pytz_tz
                tz = pytz_tz(store_config.company_id.partner_id.tz or 'America/Puerto_Rico')
                now_hm = fields.Datetime.now().replace(tzinfo=pytz_tz('UTC')).astimezone(tz).strftime('%H:%M')
                return now_hm >= val
            if self.condition_type == 'time_before':
                from pytz import timezone as pytz_tz
                tz = pytz_tz(store_config.company_id.partner_id.tz or 'America/Puerto_Rico')
                now_hm = fields.Datetime.now().replace(tzinfo=pytz_tz('UTC')).astimezone(tz).strftime('%H:%M')
                return now_hm < val
            if self.condition_type == 'product_category':
                return any(
                    val.lower() in (i.get('category', '') or '').lower()
                    for i in items
                )
        except (ValueError, TypeError):
            _logger.warning("Rule %s: invalid condition_value '%s'", self.name, val)
        return False

    @api.model
    def apply_rules(self, raw_order, store_config):
        """Return first matching rule's action dict or None."""
        rules = self.search([
            ('store_id', '=', store_config.id),
            ('active', '=', True),
        ], order='sequence, id')
        for rule in rules:
            if rule.evaluate(raw_order, store_config):
                return {
                    'action_type': rule.action_type,
                    'target_pos_config_id': rule.target_pos_config_id.id if rule.target_pos_config_id else False,
                }
        return None
