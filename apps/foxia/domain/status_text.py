"""Chuẩn hoá chuỗi trạng thái thô của đối tác — CHỈ chuẩn hoá, KHÔNG ánh xạ.

Trạng thái Foxia và trạng thái đơn vị vận chuyển được lưu nguyên văn vào
`orders.partner_delivery_status` / `partner_dvvc_delivery_status` (Sprint 2).
Map sang OrderStatus nội bộ là việc của giai đoạn sau: chưa có bản chốt danh
sách mã trạng thái từ Foxia nên map lúc này là đoán, mà đoán sai thì đơn hiển
thị sai trạng thái và không ai phát hiện được vì dữ liệu gốc đã bị nuốt.

Chuẩn hoá ở mức tối thiểu — trim + gộp khoảng trắng + lowercase — để `GROUP BY`
ra đúng tập giá trị thật, nhưng không đụng tới nội dung.
"""

from __future__ import annotations

import re

from apps.foxia.contracts.dtos import many2one_name

_WHITESPACE = re.compile(r"\s+")

# Trần bằng max_length của cột partner_*_delivery_status trên bảng orders. Cắt ở
# đây thay vì để DB raise: trạng thái dài bất thường là dữ liệu lạ, không phải
# lý do làm hỏng cả lượt đồng bộ.
MAX_LENGTH = 64


def normalize_status(value) -> str:
    """Trả chuỗi trạng thái đã chuẩn hoá; rỗng nếu không có giá trị.

    Nhận cả many2one ``[id, "tên"]`` lẫn chuỗi/selection thuần — tài liệu Foxia
    không nói rõ `giaidoan_id` và `donvanchuyen.trangthai` thuộc dạng nào.
    """
    text = many2one_name(value)
    if not text:
        return ""
    return _WHITESPACE.sub(" ", text).strip().lower()[:MAX_LENGTH]
