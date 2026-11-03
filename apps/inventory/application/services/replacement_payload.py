"""Chuẩn hoá payload "hàng gửi bù" (replacement) của luồng khớp mã chờ khớp.

Logic THUẦN dữ liệu (dict thô → dict chuẩn | thông điệp lỗi) — không đụng
request/DB — nên nằm ở application, cạnh `PendingScanService` là nơi tiêu thụ
dict đã chuẩn hoá. View (transport) vẫn là bên GỌI trước khi khớp để trả 400
sạch; xem docstring của `parse_replacement`.

Chuyển từ `transport/views/flow_history_views._parse_replacement` (refactor
tách file 2026-08).
"""
from __future__ import annotations


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_replacement(raw):
    """Chuẩn hoá payload hàng gửi bù. Trả (replacement | None, thông điệp lỗi | None).

    Validate ở view (TRƯỚC khi gọi service) để trả 400 SẠCH (chưa đụng gì tới sổ),
    thay vì để service ném lỗi sau khi mã đã được khớp — lúc đó 400 sẽ mâu thuẫn
    với trạng thái đã thay đổi.
    """
    if not raw:
        return None, None
    if not isinstance(raw, dict):
        return None, "replacement phải là object."
    raw_lines = raw.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        return None, "Hàng gửi bù phải có ít nhất 1 dòng sản phẩm."
    lines = []
    for line in raw_lines:
        if not isinstance(line, dict):
            return None, "Dòng hàng gửi bù không hợp lệ."
        product_id = _int_or_none(line.get("product_id"))
        quantity = _int_or_none(line.get("quantity"))
        if not product_id or quantity is None or quantity <= 0:
            return None, "Dòng hàng gửi bù phải có product_id và quantity > 0."
        lines.append({"product_id": product_id, "quantity": quantity})
    return {"lines": lines, "note": str(raw.get("note") or "")}, None
