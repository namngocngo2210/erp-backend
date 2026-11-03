from django.urls import path

from apps.best_express.transport import api_views
from apps.best_express.transport.webhook_views import best_express_webhook

urlpatterns = [
    path("webhook", best_express_webhook, name="best-express-webhook"),
    path("accounts", api_views.list_best_accounts, name="best-express-accounts"),
    path("estimate-fee", api_views.estimate_best_fee, name="best-express-estimate-fee"),
    path("shipments", api_views.list_create_best_shipments, name="best-express-shipments"),
    path(
        "shipments/<int:shipment_id>",
        api_views.update_best_shipment,
        name="best-express-shipment-update",
    ),
    path(
        "shipments/<int:shipment_id>/cancel",
        api_views.cancel_best_shipment,
        name="best-express-shipment-cancel",
    ),
    path(
        "shipments/<int:shipment_id>/address",
        api_views.update_best_shipment_address,
        name="best-express-shipment-address",
    ),
    path("trace", api_views.trace_best_shipments, name="best-express-trace"),
    path("print-waybill", api_views.print_best_waybills, name="best-express-print-waybill"),
]
