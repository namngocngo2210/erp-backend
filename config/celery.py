import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("desi_order")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
# apps.common không nằm trong INSTALLED_APPS (package tiện ích, không có models)
# → autodiscover mặc định bỏ qua; khai báo tường minh để nạp ops_heartbeat.
app.autodiscover_tasks(["apps.common"])
