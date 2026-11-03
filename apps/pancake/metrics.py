"""Metrics Prometheus của tích hợp Pancake — cùng khuôn fallback với foxia/metrics.py."""

from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram

    _ENABLED = True
except Exception:  # pragma: no cover - prometheus là optional
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


pancake_poll_pages_total = Counter(
    "desi_pancake_poll_pages_total",
    "Số lượt poll một page Pancake theo kết quả",
    ["status"],  # ok | error | token_rejected
)

pancake_leads_total = Counter(
    "desi_pancake_leads_total",
    "Số lead Pancake tạo mới theo trạng thái tạo đơn",
    ["push_status"],
)

pancake_poll_duration_seconds = Histogram(
    "desi_pancake_poll_duration_seconds",
    "Thời lượng poll một page",
    buckets=(0.5, 1.0, 2.0, 5.0, 15.0, 30.0, 60.0, 120.0),
)
