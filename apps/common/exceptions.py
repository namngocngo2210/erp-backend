"""DRF exception handler thống nhất (audit 2026-07 mục 4.8).

Bọc ``rest_framework.views.exception_handler`` — GIỮ NGUYÊN body/status hiện có,
chỉ:

- bổ sung key ``error: {"code": <snake_case tên exception>, "detail": <str đầu
  tiên tìm thấy>}`` khi body là dict (body dạng list giữ nguyên);
- log exception 500 (DRF không xử lý → Django trả server error) kèm request_id
  từ contextvar :mod:`apps.common.request_id`.
"""

import logging
import re

from django.db.utils import OperationalError
from rest_framework.views import exception_handler as drf_exception_handler

from apps.common.http_metrics import db_connection_errors_total
from apps.common.request_id import get_request_id

logger = logging.getLogger(__name__)

_SNAKE_RE_1 = re.compile(r"(.)([A-Z][a-z]+)")
_SNAKE_RE_2 = re.compile(r"([a-z0-9])([A-Z])")


def _snake_case(name: str) -> str:
    partial = _SNAKE_RE_1.sub(r"\1_\2", name)
    return _SNAKE_RE_2.sub(r"\1_\2", partial).lower()


def _first_detail(data) -> str:
    """Chuỗi thông báo đầu tiên tìm thấy trong cấu trúc lỗi DRF (dict/list/str)."""
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        for item in data:
            found = _first_detail(item)
            if found:
                return found
        return ""
    if isinstance(data, dict):
        for value in data.values():
            found = _first_detail(value)
            if found:
                return found
    return ""


def _record_db_connection_error(exc) -> None:
    """Đếm riêng lỗi không mở được connection Postgres.

    Cạn ``max_connections`` là sự cố đã lặp ba lần (21/07, 23/07, 29/07/2026) và cả
    ba lần đều được phát hiện qua log chứ không qua cảnh báo, vì metric hạ tầng là
    gauge lấy mỗi ~15s còn bão thì chỉ kéo dài vài chục giây. Counter này đo ở tầng
    ứng dụng nên không bỏ sót — xem docs/ws-broadcast-db-fanout-2026-07-29.md.

    Best-effort tuyệt đối: exception handler không được phép tự ném lỗi.
    """
    try:
        if not isinstance(exc, OperationalError):
            return
        message = str(exc).lower()
        if "too many clients" in message:
            reason = "too_many_clients"
        elif "connection" in message:
            reason = "connection_failed"
        else:
            return
        db_connection_errors_total.labels(reason=reason).inc()
    except Exception:  # pragma: no cover - không bao giờ được che lỗi gốc
        pass


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)

    if response is None:
        # DRF không xử lý (lỗi 500) — log kèm request_id rồi trả về None để
        # Django giữ nguyên hành vi server-error hiện tại.
        _record_db_connection_error(exc)
        logger.exception(
            "Unhandled exception (request_id=%s): %s", get_request_id(), exc
        )
        return None

    if isinstance(response.data, dict) and "error" not in response.data:
        response.data["error"] = {
            "code": _snake_case(exc.__class__.__name__),
            "detail": _first_detail(response.data),
        }
    return response
