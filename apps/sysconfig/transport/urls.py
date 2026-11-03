from django.urls import path

from apps.sysconfig.transport.views import system_settings, system_settings_history

urlpatterns = [
    path("", system_settings, name="system-settings"),
    path("history/", system_settings_history, name="system-settings-history"),
]
