"""Test cho collector Prometheus của export job.

Mục tiêu là khoá hợp đồng TÊN metric + TÊN label (đổi tên là breaking change cho
alert rule ở monitoring/). Chỉ gọi `.labels(...).inc()` là chưa đủ: Prometheus chỉ
raise khi SAI SỐ LƯỢNG label, còn sai tên (vd `kind` → `export_kind`) vẫn qua —
nên phải assert thẳng `_labelnames` và đọc lại giá trị từ registry sau khi ghi.
"""

from prometheus_client import REGISTRY

from apps.exports.infrastructure.metrics import (
    export_job_duration_seconds,
    export_job_rows,
    export_jobs_total,
)


def _sample(name: str, labels: dict) -> float | None:
    return REGISTRY.get_sample_value(name, labels)


def test_export_jobs_total_is_a_counter_keyed_by_kind_and_status():
    assert tuple(export_jobs_total._labelnames) == ("kind", "status")
    labels = {"kind": "orders-metrics-test", "status": "success"}
    before = _sample("export_jobs_total", labels) or 0.0

    export_jobs_total.labels(**labels).inc()
    export_jobs_total.labels(kind="orders-metrics-test", status="failed").inc()

    assert _sample("export_jobs_total", labels) == before + 1
    assert _sample("export_jobs_total", {**labels, "status": "failed"}) >= 1
    assert _sample("export_jobs_total", {**labels, "status": "cancelled"}) is None


def test_export_job_duration_seconds_is_a_histogram_keyed_by_kind():
    assert tuple(export_job_duration_seconds._labelnames) == ("kind",)
    labels = {"kind": "orders-duration-test"}
    count_before = _sample("export_job_duration_seconds_count", labels) or 0.0
    sum_before = _sample("export_job_duration_seconds_sum", labels) or 0.0

    export_job_duration_seconds.labels(**labels).observe(1.5)

    assert _sample("export_job_duration_seconds_count", labels) == count_before + 1
    assert _sample("export_job_duration_seconds_sum", labels) == sum_before + 1.5


def test_export_job_rows_is_a_histogram_keyed_by_kind():
    assert tuple(export_job_rows._labelnames) == ("kind",)
    labels = {"kind": "orders-rows-test"}
    count_before = _sample("export_job_rows_count", labels) or 0.0
    sum_before = _sample("export_job_rows_sum", labels) or 0.0

    export_job_rows.labels(**labels).observe(4200)

    assert _sample("export_job_rows_count", labels) == count_before + 1
    assert _sample("export_job_rows_sum", labels) == sum_before + 4200
