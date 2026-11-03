"""Port cập nhật trạng thái giao hàng trên KiotViet (trả nợ Lô 5, xem
``docs/import-layering-debt-paydown-2026-08.md``).

``application/services/return_order_sync.py`` báo ngược trạng thái sang KiotViet
khi hàng hoàn về kho. Theo contract phân tầng, `inventory` không được import
service của `kiotviet`.

Cùng cơ chế với các port của `orders`: inventory khai ``Protocol`` tại đây, app
kiotviet đăng ký adapter lúc ``KiotVietConfig.ready()``.
"""

from __future__ import annotations

from typing import Any, Protocol


class KiotVietDeliveryPort(Protocol):
    """Ghi trạng thái giao hàng ngược lên KiotViet."""

    def update_invoice_delivery_status(
        self,
        *,
        invoice_id: Any,
        status: Any,
        order: Any,
        channel: Any,
    ) -> Any:
        """Đẩy trạng thái giao hàng của một hoá đơn.

        Lỗi phía KiotViet KHÔNG được chặn nghiệp vụ kho — caller đã bọc
        ``try/except`` và chỉ ghi log cảnh báo. Port giữ nguyên giao kèo đó:
        nó không nuốt exception, để caller tự quyết.
        """
        ...


_port: KiotVietDeliveryPort | None = None


def register_kiotviet_delivery_port(port: KiotVietDeliveryPort) -> None:
    """App kiotviet gọi hàm này ở ``AppConfig.ready()`` để cắm adapter."""
    global _port
    _port = port


def kiotviet_delivery_port() -> KiotVietDeliveryPort:
    """Resolve adapter đã đăng ký; lỗi rõ ràng nếu wiring chưa chạy."""
    if _port is None:
        raise RuntimeError(
            "KiotVietDeliveryPort chưa được đăng ký — "
            "apps.kiotviet.apps.KiotVietConfig.ready() phải chạy trước "
            "(kiotviet có trong INSTALLED_APPS?)."
        )
    return _port
