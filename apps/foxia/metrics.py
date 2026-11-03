"""Metrics Prometheus của tích hợp Foxia.

Cùng khuôn fallback với `apps/common/http_metrics.py`: prometheus_client là phụ
thuộc tuỳ chọn, thiếu nó thì mọi lời gọi thành no-op chứ không làm chết đồng bộ.

Ba câu hỏi vận hành mà bộ metric này phải trả lời được:
  1. Đồng bộ có còn chạy không?           → foxia_sync_runs_total{status}
  2. Chạy có lâu bất thường không?        → foxia_sync_duration_seconds
  3. Khoá còn sống không?                 → foxia_auth_ok (alert khi = 0)

Cảnh báo đề xuất (monitoring/):
  - foxia_auth_ok == 0 quá 10 phút → khoá bị thu hồi, cần người xử lý.
  - không có foxia_sync_runs_total{status="success"} tăng trong 2 giờ → cron chết.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter, Gauge, Histogram

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

        def set(self, *_args, **_kwargs):
            return None

    def Counter(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Noop()

    def Gauge(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Noop()

    def Histogram(*_args, **_kwargs):  # type: ignore[no-redef]
        return _Noop()


# Bucket theo thang phút chứ không phải mili-giây: một lượt đồng bộ bình thường
# vài giây, lượt kéo nhiều trang vài phút, và trần task là 900s.
_DURATION_BUCKETS = (1.0, 5.0, 15.0, 60.0, 180.0, 300.0, 600.0, 900.0)

foxia_sync_runs_total = Counter(
    "desi_foxia_sync_runs_total",
    "Số lượt đồng bộ Foxia theo tài nguyên và kết quả",
    ["resource", "status"],
)

foxia_records_synced_total = Counter(
    "desi_foxia_records_synced_total",
    "Số bản ghi Foxia đã ghi vào hệ thống",
    ["resource"],
)

foxia_sync_duration_seconds = Histogram(
    "desi_foxia_sync_duration_seconds",
    "Thời lượng một lượt đồng bộ Foxia",
    ["resource"],
    buckets=_DURATION_BUCKETS,
)

foxia_auth_ok = Gauge(
    "desi_foxia_auth_ok",
    "1 = khoá API Foxia còn dùng được, 0 = bị thu hồi hoặc lỗi kết nối",
)


def observe_run(resource: str, status: str, records: int, duration_seconds: float) -> None:
    foxia_sync_runs_total.labels(resource=resource, status=status).inc()
    if records:
        foxia_records_synced_total.labels(resource=resource).inc(records)
    foxia_sync_duration_seconds.labels(resource=resource).observe(duration_seconds)


def set_auth_ok(ok: bool) -> None:
    foxia_auth_ok.set(1 if ok else 0)
