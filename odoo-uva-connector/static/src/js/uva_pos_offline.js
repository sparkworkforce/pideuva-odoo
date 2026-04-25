/** @odoo-module **/

import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { subscribePosChannel, unsubscribePosChannel } from "./uva_bus_compat";

const OFFLINE_QUEUE_KEY = "uva_offline_queue";
const RECONNECT_DISPLAY_MS = 3000;
const MAX_OFFLINE_QUEUE = 100;

export class UvaPosOffline extends Component {
    static template = "odoo_uva_connector.UvaPosOffline";

    setup() {
        this.busService = useService("bus_service");
        this.rpc = useService("rpc");
        this.state = useState({ offline: !navigator.onLine, reconnected: false });

        this._busWrapper = null;
        this._onOnline = () => this._handleOnline();
        this._onOffline = () => { this.state.offline = true; };

        onMounted(() => {
            window.addEventListener("online", this._onOnline);
            window.addEventListener("offline", this._onOffline);
            this._busWrapper = subscribePosChannel(
                this.busService, "uva_new_order", this._cacheIfOffline.bind(this)
            );
        });

        onWillUnmount(() => {
            window.removeEventListener("online", this._onOnline);
            window.removeEventListener("offline", this._onOffline);
            unsubscribePosChannel(this.busService, "uva_new_order", this._busWrapper);
        });
    }

    _cacheIfOffline(payload) {
        if (!this.state.offline) return;
        try {
            const queue = JSON.parse(localStorage.getItem(OFFLINE_QUEUE_KEY) || "[]");
            if (queue.length >= MAX_OFFLINE_QUEUE) {
                queue.shift(); // evict oldest
            }
            queue.push(payload);
            localStorage.setItem(OFFLINE_QUEUE_KEY, JSON.stringify(queue));
        } catch (_e) {
            console.warn("UvaPosOffline: localStorage full or unavailable");
        }
    }

    async _handleOnline() {
        this.state.offline = false;
        this.state.reconnected = true;
        await this._replayQueue();
        setTimeout(() => { this.state.reconnected = false; }, RECONNECT_DISPLAY_MS);
    }

    async _replayQueue() {
        let queue;
        try {
            queue = JSON.parse(localStorage.getItem(OFFLINE_QUEUE_KEY) || "[]");
        } catch (_e) {
            queue = [];
        }
        localStorage.removeItem(OFFLINE_QUEUE_KEY);
        // Re-emit cached orders as bus events so the POS popup shows them for staff review
        // instead of auto-accepting (staff must explicitly accept after reviewing)
        for (const order of queue) {
            this.busService.trigger("uva_new_order", order);
        }
    }
}
