from django.urls import path

from .views import (
    bulk_delete_customers,
    customer_order_aggregate,
    customer_orders,
    list_create_customers,
    list_customer_profiles,
    list_loyal_customers,
    list_resale_tracking,
    list_segment_categories,
    create_resale_order_note,
    update_resale_appointment,
    update_resale_contact,
    update_customer_resale_note,
    update_delete_customer,
    update_loyal_customer_note,
)

urlpatterns = [
    path("", list_create_customers, name="customers-list-create"),
    path("bulk-delete", bulk_delete_customers, name="customers-bulk-delete"),
    path("loyal", list_loyal_customers, name="customers-loyal"),
    path("loyal/segments", list_segment_categories, name="customers-loyal-segments"),
    path("loyal/<int:customer_id>/note", update_loyal_customer_note, name="customers-loyal-note"),
    path("resale", list_resale_tracking, name="customers-resale-tracking"),
    path("resale/<int:order_id>/contact", update_resale_contact, name="customers-resale-contact"),
    path("resale/<int:order_id>/appointment", update_resale_appointment, name="customers-resale-appointment"),
    path("resale/<int:order_id>/order", create_resale_order_note, name="customers-resale-order"),
    path("profiles", list_customer_profiles, name="customers-profiles"),
    path("<int:customer_id>", update_delete_customer, name="customers-update-delete"),
    path("<int:customer_id>/orders", customer_orders, name="customers-orders"),
    path("<int:customer_id>/orders/aggregate", customer_order_aggregate, name="customers-orders-aggregate"),
    path("<int:customer_id>/resale-note", update_customer_resale_note, name="customers-resale-note"),
]
