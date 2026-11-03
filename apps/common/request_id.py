"""Request-ID correlation: middleware + logging filter (audit 2026-07 mục 2.4).

- Middleware: nhận ``X-Request-ID`` inbound (nếu hợp lệ) hoặc sinh uuid4,
  gắn vào contextvar + ``request.request_id`` và trả lại ở response header.
- ``RequestIdFilter``: đưa request_id vào mọi log record (đăng ký ở LOGGING
  handlers) — JSON log tra cứu được toàn bộ dòng log của một request.

Module này cố ý KHÔNG import Django models (được import lúc cấu hình logging,
trước khi app registry sẵn sàng).
"""

import contextvars
import logging
import re
import uuid

REQUEST_ID_HEADER = "X-Request-ID"

# Chỉ chấp nhận id "sạch" từ client/proxy — chặn log injection qua header.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9\-_.]{1,64}$")

_request_id_var = contextvars.ContextVar("request_id", default="-")


def get_request_id() -> str:
    """Request-id của request hiện tại ("-" nếu ngoài chu trình request)."""
    return _request_id_var.get()


class RequestIdFilter(logging.Filter):
    """Gắn ``record.request_id`` cho mọi log record đi qua handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        return True


class RequestIdMiddleware:
    """Đọc/sinh request-id, propagate qua contextvar và trả response header."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        inbound = (request.headers.get(REQUEST_ID_HEADER) or "").strip()
        request_id = inbound if _SAFE_ID_RE.match(inbound) else uuid.uuid4().hex
        request.request_id = request_id
        token = _request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            _request_id_var.reset(token)
        response[REQUEST_ID_HEADER] = request_id
        return response
