# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
# Odoo 18 manifest. Copied to __manifest__.py by build.sh before packaging.
{
    'name': 'Uva PR Connector',
    'version': '18.0.1.0.0',
    'summary': 'Integrate Odoo POS and delivery with Uva PR (pideuva.com)',
    'description': """
Uva PR Connector
================
Integrates Odoo with Uva PR (pideuva.com), Puerto Rico's local delivery platform.

**Flow A — Incoming Orders (POS Connector)**
Uva customer orders appear automatically in the Odoo POS interface.
Staff can accept, reject, or mark items as unavailable directly from POS.

**Flow B — Outbound Delivery (Uva Fleet)**
Dispatch deliveries via Uva Fleet drivers from sale orders or stock pickings.
Real-time tracking status posted to Odoo chatters.

Supports Odoo 17, 18, and 19 Enterprise.
    """,
    'author': 'Spark Workforce LLC',
    'website': 'https://sparkworkforce.com',
    'support': 'info+fleet@pideuva.com',
    'license': 'OPL-1',
    'category': 'Point of Sale',
    'depends': [
        'point_of_sale',
        'delivery',
        'sale_stock',
        'mail',
        'base_setup',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/ir_rule.xml',
        'data/uva_cron_retry_processor.xml',
        'data/uva_cron_order_polling.xml',
        'data/uva_cron_fleet_polling.xml',
        'data/uva_cron_purge_raw_payload.xml',
        'data/uva_cron_health_check.xml',
        'views/uva_store_config_views.xml',
        'views/uva_product_mapping_views.xml',
        'views/uva_product_alias_views.xml',
        'views/uva_order_log_views.xml',
        'views/uva_fleet_delivery_views.xml',
        'views/uva_fleet_estimate_wizard_views.xml',
        'views/uva_bulk_mapping_wizard_views.xml',
        'views/uva_setup_wizard_views.xml',
        'views/res_config_settings_views.xml',
        'views/uva_dashboard_views.xml',
        'views/uva_analytics_views.xml',
        'views/uva_tracking_template.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'odoo_uva_connector/static/src/css/uva_pos_order_popup.css',
            'odoo_uva_connector/static/src/js/uva_bus_compat.js',
            'odoo_uva_connector/static/src/js/uva_pos_order_popup.js',
            'odoo_uva_connector/static/src/js/uva_pos_screen.js',
            'odoo_uva_connector/static/src/js/uva_pos_health_indicator.js',
            'odoo_uva_connector/static/src/xml/uva_pos_order_popup.xml',
            'odoo_uva_connector/static/src/xml/uva_pos_screen.xml',
            'odoo_uva_connector/static/src/xml/uva_pos_health_indicator.xml',
        ],
        'web.assets_backend': [
            'odoo_uva_connector/static/src/js/uva_map_field.js',
            'odoo_uva_connector/static/src/xml/uva_map_field.xml',
            'odoo_uva_connector/static/src/js/uva_dashboard_action.js',
            'odoo_uva_connector/static/src/xml/uva_dashboard_action.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
