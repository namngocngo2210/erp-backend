"""Pure datetime / decimal helpers for KiotViet invoice syncing.

Extracted verbatim from the ``sync_invoices_between_channels`` management command
so the logic is testable without a database and reusable by the other invoice
services. No Django models, no I/O — pure functions only.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.utils.dateparse import parse_datetime


def first_non_empty(*values):
    """Return the first non-empty value (stripped if a string), else ""."""
    for value in values:
        if value not in (None, ""):
            if isinstance(value, str):
                text = value.strip()
                if text:
                    return text
            else:
                return value
    return ""


def to_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
    """Coerce a value to Decimal, returning ``default`` on failure/empty."""
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def parse_dt_loose(value) -> datetime | None:
    """Parse a KiotViet datetime with tolerant format handling."""
    text = str(value or "").strip()
    if not text:
        return None
    # Cắt fractional vượt 6 số (KiotViet hay trả 7) để parse_datetime đọc được.
    if "." in text:
        head, _, tail = text.partition(".")
        tail_digits = "".join(ch for ch in tail if ch.isdigit())[:6]
        tail_rest = "".join(ch for ch in tail if not ch.isdigit())
        text = f"{head}.{tail_digits}{tail_rest}" if tail_digits else head
    parsed = parse_datetime(text)
    if parsed is not None:
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    try:
        return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def is_after(modified_date_raw, to_dt_str: str) -> bool:
    """True nếu modified_date_raw > to_dt_str."""
    mod = parse_dt_loose(modified_date_raw)
    to = parse_dt_loose(to_dt_str)
    if mod is None or to is None:
        return False
    return mod > to


def shift_dt(dt_str: str, *, days: int) -> str:
    """Shift một datetime string đi `days` ngày, trả về 'YYYY-MM-DD HH:MM:SS'."""
    parsed = parse_dt_loose(dt_str)
    if parsed is None:
        return dt_str
    return (parsed + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
