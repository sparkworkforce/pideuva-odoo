/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

class UvaMapField extends Component {
    static template = "odoo_uva_connector.UvaMapField";
    static props = { ...standardFieldProps };

    get mapUrl() {
        return this.props.record.data[this.props.name] || "";
    }
}

registry.category("fields").add("uva_map", {
    component: UvaMapField,
    supportedTypes: ["char"],
});
