/** @odoo-module **/

import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { subscribePosChannel, unsubscribePosChannel } from "./uva_bus_compat";

export class UvaPosHealthIndicator extends Component {
    static template = "odoo_uva_connector.UvaPosHealthIndicator";

    setup() {
        this.busService = useService("bus_service");
        this.state = useState({ status: "ok" });
        onMounted(() => {
            this._busWrapper = subscribePosChannel(
                this.busService, "uva_health_status", this._onHealth.bind(this)
            );
        });
        onWillUnmount(() => {
            unsubscribePosChannel(this.busService, "uva_health_status", this._busWrapper);
        });
    }

    _onHealth(payload) {
        this.state.status = payload.status || "down";
    }

    get dotColor() {
        return { ok: "green", degraded: "orange", down: "red" }[this.state.status] || "gray";
    }

    get label() {
        return { ok: "Connected", degraded: "Degraded", down: "Disconnected" }[this.state.status] || "Unknown";
    }
}
