"""Metrics Prometheus của module Media.

Cùng khuôn fallback với `apps/foxia/metrics.py`: prometheus_client là phụ thuộc
tuỳ chọn, thiếu nó thì mọi lời gọi thành no-op chứ không làm chết request (spec §7).
"""

from __future__ import annotations

try:
    from prometheus_client import Counter

    _ENABLED = True
except Exception:  # pragma: no cover - prometheus là optional
    _ENABLED = False

    class _Noop:
        def labels(self, *_args, **_kwargs):
            return self

        def inc(self, *_args, **_kwargs):
            return None

    def Counter(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Noop()


media_proxy_requests_total = Counter(
    "desi_media_proxy_requests_total",
    "Request proxy Media theo route, chế độ credential và kết quả",
    ["route", "mode", "status"],
)
