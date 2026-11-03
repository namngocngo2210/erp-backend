from django.urls import path

from .consumers import CRMConsumer

websocket_urlpatterns = [
    path("ws/crm/", CRMConsumer.as_asgi()),
]

