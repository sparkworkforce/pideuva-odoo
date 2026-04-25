# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class UvaProductMapping(models.Model):
    _name = 'uva.product.mapping'
    _description = 'Uva PR Product Mapping'
    _order = 'store_id, uva_product_id'

    # ------------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------------

    uva_product_id = fields.Char(
        string='Uva Product ID',
        required=True,
        index=True,
        help='The product identifier as sent by Uva in order payloads.',
    )
    company_id = fields.Many2one(
        'res.company', related='store_id.company_id', store=True,
    )
    odoo_product_id = fields.Many2one(
        'product.product',
        string='Odoo Product',
        required=True,
        ondelete='restrict',
        help='The Odoo product this Uva product ID maps to.',
    )
    store_id = fields.Many2one(
        'uva.store.config',
        string='Store',
        required=True,
        ondelete='cascade',
        help='The store this mapping belongs to. '
             'Different stores can map the same Uva product ID to different Odoo products.',
    )
    active = fields.Boolean(default=True)

    # Convenience display
    odoo_product_name = fields.Char(
        related='odoo_product_id.display_name',
        string='Odoo Product Name',
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    _sql_constraints = [
        ('uva_product_store_unique', 'UNIQUE(uva_product_id, store_id)',
         'A Uva product ID can only be mapped once per store. '
         'Different stores may map the same Uva product ID to different Odoo products.'),
    ]

    # ------------------------------------------------------------------
    # Business methods
    # ------------------------------------------------------------------

    def get_odoo_product(self, uva_product_id, store_id):
        """Return the mapped Odoo product for a given Uva product ID and store.

        Returns the product.product record if a mapping exists, or None if not found.
        Does NOT raise — callers (Unit 3 _validate_product_mappings) handle the
        None case by placing the order in PENDING state.
        """
        mapping = self.search([
            ('uva_product_id', '=', uva_product_id),
            ('store_id', '=', store_id),
            ('active', '=', True),
        ], limit=1)
        return mapping.odoo_product_id if mapping else None

    # ------------------------------------------------------------------
    # Import stub (future bulk import from Uva catalog)
    # ------------------------------------------------------------------

    def action_import_from_uva(self):
        """Fetch Uva product catalog and open bulk mapping wizard for review."""
        store_id = self.env.context.get('default_store_id') or (self[:1].store_id.id if self else False)
        if not store_id:
            from odoo.exceptions import UserError
            raise UserError("Please select a store first.")
        store_rec = self.env['uva.store.config'].browse(store_id)
        client = self.env['uva.api.client']
        try:
            products = client.get_products(
                api_key=store_rec.sudo().api_key,
                store_id=str(store_rec.id),
                demo_mode=store_rec.demo_mode,
            )
        except Exception as exc:
            from odoo.exceptions import UserError
            raise UserError(f"Failed to fetch products from Uva: {exc}") from exc

        if not products:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Import',
                    'message': 'No products returned from Uva API.',
                    'type': 'warning',
                },
            }

        # Open bulk mapping wizard pre-populated with fetched products
        wizard = self.env['uva.bulk.mapping.wizard'].create({'store_id': store_id})
        lines = []
        for prod in products:
            uva_id = str(prod.get('id') or prod.get('product_id') or '')
            if not uva_id:
                continue
            name = prod.get('name') or prod.get('product_name') or ''
            lines.append((0, 0, {
                'uva_product_id': uva_id,
                'uva_product_name': name,
            }))
        if lines:
            wizard.write({'line_ids': lines})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'uva.bulk.mapping.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }
