"""Đọc tham số query của feed. Pure: ``now`` do caller truyền vào (aware)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_WINDOW_DAYS = 7
MAX_RANGE_DAYS = 92
DEFAULT_LIMIT = 200
# Một dòng ~1KB → 1000 dòng ≈ 1MB, đủ nhỏ cho RAM web.
MAX_LIMIT = 1000


class ParamError(ValueError):
    """Tham số sai — view trả 400 kèm nguyên văn để bên gọi tự sửa."""


def parse_bound(raw: str | None, field: str, *, tz: ZoneInfo, end_of_day: bool) -> datetime | None:
    """``YYYY-MM-DD`` hoặc ISO datetime → mốc aware. Ngày trần của ``to`` nở tới cuối ngày.

    Tách nhánh theo dấu ngăn giờ: ``datetime.fromisoformat`` cũng nuốt chuỗi
    date-only và trả nửa đêm — nhánh nở-cuối-ngày sẽ không bao giờ chạy.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if "T" in text or " " in text:
            parsed = datetime.fromisoformat(text)
        else:
            parsed = datetime.combine(date.fromisoformat(text), time.max if end_of_day else time.min)
    except ValueError as exc:
        raise ParamError(f"Tham số `{field}` phải là YYYY-MM-DD hoặc ISO datetime.") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)


def parse_range(params, *, now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    end = parse_bound(params.get("to"), "to", tz=tz, end_of_day=True) or now
    start = parse_bound(params.get("from"), "from", tz=tz, end_of_day=False) or end - timedelta(
        days=DEFAULT_WINDOW_DAYS
    )
    if start > end:
        raise ParamError("Tham số `from` phải nhỏ hơn hoặc bằng `to`.")
    if end - start > timedelta(days=MAX_RANGE_DAYS):
        raise ParamError(f"Khoảng `from`..`to` tối đa {MAX_RANGE_DAYS} ngày.")
    return start, end


def parse_limit(params) -> int:
    raw = (params.get("limit") or "").strip()
    if not raw:
        return DEFAULT_LIMIT
    try:
        value = int(raw)
    except ValueError as exc:
        raise ParamError("Tham số `limit` phải là số nguyên dương.") from exc
    if value < 1:
        raise ParamError("Tham số `limit` phải là số nguyên dương.")
    return min(value, MAX_LIMIT)


def parse_cursor(params) -> int | None:
    raw = (params.get("cursor") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ParamError("Tham số `cursor` không hợp lệ — dùng `next_cursor` của trang trước.") from exc
