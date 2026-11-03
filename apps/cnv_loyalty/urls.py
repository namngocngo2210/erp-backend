from django.urls import path

from apps.cnv_loyalty.transport import api_views
from apps.cnv_loyalty.transport.webhook_views import cnv_webhook

urlpatterns = [
    path("connect", api_views.cnv_connect, name="cnv-connect"),
    path("callback", api_views.cnv_callback, name="cnv-callback"),
    path("status", api_views.cnv_status, name="cnv-status"),
    # URL public CNV gọi vào — đổi đường dẫn là phải register lại webhook bên CNV.
    path("webhook", cnv_webhook, name="cnv-webhook"),
]
