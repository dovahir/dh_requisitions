# -*- coding: utf-8 -*-
from datetime import datetime
from odoo import api, fields, models, _


class RequisitionDashboardKpi(models.AbstractModel):
    _name = 'requisition.dashboard.kpi'
    _description = 'Métricas y KPIs del Tablero de Requisiciones'

    @api.model
    def get_dashboard_data(self) -> dict:
        """Calcula y devuelve métricas estructuradas según el perfil del usuario."""
        user = self.env.user
        today = fields.Date.context_today(self)

        is_manager = user.has_group('dh_requisitions.group_requisitions_manager')
        is_approver = user.has_group('dh_requisitions.group_requisitions_approver')
        is_requester = user.has_group('dh_requisitions.group_requisitions_requester')

        # Prioridad de roles: Gestor > Aprobador > Solicitante
        if is_manager:
            return self._get_manager_dashboard(user, today)
        elif is_approver:
            return self._get_approver_dashboard(user, today)
        elif is_requester:
            return self._get_requester_dashboard(user, today)
        else:
            # Respaldo de solo lectura o consulta básica
            return self._get_requester_dashboard(user, today)

    def _get_manager_dashboard(self, user, today) -> dict:
        """Dashboard para Gestores: enfoque en atender, abastecer y surtir requisiciones."""
        Req = self.env['requisitions'].with_context(active_test=False)

        # 1. Aprobadas por Atender (Cola de Surtido / Despacho)
        approved_count = Req.search_count([('state', '=', 'approved')])

        # 2. Urgentes en Abastecimiento (Prioridad alta activa)
        urgent_active_count = Req.search_count([
            ('priority', '=', 'urgent'),
            ('state', 'not in', ('done', 'cancel', 'rejected'))
        ])

        # 3. Por Autorizar (Visibilidad de solicitudes pendientes de visto bueno)
        to_approve_count = Req.search_count([('state', '=', 'to_approve')])

        # 4. En Proceso / Surtido Parcial
        in_progress_count = Req.search_count([('state', 'in', ('in_progress', 'partially_done'))])

        # 5. Suministradas en el Mes Actual
        first_day_month = today.replace(day=1)
        done_month_count = Req.search_count([
            ('state', '=', 'done'),
            ('date_created', '>=', fields.Datetime.to_string(datetime.combine(first_day_month, datetime.min.time())))
        ])

        # 6. Total de Requisiciones Activas en la Empresa
        total_active_count = Req.search_count([('state', 'not in', ('done', 'cancel', 'rejected'))])

        cards = [
            {
                'key': 'approved_pending',
                'title': _('Aprobadas por Atender'),
                'value': approved_count,
                'icon': 'fa-cubes',
                'color': 'warning' if approved_count > 0 else 'success',
                'badge': _('Cola de Surtido'),
                'action': 'dh_requisitions.action_requisitions',
                'context': {'search_default_filter_approved': 1},
            },
            {
                'key': 'urgent_active',
                'title': _('Urgentes en Abastecimiento'),
                'value': urgent_active_count,
                'icon': 'fa-bolt',
                'color': 'danger' if urgent_active_count > 0 else 'success',
                'badge': _('Alta Prioridad'),
                'action': 'dh_requisitions.action_requisitions',
                'domain': [('priority', '=', 'urgent'), ('state', 'not in', ('done', 'cancel', 'rejected'))],
            },
            {
                'key': 'to_approve',
                'title': _('Por Autorizar'),
                'value': to_approve_count,
                'icon': 'fa-gavel',
                'color': 'warning' if to_approve_count > 0 else 'info',
                'badge': _('En Espera'),
                'action': 'dh_requisitions.action_requisitions',
                'context': {'search_default_filter_to_approve': 1},
            },
            {
                'key': 'in_progress',
                'title': _('En Proceso de Surtido'),
                'value': in_progress_count,
                'icon': 'fa-refresh',
                'color': 'primary',
                'badge': _('En Tránsito / Parcial'),
                'action': 'dh_requisitions.action_requisitions',
                'domain': [('state', 'in', ('in_progress', 'partially_done'))],
            },
            {
                'key': 'done_month',
                'title': _('Suministradas del Mes'),
                'value': done_month_count,
                'icon': 'fa-check-circle',
                'color': 'success',
                'badge': _('Efectividad'),
                'action': 'dh_requisitions.action_requisitions',
                'context': {'search_default_filter_done': 1},
            },
            {
                'key': 'total_active',
                'title': _('Total Requisiciones Activas'),
                'value': total_active_count,
                'icon': 'fa-folder-open',
                'color': 'info',
                'badge': _('En Curso'),
                'action': 'dh_requisitions.action_requisitions',
                'context': {'search_default_filter_open': 1},
            },
        ]

        return {
            'user_name': user.name,
            'today': today.strftime('%d/%m/%Y'),
            'role_code': 'manager',
            'role_label': _('Gestor de Requisiciones — Abastecimiento y Surtido'),
            'can_create': True,
            'primary_action': {
                'label': _('Atender Aprobadas'),
                'icon': 'fa-check-square-o',
                'action': 'dh_requisitions.action_requisitions',
                'context': {'search_default_filter_approved': 1},
            },
            'cards': cards,
        }

    def _get_approver_dashboard(self, user, today) -> dict:
        """Dashboard para Líderes/Aprobadores: enfoque en autorizar y dar seguimiento a requisiciones."""
        Req = self.env['requisitions'].with_context(active_test=False)

        # 1. Por Autorizar (Bandeja crítica de decisión)
        to_approve_count = Req.search_count([('state', '=', 'to_approve')])

        # 2. Urgencias por Autorizar
        urgent_to_approve_count = Req.search_count([
            ('state', '=', 'to_approve'),
            ('priority', '=', 'urgent')
        ])

        # 3. Autorizadas en Seguimiento (ver si ya las surtieron o siguen pendientes)
        my_approved_in_progress = Req.search_count([
            ('approved_by_id', '=', user.id),
            ('state', 'in', ('approved', 'in_progress', 'partially_done'))
        ])

        # 4. Rechazadas por este líder (para auditar subsanaciones)
        my_rejected_count = Req.search_count([
            ('rejected_by_id', '=', user.id),
            ('state', '=', 'rejected')
        ])

        # 5. Total Gestionadas en el Periodo
        total_managed_count = Req.search_count([
            '|', ('approved_by_id', '=', user.id), ('rejected_by_id', '=', user.id)
        ])

        cards = [
            {
                'key': 'to_approve',
                'title': _('Por Autorizar'),
                'value': to_approve_count,
                'icon': 'fa-gavel',
                'color': 'danger' if to_approve_count > 0 else 'success',
                'badge': _('Acción Inmediata'),
                'action': 'dh_requisitions.action_requisitions',
                'context': {'search_default_filter_to_approve': 1},
            },
            {
                'key': 'urgent_to_approve',
                'title': _('Urgencias por Autorizar'),
                'value': urgent_to_approve_count,
                'icon': 'fa-bolt',
                'color': 'danger' if urgent_to_approve_count > 0 else 'info',
                'badge': _('Prioridad Alta'),
                'action': 'dh_requisitions.action_requisitions',
                'domain': [('state', '=', 'to_approve'), ('priority', '=', 'urgent')],
            },
            {
                'key': 'my_approved_in_progress',
                'title': _('Autorizadas en Seguimiento'),
                'value': my_approved_in_progress,
                'icon': 'fa-clock-o',
                'color': 'primary',
                'badge': _('Surtido Pendiente'),
                'action': 'dh_requisitions.action_requisitions',
                'domain': [('approved_by_id', '=', user.id), ('state', 'in', ('approved', 'in_progress', 'partially_done'))],
            },
            {
                'key': 'my_rejected',
                'title': _('Rechazadas con Motivo'),
                'value': my_rejected_count,
                'icon': 'fa-times-circle',
                'color': 'secondary' if my_rejected_count == 0 else 'warning',
                'badge': _('En Corrección'),
                'action': 'dh_requisitions.action_requisitions',
                'domain': [('rejected_by_id', '=', user.id), ('state', '=', 'rejected')],
            },
            {
                'key': 'total_managed',
                'title': _('Total Gestionadas'),
                'value': total_managed_count,
                'icon': 'fa-check-square-o',
                'color': 'info',
                'badge': _('Histórico'),
                'action': 'dh_requisitions.action_requisitions',
                'domain': ['|', ('approved_by_id', '=', user.id), ('rejected_by_id', '=', user.id)],
            },
        ]

        return {
            'user_name': user.name,
            'today': today.strftime('%d/%m/%Y'),
            'role_code': 'approver',
            'role_label': _('Líder / Aprobador — Autorizaciones y Seguimiento'),
            'can_create': True,
            'primary_action': {
                'label': _('Revisar por Autorizar'),
                'icon': 'fa-gavel',
                'action': 'dh_requisitions.action_requisitions',
                'context': {'search_default_filter_to_approve': 1},
            },
            'cards': cards,
        }

    def _get_requester_dashboard(self, user, today) -> dict:
        """Dashboard para Solicitantes: enfoque en captura y seguimiento de solicitudes departamentales."""
        # Se aprovecha la regla de registro (ir.rule) departamental del usuario
        Req = self.env['requisitions'].with_context(active_test=False)

        # 1. Mis Borradores (en edición)
        draft_count = Req.search_count([('state', '=', 'draft')])

        # 2. Por Aprobar (enviadas a visto bueno)
        to_approve_count = Req.search_count([('state', '=', 'to_approve')])

        # 3. Aprobadas / En Proceso de Surtido
        approved_in_progress_count = Req.search_count([('state', 'in', ('approved', 'in_progress', 'partially_done'))])

        # 4. Suministradas (cumplidas)
        done_count = Req.search_count([('state', '=', 'done')])

        # 5. Rechazadas (requieren atención / corrección)
        rejected_count = Req.search_count([('state', '=', 'rejected')])

        cards = [
            {
                'key': 'draft',
                'title': _('Mis Borradores'),
                'value': draft_count,
                'icon': 'fa-pencil-square-o',
                'color': 'info',
                'badge': _('Por Enviar'),
                'action': 'dh_requisitions.action_requisitions',
                'context': {'search_default_filter_draft': 1},
            },
            {
                'key': 'to_approve',
                'title': _('Por Aprobar'),
                'value': to_approve_count,
                'icon': 'fa-clock-o',
                'color': 'warning',
                'badge': _('En Revisión'),
                'action': 'dh_requisitions.action_requisitions',
                'context': {'search_default_filter_to_approve': 1},
            },
            {
                'key': 'approved_in_progress',
                'title': _('Aprobadas / En Proceso'),
                'value': approved_in_progress_count,
                'icon': 'fa-cogs',
                'color': 'primary',
                'badge': _('En Surtido'),
                'action': 'dh_requisitions.action_requisitions',
                'domain': [('state', 'in', ('approved', 'in_progress', 'partially_done'))],
            },
            {
                'key': 'done',
                'title': _('Suministradas'),
                'value': done_count,
                'icon': 'fa-check-circle',
                'color': 'success',
                'badge': _('Completadas'),
                'action': 'dh_requisitions.action_requisitions',
                'context': {'search_default_filter_done': 1},
            },
            {
                'key': 'rejected',
                'title': _('Rechazadas'),
                'value': rejected_count,
                'icon': 'fa-times-circle',
                'color': 'danger' if rejected_count > 0 else 'secondary',
                'badge': _('Subsanar') if rejected_count > 0 else _('Sin Rechazos'),
                'action': 'dh_requisitions.action_requisitions',
                'context': {'search_default_filter_rejected': 1},
            },
        ]

        return {
            'user_name': user.name,
            'today': today.strftime('%d/%m/%Y'),
            'role_code': 'requester',
            'role_label': _('Solicitante — Mis Requisiciones Departamentales'),
            'can_create': True,
            'primary_action': {
                'label': _('Nueva Requisición'),
                'icon': 'fa-plus',
                'action': 'create_new',
            },
            'cards': cards,
        }
