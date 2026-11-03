"""Adapter phía orders cho các port mà app tầng DƯỚI khai.

Đối xứng với ``apps/kiotviet/adapters.py``, chỉ đổi vai: ở đây `orders` là app
tầng trên nên được phép import xuống `channels_core`, và nó cắm hiện thực vào
registry của `channels_core`. Đăng ký tại ``OrdersConfig.ready()``.

Method import LƯỜI (tại thời điểm gọi) vì cùng lý do bên kiotviet: ``ready()``
không kéo cả cây model/contract vào lúc khởi động, và test patch thẳng module
nguồn vẫn ăn vào call qua port.
"""

from __future__ import annotations


class OrderBroadcastAdapter:
    """Hiện thực ``apps.channels_core.contracts.OrderBroadcastPort``."""

    def validate_event_payload(self, payload: dict) -> dict | None:
        from apps.orders.contracts import OrderEventContract, OrderEventContractError

        try:
            return OrderEventContract.validate_payload(payload).to_payload()
        except OrderEventContractError:
            # Nuốt exception TẠI BIÊN: consumer chỉ cần biết "bỏ qua event này",
            # không cần biết kiểu lỗi của orders.
            return None

    def order_belongs_to_user(self, order_id: int, user_id) -> bool:
        if not user_id:
            return False
        from apps.orders.models import Order

        return Order.objects.filter(pk=order_id, staff_id=user_id).exists()

    def resolve_order_channel_code(self, order_id: int) -> str | None:
        from apps.orders.models import Order

        return (
            Order.objects.filter(pk=order_id)
            .values_list("channel__code", flat=True)
            .first()
        )


def register() -> None:
    """Đăng ký toàn bộ adapter của orders vào registry các app tầng dưới."""
    from apps.channels_core.contracts import register_order_broadcast_port

    register_order_broadcast_port(OrderBroadcastAdapter())
