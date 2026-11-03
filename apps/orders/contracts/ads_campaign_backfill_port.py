"""Port gán sản phẩm cho chiến dịch quảng cáo theo tên (backfill chạy tay).

Hai lệnh ``backfill_fb_campaign_products`` / ``backfill_google_campaign_products``
từng là hai bản chép của nhau vì ``facebook_ads`` và ``google_ads`` nằm CÙNG tầng
(.importlinter): không app nào import app kia, còn ``orders`` (nơi có bảng xếp
hạng doanh thu) lại ở DƯỚI cả hai nên không import ngược lên được. Cùng khuôn với
``ads_spend_port``: orders khai port + registry, mỗi app quảng cáo cắm provider
của mình ở ``AppConfig.ready()`` (``facebook_ads|google_ads/adapters.py``), và
lệnh chung ``backfill_ads_campaign_products --provider ...`` sống ở orders.

Provider chỉ cần trả QuerySet chiến dịch — ``apps.catalog.sku_text_match``
đọc ``name`` / ``product_id`` / ``product_source`` trên từng dòng, không quan tâm
model là gì.
"""

from __future__ import annotations

from typing import Protocol

from apps.orders.contracts.ads_spend_port import AdsSpendSource


class AdsCampaignBackfillProvider(Protocol):
    """Một nguồn quảng cáo cho lệnh backfill."""

    label: str
    """Nhãn in trong báo cáo (``"Facebook"`` / ``"Google Ads"``)."""

    def campaigns(self):
        """QuerySet MỌI chiến dịch của nguồn — luật "không đụng dòng gán tay" nằm
        ở ``assign_products_to_campaigns``, provider không lọc trước."""
        ...


_providers: dict[str, AdsCampaignBackfillProvider] = {}


def register_ads_campaign_backfill_provider(
    source: AdsSpendSource, provider: AdsCampaignBackfillProvider
) -> None:
    """App quảng cáo gọi ở ``AppConfig.ready()`` để cắm provider."""
    _providers[AdsSpendSource(source).value] = provider


def ads_campaign_backfill_provider(source: AdsSpendSource | str) -> AdsCampaignBackfillProvider:
    """Resolve provider đã đăng ký; lỗi rõ ràng nếu wiring chưa chạy."""
    key = AdsSpendSource(source).value
    provider = _providers.get(key)
    if provider is None:
        raise RuntimeError(
            f"AdsCampaignBackfillProvider cho nguồn '{key}' chưa được đăng ký — "
            "AppConfig.ready() của app quảng cáo tương ứng phải chạy trước "
            "(app có trong INSTALLED_APPS?)."
        )
    return provider


def registered_sources() -> list[str]:
    return sorted(_providers)
