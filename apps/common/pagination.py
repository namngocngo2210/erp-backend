"""Shared pagination parsing for list endpoints.

Replaces the previously copy-pasted ``_parse_required_pagination`` helpers in
``apps.orders``, ``apps.channels_core`` and ``apps.customers``. Behaviour is
identical to the original ``orders`` implementation (the most complete one):
reads from ``request.data`` on POST and ``request.query_params`` otherwise,
and enforces ``max_limit``.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

# Phần parse THUẦN (không DRF) sống ở pagination_core để tầng domain dùng được;
# re-export ở đây cho view chỉ cần một đường import.
from apps.common.pagination_core import (  # noqa: F401  (re-export)
    InvalidLimit,
    clamp_int_param,
    lenient_limit,
    lenient_offset,
    strict_limit,
)

DEFAULT_LIMIT = "100"
DEFAULT_OFFSET = "0"


def parse_required_pagination(
    request,
    max_limit: int = 100,
    default_limit: int | None = None,
):
    """Parse and validate ``limit``/``offset`` pagination params.

    Returns ``(params, None)`` on success where ``params`` is
    ``{"limit": int, "offset": int}``, or ``(None, Response)`` with a 400 error
    response when the input is invalid. Callers should ``return error`` early
    when the second element is truthy.

    ``default_limit`` overrides the module-level ``DEFAULT_LIMIT`` (100) when
    provided, allowing endpoints to use a different default without changing the
    shared ``max_limit`` default. Existing callers that omit both parameters
    keep the original behaviour.
    """
    source = request.data if request.method == "POST" else request.query_params
    fallback_limit = str(default_limit) if default_limit is not None else DEFAULT_LIMIT
    limit_raw = source.get("limit", fallback_limit)
    offset_raw = source.get("offset", DEFAULT_OFFSET)
    try:
        limit = int(limit_raw)
        offset = int(offset_raw)
    except (TypeError, ValueError):
        return None, Response(
            {"detail": "Query params 'limit' và 'offset' phải là số nguyên."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if limit < 1 or offset < 0:
        return None, Response(
            {"detail": "Query params 'limit' phải >= 1 và 'offset' phải >= 0."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if limit > max_limit:
        return None, Response(
            {"detail": f"Query param 'limit' tối đa là {max_limit}."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return {"limit": limit, "offset": offset}, None


def parse_limit(request, *, default: int, max_limit: int):
    """Đọc mỗi ``limit`` (không offset) cho list endpoint trả TOÀN BỘ tập nhỏ.

    Dùng cho các danh sách vốn không phân trang (dropdown nhân viên, timeline
    một đơn, bảng công một người…) để có TRẦN thay vì trả không giới hạn (audit
    2026-09-23 L6) mà không đổi hình dạng response — FE cũ không gửi ``limit``
    nhận đúng ``default`` dòng đầu. Trả ``(limit, None)`` hoặc ``(None, Response 400)``.
    """
    raw = request.query_params.get("limit", None)
    if raw in (None, ""):
        return default, None
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return None, Response(
            {"detail": "Query param 'limit' phải là số nguyên."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if limit < 1:
        return None, Response(
            {"detail": "Query param 'limit' phải >= 1."}, status=status.HTTP_400_BAD_REQUEST
        )
    if limit > max_limit:
        return None, Response(
            {"detail": f"Query param 'limit' tối đa là {max_limit}."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return limit, None
