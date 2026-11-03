"""Port luồng đơn KiotViet của app orders (trả nợ Lô 2, xem
``docs/import-layering-debt-paydown-2026-08.md``).

Ba nơi trong ``orders`` cần KiotViet mà contract phân tầng không cho import:

- ``transport/views/.../order_crud_view.py`` — liệt kê kênh KiotViet cho dropdown
  và lấy danh sách nhà vận chuyển KShip của một kênh;
- ``infrastructure/tasks/.../ingest_job_tasks.py`` — chạy hành động đẩy đơn sang
  KiotViet (``KiotVietService.execute_action``);
- ``infrastructure/tasks/.../auto_sync_invoice_full.py`` — duyệt các kênh đang
  hoạt động để đồng bộ hoá đơn.

Cùng cơ chế với ``kiotviet_invoice_port``: orders khai ``Protocol`` tại đây,
app kiotviet đăng ký adapter cụ thể lúc ``KiotVietConfig.ready()``.

Quy ước dữ liệu qua port: dict/primitive, hoặc **instance model kênh** cho các
API cần truyền tiếp vào ORM filter (``Order.objects.filter(kiotviet_channel=...)``).
Kênh được đối xử như một handle mờ (opaque) phía orders — orders chỉ đọc ``id``
và ``name``, không đụng vào nội tạng khác.
"""

from __future__ import annotations

from typing import Any, Protocol
from collections.abc import Iterator


class KiotVietOrderFlowPort(Protocol):
    """Những gì orders cần từ KiotViet cho luồng tạo/sửa/đồng bộ đơn."""

    def list_channel_options(self, *, offset: int, limit: int) -> list[dict]:
        """Trang danh sách kênh còn sống, sắp xếp theo (created_at, id).

        Trả về dict thuần với đúng các khoá mà endpoint dropdown cần:
        ``id``, ``name``, ``branch_id``, ``branch_ids``, ``branch_names``.
        """
        ...

    def iter_active_channels(self) -> Iterator[Any]:
        """Duyệt lười (streaming) các kênh còn sống, sắp xếp theo ``id``."""
        ...

    def get_kship_options(self, channel: Any) -> dict:
        """Nhà vận chuyển + dịch vụ + quy định xem hàng + mục hãng bắt buộc của một kênh.

        Ném ``ValueError`` khi cấu hình kênh không hợp lệ — caller đã bắt sẵn.
        """
        ...

    def execute_order_action(
        self,
        order: Any,
        action: str,
        *,
        actor_id: Any = None,
        provider_code: str | None = None,
        service_code: str | None = None,
        kship_note: str = "",
        shipper_note: str = "",
        sender_address_id: Any = None,
        service_extra: dict | None = None,
    ) -> dict:
        """Chạy một hành động KiotViet trên đơn; trả payload kết quả."""
        ...


_port: KiotVietOrderFlowPort | None = None


def register_kiotviet_order_flow_port(port: KiotVietOrderFlowPort) -> None:
    """App kiotviet gọi hàm này ở ``AppConfig.ready()`` để cắm adapter."""
    global _port
    _port = port


def kiotviet_order_flow_port() -> KiotVietOrderFlowPort:
    """Resolve adapter đã đăng ký; lỗi rõ ràng nếu wiring chưa chạy."""
    if _port is None:
        raise RuntimeError(
            "KiotVietOrderFlowPort chưa được đăng ký — "
            "apps.kiotviet.apps.KiotVietConfig.ready() phải chạy trước "
            "(kiotviet có trong INSTALLED_APPS?)."
        )
    return _port
