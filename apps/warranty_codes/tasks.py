"""Task nền của app warranty_codes: sinh mã cho lô lớn + retention scan log.

Retention cho `warranty_code_scan_logs` (spec 2026-08-06 warranty portal §6.2).

Bảng log mọi lần khách nhập/quét mã trên portal public — tăng vô hạn theo lưu
lượng portal, kể cả mã sai (chống tem giả). Copy pattern
`apps.orders.tasks.purge_old_ingest_traces` (single-flight lock + xoá theo lô).
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.common.db_guards import single_flight
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10_000
_PURGE_LOCK_KEY = "lock:purge_warranty_code_scan_logs"
_PURGE_LOCK_TTL = 900  # > time_limit=600

__all__ = ["generate_batch_codes", "purge_old_scan_logs"]


# Trần 1h/lượt: 1 triệu mã ≈ 5 phút (~3.500 mã/s). Quá trần hay lỗi DB thì
# autoretry — task resumable theo `generated_seq` nên chạy lại chỉ sinh phần còn
# thiếu. Phải khai time_limit tường minh vì mặc định toàn cục là 30s.
_GENERATE_SOFT_TIME_LIMIT = 3600


@shared_task(
    name="apps.warranty_codes.tasks.generate_batch_codes",
    soft_time_limit=_GENERATE_SOFT_TIME_LIMIT,
    time_limit=_GENERATE_SOFT_TIME_LIMIT + 60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=10,
)
def generate_batch_codes(batch_id: int) -> int:
    """Sinh mã nền cho lô lớn (quantity > WARRANTY_CODE_SYNC_GENERATE_MAX)."""
    from apps.warranty_codes.application.services.batch_service import WarrantyCodeBatchService

    inserted = WarrantyCodeBatchService.generate_codes(batch_id=batch_id)
    logger.info("generated %s warranty codes for batch %s", inserted, batch_id)
    return inserted


@shared_task(
    name="apps.warranty_codes.tasks.purge_old_scan_logs",
    soft_time_limit=540,
    time_limit=600,
)
def purge_old_scan_logs(batch_size: int = _BATCH_SIZE) -> int:
    """Xoá WarrantyCodeScanLog cũ hơn WARRANTY_PORTAL_SCAN_LOG_RETENTION_DAYS (default 180).

    retention <= 0 = tắt (an toàn), khớp quy ước các task retention khác trong repo.
    """
    from apps.warranty_codes.models import WarrantyCodeScanLog

    retention_days = int(sysconfig.get("WARRANTY_PORTAL_SCAN_LOG_RETENTION_DAYS"))
    if retention_days <= 0:
        logger.info("warranty code scan log purge disabled (WARRANTY_PORTAL_SCAN_LOG_RETENTION_DAYS<=0)")
        return 0

    cutoff = timezone.now() - timedelta(days=retention_days)
    deleted_total = 0
    with single_flight(_PURGE_LOCK_KEY, _PURGE_LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("purge_old_scan_logs already running — skip this cycle")
            return -1
        while True:
            ids = list(
                WarrantyCodeScanLog.objects.filter(created_at__lt=cutoff)
                .order_by()
                .values_list("id", flat=True)[:batch_size]
            )
            if not ids:
                break
            deleted, _ = WarrantyCodeScanLog.objects.filter(id__in=ids).delete()
            deleted_total += deleted
    logger.info(
        "purged %s warranty code scan logs older than %s days", deleted_total, retention_days
    )
    return deleted_total
