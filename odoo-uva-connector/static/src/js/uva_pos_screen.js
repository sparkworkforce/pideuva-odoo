/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { subscribePosChannel, unsubscribePosChannel } from "./uva_bus_compat";

const MAX_QUEUE = 50;

export class UvaPosScreen extends Component {
    static template = "odoo_uva_connector.UvaPosScreen";
    static props = {};

    setup() {
        this.busService = useService("bus_service");
        this.rpc = useService("rpc");

        this.state = useState({
            orderQueue: [],
            selectedOrder: null,
            unavailableItems: {},
            error: null,
            submitting: false,
        });

        this._busWrapper = null;

        onMounted(() => {
            this._busWrapper = subscribePosChannel(
                this.busService, "uva_new_order", this._handleNewOrder.bind(this)
            );
        });

        onWillUnmount(() => {
            unsubscribePosChannel(this.busService, "uva_new_order", this._busWrapper);
            this._busWrapper = null;
        });
    }

    _handleNewOrder(payload) {
        if (this.state.orderQueue.length >= MAX_QUEUE) {
            this.state.orderQueue.shift();
        }
        this.state.orderQueue.push(payload);
        if (!this.state.selectedOrder) {
            this.state.selectedOrder = payload;
        }
    }

    selectOrder(order) {
        this.state.selectedOrder = order;
        this.state.unavailableItems = {};
        this.state.error = null;
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

    _removeFromQueue(orderId) {
        const idx = this.state.orderQueue.findIndex((o) => o.order_id === orderId);
        if (idx !== -1) {
            this.state.orderQueue.splice(idx, 1);
        }
        if (this.state.selectedOrder?.order_id === orderId) {
            this.state.selectedOrder = this.state.orderQueue[0] || null;
        }
        this.state.unavailableItems = {};
        this.state.error = null;
    }

    async onAccept() {
        if (this.state.submitting || !this.state.selectedOrder) return;
        this.state.submitting = true;
        this.state.error = null;
        try {
            await this.rpc("/web/dataset/call_kw", {
                model: "uva.order.service",
                method: "process_staff_action",
                args: [this.state.selectedOrder.order_id, "accept", Object.keys(this.state.unavailableItems)],
                kwargs: {},
            });
            this._removeFromQueue(this.state.selectedOrder.order_id);
        } catch (e) {
            this.state.error = "Failed to accept order. Please try again.";
        }
        this.state.submitting = false;
    }

    async onReject() {
        if (this.state.submitting || !this.state.selectedOrder) return;
        this.state.submitting = true;
        this.state.error = null;
        try {
            await this.rpc("/web/dataset/call_kw", {
                model: "uva.order.service",
                method: "process_staff_action",
                args: [this.state.selectedOrder.order_id, "reject", []],
                kwargs: {},
            });
            this._removeFromQueue(this.state.selectedOrder.order_id);
        } catch (e) {
            this.state.error = "Failed to reject order. Please try again.";
        }
        this.state.submitting = false;
    }
}
