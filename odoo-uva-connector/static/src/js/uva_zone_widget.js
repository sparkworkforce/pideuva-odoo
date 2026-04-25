/** @odoo-module **/

import { Component, onMounted, onWillUnmount, onPatched, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const DEFAULT_LAT = 18.4655;
const DEFAULT_LNG = -66.1057;
const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
const LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";

let leafletLoaded = false;

function loadLeaflet() {
    if (leafletLoaded || window.L) {
        leafletLoaded = true;
        return Promise.resolve();
    }
    return new Promise((resolve) => {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = LEAFLET_CSS;
        document.head.appendChild(link);
        const script = document.createElement("script");
        script.src = LEAFLET_JS;
        script.onload = () => { leafletLoaded = true; resolve(); };
        script.onerror = () => { resolve(); }; // fail gracefully
        document.head.appendChild(script);
        setTimeout(resolve, 10000); // 10s timeout
    });
}

class UvaZoneWidget extends Component {
    static template = "odoo_uva_connector.UvaZoneWidget";
    static props = { ...standardFieldProps };

    setup() {
        this.mapRef = useRef("mapContainer");
        this._map = null;
        this._circle = null;
        onMounted(async () => {
            await loadLeaflet();
            this._initMap();
        });
        onPatched(() => {
            this._updateMap();
        });
        onWillUnmount(() => {
            if (this._map) { this._map.remove(); this._map = null; }
        });
    }

    _initMap() {
        const data = this.props.record.data;
        const lat = data.store_lat || DEFAULT_LAT;
        const lng = data.store_lng || DEFAULT_LNG;
        const radius = (data.delivery_zone_radius || 5.0) * 1000;
        const el = this.mapRef.el;
        if (!el || !window.L) return;
        this._map = L.map(el).setView([lat, lng], 13);
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "&copy; OpenStreetMap",
        }).addTo(this._map);
        L.marker([lat, lng]).addTo(this._map);
        this._circle = L.circle([lat, lng], { radius, color: "#1565c0", fillOpacity: 0.15 }).addTo(this._map);
        this._map.fitBounds(this._circle.getBounds());
    }

    _updateMap() {
        if (!this._map || !this._circle || !window.L) return;
        const data = this.props.record.data;
        const lat = data.store_lat || DEFAULT_LAT;
        const lng = data.store_lng || DEFAULT_LNG;
        const radius = (data.delivery_zone_radius || 5.0) * 1000;
        this._circle.setLatLng([lat, lng]);
        this._circle.setRadius(radius);
        this._map.panTo([lat, lng]);
    }
}

registry.category("fields").add("uva_zone_map", {
    component: UvaZoneWidget,
    supportedTypes: ["float"],
});
