from __future__ import annotations

import logging
import os
import tempfile
import time

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.utils import timezone

from apps.exports.application.export_job_service import ExportJobService
from apps.exports.application.registry import UnknownExportKind, get_definition
from apps.exports.application.writers import CsvWriter, ExportCancelled, StreamingXlsxWriter
from apps.exports.content_types import guess_content_type
from apps.exports.infrastructure import export_storage
from apps.exports.infrastructure.event_publisher import publish_export_event
from apps.exports.infrastructure.metrics import (
    export_job_duration_seconds,
    export_job_rows,
    export_jobs_total,
)
from apps.exports.models import ACTIVE_STATUSES, ExportJob, ExportJobStatus

logger = logging.getLogger(__name__)


# Cập nhật tiến độ mỗi N dòng: mỗi lần là 1 UPDATE + 1 SELECT kiểm tra huỷ, đặt quá
# dày sẽ biến job 50k dòng thành 50k round-trip DB.
_PROGRESS_EVERY = 1000


def _make_writer(job: ExportJob, path: str):
    if job.file_name.endswith(".csv"):
        return CsvWriter(path)
    return StreamingXlsxWriter(path, sheet_title=job.kind[:31])


def _is_cancelled(job_id) -> bool:
    status = ExportJob.objects.filter(id=job_id).values_list("status", flat=True).first()
    return status == ExportJobStatus.CANCELLED


class _StorageFailed(Exception):
    """Bọc lỗi upload để phân biệt với lỗi khi dựng file — hai thông báo khác nhau.

    Đặt TRƯỚC ``run_export`` (khác thứ tự trong brief) để người đọc thấy tên này
    đã tồn tại trước khi nó được dùng — Python tra cứu tên lúc runtime nên đặt sau
    vẫn chạy đúng, nhưng đọc từ trên xuống thì gây khựng.
    """


@shared_task(
    bind=True,
    # BẮT BUỘC override: CELERY_TASK_SOFT_TIME_LIMIT toàn cục là 30s, không đủ cho
    # một file 50.000 dòng.
    soft_time_limit=settings.EXPORT_SOFT_TIME_LIMIT,
    time_limit=settings.EXPORT_SOFT_TIME_LIMIT + 60,
    # KHÔNG retry tự động: một job export chạy lại nguyên vẹn là nhân đôi tải mà
    # người dùng không biết. Panel có nút "Thử lại" để họ tự quyết.
    max_retries=0,
)
def run_export(self, job_id: str) -> None:
    job = ExportJob.objects.filter(id=job_id).first()
    if job is None:
        logger.warning("run_export: không thấy job %s", job_id)
        return
    if job.status not in ACTIVE_STATUSES:
        # Đã bị huỷ trước khi worker nhận task.
        return

    ExportJobService.mark_running(job.id)
    job.refresh_from_db()
    publish_export_event(job)

    # time.monotonic() (KHÔNG phải time.time()): không bị lùi/nhảy khi NTP chỉnh
    # đồng hồ hệ thống giữa lúc job chạy — job export có thể kéo dài vài phút.
    started_monotonic = time.monotonic()
    written = 0

    def progress(rows_done: int) -> None:
        nonlocal written
        written = rows_done
        if rows_done % _PROGRESS_EVERY:
            return
        ExportJob.objects.filter(id=job.id).update(progress_rows=rows_done)
        current = ExportJob.objects.filter(id=job.id).values_list("status", flat=True).first()
        if current == ExportJobStatus.CANCELLED:
            raise ExportCancelled()
        job.progress_rows = rows_done
        publish_export_event(job)

    suffix = os.path.splitext(job.file_name)[1] or ".xlsx"
    tmp_path = ""
    try:
        definition = get_definition(job.kind)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        writer = _make_writer(job, tmp_path)
        try:
            definition.write(job.params, writer, progress)
        finally:
            writer.close()

        # Job ngắn không bao giờ chạm mốc _PROGRESS_EVERY nên `progress` chưa từng
        # kiểm cờ huỷ — kiểm ở đây để huỷ luôn có tác dụng bất kể job dài hay ngắn.
        # Đặt trước upload vì upload là bước đắt nhất, chặn sớm ở đây là rẻ nhất
        # (đúng một query) mà vẫn chặn được trước khi tốn băng thông lên MinIO.
        if _is_cancelled(job.id):
            raise ExportCancelled()

        key = f"exports/{job.user_id}/{job.id}/{job.file_name}"
        content_type = guess_content_type(job.file_name)
        try:
            export_storage.upload_file(key, tmp_path, content_type)
        except SoftTimeLimitExceeded:
            # KHÔNG bọc thành _StorageFailed: timeout đang upload vẫn là timeout,
            # không phải lỗi lưu trữ — thông điệp cho người dùng phải khác nhau.
            raise
        except Exception as exc:
            raise _StorageFailed() from exc

        updated = ExportJobService.mark_success(job.id, key, os.path.getsize(tmp_path), written)
        if not updated:
            # Bị huỷ ngay sau khi upload xong, trước khi update này chạy: dọn object,
            # không để rác trên MinIO mà không bản ghi nào trỏ tới (job dọn rác chỉ
            # quét job SUCCESS hết hạn, không bao giờ thấy object này).
            try:
                export_storage.delete_object(key)
            except Exception:
                logger.exception("Không dọn được file của job đã huỷ %s", job.id)
    except ExportCancelled:
        logger.info("Job export %s bị huỷ giữa chừng", job.id)
    except _StorageFailed:
        logger.exception("Không upload được file export %s", job.id)
        ExportJobService.mark_failed(job.id, "Không lưu được file lên kho lưu trữ.")
    except SoftTimeLimitExceeded:
        logger.warning("Job export %s vượt thời gian cho phép", job.id)
        ExportJobService.mark_failed(
            job.id, "Quá thời gian cho phép. Vui lòng thu hẹp bộ lọc rồi xuất lại."
        )
    except UnknownExportKind:
        ExportJobService.mark_failed(job.id, "Loại xuất file không còn được hỗ trợ.")
    except Exception:
        logger.exception("Job export %s thất bại", job.id)
        ExportJobService.mark_failed(job.id, "Lỗi khi dựng file. Vui lòng thử lại.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        job.refresh_from_db()
        publish_export_event(job)

        # Đo ở `finally` (không rải theo từng nhánh except) để không bỏ sót nhánh
        # nào — kể cả race hiếm "huỷ đúng lúc giữa upload xong và mark_success"
        # (updated=0 ở trên): trạng thái THẬT sau `refresh_from_db()` mới là căn
        # cứ, không phải nhánh except nào đã chạy. `ExportJobStatus` là TextChoices
        # nên `job.status` đã sẵn là "success"/"failed"/"cancelled" — trùng khớp
        # giá trị label mong muốn, không cần bảng ánh xạ riêng dễ lệch.
        elapsed = time.monotonic() - started_monotonic
        export_jobs_total.labels(kind=job.kind, status=job.status).inc()
        export_job_duration_seconds.labels(kind=job.kind).observe(elapsed)
        if job.status == ExportJobStatus.SUCCESS:
            export_job_rows.labels(kind=job.kind).observe(job.progress_rows)


@shared_task(
    name="apps.exports.infrastructure.tasks.purge_expired_exports",
    # BẮT BUỘC override: mặc định toàn cục (CELERY_TASK_SOFT_TIME_LIMIT=25s,
    # config/settings_parts/celery.py) không đủ cho vòng lặp delete_object() lên
    # MinIO không giới hạn số lượng job hết hạn. Khuôn theo
    # apps.hr.tasks.purge_old_attendance_photos — cùng dạng "purge theo batch".
    soft_time_limit=540,
    time_limit=600,
)
def purge_expired_exports() -> None:
    """Dọn file hết hạn và cứu job kẹt RUNNING/PENDING.

    Giữ lại bản ghi (chỉ đổi status) vì nó là vết audit "ai đã xuất cái gì".
    """
    now = timezone.now()
    expired = ExportJob.objects.filter(
        status=ExportJobStatus.SUCCESS, expires_at__lt=now
    ).only("id", "file_key")
    try:
        for job in expired.iterator(chunk_size=200):
            if job.file_key:
                try:
                    export_storage.delete_object(job.file_key)
                except SoftTimeLimitExceeded:
                    # KHÔNG được rơi vào `except Exception` bên dưới: đó coi timeout
                    # như một lần xoá lỗi rồi `continue` sang job kế — vòng lặp chạy
                    # tiếp tới tận hard time_limit rồi bị SIGKILL, và khối cứu job
                    # kẹt bên dưới (RUNNING/PENDING) không bao giờ chạy tới.
                    raise
                except Exception:
                    logger.exception("Không xoá được object export %s", job.file_key)
                    continue
            ExportJob.objects.filter(id=job.id).update(
                status=ExportJobStatus.EXPIRED, file_key=""
            )
    except SoftTimeLimitExceeded:
        # Thoát hẳn vòng lặp (không `continue`) để phần cứu job kẹt bên dưới vẫn
        # chạy — dọn dở dang object hết hạn ở chu kỳ beat sau vẫn an toàn (idempotent:
        # object nào đã update EXPIRED thì filter phía trên không chọn lại), còn cơ
        # chế cứu job kẹt thì KHÔNG được phép trễ thêm một giờ (nhịp beat kế tiếp).
        logger.warning("purge_expired_exports vượt thời gian cho phép khi dọn object hết hạn")

    # Worker chết giữa chừng để lại job kẹt RUNNING mãi mãi — panel sẽ quay vô tận.
    # Ngưỡng gấp đôi soft_time_limit để không cắt nhầm job đang chạy thật.
    stuck_before = now - timezone.timedelta(seconds=settings.EXPORT_SOFT_TIME_LIMIT * 2)
    ExportJob.objects.filter(
        status=ExportJobStatus.RUNNING, started_at__lt=stuck_before
    ).update(
        status=ExportJobStatus.FAILED,
        error_message="Tiến trình xử lý bị gián đoạn. Vui lòng xuất lại.",
        finished_at=now,
    )
    # PENDING kẹt mãi mãi nếu worker-export không bao giờ nhận task (vd. queue mất
    # consumer — chính lỗi mà đợt sửa này còn vá ở deploy-prod.sh): job đó không có
    # `started_at` nên phải so bằng `created_at`. Không cứu thì job PENDING là bất
    # tử — không purge, không hết hạn, và unique constraint dedupe-active khiến
    # người dùng bấm lại chỉ nhận về đúng job zombie đó.
    ExportJob.objects.filter(
        status=ExportJobStatus.PENDING, created_at__lt=stuck_before
    ).update(
        status=ExportJobStatus.FAILED,
        error_message="Không có worker xử lý trong thời gian cho phép. Vui lòng xuất lại.",
        finished_at=now,
    )
