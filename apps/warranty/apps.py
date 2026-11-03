from django.apps import AppConfig


class WarrantyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.warranty"

    def ready(self):
        from apps.warranty import subscribers

        subscribers.register()

        # Đăng ký định nghĩa export vào engine. Import trong ready() vì module đụng
        # model và queryset — nạp ở top-level sẽ chạy trước khi app registry sẵn sàng.
        from apps.warranty import exports_definitions  # noqa: F401
