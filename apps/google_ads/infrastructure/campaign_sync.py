"""Đồng bộ chi phí Google Ads ở grain chiến dịch.

Một truy vấn GAQL `FROM campaign` trả CẢ danh mục lẫn số liệu, nên catalog và chi phí
được upsert trong cùng vòng lặp — không tốn thêm quota so với việc chỉ lấy chi phí.

Nhịp đồng bộ mirror nguyên grain account đang chạy ổn định: account chưa có dòng nào
thì backfill 365 ngày, account đã có thì refresh 7 ngày gần nhất.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsCampaign,
    GoogleAdsCampaignDailySpend,
    GoogleAdsConfig,
)

# Helper nội bộ của app — dùng lại thay vì chép, để logic dựng client, chặn GAQL
# injection và đọc enum chỉ tồn tại ở MỘT chỗ.
from apps.google_ads.services import (
    BACKFILL_DAYS,
    MICROS_PER_UNIT,
    RECENT_DAYS,
    GoogleAdsApiError,
    _build_client,
    _enum_name,
    _safe_iso_date,
    _search,
)

logger = logging.getLogger(__name__)


def _fetch_campaign_rows(client, customer_id: str, since: str, until: str) -> list[dict]:
    """Lấy chi phí theo (chiến dịch, ngày) của 1 customer."""
    # Phòng GAQL injection: date được nội suy vào query nên phải parse chặt.
    since = _safe_iso_date(since)
    until = _safe_iso_date(until)
    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            segments.date,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks
        FROM campaign
        WHERE segments.date BETWEEN '{since}' AND '{until}'
    """
    out: list[dict] = []
    for row in _search(client, customer_id, query):
        out.append(
            {
                "campaign_id": str(row.campaign.id),
                "name": row.campaign.name or "",
                "status": _enum_name(row.campaign.status),
                "date": row.segments.date,
                "spend": (
                    Decimal(row.metrics.cost_micros) / MICROS_PER_UNIT
                ).quantize(Decimal("0.01")),
                "impressions": int(row.metrics.impressions or 0),
                "clicks": int(row.metrics.clicks or 0),
            }
        )
    return out


def _upsert_campaign_rows(account: GoogleAdsAccount, rows: list[dict]) -> int:
    """Upsert danh mục + chi phí. Trả số dòng chi phí đã ghi."""
    now = timezone.now()
    # Cache trong một lượt: backfill 365 ngày sinh 365 dòng CHUNG một chiến dịch,
    # không cache thì mỗi dòng là một update_or_create thừa lên danh mục.
    seen: dict[str, GoogleAdsCampaign] = {}
    upserted = 0
    for row in rows:
        campaign = seen.get(row["campaign_id"])
        if campaign is None:
            campaign, _ = GoogleAdsCampaign.objects.update_or_create(
                ad_account=account,
                campaign_id=row["campaign_id"],
                defaults={
                    "name": row["name"],
                    "status": row["status"],
                    "last_seen_at": now,
                },
            )
            seen[row["campaign_id"]] = campaign
        GoogleAdsCampaignDailySpend.objects.update_or_create(
            campaign=campaign,
            date=row["date"],
            defaults={
                "spend": row["spend"],
                "impressions": row["impressions"],
                "clicks": row["clicks"],
            },
        )
        upserted += 1
    return upserted


def sync_campaign_spend_for_config(
    config: GoogleAdsConfig,
    *,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Đồng bộ chi phí grain chiến dịch cho mọi customer active của config.

    Lỗi cách ly theo từng account (giống `sync_daily_spend_for_config`): một tài khoản
    mất quyền không được làm những tài khoản còn lại mất lượt, nhưng cũng không được
    biến mất khỏi log — đếm vào `failed_accounts`.
    """
    accounts = list(
        GoogleAdsAccount.objects.filter(config=config, status=GoogleAdsAccount.STATUS_ACTIVE)
    )
    if not accounts:
        return {"accounts": 0, "rows_upserted": 0, "failed_accounts": 0}

    client = _build_client(config)
    today = timezone.localdate()

    if since and until:
        windows = {account.id: (since, until) for account in accounts}
    else:
        backfill_since = (today - timedelta(days=BACKFILL_DAYS)).isoformat()
        recent_since = (today - timedelta(days=RECENT_DAYS)).isoformat()
        until_str = today.isoformat()
        has_data_ids = set(
            GoogleAdsCampaignDailySpend.objects.filter(campaign__ad_account__in=accounts)
            .values_list("campaign__ad_account_id", flat=True)
            .distinct()
        )
        windows = {
            account.id: (
                recent_since if account.id in has_data_ids else backfill_since,
                until_str,
            )
            for account in accounts
        }

    upserted = 0
    failed = 0
    for account in accounts:
        start, end = windows[account.id]
        try:
            rows = _fetch_campaign_rows(client, account.customer_id, start, end)
        except GoogleAdsApiError as exc:
            failed += 1
            logger.warning(
                "google_ads campaign sync bỏ qua customer %s (config %s): %s",
                account.customer_id, config.id, exc,
            )
            continue
        upserted += _upsert_campaign_rows(account, rows)

    if failed:
        logger.warning(
            "google_ads campaign sync: %d/%d tài khoản lỗi (config %s)",
            failed, len(accounts), config.id,
        )

    return {"accounts": len(accounts), "rows_upserted": upserted, "failed_accounts": failed}
