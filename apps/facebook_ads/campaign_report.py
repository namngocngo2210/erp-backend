"""Tổng hợp chi phí quảng cáo theo chiến dịch.

Roll-up thẳng từ grain ad (`FacebookAdDailySpend` + FK `FacebookAd.campaign`) — chi
tiêu Facebook lưu ở tầng ad, không có bảng spend riêng cho campaign. Không gọi thêm
Graph API. Đổi chiều báo cáo sang adset sau này cũng chỉ là đổi cột `values()`.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Sum

from apps.facebook_ads.models import FacebookAdDailySpend

# TÁI SỬ DỤNG helper của báo cáo theo kênh/page, KHÔNG định nghĩa lại: ba tab nằm
# trên CÙNG một màn hình, lệch hệ số là hai con số chênh nhau không lời giải thích.
# Là HÀM chứ không hằng số vì hệ số FB đổi theo mốc thời gian.
# Chiều import hợp lệ theo `.importlinter`: apps.facebook_ads nằm TRÊN apps.orders.
from apps.orders._repositories_reports import facebook_ads_cost_multiplier

PLATFORM = "facebook"

# Ad chưa gắn được campaign (ad đã xoá trên FB vẫn còn chi tiêu lịch sử). Không gộp
# im lặng vào tổng: giấu đi thì tổng theo chiến dịch không khớp tổng theo TKQC mà
# không ai biết vì sao.
UNKNOWN_CAMPAIGN_LABEL = "Không rõ chiến dịch"


def aggregate_campaign_cost(
    since: date, until: date, ad_account_ids: list[int] | None = None,
) -> list[dict]:
    """Chi phí + hiển thị + click theo chiến dịch trong khoảng ngày.

    KHÔNG trả `reach` dù grain ad có lưu: reach không cộng được giữa các ngày, và
    Google không có cột tương đương — một cột chỉ có số ở nửa số dòng đọc như lỗi.
    """
    qs = FacebookAdDailySpend.objects.filter(date__gte=since, date__lte=until)
    if ad_account_ids is not None:
        qs = qs.filter(ad__ad_account_id__in=ad_account_ids)

    rows = (
        qs.values(
            "ad__campaign__fb_campaign_id",
            "ad__campaign__name",
            "ad__ad_account__name",
        )
        .annotate(
            cost=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
        )
        .order_by("-cost")
    )

    # Hệ số theo NGÀY BẮT ĐẦU kỳ — một lần cho cả bảng, xem mục `shared.py`.
    multiplier = facebook_ads_cost_multiplier(since)
    result: list[dict] = []
    for row in rows:
        campaign_id = row["ad__campaign__fb_campaign_id"] or ""
        # `is None` chứ không `or`: Decimal("0.00") falsy nên `or` sẽ nuốt mất 2 số
        # thập phân của trường hợp chi phí đúng bằng 0.
        raw_cost = row["cost"] if row["cost"] is not None else Decimal("0")
        # Nhân hệ số trên TỔNG đã cộng, không nhân từng dòng — không tích luỹ sai số
        # làm tròn từng ngày.
        cost = raw_cost * multiplier
        result.append({
            "platform": PLATFORM,
            "account_name": row["ad__ad_account__name"] or "",
            "campaign_id": campaign_id,
            "campaign_name": row["ad__campaign__name"] or UNKNOWN_CAMPAIGN_LABEL,
            "cost": f"{cost:.2f}",
            "impressions": int(row["impressions"] or 0),
            "clicks": int(row["clicks"] or 0),
            # Chưa có attribution theo chiến dịch — null chứ không phải 0.
            "revenue": None,
        })
    return result
