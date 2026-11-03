from django.apps import AppConfig


class WarrantyCodesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.warranty_codes"
    verbose_name = "Mã bảo hành"

    def ready(self):
        # Đăng ký định nghĩa export vào engine. Import trong ready() vì module đụng
        # model và queryset — nạp ở top-level sẽ chạy trước khi app registry sẵn sàng.
        from apps.warranty_codes import exports_definitions  # noqa: F401
