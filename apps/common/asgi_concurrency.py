"""Gate concurrency HTTP ở tầng ASGI — chống bão connection 2026-08-03.

Dưới UvicornWorker, Django chạy mỗi sync view trong ThreadSensitiveContext
riêng: asgiref (>=3.x) cấp một single-thread executor CHO TỪNG request, không có
pool chung nào chặn lại. Burst tạo đơn buổi sáng (75 POST /api/orders/3 phút)
vì thế leo thẳng lên ~90 thread ~ ~90 connection Postgres (CONN_MAX_AGE=0,
chưa có PgBouncer) → kịch trần max_connections → FATAL "too many clients" đổ
lên cả worker/beat lẫn request thường.

Gate này giữ request THỪA ở trạng thái ``await`` trên semaphore — chỉ tốn một
coroutine, KHÔNG tốn thread, KHÔNG tốn connection — thay vì để nó chen vào DB.
Chọn xếp hàng thay vì từ chối kiểu ``limit_concurrency`` của uvicorn (503 ngay):
giữa burst livestream, trả 503 cho POST /api/orders là mất đơn thật; xếp hàng
chỉ đội thêm latency. Sau ``queue_timeout`` giây chưa tới lượt mới trả 503 để
không giam client vô hạn (gunicorn timeout cũng sẽ chém ở 30s).

Trần là TRÊN MỖI worker process (gunicorn --workers N → tổng = N × trần).
Websocket không đi qua gate — đã có semaphore riêng (WS_HANDSHAKE_DB_CONCURRENCY
/ WS_BROADCAST_DB_CONCURRENCY, sự cố 2026-07-23). ``/api/healthz`` và endpoint
healthcheck của compose phải nằm trong ``bypass_prefixes``: để probe kẹt hàng
đợi lúc burst là container bị đánh unhealthy → restart giữa bão.
"""

import asyncio
import logging

from apps.common.http_metrics import http_gate_timeouts_total

logger = logging.getLogger(__name__)

_RETRY_AFTER_SECONDS = b"3"


def _path_prefix(path: str) -> str:
    """Gộp path về 2 cấp đầu ("/api/orders/550354" → "/api/orders").

    Gate đứng trước URL resolver nên không có route pattern; path thô làm label
    thì mỗi order_id là một series mới — 2 cấp đủ để biết cụm endpoint nào bị
    nghẽn mà tập giá trị vẫn hữu hạn.
    """
    parts = path.split("/", 3)
    return "/".join(parts[:3]) or "/"


class HttpConcurrencyLimiter:
    def __init__(
        self,
        app,
        max_concurrency: int,
        queue_timeout: float,
        bypass_prefixes: tuple[str, ...] = (),
    ):
        self.app = app
        self.queue_timeout = queue_timeout
        self.bypass_prefixes = bypass_prefixes
        # Semaphore tạo lazy TRONG event loop của worker: khởi tạo sớm ở import
        # time sẽ dính loop khác (asyncio.run trong management command, test...).
        self._max_concurrency = max_concurrency
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrency)
        return self._semaphore

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in self.bypass_prefixes):
            await self.app(scope, receive, send)
            return

        semaphore = self._get_semaphore()
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=self.queue_timeout)
        except TimeoutError:
            logger.warning(
                "http concurrency gate timeout: path=%s waited=%.1fs",
                path,
                self.queue_timeout,
            )
            http_gate_timeouts_total.labels(path_prefix=_path_prefix(path)).inc()
            await self._send_503(send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            semaphore.release()

    @staticmethod
    async def _send_503(send):
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", _RETRY_AFTER_SECONDS),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"detail": "H\\u1ec7 th\\u1ed1ng \\u0111ang qu\\u00e1 t\\u1ea3i, vui l\\u00f2ng th\\u1eed l\\u1ea1i."}',
            }
        )
