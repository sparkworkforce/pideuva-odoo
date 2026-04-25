# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # ------------------------------------------------------------------
    # Uva Fleet (Flow B) company-level settings
    # Stored via ir.config_parameter — no DB column on res.config.settings
    # ------------------------------------------------------------------

    uva_fleet_api_key = fields.Char(
        string='Uva Fleet API Key',
        config_parameter='uva.fleet.api_key',
        groups='base.group_system',
        help='API key for the Uva Fleet delivery service. System admins only.',
    )
    uva_fleet_webhook_secret = fields.Char(
        string='Uva Fleet Webhook Secret',
        config_parameter='uva.fleet.webhook_secret',
        groups='base.group_system',
        help='Shared secret for validating Uva Fleet webhook signatures. System admins only.',
    )
    uva_fleet_demo_mode = fields.Boolean(
        string='Uva Fleet Demo Mode',
        config_parameter='uva.fleet.demo_mode',
        default=True,
        help='When enabled, Uva Fleet API calls return mock responses. '
             'Enabled by default — disable only after configuring real API credentials.',
    )

    # ------------------------------------------------------------------
    # Onboarding
    # ------------------------------------------------------------------

    uva_onboarding_state = fields.Selection(
        [('not_done', 'Not Done'), ('just_done', 'Just Done'), ('done', 'Done'), ('closed', 'Closed')],
        string='Uva Onboarding State',
        config_parameter='uva.onboarding_state',
        default='not_done',
    )
    uva_setup_complete = fields.Boolean(
        string='Uva Setup Complete',
        compute='_compute_uva_setup_complete',
    )

    @api.depends('uva_fleet_api_key', 'uva_fleet_demo_mode')
    def _compute_uva_setup_complete(self):
        for rec in self:
            has_creds = bool(rec.uva_fleet_api_key) or rec.uva_fleet_demo_mode
            has_store = bool(self.env['uva.store.config'].search_count([('active', '=', True)]))
            has_mapping = bool(self.env['uva.product.mapping'].search_count([('active', '=', True)]))
            rec.uva_setup_complete = has_creds and has_store and has_mapping

    def action_close_uva_onboarding(self):
        self.env['ir.config_parameter'].sudo().set_param('uva.onboarding_state', 'closed')
