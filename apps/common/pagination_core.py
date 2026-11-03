"""Parse ``limit``/``offset`` — phần THUẦN (không Django/DRF).

Tách khỏi ``apps.common.pagination`` (vốn trả ``Response`` 400 của DRF) để tầng
domain (vd ``apps.daily_report.domain.paging``) dùng được mà không kéo DRF vào.
Gom 3 bản parse tự viết ở contact_list_view / users admin list / daily_report
(audit 2026-09-23 T3/P2-5) — mỗi hàm giữ đúng luật khoan dung/nghiêm của bản gốc.
"""

from __future__ import annotations


class InvalidLimit(ValueError):
    """``limit`` không phải số nguyên dương (chỉ ``strict_limit`` ném)."""


def clamp_int_param(raw, *, default: int, minimum: int, maximum: int | None = None) -> int:
    """Khoan dung: không parse được → ``default``; ngoài khoảng → kẹp về biên."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def lenient_limit(raw, *, default: int, max_limit: int) -> int:
    """Khoan dung kiểu contact list: thiếu / hỏng / ``<= 0`` → ``default``; quá trần → trần."""
    try:
        limit = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        limit = default
    if limit <= 0:
        limit = default
    return min(limit, max_limit)


def lenient_offset(raw) -> int:
    """Thiếu / hỏng / âm → 0."""
    return clamp_int_param(raw, default=0, minimum=0)


def strict_limit(raw, *, default: int, max_limit: int) -> int:
    """Nghiêm kiểu daily_report: rỗng → ``min(default, max_limit)``; hỏng hoặc
    ``< 1`` → ``InvalidLimit``; quá trần → trần (không lỗi)."""
    text = (str(raw) if raw is not None else "").strip()
    if not text:
        return min(default, max_limit)
    try:
        value = int(text)
    except ValueError as exc:
        raise InvalidLimit(text) from exc
    if value < 1:
        raise InvalidLimit(text)
    return min(value, max_limit)
