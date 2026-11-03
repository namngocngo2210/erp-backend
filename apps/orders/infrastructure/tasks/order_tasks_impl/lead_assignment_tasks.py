import logging

from celery import shared_task
from django.db import OperationalError
from django.core.cache import cache

from apps.orders.application.services.lead_assignment_service import LeadAssignmentService

logger = logging.getLogger(__name__)

__all__ = ["sweep_lead_assignment"]


@shared_task(
    bind=True,
    name="apps.orders.tasks.sweep_lead_assignment",
    soft_time_limit=240,
    time_limit=300,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def sweep_lead_assignment(self) -> dict:
    """Cron 5': chia lại đơn thuộc sheet còn trống staff (vớt skip im lặng).

    Single-flight lock (cache.add) mirror sweep_floating_pool — chống 2 lần
    sweep chạy chồng khi lần trước còn dang dở."""
    lock_key = "orders:lead_assignment_sweep:lock"
    if not cache.add(lock_key, 1, 310):  # ttl > time_limit
        logger.info("sweep_lead_assignment skip: lock held")
        return {"skipped": "lock_held"}
    try:
        summary = LeadAssignmentService.sweep_unassigned()
    finally:
        cache.delete(lock_key)
    logger.info("sweep_lead_assignment done: %s", summary)
    return summary
