"""Đơn đã có mã hoá đơn thì không sửa SẢN PHẨM và TÀI CHÍNH được nữa.

Mã hoá đơn (``Order.invoice_code``) = đơn đã được đẩy lên KiotViet và chốt hoá đơn ở đó.
Từ thời điểm này, dòng hàng và số tiền đã nằm trong sổ của KiotViet; sửa ở hệ thống mình
làm hai bên lệch nhau: trừ tồn sai, đối chiếu hoàn sai, lệch doanh thu.

Đây là mốc SỚM hơn mã vận đơn: luồng tạo vận đơn KShip bắt buộc phải có mã hoá đơn
trước, nên khoá theo hoá đơn bao trùm luôn giai đoạn đã có vận đơn.

Phạm vi khoá = đúng hai khối trên trang chi tiết đơn: "Sản phẩm & thông số hàng hoá" và
"Tài chính". Mọi thứ khác vẫn sửa được — khách, địa chỉ, kênh/sale/chi nhánh, mã đơn/hoá
đơn/vận đơn, phí vận chuyển, trạng thái, CRM, thu hồi, VAT — vì đó là những thông tin
thường xuyên phải vá sau khi đơn đã lên đường.

Module thuần: không import DRF/model, không I/O — chỉ đọc thuộc tính của order đã nạp.
Xem docs/invoice-code-edit-lock.md.
"""
from __future__ import annotations

from typing import Any
from collections.abc import Iterable

from .field_diff import changed_fields

# Key = tên field trong `validated_data` của OrderInlineUpdateSerializer.
INVOICE_LOCKED_FIELD_LABELS: dict[str, str] = {
    # Khối "Sản phẩm & thông số hàng hoá"
    "items": "danh sách sản phẩm",
    "sku": "SKU",
    "quantity": "số lượng",
    "weight": "khối lượng",
    "dimensions": "kích thước",
    # Khối "Tài chính"
    "total_amount": "tổng tiền",
    "discount_amount": "giảm giá",
    "booking_voucher_discount": "voucher booking",
    "other_price": "phụ phí khác",
    "customer_prepaid": "khách ứng trước",
    "total_after_discount": "sau giảm giá",
}

INVOICE_LOCKED_FIELDS: frozenset[str] = frozenset(INVOICE_LOCKED_FIELD_LABELS)


def is_invoice_locked(order: Any) -> bool:
    """Đơn đã có mã hoá đơn ⇒ khối sản phẩm + tài chính bị khoá."""
    return bool(str(getattr(order, "invoice_code", "") or "").strip())


def locked_field_changes(order: Any, validated_data: dict[str, Any]) -> list[str]:
    """Các field bị khoá mà payload ĐỔI THẬT giá trị, theo thứ tự khai báo.

    So GIÁ TRỊ chứ không chặn theo sự có mặt trong payload: form đơn gửi lại full
    object, chặn theo sự có mặt sẽ 403 oan cho mọi cập nhật khác đi kèm (cùng khuôn
    guard `staff_id` trong order_action_view).
    """
    return changed_fields(order, validated_data, INVOICE_LOCKED_FIELD_LABELS)


def describe_locked_fields(fields: Iterable[str]) -> str:
    """Chuỗi nhãn tiếng Việt để ghép vào `detail` của response 403."""
    return ", ".join(INVOICE_LOCKED_FIELD_LABELS.get(field, field) for field in fields)
