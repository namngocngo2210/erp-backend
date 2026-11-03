from django.urls import path

from apps.pancake_pos.transport.views import (
    health,
    initial_stock_status,
    pancake_warehouses,
    start_initial_stock,
    warehouse_links,
    list_links,
    list_order_links,
    list_sync_runs,
    order_refs,
    order_routing,
    retry_order_link,
    retry_link,
    start_sync,
    sync_run_detail,
    sync_status,
)

from apps.pancake_pos.transport.delivery_views import delivery_mismatches, delivery_stuck, order_delivery
from apps.pancake_pos.transport.webhook_views import (
    inbound_orders,
    pancake_pos_webhook,
    resolve_inbound_order,
    webhook_configure,
    webhook_retry_errors,
    webhook_status,
)

urlpatterns = [
    path("sync", start_sync, name="pancake-pos-sync"),
    path("sync-runs", list_sync_runs, name="pancake-pos-sync-runs"),
    path("sync-runs/<int:pk>", sync_run_detail, name="pancake-pos-sync-run"),
    path("links", list_links, name="pancake-pos-links"),
    path("links/<int:pk>/retry", retry_link, name="pancake-pos-link-retry"),
    path("status", sync_status, name="pancake-pos-status"),
    path("health", health, name="pancake-pos-health"),
    path("warehouse-links", warehouse_links, name="pancake-pos-warehouse-links"),
    path("pancake-warehouses", pancake_warehouses, name="pancake-pos-remote-warehouses"),
    path("initial-stock", start_initial_stock, name="pancake-pos-initial-stock"),
    path("initial-stock/status", initial_stock_status, name="pancake-pos-initial-stock-status"),
    path("order-links", list_order_links, name="pancake-pos-order-links"),
    path("order-links/<int:pk>/retry", retry_order_link, name="pancake-pos-order-link-retry"),
    path("orders/<int:order_id>/refs", order_refs, name="pancake-pos-order-refs"),
    path("orders/<int:order_id>/routing", order_routing, name="pancake-pos-order-routing"),
    # Trạng thái vận chuyển thực từ Pancake (MR D) — chỉ đọc.
    path("orders/<int:order_id>/delivery", order_delivery, name="pancake-pos-order-delivery"),
    path("delivery/stuck", delivery_stuck, name="pancake-pos-delivery-stuck"),
    path("delivery/mismatches", delivery_mismatches, name="pancake-pos-delivery-mismatches"),
    # Webhook Pancake → CRM (two-way-sync B1). `webhook` = endpoint Pancake gọi (header bí mật).
    path("webhook", pancake_pos_webhook, name="pancake-pos-webhook"),
    path("webhook/status", webhook_status, name="pancake-pos-webhook-status"),
    path("webhook/configure", webhook_configure, name="pancake-pos-webhook-configure"),
    path("webhook/retry-errors", webhook_retry_errors, name="pancake-pos-webhook-retry-errors"),
    path("inbound-orders", inbound_orders, name="pancake-pos-inbound-orders"),
    path("inbound-orders/<int:pk>/resolve", resolve_inbound_order, name="pancake-pos-inbound-order-resolve"),
]
