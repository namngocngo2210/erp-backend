"""Vòng đời của một ExportJob. Không biết Celery — task gọi vào đây, không ngược lại."""

from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.exports.application.registry import get_definition
from apps.exports.models import ACTIVE_STATUSES, ExportJob, ExportJobStatus


class TooManyActiveJobs(Exception):
    """Người dùng đã có quá nhiều job đang chạy."""


class ExportJobService:
    @staticmethod
    def params_hash(kind: str, params: dict) -> str:
        # sort_keys: hai dict cùng nội dung khác thứ tự key phải ra CÙNG hash, nếu
        # không thì dedupe vô dụng ngay khi FE đổi thứ tự field.
        raw = json.dumps(
            {"kind": kind, "params": params}, sort_keys=True, ensure_ascii=False, default=str
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def create(user, kind: str, raw_params: dict) -> tuple[ExportJob, bool]:
        """Validate + dedupe + tạo job. Trả (job, created).

        Toàn bộ phần đắt (đếm dòng) đã nằm ở `estimate` của definition và chạy TRƯỚC
        khi ghi bản ghi — vượt trần thì không có job nào được tạo.
        """
        definition = get_definition(kind)
        params = definition.validate(user, raw_params)
        definition.check_permission(user, params)
        total_rows = definition.estimate(params)

        digest = ExportJobService.params_hash(kind, params)
        existing = (
            ExportJob.objects.filter(
                user=user, kind=kind, params_hash=digest, status__in=ACTIVE_STATUSES
            )
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            return existing, False

        # Đếm rồi so trần là đọc-rồi-ghi, có thể cho lọt quá trần 1 job nếu hai request
        # (params khác nhau) cùng đọc count() lúc sát ngưỡng. Chấp nhận có chủ đích: trần
        # là van an toàn chống lạm dụng, không phải hạn ngạch cần chính xác tuyệt đối —
        # không đáng đánh đổi thêm một khoá DB chỉ để bịt kẽ hở vô hại này.
        active_count = ExportJob.objects.filter(user=user, status__in=ACTIVE_STATUSES).count()
        if active_count >= settings.EXPORT_MAX_ACTIVE_JOBS_PER_USER:
            raise TooManyActiveJobs()

        try:
            # Bọc riêng bằng savepoint: nếu IntegrityError xảy ra, chỉ savepoint này bị
            # rollback, transaction bao ngoài (nếu có, ví dụ transaction của request) vẫn
            # dùng được bình thường sau đó.
            with transaction.atomic():
                job = ExportJob.objects.create(
                    user=user,
                    kind=kind,
                    params=params,
                    params_hash=digest,
                    total_rows=total_rows,
                    file_name=definition.file_name(params),
                    expires_at=timezone.now()
                    + timezone.timedelta(hours=settings.EXPORT_FILE_TTL_HOURS),
                )
        except IntegrityError:
            # Request song song đã tạo job trùng đúng giữa lúc ta kiểm tra và ghi —
            # UniqueConstraint `export_dedupe_active_uniq` ở DB chặn thay cho ta. Trả về
            # job của bên kia, đúng ngữ nghĩa dedupe (không phải lỗi cho người dùng thấy).
            existing = (
                ExportJob.objects.filter(
                    user=user, kind=kind, params_hash=digest, status__in=ACTIVE_STATUSES
                )
                .order_by("-created_at")
                .first()
            )
            if existing is None:
                raise
            return existing, False
        return job, True

    @staticmethod
    def enqueue(job: ExportJob) -> None:
        """Đẩy job xuống Celery SAU khi transaction commit.

        ``on_commit`` là bắt buộc: worker có thể nhận task trước khi transaction của
        request commit xong và sẽ không tìm thấy bản ghi job.
        """
        from apps.exports.infrastructure.tasks import run_export

        def _send():
            result = run_export.delay(str(job.id))
            ExportJob.objects.filter(id=job.id).update(celery_task_id=result.id)

        transaction.on_commit(_send)

    @staticmethod
    def cancel(job: ExportJob) -> None:
        """Đánh dấu huỷ. Job RUNNING dừng ở mốc `progress` kế tiếp (xem writers)."""
        from celery import current_app

        if job.celery_task_id:
            current_app.control.revoke(job.celery_task_id)
        ExportJob.objects.filter(id=job.id, status__in=ACTIVE_STATUSES).update(
            status=ExportJobStatus.CANCELLED, finished_at=timezone.now()
        )

    @staticmethod
    def mark_running(job_id) -> None:
        ExportJob.objects.filter(id=job_id).update(
            status=ExportJobStatus.RUNNING, started_at=timezone.now()
        )

    @staticmethod
    def mark_success(job_id, file_key: str, file_size: int, rows: int) -> int:
        """Chỉ đánh SUCCESS khi job VẪN đang chạy.

        `.update()` không điều kiện sẽ ghi đè cả trạng thái CANCELLED mà người dùng
        vừa đặt — huỷ xong file vẫn hiện ra để tải. Trả về số dòng bị ảnh hưởng để
        worker biết mình có bị huỷ giữa chừng hay không (0 nghĩa là đã bị huỷ đúng
        lúc giữa lúc upload xong và update này chạy).
        """
        return ExportJob.objects.filter(id=job_id, status__in=ACTIVE_STATUSES).update(
            status=ExportJobStatus.SUCCESS,
            file_key=file_key,
            file_size=file_size,
            progress_rows=rows,
            finished_at=timezone.now(),
        )

    @staticmethod
    def mark_failed(job_id, message: str) -> None:
        ExportJob.objects.filter(id=job_id).update(
            status=ExportJobStatus.FAILED,
            error_message=message[:2000],
            finished_at=timezone.now(),
        )
