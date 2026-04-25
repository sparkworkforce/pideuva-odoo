# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import json
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class UvaMenuSync(models.Model):
    _name = 'uva.menu.sync'
    _description = 'Uva Menu Sync Log'
    _order = 'create_date desc'

    store_id = fields.Many2one('uva.store.config', required=True, ondelete='cascade')
    sync_type = fields.Selection([
        ('full', 'Full Sync'),
        ('price_update', 'Price Update'),
        ('availability', 'Availability Change'),
        ('new_product', 'New Product'),
    ], required=True)
    product_id = fields.Many2one('product.product')
    state = fields.Selection([
        ('pending', 'Pending'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ], default='pending')
    error = fields.Text()
    company_id = fields.Many2one('res.company', related='store_id.company_id', store=True)

    @api.model
    def push_menu_update(self, store_config, sync_type, product=None):
        """Build payload and push menu update to Uva API."""
        mappings = self.env['uva.product.mapping'].search([
            ('store_id', '=', store_config.id),
        ]) if not product else self.env['uva.product.mapping'].search([
            ('store_id', '=', store_config.id),
            ('odoo_product_id', 'in', product.ids),
        ])
        log = self.create({
            'store_id': store_config.id,
            'sync_type': sync_type,
            'product_id': product.id if product and len(product) == 1 else False,
        })
        # Build payload using mapping IDs (not Odoo internal IDs)
        products_data = []
        for m in mappings:
            p = m.odoo_product_id
            products_data.append({
                'uva_product_id': m.uva_product_id,
                'name': p.name,
                'price': p.lst_price,
                'available': p.active,
                'category': p.categ_id.name if p.categ_id else '',
            })
        payload = {'sync_type': sync_type, 'products': products_data}
        if store_config.demo_mode:
            _logger.info("[uva:menu_sync] DEMO: %d products", len(products_data))
            log.write({'state': 'done'})
            return log
        try:
            client = self.env['uva.api.client']
            client._request('POST', '/menu/sync', store_config.sudo().api_key, json=payload)
            log.write({'state': 'done'})
        except Exception as exc:
            log.write({'state': 'failed', 'error': str(exc)})
        return log

    @api.model
    def cron_sync_all_stores(self):
        """Daily cron: full menu sync for all enabled stores."""
        stores = self.env['uva.store.config'].search([
            ('active', '=', True),
            ('menu_sync_enabled', '=', True),
        ])
        for store in stores:
            try:
                self.push_menu_update(store, 'full')
            except Exception as exc:
                _logger.error("[uva:menu_sync] cron error for store %s: %s", store.name, exc)
