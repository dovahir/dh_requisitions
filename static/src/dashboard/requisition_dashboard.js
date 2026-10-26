/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class RequisitionDashboard extends Component {
    static template = "dh_requisitions.RequisitionDashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            loaded: false,
            loading: false,
            data: {
                user_name: "",
                today: "",
                role_code: "requester",
                role_label: "",
                can_create: true,
                cards: [],
            },
        });

        onWillStart(async () => {
            await this.loadDashboard();
        });
    }

    async loadDashboard() {
        this.state.loading = true;
        try {
            const data = await this.orm.call(
                "requisition.dashboard.kpi",
                "get_dashboard_data",
                []
            );
            this.state.data = data;
            this.state.loaded = true;
        } catch (error) {
            console.error("[RequisitionDashboard] Error al cargar métricas:", error);
            this.notification.add(
                "No se pudieron cargar las métricas del tablero de requisiciones.",
                { type: "danger" }
            );
        } finally {
            this.state.loading = false;
        }
    }

    async openCard(card) {
        if (!card) return;
        const ctx = card.context || {};
        const extraDomain = card.domain || [];

        // Si la tarjeta define un dominio específico, se abre una acción de ventana con ese dominio
        if (extraDomain.length) {
            await this.action.doAction({
                type: "ir.actions.act_window",
                name: card.title,
                res_model: "requisitions",
                domain: extraDomain,
                context: ctx,
                views: [
                    [false, "list"],
                    [false, "kanban"],
                    [false, "form"],
                ],
                target: "current",
            });
            return;
        }

        // Si la tarjeta define una acción oficial por xmlid (respeta search_view_id y default filters)
        if (card.action) {
            await this.action.doAction(card.action, {
                additionalContext: ctx,
            });
            return;
        }

        // Respaldo genérico a la lista de requisiciones
        await this.action.doAction("dh_requisitions.action_requisitions", {
            additionalContext: ctx,
        });
    }

    async createNewRequisition() {
        await this.action.doAction({
            type: "ir.actions.act_window",
            name: "Nueva Requisición",
            res_model: "requisitions",
            views: [[false, "form"]],
            target: "current",
        });
    }

    async triggerPrimaryAction() {
        const primary = this.state.data?.primary_action;
        if (!primary) {
            await this.createNewRequisition();
            return;
        }

        if (primary.action === "create_new") {
            await this.createNewRequisition();
            return;
        }

        await this.action.doAction(primary.action, {
            additionalContext: primary.context || {},
        });
    }

    async openView(viewType) {
        await this.action.doAction({
            type: "ir.actions.act_window",
            name: "Requisiciones",
            res_model: "requisitions",
            views: [
                [false, viewType],
                [false, "form"],
            ],
            target: "current",
        });
    }

    async refresh() {
        await this.loadDashboard();
    }
}

registry.category("actions").add("dh_requisitions_dashboard", RequisitionDashboard);
