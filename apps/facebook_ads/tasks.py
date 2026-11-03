import logging

from celery import shared_task
from django.db import OperationalError
from celery.exceptions import SoftTimeLimitExceeded
from django.core.cache import cache
from django.utils import timezone

from apps.common.db_guards import single_flight
from apps.common.ops_alerts import send_ops_alert

from .infrastructure.ad_catalog_sync import sync_ad_catalog_for_app
from .infrastructure.ad_spend_sync import sync_ad_spend_for_app
from .infrastructure.token_service import (
    mark_token_invalid,
    mark_token_ok,
    refresh_token_metadata,
    try_extend_token,
)
from .models import FacebookApp
from .services import FacebookGraphError, FacebookTokenError, sync_daily_spend_for_app

logger = logging.getLogger(__name__)

# Ngắt mạch: app có token chết thì mỗi tick chỉ nhắc lại 1 lần/giờ thay vì mỗi lượt.
# Trước fix, cron 60s sinh 1.440 dòng warning/ngày/app và vẫn nã request vào Graph API.
_BREAKER_LOG_TTL_SECONDS = 3600
_TOKEN_ALERT_TTL_SECONDS = 6 * 3600


def _breaker_log_key(scope: str, app_pk: int) -> str:
    """Khoá throttle log ngắt mạch, TÁCH RIÊNG theo từng task.

    Ba task (daily spend / ad catalog / ad spend) cùng đọc trạng thái token của một
    app. Dùng chung một khoá thì task nào chạy trước trong giờ đó chiếm mất
    `cache.add`, hai task còn lại im lặng hoàn toàn — người trực log chỉ thấy 1 trong
    3 luồng đã dừng đồng bộ, đúng thứ dòng log này sinh ra để tránh.
    """
    return f"fb:breaker_logged:{scope}:{app_pk}"


def _alert_token_invalid(app: FacebookApp, reason: str) -> None:
    since = app.token_invalid_since
    downtime = f" (hỏng từ {timezone.localtime(since):%d/%m %H:%M})" if since else ""
    send_ops_alert(
        key=f"fb:token_invalid:{app.pk}",
        title=f'[Facebook Ads] Token app "{app.name}" (id {app.pk}) đã hỏng{downtime}',
        lines=[
            f"Lý do: {reason}",
            "Hệ quả: chi tiêu quảng cáo của app này KHÔNG được cập nhật; báo cáo ads sang Lark bị chặn.",
            "Xử lý: cấp lại System User token (Business Manager → System Users, bỏ tick expiration) "
            "rồi cập nhật trong màn hình cấu hình Facebook Ads.",
        ],
        dedupe_seconds=_TOKEN_ALERT_TTL_SECONDS,
    )


@shared_task(
    bind=True,
    name="apps.facebook_ads.tasks.sync_all_daily_spend",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=360,
)
def sync_all_daily_spend(self, date_preset: str | None = None) -> dict:
    # time_limit 360s > chu kỳ beat: FB chậm là nhiều instance chồng nhau cùng upsert
    # DailySpend (race update_or_create → IntegrityError lẻ) và chiếm slot worker liên
    # tục. Tick sau skip khi tick trước còn chạy (audit 2026-07-14 vòng 5).
    with single_flight("lock:fb_sync_all_daily_spend", 400) as acquired:
        if not acquired:
            logger.info("sync_all_daily_spend already running — skip this tick")
            return {"skipped": "already_running"}
        return _sync_all_daily_spend(date_preset=date_preset)


def _sync_all_daily_spend(*, date_preset: str | None) -> dict:
    apps = FacebookApp.objects.filter(deleted_at__isnull=True)
    summary: dict = {
        "apps": 0,
        "rows_upserted": 0,
        "failed_accounts": 0,
        "skipped_apps": 0,
        "errors": [],
    }
    for app in apps:
        if app.token_status == FacebookApp.TOKEN_INVALID:
            # Ngắt mạch: token chết thì thử lại bao nhiêu lần cũng vô nghĩa. Chờ người
            # dán token mới — signal pre_save reset trạng thái và mở lại mạch.
            summary["skipped_apps"] += 1
            if cache.add(
                _breaker_log_key("daily_spend", app.pk), "1", _BREAKER_LOG_TTL_SECONDS,
            ):
                logger.warning(
                    "sync_daily_spend: bỏ qua app %s — token đang hỏng (%s)",
                    app.pk, app.token_error or "không rõ lý do",
                )
            _alert_token_invalid(app, app.token_error or "token không hợp lệ")
            continue
        try:
            result = sync_daily_spend_for_app(app, date_preset=date_preset)
            summary["apps"] += 1
            summary["rows_upserted"] += result.get("rows_upserted", 0)
            failed = result.get("failed_accounts", 0)
            if failed:
                # Account lỗi (vd account bị vô hiệu hoá) không raise exception — batch
                # API vẫn 200 ở tầng ngoài, chỉ riêng sub-request của account đó lỗi.
                # Log ở đây để lộ ra Sentry/log tổng hợp mỗi lần cron chạy.
                logger.warning(
                    "sync_daily_spend: app %s has %d failed account(s)", app.id, failed,
                )
                summary["failed_accounts"] += failed
            mark_token_ok(app)
        except FacebookTokenError as exc:
            logger.warning("sync_daily_spend: token app %s bị từ chối: %s", app.id, exc)
            mark_token_invalid(app, str(exc))
            _alert_token_invalid(app, str(exc))
            summary["errors"].append({"app_id": app.id, "error": str(exc), "kind": "token"})
        except FacebookGraphError as exc:
            # Lỗi tạm thời (rate limit, 5xx, payload quá lớn): KHÔNG khoá app lại.
            logger.warning("sync_daily_spend failed for app %s: %s", app.id, exc)
            summary["errors"].append({"app_id": app.id, "error": str(exc)})
        except Exception:
            logger.exception("Unexpected error syncing daily spend for app %s", app.id)
            summary["errors"].append({"app_id": app.id, "error": "unexpected"})
    return summary


@shared_task(
    bind=True,
    name="apps.facebook_ads.tasks.check_facebook_tokens",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=180,
    time_limit=240,
)
def check_facebook_tokens(self) -> dict:
    """Cron hằng ngày: soi hạn token, tự gia hạn nếu được, gọi người nếu không."""
    return _check_facebook_tokens()


def _check_facebook_tokens() -> dict:
    summary = {"checked": 0, "ok": 0, "expiring": 0, "invalid": 0, "extended": 0}
    for app in FacebookApp.objects.filter(deleted_at__isnull=True):
        summary["checked"] += 1
        refresh_token_metadata(app)

        if app.token_status == FacebookApp.TOKEN_INVALID:
            summary["invalid"] += 1
            _alert_token_invalid(app, app.token_error or "token không hợp lệ")
            continue

        if app.token_status == FacebookApp.TOKEN_EXPIRING:
            # Token còn sống thì đổi được sang long-lived (60 ngày) — mua thêm thời
            # gian cho tới khi chuyển hẳn sang System User token.
            if try_extend_token(app):
                summary["extended"] += 1
                continue
            summary["expiring"] += 1
            expires = app.token_expires_at
            when = f" (hết hạn {timezone.localtime(expires):%d/%m %H:%M})" if expires else ""
            send_ops_alert(
                key=f"fb:token_expiring:{app.pk}",
                title=f'[Facebook Ads] Token app "{app.name}" (id {app.pk}) sắp hết hạn{when}',
                lines=[
                    "Tự động gia hạn KHÔNG thành công.",
                    "Xử lý: cấp System User token không hết hạn (Business Manager → System Users, "
                    "bỏ tick expiration) và cập nhật lại cấu hình.",
                ],
                dedupe_seconds=24 * 3600,
            )
            continue

        summary["ok"] += 1
    return summary


@shared_task(
    bind=True,
    name="apps.facebook_ads.tasks.sync_ads_report_lark",
    # KHÔNG retry tự động: task gửi thông báo/đẩy dữ liệu ra ngoài — chạy lại là
    # gửi trùng (audit 2026-09-23 T1). Lỗi tạm thời để beat/tick sau xử lý.
    max_retries=0,
    soft_time_limit=300,
    time_limit=360,
)
def sync_ads_report_lark(self) -> dict:
    """Cron entrypoint: push the rolling 7-day ads report to Lark Bitable."""
    from apps.facebook_ads.infrastructure.lark_ads_report_sync import sync_ads_report_to_lark

    try:
        return sync_ads_report_to_lark()
    except Exception:
        logger.exception("sync_ads_report_lark failed")
        raise


@shared_task(
    bind=True,
    name="apps.facebook_ads.tasks.sync_all_ad_catalog",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=600,
    time_limit=660,
)
def sync_all_ad_catalog(self) -> dict:
    """Cron 1h: làm mới danh mục ad + page. Tách khỏi luồng chi tiêu vì nhịp khác hẳn."""
    with single_flight("lock:fb_sync_ad_catalog", 700) as acquired:
        if not acquired:
            logger.info("sync_all_ad_catalog already running — skip this tick")
            return {"skipped": "already_running"}
        return _sync_all_ad_catalog()


def _sync_all_ad_catalog() -> dict:
    summary: dict = {
        "apps": 0, "accounts": 0, "ads_upserted": 0,
        "pages_upserted": 0, "unresolved_pages": 0,
        # TKQC lỗi lẻ được `sync_ad_catalog_for_app` cô lập — phải lộ ra ở kết quả
        # task, nếu không operator đọc "thành công" trong khi catalog đang khuyết.
        "failed_accounts": 0,
        "skipped_apps": 0, "errors": [],
    }
    # Một TKQC có thể gắn vào nhiều app — chỉ kéo mỗi fb_id một lần mỗi lượt.
    synced_fb_ids: set[str] = set()
    for app in FacebookApp.objects.filter(deleted_at__isnull=True).order_by("pk"):
        if app.token_status == FacebookApp.TOKEN_INVALID:
            # Ngắt mạch: cùng logic với _sync_all_daily_spend — token chết thì thử
            # lại vô nghĩa, chỉ log throttled 1 lần/giờ thay vì mỗi lượt cron.
            summary["skipped_apps"] += 1
            if cache.add(
                _breaker_log_key("ad_catalog", app.pk), "1", _BREAKER_LOG_TTL_SECONDS,
            ):
                logger.warning(
                    "sync_ad_catalog: bỏ qua app %s — token đang hỏng (%s)",
                    app.pk, app.token_error or "không rõ lý do",
                )
            continue
        try:
            result = sync_ad_catalog_for_app(app, synced_fb_ids)
            summary["apps"] += 1
            for key in ("accounts", "ads_upserted", "pages_upserted", "unresolved_pages"):
                summary[key] += result.get(key, 0)
            failed = result.get("failed_accounts", 0)
            if failed:
                logger.warning(
                    "sync_ad_catalog: app %s has %d failed account(s)", app.id, failed,
                )
                summary["failed_accounts"] += failed
            mark_token_ok(app)
        except FacebookTokenError as exc:
            logger.warning("sync_ad_catalog: token app %s bị từ chối: %s", app.id, exc)
            mark_token_invalid(app, str(exc))
            _alert_token_invalid(app, str(exc))
            summary["errors"].append({"app_id": app.id, "error": str(exc), "kind": "token"})
        except FacebookGraphError as exc:
            logger.warning("sync_ad_catalog failed for app %s: %s", app.id, exc)
            summary["errors"].append({"app_id": app.id, "error": str(exc)})
        except SoftTimeLimitExceeded:
            # Đứng trước `except Exception` — xem giải thích ở _sync_all_ad_spend.
            logger.warning(
                "sync_ad_catalog: chạm soft time limit ở app %s — dừng lượt này", app.id,
            )
            raise
        except Exception:
            logger.exception("Unexpected error syncing ad catalog for app %s", app.id)
            summary["errors"].append({"app_id": app.id, "error": "unexpected"})
    return summary


@shared_task(
    bind=True,
    name="apps.facebook_ads.tasks.sync_all_ad_spend",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    # KHÔNG dùng 300s như grain account: khối lượng lớn hơn hai bậc. Grain account
    # là (số TKQC × 7) dòng; grain ad là (số ad × 7) dòng, và mỗi ad chưa có trong
    # catalog còn kéo thêm một GET tuần tự (`_lazy_fetch_ad`, timeout 30s + retry).
    # Lần deploy đầu catalog rỗng nên VÀI TRĂM ad đều phải lazy-fetch — 300s không
    # bao giờ đủ, task bị hard-kill và giữ khoá tới hết TTL. Xem mục "Triển khai"
    # trong spec: phải chạy tay `sync_all_ad_catalog` một lượt trước khi bật beat.
    soft_time_limit=900,
    time_limit=960,
)
def sync_all_ad_spend(self) -> dict:
    """Cửa sổ trailing 7 ngày ở grain ad. Chạy độc lập với sync_all_daily_spend."""
    # TTL khoá (1000s) PHẢI > time_limit (960s): nếu ngắn hơn, worker bị hard-kill
    # vẫn còn đang chạy trong khi khoá đã hết hạn → tick sau chen vào, hai lượt cùng
    # upsert một tập dòng.
    with single_flight("lock:fb_sync_all_ad_spend", 1000) as acquired:
        if not acquired:
            logger.info("sync_all_ad_spend already running — skip this tick")
            return {"skipped": "already_running"}
        return _sync_all_ad_spend()


def _sync_all_ad_spend() -> dict:
    summary: dict = {
        "apps": 0,
        "rows_upserted": 0,
        # Không có hai khoá này thì operator đọc kết quả task thấy "thành công sạch"
        # trong khi vài TKQC im lặng hỏng và chốt so khớp 0.98 đã kêu — đúng kiểu
        # "số sai âm thầm" mà cả feature này sinh ra để chặn.
        "failed_accounts": 0,
        "variance_findings": 0,
        "skipped_apps": 0,
        "errors": [],
    }
    for app in FacebookApp.objects.filter(deleted_at__isnull=True):
        if app.token_status == FacebookApp.TOKEN_INVALID:
            # Ngắt mạch: cùng logic với _sync_all_daily_spend/_sync_all_ad_catalog —
            # token chết thì thử lại vô nghĩa, chỉ log throttled 1 lần/giờ thay vì
            # mỗi lượt cron.
            summary["skipped_apps"] += 1
            if cache.add(
                _breaker_log_key("ad_spend", app.pk), "1", _BREAKER_LOG_TTL_SECONDS,
            ):
                logger.warning(
                    "sync_ad_spend: bỏ qua app %s — token đang hỏng (%s)",
                    app.pk, app.token_error or "không rõ lý do",
                )
            continue
        try:
            result = sync_ad_spend_for_app(app)
            summary["apps"] += 1
            summary["rows_upserted"] += result.get("rows_upserted", 0)
            failed = result.get("failed_accounts", 0)
            if failed:
                # Cùng cách xử lý với _sync_all_daily_spend: TKQC lỗi không raise
                # (batch API vẫn 200 ở tầng ngoài) nên phải tự log ra mỗi lượt cron.
                logger.warning(
                    "sync_ad_spend: app %s has %d failed account(s)", app.id, failed,
                )
                summary["failed_accounts"] += failed
            variance = result.get("variance_findings", 0)
            if variance:
                logger.warning(
                    "sync_ad_spend: app %s has %d spend variance finding(s)",
                    app.id, variance,
                )
                summary["variance_findings"] += variance
            mark_token_ok(app)
        except FacebookTokenError as exc:
            logger.warning("sync_ad_spend: token app %s bị từ chối: %s", app.id, exc)
            mark_token_invalid(app, str(exc))
            _alert_token_invalid(app, str(exc))
            summary["errors"].append({"app_id": app.id, "error": str(exc), "kind": "token"})
        except FacebookGraphError as exc:
            logger.warning("sync_ad_spend failed for app %s: %s", app.id, exc)
            summary["errors"].append({"app_id": app.id, "error": str(exc)})
        except SoftTimeLimitExceeded:
            # Phải đứng TRƯỚC `except Exception`: SoftTimeLimitExceeded kế thừa
            # Exception, nuốt nó là vô hiệu hoá hẳn soft limit — task chạy tiếp tới
            # khi bị hard-kill ở time_limit, không kịp dọn và giữ khoá tới hết TTL.
            logger.warning("sync_ad_spend: chạm soft time limit ở app %s — dừng lượt này", app.id)
            raise
        except Exception:
            logger.exception("Unexpected error syncing ad spend for app %s", app.id)
            summary["errors"].append({"app_id": app.id, "error": "unexpected"})
    return summary


@shared_task(
    name="apps.facebook_ads.tasks.assign_campaign_products",
    autoretry_for=(OperationalError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=120,
    time_limit=180,
)
def assign_campaign_products() -> dict:
    """Đọc tên chiến dịch → gán sản phẩm cho những dòng chưa ai gán tay.

    Chạy theo lịch thay vì làm một lần trong migration: chiến dịch mới sinh liên tục,
    và sản phẩm mới thêm vào danh mục có thể làm một tên cũ bỗng đọc ra được. Nhờ vậy
    cũng không cần bước backfill thủ công sau khi deploy.
    """
    from apps.catalog.sku_text_match import assign_products_to_campaigns
    from apps.orders.reporting.product_sales_rank import product_revenue_rank

    from .models import FacebookAdCampaign

    # Bảng xếp hạng doanh thu là BẮT BUỘC, không phải tuỳ chọn: thiếu nó thì mã gốc
    # `TMS01` sẽ ra SKU rác cùng tên thay vì `TMS01-PROMAX` (657 chiến dịch trên prod).
    result = assign_products_to_campaigns(
        FacebookAdCampaign.objects.all(), rank=product_revenue_rank(),
    )
    if result["assigned"]:
        logger.info(
            "gán sản phẩm cho chiến dịch Facebook: %s/%s", result["assigned"], result["scanned"],
        )
    return result
