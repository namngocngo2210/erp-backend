import logging

from celery import shared_task
from django.db import OperationalError

from apps.common.db_guards import single_flight

from .infrastructure.campaign_sync import sync_campaign_spend_for_config
from .models import GoogleAdsConfig
from .services import (
    GoogleAdsApiError,
    sync_accounts_for_config,
    sync_daily_spend_for_config,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="apps.google_ads.tasks.sync_all_daily_spend_google",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=360,
)
def sync_all_daily_spend_google(self) -> dict:
    """Đồng bộ chi phí grain account cho mọi config.

    Khoá single-flight (mirror `apps.facebook_ads.tasks.sync_all_daily_spend`): beat
    chạy mỗi 300s đúng bằng `soft_time_limit`, nên một lượt chạy lâu là tick sau chen
    ngay vào. Từ lúc quét toàn cây MCC, mỗi tài khoản MỚI phát hiện rơi vào nhánh
    backfill 365 ngày ở lượt kế tiếp — lượt đó dễ vượt 300s, bị hard-kill, rồi tick sau
    lại nã đúng những truy vấn vừa dở dang. TTL 400s > time_limit 360s để khoá không
    tự hết hạn khi worker còn đang bị kill.
    """
    with single_flight("lock:gads_sync_all_daily_spend", 400) as acquired:
        if not acquired:
            logger.info("sync_all_daily_spend_google already running — skip this tick")
            return {"skipped": "already_running"}
        return _sync_all_daily_spend_google()


def _sync_all_daily_spend_google() -> dict:
    configs = GoogleAdsConfig.objects.filter(deleted_at__isnull=True)
    summary: dict = {"configs": 0, "rows_upserted": 0, "failed_accounts": 0, "errors": []}
    for config in configs:
        try:
            result = sync_daily_spend_for_config(config)
            summary["configs"] += 1
            summary["rows_upserted"] += result.get("rows_upserted", 0)
            summary["failed_accounts"] += result.get("failed_accounts", 0)
        except GoogleAdsApiError as exc:
            logger.warning("sync_daily_spend failed for google config %s: %s", config.id, exc)
            summary["errors"].append({"config_id": config.id, "error": str(exc)})
        except Exception:
            logger.exception("Unexpected error syncing google daily spend for config %s", config.id)
            summary["errors"].append({"config_id": config.id, "error": "unexpected"})
    return summary


@shared_task(
    bind=True,
    name="apps.google_ads.tasks.sync_all_accounts_google",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=360,
)
def sync_all_accounts_google(self) -> dict:
    """Tự động phát hiện/cập nhật danh sách tài khoản con dưới mọi MCC.

    Bổ sung cho sync_all_daily_spend_google (task đó chỉ kéo chi phí cho các tài
    khoản đã có sẵn). Account list đổi hiếm nên chạy thưa (xem CELERY_BEAT_SCHEDULE).
    """
    configs = GoogleAdsConfig.objects.filter(deleted_at__isnull=True)
    summary: dict = {"configs": 0, "created": 0, "updated": 0, "removed": 0, "errors": []}
    for config in configs:
        try:
            result = sync_accounts_for_config(config)
            summary["configs"] += 1
            summary["created"] += result.get("created", 0)
            summary["updated"] += result.get("updated", 0)
            summary["removed"] += result.get("removed", 0)
        except GoogleAdsApiError as exc:
            logger.warning("sync_accounts failed for google config %s: %s", config.id, exc)
            summary["errors"].append({"config_id": config.id, "error": str(exc)})
        except Exception:
            logger.exception("Unexpected error syncing google accounts for config %s", config.id)
            summary["errors"].append({"config_id": config.id, "error": "unexpected"})
    return summary


@shared_task(
    bind=True,
    name="apps.google_ads.tasks.sync_all_campaign_spend_google",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    # KHÔNG dùng 300s như grain account: mỗi account trả (số chiến dịch × số ngày)
    # dòng thay vì (số ngày) dòng, và lần chạy đầu là backfill 365 ngày cho cả cây MCC.
    soft_time_limit=900,
    time_limit=960,
)
def sync_all_campaign_spend_google(self) -> dict:
    """Cửa sổ trailing 7 ngày ở grain chiến dịch. Chạy độc lập với grain account."""
    # TTL khoá (1000s) PHẢI > time_limit (960s): ngắn hơn thì worker đang bị hard-kill
    # mà khoá đã hết hạn → tick sau chen vào, hai lượt cùng upsert một tập dòng.
    with single_flight("lock:gads_sync_all_campaign_spend", 1000) as acquired:
        if not acquired:
            logger.info("sync_all_campaign_spend_google already running — skip this tick")
            return {"skipped": "already_running"}
        return _sync_all_campaign_spend_google()


def _sync_all_campaign_spend_google() -> dict:
    configs = GoogleAdsConfig.objects.filter(deleted_at__isnull=True)
    summary: dict = {"configs": 0, "rows_upserted": 0, "failed_accounts": 0, "errors": []}
    for config in configs:
        try:
            result = sync_campaign_spend_for_config(config)
            summary["configs"] += 1
            summary["rows_upserted"] += result.get("rows_upserted", 0)
            summary["failed_accounts"] += result.get("failed_accounts", 0)
        except GoogleAdsApiError as exc:
            logger.warning("campaign sync failed for google config %s: %s", config.id, exc)
            summary["errors"].append({"config_id": config.id, "error": str(exc)})
        except Exception:
            logger.exception(
                "Unexpected error syncing google campaign spend for config %s", config.id,
            )
            summary["errors"].append({"config_id": config.id, "error": "unexpected"})
    return summary


@shared_task(
    name="apps.google_ads.tasks.assign_campaign_products_google",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=120,
    time_limit=180,
)
def assign_campaign_products_google() -> dict:
    """Bản Google của ``apps.facebook_ads.tasks.assign_campaign_products``.

    Phủ tự động ở đây thấp hơn hẳn (đo T8/2026: ~41% chi phí) vì chiến dịch
    Shopping/Pmax đặt tên theo nhóm hàng — phần còn lại chờ người gán tay.
    """
    from apps.catalog.sku_text_match import assign_products_to_campaigns
    from apps.orders.reporting.product_sales_rank import product_revenue_rank

    from .models import GoogleAdsCampaign

    result = assign_products_to_campaigns(
        GoogleAdsCampaign.objects.all(), rank=product_revenue_rank(),
    )
    if result["assigned"]:
        logger.info(
            "gán sản phẩm cho chiến dịch Google: %s/%s", result["assigned"], result["scanned"],
        )
    return result
