# Uva PR Connector — Troubleshooting Guide

---

## Connection Health

Each store configuration displays a connection health indicator. Check it under **Uva → Store Configuration** or in the POS health dot.

| Status | Meaning | Action |
|--------|---------|--------|
| 🟢 **OK** | Last poll was recent (within 3× the polling interval), or demo mode is active. | No action needed. |
| 🟡 **Degraded** | Last poll is stale (3×–6× the polling interval). | Check network connectivity and Uva API status. The system will auto-recover when the next poll succeeds. |
| 🔴 **Down** | No successful poll recorded, or severely overdue. | Verify API key is correct, check that the Uva API is reachable, and review Odoo server logs. |

When health is degraded or down, the module automatically:
- Posts a warning in the store's chatter.
- Creates a to-do activity for the responsible user.
- Sends a health notification to the POS session.

---

## Common Errors

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| Webhook returns 403 — "webhook not configured" | Webhook secret is empty on the store configuration | Enter the webhook secret in store config or setup wizard |
| Webhook returns 400 — "invalid signature" | HMAC mismatch — secret doesn't match Uva's | Verify the webhook secret matches exactly. Secrets are case-sensitive. |
| Webhook returns 429 — "rate limit exceeded" | More than 60 requests/min from the same store/company | Usually transient. Check for duplicate webhook configs on Uva's side. |
| API timeout / connection errors | Network issue or Uva API downtime | Failed calls auto-retry via the retry queue. Check **Uva → Retry Queue**. |
| Orders stuck in "Awaiting Mapping" | Uva product IDs not mapped to Odoo products | Use the Bulk Mapping Wizard or add mappings manually. |
| Orders stuck in "Error" | POS order creation failed | Check the order's chatter for the error. Fix the issue and click **Retry**. |
| Orders not arriving | Store hours enabled and store is currently closed | Check **Opening Time** and **Closing Time** on the store config. Disable `store_hours_enabled` to accept orders 24/7. |
| Orders not arriving | Order routing rules filtering them out | Check **Uva → Configuration → Order Rules** — a rule may be routing orders elsewhere or blocking them. |
| Customer not receiving notifications | Notifications not enabled on the store | Enable `notification_enabled` on the store config. Also verify the webhook URL in `ir.config_parameter`. |
| Menu sync failed | API error or product data issue | Check **Uva → Menu Sync Log** for the specific error. Retry with the **Sync Menu** button on the store config. |
| Performance alerts not firing | Alerts not enabled or threshold too low | Enable `alert_enabled` and set `alert_acceptance_threshold` on the store config. |
| Tips not showing on orders | Tip field not mapped or zero tip | Tips only display when the customer includes one. Verify the order payload contains a tip field. |
| POS offline — red banner | Connection to Uva API lost | Orders will replay automatically when connection restores. Check network and API status. |

---

## Store Hours Issues

If orders are not arriving at your POS:

1. Go to **Uva → Store Configuration** and open the store.
2. Check if **Store Hours** (`store_hours_enabled`) is enabled.
3. Verify the **Opening Time** and **Closing Time** are correct for your timezone.
4. If the store should accept orders 24/7, disable the store hours toggle.

> **Note**: The polling cron skips stores that are outside their configured hours. This is by design — not a bug.

---

## Notification Failures

If customers are not receiving order status notifications:

1. Verify `notification_enabled` is checked on the store configuration.
2. Check the webhook registration URL in **Settings → Technical → Parameters → System Parameters** — look for the `uva.webhook_callback_url` parameter. It must be a publicly reachable HTTPS URL.
3. Review the store's chatter for any notification delivery errors.
4. Test by accepting an order and checking if Uva received the status update (check Uva's dashboard or contact Uva support).

---

## Menu Sync Failures

If menu sync is not working:

1. Go to **Uva → Menu Sync Log** to see the sync history and error details.
2. Common causes:
   - **API authentication error**: Verify the store's API key is valid.
   - **Product data issue**: A product may have missing required fields (name, price). Fix the product in Odoo and retry.
   - **Network timeout**: Transient issue. Click **Sync Menu** on the store config to retry manually.
3. The daily menu sync cron runs at 3:00 AM. If you need an immediate sync, use the **Sync Menu** button.

---

## Performance Alert Configuration

If performance alerts are not working as expected:

1. Go to **Uva → Store Configuration** and open the store.
2. Verify **Performance Alerts** (`alert_enabled`) is checked.
3. Check the **Acceptance Threshold** (`alert_acceptance_threshold`) — this is the minimum acceptance rate percentage. If set to 90, alerts fire when the rate drops below 90%.
4. The performance alert cron evaluates metrics periodically. Alerts appear as:
   - Chatter warnings on the store record.
   - To-do activities assigned to the responsible user.

---

## Offline POS Recovery

When the POS loses connection to the Uva API:

1. A **red banner** appears at the top of the POS screen.
2. The POS continues to function for regular (non-Uva) orders.
3. When the connection is restored:
   - The banner turns **green** briefly, then disappears.
   - Any orders that arrived during the outage are **replayed automatically** and appear as popups.
4. If orders are missing after reconnection:
   - Check the **Error Orders** tab in POS for any that failed during replay.
   - Go to **Uva → Incoming Orders** in the backend to see all orders and their states.

---

## Tip Handling Issues

Tips from Uva orders are displayed on the order popup and recorded on the POS order.

If tips are not appearing:
1. Verify the Uva order payload includes a tip field (check the raw payload on the order log before it's purged).
2. Tips of $0.00 are not displayed — this is expected behavior.
3. If the tip field is present but not showing, check for product mapping issues on the tip line item (some Uva configurations send tips as a separate line item).

---

## Order Routing Rule Debugging

If orders are being routed incorrectly or not arriving at the expected POS:

1. Go to **Uva → Configuration → Order Rules**.
2. Review the rules in priority order (lower number = higher priority). The **first matching rule** wins.
3. Check each rule's conditions against the order that was misrouted.
4. Common issues:
   - **Overlapping rules**: Two rules match the same order. Adjust priorities.
   - **Catch-all rule too high**: A broad rule with low priority number catches orders before more specific rules. Move it to a higher number.
   - **Missing rule**: No rule matches the order. Add a default/catch-all rule with the highest priority number.
5. Test by creating a new order (or using sandbox mode) and verifying it routes correctly.

---

## Retry Queue

The retry queue handles failed API calls automatically.

### How It Works

1. When an API call fails (timeout, 5xx error, network issue), it's added to the retry queue.
2. A cron job runs every minute and retries due entries with exponential backoff.
3. After repeated failures, entries are marked as failed and require manual intervention.

### Viewing the Queue

Go to **Uva → Retry Queue**. Each entry shows:
- The API endpoint that failed
- Number of attempts
- Next retry time
- Current status

### Manual Actions

- **Retry Now**: Click to immediately retry a failed entry (bypasses the backoff timer).
- **Discard**: Permanently skip a failed entry. Use this only if the call is no longer needed.

---

## Orders Stuck in "Awaiting Mapping" (pending)

**Cause**: The incoming order contains Uva product IDs that aren't mapped to Odoo products.

**Fix**:
1. Go to **Uva → Product Mapping**.
2. Check which Uva product IDs are missing. (Tip: use the **Bulk Mapping Wizard** — it shows unmapped IDs from recent orders.)
3. Create the missing mappings.
4. The order will be processed on the next polling cycle, or you can manually reprocess it from the order log.

> **Note**: Auto-mapping may resolve some of these automatically if the Uva product name matches an Odoo product name exactly.

---

## Orders Stuck in "Error"

**Cause**: The order was accepted but POS order creation failed. Common reasons:
- Mapped Odoo product is archived or deleted.
- POS session is closed or misconfigured.
- Product is missing required fields (e.g., no price set).

**Fix**:
1. Go to **Uva → Incoming Orders** and open the affected order.
2. Read the **chatter** (message history) — it contains the specific error message.
3. Fix the underlying issue (reactivate the product, open the POS session, etc.).
4. Click the **Retry** button on the order log entry.

---

## Fleet Delivery Stuck

**Cause**: The Uva Fleet delivery status isn't updating.

**Fix**:
1. Go to **Uva → Fleet Deliveries** and open the delivery record.
2. Check the **chatter** for the last known status from Uva.
3. Verify that:
   - The Fleet webhook URL is correctly configured on Uva's side: `https://your-odoo.odoo.com/uva/webhook/fleet/<company_id>`
   - The Fleet webhook secret matches in both Odoo (**Settings → Uva Fleet → Uva Fleet Webhook Secret**) and Uva.
   - The Fleet polling cron is active (**Settings → Technical → Scheduled Actions → Uva PR: Poll Fleet Delivery Status**).
4. Contact Uva support at **info+fleet@pideuva.com** if the delivery appears stuck on their end.

---

## How to Check Logs

### Chatter Messages

Every order log and fleet delivery record has a chatter thread. State changes, errors, and health warnings are posted there automatically. This is the fastest way to see what happened.

### Odoo Server Logs

For deeper investigation, search the Odoo server log for module-specific entries:

```
UvaOrderWebhookController     — incoming order webhook events
UvaFleetStatusWebhookController — fleet status webhook events
uva.order.service              — order processing logic
uva.fleet.service              — fleet delivery logic
uva.api.client                 — API call details and errors
uva.api.retry.queue            — retry queue processing
uva.store.config               — health check results
uva.menu.sync                  — menu sync operations
uva.notification               — customer notification delivery
```

On Odoo.sh, access logs via **your-project.odoo.com → Logs** in the project dashboard.

### Dashboard

Go to **Uva → Dashboard** for a high-level overview of:
- Recent orders and their states
- Connection health per store
- Retry queue status
- Performance metrics and alerts
