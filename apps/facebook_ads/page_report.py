"""Tổng hợp chi phí quảng cáo theo page.

Khác báo cáo theo kênh ở chỗ KHÔNG chia đều spend cho số kênh dùng chung TKQC
(``lark_ads_report_sync.py:129``) — chi phí ở đây gán đúng page thật qua grain ad.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Sum

from apps.facebook_ads.models import FacebookAdDailySpend

# Hệ số hiển thị chi phí Ads (phụ phí/VAT theo yêu cầu business, spec §8.2) — đổi
# theo mốc thời gian nên là HÀM chứ không phải hằng số.
# TÁI SỬ DỤNG helper của báo cáo theo kênh, KHÔNG định nghĩa lại ở đây: hai tab
# "Theo kênh" / "Theo page" nằm trên CÙNG một màn hình, cùng khoảng ngày — lệch hệ
# số là hai con số chênh nhau mà không có gì giải thích, người đọc sẽ tin nhầm
# một trong hai. Định nghĩa lại tại chỗ chỉ dời thời điểm lệch sang lần business
# đổi hệ số. Chiều import hợp lệ theo contract phân tầng (`.importlinter`):
# `apps.facebook_ads` nằm TRÊN `apps.orders` nên được phép import xuống.
from apps.orders._repositories_reports import facebook_ads_cost_multiplier

# Nhãn cho chi phí của ad chưa suy ra được page. Không gộp im lặng vào tổng: giấu
# đi thì tổng báo cáo page không khớp tổng theo TKQC mà không ai biết vì sao.
UNKNOWN_PAGE_LABEL = "Không rõ page"


def page_display_name(fb_page_id: str, name: str) -> str:
    """Nhãn hiển thị. Không bao giờ trả chuỗi rỗng — ô trống đọc như lỗi giao diện."""
    if not fb_page_id:
        return UNKNOWN_PAGE_LABEL
    return name or f"Page {fb_page_id}"


def aggregate_page_cost(
    since: date, until: date, ad_account_ids: list[int] | None = None,
) -> list[dict]:
    """Chi phí + hiển thị + click theo page trong khoảng ngày.

    ``reach`` cố ý KHÔNG cộng dồn (spec §9): reach không cộng được giữa các ngày,
    tổng ra sẽ lớn hơn thực tế và không có cách sửa ở tầng đọc.
    """
    qs = FacebookAdDailySpend.objects.filter(date__gte=since, date__lte=until)
    if ad_account_ids is not None:
        qs = qs.filter(ad__ad_account_id__in=ad_account_ids)

    rows = (
        qs.values("ad__page__fb_page_id", "ad__page__name")
        .annotate(
            cost=Sum("spend"),
            impressions=Sum("impressions"),
            clicks=Sum("clicks"),
        )
        .order_by("-cost")
    )

    # Hệ số chọn theo NGÀY BẮT ĐẦU kỳ, một lần cho cả bảng — mọi dòng trong cùng
    # một lần xem phải chung hệ số, không thì hai dòng cạnh nhau không so được.
    multiplier = facebook_ads_cost_multiplier(since)
    result: list[dict] = []
    for row in rows:
        fb_page_id = row["ad__page__fb_page_id"] or ""
        # `is None` chứ không dùng truthy: Decimal("0.00") falsy nên `or` sẽ nuốt
        # mất 2 số thập phân của trường hợp chi phí đúng bằng 0, làm "0.00" hiển
        # thị thành "0" — lệch định dạng so với mọi dòng khác trong cùng bảng.
        raw_cost = row["cost"] if row["cost"] is not None else Decimal("0")
        # Nhân hệ số trên TỔNG đã cộng (không nhân từng dòng) — giống báo cáo theo
        # kênh: nhân sau khi SUM thì không tích luỹ sai số làm tròn từng ngày.
        cost = raw_cost * multiplier
        result.append({
            "page_id": fb_page_id,
            "page_name": page_display_name(fb_page_id, row["ad__page__name"] or ""),
            "cost": f"{cost:.2f}",
            "impressions": int(row["impressions"] or 0),
            "clicks": int(row["clicks"] or 0),
            # Giai đoạn A chưa có attribution — null chứ không phải 0.
            "revenue": None,
            "closed_orders": None,
            "ratio_pct": None,
        })
    return result
