# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
from odoo import fields, models


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
