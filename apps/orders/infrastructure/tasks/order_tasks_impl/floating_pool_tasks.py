import logging

from celery import shared_task
from django.db import OperationalError
from django.core.cache import cache

from apps.orders.application.services.floating_pool_service import FloatingPoolService

logger = logging.getLogger(__name__)

__all__ = ["sweep_floating_pool"]


@shared_task(
    bind=True,
    name="apps.orders.tasks.sweep_floating_pool",
    soft_time_limit=240,
    time_limit=300,
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def sweep_floating_pool(self) -> dict:
    """Cron 15': quét kho số thả nổi (docs/sale-floating-pool.md §8).

    Single-flight lock (cache.add) mirror apps.inventory.tasks.match_pending_shipment_scans
    — chống 2 lần sweep chạy chồng khi lần trước còn dang dở."""
    lock_key = "orders:floating_pool_sweep:lock"
    if not cache.add(lock_key, 1, 310):  # ttl > time_limit
        logger.info("sweep_floating_pool skip: lock held")
        return {"skipped": "lock_held"}
    try:
        summary = FloatingPoolService.sweep()
    finally:
        cache.delete(lock_key)
    logger.info("sweep_floating_pool done: %s", summary)
    return summary
