from django.apps import AppConfig


class CallcenterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.callcenter"
    verbose_name = "Call Center (Zenify)"

    def ready(self):
        # Đăng ký định nghĩa export vào engine. Import trong ready() vì module đụng
        # model và queryset — nạp ở top-level sẽ chạy trước khi app registry sẵn sàng.
        from apps.callcenter import exports_definitions  # noqa: F401
