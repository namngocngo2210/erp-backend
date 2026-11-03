"""Cảnh báo trùng đơn cho các trang danh sách đơn hàng.

Luật khớp (chốt 2026-08-18, tách hai rổ 2026-08-21): hai đơn khớp nhau khi
- cùng `phone_normalized`, VÀ
- giao nhau ít nhất một sản phẩm, VÀ
- CẢ HAI cùng nằm trong `WARNING_STATUSES`, VÀ
- `created_at` cách nhau không quá `DUPLICATE_WINDOW_DAYS` ngày.

Đơn khớp rơi vào MỘT trong hai rổ theo trạng thái của chính nó — hai rổ là hai
cảnh báo khác nhau về nghiệp vụ, không phải hai mức độ của cùng một cảnh báo:
- `shipping`: khách đang có đơn khác chạy trên đường → nguy cơ giao trùng.
- `returning`: khách đang/vừa hoàn đơn cùng sản phẩm → nguy cơ bom hàng.

Cửa sổ tính TƯƠNG ĐỐI giữa hai đơn, không neo vào "hôm nay": mở lại danh sách
đơn của tháng trước vẫn phải thấy đúng cặp trùng của thời điểm đó.

Khác `lead_filter_service` — bên đó CHẶN lead lúc ingest (cửa sổ 24h trượt,
chỉ đơn chưa terminal); module này chỉ dựng dữ liệu để FE HIỂN THỊ cảnh báo,
không chặn gì.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from apps.orders.models import OrderItem, OrderStatus

DUPLICATE_WINDOW_DAYS = 30

# Các chặng đơn còn chạy trên đường. Cố ý GỒM Giao thành công (khách vừa nhận
# hàng mà lại có đơn nữa vẫn là trùng).
SHIPPING_STATUSES = (
    OrderStatus.NEW,
    OrderStatus.PENDING_PROCESSING,
    OrderStatus.IN_DELIVERY,
    OrderStatus.DELIVERED_SUCCESS,
    OrderStatus.PICKING_UP,
    OrderStatus.AWAITING_REPICKUP,
    OrderStatus.PICKED_UP,
    OrderStatus.AWAITING_REDELIVERY,
    OrderStatus.AWAITING_SHIPMENT,
)

# Nhánh hoàn — gồm cả đơn đã hoàn xong: lịch sử bom hàng trong 30 ngày vẫn là
# thông tin sale cần thấy trước khi chốt đơn mới.
RETURN_STATUSES = (
    OrderStatus.IN_RETURN,
    OrderStatus.RETURNED,
    OrderStatus.AWAITING_RETURN_SHIPMENT,
    OrderStatus.AWAITING_RETURN_CONFIRMATION,
)

# Đơn huỷ (6) cố ý KHÔNG có mặt: huỷ phần lớn là khách đổi ý hoặc lead trùng,
# đưa vào thì cảnh báo nổ dày tới mức sale bỏ qua cả cảnh báo thật.
WARNING_STATUSES = SHIPPING_STATUSES + RETURN_STATUSES

# Cắt bớt id trả về: FE chỉ cần vài id để hiện trong tooltip. Áp cho TỪNG rổ —
# một rổ đầy không được che mất rổ kia.
MAX_DUPLICATE_IDS = 5


@dataclass(frozen=True)
class DuplicateWarning:
    """Hai rổ đơn khớp của một đơn trên trang. Rỗng cả hai = không có cảnh báo."""

    shipping: list[int] = field(default_factory=list)
    returning: list[int] = field(default_factory=list)


def build_duplicate_map(page_orders) -> dict[int, DuplicateWarning]:
    """Map `order_id -> DuplicateWarning` cho đúng các đơn của MỘT trang danh sách.

    Một query duy nhất cho cả trang (không phụ thuộc số dòng): quét `OrderItem`
    join `Order` theo tập `phone_normalized` của trang, chặn thêm `created_at`
    trong khoảng [min(trang) − 30 ngày, max(trang) + 30 ngày] để bám
    `order_phone_norm_idx` thay vì mở toàn bộ lịch sử đơn. Lọc cặp chính xác
    theo 30 ngày làm nốt trong Python — dữ liệu đã nằm sẵn trong bộ nhớ.
    """
    statuses = set(WARNING_STATUSES)
    candidates = [
        order
        for order in page_orders
        if order.status in statuses and order.phone_normalized and order.created_at
    ]
    if not candidates:
        return {}

    window = timedelta(days=DUPLICATE_WINDOW_DAYS)
    phones = {order.phone_normalized for order in candidates}
    created_values = [order.created_at for order in candidates]

    rows = OrderItem.objects.filter(
        order__phone_normalized__in=phones,
        order__status__in=WARNING_STATUSES,
        order__deleted_at__isnull=True,
        order__created_at__gte=min(created_values) - window,
        order__created_at__lte=max(created_values) + window,
    ).values_list(
        "order_id",
        "order__phone_normalized",
        "order__created_at",
        "order__status",
        "product_id",
    )

    return_statuses = set(RETURN_STATUSES)
    products_by_order: dict[int, set[int]] = defaultdict(set)
    created_by_order: dict[int, object] = {}
    status_by_order: dict[int, int] = {}
    order_ids_by_phone: dict[str, set[int]] = defaultdict(set)
    for order_id, phone, created_at, status, product_id in rows:
        products_by_order[order_id].add(product_id)
        created_by_order[order_id] = created_at
        status_by_order[order_id] = status
        order_ids_by_phone[phone].add(order_id)

    duplicate_map: dict[int, DuplicateWarning] = {}
    for order in candidates:
        own_products = products_by_order.get(order.id)
        if not own_products:
            continue
        matches = [
            other_id
            for other_id in order_ids_by_phone.get(order.phone_normalized, ())
            if other_id != order.id
            and own_products & products_by_order[other_id]
            and abs(created_by_order[other_id] - order.created_at) <= window
        ]
        if not matches:
            continue
        matches.sort(key=lambda other_id: (created_by_order[other_id], other_id), reverse=True)
        shipping = [i for i in matches if status_by_order[i] not in return_statuses]
        returning = [i for i in matches if status_by_order[i] in return_statuses]
        duplicate_map[order.id] = DuplicateWarning(
            shipping=shipping[:MAX_DUPLICATE_IDS],
            returning=returning[:MAX_DUPLICATE_IDS],
        )
    return duplicate_map
