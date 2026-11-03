"""Trần đồng thời cho các truy vấn DB chạy trong lúc bắt tay WebSocket.

Sự cố 2026-07-23: một gunicorn worker recycle làm mọi WS client trên worker đó rớt
cùng một giây. Toàn bộ client reconnect đồng thời, và mỗi lần bắt tay mở một
connection Postgres (xác thực JWT ở middleware + nạp scope RBAC ở consumer). ~96
connection đồng thời đã cạn ``max_connections=100``; mọi request HTTP — kể cả
``/api/orders/reports/ads`` — trả 500 suốt ~2 phút.

Bản vá jitter phía frontend giảm xác suất dồn cục nhưng KHÔNG đủ làm lưới an toàn:
tab trình duyệt đang mở vẫn chạy bundle cũ nhiều ngày sau khi deploy, và mọi nguyên
nhân rớt hàng loạt khác (reload nginx, mạng chập, deploy) đều tái tạo đúng kịch bản.
Gate này chặn ở phía server nên không phụ thuộc hành vi client.

Trần thực tế của cả web = ``WS_HANDSHAKE_DB_CONCURRENCY`` × số gunicorn worker, vì
mỗi uvicorn worker là một process với event loop riêng.
"""

from __future__ import annotations

import asyncio

from django.conf import settings

DEFAULT_WS_HANDSHAKE_DB_CONCURRENCY = 8
# Gate thứ hai, cho đường broadcast (sự cố 2026-07-29). Trần thấp hơn handshake vì
# đây chỉ là FALLBACK: payload đủ `staff_id`/`channel_code` thì consumer không chạm
# DB chút nào. Giữ nhỏ để một call site sót field không kéo sập cả trần connection.
DEFAULT_WS_BROADCAST_DB_CONCURRENCY = 4

_gate: asyncio.Semaphore | None = None
_gate_loop: asyncio.AbstractEventLoop | None = None

_broadcast_gate: asyncio.Semaphore | None = None
_broadcast_gate_loop: asyncio.AbstractEventLoop | None = None


def _configured_limit(
    setting_name: str = "WS_HANDSHAKE_DB_CONCURRENCY",
    default: int = DEFAULT_WS_HANDSHAKE_DB_CONCURRENCY,
) -> int:
    raw = getattr(settings, setting_name, default)
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return default
    # 0/âm là cấu hình sai — rơi về mặc định thay vì tạo semaphore khoá chết.
    return limit if limit > 0 else default


def handshake_db_gate() -> asyncio.Semaphore:
    """Semaphore dùng chung cho mọi bắt tay WS trong cùng một event loop."""
    global _gate, _gate_loop

    loop = asyncio.get_running_loop()
    if _gate is None or _gate_loop is not loop:
        _gate = asyncio.Semaphore(_configured_limit())
        _gate_loop = loop
    return _gate


def broadcast_db_gate() -> asyncio.Semaphore:
    """Semaphore cho các truy vấn DB fallback lúc phân quyền event broadcast.

    Tách khỏi gate bắt tay để hai đường không tranh slot của nhau: một cơn bão
    broadcast không được làm nghẽn việc user mới kết nối, và ngược lại.
    """
    global _broadcast_gate, _broadcast_gate_loop

    loop = asyncio.get_running_loop()
    if _broadcast_gate is None or _broadcast_gate_loop is not loop:
        _broadcast_gate = asyncio.Semaphore(
            _configured_limit(
                "WS_BROADCAST_DB_CONCURRENCY", DEFAULT_WS_BROADCAST_DB_CONCURRENCY
            )
        )
        _broadcast_gate_loop = loop
    return _broadcast_gate


def reset_handshake_gate() -> None:
    """Xoá semaphore đã cache — cho test khi đổi setting hoặc đổi event loop."""
    global _gate, _gate_loop, _broadcast_gate, _broadcast_gate_loop

    _gate = None
    _gate_loop = None
    _broadcast_gate = None
    _broadcast_gate_loop = None
