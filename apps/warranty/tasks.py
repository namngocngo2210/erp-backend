from celery import shared_task
from django.db import OperationalError

from apps.warranty.tracking_sync_service import WarrantyTrackingSyncService


@shared_task(
    name="apps.warranty.tasks.sync_best_tracking_statuses",
    soft_time_limit=240,
    time_limit=300,
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def sync_best_tracking_statuses() -> dict:
    """Cron 10': trace Best cập nhật trạng thái VC 3 mã của hành trình BH mở."""
    return WarrantyTrackingSyncService.sync()
