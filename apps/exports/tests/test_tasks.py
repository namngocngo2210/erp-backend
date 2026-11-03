from unittest.mock import patch

import pytest
from django.utils import timezone
from prometheus_client import REGISTRY

from apps.exports.application import registry as export_registry
from apps.exports.application.export_job_service import ExportJobService
from apps.exports.application.registry import ExportDefinition, register_export
from apps.exports.infrastructure.tasks import purge_expired_exports, run_export
from apps.exports.models import ExportJob, ExportJobStatus

pytestmark = pytest.mark.django_db


def _export_jobs_total(kind: str, status: str) -> float:
    """Đọc counter thật từ registry mặc định của prometheus_client.

    `PROMETHEUS_MULTIPROC_DIR` không được set trong môi trường test (chỉ set ở
    tiến trình gunicorn/celery worker thật) nên `Counter` ở đây chạy chế độ
    single-process bình thường — `REGISTRY.get_sample_value` đọc thẳng giá trị
    in-memory, không cần `MultiProcessCollector`. Cùng cách
    `apps/common/tests/test_asgi_concurrency.py` đang dùng cho
    `desi_http_gate_timeouts_total`. Trả về 0.0 khi chưa có sample nào (registry
    global, số liệu cộng dồn qua các test — luôn so bằng delta before/after,
    không so bằng giá trị tuyệt đối).
    """
    return REGISTRY.get_sample_value("export_jobs_total", {"kind": kind, "status": status}) or 0.0


class _CancelMidRunExport(ExportDefinition):
    """Mô phỏng người dùng bấm Huỷ ĐÚNG LÚC worker đang ghi file, với tổng số
    dòng DƯỚI ``_PROGRESS_EVERY`` (1000) — ca mà bug Important #1 xảy ra:
    ``progress()`` không bao giờ chạm mốc kiểm cờ huỷ nên job < 1000 dòng chạy
    tới hết bất kể đã bị huỷ.
    """

    kind = "fake_cancel_mid_run"

    def validate(self, user, raw_params):
        return {"n": int(raw_params.get("n", 5))}

    def check_permission(self, user, params):
        return None

    def estimate(self, params):
        return params["n"]

    def file_name(self, params):
        return "fake_cancel_mid_run.xlsx"

    def write(self, params, writer, progress):
        writer.write_header(["i"])
        for i in range(params["n"]):
            writer.write_row([i])
            progress(i + 1)
            if i == 0:
                # Set thẳng DB (không qua ExportJobService.cancel(), vốn cần
                # celery_task_id + broker) — mô phỏng đúng hiệu ứng của việc user
                # bấm Huỷ trong lúc worker đang chạy dở dòng đầu tiên.
                ExportJob.objects.filter(
                    kind=self.kind, status=ExportJobStatus.RUNNING
                ).update(status=ExportJobStatus.CANCELLED)


@pytest.fixture
def cancel_mid_run_export():
    if "fake_cancel_mid_run" not in export_registry._REGISTRY:
        register_export(_CancelMidRunExport())
    return export_registry._REGISTRY["fake_cancel_mid_run"]


def test_run_export_writes_file_and_marks_success(fake_export, export_user):
    before = _export_jobs_total("fake", "success")
    job, _ = ExportJobService.create(export_user, "fake", {"n": 5})
    with patch("apps.exports.infrastructure.tasks.export_storage") as storage:
        run_export(str(job.id))
    job.refresh_from_db()
    assert job.status == ExportJobStatus.SUCCESS
    assert job.progress_rows == 5
    assert job.file_key.startswith(f"exports/{export_user.id}/{job.id}/")
    storage.upload_file.assert_called_once()
    # Khoá luôn nhánh happy path của Task 20: `export_jobs_total{kind="fake",
    # status="success"}` phải tăng đúng 1 — không chỉ khoá tên metric/label như
    # test_metrics.py (đọc `job.status` sai, vd `job.kind`, sẽ không lộ ở đó vì
    # cả hai đều là string hợp lệ cho label `status`).
    assert _export_jobs_total("fake", "success") == before + 1


def test_run_export_marks_failed_on_error(fake_export, export_user):
    before = _export_jobs_total("fake", "failed")
    job, _ = ExportJobService.create(export_user, "fake", {"n": 5})
    with patch("apps.exports.infrastructure.tasks.export_storage") as storage:
        storage.upload_file.side_effect = RuntimeError("minio down")
        run_export(str(job.id))
    job.refresh_from_db()
    assert job.status == ExportJobStatus.FAILED
    assert "Không lưu được file" in job.error_message
    assert _export_jobs_total("fake", "failed") == before + 1


def test_run_export_returns_early_when_cancelled_before_start(fake_export, export_user):
    """Nhánh early-return: job đã CANCELLED trước khi worker kịp nhận task."""
    job, _ = ExportJobService.create(export_user, "fake", {"n": 5000})
    ExportJob.objects.filter(id=job.id).update(status=ExportJobStatus.CANCELLED)
    with patch("apps.exports.infrastructure.tasks.export_storage") as storage:
        run_export(str(job.id))
    job.refresh_from_db()
    assert job.status == ExportJobStatus.CANCELLED
    storage.upload_file.assert_not_called()


def test_run_export_stays_cancelled_when_cancelled_mid_run(cancel_mid_run_export, export_user):
    """Regression Important #1: job < _PROGRESS_EVERY dòng bị huỷ GIỮA CHỪNG (worker
    đang chạy, không phải trước khi start) phải dừng ở CANCELLED — không được
    `mark_success` ghi đè ngược, và KHÔNG được upload file đã huỷ lên MinIO.
    """
    before = _export_jobs_total("fake_cancel_mid_run", "cancelled")
    job, _ = ExportJobService.create(export_user, "fake_cancel_mid_run", {"n": 5})
    with patch("apps.exports.infrastructure.tasks.export_storage") as storage:
        run_export(str(job.id))
    job.refresh_from_db()
    assert job.status == ExportJobStatus.CANCELLED
    storage.upload_file.assert_not_called()
    # Nhánh `except ExportCancelled` không gọi `mark_failed` — job status vẫn
    # là CANCELLED (đặt trực tiếp bởi fake export, không qua ExportJobService).
    # Khoá cặp nhãn status="cancelled" riêng với "failed" ở hai test trên.
    assert _export_jobs_total("fake_cancel_mid_run", "cancelled") == before + 1


def test_purge_deletes_expired_files(fake_export, export_user):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS,
        file_key="exports/x/y.xlsx",
        expires_at=timezone.now() - timezone.timedelta(hours=1),
    )
    with patch("apps.exports.infrastructure.tasks.export_storage") as storage:
        purge_expired_exports()
    job.refresh_from_db()
    assert job.status == ExportJobStatus.EXPIRED
    storage.delete_object.assert_called_once_with("exports/x/y.xlsx")


def test_purge_fails_stuck_running_jobs(fake_export, export_user):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.RUNNING,
        started_at=timezone.now() - timezone.timedelta(hours=3),
    )
    with patch("apps.exports.infrastructure.tasks.export_storage"):
        purge_expired_exports()
    job.refresh_from_db()
    assert job.status == ExportJobStatus.FAILED


def test_purge_fails_stuck_pending_jobs(fake_export, export_user):
    """final-fix-report mục 3c: job PENDING kẹt (worker-export không bao giờ nhận
    task) là bất tử trước fix — không RUNNING nên nhánh cứu cũ bỏ qua, không
    SUCCESS/expires_at nên không bị dọn ở nhánh trên, và unique constraint
    dedupe-active khiến bấm lại chỉ nhận về đúng job zombie đó. Job PENDING không
    có `started_at` nên phải so bằng `created_at`."""
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.PENDING,
        created_at=timezone.now() - timezone.timedelta(hours=3),
    )
    with patch("apps.exports.infrastructure.tasks.export_storage"):
        purge_expired_exports()
    job.refresh_from_db()
    assert job.status == ExportJobStatus.FAILED


def test_purge_recent_pending_job_is_not_touched(fake_export, export_user):
    """Đối chứng: job PENDING vừa tạo (chưa quá ngưỡng) không bị đánh FAILED oan —
    worker-export có thể đơn giản là chưa kịp nhận task."""
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    with patch("apps.exports.infrastructure.tasks.export_storage"):
        purge_expired_exports()
    job.refresh_from_db()
    assert job.status == ExportJobStatus.PENDING


def test_purge_survives_soft_time_limit_and_still_rescues_stuck_jobs(fake_export, export_user):
    """final-fix-report mục 3b: trước fix, `SoftTimeLimitExceeded` ném lúc
    `delete_object()` bị nuốt bởi `except Exception: ... continue` trần — vòng lặp
    chạy tiếp tới hard `time_limit` rồi bị SIGKILL, nên khối cứu job kẹt (đặt SAU
    vòng lặp) không bao giờ chạy tới. Test này ép `delete_object` ném
    `SoftTimeLimitExceeded` ở lượt xoá ĐẦU TIÊN rồi xác nhận task vẫn hoàn thành
    (không crash) VÀ vẫn cứu được job RUNNING/PENDING kẹt."""
    from celery.exceptions import SoftTimeLimitExceeded

    expired_job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=expired_job.id).update(
        status=ExportJobStatus.SUCCESS,
        file_key="exports/x/y.xlsx",
        expires_at=timezone.now() - timezone.timedelta(hours=1),
    )
    stuck_job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=stuck_job.id).update(
        status=ExportJobStatus.RUNNING,
        started_at=timezone.now() - timezone.timedelta(hours=3),
    )
    with patch("apps.exports.infrastructure.tasks.export_storage") as storage:
        storage.delete_object.side_effect = SoftTimeLimitExceeded()
        purge_expired_exports()  # không được ném ra ngoài
    expired_job.refresh_from_db()
    stuck_job.refresh_from_db()
    # Object xoá lỗi vì timeout — job hết hạn KHÔNG được chuyển EXPIRED (khác nhánh
    # `except Exception` xoá lỗi thường, nơi lỗi bị nuốt và job vẫn coi như đã dọn).
    assert expired_job.status == ExportJobStatus.SUCCESS
    # Nhưng khối cứu job kẹt RUNNING vẫn phải chạy tới, không bị timeout chặn lại.
    assert stuck_job.status == ExportJobStatus.FAILED


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("don-hang.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("don-hang.csv", "text/csv; charset=utf-8"),
        ("don-hang.CSV", "text/csv; charset=utf-8"),
        # Không đuôi → xlsx, khớp `_make_writer` (chỉ .csv mới đi đường CSV).
        ("don-hang", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("don-hang.pdf", "application/octet-stream"),
    ],
)
def test_guess_content_type_matches_writer_convention(file_name, expected):
    """Lúc upload lên MinIO và lúc trả file về trình duyệt phải ra CÙNG một
    Content-Type — nên cả hai gọi chung hàm này thay vì tự đoán riêng."""
    from apps.exports.content_types import guess_content_type

    assert guess_content_type(file_name) == expected
