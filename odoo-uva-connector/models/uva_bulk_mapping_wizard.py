# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import difflib
import json
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class UvaBulkMappingWizard(models.TransientModel):
    _name = 'uva.bulk.mapping.wizard'
    _description = 'Uva Bulk Product Mapping Wizard'

    store_id = fields.Many2one(
        'uva.store.config',
        string='Store',
        required=True,
    )
    line_ids = fields.One2many(
        'uva.bulk.mapping.wizard.line',
        'wizard_id',
        string='Unmapped Products',
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('matched', 'Matched'),
        ('done', 'Done'),
    ], default='draft', required=True)
    matched_count = fields.Integer(
        string='Matched',
        compute='_compute_counts',
    )
    total_count = fields.Integer(
        string='Total',
        compute='_compute_counts',
    )

    @api.depends('line_ids.odoo_product_id')
    def _compute_counts(self):
        for wiz in self:
            wiz.total_count = len(wiz.line_ids)
            wiz.matched_count = len(wiz.line_ids.filtered('odoo_product_id'))

    def action_load_unmapped(self):
        """Load Uva product IDs from order logs that have no mapping for this store."""
        self.ensure_one()
        self.line_ids.unlink()

        # Collect existing mapped product IDs for this store
        existing = set(
            self.env['uva.product.mapping'].search([
                ('store_id', '=', self.store_id.id),
                ('active', '=', True),
            ]).mapped('uva_product_id')
        )

        # Parse raw_payload from order logs to extract unmapped products
        logs = self.env['uva.order.log'].search([
            ('store_id', '=', self.store_id.id),
            ('raw_payload', '!=', False),
        ])

        unmapped = {}  # uva_product_id -> uva_product_name
        for log in logs:
            try:
                payload = json.loads(log.raw_payload)
            except (json.JSONDecodeError, TypeError):
                continue
            items = payload.get('items') or payload.get('products') or []
            for item in items:
                pid = str(item.get('id') or item.get('product_id') or '')
                if not pid or pid in existing or pid in unmapped:
                    continue
                name = item.get('name') or item.get('product_name') or ''
                unmapped[pid] = name

        lines = [(0, 0, {
            'uva_product_id': pid,
            'uva_product_name': name,
        }) for pid, name in unmapped.items()]

        self.write({'line_ids': lines, 'state': 'draft'})
        return self._reopen()

    def action_auto_match(self):
        """Auto-match unmatched lines by exact ilike then fuzzy name matching."""
        self.ensure_one()
        Product = self.env['product.product']
        all_products = Product.search([])
        # Build name→product map once for fuzzy matching
        name_map = {}
        for prod in all_products:
            name_map.setdefault(prod.name.lower(), []).append(prod)
        all_names = list(name_map.keys())

        Alias = self.env['uva.product.alias']
        for line in self.line_ids.filtered(lambda l: not l.odoo_product_id and l.uva_product_name):
            # 0) Alias lookup
            canonical, alias_product = Alias.resolve(line.uva_product_name)
            if alias_product:
                line.write({
                    'odoo_product_id': alias_product.id,
                    'match_confidence': 'Alias match',
                    'match_score': 100.0,
                })
                continue
            search_name = canonical  # may differ from uva_product_name if alias found
            # 1) Exact ilike match
            exact = Product.search([('name', 'ilike', search_name)], limit=2)
            if len(exact) == 1:
                line.write({
                    'odoo_product_id': exact.id,
                    'match_confidence': 'Exact match',
                    'match_score': 100.0,
                })
                continue
            # 2) Fuzzy matching using get_close_matches (optimized)
            uva_lower = search_name.lower()
            close = difflib.get_close_matches(uva_lower, all_names, n=2, cutoff=0.6)
            if not close:
                continue
            best_name = close[0]
            best_prods = name_map[best_name]
            if len(best_prods) != 1:
                continue  # ambiguous — multiple products with same name
            ratio = difflib.SequenceMatcher(None, uva_lower, best_name).ratio()
            # Respect store confidence threshold for auto-apply
            threshold = self.store_id.mapping_confidence_threshold or 90.0
            score = round(ratio * 100, 1)
            # Accept if only one close match, or if best is significantly better than runner-up
            if len(close) == 1 or (len(close) > 1 and ratio - difflib.SequenceMatcher(None, uva_lower, close[1]).ratio() >= 0.10):
                line.write({
                    'odoo_product_id': best_prods[0].id if score >= threshold else False,
                    'match_confidence': f'Fuzzy ({ratio:.0%})',
                    'match_score': score,
                })
        self.write({'state': 'matched'})
        return self._reopen()

    def action_apply(self):
        """Create uva.product.mapping records for all matched lines."""
        self.ensure_one()
        Mapping = self.env['uva.product.mapping']
        created = 0
        for line in self.line_ids.filtered(lambda l: l.odoo_product_id and l.uva_product_id):
            # Skip if mapping already exists
            if Mapping.search_count([
                ('uva_product_id', '=', line.uva_product_id),
                ('store_id', '=', self.store_id.id),
            ]):
                continue
            Mapping.create({
                'uva_product_id': line.uva_product_id,
                'odoo_product_id': line.odoo_product_id.id,
                'store_id': self.store_id.id,
            })
            created += 1
        self.write({'state': 'done'})
        return self._reopen()

    def action_reset(self):
        """Clear lines and reset to draft."""
        self.ensure_one()
        self.line_ids.unlink()
        self.write({'state': 'draft'})
        return self._reopen()

    def _reopen(self):
        """Return action to re-open this wizard (keeps target=new)."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }


class UvaBulkMappingWizardLine(models.TransientModel):
    _name = 'uva.bulk.mapping.wizard.line'
    _description = 'Uva Bulk Mapping Wizard Line'

    wizard_id = fields.Many2one(
        'uva.bulk.mapping.wizard',
        ondelete='cascade',
        required=True,
    )
    uva_product_id = fields.Char(string='Uva Product ID', required=True)
    uva_product_name = fields.Char(string='Uva Product Name')
    odoo_product_id = fields.Many2one('product.product', string='Odoo Product')
    match_confidence = fields.Char(string='Match Confidence')
    match_score = fields.Float(string='Match Score (%)')
    is_mapped = fields.Boolean(compute='_compute_is_mapped')

    @api.depends('odoo_product_id')
    def _compute_is_mapped(self):
        for line in self:
            line.is_mapped = bool(line.odoo_product_id)
