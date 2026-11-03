"""Port xác minh hủy hóa đơn KiotViet (trả nợ Lô 3, xem
``docs/import-layering-debt-paydown-2026-08.md``).

Guard chặn xuất kho đơn đã hủy cần hỏi KiotViet xem hóa đơn còn sống không.
Hai nơi dùng: ``application/services/order_service_commands.py`` (guard xuất kho)
và ``transport/views/shipping_lookup_view.py`` (tra cứu vận đơn).

Khác hai port trước ở một điểm quan trọng: phụ thuộc này không chỉ là lời gọi mà
còn gồm **kiểu dữ liệu** — một enum kết quả và một exception. Nếu để orders dùng
enum/exception của kiotviet thì ranh giới chỉ là hình thức. Nên orders khai kiểu
của mình tại đây, adapter phía kiotviet dịch qua lại.

CẢNH BÁO cho người sửa sau: ``CancelVerdict`` được so sánh bằng ``is`` ở phía
orders (guard cố tình không tin verdict lạ từ caller). Đừng trả về chuỗi trần hay
enum của app khác — dù ``==`` vẫn đúng vì đây là ``str`` enum, ``is`` sẽ sai và
guard sẽ âm thầm đi nhánh khác.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol


class CancelVerdict(str, Enum):
    """Kết quả xác minh. Giá trị chuỗi trùng với ``CancelCheck`` phía kiotviet."""

    OK = "ok"
    CANCELLED = "cancelled"
    UNVERIFIABLE = "unverifiable"


class CancellationVerificationUnavailable(Exception):
    """Không hỏi được KiotViet (lỗi/timeout/rate-limit) → caller phải fail-closed.

    Adapter dịch ``VerificationUnavailable`` của kiotviet sang exception này, để
    orders bắt kiểu của chính mình.
    """


class KiotVietCancellationPort(Protocol):
    """Những gì orders cần để biết một đơn đã bị hủy hóa đơn hay chưa."""

    def verify_not_cancelled(self, order: Any) -> CancelVerdict:
        """Hỏi KiotViet trạng thái hóa đơn của đơn này.

        Ném ``CancellationVerificationUnavailable`` khi không xác minh được —
        caller phải chặn thao tác, không được coi như hợp lệ.
        """
        ...


_port: KiotVietCancellationPort | None = None


def register_kiotviet_cancellation_port(port: KiotVietCancellationPort) -> None:
    """App kiotviet gọi hàm này ở ``AppConfig.ready()`` để cắm adapter."""
    global _port
    _port = port


def kiotviet_cancellation_port() -> KiotVietCancellationPort:
    """Resolve adapter đã đăng ký; lỗi rõ ràng nếu wiring chưa chạy."""
    if _port is None:
        raise RuntimeError(
            "KiotVietCancellationPort chưa được đăng ký — "
            "apps.kiotviet.apps.KiotVietConfig.ready() phải chạy trước "
            "(kiotviet có trong INSTALLED_APPS?)."
        )
    return _port
