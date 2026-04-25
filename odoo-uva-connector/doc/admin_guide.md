# Uva PR Connector — Administrator Guide

## Installation

1. Clone the repository into your Odoo addons path:
   ```bash
   git clone https://github.com/sparkworkforce/odoo-uva-connector.git
   ```

2. Build for your Odoo version:
   ```bash
   cd odoo-uva-connector
   ./build.sh 18   # or 17, 19
   ```
   This copies the correct version manifest to `__manifest__.py`.

3. In Odoo, go to **Apps → Update Apps List**, then search for **Uva PR Connector** and click **Install**.

---

## Initial Setup

After installation, a setup wizard guides you through configuration:

1. Go to **Point of Sale → Configuration → Uva Setup Wizard** (or find it under **Settings → Uva Fleet** for Fleet credentials).
2. **Step 1 — Credentials**:
   - **Demo Mode**: Enabled by default. Leave on to explore the module without real API keys.
   - **API Key**: Your Uva Orders API key (required when demo mode is off).
   - **Webhook Secret**: Shared secret for HMAC validation (required when demo mode is off).
3. **Step 2 — Store**:
   - **Store Name**: A label for this store (e.g., "Main Store").
   - **POS Configuration**: Select which POS session receives Uva orders.
4. Click **Done**. Your store configuration is created.

> **Tip**: You can run the wizard again to add more stores. Each POS configuration can only be linked to one Uva store.

---

## Sandbox Mode

Each store can be toggled into sandbox mode independently, allowing you to test API integration against Uva's sandbox environment without affecting production orders.

1. Go to **Uva → Store Configuration** and open the store.
2. Enable the **Sandbox Mode** toggle.
3. Save.

When sandbox mode is active:
- API calls are routed to `https://sandbox.pideuva.com/v1` instead of production.
- Orders received in sandbox mode are tagged accordingly.
- Sandbox and production can run side-by-side on different stores.

> **Note**: Sandbox mode is per-store, not global. You can have one store in sandbox and another in production simultaneously.

---

## Product Mapping

Uva products must be mapped to Odoo products before orders can be processed.

### Manual Mapping

1. Go to **Uva → Product Mapping**.
2. Click **New**.
3. Select the **Store**, enter the **Uva Product ID**, and choose the matching **Odoo Product**.
4. Save.

### Bulk Mapping Wizard

1. Go to **Uva → Product Mapping** and click **Bulk Map** (or find it under **Uva → Bulk Mapping Wizard**).
2. The wizard fetches unmapped Uva product IDs from recent orders.
3. For each Uva product, select the corresponding Odoo product from the dropdown.
4. Click **Apply** to create all mappings at once.

### Auto-Mapping

The module can automatically map Uva products to Odoo products when names match exactly. Auto-mapping runs during order processing — if an unmapped Uva product name matches an existing Odoo product name, the mapping is created automatically.

> **Note**: Orders with unmapped products that cannot be auto-mapped will stay in **Awaiting Mapping** state until mappings are added manually.

---

## Menu Sync

Menu sync pushes your Odoo product catalog to Uva so your menu stays up to date without manual entry on the Uva side.

### Configuration

1. Go to **Uva → Store Configuration** and open the store.
2. Enable **Menu Sync** (`menu_sync_enabled`).
3. Save.

### Manual Sync

Click the **Sync Menu** button on the store configuration form to push the current product catalog to Uva immediately.

### Automatic Sync

A daily cron job (**Uva PR: Daily Menu Sync**) automatically syncs menus for all stores that have menu sync enabled.

### Sync Log

Check **Uva → Menu Sync Log** to review sync history, including any products that failed to sync and the reason.

---

## Store Hours

Control when your store accepts Uva orders by configuring operating hours.

1. Go to **Uva → Store Configuration** and open the store.
2. Enable **Store Hours** (`store_hours_enabled`).
3. Set **Opening Time** and **Closing Time**.
4. Save.

When store hours are enabled:
- Orders arriving outside the configured hours are not delivered to POS.
- The polling cron respects store hours and skips closed stores.
- POS staff will not receive order popups outside operating hours.

---

## Order Routing Rules

Order routing rules let you automatically route incoming orders based on conditions such as order total, product category, or delivery zone.

1. Go to **Uva → Configuration → Order Rules**.
2. Click **New** to create a rule.
3. Define conditions (e.g., order total > $50, specific product categories, delivery zone).
4. Set the action (e.g., route to a specific POS, auto-accept, flag for review).
5. Set the **priority** (lower number = higher priority). Rules are evaluated in priority order; the first match wins.
6. Save.

> **Tip**: Use routing rules to send high-value orders to a dedicated POS station or auto-accept orders from trusted zones.

---

## Customer Notifications

Enable customer notifications to let Uva send order status updates (accepted, preparing, ready) to customers on your behalf.

1. Go to **Uva → Store Configuration** and open the store.
2. Enable **Customer Notifications** (`notification_enabled`).
3. Save.

When enabled, status changes triggered by POS staff actions (accept, start preparing, ready) are relayed to Uva, which notifies the customer.

---

## Performance Alerts

Monitor store performance and get alerted when acceptance rates or response times drop below thresholds.

1. Go to **Uva → Store Configuration** and open the store.
2. Enable **Performance Alerts** (`alert_enabled`).
3. Set the **Acceptance Threshold** (`alert_acceptance_threshold`) — the minimum acceptable order acceptance rate (e.g., 90%).
4. Save.

When the acceptance rate falls below the threshold, the module:
- Posts a warning in the store's chatter.
- Creates a to-do activity for the responsible user.

---

## Delivery Zone Map

Each store configuration includes a **delivery zone map widget** that visually displays the store's Uva delivery coverage area.

1. Go to **Uva → Store Configuration** and open the store.
2. The map widget is displayed on the form, showing the delivery zone boundaries.

This is read-only and reflects the coverage data from Uva's API.

---

## Revenue Attribution

Track revenue generated through Uva orders with the built-in analytics view.

1. Go to **Uva → Analytics → Revenue**.
2. View revenue breakdowns by store, time period, and order source.
3. Use filters and groupings to analyze Uva-sourced revenue versus other channels.

---

## Multi-Company Setup

The module supports Odoo multi-company environments. Each store configuration is linked to a specific company via the `company_id` field.

1. Go to **Uva → Store Configuration** and open or create a store.
2. Set the **Company** field to the appropriate company.
3. Save.

Standard Odoo record rules apply — users only see stores belonging to their current company. Fleet webhook URLs use the company ID: `https://your-odoo.odoo.com/uva/webhook/fleet/<company_id>`.

---

## Fleet Configuration (Flow B)

1. Go to **Settings** and find the **Uva Fleet** section.
2. Enter:
   - **Uva Fleet API Key**
   - **Uva Fleet Webhook Secret**
   - **Uva Fleet Demo Mode**: Toggle off when ready for live deliveries.
3. Save.
4. Go to **Inventory → Configuration → Shipping Methods** and add **Uva Fleet** as a delivery carrier.

---

## Webhook URLs

Provide these URLs to Uva so they can send data to your Odoo instance:

| Webhook | URL | Purpose |
|---------|-----|---------|
| Incoming Orders | `https://your-odoo.odoo.com/uva/webhook/orders/<store_id>` | Receives new customer orders |
| Fleet Status | `https://your-odoo.odoo.com/uva/webhook/fleet/<company_id>` | Receives delivery status updates |

- **`<store_id>`** is the numeric ID of your `uva.store.config` record (visible in the URL bar when editing the store config).
- **`<company_id>`** is your Odoo company ID (usually `1` for single-company setups).

Both endpoints require the `X-Uva-Signature` header with a valid HMAC-SHA256 signature.

You can also register webhooks programmatically via the `POST /webhooks/register` API endpoint (see `doc/api_compatibility.md`).

---

## Security

- **API keys** are stored as system-restricted fields (`groups='base.group_system'`). Only Odoo administrators can view or edit them.
- **Webhook secrets** are used for HMAC-SHA256 signature validation. Every incoming webhook is verified before processing.
- **Rate limiting**: Each webhook endpoint allows a maximum of 60 requests per minute per store/company. Excess requests receive HTTP 429.
- **PII auto-purge**: Raw order payloads (containing customer name, address, phone) are automatically purged after 30 days by a scheduled action.
- **Replay protection**: Fleet webhooks reject payloads with timestamps older than 5 minutes.
- **Multi-company isolation**: Store configurations, order logs, and fleet records respect Odoo's multi-company record rules. Users only access data for their current company.
- **Sandbox isolation**: Sandbox mode routes traffic to a separate API environment, preventing test data from mixing with production.
- **Notification permissions**: Customer notification settings are restricted to users with store configuration write access.

---

## Cron Jobs

The module installs eight scheduled actions. Adjust intervals under **Settings → Technical → Automation → Scheduled Actions**.

| Cron Job | Default Interval | What It Does |
|----------|-----------------|--------------|
| **Poll Incoming Orders** | Every 1 minute | Polls the Uva API for new orders (fallback when webhooks are unavailable). Per-store throttle respects each store's polling interval setting. Skips stores outside configured store hours. |
| **Poll Fleet Delivery Status** | Every 1 minute | Checks status of active Uva Fleet deliveries. |
| **Process API Retry Queue** | Every 1 minute | Retries failed API calls (exponential backoff). |
| **Purge Raw Payload (Orders)** | Daily at 2:00 AM | Clears raw JSON payloads older than 30 days (PII compliance). |
| **Purge Retry Queue Payloads** | Daily at 2:30 AM | Clears completed retry queue payloads older than 30 days. |
| **Connection Health Check** | Every 5 minutes | Checks polling freshness per store. Posts chatter warnings and creates activities for degraded/down stores. |
| **Auto-Accept Orders** | Every 1 minute | Automatically accepts orders that have exceeded their auto-accept timeout without staff action. |
| **Daily Menu Sync** | Daily at 3:00 AM | Syncs product catalogs to Uva for all stores with menu sync enabled. |

---

## Troubleshooting

### Connection Health

Each store shows a health indicator:
- 🟢 **OK** — Polling is fresh (within 3× the polling interval) or demo mode is active.
- 🟡 **Degraded** — Last poll is stale (3×–6× the polling interval). Check API connectivity.
- 🔴 **Down** — No successful poll recorded, or severely stale. Verify API key and network access.

### Common Issues

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| Webhook returns 403 | Webhook secret not configured on the store | Enter the webhook secret in store config or setup wizard |
| Webhook returns 400 "invalid signature" | HMAC mismatch — secret doesn't match Uva's | Verify the webhook secret matches what Uva has on file |
| Orders stuck in "Awaiting Mapping" | Uva product IDs not mapped to Odoo products | Use the Bulk Mapping Wizard or add mappings manually |
| Orders stuck in "Error" | POS order creation failed | Check the order's chatter for the error message. Fix the issue and click **Retry** on the order log |
| API timeout errors | Network issue or Uva API downtime | Check the retry queue — failed calls are retried automatically |
| Orders not arriving | Store hours enabled and store is currently closed | Check **Opening Time** and **Closing Time** on the store config |
| Menu sync failed | API error or product data issue | Check **Uva → Menu Sync Log** for details |
| Performance alert not firing | Alerts not enabled on the store | Enable `alert_enabled` and set `alert_acceptance_threshold` on the store config |

### Retry Queue

Go to **Uva → Retry Queue** to see failed API calls:
- **Pending**: Will be retried automatically on the next cron run.
- **Manual Retry**: Click the **Retry Now** button on any entry.
- **Discard**: Click **Discard** to permanently skip a failed call.

### Logs

- **Chatter**: Every order log and fleet delivery record tracks state changes in the Odoo chatter (message history).
- **Odoo server logs**: Search for `UvaOrderWebhookController`, `UvaFleetStatusWebhookController`, or `uva.` to find module-specific log entries.
- **Dashboard**: Go to **Uva → Dashboard** for an overview of order and connection status.
