"""Port hoá đơn KiotViet của app orders (trả nợ BE-2, audit 2026-07).

``orders/invoice.py`` (in hoá đơn + phiếu giao) cần dữ liệu hoá đơn từ KiotViet
nhưng theo contract phân tầng (``.importlinter``) orders KHÔNG được import service
của app kiotviet. Chiều gọi ở đây là request/response (orders hỏi — KiotViet trả),
không phải side-effect, nên event bus không hợp: orders khai protocol tại đây và
resolve qua registry; app kiotviet đăng ký adapter cụ thể lúc
``KiotVietConfig.ready()`` — mirror đúng cách chiều ngược lại wiring subscriber
trong ``OrdersConfig.ready()``.

Quy ước: dữ liệu qua port là dict/primitive hoặc object HTTP-response tối giản
(status_code/text/json) — không lộ chi tiết hạ tầng kiotviet sang orders.
"""

from __future__ import annotations

from typing import Any, Protocol


class HttpResponseLike(Protocol):
    """Bề mặt tối thiểu của một HTTP response mà orders cần đọc."""

    status_code: int
    text: str

    def json(self) -> Any: ...


class KiotVietInvoicePort(Protocol):
    """Những gì orders cần từ KiotViet để in hoá đơn / phiếu giao hàng."""

    def get_invoice_by_code(self, *, invoice_code: str, order: Any, channel: Any) -> dict:
        """Trả về payload hoá đơn (Public API) theo mã hoá đơn."""
        ...

    def is_koc_non_cod_order(self, order: Any) -> bool:
        """Đơn kênh KOC thuộc loại không được thu tiền người nhận (COD in phiếu = 0)."""
        ...

    def web_request(
        self,
        *,
        order: Any,
        channel: Any,
        method: str,
        endpoint: str,
        params: dict | None = None,
    ) -> HttpResponseLike:
        """Gọi KiotViet Web API đã xác thực; orders tự đọc status/json như trước."""
        ...


_port: KiotVietInvoicePort | None = None


def register_kiotviet_invoice_port(port: KiotVietInvoicePort) -> None:
    """App kiotviet gọi hàm này ở ``AppConfig.ready()`` để cắm adapter."""
    global _port
    _port = port


def kiotviet_invoice_port() -> KiotVietInvoicePort:
    """Resolve adapter đã đăng ký; lỗi rõ ràng nếu wiring chưa chạy."""
    if _port is None:
        raise RuntimeError(
            "KiotVietInvoicePort chưa được đăng ký — "
            "apps.kiotviet.apps.KiotVietConfig.ready() phải chạy trước "
            "(kiotviet có trong INSTALLED_APPS?)."
        )
    return _port
