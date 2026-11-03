"""Dựng dòng Excel cho export trang Danh sách contact.

Bộ cột MIRROR đúng bảng trên màn hình (`features/contacts/components/ContactsTable.tsx`),
bỏ 2 cột không có nghĩa trong file: "#" (số thứ tự theo trang) và "Thao tác nhanh"
(3 nút bấm). Nguồn dữ liệu là item do `ContactListService` trả về — CÙNG shape với
payload của list endpoint, nên file xuất không thể lệch màn hình về mặt dữ liệu.

Khác biệt CÓ CHỦ ĐÍCH so với màn hình:
- Cột SKU đổ ĐỦ dòng hàng (màn hình chỉ hiện SKU đầu + badge "+n" vì hẹp chỗ).
- "Giá đơn" và "SL đơn" ghi dạng SỐ để Excel cộng/lọc được, không phải chuỗi đã format.
"""

from __future__ import annotations

from django.utils import timezone

from apps.orders.crm_catalog import SALES_STATUS_LABELS
from apps.orders.domain.contact_rules import ACTION_LABELS
from apps.orders.models import OrderSource

# Trần dòng mỗi lần xuất. Thấp hơn hẳn 50.000 của trang Đơn hàng vì pipeline contact
# materialize TOÀN BỘ dòng ra Python để lọc (DISTINCT ON + enrich, không phân trang
# dưới DB) — cùng số dòng thì nó tốn RAM worker hơn nhiều so với luồng đơn thuần
# queryset.iterator() của Orders.
CONTACT_EXPORT_MAX_ROWS = 20_000

CONTACT_EXPORT_HEADERS: tuple[str, ...] = (
    "Mã KH",
    "Khách hàng",
    "SĐT",
    "Địa chỉ",
    "Nguồn khách",
    "Ngày data về",
    "SKU",
    "Giá đơn",
    "Loại khách",
    "Tác nghiệp cần",
    "Tác nghiệp",
    "Kết quả",
    "Thời gian chờ",
    "SL đơn",
)

# Nhãn bucket tác nghiệp — MIRROR `OPERATION_BUCKETS` ở frontend
# (features/contacts/operation-buckets.ts). Đổi nhãn phải sửa CẢ HAI chỗ.
_BUCKET_LABELS: dict[str, str] = {
    "overdue": "Quá hạn",
    "today": "Hôm nay",
    "upcoming": "Sắp tới",
    "ended": "Kết thúc",
    "none": "",
}

_ORDER_SOURCE_LABELS: dict[str, str] = dict(OrderSource.choices)

_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 24 * _SECONDS_PER_HOUR


def _format_datetime(value) -> str:
    """Datetime aware → "dd/mm/YYYY HH:MM" giờ địa phương.

    Đảo thứ tự so với màn hình (màn hình hiện "HH:MM dd/mm/YYYY"): trong Excel người
    dùng lọc/sắp xếp theo ngày, ngày đứng trước đọc nhanh hơn hẳn.
    """
    if not value:
        return ""
    return f"{timezone.localtime(value):%d/%m/%Y %H:%M}"


def _format_due(value) -> str:
    """Giờ đến hạn tác nghiệp — ngắn gọn "HH:MM dd/mm" như ô trên màn hình."""
    if not value:
        return ""
    return f"{timezone.localtime(value):%H:%M %d/%m}"


def humanize_waiting(waiting_since, *, now=None) -> str:
    """Mirror `humanizeWaiting` (contact-actions.ts): <1h / Xh / Xd, rỗng khi None.

    Mốc âm (waiting_since ở tương lai, do lệch giờ máy chủ/ghi lùi) trả "<1h" y hệt
    frontend thay vì số âm.
    """
    if not waiting_since:
        return ""
    now = now or timezone.now()
    seconds = (now - waiting_since).total_seconds()
    if seconds < _SECONDS_PER_HOUR:
        return "<1h"
    if seconds < _SECONDS_PER_DAY:
        return f"{int(seconds // _SECONDS_PER_HOUR)}h"
    return f"{int(seconds // _SECONDS_PER_DAY)}d"


def _source_label(order: dict) -> str:
    """Mirror `sourceLabel` (ContactsTable.tsx): ưu tiên tên kênh, fallback nhãn nguồn."""
    channel_name = order.get("channel_name") or ""
    if channel_name:
        return channel_name
    source = order.get("order_source") or ""
    return _ORDER_SOURCE_LABELS.get(source, source)


def _sku_label(item: dict) -> str:
    """Mirror `skuLabel` (ContactSkuCell.tsx): sku → product_name → rỗng."""
    return item.get("sku") or item.get("product_name") or ""


def _sku_cell(items: list[dict]) -> str:
    """Đổ ĐỦ dòng hàng: "SKU-A ×2, SKU-B". `quantity = 0` là đơn cũ không có OrderItem
    (SKU suy từ chuỗi gộp `Order.sku`) — không có số lượng thật nên không ghi ×."""
    parts = []
    for item in items or []:
        label = _sku_label(item)
        if not label:
            continue
        quantity = item.get("quantity") or 0
        parts.append(f"{label} ×{quantity}" if quantity > 0 else label)
    return ", ".join(parts)


def _operation_cell(operation: dict) -> str:
    """Gộp 2 phần của ô "Tác nghiệp" trên màn hình (badge tên bước + giờ đến hạn)
    vào 1 cell: "Gọi lần 1 · 14:30 20/08 (Quá hạn)". Khách chưa vào flow → rỗng."""
    bucket = (operation or {}).get("bucket") or "none"
    if bucket == "none":
        return ""
    stage_name = operation.get("stage_name") or ""
    due = _format_due(operation.get("due_at"))
    bucket_label = _BUCKET_LABELS.get(bucket, bucket)
    parts = [part for part in (stage_name, due) if part]
    text = " · ".join(parts)
    return f"{text} ({bucket_label})" if bucket_label else text


def _amount(raw) -> float:
    """Tiền về float để Excel cộng được (cùng quy ước `order_row_values`)."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def contact_row_values(item: dict, *, now=None) -> list:
    """1 item của `ContactListService` → 1 dòng theo đúng thứ tự CONTACT_EXPORT_HEADERS."""
    order = item.get("latest_order") or {}
    sales_status_code = order.get("sales_status_code") or ""
    action = item.get("action_needed") or "none"
    return [
        item.get("customer_code") or "",
        item.get("full_name") or "",
        item.get("phone_raw") or "",
        order.get("address") or "",
        _source_label(order),
        _format_datetime(order.get("created_at")),
        _sku_cell(order.get("items")),
        _amount(order.get("total_after_discount")),
        "Khách mới" if item.get("is_new_customer") else "Khách cũ",
        ACTION_LABELS.get(action, action),
        _operation_cell(item.get("operation") or {}),
        SALES_STATUS_LABELS.get(sales_status_code, sales_status_code),
        humanize_waiting(item.get("waiting_since"), now=now),
        item.get("order_count") or 0,
    ]
