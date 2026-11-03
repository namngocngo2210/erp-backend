"""Kỳ báo cáo theo ngày nghiệp vụ Việt Nam.

Chỉ nhận ngày (YYYY-MM-DD), không nhận datetime: mart đã cắt theo ngày
``ngay_tao_ngay``; nhận datetime chỉ tạo ảo giác chính xác tới phút.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from apps.analytics.sql.tz import BUSINESS_TZ

TZ = ZoneInfo(BUSINESS_TZ)
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_RANGE_DAYS = 92


class ParamError(ValueError):
    """Tham số query sai — view trả 400 kèm nguyên văn để agent tự sửa."""


def today_vn(now: datetime | None = None) -> date:
    # Mặc định = ``datetime.now(UTC)`` (tương đương ``django.utils.timezone.now()``
    # khi USE_TZ=True) — domain không import django (audit 2026-09-23 T3/P2-2);
    # caller cần mốc khác thì truyền ``now`` aware.
    return (now or datetime.now(UTC)).astimezone(TZ).date()


def yesterday_vn(now: datetime | None = None) -> date:
    return today_vn(now) - timedelta(days=1)


def iso(d: date) -> str:
    return d.isoformat()


def parse_date_param(params, name: str, *, default: date) -> date:
    raw = (params.get(name) or "").strip()
    if not raw:
        return default
    if not _DATE_PATTERN.match(raw):
        raise ParamError(f"Tham số `{name}` phải là ngày dạng YYYY-MM-DD.")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ParamError(f"Tham số `{name}` phải là ngày dạng YYYY-MM-DD.") from exc


def parse_range(params, *, now: datetime | None = None) -> tuple[date, date]:
    default = yesterday_vn(now)
    d_from = parse_date_param(params, "from", default=default)
    d_to = parse_date_param(params, "to", default=default)
    if d_from > d_to:
        raise ParamError("Tham số `from` phải nhỏ hơn hoặc bằng `to`.")
    if (d_to - d_from).days + 1 > MAX_RANGE_DAYS:
        raise ParamError(f"Khoảng `from`..`to` tối đa {MAX_RANGE_DAYS} ngày.")
    return d_from, d_to


def parse_choice(params, name: str, allowed: tuple[str, ...], *, default: str) -> str:
    raw = (params.get(name) or "").strip()
    if not raw:
        return default
    if raw not in allowed:
        raise ParamError(f"Tham số `{name}` phải là một trong: {', '.join(allowed)}.")
    return raw


def parse_int(params, name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = (params.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ParamError(f"Tham số `{name}` phải là số nguyên.") from exc
    if value < minimum or value > maximum:
        raise ParamError(f"Tham số `{name}` phải trong khoảng {minimum}..{maximum}.")
    return value
