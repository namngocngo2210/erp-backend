from django.urls import path

from apps.pancake.transport.views import (
    connection,
    list_leads,
    list_pages,
    retry_order,
    staff_maps,
    sync_pages,
    update_page,
)

urlpatterns = [
    path("connection", connection, name="pancake-connection"),
    path("pages", list_pages, name="pancake-pages"),
    path("pages/sync", sync_pages, name="pancake-pages-sync"),
    path("pages/<int:pk>", update_page, name="pancake-page"),
    path("staff-maps", staff_maps, name="pancake-staff-maps"),
    path("leads", list_leads, name="pancake-leads"),
    path("leads/<int:pk>/retry-order", retry_order, name="pancake-lead-retry-order"),
]
