# Uva PR API Compatibility Reference

All Uva API endpoints used by this module. Replaces scattered `TODO(uva-api)` comments.

Base URL: `https://api.pideuva.com/v1` (production) / `https://sandbox.pideuva.com/v1` (sandbox)
Auth: `Authorization: Bearer {api_key}`

---

## GET /health

Ping endpoint to verify connectivity and credentials.

- **Request**: No body or params.
- **Response**: `200 OK` (any body accepted)
- **Status**: ⚠️ Unconfirmed — path may differ

---

## GET /orders

Poll for new incoming orders since a timestamp.

- **Params**: `store_id` (string), `since` (ISO 8601 datetime)
- **Response**:
  ```json
  { "orders": [ { "id": "...", "items": [...], "customer": {...}, ... } ] }
  ```
- **Status**: ⚠️ Unconfirmed — param names and response schema assumed

---

## POST /orders/{id}/status

Notify Uva of a staff action on an order (accept, reject, modify).

- **Body**:
  ```json
  { "action": "accept|reject|modify", "unavailable_items": ["item_id_1"] }
  ```
- **Response**: `200 OK`
- **Status**: ⚠️ Unconfirmed — endpoint path and payload schema assumed

---

## GET /products

Fetch product catalog for a store.

- **Params**: `store_id` (string)
- **Response**:
  ```json
  { "products": [ { "id": "...", "name": "...", "price": 0.00 } ] }
  ```
- **Status**: ⚠️ Unconfirmed — path and response schema assumed

---

## POST /webhooks/register

Register a webhook callback URL with Uva so they push events to your Odoo instance.

- **Body**:
  ```json
  {
    "url": "https://your-odoo.odoo.com/uva/webhook/orders/<store_id>",
    "events": ["new_order", "order_update"],
    "secret": "your_webhook_secret"
  }
  ```
- **Response**:
  ```json
  { "webhook_id": "wh_abc123", "status": "active" }
  ```
- **Status**: ⚠️ Unconfirmed — payload schema assumed

---

## POST /menu/sync

Push the store's product catalog to Uva to keep the menu in sync.

- **Body**:
  ```json
  {
    "store_id": "...",
    "products": [
      { "id": "...", "name": "...", "price": 0.00, "available": true, "category": "..." }
    ]
  }
  ```
- **Response**:
  ```json
  { "synced": 42, "errors": [] }
  ```
- **Status**: ⚠️ Unconfirmed — payload schema assumed

---

## POST /fleet/estimate

Request a delivery cost estimate.

- **Body**:
  ```json
  { "pickup": { "lat": 0.0, "lng": 0.0, "address": "..." },
    "destination": { "lat": 0.0, "lng": 0.0, "address": "..." } }
  ```
- **Response**:
  ```json
  { "amount": 5.00, "currency": "USD", "eta_minutes": 30 }
  ```
- **Status**: ⚠️ Unconfirmed

---

## POST /fleet/deliveries

Create a Uva Fleet delivery order.

- **Body**:
  ```json
  { "pickup": { "lat": 0.0, "lng": 0.0, "address": "..." },
    "destination": { "lat": 0.0, "lng": 0.0, "address": "..." },
    "reference": "SO001" }
  ```
- **Response**:
  ```json
  { "delivery_id": "UVA-ABC123", "tracking_url": "https://..." }
  ```
- **Status**: ⚠️ Unconfirmed

---

## DELETE /fleet/deliveries/{id}

Cancel a Uva Fleet delivery.

- **Response**: `200 OK` or `204 No Content`
- **Error**: `422` with `error_code: COVERAGE_ERROR` if cancellation rejected
- **Status**: ⚠️ Unconfirmed — error codes assumed

---

## GET /fleet/deliveries/{id}/status

Poll the status of a Uva Fleet delivery.

- **Response**:
  ```json
  { "status": "pending|assigned|in_transit|delivered|cancelled|failed",
    "updated_at": "2025-01-01T00:00:00Z",
    "driver": { "name": "...", "phone": "...", "lat": 0.0, "lng": 0.0 },
    "eta_minutes": 15 }
  ```
- **Status**: ⚠️ Unconfirmed

---

## Webhook: Incoming Order

Received at `POST /uva/webhook/orders/{store_id}`.

- **Expected payload**:
  ```json
  { "event": "new_order", "order": { "id": "...", "items": [...], ... } }
  ```
- **Status**: ⚠️ Unconfirmed

---

## Webhook: Fleet Status Update

Received at `POST /uva/webhook/fleet/{company_id}`.

- **Expected payload**:
  ```json
  { "event": "status_update", "delivery_id": "...", "status": "...",
    "driver": { "name": "...", "phone": "...", "lat": 0.0, "lng": 0.0 } }
  ```
- **Status**: ⚠️ Unconfirmed

---

## HMAC Signature Validation

All webhook requests include a signature header for payload verification.

- **Header**: `X-Uva-Signature` (assumed)
- **Format**: `sha256=<hex_digest>` or plain `<hex_digest>`
- **Algorithm**: HMAC-SHA256 with the store's `webhook_secret` as key
- **Status**: ⚠️ Unconfirmed — header name and format assumed

---

## Error Responses

| HTTP Status | Meaning | Retryable |
|---|---|---|
| 401 / 403 | Auth error (invalid/expired API key) | No |
| 422 + `COVERAGE_ERROR` | Destination outside service area | No |
| 5xx | Server error | Yes |
| Timeout | Network timeout | Yes |

**Status**: ⚠️ Coverage error status code and error_code field unconfirmed
