"""Prometheus metrics cho job xuất file chạy nền.

Bám đúng khuôn ``apps/common/http_metrics.py``: fallback no-op khi
``prometheus_client`` chưa cài (không có nhánh code nào phải guard import), và
tương thích multiprocess mode (``PROMETHEUS_MULTIPROC_DIR=/tmp/prom_multiproc``
— worker Celery là tiến trình riêng với gunicorn, ``/api/orders/metrics`` gộp
số liệu qua ``MultiProcessCollector`` nên các collector ở đây PHẢI khai kiểu
``Counter``/``Histogram`` chuẩn của thư viện, không tự chế registry riêng —
sai kiểu là mất khả năng gộp giữa các worker.

Vì sao cần: trước module này, một job export chết lặng lẽ (storage lỗi, quá
soft_time_limit, kind không còn hỗ trợ...) chỉ nằm trong log — không ai biết
tỷ lệ fail đang tăng cho tới khi người dùng report "xuất file không ra".
"""

from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram

    _ENABLED = True
except Exception:  # pragma: no cover - prometheus là optional, giống http_metrics.py
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


export_jobs_total = Counter(
    "export_jobs_total",
    "Số job export theo loại và kết quả cuối cùng.",
    ["kind", "status"],
)

export_job_duration_seconds = Histogram(
    "export_job_duration_seconds",
    "Thời gian dựng file export, đo từ lúc job chuyển sang RUNNING.",
    ["kind"],
    # Bucket bám thực tế: dưới 5s là file nhỏ, trên 300s là sắp chạm
    # soft_time_limit (EXPORT_SOFT_TIME_LIMIT), 600s gần time_limit cứng.
    buckets=(1, 5, 15, 30, 60, 120, 300, 600),
)

export_job_rows = Histogram(
    "export_job_rows",
    "Số dòng mỗi file export thành công.",
    ["kind"],
    buckets=(100, 1000, 5000, 10000, 20000, 50000),
)
