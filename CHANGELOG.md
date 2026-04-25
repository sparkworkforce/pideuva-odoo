# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/) within Odoo's versioning scheme (`ODOO_VERSION.MODULE_VERSION`).

## [18.0.2.0.0] - 2026-04-25

### Added

- Order routing rules — auto-assign incoming orders to stores by configurable criteria
- Auto-accept orders via configurable cron job
- Order modification support (item changes after acceptance)
- Product name aliases for auto-mapping (`uva.product.alias` model)
- Bulk product mapping wizard for batch Uva-to-Odoo product linking
- Prep time tracking per incoming order
- Revenue attribution for Uva-sourced orders (`sale.order` extension)
- Store hours enforcement on incoming orders
- First-install setup wizard for guided onboarding
- Customer notifications model with email and browser push support
- Menu synchronization with Uva platform via cron
- Multi-language customer tracking page (controller + template)
- Delivery proof capture on fleet deliveries
- Delivery zone map with OWL zone widget
- Dedicated Uva orders screen in POS (`uva_pos_screen`)
- POS health indicator showing API connectivity status
- POS error orders panel for failed/problematic orders
- POS offline resilience — continues working when API is down
- Bus compatibility layer for cross-version real-time notifications
- OWL dashboard action component with per-store metrics
- OWL map field widget for delivery zone configuration
- Analytics views for order and delivery data
- Revenue reporting views
- Onboarding views for first-time configuration
- Performance alerts cron job
- Health check cron job
- Menu sync cron job
- `pos.order` extension model
- `sale.order` extension with `uva_order_ref` field
- Documentation: admin guide, staff guide, troubleshooting, API compatibility, launch checklist
- Spanish translation (`es.po`)
- 13 test suites covering API client, retry queue, store config, orders, webhooks, fleet, security, setup wizard, bulk mapping, health check, and end-to-end mock
- CI workflow (`.github/workflows/ci.yml`)
- Release and deploy scripts (`release.sh`, `deploy.sh`)

### Changed

- API client now includes SSRF protection on all outbound requests
- Retry queue upgraded with exponential backoff strategy
- Store configuration expanded with store hours, delivery zones, and zone geometry
- Fleet delivery model extended with proof-of-delivery fields
- Order log model expanded with prep time and modification tracking
- Module dependencies updated: added `sale` alongside existing `point_of_sale`, `delivery`, `sale_stock`, `mail`, `base_setup`
- Module structure grown from 11 models to 20 models, 2 controllers to 3, 1 OWL component to 9
- Cron jobs expanded from 3 (polling, retry, purge) to 8 (added auto-accept, health check, menu sync, performance alerts)
- Security rules expanded to 8 multi-company `ir.rule` records

### Security

- HMAC webhook signature validation on both order and fleet endpoints
- Rate limiting on all webhook endpoints
- XSS prevention in customer tracking pages
- SSRF protection in API client (blocks private/internal IP ranges)
- API credentials restricted to `base.group_system`
- Access control entries for all 20 models in `ir.model.access.csv`

## [18.0.1.0.0] - 2025-04-05

### Added

- Flow A: Incoming Uva orders via webhook and polling
- Flow B: Outbound delivery via Uva Fleet (`delivery.carrier` integration)
- Real-time POS notifications via `bus.bus`
- Uva API client with automatic retry queue
- Store configuration and product mapping models
- Delivery cost estimation wizard
- Webhook signature validation for order and fleet endpoints
- Chatter integration for order and delivery tracking
- Cron jobs: order polling, fleet polling, retry processing, payload purge
- Multi-version support: Odoo 17, 18, 19 via `build.sh`
- OWL-based POS order popup component
