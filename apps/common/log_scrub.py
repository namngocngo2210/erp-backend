"""Lọc secret khỏi log record trước khi ghi ra (audit 2026-09-23 H2).

Access token JWT từng đi qua query string WebSocket (`/ws/crm/?token=...`) và
uvicorn ghi nguyên request line vào log container → Alloy → Loki → Grafana. Từ
release này token đi qua `Sec-WebSocket-Protocol` (apps/channels_core/jwt_middleware.py),
nhưng client cũ còn dùng `?token=` một release nữa, và mọi log ghi URL đều là chỗ
rò tiềm năng. Filter này thay `token=<...>` bằng `token=***` trong `msg` và `args`
của record — áp cho handler console của Django LẪN logger `uvicorn.access`/
`uvicorn.error` (gunicorn UvicornWorker gắn handler riêng cho hai logger đó, xem
`config/asgi.py`). Alloy scrub thêm một lớp nữa ở monitoring/alloy-config.alloy.

Không import Django: module được nạp lúc cấu hình logging, trước app registry.
"""

from __future__ import annotations

import logging
import re

# `token=` theo sau bởi mọi ký tự không phải dấu ngăn query/khoảng trắng/dấu nháy.
_TOKEN_RE = re.compile(r"(?i)(\btoken=)[^&\s\"']+")
_REPLACEMENT = r"\1***"

UVICORN_LOGGERS = ("uvicorn.access", "uvicorn.error")


def scrub_token(text: str) -> str:
    return _TOKEN_RE.sub(_REPLACEMENT, text)


class TokenScrubFilter(logging.Filter):
    """Che `token=...` trong record.msg và mọi arg chuỗi. Luôn cho record đi qua."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and "token=" in record.msg.lower():
            record.msg = scrub_token(record.msg)
        args = record.args
        if isinstance(args, tuple) and any(
            isinstance(a, str) and "token=" in a.lower() for a in args
        ):
            record.args = tuple(scrub_token(a) if isinstance(a, str) else a for a in args)
        elif isinstance(args, dict):
            record.args = {
                k: scrub_token(v) if isinstance(v, str) else v for k, v in args.items()
            }
        return True


def install_uvicorn_scrub_filters() -> None:
    """Gắn filter vào logger của uvicorn (idempotent).

    Không khai `uvicorn.*` trong LOGGING của Django: dictConfig sẽ XOÁ handler mà
    gunicorn UvicornWorker đã gắn cho hai logger này lúc worker khởi động → mất
    hẳn access log. Gắn filter lên logger (không đụng handler) là cách an toàn.
    """
    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, TokenScrubFilter) for f in logger.filters):
            logger.addFilter(TokenScrubFilter())
