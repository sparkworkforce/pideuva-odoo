# Uva PR Connector — Launch Checklist

Everything below requires manual action (API access, Odoo.sh, real devices, Uva partnership). Complete these to take the module from 9/10 to 10/10.

---

## 1. Validate Against Real Uva API

**Why:** Every endpoint is structurally complete but unconfirmed. The `doc/api_compatibility.md` file documents all assumptions.

**Steps:**
- [ ] Contact Uva (info+fleet@pideuva.com) to request API sandbox credentials
- [ ] Get official API documentation — confirm base URL, auth mechanism, endpoint paths
- [ ] Update `doc/api_compatibility.md` — flip each "⚠️ Unconfirmed" to "✅ Confirmed"
- [ ] Confirm HMAC signature format (header name `X-Uva-Signature`, `sha256=` prefix or plain hex)
- [ ] Confirm webhook payload schemas for both order and fleet status webhooks
- [ ] Confirm error response codes (especially 422 for coverage errors)
- [ ] Confirm product catalog endpoint exists (`GET /products`)
- [ ] Confirm webhook registration endpoint exists (`POST /webhooks/register`)
- [ ] Test `GET /health` ping endpoint — update path if different
- [ ] Run E2E tests against sandbox: `python -m pytest tests/test_uva_e2e_mock.py` with real API mocks replaced by sandbox calls
- [ ] Update `ir.config_parameter` defaults if base URL differs from `https://api.pideuva.com/v1`
- [ ] If any endpoint path differs, update the corresponding method in `models/uva_api_client.py`

**Files to update after confirmation:**
- `doc/api_compatibility.md` — mark endpoints as confirmed
- `models/uva_api_client.py` — adjust paths/params if needed
- `controllers/uva_fleet_webhook.py` — adjust payload field names if needed
- `controllers/uva_order_webhook.py` — adjust header name if needed

---

## 2. Deploy to Odoo.sh Staging Instance

**Why:** The module compiles and XML parses, but Odoo's module installer does additional validation that can only be caught at install time.

**Steps:**
- [ ] Create a staging branch on Odoo.sh (Odoo 18 Enterprise)
- [ ] Run `./build.sh 18` to generate `__manifest__.py`
- [ ] Push to Odoo.sh and trigger module install
- [ ] Verify: no install errors in the Odoo.sh logs
- [ ] Verify: all 6 cron jobs appear in Settings → Technical → Scheduled Actions
- [ ] Verify: all menu items appear under Point of Sale → Uva
- [ ] Verify: Settings → Uva Fleet section renders correctly
- [ ] Verify: onboarding banner appears when setup is incomplete
- [ ] Open POS session, verify Uva components load (no JS console errors)
- [ ] Create a store config in demo mode, verify webhook URL is generated
- [ ] Send a test webhook via `curl`:
  ```bash
  SECRET="your-webhook-secret"
  PAYLOAD='{"id":"TEST-001","items":[{"product_id":"P1","name":"Test","qty":1,"price":10}]}'
  SIG=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')
  curl -X POST https://your-instance.odoo.com/uva/webhook/orders/1 \
    -H "Content-Type: application/json" \
    -H "X-Uva-Signature: $SIG" \
    -d "$PAYLOAD"
  ```
- [ ] Verify: order appears in Uva → Incoming Orders
- [ ] Accept the order from POS, verify POS order is created
- [ ] Open the tracking page at `/uva/track/DEMO-XXXXX`, verify map loads
- [ ] Test the "Ship with Uva Fleet" button on a sale order (demo mode)
- [ ] Verify: dashboard KPIs update correctly
- [ ] Verify: analytics graph/pivot views render

**Repeat for Odoo 17 and 19:**
- [ ] `./build.sh 17` → push to 17.0 branch → install → smoke test
- [ ] `./build.sh 19` → push to 19.0 branch → install → smoke test

---

## 3. Take Screenshots and Record Demo Video

**Why:** The `static/description/screenshots/` directory is empty. The Odoo App Store page has placeholder sections. Real screenshots are what sell the module.

**Screenshots to capture** (save as PNG in `static/description/screenshots/`):

- [ ] `01_pos_order_popup.png` — POS order notification with line items table, accept/reject buttons, countdown timer
- [ ] `02_pos_order_queue.png` — POS screen showing multiple queued Uva orders
- [ ] `03_order_kanban.png` — Incoming Orders kanban view grouped by state
- [ ] `04_fleet_tracking.png` — Customer-facing tracking page on mobile (use Chrome DevTools mobile view)
- [ ] `05_dashboard.png` — Uva Dashboard with KPI cards and fleet stats
- [ ] `06_analytics.png` — Order Analytics graph view showing orders by day
- [ ] `07_setup_wizard.png` — Setup wizard credentials step
- [ ] `08_bulk_mapping.png` — Bulk mapping wizard with fuzzy match results
- [ ] `09_store_config.png` — Store configuration form with webhook URL and health indicator
- [ ] `10_sale_order_fleet.png` — Sale order with "Ship with Uva Fleet" button

**After capturing, update `static/description/index.html`:**
- Replace the gray placeholder `<div>` sections with `<img>` tags pointing to the screenshots

**Demo video (2 minutes):**
- [ ] Record screen: Settings → Setup Wizard → create store in demo mode
- [ ] Send test webhook → show POS popup → accept → POS order created
- [ ] Show dashboard with updated KPIs
- [ ] Create sale order → Ship with Uva Fleet → estimate wizard → confirm
- [ ] Show tracking page with map
- [ ] Upload to YouTube/Vimeo, add link to `static/description/index.html` and `README.md`

---

## 4. Validate bus.bus Channel Format for Odoo 18

**Why:** The `_notify_pos` method uses string-based channels (`pos.config-{id}`). Odoo 18 may expect record-based channels.

**Steps:**
- [ ] On the Odoo.sh staging instance, open browser DevTools → Network tab
- [ ] Open a POS session
- [ ] Check what bus channels the POS JS subscribes to (look for `/longpolling/poll` requests)
- [ ] Note the exact channel format (e.g., `(dbname, 'pos.config', 1)` vs `'pos.config-1'`)
- [ ] Send a test webhook to trigger `_notify_pos`
- [ ] Check if the POS receives the notification (order popup should appear)
- [ ] If notifications don't arrive, update `models/uva_order_service.py`:
  ```python
  # Current:
  self.env['bus.bus']._sendone(channel, 'uva_new_order', {...})
  
  # May need to change to (Odoo 18 format):
  self.env['pos.config'].browse(pos_config_id)._bus_send('uva_new_order', {...})
  ```
- [ ] Also check `_notify_pos_health` uses the same channel format
- [ ] Test the POS error orders component receives bus updates
- [ ] Verify the `uva_bus_compat.js` helper works with Odoo 18's bus service

---

## 5. Add Uva Brand Assets to Tracking Page

**Why:** The tracking page is customer-facing. It should feel like a Uva experience.

**Steps:**
- [ ] Request from Uva: logo (SVG or PNG), brand color palette, tagline
- [ ] Confirm `#ff6b00` matches their actual brand orange (or update CSS variable)
- [ ] Save logo to `static/description/icon.png` (Odoo App Store icon, 128x128)
- [ ] Save logo to `static/src/img/uva_logo.svg` (for tracking page)
- [ ] Update `views/uva_tracking_template.xml`:
  - Replace `🛵 Delivery Tracking` header with Uva logo + "Delivery Tracking"
  - Add `Powered by Uva PR` with logo in footer
- [ ] Update `static/description/index.html` hero section with official logo
- [ ] Get written approval from Uva marketing for logo usage in the Odoo App Store listing
- [ ] If Uva has a custom font, add it to the tracking page CSS

---

## 6. Run Full Test Suite on All Target Versions

**Why:** Odoo 17, 18, and 19 have ORM differences that may cause test failures.

**Steps:**

**Odoo 18 (primary target):**
- [ ] On Odoo.sh staging:
  ```bash
  odoo-bin -d test_db --test-enable --stop-after-init -i odoo_uva_connector
  ```
- [ ] Fix any test failures (check logs for `ERROR` and `FAIL`)
- [ ] Check for ORM deprecation warnings (`WARNING` level)
- [ ] Verify all 14 test files pass

**Odoo 17:**
- [ ] Switch to 17.0 branch, run `./build.sh 17`
- [ ] Run same test command
- [ ] Known differences to check:
  - `pos.order` field names may differ (e.g., `full_product_name` may not exist in 17)
  - `bus.bus._sendone` signature may differ
  - `invisible` attribute syntax (Odoo 17 uses `attrs` dict, 18+ uses inline expressions)
- [ ] If tests fail, add version-specific shims in the affected methods

**Odoo 19:**
- [ ] Switch to 19.0 branch, run `./build.sh 19`
- [ ] Run same test command
- [ ] Check for any newly deprecated APIs in Odoo 19

**After all versions pass:**
- [ ] Update `README.md` supported versions table with test results
- [ ] Tag the release: `git tag v18.0.1.0.0`

---

## 7. Usability Test with Real POS Staff

**Why:** POS UX can only be validated by watching real users. Button placement, notification timing, and workflow flow need iteration.

**Setup:**
- [ ] Install module on a tablet (iPad or Android) running Odoo POS in demo mode
- [ ] Recruit 2-3 POS staff from a PR restaurant (ideally current Uva merchants)
- [ ] Prepare 10 test scenarios (mix of accept, reject, unavailable items, errors)

**Test script:**
1. [ ] Staff opens POS session — observe: do they notice the Uva health indicator?
2. [ ] Send test order via webhook — observe: do they hear the notification sound? How fast do they find the popup?
3. [ ] Ask them to accept the order — observe: is the Accept button obvious? Do they understand the countdown?
4. [ ] Send order with 5 items — ask them to mark 2 as unavailable — observe: is the toggle intuitive?
5. [ ] Send 3 orders rapidly — observe: do they understand the queue? Can they navigate between orders?
6. [ ] Trigger an error order — observe: do they find the Error Orders tab? Can they retry?
7. [ ] Ask them to print a kitchen ticket — observe: does the print flow work on their hardware?
8. [ ] Show them the tracking page on their phone — observe: is it readable? Do they understand the status?
9. [ ] Ask them to set up a new store config — observe: is the wizard clear?
10. [ ] Ask: "What's confusing? What's missing?"

**Feedback to act on:**
- [ ] Adjust button sizes/colors based on tap accuracy observations
- [ ] Adjust notification sound volume/frequency based on feedback
- [ ] Adjust auto-accept timeout default based on how fast staff actually respond
- [ ] Add/remove information from the POS popup based on what staff actually look at
- [ ] Update `doc/staff_guide.md` based on common questions

---

## Priority Order

| # | Item | Effort | Blocker? |
|---|------|--------|----------|
| 1 | Validate Uva API | 1-2 weeks | **Yes** — can't ship without confirmed endpoints |
| 2 | Deploy to Odoo.sh | 1 day | **Yes** — catches install-time issues |
| 4 | Validate bus.bus | 1 hour | **Yes** — POS notifications may not work |
| 6 | Run tests on all versions | 1 day | **Yes** — version compat is a selling point |
| 3 | Screenshots + video | 1 day | No — but critical for App Store listing |
| 5 | Uva brand assets | 1 day | No — cosmetic but professional |
| 7 | Usability test | 1 day | No — but highest UX impact |

---

## Definition of Done

The module is ready to publish on the Odoo App Store when:
- [ ] All 8 API endpoints confirmed and tested against Uva sandbox
- [ ] Module installs cleanly on Odoo 17, 18, and 19 Enterprise
- [ ] All 14 test files pass on all 3 versions
- [ ] POS notifications arrive correctly (bus.bus validated)
- [ ] 10 screenshots captured and embedded in App Store page
- [ ] Demo video recorded and linked
- [ ] Uva logo and brand assets approved and integrated
- [ ] At least 2 real POS staff have tested and provided feedback
- [ ] Feedback incorporated into final UI adjustments

---

## Final 7 — Last Mile to 10/10

These are the only remaining gaps. All require manual action.

### 8. Install on Odoo.sh and Fix Runtime Issues

The module compiles and XML parses, but Odoo's installer does additional validation.

- [ ] Create Odoo.sh staging project (Odoo 18 Enterprise)
- [ ] Run `./build.sh 18`, push, install module
- [ ] Check install logs for: view inheritance errors, missing `ir.model` refs for AbstractModels, asset bundling failures
- [ ] Known risk: `uva.order.service` is `models.AbstractModel` — the `uva_cron_auto_accept.xml` references `model_uva_order_service` which may not exist as an `ir.model` record. If install fails, change to `models.Model` or move the cron method to `uva.order.log`
- [ ] Verify all 8 cron jobs appear in Settings → Technical → Scheduled Actions
- [ ] Verify all menu items render under Point of Sale → Uva
- [ ] Verify Settings → Uva Fleet section and onboarding banner render
- [ ] Test webhook with curl (see section 2 above for command)
- [ ] Repeat for Odoo 17 (`./build.sh 17`) and Odoo 19 (`./build.sh 19`)

### 9. Validate bus.bus Channel Format in Odoo 18 POS

This determines whether the entire POS notification flow works.

- [ ] On Odoo.sh staging, open a POS session
- [ ] Open browser DevTools → Network → filter for `longpolling`
- [ ] Note the exact channel format the POS JS subscribes to
- [ ] Send a test webhook to trigger `_notify_pos`
- [ ] Check: does the POS popup appear?
- [ ] If NOT: the channel format needs updating. Check if Odoo 18 expects:
  - Record-based: `self.env['pos.config'].browse(id)._bus_send('uva_new_order', {...})`
  - Tuple-based: `(self.env.cr.dbname, 'pos.config', id)`
  - Current string-based: `f'pos.config-{id}'`
- [ ] Fix `_notify_pos` and `_notify_pos_health` in `models/uva_order_service.py`
- [ ] Also verify `uva_bus_compat.js` subscription matches the server-side channel format

### 10. Test Against Real Uva API Sandbox

- [ ] Contact Uva (info+fleet@pideuva.com), request sandbox credentials
- [ ] Get official API docs — compare against `doc/api_compatibility.md`
- [ ] Set `uva.api.sandbox_mode = True` in ir.config_parameter
- [ ] Set sandbox API key on a test store config
- [ ] Test each endpoint manually:
  - `GET /health` — verify connectivity
  - `GET /orders?store_id=X&since=Y` — verify response schema
  - `POST /orders/{id}/status` — verify accept/reject callback
  - `POST /fleet/estimate` — verify cost estimate
  - `POST /fleet/deliveries` — verify delivery creation
  - `GET /fleet/deliveries/{id}/status` — verify status polling
  - `DELETE /fleet/deliveries/{id}` — verify cancellation
  - `POST /webhooks/register` — verify webhook registration
  - `POST /menu/sync` — verify menu push
  - `GET /products` — verify product catalog
- [ ] Update `doc/api_compatibility.md` — flip ⚠️ to ✅ for each confirmed endpoint
- [ ] If any endpoint path/schema differs, update `models/uva_api_client.py`
- [ ] Run `test_uva_e2e_mock.py` with sandbox credentials (replace mock patches)

### 11. Capture Screenshots and Record Demo Video

Save PNGs to `static/description/screenshots/`:

- [ ] `01_pos_popup.png` — POS order popup with product thumbnails, line items, prep buttons
- [ ] `02_pos_queue.png` — POS screen with multiple queued orders
- [ ] `03_order_kanban.png` — Incoming Orders kanban grouped by state
- [ ] `04_tracking_mobile.png` — Customer tracking page on mobile (Chrome DevTools)
- [ ] `05_tracking_delivered.png` — Tracking page showing delivered state with proof photo
- [ ] `06_dashboard.png` — Dashboard with KPI cards and per-store table
- [ ] `07_analytics.png` — Order Analytics graph view
- [ ] `08_setup_wizard.png` — Setup wizard credentials step
- [ ] `09_store_config.png` — Store config with zone map and store hours
- [ ] `10_sale_order.png` — Sale order with "Ship with Uva Fleet" button

After capturing:
- [ ] Update `static/description/index.html` — replace placeholder divs with `<img>` tags
- [ ] Record 2-minute demo video showing Flow A + Flow B end-to-end
- [ ] Upload video to YouTube, add link to `index.html` and `README.md`

### 12. Usability Test with PR Restaurant Staff

- [ ] Install module on tablet in demo mode
- [ ] Recruit 2-3 POS staff from a PR restaurant (ideally current Uva merchants)

Test scenarios:
- [ ] Order arrives → staff hears sound? Finds popup? Understands countdown?
- [ ] Accept order → sees product thumbnails? Clicks Start Preparing? Prints kitchen ticket?
- [ ] Mark items unavailable → toggle intuitive? Modify flow clear?
- [ ] Mark Ready → button discoverable after Start Preparing?
- [ ] Error order → finds Error Orders tab? Retry works?
- [ ] Offline mode → sees banner? Orders replay on reconnect?
- [ ] Tracking page on phone → readable? Push notification works?
- [ ] Store hours → understands opening/closing config?

After testing:
- [ ] Adjust button sizes/colors based on tap accuracy
- [ ] Adjust notification sound volume/frequency
- [ ] Adjust auto-accept timeout default
- [ ] Update `doc/staff_guide.md` with common questions

### 13. Integrate Uva Brand Assets

- [ ] Request from Uva: logo (SVG + PNG), brand color hex codes, tagline, fonts
- [ ] Confirm `#ff6b00` matches their orange (or update CSS variable in tracking template)
- [ ] Save icon to `static/description/icon.png` (128×128, for Odoo App Store)
- [ ] Save logo to `static/src/img/uva_logo.svg`
- [ ] Update tracking page header: replace 🛵 emoji with logo
- [ ] Update tracking page footer: add logo next to "Powered by Uva PR"
- [ ] Update POS popup header: add small Uva logo
- [ ] Update App Store `index.html` hero section with official logo
- [ ] Get written approval from Uva marketing for logo usage

### 14. Submit to Odoo App Store

- [ ] Create Odoo Apps publisher account at apps.odoo.com
- [ ] Set pricing (one-time or subscription per version)
- [ ] Select supported versions: 17.0, 18.0, 19.0
- [ ] Upload module ZIP (output of `./build.sh`)
- [ ] Fill in listing: description, screenshots, video link, support email
- [ ] Set support policy: response time, included support hours
- [ ] Submit for Odoo technical review
- [ ] Address any review feedback (ORM warnings, view issues)
- [ ] Publish

---

## Updated Definition of Done

The module is ready to publish when ALL of these are checked:

- [ ] Module installs cleanly on Odoo 17, 18, and 19 Enterprise (Odoo.sh)
- [ ] All 14 test files pass on all 3 versions
- [ ] POS notifications arrive correctly (bus.bus channel validated)
- [ ] All API endpoints confirmed against Uva sandbox
- [ ] `doc/api_compatibility.md` shows all ✅
- [ ] 10 screenshots captured and embedded in App Store page
- [ ] Demo video recorded and linked
- [ ] Uva logo and brand assets approved and integrated
- [ ] At least 2 real POS staff have tested and provided feedback
- [ ] Feedback incorporated into final UI adjustments
- [ ] Odoo App Store listing submitted and approved
