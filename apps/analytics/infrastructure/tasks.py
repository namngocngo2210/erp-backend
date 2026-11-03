import logging

from celery import shared_task
from django.db import OperationalError

from apps.analytics.application.mart_refresh_service import refresh_all_marts
from apps.common.db_guards import single_flight

logger = logging.getLogger(__name__)

_MART_LOCK_KEY = "lock:refresh_dwh_marts"
_MART_LOCK_TTL = 2400  # > time_limit=1800


@shared_task(
    name="apps.analytics.tasks.refresh_dwh_marts",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=1500,
    time_limit=1800,
)
def refresh_dwh_marts() -> dict:
    """Refresh toàn bộ mart `dwh`.

    Lỗi PHẢI thoát ra ngoài: nuốt lỗi khiến Celery báo `succeeded` trong khi
    bước con đã chết, celery-exporter không đếm được và alert không nổ
    (sự cố 2026-07-22).
    """
    with single_flight(_MART_LOCK_KEY, _MART_LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("refresh_dwh_marts already running — skip this cycle")
            return {"skipped": True, "timings": {}}
        timings = refresh_all_marts()
    return {"skipped": False, "timings": timings}
