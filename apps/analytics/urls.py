from django.urls import path

from apps.analytics.transport import views

urlpatterns = [
    path("coverage", views.coverage_view, name="analytics-coverage"),
    path("customers", views.customer_search_view, name="analytics-customer-search"),
    path(
        "customers/<int:customer_id>",
        views.customer_profile_view,
        name="analytics-customer-profile",
    ),
]
