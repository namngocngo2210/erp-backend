import logging

from celery import shared_task
from django.db import OperationalError
from django.core.cache import cache

from apps.contact_flow.application.services.entry_service import OperationEntryService

logger = logging.getLogger(__name__)

__all__ = ["sweep_operation_entry"]


@shared_task(
    bind=True,
    name="apps.contact_flow.tasks.sweep_operation_entry",
    soft_time_limit=240,
    time_limit=300,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def sweep_operation_entry(self) -> dict:
    """Cron 5': tạo state tác nghiệp cho khách có đơn hợp lệ (24h) chưa vào flow —
    vớt các đường ingest bỏ qua hook at-ingest (import, lỗi tạm thời,...).

    Single-flight lock (cache.add) mirror sweep_lead_assignment — chống 2 lần
    sweep chạy chồng khi lần trước còn dang dở."""
    lock_key = "contact_flow:entry_sweep:lock"
    if not cache.add(lock_key, 1, 310):  # ttl > time_limit
        logger.info("sweep_operation_entry skip: lock held")
        return {"skipped": "lock_held"}
    try:
        summary = OperationEntryService.sweep()
    finally:
        cache.delete(lock_key)
    logger.info("sweep_operation_entry done: %s", summary)
    return summary
