"""Route cho prefix /api/sale/ — tách khỏi urls.py (mount ở /api/orders/) vì trang
Danh sách contact (Sale) không thuộc namespace /orders. Xem config/urls.py.
"""
from django.urls import path

from .views import contact_activities, sale_contacts_list

urlpatterns = [
    path("contacts", sale_contacts_list, name="sale-contacts-list"),
    path(
        "contacts/<int:customer_id>/activities",
        contact_activities,
        name="sale-contact-activities",
    ),
]
