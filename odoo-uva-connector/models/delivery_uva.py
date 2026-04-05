# License: OPL-1 (https://www.odoo.com/documentation/17.0/legal/licenses.html)
import json
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from .uva_api_client import UvaApiError, UvaCoverageError

_logger = logging.getLogger(__name__)


class DeliveryUva(models.Model):
    """Extends delivery.carrier to add Uva Fleet as a carrier option.

    delivery.carrier API is stable across Odoo 17/18/19 — no shims needed (NFR-01.4).
    """
    _inherit = 'delivery.carrier'

    delivery_type = fields.Selection(
        selection_add=[('uva', 'Uva Fleet')],
        ondelete={'uva': 'set default'},
    )

    # ------------------------------------------------------------------
    # Credential helpers — read from ir.config_parameter (res.config.settings)
    # ------------------------------------------------------------------

    def _get_fleet_credentials(self):
        """Return (api_key, demo_mode) from ir.config_parameter."""
        ICP = self.env['ir.config_parameter'].sudo()
        api_key = ICP.get_param('uva.fleet.api_key', '')
        demo_mode_raw = ICP.get_param('uva.fleet.demo_mode', 'False')
        demo_mode = demo_mode_raw in ('True', '1', 'true')
        return api_key, demo_mode

    def _get_fleet_api_client(self):
        """Return the uva.api.client AbstractModel."""
        return self.env['uva.api.client']

    def _get_store_id_for_retry(self):
        """Return a store_id for the retry queue.

        Flow B uses company-level credentials, not per-store. The retry queue
        requires a store_id for credential lookup. We use the first active store
        config as a proxy — this is a known limitation.
        # TODO: consider adding a company-level config model for Flow B in a future version.
        """
        store = self.env['uva.store.config'].search(
            [('active', '=', True)], limit=1
        )
        return store.id if store else False

    # ------------------------------------------------------------------
    # Odoo delivery.carrier API methods
    # ------------------------------------------------------------------

    def uva_get_shipping_price(self, orders):
        """Return cost estimate for display in the carrier wizard.

        Called by Odoo before the merchant confirms dispatch.
        Returns a list of price dicts: [{'name': str, 'price': float, 'currency': str}]
        """
        self.ensure_one()
        api_key, demo_mode = self._get_fleet_credentials()
        client = self._get_fleet_api_client()

        prices = []
        for order in orders:
            pickup = self._get_pickup_address(order)
            destination = self._get_destination_address(order)
            try:
                estimate = client.get_delivery_estimate(
                    api_key=api_key,
                    pickup=pickup,
                    destination=destination,
                    demo_mode=demo_mode,
                )
                prices.append({
                    'name': self.name,
                    'price': estimate.get('amount', 0.0),
                    'currency': estimate.get('currency', 'USD'),
                    'eta_minutes': estimate.get('eta_minutes', 0),
                })
            except UvaCoverageError as exc:
                raise UserError(_(
                    "Uva Fleet does not cover the delivery address for order %(order)s.\n"
                    "Destination: %(dest)s\n"
                    "Please verify the zip code is within Uva's service area "
                    "(San Juan, Dorado, Caguas and surrounding areas).\n\n"
                    "Details: %(detail)s",
                    order=order.name,
                    dest=destination.get('zip', 'unknown'),
                    detail=str(exc),
                )) from exc
            except UvaApiError as exc:
                raise UserError(_(
                    "Could not retrieve Uva Fleet estimate for order %(order)s: %(error)s\n"
                    "Please try again or contact Uva Fleet support.",
                    order=order.name,
                    error=str(exc),
                )) from exc
        return prices

    def uva_send_shipping(self, pickings):
        """Create a Uva Fleet delivery for each picking.

        Called by Odoo after the merchant confirms dispatch.
        Returns a list of tracking dicts per Odoo carrier API contract.
        """
        self.ensure_one()
        api_key, demo_mode = self._get_fleet_credentials()
        client = self._get_fleet_api_client()
        results = []

        for picking in pickings:
            pickup = self._get_pickup_address_from_picking(picking)
            destination = self._get_destination_address_from_picking(picking)

            # Step 1: Get estimate (for cost recording — merchant already confirmed)
            estimated_cost = 0.0
            try:
                estimate = client.get_delivery_estimate(
                    api_key=api_key,
                    pickup=pickup,
                    destination=destination,
                    demo_mode=demo_mode,
                )
                estimated_cost = estimate.get('amount', 0.0)
            except (UvaApiError, UvaCoverageError):
                pass  # estimate failure is non-blocking at dispatch time

            # Step 2: Create delivery
            try:
                result = client.create_delivery(
                    api_key=api_key,
                    pickup=pickup,
                    destination=destination,
                    reference=picking.name,
                    demo_mode=demo_mode,
                )
            except UvaCoverageError as exc:
                raise UserError(_(
                    "Uva Fleet cannot dispatch to this address — outside service coverage area.\n"
                    "Destination zip code: %(zip)s\n"
                    "Uva Fleet covers: San Juan, Dorado, Caguas and surrounding PR areas.\n\n"
                    "Please correct the delivery address and try again.\n"
                    "Details: %(detail)s",
                    zip=destination.get('zip', 'unknown'),
                    detail=str(exc),
                )) from exc
            except UvaApiError as exc:
                # Transient failure — enqueue retry (FR-10)
                store_id = self._get_store_id_for_retry()
                if store_id:
                    self.env['uva.api.retry.queue'].enqueue(
                        action_type='create_fleet_delivery',
                        payload=json.dumps({
                            'pickup': pickup,
                            'destination': destination,
                            'reference': picking.name,
                        }),
                        res_model='stock.picking',
                        res_id=picking.id,
                        store_id=store_id,
                        error=str(exc),
                    )
                else:
                    # No store config found — cannot enqueue retry; notify merchant via chatter
                    _logger.error(
                        "[uva:%s] uva_send_shipping: no active store config for retry queue — "
                        "dispatch failure will NOT be retried automatically",
                        picking.name,
                    )
                    if hasattr(picking, 'message_post'):
                        picking.message_post(
                            body=_(
                                "⚠️ Uva Fleet dispatch failed and could not be queued for retry "
                                "(no active Uva store configuration found).\n"
                                "Error: %(error)s\nPlease retry manually.",
                                error=str(exc),
                            ),
                            message_type='notification',
                            subtype_xmlid='mail.mt_note',
                        )
                raise UserError(_(
                    "Uva Fleet dispatch failed for %(picking)s: %(error)s\n"
                    "The request has been queued for automatic retry.",
                    picking=picking.name,
                    error=str(exc),
                )) from exc

            # Step 3: Create uva.fleet.delivery tracking record
            delivery_id = result.get('delivery_id', '')
            tracking_url = result.get('tracking_url', '')
            fleet_delivery = self.env['uva.fleet.delivery'].create({
                'uva_delivery_id': delivery_id,
                'carrier_id': self.id,
                'picking_id': picking.id,
                'sale_order_id': picking.sale_id.id if picking.sale_id else False,
                'company_id': picking.company_id.id,
                'estimated_cost': estimated_cost,
                'tracking_url': tracking_url,
                'state': 'pending',
            })
            _logger.info(
                "uva_send_shipping: created fleet delivery %s for picking %s",
                fleet_delivery.uva_delivery_id, picking.name,
            )

            results.append({
                'exact_price': estimated_cost,
                'tracking_number': delivery_id,
                'tracking_url': tracking_url,
            })

        return results

    def uva_cancel_shipping(self, picking):
        """Cancel a Uva Fleet delivery.

        Called by Odoo when the merchant cancels a dispatched delivery.
        Returns a dict with success/error info.
        """
        self.ensure_one()
        api_key, demo_mode = self._get_fleet_credentials()
        client = self._get_fleet_api_client()

        fleet_delivery = self.env['uva.fleet.delivery'].search([
            ('picking_id', '=', picking.id),
            ('state', 'not in', ['cancelled', 'delivered', 'failed']),
        ], limit=1)

        if not fleet_delivery:
            raise UserError(_(
                "No active Uva Fleet delivery found for %(picking)s.",
                picking=picking.name,
            ))

        try:
            client.cancel_delivery(
                api_key=api_key,
                delivery_id=fleet_delivery.uva_delivery_id,
                demo_mode=demo_mode,
            )
            fleet_delivery.write({'state': 'cancelled'})
            fleet_delivery.message_post(
                body=_("Delivery cancelled by merchant from Odoo."),
                message_type='notification',
                subtype_xmlid='mail.mt_note',
            )
            return {'success': True}

        except UvaCoverageError as exc:
            # Coverage error on cancel is unusual but handle gracefully
            raise UserError(_(
                "Uva Fleet rejected the cancellation: %(detail)s",
                detail=str(exc),
            )) from exc

        except UvaApiError as exc:
            # Transient failure — enqueue retry
            store_id = self._get_store_id_for_retry()
            if store_id:
                self.env['uva.api.retry.queue'].enqueue(
                    action_type='cancel_fleet_delivery',
                    payload=json.dumps({
                        'delivery_id': fleet_delivery.uva_delivery_id,
                    }),
                    res_model='stock.picking',
                    res_id=picking.id,
                    store_id=store_id,
                    error=str(exc),
                )
            raise UserError(_(
                "Uva Fleet cancellation failed for %(picking)s: %(error)s\n"
                "The request has been queued for automatic retry.",
                picking=picking.name,
                error=str(exc),
            )) from exc

    # ------------------------------------------------------------------
    # Address helpers
    # ------------------------------------------------------------------

    def _get_pickup_address(self, order):
        """Extract pickup address from a sale.order."""
        warehouse = order.warehouse_id
        partner = warehouse.partner_id if warehouse.id else self.env.company.partner_id
        return self._partner_to_address(partner)

    def _get_destination_address(self, order):
        """Extract destination address from a sale.order."""
        return self._partner_to_address(order.partner_shipping_id or order.partner_id)

    def _get_pickup_address_from_picking(self, picking):
        """Extract pickup address from a stock.picking."""
        partner = picking.picking_type_id.warehouse_id.partner_id
        if not partner:
            partner = self.env.company.partner_id
        return self._partner_to_address(partner)

    def _get_destination_address_from_picking(self, picking):
        """Extract destination address from a stock.picking."""
        return self._partner_to_address(picking.partner_id)

    def _partner_to_address(self, partner):
        """Convert a res.partner to the address dict expected by Uva Fleet API."""
        return {
            'name': partner.name or '',
            'street': partner.street or '',
            'street2': partner.street2 or '',
            'city': partner.city or '',
            'zip': partner.zip or '',
            'state': partner.state_id.code if partner.state_id else '',
            'country': partner.country_id.code if partner.country_id else 'PR',
            'phone': partner.phone or partner.mobile or '',
            # TODO(uva-api): confirm exact address field names expected by Uva Fleet API
        }
