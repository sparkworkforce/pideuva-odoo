# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class UvaSetupWizard(models.TransientModel):
    _name = 'uva.setup.wizard'
    _description = 'Uva Connector Setup Wizard'

    step = fields.Selection([
        ('credentials', 'Credentials'),
        ('store', 'Store'),
        ('done', 'Done'),
    ], default='credentials', required=True)

    # Step 1 — Credentials
    demo_mode = fields.Boolean(string='Demo Mode', default=True)
    api_key = fields.Char(string='API Key', groups='base.group_system')
    webhook_secret = fields.Char(string='Webhook Secret', groups='base.group_system')

    # Step 2 — Store
    name = fields.Char(string='Store Name')
    pos_config_id = fields.Many2one('pos.config', string='POS Configuration')

    # Result
    store_config_id = fields.Many2one(
        'uva.store.config', string='Store Configuration', readonly=True,
    )

    def action_next(self):
        self.ensure_one()
        if self.step == 'credentials':
            if not self.demo_mode:
                if not self.api_key:
                    raise UserError(_("API Key is required when Demo Mode is disabled."))
                if not self.webhook_secret:
                    raise UserError(_("Webhook Secret is required when Demo Mode is disabled."))
            self.step = 'store'
        elif self.step == 'store':
            if not self.name:
                raise UserError(_("Store Name is required."))
            if not self.pos_config_id:
                raise UserError(_("POS Configuration is required."))
            vals = {
                'name': self.name,
                'pos_config_id': self.pos_config_id.id,
                'demo_mode': self.demo_mode,
                'api_key': self.api_key if not self.demo_mode else False,
                'webhook_secret': self.webhook_secret if not self.demo_mode else False,
            }
            if self.store_config_id:
                self.store_config_id.write(vals)
            else:
                self.store_config_id = self.env['uva.store.config'].create(vals)
            self.step = 'done'
        return self._reopen()

    def action_prev(self):
        self.ensure_one()
        if self.step == 'store':
            self.step = 'credentials'
        elif self.step == 'done':
            self.step = 'store'
        return self._reopen()

    def action_test_connection(self):
        """Test connection using store config or API client directly."""
        self.ensure_one()
        if self.demo_mode:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Connection Test"),
                    'message': _("Connection successful (demo mode)."),
                    'type': 'success',
                    'sticky': False,
                    'next': self._reopen(),
                },
            }
        if self.store_config_id:
            return self.store_config_id.action_test_connection()
        # No store yet — test directly via API client
        if not self.sudo().api_key:
            raise UserError(_("API Key is required to test the connection."))
        client = self.env['uva.api.client']
        try:
            client.health_check(api_key=self.sudo().api_key)
        except Exception as exc:
            raise UserError(_('Connection failed: %s', str(exc))) from exc
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Connection Test"),
                'message': _("Connection successful!"),
                'type': 'success',
                'sticky': False,
                'next': self._reopen(),
            },
        }

    def action_done(self):
        return {'type': 'ir.actions.act_window_close'}

    def action_open_product_mapping(self):
        self.ensure_one()
        if not self.store_config_id:
            raise UserError(_('Complete the setup wizard first.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _("Product Mappings"),
            'res_model': 'uva.product.mapping',
            'view_mode': 'list,form',
            'domain': [('store_id', '=', self.store_config_id.id)],
            'context': {'default_store_id': self.store_config_id.id},
        }

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
