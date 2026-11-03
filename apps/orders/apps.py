from django.apps import AppConfig


class OrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orders"

    def ready(self):
        # Import trong ready(): module subscriber đụng model, nạp ở top-level sẽ chạy
        # trước khi app registry sẵn sàng.
        from apps.orders import adapters, subscribers

        subscribers.register()
        # Cắm hiện thực vào registry của app tầng DƯỚI (channels_core) — xem
        # apps/orders/adapters.py và apps/channels_core/contracts.py.
        adapters.register()
        # Cắm serializer transport vào port của application (row realtime + validate
        # landing ingest) — application không import transport (audit 2026-09-23).
        from apps.orders.transport.serializers import port_bindings

        port_bindings.bind()

        # Đăng ký định nghĩa export vào engine. Import trong ready() vì module đụng
        # model và queryset — nạp ở top-level sẽ chạy trước khi app registry sẵn sàng.
        from apps.orders import exports_definitions  # noqa: F401

