"""Nhãn tiếng Việt của trạng thái đơn — mirror frontend/src/i18n/messages/status.ts.

Backend `OrderStatus.label` là tiếng Anh, không dùng cho khách hàng cuối được.
"""

ORDER_STATUS_LABELS_VI: dict[int, str] = {
    0: "Mới",
    1: "Chờ xử lý",
    2: "Đang giao hàng",
    3: "Giao thành công",
    4: "Đang chuyển hoàn",
    5: "Đã chuyển hoàn",
    6: "Đã hủy",
    7: "Đang lấy hàng",
    8: "Chờ lấy lại",
    9: "Đã lấy hàng",
    10: "Chờ giao lại",
    11: "Chờ chuyển hàng",
    12: "Chờ chuyển hoàn lại",
    13: "Chờ xác nhận hoàn",
}


def status_label(value: int) -> str:
    return ORDER_STATUS_LABELS_VI.get(value, "Không xác định")
