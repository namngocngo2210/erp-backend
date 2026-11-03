"""Tổng hợp chi phí Google Ads theo chiến dịch.

Cùng chữ ký và cùng hình dạng dòng trả về với `apps.facebook_ads.campaign_report` —
frontend gộp hai nguồn nên không được để hai payload lệch nhau.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Sum

from apps.google_ads.models import GoogleAdsCampaignDailySpend

# Hệ số của GOOGLE (VAT 8% tại VN), KHÔNG dùng ADS_DAILY_COST_MULTIPLIER — hằng số
# đó là 1.1 của Facebook. Dùng nhầm hệ số FB ở đây từng làm tab "Chiến dịch" thổi
# chi phí Google lên 1,85% so với Báo cáo tổng và Báo cáo ads trên CÙNG màn hình
# (prod tháng 8/2026: 161,9tr thay vì 158,9tr — lệch ~2,94tr).
from apps.orders._repositories_reports import GOOGLE_ADS_DAILY_COST_MULTIPLIER

PLATFORM = "google"

UNKNOWN_CAMPAIGN_LABEL = "Không rõ chiến dịch"


def aggregate_campaign_cost(
    since: date, until: date, ad_account_ids: list[int] | None = None,
) -> list[dict]:
    """Chi phí + hiển thị + click theo chiến dịch trong khoảng ngày."""
    qs = GoogleAdsCampaignDailySpend.objects.filter(date__gte=since, date__lte=until)
    if ad_account_ids is not None:
        qs = qs.filter(campaign__ad_account_id__in=ad_account_ids)

    rows = (
        qs.values(
            "campaign__campaign_id", "campaign__name", "campaign__ad_account__name",
        )
        .annotate(
            cost=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
        )
        .order_by("-cost")
    )

    result: list[dict] = []
    for row in rows:
        raw_cost = row["cost"] if row["cost"] is not None else Decimal("0")
        cost = raw_cost * GOOGLE_ADS_DAILY_COST_MULTIPLIER
        result.append({
            "platform": PLATFORM,
            "account_name": row["campaign__ad_account__name"] or "",
            "campaign_id": row["campaign__campaign_id"] or "",
            "campaign_name": row["campaign__name"] or UNKNOWN_CAMPAIGN_LABEL,
            "cost": f"{cost:.2f}",
            "impressions": int(row["impressions"] or 0),
            "clicks": int(row["clicks"] or 0),
            "revenue": None,
        })
    return result
