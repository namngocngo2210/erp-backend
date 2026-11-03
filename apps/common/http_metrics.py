"""RED metrics cho tầng HTTP — Rate / Errors / Duration theo từng endpoint.

Vì sao cần (audit tải 2026-07-24): trước module này, toàn hệ thống chỉ có 5 metric
Prometheus và cả 5 đều riêng cho landing-ingest. Mọi alert đều ở tầng hạ tầng
(PostgresDown, ContainerOOMKill, HostHighCpu...). Nghĩa là khi người dùng kêu
"hệ thống chậm", không có cách nào biết endpoint nào chậm ngoài việc đoán —
đúng tình huống đã lặp lại ở report-precompute-timeout và resale-tracking.

Chống nổ cardinality
--------------------
Label ``route`` dùng **URL pattern** (``/api/orders/<int:order_id>``) chứ KHÔNG
dùng path thật (``/api/orders/48213``). Dùng path thật thì mỗi đơn hàng đẻ ra một
time series riêng — vài chục nghìn series, Prometheus sẽ sập trước khi hệ thống sập.

Multiprocess
------------
Gunicorn chạy nhiều worker; ``PROMETHEUS_MULTIPROC_DIR`` phải được set cho mọi
tiến trình để ``/api/orders/metrics`` gộp được số liệu (xem metrics_view.py).
"""

from __future__ import annotations

import asyncio
import time

try:
    from prometheus_client import Counter, Histogram

    _ENABLED = True
except Exception:  # pragma: no cover - prometheus là optional, giống apps/orders/metrics.py
    _ENABLED = False

    class _Noop:
        def labels(self, *_args, **_kwargs):
            return self

        def inc(self, *_args, **_kwargs):
            return None

        def observe(self, *_args, **_kwargs):
            return None

    def Counter(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Noop()

    def Histogram(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Noop()


# Bucket chọn theo ngưỡng có ý nghĩa nghiệp vụ, không dùng default của thư viện:
# dưới 250ms là mượt, 1s là người dùng bắt đầu thấy khựng, trên 5s là hỏng.
# Ít bucket = ít series (mỗi bucket là một series riêng cho mỗi tổ hợp label).
# 2.0 trùng ngưỡng alert HttpEndpointSlow: thiếu nó, histogram_quantile nội suy
# trong (1; 2.5] nên vài request 1,1s cũng báo "p95 = 2.0s".
_DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0, 30.0)

http_request_duration_seconds = Histogram(
    "desi_http_request_duration_seconds",
    "Thời gian xử lý request HTTP, theo route pattern.",
    ["method", "route", "status"],
    buckets=_DURATION_BUCKETS,
)

http_requests_total = Counter(
    "desi_http_requests_total",
    "Tổng số request HTTP đã phục vụ, theo route pattern.",
    ["method", "route", "status"],
)

# Counter chứ không phải gauge — và đó là điểm mấu chốt. Mọi cảnh báo về connection
# Postgres trước nay dựa vào `pg_stat_activity_count`, một gauge lấy theo scrape
# interval ~15s: một cơn bão 10 giây tàng hình hoàn toàn với Prometheus trong khi app
# đã ném hàng trăm FATAL (sự cố 2026-07-23 kéo 2 phút, 2026-07-29 phát hiện qua log).
# Counter tích luỹ nên không bỏ sót bão ngắn, và nó đo đúng thứ người dùng chịu:
# request bị hỏng, chứ không phải "số connection đang mở".
db_connection_errors_total = Counter(
    "desi_db_connection_errors_total",
    "Số request chết vì không mở được connection Postgres.",
    ["reason"],
)

# Cùng triết lý counter-không-bỏ-sót như trên, cho gate concurrency HTTP (sự cố
# 2026-08-03 13:46): bão 503 kéo đúng 2 phút và chỉ được biết khi người dùng
# kêu — gate lúc đó chỉ ghi log WARNING, không có metric nào để alert. Label là
# path prefix 2 cấp ("/api/orders") chứ không phải path đầy đủ: gate đứng TRƯỚC
# URL resolver của Django nên không có route pattern để dùng, mà path thô thì nổ
# cardinality (mỗi order_id một series).
http_gate_timeouts_total = Counter(
    "desi_http_gate_timeouts_total",
    "Số request bị gate concurrency trả 503 vì chờ quá queue_timeout.",
    ["path_prefix"],
)

# Endpoint /metrics tự nó không đáng đo — Prometheus scrape mỗi 15s sẽ tạo nhiễu
# thường trực và tự đo chính mình.
_EXCLUDED_ROUTES = frozenset({"metrics"})


def _route_label(request) -> str:
    """URL pattern của request, hoặc nhãn gộp khi không resolve được.

    ``resolver_match`` chỉ có SAU khi Django resolve xong URL, nên chỉ đọc được
    ở nhánh sau ``get_response``. Request 404 không khớp route nào sẽ gộp hết vào
    một nhãn duy nhất — nếu không, mọi URL rác từ bot quét đều thành series mới.
    """
    match = getattr(request, "resolver_match", None)
    if match is None:
        return "<unmatched>"
    # `route` là chuỗi pattern đã đăng ký; fallback về view name cho các URL
    # cũ khai báo bằng re_path (không có thuộc tính route).
    return getattr(match, "route", None) or match.view_name or "<unknown>"


class HttpMetricsMiddleware:
    """Đo thời lượng + đếm request cho mọi endpoint.

    Đặt gần đầu chuỗi MIDDLEWARE để bao trọn thời gian xử lý phía server (kể cả
    thời gian của các middleware bên dưới), chỉ sau RequestIdMiddleware để mọi
    log trong request vẫn mang request_id.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.perf_counter()
        status = "500"  # view ném exception mà không ai bắt → vẫn phải ghi nhận
        try:
            response = self.get_response(request)
            status = str(response.status_code)
            return response
        except asyncio.CancelledError:
            # Client ngắt kết nối giữa chừng: ASGIHandler của Django cancel task và
            # nuốt lỗi, KHÔNG gửi response nào. Để mặc định "500" thì mỗi lần người
            # dùng đóng tab/F5 thành một lỗi server ảo — sự cố 2026-09-23:
            # HttpEndpointDown{route="<unmatched>"} kêu critical vì 1/1 request
            # "500" mà access log không có dòng 5xx nào. 499 theo quy ước nginx.
            status = "499"
            raise
        finally:
            route = _route_label(request)
            if route not in _EXCLUDED_ROUTES:
                elapsed = time.perf_counter() - start
                labels = (request.method, route, status)
                http_request_duration_seconds.labels(*labels).observe(elapsed)
                http_requests_total.labels(*labels).inc()
