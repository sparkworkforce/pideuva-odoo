/** @odoo-module **/
import { Component, onWillStart, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { registry } from "@web/core/registry";

const REFRESH_INTERVAL = 60000; // 60 seconds

export class UvaDashboardAction extends Component {
    static template = "odoo_uva_connector.UvaDashboardAction";
    setup() {
        this.rpc = useService("rpc");
        this.action = useService("action");
        this.state = useState({ stats: {}, storeStats: [] });
        this._refreshTimer = null;
        onWillStart(async () => { await this.loadStats(); });
        onMounted(() => {
            this._refreshTimer = setInterval(() => this.loadStats(), REFRESH_INTERVAL);
        });
        onWillUnmount(() => {
            if (this._refreshTimer) clearInterval(this._refreshTimer);
        });
    }
    async loadStats() {
        const data = await this.rpc("/web/dataset/call_kw", {
            model: "uva.order.log", method: "get_dashboard_stats", args: [], kwargs: {},
        });
        this.state.stats = data;
        this.state.storeStats = data.store_stats || [];
    }
    openOrders() {
        this.action.doAction("odoo_uva_connector.action_uva_dashboard");
    }
    openAnalytics() {
        this.action.doAction("odoo_uva_connector.action_uva_analytics");
    }
    openFleetAnalytics() {
        this.action.doAction("odoo_uva_connector.action_uva_fleet_analytics");
    }
}
registry.category("actions").add("uva_dashboard", UvaDashboardAction);
