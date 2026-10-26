# -*- coding: utf-8 -*-
{
    "name": "Requisiciones",
    "version": "19.0.1.0.0",
    "category": "Supply Chain",
    "summary": "Solicitud, aprobación y seguimiento de requisiciones de materiales, equipos y servicios.",
    "description": """
Requisiciones de Personal
=========================
Sistema para la gestión y seguimiento de solicitudes internas de materiales, herramientas, equipos y servicios.

Características principales:
- Registro de solicitudes organizado por materiales, herramientas, equipos y servicios.
- Flujo claro de aprobación con motivos justificados en rechazos y cancelaciones.
- Control de cantidades solicitadas, pendientes y efectivamente suministradas.
- Solicitudes de suministro entre almacenes para abastecer obras y proyectos.
- Configuración de rutas y traslados de material entre almacenes.
- Consolidación de solicitudes a cotizaciones de compra con seguimiento de entregas y cancelaciones.
- Gestión de partidas pendientes de envío a obra, división de cantidades y sugerencia de compra por desabasto en base.
- Matriz de trazabilidad y asignación de suministro con control de entregas, devoluciones y cancelación de cantidad.
- Asistente interactivo de solicitud de compra con selección granular de partidas y priorización simplificada.
- Cancelación unificada con motivo justificado obligatorio en todos los estados y afectación integral en cascada.
    """,
    "author": "Aldahir",
    "maintainer": "Aldahir",
    "website": "https://github.com/dovahir/dh_requisitions.git",
    "depends": ["base", "mail", "hr", "stock", "purchase", "supply_chain_aio"],
    "data": [
        "security/requisitions_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "views/actions.xml",
        "views/stock_picking_type_views.xml",
        "views/stock_warehouse_views.xml",
        "wizard/requisition_warehouse_setup_wizard_views.xml",
        "views/res_config_settings_views.xml",
        "views/requisition_supply_request_views.xml",
        "views/requisition_purchase_request_line_views.xml",
        "views/requisitions_views.xml",
        "views/requisitions_line_views.xml",
        "views/product_category_views.xml",
        "views/stock_picking_views.xml",
        "views/purchase_order_views.xml",
        "wizard/requisition_reject_wizard_views.xml",
        "wizard/requisition_cancel_wizard_views.xml",
        "wizard/requisition_delivery_wizard_views.xml",
        "wizard/requisition_supply_request_wizard_views.xml",
        "wizard/requisition_purchase_request_wizard_views.xml",
        "wizard/requisition_supply_request_block_wizard_views.xml",
        "wizard/requisition_supply_request_cancel_wizard_views.xml",
        "wizard/requisition_supply_request_line_split_wizard_views.xml",
        "wizard/requisition_supply_request_add_pending_wizard_views.xml",
        "wizard/requisition_supply_request_assign_pending_wizard_views.xml",
        "wizard/requisition_purchase_request_add_to_rfq_wizard_views.xml",
        "wizard/requisition_allocation_cancel_wizard_views.xml",
        "views/requisition_allocation_views.xml",
        "views/menus.xml",
        "report/paper_format.xml",
        "report/requisition_supply_transfer_report.xml",
    ],


    "assets": {},
    "license": "LGPL-3",
    "installable": True,
    "auto_install": False,
    "application": True
}
