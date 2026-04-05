/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { subscribePosChannel, unsubscribePosChannel } from "./uva_bus_compat";

/**
 * UvaPosOrderPopup — OWL 2 component for incoming Uva order notifications.
 *
 * Contract per D-08:
 *   Props:
 *     - order:       Object  — validated Uva order payload
 *     - storeConfig: Object  — { autoAcceptTimeout: Number, storeName: String }
 *     - onAccept:    Function(orderId, unavailableItems[])
 *     - onReject:    Function(orderId, reason)
 *
 *   State:
 *     - countdown:        Number  — seconds remaining before auto-accept
 *     - unavailableItems: Set     — item IDs marked unavailable by staff
 *
 *   Lifecycle:
 *     - onMounted:      start countdown timer
 *     - onWillUnmount:  clear timer + unsubscribe from bus (CRITICAL — prevents double-subscribe)
 *
 *   Bus channel: subscribes to 'uva_new_order' message type (via uva_bus_compat.js)
 *   Staff actions: sent via JSON-RPC to uva.order.service — NOT via bus.bus
 */
export class UvaPosOrderPopup extends Component {
    static template = "odoo_uva_connector.UvaPosOrderPopup";

    static props = {
        order: { type: Object },
        storeConfig: { type: Object },
        onAccept: { type: Function },
        onReject: { type: Function },
    };

    setup() {
        this.busService = useService("bus_service");
        this.rpc = useService("rpc");

        this.state = useState({
            countdown: this.props.storeConfig.autoAcceptTimeout || 0,
            unavailableItems: new Set(),
            visible: false,
            currentOrder: null,
            error: null,       // error message shown in popup when RPC fails
            submitting: false, // prevents double-submit while RPC is in flight
        });

        this._countdownInterval = null;
        this._onNewOrder = this._handleNewOrder.bind(this);
        this._busWrapper = null;  // stores the wrapper ref returned by subscribePosChannel

        onMounted(() => {
            // Subscribe and store the wrapper reference for cleanup
            this._busWrapper = subscribePosChannel(
                this.busService, "uva_new_order", this._onNewOrder
            );

            // If an order was passed as a prop directly, show it immediately
            if (this.props.order) {
                this._showOrder(this.props.order);
            }
        });

        onWillUnmount(() => {
            // CRITICAL: always unsubscribe using the stored wrapper reference and clear
            // timer on unmount. Failure causes double-subscribe on remount (D-06 addendum).
            this._clearCountdown();
            unsubscribePosChannel(this.busService, "uva_new_order", this._busWrapper);
            this._busWrapper = null;
        });
    }

    // ------------------------------------------------------------------
    // Bus message handler
    // ------------------------------------------------------------------

    _handleNewOrder(payload) {
        // payload shape: { order_id, external_id, state, store_name, auto_accept_timeout }
        this._showOrder(payload);
    }

    _showOrder(orderData) {
        this.state.currentOrder = orderData;
        this.state.unavailableItems = new Set();
        this.state.visible = true;
        this.state.error = null;
        this.state.submitting = false;

        const timeout = orderData.auto_accept_timeout
            ?? this.props.storeConfig.autoAcceptTimeout
            ?? 0;

        if (timeout > 0) {
            this.state.countdown = timeout;
            this._startCountdown(timeout);
        } else {
            this.state.countdown = 0;
        }
    }

    // ------------------------------------------------------------------
    // Countdown timer
    // ------------------------------------------------------------------

    _startCountdown(seconds) {
        this._clearCountdown();
        const endTime = Date.now() + seconds * 1000;
        this._countdownInterval = setInterval(() => {
            const remaining = Math.ceil((endTime - Date.now()) / 1000);
            if (remaining <= 0) {
                this._clearCountdown();
                this._autoAccept();
            } else {
                this.state.countdown = remaining;
            }
        }, 500); // 500ms tick for smooth display; uses wall-clock delta for accuracy
    }

    _clearCountdown() {
        if (this._countdownInterval !== null) {
            clearInterval(this._countdownInterval);
            this._countdownInterval = null;
        }
    }

    _autoAccept() {
        if (this.state.currentOrder && !this.state.submitting) {
            this.props.onAccept(
                this.state.currentOrder.order_id,
                [...this.state.unavailableItems]
            );
            this.state.visible = false;
        }
    }

    // ------------------------------------------------------------------
    // Staff actions — sent via JSON-RPC, NOT via bus.bus
    // ------------------------------------------------------------------

    async onAccept() {
        if (this.state.submitting) return;
        this._clearCountdown();
        const orderId = this.state.currentOrder?.order_id;
        if (!orderId) return;

        this.state.submitting = true;
        this.state.error = null;
        try {
            await this.rpc("/web/dataset/call_kw", {
                model: "uva.order.service",
                method: "process_staff_action",
                args: [orderId, "accept", [...this.state.unavailableItems]],
                kwargs: {},
            });
            this.props.onAccept(orderId, [...this.state.unavailableItems]);
            this.state.visible = false;
        } catch (error) {
            console.error("UvaPosOrderPopup: error accepting order", error);
            this.state.error = "Failed to accept order. Please try again.";
            this.state.submitting = false;
            // Keep popup open so staff can retry
        }
    }

    async onReject() {
        if (this.state.submitting) return;
        this._clearCountdown();
        const orderId = this.state.currentOrder?.order_id;
        if (!orderId) return;

        this.state.submitting = true;
        this.state.error = null;
        try {
            await this.rpc("/web/dataset/call_kw", {
                model: "uva.order.service",
                method: "process_staff_action",
                args: [orderId, "reject", []],
                kwargs: {},
            });
            this.props.onReject(orderId, "staff_rejected");
            this.state.visible = false;
        } catch (error) {
            console.error("UvaPosOrderPopup: error rejecting order", error);
            this.state.error = "Failed to reject order. Please try again.";
            this.state.submitting = false;
            // Keep popup open so staff can retry
        }
    }

    toggleItemUnavailable(itemId) {
        if (this.state.unavailableItems.has(itemId)) {
            this.state.unavailableItems.delete(itemId);
        } else {
            this.state.unavailableItems.add(itemId);
        }
    }

    get hasUnavailableItems() {
        return this.state.unavailableItems.size > 0;
    }
}
