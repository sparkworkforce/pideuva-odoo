/** @odoo-module **/

/**
 * bus.bus compatibility shim — Odoo 17 / 18 / 19
 *
 * Verified against source: addons/bus/static/src/services/bus_service.js
 *
 * v17 (branch 17.0):
 *   subscribe(notificationType, callback)
 *     - callback receives: detail (the payload directly, as a single arg)
 *     - NO unsubscribe() method on the service
 *     - removeEventListener() IS available on the public EventBus
 *
 * v18 (branch 18.0):
 *   subscribe(notificationType, callback)
 *     - callback receives: (payload, { id }) — TWO args
 *     - unsubscribe(notificationType, callback) available
 *     - Odoo internally wraps the callback in a Map (subscribeFnToWrapper)
 *       so unsubscribe MUST receive the exact same function reference passed to subscribe
 *
 * v19 (branch 19.0):
 *   subscribe(notificationType, callback)
 *     - callback receives: (payload, { id }) — identical to v18
 *     - unsubscribe(notificationType, callback) available
 *     - Payload is deep-cloned internally via JSON.parse(JSON.stringify(payload))
 *
 * NORMALIZATION REQUIREMENT:
 *   v17 delivers payload as the first (and only) arg.
 *   v18/v19 deliver (payload, {id}) — two args.
 *   The shim normalizes both to: callback(payload) — one arg, always the payload.
 *
 * UNSUBSCRIBE REQUIREMENT:
 *   subscribePosChannel ALWAYS wraps the caller's callback in a normalizing wrapper.
 *   It returns the wrapper reference. The caller MUST store this reference and pass
 *   it to unsubscribePosChannel — NOT the original callback.
 *
 *   For v18/v19: busService.unsubscribe(type, wrapper) — Odoo's internal Map
 *     tracks wrapper → internal_wrapper, so passing the same wrapper reference works.
 *   For v17: removeEventListener(type, wrapper) — same wrapper reference required.
 *
 * USAGE:
 *   // In onMounted:
 *   this._busWrapper = subscribePosChannel(busService, 'uva_new_order', this._onNewOrder);
 *
 *   // In onWillUnmount:
 *   unsubscribePosChannel(busService, 'uva_new_order', this._busWrapper);
 */

/**
 * Subscribe to a Uva notification type on the bus service.
 *
 * Normalizes the callback args across v17/18/19 so the caller always receives
 * just the payload object.
 *
 * @param {object} busService - The Odoo bus_service instance
 * @param {string} notificationType - The message type (e.g. 'uva_new_order')
 * @param {function} callback - Handler: (payload) => void
 * @returns {function} wrappedCallback — MUST be stored and passed to unsubscribePosChannel
 */
export function subscribePosChannel(busService, notificationType, callback) {
    let wrappedCallback;

    if (typeof busService.unsubscribe === 'function') {
        // v18 / v19: callback receives (payload, {id}) — normalize to (payload)
        wrappedCallback = (payload, _meta) => callback(payload);
        busService.subscribe(notificationType, wrappedCallback);
    } else {
        // v17: callback receives (detail) — already just the payload, pass through
        wrappedCallback = (detail) => callback(detail);
        busService.subscribe(notificationType, wrappedCallback);
    }

    return wrappedCallback;
}

/**
 * Unsubscribe from a Uva notification type on the bus service.
 *
 * Safe to call multiple times (idempotent).
 *
 * @param {object} busService - The Odoo bus_service instance
 * @param {string} notificationType - The message type to unsubscribe from
 * @param {function} wrappedCallback - The reference returned by subscribePosChannel
 *                                     (NOT the original callback)
 */
export function unsubscribePosChannel(busService, notificationType, wrappedCallback) {
    if (!wrappedCallback) {
        // Never subscribed or already cleaned up — safe no-op
        return;
    }

    if (typeof busService.unsubscribe === 'function') {
        // v18 / v19: pass the exact wrapper reference that was passed to subscribe
        busService.unsubscribe(notificationType, wrappedCallback);
    } else {
        // v17: use removeEventListener with the same wrapper reference
        if (typeof busService.removeEventListener === 'function') {
            busService.removeEventListener(notificationType, wrappedCallback);
        }
    }
}
