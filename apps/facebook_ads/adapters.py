"""Adapter cấp số liệu chi tiêu Facebook Ads cho port của `orders`.

`facebook_ads` nằm ở tầng TRÊN `orders` nên import xuống là hợp lệ; adapter sống ở
đây và được đăng ký tại `FacebookAdsConfig.ready()`. Import model LƯỜI (lúc gọi)
để `ready()` không kéo model vào trước khi app registry sẵn sàng.
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


class FacebookAdsSpendAdapter:
    """Hiện thực ``apps.orders.contracts.ads_spend_port.AdsSpendPort``."""

    def _queryset(self, date_from: date_cls | None, date_to: date_cls | None):
        from apps.facebook_ads.models import FacebookAdAccountDailySpend

        qs = FacebookAdAccountDailySpend.objects.all()
        if date_from is not None:
            qs = qs.filter(date__gte=date_from)
        if date_to is not None:
            qs = qs.filter(date__lte=date_to)
        return qs

    def account_groups(self, account_ids: list[int]) -> list[list[int]]:
        """Hiện thực ``AdsSpendPort.account_groups`` — gom bản sao theo ``account_id``.

        Cùng một tài khoản thật nằm ở nhiều dòng vì unique là ``(app, account_id)``.
        Nhóm trả về gồm CẢ bản sao chưa được map vào kênh nào, vì chi tiêu có
        thể treo dưới bản đó.
        """
        from apps.facebook_ads.models import FacebookAdAccount

        if not account_ids:
            return []
        external_ids = list(
            FacebookAdAccount.objects.filter(id__in=account_ids)
            # FacebookAdAccount hiện không có Meta.ordering, nhưng DISTINCT sẽ ngầm gộp
            # thêm cột ORDER BY nếu sau này có — order_by() rỗng chặn trước.
            .order_by()
            .values_list("account_id", flat=True)
            .distinct()
        )
        grouped: dict[str, list[int]] = {}
        rows = (
            FacebookAdAccount.objects.filter(account_id__in=external_ids)
            .order_by()
            .values_list("id", "account_id")
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
        """Chi tiêu theo (ngày, TKQC, sản phẩm) — đọc ở grain AD, không phải account.

        CỘNG chứ không ``Max`` như ``total_spend``: ``FacebookAd.fb_ad_id`` unique
        toàn cục nên mỗi ad chỉ có MỘT dòng chi tiêu mỗi ngày, không có chuyện bản
        sao TKQC lặp lại cùng số tiền. Ngược lại, ``Max`` ở đây sẽ LÀM MẤT dữ liệu:
        prod 2026-08-24 có 5 TKQC thật mà ad nằm rải trên nhiều dòng tài khoản, các
        ad đó là ad KHÁC NHAU nên phải cộng.
        """
        from apps.facebook_ads.models import FacebookAdDailySpend

        if not account_ids:
            return []
        qs = FacebookAdDailySpend.objects.filter(ad__ad_account_id__in=account_ids)
        if date_from is not None:
            qs = qs.filter(date__gte=date_from)
        if date_to is not None:
            qs = qs.filter(date__lte=date_to)
        rows = qs.values("date", "ad__ad_account_id", "ad__campaign__product_id").annotate(
            spend=Sum("spend")
        )
        return [
            {
                "date": row["date"],
                "ad_account_id": row["ad__ad_account_id"],
                "product_id": row["ad__campaign__product_id"],
                "spend": row["spend"],
            }
            for row in rows
        ]

    def total_spend(
        self,
        *,
        date_from: date_cls | None = None,
        date_to: date_cls | None = None,
    ) -> Decimal:
        """Tổng chi tiêu, gộp bản sao: mỗi TKQC THẬT tính một lần cho mỗi ngày.

        KHÔNG được ``Sum`` thẳng cả bảng. Cùng một TKQC có nhiều dòng
        ``FacebookAdAccount`` (unique là ``(app, account_id)``) và
        ``sync_daily_spend_for_app`` chạy vòng qua MỌI app, nên mỗi bản sao có
        một dòng daily spend đầy đủ của cùng số tiền — unique
        ``(ad_account, date)`` không chặn được vì khác ``ad_account_id``.
        Prod 2026-08-19: 240 dòng / 60 TKQC thật → cộng thô ra đúng ×4.

        Gộp bằng ``Max`` chứ không phải ``Avg``/``Min``: chi tiêu trong ngày chỉ
        tăng, nên khi các bản sao lệch nhau vì sync chạy lệch nhịp (prod: 3.301đ)
        thì bản lớn nhất là bản mới nhất.
        """
        per_account_day = (
            self._queryset(date_from, date_to)
            .values("ad_account__account_id", "date")
            .annotate(day_spend=Max("spend"))
        )
        total = per_account_day.aggregate(total=Sum("day_spend"))["total"]
        return total or Decimal("0")


class FacebookAdsCampaignBackfillProvider:
    """Hiện thực ``apps.orders.contracts.ads_campaign_backfill_port.AdsCampaignBackfillProvider``."""

    label = "Facebook"

    def campaigns(self):
        from apps.facebook_ads.models import FacebookAdCampaign

        return FacebookAdCampaign.objects.all()


def register() -> None:
    from apps.orders.contracts.ads_campaign_backfill_port import (
        register_ads_campaign_backfill_provider,
    )
    from apps.orders.contracts.ads_spend_port import (
        AdsSpendSource,
        register_ads_spend_provider,
    )

    register_ads_spend_provider(AdsSpendSource.FACEBOOK, FacebookAdsSpendAdapter())
    register_ads_campaign_backfill_provider(
        AdsSpendSource.FACEBOOK, FacebookAdsCampaignBackfillProvider()
    )
