"""Nhóm trạng thái VC hiển thị cho hành trình BH theo mã chi tiết Best Express.

Bảng RIÊNG cho bảo hành (11 nhóm hiển thị) — KHÔNG dùng chung với
`apps.best_express.domain.status_map` (bảng đó phục vụ trang Best shipments,
map 202/707 khác mục đích). Pure — không I/O.
"""

from __future__ import annotations

BEST_VC_GROUP_LABELS: dict[str, str] = {
    "created": "Tạo mới",
    "picking_up": "Đang lấy hàng",
    "pickup_failed": "Lấy hàng không thành công",
    "in_transit": "Đang trung chuyển",
    "delivering": "Đang giao hàng",
    "redelivery": "Chờ giao lại",
    "delivered": "GTC",
    "returning": "Đang chuyển hoàn",
    "returned": "Đã trả hàng",
    "lost": "Thất lạc",
    "damaged": "Hư hỏng",
}

_GROUP_BY_CODE: dict[str, str] = {
    "211": "picking_up",
    "203": "pickup_failed",
    "202": "in_transit",
    "301": "in_transit",
    "302": "in_transit",
    "303": "in_transit",
    "304": "in_transit",
    "309": "in_transit",
    "601": "delivering",
    "604": "redelivery",
    "666": "delivered",
    "605": "returning",
    "701": "returning",
    "702": "returning",
    "703": "returning",
    "704": "returning",
    "705": "returning",
    "707": "returning",
    "708": "returned",
    "777": "lost",
    "1000": "damaged",
}


# (field mã, field trạng thái thô, field mốc thời gian) — 3 chặng của hành trình.
# Dùng chung cho subscriber realtime và cron trace để hai đường không lệch nhau.
JOURNEY_CODE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("new_tracking_code", "new_tracking_best_status", "new_tracking_best_status_ms"),
    ("recall_code", "recall_best_status", "recall_best_status_ms"),
    (
        "return_tracking_code",
        "return_tracking_best_status",
        "return_tracking_best_status_ms",
    ),
)


def should_apply_best_status(current_ms: int, incoming_ms: int) -> bool:
    """Có nên ghi đè trạng thái đang lưu bằng event vừa tới?

    Best không đảm bảo thứ tự event; không chặn thì "GTC" bị kéo ngược về
    "Đang trung chuyển". Dùng `>=` (không phải `>`) để event cùng mốc vẫn ghi
    được — nội dung giống nhau nên vô hại, và tránh kẹt khi mốc đang lưu là 0.

    LƯU Ý (review 2026-07-22, finding #1/#2): hàm này chỉ còn là bộ lọc SỚM
    dựa trên bản đọc có thể đã cũ (tránh bắn UPDATE vô ích khi rõ ràng đã lạc
    hậu) — KHÔNG còn là trọng tài chống race. `current_ms` chỉ tăng theo thời
    gian (ghi có điều kiện ở tầng DB), nên `current` đọc được không bao giờ lớn
    hơn giá trị thật đang có trong DB tại thời điểm ghi; hàm trả False ở đây thì
    UPDATE có điều kiện (`ms_field__lte=incoming_ms`) chắc chắn cũng sẽ khớp 0
    dòng — bỏ qua sớm không làm mất write hợp lệ nào. Trọng tài THẬT nằm ở
    mệnh đề `WHERE <ms_field> <= incoming_ms` trong câu UPDATE ở
    `subscribers.py` / `tracking_sync_service.py`.
    """
    try:
        incoming = int(incoming_ms or 0)
    except (TypeError, ValueError):
        incoming = 0
    try:
        current = int(current_ms or 0)
    except (TypeError, ValueError):
        current = 0
    return incoming >= current


def map_best_vc_group(code) -> str | None:
    """Mã Best (str/int) → key nhóm hiển thị; mã lạ/rỗng → None."""
    raw = str(code or "").strip()
    if not raw:
        return None
    return _GROUP_BY_CODE.get(raw)


def _group_for(tracking_code: str, best_status: str) -> str | None:
    """Nhóm của 1 mã: không có mã → None (ẩn badge); có mã nhưng chưa có/không
    hiểu data Best → "created"."""
    if not (tracking_code or "").strip():
        return None
    return map_best_vc_group(best_status) or "created"


def journey_vc_groups(journey) -> dict:
    """Nhóm VC per-code + nhóm chính của hành trình.

    `main` theo mã ưu tiên (chặng muộn nhất có mã): gửi lại > thu hồi > gửi đi;
    không có mã nào → "created".
    """
    new = _group_for(journey.new_tracking_code, journey.new_tracking_best_status)
    recall = _group_for(journey.recall_code, journey.recall_best_status)
    ret = _group_for(journey.return_tracking_code, journey.return_tracking_best_status)
    return {"new": new, "recall": recall, "return": ret, "main": ret or recall or new or "created"}
