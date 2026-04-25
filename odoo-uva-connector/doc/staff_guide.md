# Uva PR Connector — POS Staff Guide

This guide explains how to handle Uva orders in the Odoo Point of Sale.

---

## Receiving Orders

When a new Uva order arrives, a **popup appears on your POS screen** with a notification sound.

The popup shows:
- **Store name** — which store the order is for (badge in the header).
- **Uva Order ID** — the external order reference.
- **Customer** — name, delivery address, and phone (if provided by the customer).
- **Total** — the order total amount.
- **Tip** — if the customer included a tip, it is shown separately below the total.
- **Special Instructions** — any notes from the customer (highlighted in blue).
- **Items** — each product with quantity, price, and a **thumbnail image** of the product.

> You do not need to do anything in the Uva app. Everything is handled from POS.

---

## Store Hours

Your store may have operating hours configured. When store hours are enabled:
- Orders **only arrive during opening hours**. If you're not seeing orders, your store may be closed.
- Outside of configured hours, the POS will not receive Uva order popups.
- If you believe the hours are wrong, contact your manager to adjust them in the store configuration.

---

## Accepting or Rejecting Orders

At the bottom of the popup you have two buttons:

- **✓ Accept** — Confirms the order. It will be created as a POS order in Odoo.
- **✗ Reject** — Declines the order. Uva is notified that the order was rejected.

### Marking Items as Unavailable

If a specific item is out of stock:
1. Click **Mark Unavailable** next to that item. It turns red.
2. Click **✓ Accept (with modifications)** — the order is accepted but Uva is told which items are unavailable.
3. To undo, click the item again before accepting.

### Auto-Accept Timer

If your store has an auto-accept timeout configured, you'll see a countdown in the header (e.g., "Auto-accept in 45s"). If you don't act before the timer runs out, the order is **automatically accepted**.

- The countdown turns **yellow** at 30 seconds and **red** at 10 seconds.
- Clicking Accept or Reject cancels the timer.

---

## Prep Time Tracking

After accepting an order, you can track preparation time:

1. Click **Start Preparing** on the accepted order. This records when preparation began.
2. When the order is ready, click **Ready**. This records the completion time and notifies Uva that the order is ready for pickup.

Prep times are tracked per order and visible to managers in the dashboard and analytics.

---

## Order Modification

If you need to change an accepted order (e.g., substitute an item):

1. Open the accepted order from the **Uva Orders** screen.
2. Click **Modify**.
3. Make changes — mark items as unavailable, adjust quantities, or add notes.
4. Confirm the modification. Uva is notified of the changes.

> **Note**: Modification is only available for accepted orders that haven't been finalized yet.

---

## Kitchen Ticket Printing

To print a kitchen ticket for an order:

1. Open the order popup or the order from the **Uva Orders** screen.
2. Click **Print**.
3. The kitchen ticket is sent to your configured POS receipt printer.

This works with any printer configured in your POS session settings.

---

## Order Queue

During busy periods, multiple orders may arrive while you're reviewing one.

- A **badge** in the popup header shows how many orders are waiting (e.g., "+3 more").
- After you accept or reject the current order, the **next order in the queue** appears automatically.
- The queue holds up to 50 orders.

---

## Sound Notifications

Each new order triggers a short notification beep.

- Click the **🔊 / 🔇 button** in the popup header to toggle sound on or off.
- If you don't hear sounds, check that your **browser allows audio** for the Odoo site:
  - Chrome: Click the lock icon in the address bar → Site settings → Sound → Allow.
  - Firefox: Click the lock icon → Permissions → Autoplay → Allow Audio.

---

## Offline Mode

If the connection to the Uva API goes down, the POS continues working:

- A **red banner** appears at the top of the POS screen indicating the connection is offline.
- When the connection is restored, the banner turns **green** briefly, then disappears.
- Any orders that arrived while offline are **replayed automatically** when the connection is restored — they will appear as popups in the normal queue.
- You can continue processing existing POS orders normally while offline. Only new Uva orders are paused.

---

## Error Orders Tab

The POS includes an **Error Orders** tab that shows orders that failed to process:

1. In the POS, look for the **Error Orders** tab (or icon with a count badge).
2. Each entry shows the order details and the error reason.
3. Once the underlying issue is resolved (e.g., product mapping added, POS session reopened), the order can be retried from this tab or from the backend.

> **Tip**: If you see orders piling up in the Error tab, notify your manager — it usually means a configuration issue needs fixing.

---

## Order States

Orders go through these states:

| State | What It Means |
|-------|--------------|
| **New** (draft) | Order just arrived. Waiting for staff action. |
| **Awaiting Mapping** (pending) | Some Uva products aren't mapped to Odoo products yet. Ask your manager to add product mappings. |
| **Accepted** | You accepted the order. Odoo is creating the POS order. |
| **Preparing** | You clicked Start Preparing. The kitchen is working on it. |
| **Ready** | You clicked Ready. Uva has been notified the order is ready for pickup. |
| **Rejected** | You rejected the order. Uva has been notified. |
| **Processed** (done) | POS order was created successfully. The order is complete. |
| **Error** | Something went wrong creating the POS order. See below. |

---

## What to Do When Something Goes Wrong

### The popup shows an error message

If you see "Failed to accept order. Please try again." or "Failed to reject order. Please try again.":
1. Wait a few seconds and try the button again.
2. If it keeps failing, check your internet connection.
3. If the problem persists, contact your manager.

### An order is stuck in "Error" state

This means the POS order couldn't be created (e.g., a product is misconfigured).
1. Go to **Uva → Incoming Orders** and find the order.
2. Check the **chatter** (message history) at the bottom — it explains what went wrong.
3. After the issue is fixed, click the **Retry** button on the order to re-attempt POS order creation.

### An order is stuck in "Awaiting Mapping"

This means a Uva product doesn't have a matching Odoo product yet. Let your manager know — they need to add the product mapping. Once mapped, the order will be processed.

### No orders are arriving

If you're not receiving any orders:
1. Check the **connection health indicator** (see below). If it's red, the connection is down.
2. Check if your store is within **operating hours**. Orders won't arrive outside configured store hours.
3. Contact your manager if both look fine.

### Connection health indicator

The POS screen may show a small health dot:
- 🟢 Green — Connection is healthy.
- 🟡 Yellow — Connection is degraded. Orders may be delayed.
- 🔴 Red — Connection is down. Contact your manager.
