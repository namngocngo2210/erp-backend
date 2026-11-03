"""Celery tasks của module HR."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import OperationalError
from django.conf import settings
from django.utils import timezone

from apps.common.db_guards import single_flight
from apps.hr.application.services import attendance_reminder_service

logger = logging.getLogger(__name__)

# Khóa single-flight: chống 2 lần purge chạy chồng (beat trễ / worker restart),
# theo đúng pattern purge audit_logs (apps.audit.tasks.purge_old_audit_logs).
_PHOTO_PURGE_LOCK_KEY = "lock:purge_old_attendance_photos"
_PHOTO_PURGE_LOCK_TTL = 900  # > time_limit=600
_PHOTO_PURGE_BATCH_SIZE = 500  # xoá MinIO qua HTTP mỗi dòng → batch nhỏ hơn purge DB thuần


@shared_task(
    bind=True,
    ignore_result=True,
    # ~150 DM tuần tự, mỗi call timeout 15s → trần rộng để không bị chém giữa chừng.
    soft_time_limit=540,
    time_limit=600,
    name="apps.hr.tasks.send_attendance_reminder",
    # KHÔNG retry tự động: task gửi thông báo/đẩy dữ liệu ra ngoài — chạy lại là
    # gửi trùng (audit 2026-09-23 T1). Lỗi tạm thời để beat/tick sau xử lý.
    max_retries=0,
)
def send_attendance_reminder(self, kind: str):
    """Nhắc chấm công qua Lark DM (beat: 07:50 "arrive", 17:30 "leave")."""
    summary = attendance_reminder_service.send_attendance_reminder(kind)
    logger.info("send_attendance_reminder(%s): %s", kind, summary)


@shared_task(
    name="apps.hr.tasks.purge_old_attendance_photos",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=540, time_limit=600,
)
def purge_old_attendance_photos(batch_size: int = _PHOTO_PURGE_BATCH_SIZE) -> dict:
    """Xoá ảnh chấm công cũ hơn ATTENDANCE_PHOTO_RETENTION_DAYS (env, default 180).

    Chỉ xoá OBJECT MinIO + clear ``photo_key`` trên AttendanceEvent — bản ghi
    chấm công (giờ vào/ra) LUÔN được giữ, chỉ ảnh xác thực hết giá trị theo thời
    gian mới bị dọn. Trả ``{"deleted": -1, "failed": 0}`` nếu bỏ qua vì một lần
    chạy khác đang giữ khóa (xem ``_PHOTO_PURGE_LOCK_KEY``).

    Dòng xoá MinIO thất bại (network/permission — không phải "key không tồn tại",
    ``delete_object`` là idempotent) thì GIỮ NGUYÊN ``photo_key`` để lần chạy sau
    thử lại, tránh vừa mất tham chiếu vừa để object mồ côi vĩnh viễn trên MinIO.
    """
    from apps.hr.infrastructure.photo_storage import delete_photo
    from apps.hr.models import AttendanceEvent

    retention_days = int(getattr(settings, "ATTENDANCE_PHOTO_RETENTION_DAYS", 180))
    if retention_days <= 0:
        logger.info("attendance photo purge disabled (ATTENDANCE_PHOTO_RETENTION_DAYS<=0)")
        return {"deleted": 0, "failed": 0}

    cutoff = timezone.now() - timedelta(days=retention_days)
    deleted_total = 0
    failed_total = 0
    with single_flight(_PHOTO_PURGE_LOCK_KEY, _PHOTO_PURGE_LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("purge_old_attendance_photos already running — skip this cycle")
            return {"deleted": -1, "failed": 0}
        # Keyset pagination theo id (không requery từ đầu): dòng xoá MinIO thất bại
        # GIỮ NGUYÊN photo_key nên vẫn khớp filter ở vòng sau — nếu offset/limit lại
        # từ đầu mỗi vòng, những dòng lỗi liên tục sẽ khiến vòng lặp KHÔNG BAO GIỜ
        # kết thúc trong cùng 1 lần chạy task.
        last_id = 0
        while True:
            rows = list(
                AttendanceEvent.objects.filter(occurred_at__lt=cutoff, id__gt=last_id)
                .exclude(photo_key="")
                .order_by("id")
                .values_list("id", "photo_key")[:batch_size]
            )
            if not rows:
                break
            last_id = rows[-1][0]
            cleared_ids = []
            for event_id, photo_key in rows:
                if delete_photo(photo_key):
                    cleared_ids.append(event_id)
                    deleted_total += 1
                else:
                    failed_total += 1
            if cleared_ids:
                AttendanceEvent.objects.filter(id__in=cleared_ids).update(photo_key="")
    logger.info(
        "purged %s attendance photos older than %s days (failed=%s)",
        deleted_total, retention_days, failed_total,
    )
    return {"deleted": deleted_total, "failed": failed_total}
