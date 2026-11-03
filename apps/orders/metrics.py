"""Prometheus metrics for the orders app.

Graceful fallback: if `prometheus_client` is not installed, all counters
become no-op objects so application code never needs to guard the import.
Install `prometheus-client` and expose `/metrics` to start collecting.

Multi-process mode (gunicorn/uvicorn workers):
    Set env `PROMETHEUS_MULTIPROC_DIR=/tmp/prom_multiproc` in every process
    and create the directory. The `/metrics` endpoint will aggregate counts
    across processes automatically.
"""
from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram

    _ENABLED = True
except Exception:  # pragma: no cover - prometheus optional
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


# ---- Landing ingest ---------------------------------------------------------

landing_ingest_received_total = Counter(
    "desi_landing_ingest_received_total",
    "Landing ingest HTTP requests received (after token check).",
    ["token"],  # ok | invalid
)

landing_ingest_validated_total = Counter(
    "desi_landing_ingest_validated_total",
    "Landing ingest payloads after serializer validation.",
    ["result"],  # valid | invalid
)

landing_ingest_enqueued_total = Counter(
    "desi_landing_ingest_enqueued_total",
    "Landing ingest tasks enqueued to Go task-worker.",
    ["result"],  # success | failed
)

landing_ingest_processed_total = Counter(
    "desi_landing_ingest_processed_total",
    "Landing ingest tasks processed by the Go task-worker.",
    ["result"],  # created | dry_run_skipped | failed
)

landing_ingest_staff_hint_total = Counter(
    "desi_landing_ingest_staff_hint_total",
    "Landing ingest payloads designating a sale via staff_username.",
    ["result"],  # assigned | unknown_username
)

landing_ingest_process_latency_seconds = Histogram(
    "desi_landing_ingest_process_latency_seconds",
    "End-to-end Go task latency for landing ingest.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)

__all__ = [
    "_ENABLED",
    "landing_ingest_received_total",
    "landing_ingest_validated_total",
    "landing_ingest_enqueued_total",
    "landing_ingest_processed_total",
    "landing_ingest_staff_hint_total",
    "landing_ingest_process_latency_seconds",
]
