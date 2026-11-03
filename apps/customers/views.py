"""Facade module to preserve public imports while delegating to layered implementation."""

# `OrderService` được re-export vì test authz đang patch qua đường
# `apps.customers.views.OrderService.can_edit_channel` — facade phải giữ nguyên
# bề mặt public cũ của module.
from apps.orders.application.services.order_service_impl import OrderService

from .transport.views.customers_views_impl import (
    bulk_delete_customers,
    create_resale_order_note,
    customer_order_aggregate,
    customer_orders,
    list_create_customers,
    list_customer_profiles,
    list_loyal_customers,
    list_resale_tracking,
    list_segment_categories,
    update_customer_resale_note,
    update_delete_customer,
    update_loyal_customer_note,
    update_resale_appointment,
    update_resale_contact,
)

__all__ = [
    "OrderService",
    "bulk_delete_customers",
    "create_resale_order_note",
    "customer_order_aggregate",
    "customer_orders",
    "list_create_customers",
    "list_customer_profiles",
    "list_loyal_customers",
    "list_resale_tracking",
    "list_segment_categories",
    "update_customer_resale_note",
    "update_delete_customer",
    "update_loyal_customer_note",
    "update_resale_appointment",
    "update_resale_contact",
]
