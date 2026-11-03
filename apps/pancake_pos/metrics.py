"""Metrics Prometheus của Pancake POS — cùng khuôn fallback với apps/pancake/metrics.py."""

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


webhook_received_total = Counter(
    "desi_pancake_pos_webhook_received_total",
    "Số request webhook Pancake POS nhận theo loại sự kiện và kết quả",
    # result: stored | duplicate | unauthorized | disabled | invalid | too_large | error
    ["event_type", "result"],
)

webhook_processed_total = Counter(
    "desi_pancake_pos_webhook_processed_total",
    "Số sự kiện webhook Pancake POS đã xử lý theo loại và trạng thái",
    ["event_type", "status"],  # processed | ignored | error
)
