"""Từ điển mã → nhãn tiếng Việt của schema ai — NGUỒN SỰ THẬT DUY NHẤT.

Lấy tối đa từ crm_catalog + enum model (một chỗ sửa, mart tự phủ theo).
Riêng nhãn Order.status chép từ frontend/src/i18n/messages/status.ts vì Python
không import được TS — từ thời điểm seed, DB là nguồn sự thật, file TS là bản
trình bày (spec §3.3).
"""

from apps.ai_mart.domain.chat_dictionary import CHAT_DICTIONARY
from apps.callcenter.models import TranscribeStatus
from apps.inventory.models import MovementReferenceType, MovementType, StockState
from apps.orders import crm_catalog as cat
from apps.orders.models import KocOrderType, OrderSource, OrderType, ShippingType

# Chép từ frontend/src/i18n/messages/status.ts (khối "vi") — giữ khớp từng chữ.
_ORDER_STATUS_VI = {
    0: "Mới", 1: "Chờ xử lý", 2: "Đang giao hàng", 3: "Giao thành công",
    4: "Đang chuyển hoàn", 5: "Đã chuyển hoàn", 6: "Đã hủy", 7: "Đang lấy hàng",
    8: "Chờ lấy lại", 9: "Đã lấy hàng", 10: "Chờ giao lại", 11: "Chờ chuyển hàng",
    12: "Chờ chuyển hoàn lại", 13: "Chờ xác nhận hoàn",
}

# Nhãn tiếng Việt cho enum có label tiếng Anh trong model — model là nguồn MÃ,
# đây là nguồn NHÃN hiển thị.
_ORDER_TYPE_VI = {
    OrderType.NORMAL.value: "Đơn thường",
    OrderType.NO_RETURN.value: "Không thu hồi",
    OrderType.HAS_RETURN.value: "Có thu hồi",
}
_ORDER_SOURCE_VI = {
    OrderSource.LANDING.value: "Landing", OrderSource.KOL.value: "KOL",
    OrderSource.ZALO.value: "Zalo", OrderSource.MESSENGER.value: "Messenger",
    OrderSource.HOTLINE.value: "Hotline", OrderSource.KOC.value: "KOC",
    OrderSource.SHOPEE.value: "Shopee", OrderSource.TIKTOK.value: "TikTok",
}
_SHIPPING_TYPE_VI = {
    ShippingType.SHIPMENT.value: "Gửi vận chuyển",
    ShippingType.DIRECT.value: "Bán trực tiếp tại quầy",
}

# KiotViet invoice status — chỉ seed mã mà CODE của mình đã dùng và hiểu rõ
# (gtc.py: 2 = huỷ; service_actions.py:226: 1 = hoàn thành, 3 = đang xử lý).
# Mã lạ trên prod sẽ bị `ai_mart_verify` (Task 9) liệt kê — HỎI user, không đoán.
_INVOICE_STATUS_VI = {1: "Hoàn thành", 2: "Đã hủy", 3: "Đang xử lý"}

_GENDER_VI = {0: "Nữ", 1: "Nam"}

# Kho — lấy TRỰC TIẾP từ TextChoices của apps/inventory/models.py (đã là tiếng Việt),
# không chép tay để tránh lệch khi enum đổi. "return" là override có chủ đích: nhãn
# gốc trong model là "Phiếu trả hàng" (đơn thuần), nhưng trong ai_mart giá trị này chỉ
# còn xuất hiện ở dữ liệu LỊCH SỬ (xem comment MovementReferenceType.RETURN) nên gắn
# thêm "(lịch sử)" để không ai tưởng nhầm đây là luồng đang dùng.
_RETURN_HISTORICAL_LABEL = "Phiếu trả hàng (lịch sử)"


def _from_catalog(options) -> list[tuple[str, str]]:
    return [(o.code, o.label) for o in options]


def _from_map(mapping) -> list[tuple[str, str]]:
    return [(str(k), v) for k, v in mapping.items()]


DICTIONARY: dict[str, list[tuple[str, str]]] = {
    "trang_thai_don": _from_map(_ORDER_STATUS_VI),
    "trang_thai_hoa_don": _from_map(_INVOICE_STATUS_VI),
    "loai_don": _from_map(_ORDER_TYPE_VI),
    "loai_don_koc": [(m.value, m.label) for m in KocOrderType],
    "nguon_don": _from_map(_ORDER_SOURCE_VI),
    "hinh_thuc_giao": _from_map(_SHIPPING_TYPE_VI),
    "trang_thai_sale": _from_catalog(cat.SALES_STATUS_OPTIONS),
    "ly_do_khong_chot": _from_catalog(cat.NOT_CLOSED_REASON_OPTIONS),
    "trang_thai_giao_nghiep_vu": _from_catalog(cat.DELIVERY_BUSINESS_STATUS_OPTIONS),
    "nhom_ly_do_bom": [
        (item["code"], item["name_vi"]) for item in cat.BOMB_REASON_GROUP_SEED
    ],
    "phan_loai_khach": _from_catalog(cat.CUSTOMER_CLASSIFICATION_OPTIONS),
    "nghe_nghiep": _from_catalog(cat.OCCUPATION_OPTIONS),
    "muc_do_dau": _from_catalog(cat.PAIN_LEVEL_OPTIONS),
    "nhom_tuoi": _from_catalog(cat.AGE_GROUP_OPTIONS),
    "gioi_tinh": _from_map(_GENDER_VI),
    "trang_thai_ton": [(m.value, m.label) for m in StockState],
    "loai_bien_dong_kho": [(m.value, m.label) for m in MovementType],
    "loai_chung_tu_kho": [
        (m.value, _RETURN_HISTORICAL_LABEL if m == MovementReferenceType.RETURN else m.label)
        for m in MovementReferenceType
    ],
    "trang_thai_boc_bang": [(str(m.value), m.label) for m in TranscribeStatus],
}

# Nhóm mã của mart hội thoại chat (spec 2026-09-04) — gộp vào đây để
# dictionary_rows() là MỘT nguồn seed duy nhất cho mọi migration từ điển.
DICTIONARY.update(CHAT_DICTIONARY)


def dictionary_rows() -> list[tuple[str, str, str, int]]:
    """Phẳng hoá để seed: (nhom_ma, ma, nhan, thu_tu). thu_tu = vị trí khai báo × 10."""
    rows: list[tuple[str, str, str, int]] = []
    for nhom, items in DICTIONARY.items():
        for i, (ma, nhan) in enumerate(items):
            rows.append((nhom, ma, nhan, (i + 1) * 10))
    return rows
