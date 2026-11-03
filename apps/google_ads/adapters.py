"""Adapter cấp số liệu chi tiêu Google Ads cho port của `orders`.

Đối xứng với `apps/facebook_ads/adapters.py` — xem docstring ở đó.
"""

from __future__ import annotations

from datetime import date as date_cls
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db.models import Max, Sum

if TYPE_CHECKING:
    # Chỉ dùng cho type hint — import thật vẫn nằm trong thân method (xem lý do ở
    # docstring module): `apps.orders.contracts` có __init__.py kéo theo
    # `apps.orders.models`, nên import ở mức module sẽ phá đúng thứ mà lazy-import
    # đang phòng.
    from apps.orders.contracts.ads_spend_port import AdAccountScope


class GoogleAdsSpendAdapter:
    """Hiện thực ``apps.orders.contracts.ads_spend_port.AdsSpendPort``."""

    def _queryset(self, date_from: date_cls | None, date_to: date_cls | None):
        from apps.google_ads.models import GoogleAdsAccountDailySpend

        qs = GoogleAdsAccountDailySpend.objects.all()
        if date_from is not None:
            qs = qs.filter(date__gte=date_from)
        if date_to is not None:
            qs = qs.filter(date__lte=date_to)
        return qs

    def account_groups(self, account_ids: list[int]) -> list[list[int]]:
        """Hiện thực ``AdsSpendPort.account_groups`` — gom bản sao theo ``customer_id``.

        Cùng một tài khoản thật nằm ở nhiều dòng vì unique là ``(config, customer_id)``.
        Nhóm trả về gồm CẢ bản sao chưa được map vào kênh nào, vì chi tiêu có
        thể treo dưới bản đó.
        """
        from apps.google_ads.models import GoogleAdsAccount

        if not account_ids:
            return []
        external_ids = list(
            GoogleAdsAccount.objects.filter(id__in=account_ids)
            # GoogleAdsAccount hiện không có Meta.ordering, nhưng DISTINCT sẽ ngầm gộp
            # thêm cột ORDER BY nếu sau này có — order_by() rỗng chặn trước.
            .order_by()
            .values_list("customer_id", flat=True)
            .distinct()
        )
        grouped: dict[str, list[int]] = {}
        rows = (
            GoogleAdsAccount.objects.filter(customer_id__in=external_ids)
            .order_by()
            .values_list("id", "customer_id")
        )
        for pk, external_id in rows:
            grouped.setdefault(external_id, []).append(pk)
        return list(grouped.values())

    def account_scope(self, account_ids: list[int]) -> AdAccountScope:
        """Trải list PK sang mọi bản sao của cùng tài khoản thật, kèm số đếm thật.

        Mapping kênh chỉ trỏ được một dòng, còn dữ liệu chi tiêu có thể treo dưới
        dòng khác — nên lọc thẳng theo PK là cách làm báo cáo trắng bảng mà không
        để lại dấu vết. `count` đếm theo tài khoản THẬT, không phải số dòng: hiển
        thị `len(ids)` sẽ báo "2 tài khoản" cho người chỉ được gán 1.

        `account_ids` rỗng → `AdAccountScope([], 0)`.
        """
        from apps.orders.contracts.ads_spend_port import AdAccountScope

        groups = self.account_groups(account_ids)
        return AdAccountScope(
            ids=[pk for group in groups for pk in group], count=len(groups),
        )

    def spend_rows_by_account(
        self,
        *,
        account_ids: list[int],
        date_from: date_cls | None = None,
        date_to: date_cls | None = None,
    ) -> list[dict]:
        if not account_ids:
            return []
        qs = self._queryset(date_from, date_to).filter(ad_account_id__in=account_ids)
        return list(qs.values("date", "ad_account_id").annotate(spend=Sum("spend")))

    def spend_rows_by_account_product(
        self,
        *,
        account_ids: list[int],
        date_from: date_cls | None = None,
        date_to: date_cls | None = None,
    ) -> list[dict]:
        """Chi tiêu theo (ngày, TKQC, sản phẩm) — đọc ở grain CAMPAIGN.

        Khác bản Facebook ở chỗ PHẢI khử bản sao: ``GoogleAdsAccount`` unique theo
        ``(config, customer_id)`` nên hai MCC cùng thấy một customer sinh hai dòng
        tài khoản, kéo theo cùng một chiến dịch có hai dòng chi tiêu của CÙNG số
        tiền. Lấy ``Max`` theo (ngày, customer_id, campaign_id) trước rồi mới cộng —
        cùng luật với ``total_spend``.

        Dòng trả về mang PK ĐẠI DIỆN (nhỏ nhất) của mỗi customer_id: mọi bản sao đều
        nằm cùng một nhóm ở phía caller nên chọn bản nào cũng ra cùng tập kênh.
        """
        from apps.google_ads.models import GoogleAdsAccount, GoogleAdsCampaignDailySpend

        if not account_ids:
            return []
        qs = GoogleAdsCampaignDailySpend.objects.filter(
            campaign__ad_account_id__in=account_ids
        )
        if date_from is not None:
            qs = qs.filter(date__gte=date_from)
        if date_to is not None:
            qs = qs.filter(date__lte=date_to)
        deduped = qs.values(
            "date",
            "campaign__ad_account__customer_id",
            "campaign__campaign_id",
            "campaign__product_id",
        ).annotate(day_spend=Max("spend"))

        representative: dict[str, int] = {}
        for pk, customer_id in (
            GoogleAdsAccount.objects.filter(id__in=account_ids)
            .order_by()
            .values_list("id", "customer_id")
        ):
            current = representative.get(customer_id)
            if current is None or pk < current:
                representative[customer_id] = pk

        totals: dict[tuple, Decimal] = {}
        for row in deduped:
            customer_id = row["campaign__ad_account__customer_id"]
            account_pk = representative.get(customer_id)
            if account_pk is None:
                continue
            key = (row["date"], account_pk, row["campaign__product_id"])
            totals[key] = totals.get(key, Decimal("0")) + (row["day_spend"] or Decimal("0"))
        return [
            {"date": date, "ad_account_id": account_pk, "product_id": product_id, "spend": spend}
            for (date, account_pk, product_id), spend in totals.items()
        ]

    def total_spend(
        self,
        *,
        date_from: date_cls | None = None,
        date_to: date_cls | None = None,
    ) -> Decimal:
        """Tổng chi tiêu, gộp bản sao theo ``customer_id``.

        Đối xứng với bản Facebook (xem docstring ở đó về lý do dùng ``Max``).
        ``GoogleAdsAccount`` unique theo ``(config, customer_id)`` và job sync lặp
        mọi ``GoogleAdsConfig``, nên hai MCC cùng thấy một customer là sinh hai
        dòng chi tiêu của cùng số tiền. Prod 2026-08-19 chưa dính (2 dòng /
        2 customer) — chặn trước, không chờ thêm MCC.
        """
        per_account_day = (
            self._queryset(date_from, date_to)
            .values("ad_account__customer_id", "date")
            .annotate(day_spend=Max("spend"))
        )
        total = per_account_day.aggregate(total=Sum("day_spend"))["total"]
        return total or Decimal("0")


class GoogleAdsCampaignBackfillProvider:
    """Hiện thực ``apps.orders.contracts.ads_campaign_backfill_port.AdsCampaignBackfillProvider``."""

    label = "Google Ads"

    def campaigns(self):
        from apps.google_ads.models import GoogleAdsCampaign

        return GoogleAdsCampaign.objects.all()


def register() -> None:
    from apps.orders.contracts.ads_campaign_backfill_port import (
        register_ads_campaign_backfill_provider,
    )
    from apps.orders.contracts.ads_spend_port import (
        AdsSpendSource,
        register_ads_spend_provider,
    )

    register_ads_spend_provider(AdsSpendSource.GOOGLE, GoogleAdsSpendAdapter())
    register_ads_campaign_backfill_provider(
        AdsSpendSource.GOOGLE, GoogleAdsCampaignBackfillProvider()
    )
