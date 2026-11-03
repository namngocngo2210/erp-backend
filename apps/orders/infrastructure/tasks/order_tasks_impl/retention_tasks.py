"""Retention cho bảng log tăng vô hạn của orders (audit hardening 2026-07 mục 3.4).

- landing_ingest_traces: debug trace ingest landing — giá trị điều tra giảm nhanh
  theo thời gian; default giữ 120 ngày (env LANDING_INGEST_TRACE_RETENTION_DAYS).
- order_journey_events: lịch sử hành trình đơn — default GIỮ VĨNH VIỄN
  (JOURNEY_EVENT_RETENTION_DAYS=0); chỉ purge khi vận hành chủ động bật qua env.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.db import OperationalError
from django.conf import settings
from django.utils import timezone

from apps.common.db_guards import single_flight

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10_000
_TRACE_LOCK_KEY = "lock:purge_old_ingest_traces"
_JOURNEY_LOCK_KEY = "lock:purge_old_journey_events"
_LOCK_TTL = 900  # > time_limit=600

__all__ = ["purge_old_ingest_traces", "purge_old_journey_events"]


def _purge_by_created_at(model, cutoff, batch_size: int) -> int:
    deleted_total = 0
    while True:
        ids = list(
            model.objects.filter(created_at__lt=cutoff)
            .order_by()
            .values_list("id", flat=True)[:batch_size]
        )
        if not ids:
            break
        deleted, _ = model.objects.filter(id__in=ids).delete()
        deleted_total += deleted
    return deleted_total


@shared_task(
    name="apps.orders.tasks.purge_old_ingest_traces",
    soft_time_limit=540,
    time_limit=600,
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def purge_old_ingest_traces(batch_size: int = _BATCH_SIZE) -> int:
    """Xoá landing_ingest_traces cũ hơn LANDING_INGEST_TRACE_RETENTION_DAYS (default 120)."""
    from apps.orders.models import LandingIngestTrace

    retention_days = int(getattr(settings, "LANDING_INGEST_TRACE_RETENTION_DAYS", 120))
    if retention_days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=retention_days)
    with single_flight(_TRACE_LOCK_KEY, _LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("purge_old_ingest_traces already running — skip this cycle")
            return -1
        deleted = _purge_by_created_at(LandingIngestTrace, cutoff, batch_size)
    logger.info("purged %s landing ingest traces older than %s days", deleted, retention_days)
    return deleted


@shared_task(
    name="apps.orders.tasks.purge_old_journey_events",
    soft_time_limit=540,
    time_limit=600,
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def purge_old_journey_events(batch_size: int = _BATCH_SIZE) -> int:
    """Xoá order_journey_events cũ hơn JOURNEY_EVENT_RETENTION_DAYS.

    Default 0 = KHÔNG xoá (an toàn) — bật bằng env khi vận hành quyết định.
    """
    from apps.orders.models import OrderJourneyEvent

    retention_days = int(getattr(settings, "JOURNEY_EVENT_RETENTION_DAYS", 0))
    if retention_days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=retention_days)
    with single_flight(_JOURNEY_LOCK_KEY, _LOCK_TTL) as acquired:
        if not acquired:
            logger.warning("purge_old_journey_events already running — skip this cycle")
            return -1
        deleted = _purge_by_created_at(OrderJourneyEvent, cutoff, batch_size)
    logger.info("purged %s journey events older than %s days", deleted, retention_days)
    return deleted
