"""Test gate concurrency HTTP ở tầng ASGI — sự cố 2026-08-03 10:34.

Burst tạo đơn buổi sáng: mỗi request sync view dưới UvicornWorker được asgiref
cấp MỘT thread riêng (ThreadSensitiveContext không có pool chung) nên ~90 request
đồng thời giữ ~90 connection Postgres (CONN_MAX_AGE=0) → kịch trần
max_connections=100, FATAL "too many clients" cho cả worker/beat. Gate này ép
request thừa XẾP HÀNG (await, không tốn thread/connection) thay vì từ chối.
"""

import asyncio

from django.test import SimpleTestCase

from apps.common.asgi_concurrency import HttpConcurrencyLimiter


def _http_scope(path="/api/orders"):
    return {"type": "http", "path": path, "method": "GET", "headers": []}


class _TrackingApp:
    """App ASGI giả: đếm số request đang chạy đồng thời, chờ lệnh thả."""

    def __init__(self):
        self.in_flight = 0
        self.max_in_flight = 0
        self.release = asyncio.Event()
        self.handled = 0

    async def __call__(self, scope, receive, send):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await self.release.wait()
        finally:
            self.in_flight -= 1
        self.handled += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def _collect_response(limiter, scope):
    messages = []

    async def receive():  # pragma: no cover - không dùng trong test
        return {"type": "http.request"}

    async def send(message):
        messages.append(message)

    await limiter(scope, receive, send)
    return messages


class HttpConcurrencyLimiterTests(SimpleTestCase):
    def test_caps_concurrent_http_requests(self):
        async def run():
            app = _TrackingApp()
            limiter = HttpConcurrencyLimiter(app, max_concurrency=2, queue_timeout=5)
            tasks = [
                asyncio.create_task(_collect_response(limiter, _http_scope()))
                for _ in range(6)
            ]
            await asyncio.sleep(0.05)
            observed_at_peak = app.max_in_flight
            app.release.set()
            await asyncio.gather(*tasks)
            return observed_at_peak, app.handled

        observed_at_peak, handled = asyncio.run(run())
        self.assertEqual(observed_at_peak, 2)
        self.assertEqual(handled, 6)

    def test_queue_wait_exceeding_timeout_returns_503(self):
        async def run():
            app = _TrackingApp()
            limiter = HttpConcurrencyLimiter(
                app, max_concurrency=1, queue_timeout=0.05
            )
            holder = asyncio.create_task(_collect_response(limiter, _http_scope()))
            await asyncio.sleep(0.01)
            queued_messages = await _collect_response(limiter, _http_scope())
            app.release.set()
            await holder
            return queued_messages, app.handled

        messages, handled = asyncio.run(run())
        start = next(m for m in messages if m["type"] == "http.response.start")
        self.assertEqual(start["status"], 503)
        self.assertEqual(handled, 1)

    def test_timeout_increments_gate_counter_with_coarse_path(self):
        """Mỗi 503 của gate phải đếm được — alert HttpGateTimeouts sống nhờ nó.

        Path chứa order_id phải gộp về prefix 2 cấp, nếu không mỗi đơn một
        series và Prometheus nổ cardinality.
        """
        from prometheus_client import REGISTRY

        def _count():
            return (
                REGISTRY.get_sample_value(
                    "desi_http_gate_timeouts_total",
                    {"path_prefix": "/api/orders"},
                )
                or 0.0
            )

        before = _count()

        async def run():
            app = _TrackingApp()
            limiter = HttpConcurrencyLimiter(
                app, max_concurrency=1, queue_timeout=0.05
            )
            holder = asyncio.create_task(_collect_response(limiter, _http_scope()))
            await asyncio.sleep(0.01)
            await _collect_response(limiter, _http_scope(path="/api/orders/550354"))
            app.release.set()
            await holder

        asyncio.run(run())
        self.assertEqual(_count(), before + 1)

    def test_bypass_path_skips_gate(self):
        async def run():
            app = _TrackingApp()
            limiter = HttpConcurrencyLimiter(
                app,
                max_concurrency=1,
                queue_timeout=5,
                bypass_prefixes=("/api/healthz",),
            )
            holder = asyncio.create_task(_collect_response(limiter, _http_scope()))
            await asyncio.sleep(0.01)
            self.assertEqual(app.in_flight, 1)
            probe = asyncio.create_task(
                _collect_response(limiter, _http_scope(path="/api/healthz"))
            )
            await asyncio.sleep(0.05)
            in_flight_with_probe = app.in_flight
            app.release.set()
            await asyncio.gather(holder, probe)
            return in_flight_with_probe

        in_flight_with_probe = asyncio.run(run())
        self.assertEqual(in_flight_with_probe, 2)

    def test_prod_wiring_bypasses_token_refresh(self):
        """Bão 2026-08-04 09:13: refresh token 503 → user văng phiên giữa burst.

        Refresh phải nằm ngoài gate: rẻ (verify JWT + 1 write nhỏ), còn mất phiên
        thì user retry đăng nhập dồn dập chỉ làm bão nặng thêm.
        """
        from config.asgi import application

        limiter = application.application_mapping["http"]
        self.assertIn("/api/users/token/refresh", limiter.bypass_prefixes)

    def test_non_http_scope_passes_through_ungated(self):
        async def run():
            app = _TrackingApp()
            limiter = HttpConcurrencyLimiter(app, max_concurrency=1, queue_timeout=5)
            holder = asyncio.create_task(_collect_response(limiter, _http_scope()))
            await asyncio.sleep(0.01)
            ws = asyncio.create_task(
                _collect_response(limiter, {"type": "websocket", "path": "/ws/crm"})
            )
            await asyncio.sleep(0.05)
            in_flight_with_ws = app.in_flight
            app.release.set()
            await asyncio.gather(holder, ws)
            return in_flight_with_ws

        in_flight_with_ws = asyncio.run(run())
        self.assertEqual(in_flight_with_ws, 2)
