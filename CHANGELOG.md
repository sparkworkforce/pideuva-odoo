# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/) within Odoo's versioning scheme (`ODOO_VERSION.MODULE_VERSION`).

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
