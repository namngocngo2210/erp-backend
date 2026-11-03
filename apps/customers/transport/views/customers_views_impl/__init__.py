from .customer_crud_view import (
    bulk_delete_customers,
    list_create_customers,
    update_delete_customer,
)
from .customer_profile_view import (
    customer_order_aggregate,
    customer_orders,
    list_customer_profiles,
)
from .loyalty_view import (
    list_loyal_customers,
    list_segment_categories,
    update_loyal_customer_note,
)
from .resale_tracking_view import (
    create_resale_order_note,
    list_resale_tracking,
    update_customer_resale_note,
    update_resale_appointment,
    update_resale_contact,
)

__all__ = [
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
