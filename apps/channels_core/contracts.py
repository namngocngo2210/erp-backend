"""Port mà `channels_core` cần từ app `orders` (trả nợ Lô 4, xem
``docs/import-layering-debt-paydown-2026-08.md``).

CHIỀU NGƯỢC với ba port trước. Theo contract phân tầng, `channels_core` nằm DƯỚI
`orders`, nên `consumers.py` không được import model/contract của orders. Vì thế
**channels_core khai port ở đây**, còn **orders đăng ký adapter** tại
``OrdersConfig.ready()`` (``apps/orders/adapters.py``) — đúng cơ chế mà
``kiotviet/adapters.py`` dùng, chỉ đổi vai.

Consumer WebSocket chạy trên MỌI client trong group ``crm_updates``, nên hai
method tra DB dưới đây nằm trên đường fallback đã bị ``broadcast_db_gate`` chặn
(sự cố 2026-07-29). Port không đổi tính chất đó: nó chỉ dời chỗ định nghĩa, không
thêm lần chạm DB nào.
"""

from __future__ import annotations

from typing import Protocol


class OrderBroadcastPort(Protocol):
    """Những gì consumer realtime cần biết về đơn hàng."""

    def validate_event_payload(self, payload: dict) -> dict | None:
        """Chuẩn hoá payload event đơn hàng.

        Trả dict đã validate, hoặc ``None`` nếu payload không hợp lệ — consumer
        chỉ việc bỏ qua event. Cố ý KHÔNG ném exception qua biên: kiểu exception
        của orders mà lọt sang channels_core thì ranh giới lại chỉ là hình thức.
        """
        ...

    def order_belongs_to_user(self, order_id: int, user_id: int | None) -> bool:
        """Đơn này có do user đó phụ trách không (scope "own")."""
        ...

    def resolve_order_channel_code(self, order_id: int) -> str | None:
        """Mã kênh của đơn (scope "channels")."""
        ...


_port: OrderBroadcastPort | None = None


def register_order_broadcast_port(port: OrderBroadcastPort) -> None:
    """App orders gọi hàm này ở ``AppConfig.ready()`` để cắm adapter."""
    global _port
    _port = port


def order_broadcast_port() -> OrderBroadcastPort:
    """Resolve adapter đã đăng ký; lỗi rõ ràng nếu wiring chưa chạy."""
    if _port is None:
        raise RuntimeError(
            "OrderBroadcastPort chưa được đăng ký — "
            "apps.orders.apps.OrdersConfig.ready() phải chạy trước "
            "(orders có trong INSTALLED_APPS?)."
        )
    return _port
