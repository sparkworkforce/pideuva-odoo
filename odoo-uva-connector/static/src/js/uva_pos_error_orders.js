/** @odoo-module **/
import { Component, onMounted, onWillUnmount, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { subscribePosChannel, unsubscribePosChannel } from "./uva_bus_compat";

export class UvaPosErrorOrders extends Component {
    static template = "odoo_uva_connector.UvaPosErrorOrders";
    static props = { storeId: { type: Number } };

    setup() {
        this.rpc = useService("rpc");
        this.bus = useService("bus_service");
        this.state = useState({ orders: [], loading: false });
        this._busWrapper = null;

        onWillStart(() => this.loadErrors());
        onMounted(() => {
            this._busWrapper = subscribePosChannel(this.bus, "uva_new_order", () => this.loadErrors());
        });
        onWillUnmount(() => {
            unsubscribePosChannel(this.bus, "uva_new_order", this._busWrapper);
        });
    }

    async loadErrors() {
        this.state.loading = true;
        try {
            this.state.orders = await this.rpc("/web/dataset/call_kw", {
                model: "uva.order.log",
                method: "get_error_orders",
                args: [this.props.storeId],
                kwargs: {},
            });
        } finally {
            this.state.loading = false;
        }
    }

    async onRetry(orderId) {
        try {
            await this.rpc("/web/dataset/call_kw", {
                model: "uva.order.service",
                method: "action_retry_from_pos",
                args: [orderId],
                kwargs: {},
            });
        } catch (e) {
            console.error("UvaPosErrorOrders: retry failed", e);
        }
        await this.loadErrors();
    }

    async onReject(orderId) {
        try {
            await this.rpc("/web/dataset/call_kw", {
                model: "uva.order.service",
                method: "action_reject_from_pos",
                args: [orderId, "Rejected from POS"],
                kwargs: {},
            });
        } catch (e) {
            console.error("UvaPosErrorOrders: reject failed", e);
        }
        await this.loadErrors();
    }
}
