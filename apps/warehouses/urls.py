from django.urls import path

from apps.warehouses.transport.views.warehouse_views import (
    list_create_warehouses,
    update_delete_warehouse,
)

urlpatterns = [
    path("", list_create_warehouses, name="warehouses-list-create"),
    path("<int:warehouse_id>", update_delete_warehouse, name="warehouses-update-delete"),
]
