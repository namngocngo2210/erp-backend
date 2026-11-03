import logging

from celery import shared_task
from django.conf import settings
from django.db import OperationalError
from django.core.cache import cache

from apps.orders.application.services.lead_close_service import LeadCloseService

logger = logging.getLogger(__name__)

__all__ = ["close_converted_leads"]


@shared_task(
    bind=True,
    name="apps.orders.tasks.close_converted_leads",
    soft_time_limit=240,
    time_limit=300,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def close_converted_leads(self) -> dict:
    """Cron hằng ngày: đóng lead đã chuyển đổi thành hóa đơn KiotViet.

    Single-flight lock mirror sweep_lead_assignment — chống chạy chồng.

    No-op khi LEAD_AUTO_CLOSE_ENABLED tắt (mặc định từ 2026-09-08, xem
    lead_close_service) — job vẫn nằm trong beat để bật lại bằng env."""
    if not getattr(settings, "LEAD_AUTO_CLOSE_ENABLED", False):
        logger.info("close_converted_leads skip: LEAD_AUTO_CLOSE_ENABLED off")
        return {"skipped": "disabled"}
    lock_key = "orders:close_converted_leads:lock"
    if not cache.add(lock_key, 1, 310):  # ttl > time_limit
        logger.info("close_converted_leads skip: lock held")
        return {"skipped": "lock_held"}
    try:
        summary = LeadCloseService.close_converted()
    finally:
        cache.delete(lock_key)
    logger.info("close_converted_leads done: %s", summary)
    return summary
