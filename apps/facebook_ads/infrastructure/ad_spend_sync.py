"""Chi tiêu Facebook Ads ở grain ad × ngày.

Chạy song song với ``services.sync_daily_spend_for_app`` (grain account) chứ không
thay thế: tổng cộng từ grain ad luôn thấp hơn grain account vì FB có khoản không
gán được về ad cụ thể (spec §6).
"""

import logging
from datetime import timedelta
from decimal import Decimal

from celery.exceptions import SoftTimeLimitExceeded
from django.db.models import Sum
from django.utils import timezone

from apps.common.ops_alerts import send_ops_alert
from apps.facebook_ads.ad_catalog import parse_ad_payload
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookAdAccountDailySpend,
    FacebookAdDailySpend,
    FacebookApp,
)
from apps.facebook_ads.services import (
    GRAPH_API_BASE,
    _client,
    _log_batch_errors,
    fetch_insights_batch,
)

logger = logging.getLogger(__name__)

# Cửa sổ refresh trailing, LUÔN gồm hôm nay (bài học services.py:383).
AD_SPEND_RECENT_DAYS = 7
AD_FIELDS_LAZY = (
    "id,name,campaign{id,name},adset{id,name,promoted_object},"
    "creative{id,effective_object_story_id,object_story_spec{page_id}}"
)

# Dưới ngưỡng này coi như grain ad hụt bất thường so với grain account.
SPEND_VARIANCE_THRESHOLD = 0.98
_VARIANCE_ALERT_TTL_SECONDS = 12 * 3600


def spend_variance_ratio(ad_total, account_total) -> float | None:
    """Tỷ lệ tổng grain ad / tổng grain account. None khi không có mẫu số."""
    if not account_total:
        return None
    return float(ad_total) / float(account_total)


def check_spend_variance(app: FacebookApp) -> list[dict]:
    """So tổng hai grain theo (TKQC, ngày). Trả danh sách ngày lệch quá ngưỡng.

    CHỈ kiểm ngày đã đóng: chi tiêu hôm nay ở hai grain có độ trễ cập nhật khác
    nhau nên luôn lệch, kiểm ngày đang chạy sẽ sinh cảnh báo rác mỗi lượt cron —
    và cảnh báo rác thì không ai đọc nữa.
    """
    cutoff = timezone.localdate()
    window_start = cutoff - timedelta(days=AD_SPEND_RECENT_DAYS)

    account_totals = {
        (row["ad_account_id"], row["date"]): row["total"]
        for row in FacebookAdAccountDailySpend.objects.filter(
            ad_account__app=app, date__gte=window_start, date__lt=cutoff,
        ).values("ad_account_id", "date").annotate(total=Sum("spend"))
    }
    ad_totals = {
        (row["ad__ad_account_id"], row["date"]): row["total"]
        for row in FacebookAdDailySpend.objects.filter(
            ad__ad_account__app=app, date__gte=window_start, date__lt=cutoff,
        ).values("ad__ad_account_id", "date").annotate(total=Sum("spend"))
    }

    findings: list[dict] = []
    for key, account_total in account_totals.items():
        account_id, day = key
        ratio = spend_variance_ratio(ad_totals.get(key, 0), account_total)
        if ratio is None or ratio >= SPEND_VARIANCE_THRESHOLD:
            continue
        findings.append({"ad_account_id": account_id, "date": day, "ratio": ratio})
        send_ops_alert(
            key=f"fb:spend_variance:{account_id}:{day.isoformat()}",
            title=(
                f"[Facebook Ads] Chi tiêu theo ad hụt so với theo tài khoản "
                f"(TKQC {account_id}, ngày {day:%d/%m})"
            ),
            lines=[
                f"Tổng theo tài khoản: {account_total}",
                f"Tổng cộng từ các ad: {ad_totals.get(key, 0)} ({ratio:.1%})",
                "Báo cáo theo page sẽ thiếu phần chênh này.",
                "Kiểm tra: ad bị xoá khỏi TKQC, hoặc khoản điều chỉnh/credit ở cấp tài khoản.",
            ],
            dedupe_seconds=_VARIANCE_ALERT_TTL_SECONDS,
        )
    return findings


def _lazy_fetch_ad(app: FacebookApp, account: FacebookAdAccount, fb_ad_id: str) -> FacebookAd:
    """Bổ sung ad thiếu trong catalog. Không gọi được API thì vẫn tạo bản ghi trống.

    Insights trả chi tiêu của cả ad đã xoá, nhưng ``/ads`` không liệt kê chúng nữa.
    Bỏ qua những dòng này là làm hụt tổng chi phí — nguy hiểm hơn nhiều so với việc
    một page hiển thị "Không rõ".
    """
    from apps.facebook_ads.infrastructure.campaign_upsert import resolve_campaign
    from apps.facebook_ads.models import FacebookPage

    defaults = {"ad_account": account}
    try:
        response = _client.get(
            f"{GRAPH_API_BASE}/{fb_ad_id}",
            params={"fields": AD_FIELDS_LAZY, "access_token": app.access_token},
            timeout=30,
        )
        if response.status_code == 200:
            parsed = parse_ad_payload(response.json() or {})
            defaults.update({
                "name": parsed["name"],
                "campaign": resolve_campaign(
                    parsed["campaign_id"], parsed["campaign_name"], ad_account=account,
                ),
                "adset_id": parsed["adset_id"],
                "adset_name": parsed["adset_name"],
            })
            if parsed["fb_page_id"]:
                page, _ = FacebookPage.objects.get_or_create(fb_page_id=parsed["fb_page_id"])
                defaults["page"] = page
    except SoftTimeLimitExceeded:
        # Tầng 1/3 phải cho tín hiệu dừng mềm đi qua. Đây là vòng lặp TỐN THỜI GIAN
        # NHẤT của task (1 GET tuần tự cho mỗi ad lạ, timeout 30s + retry): nuốt ở
        # đây là bảo đảm soft limit không bao giờ có tác dụng.
        raise
    except Exception:  # noqa: BLE001 - ad rác không được làm hỏng cả lượt đồng bộ
        logger.warning("lazy fetch ad %s thất bại — tạo bản ghi trống", fb_ad_id)

    ad, _ = FacebookAd.objects.get_or_create(fb_ad_id=fb_ad_id, defaults=defaults)
    return ad


def _upsert_ad_rows(
    app: FacebookApp, account: FacebookAdAccount, rows: list[dict],
) -> tuple[int, int, int]:
    """Ghi các dòng insights vào DB.

    Trả (số dòng upsert, số ad phải lazy-fetch, số dòng bị bỏ vì thiếu khoá).
    """
    if not rows:
        return 0, 0, 0

    # FB có thể trả trùng (ad_id, date_start) trong cùng 1 response (batch trả về
    # dữ liệu chồng lấn). bulk_create(update_conflicts=True) sẽ ném lỗi Postgres
    # "ON CONFLICT DO UPDATE command cannot affect row a second time" nếu 2 dòng
    # cùng khoá unique nằm trong 1 lệnh — khử trùng trước, giữ dòng ĐỨNG SAU (FB gửi
    # lại nghĩa là bản mới hơn, giống logic ghi đè giữa 2 lượt sync).
    deduped: dict[tuple[str, str], dict] = {}
    skipped = 0
    for r in rows:
        fb_ad_id = str(r.get("ad_id") or "")
        date_str = r.get("date_start")
        if not fb_ad_id or not date_str:
            # Không có (ad_id, ngày) thì không có chỗ để ghi — nhưng ĐẾM và báo ra,
            # không bỏ im lặng. Cả pipeline này dựng lên với cam kết "chi tiêu không
            # bao giờ biến mất mà không để lại dấu vết"; đây là đường rơi duy nhất
            # còn lại, và nếu FB đổi tên field thì nó rơi 100% dòng chứ không vài dòng.
            skipped += 1
            continue
        deduped[(fb_ad_id, date_str)] = r

    fb_ad_ids = {key[0] for key in deduped}
    known = {a.fb_ad_id: a for a in FacebookAd.objects.filter(fb_ad_id__in=fb_ad_ids)}

    lazy_count = 0
    for fb_ad_id in sorted(fb_ad_ids - set(known)):
        known[fb_ad_id] = _lazy_fetch_ad(app, account, fb_ad_id)
        lazy_count += 1

    objs = [
        FacebookAdDailySpend(
            ad=known[fb_ad_id],
            date=date_str,
            spend=Decimal(str(r.get("spend") or "0")),
            impressions=int(r.get("impressions") or 0),
            clicks=int(r.get("clicks") or 0),
            reach=int(r.get("reach") or 0),
        )
        for (fb_ad_id, date_str), r in deduped.items()
    ]

    if objs:
        # bulk_create + update_conflicts: số dòng mỗi lượt cao hơn grain account
        # khoảng hai bậc, update_or_create từng dòng sẽ thành N query.
        FacebookAdDailySpend.objects.bulk_create(
            objs,
            update_conflicts=True,
            update_fields=["spend", "impressions", "clicks", "reach", "last_synced_at"],
            unique_fields=["ad", "date"],
        )
    return len(objs), lazy_count, skipped


def sync_ad_spend_for_app(
    app: FacebookApp, *, since: str | None = None, until: str | None = None,
) -> dict:
    """Đồng bộ chi tiêu grain ad. Không truyền since/until = cửa sổ trailing 7 ngày."""
    accounts = list(
        FacebookAdAccount.objects.filter(app=app, status=FacebookAdAccount.STATUS_ACTIVE)
    )
    if not accounts:
        # Cùng SHAPE với nhánh thường: caller (task/command) đọc bằng .get() nên khoá
        # thiếu sẽ âm thầm thành 0, che mất khác biệt giữa "không có TKQC nào" và
        # "có TKQC nhưng không lấy được gì".
        return {
            "accounts": 0,
            "rows_upserted": 0,
            "lazy_fetched_ads": 0,
            "skipped_rows": 0,
            "failed_accounts": 0,
            "variance_findings": 0,
        }

    custom_range = bool(since and until)
    if not custom_range:
        today = timezone.localdate()
        until = today.isoformat()
        since = (today - timedelta(days=AD_SPEND_RECENT_DAYS)).isoformat()

    results = fetch_insights_batch(
        app, accounts, level="ad", since=since, until=until, time_increment="1",
    )
    failed = _log_batch_errors(results, accounts)

    upserted = 0
    lazy = 0
    skipped = 0
    for account in accounts:
        try:
            rows_upserted, lazy_count, skipped_count = _upsert_ad_rows(
                app, account, results.get(account.id) or [],
            )
        except SoftTimeLimitExceeded:
            # Tầng 2/3: tín hiệu dừng mềm không phải "account này lỗi" — nuốt nó ở
            # đây là chạy tiếp qua toàn bộ account còn lại rồi mới bị hard-kill.
            raise
        except Exception:  # noqa: BLE001 - 1 account lỗi (vd DB xung đột) không được
            # chặn các account còn lại trong cùng lượt sync — sibling grain account
            # đã an toàn vì upsert từng dòng, ở đây bulk_create nên phải tự bọc guard.
            logger.exception(
                "facebook_ads ad spend upsert failed account_id=%s", account.id,
            )
            failed += 1
            continue
        upserted += rows_upserted
        lazy += lazy_count
        skipped += skipped_count

    if skipped:
        logger.warning(
            "facebook_ads ad spend: bỏ %d dòng insights thiếu ad_id/date_start "
            "(app_id=%s) — phần chi tiêu này KHÔNG vào báo cáo theo page",
            skipped, app.id,
        )

    # Chỉ chạy cho nhánh cửa sổ trailing; backfill tự kiểm bằng command riêng.
    variance = check_spend_variance(app) if not custom_range else []

    return {
        "accounts": len(accounts),
        "rows_upserted": upserted,
        "lazy_fetched_ads": lazy,
        "skipped_rows": skipped,
        "failed_accounts": failed,
        "variance_findings": len(variance),
    }
