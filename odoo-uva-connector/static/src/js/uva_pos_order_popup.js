/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { subscribePosChannel, unsubscribePosChannel } from "./uva_bus_compat";

const MAX_QUEUE = 50;

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
        this.hardwareProxy = useService("hardware_proxy");

        this.state = useState({
            countdown: this.props.storeConfig.autoAcceptTimeout || 0,
            unavailableItems: {},
            visible: false,
            currentOrder: null,
            error: null,
            submitting: false,
            orderQueue: [],
            soundEnabled: true,
        });

        this._countdownInterval = null;
        this._audioCtx = null;
        this._onNewOrder = this._handleNewOrder.bind(this);
        this._busWrapper = null;

        onMounted(() => {
            this._busWrapper = subscribePosChannel(
                this.busService, "uva_new_order", this._onNewOrder
            );
            if (this.props.order) {
                this._showOrder(this.props.order);
            }
        });

        onWillUnmount(() => {
            this._clearCountdown();
            unsubscribePosChannel(this.busService, "uva_new_order", this._busWrapper);
            this._busWrapper = null;
            if (this._audioCtx && this._audioCtx.state !== "closed") {
                this._audioCtx.close();
            }
        });
    }

    // ------------------------------------------------------------------
    // Bus message handler
    // ------------------------------------------------------------------

    _handleNewOrder(payload) {
        this._playNotificationSound();
        if (this.state.visible) {
            if (this.state.orderQueue.length >= MAX_QUEUE) {
                this.state.orderQueue.shift();
            }
            this.state.orderQueue.push(payload);
        } else {
            this._showOrder(payload);
        }
    }

    _showOrder(orderData) {
        this.state.currentOrder = orderData;
        this.state.unavailableItems = {};
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

    _showNextOrder() {
        if (this.state.orderQueue.length > 0) {
            this._showOrder(this.state.orderQueue.shift());
        } else {
            this.state.visible = false;
        }
    }

    // ------------------------------------------------------------------
    // Sound (reuses a single AudioContext)
    // ------------------------------------------------------------------

    _getAudioContext() {
        if (!this._audioCtx || this._audioCtx.state === "closed") {
            this._audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (this._audioCtx.state === "suspended") {
            this._audioCtx.resume();
        }
        return this._audioCtx;
    }

    _playNotificationSound() {
        if (!this.state.soundEnabled) return;
        try {
            const ctx = this._getAudioContext();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.frequency.value = 880;
            osc.type = "sine";
            gain.gain.value = 0.3;
            osc.start();
            osc.stop(ctx.currentTime + 0.15);
        } catch (_) {
            // Web Audio not available — silent fallback
        }
    }

    toggleSound() {
        this.state.soundEnabled = !this.state.soundEnabled;
    }

    get queueLength() {
        return this.state.orderQueue.length;
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
        }, 500);
    }

    _clearCountdown() {
        if (this._countdownInterval !== null) {
            clearInterval(this._countdownInterval);
            this._countdownInterval = null;
        }
    }

    _autoAccept() {
        if (this.state.currentOrder && !this.state.submitting) {
            this.onAccept();
        }
    }

    // ------------------------------------------------------------------
    // Staff actions
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
                args: [orderId, "accept", Object.keys(this.state.unavailableItems)],
                kwargs: {},
            });
            this.props.onAccept(orderId, Object.keys(this.state.unavailableItems));
            this._showNextOrder();
        } catch (error) {
            console.error("UvaPosOrderPopup: error accepting order", error);
            this.state.error = "Failed to accept order. Please try again.";
            this.state.submitting = false;
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
            this._showNextOrder();
        } catch (error) {
            console.error("UvaPosOrderPopup: error rejecting order", error);
            this.state.error = "Failed to reject order. Please try again.";
            this.state.submitting = false;
        }
    }

    async onModify() {
        if (this.state.submitting) return;
        const orderId = this.state.currentOrder?.order_id;
        if (!orderId) return;
        const removedItems = Object.keys(this.state.unavailableItems);
        if (!removedItems.length) {
            this.state.error = "Mark items as unavailable before modifying.";
            return;
        }
        this.state.submitting = true;
        this.state.error = null;
        try {
            await this.rpc("/web/dataset/call_kw", {
                model: "uva.order.service",
                method: "process_modification",
                args: [orderId, { removed_items: removedItems }],
                kwargs: {},
            });
            this._showNextOrder();
        } catch (error) {
            console.error("UvaPosOrderPopup: error modifying order", error);
            this.state.error = "Failed to modify order. Please try again.";
            this.state.submitting = false;
        }
    }

    toggleItemUnavailable(itemId) {
        if (this.state.unavailableItems[itemId]) {
            delete this.state.unavailableItems[itemId];
        } else {
            this.state.unavailableItems[itemId] = true;
        }
    }

    get hasUnavailableItems() {
        return Object.keys(this.state.unavailableItems).length > 0;
    }

    async onStartPreparing() {
        const orderId = this.state.currentOrder?.order_id;
        if (!orderId) return;
        try {
            await this.rpc("/web/dataset/call_kw", {
                model: "uva.order.log",
                method: "action_start_preparing",
                args: [[orderId]],
                kwargs: {},
            });
        } catch (error) {
            console.error("UvaPosOrderPopup: error starting prep", error);
            this.state.error = "Failed to start preparation.";
        }
    }

    async onMarkReady() {
        const orderId = this.state.currentOrder?.order_id;
        if (!orderId) return;
        try {
            await this.rpc("/web/dataset/call_kw", {
                model: "uva.order.log",
                method: "action_mark_ready",
                args: [[orderId]],
                kwargs: {},
            });
        } catch (error) {
            console.error("UvaPosOrderPopup: error marking ready", error);
            this.state.error = "Failed to mark order ready.";
        }
    }

    async printKitchenTicket() {
        const order = this.state.currentOrder;
        if (!order) return;
        const esc = (s) => String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        const items = order.items || [];
        let receipt = `<div style="font-family:monospace;width:280px">`;
        receipt += `<h2 style="text-align:center">🛵 UVA ORDER</h2>`;
        receipt += `<p><b>Order:</b> ${esc(order.external_id)}</p>`;
        if (order.customer_name) receipt += `<p><b>Customer:</b> ${esc(order.customer_name)}</p>`;
        if (order.delivery_address) receipt += `<p><b>Address:</b> ${esc(order.delivery_address)}</p>`;
        receipt += `<hr/>`;
        for (const item of items) {
            const qty = parseInt(item.qty || item.quantity || 1, 10);
            const name = esc(item.name || item.product_id || '');
            receipt += `<p><b>${qty}x</b> ${name}`;
            if (item.price) receipt += ` — $${esc(item.price)}`;
            receipt += `</p>`;
            const notes = item.special_instructions || item.notes;
            if (notes) receipt += `<p style="margin-left:10px"><i>→ ${esc(notes)}</i></p>`;
        }
        if (order.notes) {
            receipt += `<hr/><p><b>Notes:</b> ${esc(order.notes)}</p>`;
        }
        receipt += `<hr/><p style="text-align:center;font-size:0.8em">${new Date().toLocaleString()}</p>`;
        receipt += `</div>`;
        try {
            await this.hardwareProxy.printer.printReceipt(receipt);
        } catch (_) {
            // Fallback: open print dialog
            const win = window.open('', '_blank', 'width=320,height=600');
            if (win) {
                win.document.write(receipt);
                win.document.close();
                win.print();
            }
        }
    }
}
