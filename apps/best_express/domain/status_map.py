"""Bảng map mã trạng thái Best Express → mã trạng thái nội bộ. Pure — không I/O,
không Django.

Enum Django ``BestShipmentStatus(models.TextChoices)`` (choices/label cho model
field) nằm ở ``apps/best_express/models.py`` và lấy giá trị + nhãn từ
``STATUS_LABELS`` ở đây (audit 2026-09-23 T3/P2-2: domain không import django).
Giá trị trả về của ``map_best_status`` là chuỗi thuần, so sánh được với member
TextChoices (TextChoices là ``str``).
"""

from __future__ import annotations

CREATED = "created"
PICKING_UP = "picking_up"
PICKUP_FAILED = "pickup_failed"
PICKED_UP = "picked_up"
IN_TRANSIT = "in_transit"
OUT_FOR_DELIVERY = "out_for_delivery"
DELIVERY_FAILED = "delivery_failed"
DELIVERED = "delivered"
RETURNING = "returning"
RETURN_FAILED = "return_failed"
RETURNED = "returned"
LOST = "lost"
DAMAGED = "damaged"
CANCELLED = "cancelled"

# Thứ tự = thứ tự choices của model field (giữ nguyên để không sinh migration).
STATUS_LABELS: dict[str, str] = {
    CREATED: "Đã tạo",
    PICKING_UP: "Đang đi lấy hàng",
    PICKUP_FAILED: "Lấy hàng thất bại",
    PICKED_UP: "Đã lấy hàng",
    IN_TRANSIT: "Đang trung chuyển",
    OUT_FOR_DELIVERY: "Đang giao",
    DELIVERY_FAILED: "Giao thất bại",
    DELIVERED: "Giao thành công",
    RETURNING: "Đang chuyển hoàn",
    RETURN_FAILED: "Trả hàng thất bại",
    RETURNED: "Đã chuyển hoàn",
    LOST: "Thất lạc",
    DAMAGED: "Hư hỏng",
    CANCELLED: "Đã hủy",
}


_STATUS_BY_CODE: dict[str, str] = {
    "211": PICKING_UP,
    "203": PICKUP_FAILED,
    "202": PICKED_UP,
    "301": IN_TRANSIT,
    "302": IN_TRANSIT,
    "303": IN_TRANSIT,
    "304": IN_TRANSIT,
    "309": IN_TRANSIT,
    "601": OUT_FOR_DELIVERY,
    "604": DELIVERY_FAILED,
    "666": DELIVERED,
    "605": RETURNING,
    "701": RETURNING,
    "702": RETURNING,
    "703": RETURNING,
    "704": RETURNING,
    "705": RETURNING,
    "707": RETURN_FAILED,
    "708": RETURNED,
    "777": LOST,
    "1000": DAMAGED,
    "2": CANCELLED,
}


def map_best_status(code) -> str | None:
    """Mã Best (str/int) → mã trạng thái nội bộ; mã lạ trả None (caller giữ status cũ)."""
    raw = str(code or "").strip()
    if not raw:
        return None
    if raw.lower() == "customer cancel":
        return CANCELLED
    return _STATUS_BY_CODE.get(raw)
