/** @odoo-module **/

/**
 * Tests for UvaPosOrderPopup and uva_bus_compat.js
 *
 * Uses Odoo's QUnit-based test framework for POS JS tests.
 * Run via: python odoo-bin --test-tags /odoo_uva_connector (JS tests run in browser)
 */

import { subscribePosChannel, unsubscribePosChannel } from "./uva_bus_compat";

// ---------------------------------------------------------------------------
// uva_bus_compat.js tests
// ---------------------------------------------------------------------------

QUnit.module("uva_bus_compat", () => {

    QUnit.test("subscribePosChannel — v18/v19 path (unsubscribe available)", (assert) => {
        const received = [];
        let subscribedWith = null;
        let unsubscribedWith = null;
        const mockBusService = {
            subscribe(type, cb) { subscribedWith = cb; },
            unsubscribe(type, cb) { unsubscribedWith = cb; },
        };
        const callback = (payload) => received.push(payload);

        const wrapper = subscribePosChannel(mockBusService, "uva_new_order", callback);
        assert.ok(wrapper, "subscribePosChannel returns a wrapper reference");
        assert.ok(subscribedWith, "subscribe was called");
        assert.notEqual(wrapper, callback, "wrapper is a different function from callback");

        // Simulate v18/v19 message: (payload, {id})
        subscribedWith({ order_id: 1 }, { id: 42 });
        assert.equal(received.length, 1, "callback fired once");
        assert.deepEqual(received[0], { order_id: 1 }, "callback received normalized payload (not meta)");

        unsubscribePosChannel(mockBusService, "uva_new_order", wrapper);
        assert.equal(unsubscribedWith, wrapper, "unsubscribe called with the exact wrapper reference");
    });

    QUnit.test("subscribePosChannel — v17 path (no unsubscribe)", (assert) => {
        const received = [];
        const listeners = {};
        const mockBusService = {
            subscribe(type, cb) {
                listeners[type] = listeners[type] || [];
                listeners[type].push(cb);
            },
            removeEventListener(type, cb) {
                if (listeners[type]) {
                    listeners[type] = listeners[type].filter(fn => fn !== cb);
                }
            },
            // no unsubscribe property — v17
        };
        const callback = (payload) => received.push(payload);

        const wrapper = subscribePosChannel(mockBusService, "uva_new_order", callback);
        assert.ok(wrapper, "subscribePosChannel returns a wrapper reference");
        assert.equal(listeners["uva_new_order"].length, 1, "wrapper registered");
        assert.equal(listeners["uva_new_order"][0], wrapper, "registered wrapper matches returned ref");

        // Simulate v17 message: (detail) — single arg
        wrapper({ order_id: 2 });
        assert.equal(received.length, 1, "callback fired via wrapper");
        assert.deepEqual(received[0], { order_id: 2 }, "callback received payload");

        unsubscribePosChannel(mockBusService, "uva_new_order", wrapper);
        assert.equal(listeners["uva_new_order"].length, 0, "wrapper removed via removeEventListener");
    });

    QUnit.test("unsubscribePosChannel is idempotent — safe to call multiple times", (assert) => {
        const mockBusService = {
            subscribe(type, cb) {},
            unsubscribe(type, cb) {},
        };
        const callback = () => {};

        const wrapper = subscribePosChannel(mockBusService, "uva_new_order", callback);

        assert.expect(1);
        try {
            unsubscribePosChannel(mockBusService, "uva_new_order", wrapper);
            unsubscribePosChannel(mockBusService, "uva_new_order", wrapper);
            unsubscribePosChannel(mockBusService, "uva_new_order", wrapper);
            assert.ok(true, "multiple unsubscribe calls did not throw");
        } catch (e) {
            assert.ok(false, `unsubscribe threw: ${e.message}`);
        }
    });

    QUnit.test("unsubscribePosChannel with null wrapper is safe (never subscribed)", (assert) => {
        const mockBusService = {
            subscribe(type, cb) {},
            unsubscribe(type, cb) {},
        };

        assert.expect(1);
        try {
            unsubscribePosChannel(mockBusService, "uva_new_order", null);
            assert.ok(true, "unsubscribe with null wrapper did not throw");
        } catch (e) {
            assert.ok(false, `threw: ${e.message}`);
        }
    });

    QUnit.test("subscribe twice — component must prevent via onWillUnmount, not shim", (assert) => {
        // The shim does NOT deduplicate subscriptions — that's the component's responsibility.
        // The component stores the wrapper from the first subscribe and unsubscribes in
        // onWillUnmount before the next mount can subscribe again.
        let subscribeCount = 0;
        const mockBusService = {
            subscribe(type, cb) { subscribeCount++; },
            unsubscribe(type, cb) {},
        };
        const callback = () => {};

        const w1 = subscribePosChannel(mockBusService, "uva_new_order", callback);
        const w2 = subscribePosChannel(mockBusService, "uva_new_order", callback);

        assert.equal(subscribeCount, 2, "shim does not deduplicate — component must manage lifecycle");
        assert.notEqual(w1, w2, "each call returns a distinct wrapper reference");
    });

});

// ---------------------------------------------------------------------------
// UvaPosOrderPopup countdown tests (unit-level, no DOM required)
// ---------------------------------------------------------------------------

QUnit.module("UvaPosOrderPopup countdown logic", () => {

    /**
     * Minimal countdown logic extracted for unit testing without OWL mount.
     * Tests the timer management contract from D-08.
     */
    function makeCountdownController(timeout, onAutoAccept) {
        let interval = null;
        let count = timeout;

        return {
            start() {
                this.clear();
                interval = setInterval(() => {
                    count -= 1;
                    if (count <= 0) {
                        this.clear();
                        onAutoAccept();
                    }
                }, 1);  // 1ms for tests
            },
            clear() {
                if (interval !== null) {
                    clearInterval(interval);
                    interval = null;
                }
            },
            get count() { return count; },
            get isRunning() { return interval !== null; },
        };
    }

    QUnit.test("countdown starts and fires auto-accept at zero", (assert) => {
        const done = assert.async();
        let accepted = false;
        const ctrl = makeCountdownController(3, () => { accepted = true; });
        ctrl.start();
        setTimeout(() => {
            assert.ok(accepted, "auto-accept fired when countdown reached zero");
            done();
        }, 20);
    });

    QUnit.test("countdown cleared on manual action prevents auto-accept", (assert) => {
        const done = assert.async();
        let accepted = false;
        const ctrl = makeCountdownController(10, () => { accepted = true; });
        ctrl.start();
        // Simulate manual action — clear immediately
        ctrl.clear();
        setTimeout(() => {
            assert.notOk(accepted, "auto-accept did NOT fire after manual clear");
            assert.notOk(ctrl.isRunning, "interval is not running after clear");
            done();
        }, 50);
    });

    QUnit.test("clear is idempotent — safe to call multiple times", (assert) => {
        const ctrl = makeCountdownController(5, () => {});
        ctrl.start();
        assert.expect(1);
        try {
            ctrl.clear();
            ctrl.clear();
            ctrl.clear();
            assert.ok(true, "multiple clear() calls did not throw");
        } catch (e) {
            assert.ok(false, `clear() threw: ${e.message}`);
        }
    });

    QUnit.test("start clears previous interval before starting new one", (assert) => {
        const done = assert.async();
        let fireCount = 0;
        const ctrl = makeCountdownController(3, () => { fireCount++; });
        ctrl.start();
        ctrl.start();  // should clear the first interval
        setTimeout(() => {
            assert.equal(fireCount, 1, "auto-accept fired exactly once despite double start");
            done();
        }, 20);
    });

});
