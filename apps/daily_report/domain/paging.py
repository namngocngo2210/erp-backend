"""Phân trang keyset cho API Daily Report.

Hermes kéo toàn bộ theo cụm rồi tự cộng. Keyset thay offset vì mart ETL 30 phút
chèn dòng mới giữa lúc đang kéo — offset sâu vừa chậm dần vừa nhảy sót/lặp.
"""

from __future__ import annotations

from apps.common.pagination_core import InvalidLimit, strict_limit
from apps.daily_report.domain.periods import ParamError

DEFAULT_LIMIT = 200
# Một dòng đơn ~1,5KB → 1000 dòng ≈ 1,5MB; dòng hàng và dòng ads nhẹ hơn nhiều.
ORDER_MAX_LIMIT = 1000
LINE_MAX_LIMIT = 2000
ADS_MAX_LIMIT = 2000

GROUP_BY_GONE = "Tham số `group_by` không còn; API trả từng dòng, tự nhóm phía gọi."
_BAD_LIMIT = "Tham số `limit` phải là số nguyên dương."
_BAD_CURSOR = "Tham số `cursor` không hợp lệ — dùng `next_cursor` của trang trước."


def reject_group_by(params) -> None:
    """Không lặng lẽ bỏ qua: bên gọi phải biết hình dạng response đã đổi."""
    if "group_by" in params:
        raise ParamError(GROUP_BY_GONE)


def parse_limit(params, *, max_limit: int) -> int:
    try:
        return strict_limit(params.get("limit"), default=DEFAULT_LIMIT, max_limit=max_limit)
    except InvalidLimit as exc:
        raise ParamError(_BAD_LIMIT) from exc


def parse_int_cursor(params) -> int | None:
    raw = (params.get("cursor") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ParamError(_BAD_CURSOR) from exc


def slice_page(rows: list, limit: int) -> tuple[list, bool]:
    """``rows`` đã fetch ``limit + 1`` → (trang, còn trang sau?)."""
    return rows[:limit], len(rows) > limit
