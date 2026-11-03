import logging
from datetime import timedelta

from celery import shared_task
from django.db import OperationalError, transaction
from django.conf import settings
from django.utils import timezone

from apps.common.db_guards import single_flight

logger = logging.getLogger(__name__)

# Khóa single-flight: chống 2 lần purge chạy chồng (beat trễ / worker restart).
# TTL là van an toàn > time_limit để tự nhả nếu worker chết giữa chừng.
_PURGE_LOCK_KEY = "lock:purge_old_audit_logs"
_PURGE_LOCK_TTL = 900  # > time_limit=600
_PURGE_BATCH_SIZE = 10_000


# max_retries chỉ có tác dụng khi task THẬT SỰ retry — bản cũ không bao giờ gọi
# self.retry nên DB rớt thoáng qua là mất hẳn dòng audit (audit 2026-07-14 vòng 5).
@shared_task(
    bind=True,
    max_retries=3,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
)
def create_audit_log_task(self, user_id, action_type, entity_type, entity_id, method,
                          ip_address, response_data, duration_ms, source, curl_command, metadata=None):
    """Synchronous fallback for creating one audit log row."""
    try:
        from apps.audit.models import AuditLog

        AuditLog.objects.create(
            user_id=user_id,
            action_type=action_type,
            entity_type=entity_type,
            entity_id=entity_id,
            method=method,
            ip_address=ip_address,
            response_data=response_data,
            duration_ms=duration_ms,
            source=source,
            curl_command=curl_command,
            metadata=metadata
        )
    except Exception as e:
        logger.error(f"Failed to create audit log: {e}")
        raise


# Batch từ apps.dispatch.api.write_audit (sự cố 2026-08-03/04: 1 task/request =
# ~260 task churn/phút queue audit lúc burst). OperationalError (DB rớt thoáng
# qua) retry CẢ batch — bulk_create là một INSERT nguyên tử nên retry không tạo
# dòng trùng. Lỗi dữ liệu (một dòng hỏng) KHÔNG retry: fallback ghi từng dòng
# trong savepoint riêng, bỏ đúng dòng hỏng, giữ phần còn lại.
@shared_task(
    max_retries=3,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
)
def create_audit_log_batch_task(entries):
    from apps.audit.models import AuditLog

    try:
        with transaction.atomic():
            AuditLog.objects.bulk_create([AuditLog(**entry) for entry in entries])
        return
    except OperationalError:
        raise
    except Exception as exc:
        logger.warning("audit bulk_create failed (%s), falling back to per-row", exc)

    for entry in entries:
        try:
            with transaction.atomic():
                AuditLog.objects.create(**entry)
        except Exception as exc:  # noqa: BLE001
            logger.error("audit per-row fallback failed, dropping row: %s", exc)


@shared_task(name="apps.audit.tasks.purge_old_audit_logs",
             soft_time_limit=540, time_limit=600)
def purge_old_audit_logs(batch_size: int = _PURGE_BATCH_SIZE) -> int:
    """Xoá audit_logs cũ hơn AUDIT_LOG_RETENTION_DAYS (env, default 90) theo batch.

    Batch 10k để tránh giữ lock dài / bloat WAL một lần. Trả về số row đã xoá,
    -1 nếu bỏ qua vì một lần chạy khác đang giữ khóa.
    """
    retention_days = int(getattr(settings, "AUDIT_LOG_RETENTION_DAYS", 90))
    if retention_days <= 0:
        logger.info("audit purge disabled (AUDIT_LOG_RETENTION_DAYS<=0)")
        return 0

    from apps.audit.models import AuditLog

    cutoff = timezone.now() - timedelta(days=retention_days)
    deleted_total = 0
    with single_flight(_PURGE_LOCK_KEY, _PURGE_LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("purge_old_audit_logs already running — skip this cycle")
            return -1
        while True:
            ids = list(
                AuditLog.objects.filter(timestamp__lt=cutoff)
                .order_by()
                .values_list("id", flat=True)[:batch_size]
            )
            if not ids:
                break
            deleted, _ = AuditLog.objects.filter(id__in=ids).delete()
            deleted_total += deleted
    logger.info("purged %s audit logs older than %s days", deleted_total, retention_days)
    return deleted_total
