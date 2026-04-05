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
        """Bulk import product mappings from the Uva product catalog.

        # TODO(uva-api): implement once Uva product catalog endpoint is confirmed.
        """
        raise NotImplementedError(
            "TODO(uva-api): bulk import from Uva product catalog not yet implemented. "
            "Implement once Uva API docs are received."
        )
