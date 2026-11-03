"""
KiotViet app configuration.
"""

from django.apps import AppConfig


class KiotVietConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.kiotviet"
    verbose_name = "KiotViet Integration"

    def ready(self):
        # Import trong ready(): adapter đăng ký vào registry bên orders/contracts,
        # nạp ở top-level sẽ chạy trước khi app registry sẵn sàng.
        from apps.kiotviet import adapters

        adapters.register()
