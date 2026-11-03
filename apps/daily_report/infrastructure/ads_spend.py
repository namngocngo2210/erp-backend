"""Chi tiêu quảng cáo theo TÀI KHOẢN THẬT — đọc OLTP (không có trong mart).

Một tài khoản thật có nhiều dòng ``FacebookAdAccount``/``GoogleAdsAccount`` (bản
sao theo app/config), mỗi bản sao lại có dòng chi tiêu đầy đủ của cùng số tiền.
Vì vậy gộp theo id ngoài (``account_id`` / ``customer_id``) và lấy ``Max`` mỗi
ngày — cùng luật với ``AdsSpendPort.total_spend`` để không lệch Báo cáo tổng.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from django.db.models import Max, Sum

from apps.channels_core.models import ChannelAdAccount
from apps.facebook_ads.models import (
    FacebookAdAccount,
    FacebookAdAccountDailySpend,
    FacebookAdCampaign,
    FacebookAdDailySpend,
)
from apps.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsAccountDailySpend,
    GoogleAdsCampaign,
    GoogleAdsCampaignDailySpend,
)

FACEBOOK = "facebook"
GOOGLE = "google"


@dataclass(frozen=True)
class AccountDay:
    platform: str
    account_id: str  # id ngoài của nền tảng
    name: str
    channels: tuple[str, ...]  # mã kênh đã map (mọi bản sao gộp lại)
    day: date
    spend: Decimal  # CHƯA nhân hệ số
    impressions: int
    clicks: int


@dataclass(frozen=True)
class AccountSpend:
    rows: list[AccountDay]
    synced_at: dict[str, datetime | None] = field(default_factory=dict)


@dataclass(frozen=True)
class CampaignDay:
    platform: str
    campaign_id: str  # id chiến dịch bên nền tảng
    name: str
    account_id: str  # id ngoài của TKQC chứa chiến dịch
    sku: str | None  # SKU đã gán cho chiến dịch (docs/ads-campaign-product-mapping.md)
    day: date
    spend: Decimal  # CHƯA nhân hệ số
    impressions: int
    clicks: int


def _facebook_campaign_rows(d_from: date, d_to: date) -> list[CampaignDay]:
    """Grain AD, CỘNG lên chiến dịch — không ``Max`` như grain tài khoản.

    ``FacebookAd.fb_ad_id`` unique toàn cục nên mỗi ad chỉ có MỘT dòng chi tiêu mỗi
    ngày; bản sao TKQC (cùng ``account_id``, khác app) mang ad KHÁC NHAU chứ không
    lặp lại cùng số tiền. Gộp theo id ngoài của TKQC để hai bản sao về một dòng.
    Cùng luật với ``FacebookAdsSpendAdapter.spend_rows_by_account_product``.
    """
    rows = (
        FacebookAdDailySpend.objects.filter(date__range=(d_from, d_to), ad__campaign__isnull=False)
        .values(
            "date",
            "ad__campaign__fb_campaign_id",
            "ad__campaign__name",
            "ad__ad_account__account_id",
            "ad__campaign__product__sku",
        )
        .annotate(spend=Sum("spend"), impressions=Sum("impressions"), clicks=Sum("clicks"))
        .order_by()
    )
    return [
        CampaignDay(
            platform=FACEBOOK,
            campaign_id=r["ad__campaign__fb_campaign_id"],
            name=r["ad__campaign__name"] or "",
            account_id=r["ad__ad_account__account_id"],
            sku=r["ad__campaign__product__sku"],
            day=r["date"],
            spend=r["spend"] or Decimal("0"),
            impressions=int(r["impressions"] or 0),
            clicks=int(r["clicks"] or 0),
        )
        for r in rows
    ]


def _google_campaign_rows(d_from: date, d_to: date) -> list[CampaignDay]:
    """Grain CHIẾN DỊCH, ``Max`` theo (ngày, customer_id, campaign_id).

    Hai MCC cùng thấy một customer sinh hai dòng ``GoogleAdsAccount`` và hai dòng
    ``GoogleAdsCampaign`` cho CÙNG chiến dịch, mỗi bản có chi tiêu đầy đủ của cùng
    số tiền — cộng thô là nhân đôi. Cùng luật với ``total_spend``.
    """
    per_day = (
        GoogleAdsCampaignDailySpend.objects.filter(date__range=(d_from, d_to))
        .values("date", "campaign__ad_account__customer_id", "campaign__campaign_id")
        .annotate(spend=Max("spend"), impressions=Max("impressions"), clicks=Max("clicks"))
        .order_by()
    )
    per_day = list(per_day)
    campaign_ids = {r["campaign__campaign_id"] for r in per_day}
    meta: dict[tuple[str, str], tuple[str, str | None]] = {}
    for customer_id, campaign_id, name, sku in (
        GoogleAdsCampaign.objects.filter(campaign_id__in=campaign_ids)
        .order_by("id")
        .values_list("ad_account__customer_id", "campaign_id", "name", "product__sku")
    ):
        meta.setdefault((customer_id, campaign_id), (name or "", sku))
    out = []
    for r in per_day:
        key = (r["campaign__ad_account__customer_id"], r["campaign__campaign_id"])
        name, sku = meta.get(key, ("", None))
        out.append(
            CampaignDay(
                platform=GOOGLE,
                campaign_id=key[1],
                name=name,
                account_id=key[0],
                sku=sku,
                day=r["date"],
                spend=r["spend"] or Decimal("0"),
                impressions=int(r["impressions"] or 0),
                clicks=int(r["clicks"] or 0),
            )
        )
    return out


def campaign_spend(d_from: date, d_to: date) -> list[CampaignDay]:
    return _facebook_campaign_rows(d_from, d_to) + _google_campaign_rows(d_from, d_to)


@dataclass(frozen=True)
class CampaignRef:
    account_id: str  # id ngoài của TKQC chứa chiến dịch
    name: str
    sku: str | None


def facebook_campaign_directory(campaign_ids: set[str]) -> dict[str, CampaignRef]:
    """TKQC/tên/SKU của chiến dịch Facebook theo ``fb_campaign_id`` — để dựng dòng
    cho chiến dịch có đơn nhưng không tiêu tiền trong ngày. Thiếu = chưa sync."""
    if not campaign_ids:
        return {}
    rows = FacebookAdCampaign.objects.filter(fb_campaign_id__in=campaign_ids).values_list(
        "fb_campaign_id", "ad_account__account_id", "name", "product__sku"
    )
    return {cid: CampaignRef(account_id, name or "", sku) for cid, account_id, name, sku in rows}


def _facebook_rows(d_from: date, d_to: date) -> tuple[list[AccountDay], datetime | None]:
    spend_qs = FacebookAdAccountDailySpend.objects.filter(date__range=(d_from, d_to))
    per_day = (
        spend_qs.values("ad_account__account_id", "date")
        .annotate(spend=Max("spend"), impressions=Max("impressions"), clicks=Max("clicks"))
        .order_by()
    )
    external_ids = {r["ad_account__account_id"] for r in per_day}
    names: dict[str, str] = {}
    pk_to_external: dict[int, str] = {}
    for pk, ext, name in (
        FacebookAdAccount.objects.filter(account_id__in=external_ids)
        .order_by("id")
        .values_list("id", "account_id", "name")
    ):
        names.setdefault(ext, name)
        pk_to_external[pk] = ext
    channels = _channels_by_external(ChannelAdAccount.Provider.FACEBOOK, pk_to_external)
    rows = [
        AccountDay(
            platform=FACEBOOK,
            account_id=r["ad_account__account_id"],
            name=names.get(r["ad_account__account_id"], ""),
            channels=channels.get(r["ad_account__account_id"], ()),
            day=r["date"],
            spend=r["spend"] or Decimal("0"),
            impressions=int(r["impressions"] or 0),
            clicks=int(r["clicks"] or 0),
        )
        for r in per_day
    ]
    return rows, spend_qs.aggregate(m=Max("last_synced_at"))["m"]


def _google_rows(d_from: date, d_to: date) -> tuple[list[AccountDay], datetime | None]:
    spend_qs = GoogleAdsAccountDailySpend.objects.filter(date__range=(d_from, d_to))
    per_day = (
        spend_qs.values("ad_account__customer_id", "date")
        .annotate(spend=Max("spend"), impressions=Max("impressions"), clicks=Max("clicks"))
        .order_by()
    )
    external_ids = {r["ad_account__customer_id"] for r in per_day}
    names: dict[str, str] = {}
    pk_to_external: dict[int, str] = {}
    for pk, ext, name in (
        GoogleAdsAccount.objects.filter(customer_id__in=external_ids)
        .order_by("id")
        .values_list("id", "customer_id", "name")
    ):
        names.setdefault(ext, name)
        pk_to_external[pk] = ext
    channels = _channels_by_external(ChannelAdAccount.Provider.GOOGLE, pk_to_external)
    rows = [
        AccountDay(
            platform=GOOGLE,
            account_id=r["ad_account__customer_id"],
            name=names.get(r["ad_account__customer_id"], ""),
            channels=channels.get(r["ad_account__customer_id"], ()),
            day=r["date"],
            spend=r["spend"] or Decimal("0"),
            impressions=int(r["impressions"] or 0),
            clicks=int(r["clicks"] or 0),
        )
        for r in per_day
    ]
    return rows, spend_qs.aggregate(m=Max("last_synced_at"))["m"]


def _channels_by_external(provider: str, pk_to_external: dict[int, str]) -> dict[str, tuple[str, ...]]:
    """Mã kênh theo id ngoài — hợp của mọi bản sao, giữ thứ tự, không lặp."""
    out: dict[str, list[str]] = {}
    links = (
        ChannelAdAccount.objects.filter(
            provider=provider, account_id__in=pk_to_external.keys(), channel__deleted_at__isnull=True
        )
        .order_by("channel__code")
        .values_list("account_id", "channel__code")
    )
    for pk, code in links:
        ext = pk_to_external[pk]
        bucket = out.setdefault(ext, [])
        if code not in bucket:
            bucket.append(code)
    return {ext: tuple(codes) for ext, codes in out.items()}


def account_spend(d_from: date, d_to: date) -> AccountSpend:
    fb_rows, fb_synced = _facebook_rows(d_from, d_to)
    gg_rows, gg_synced = _google_rows(d_from, d_to)
    return AccountSpend(rows=fb_rows + gg_rows, synced_at={FACEBOOK: fb_synced, GOOGLE: gg_synced})


@dataclass(frozen=True)
class AccountRef:
    platform: str
    account_id: str
    name: str
    channels: tuple[str, ...]


def account_directory() -> list[AccountRef]:
    """Mọi TKQC hiện có (không theo kỳ) kèm mã kênh đã map — bản đồ TKQC → Digital."""
    out: list[AccountRef] = []
    sources = (
        (FACEBOOK, ChannelAdAccount.Provider.FACEBOOK, FacebookAdAccount.objects.all(), "account_id"),
        (GOOGLE, ChannelAdAccount.Provider.GOOGLE, GoogleAdsAccount.objects.all(), "customer_id"),
    )
    for platform, provider, qs, external_field in sources:
        names: dict[str, str] = {}
        pk_to_external: dict[int, str] = {}
        for pk, ext, name in qs.order_by("id").values_list("id", external_field, "name"):
            names.setdefault(ext, name or "")
            pk_to_external[pk] = ext
        channels = _channels_by_external(provider, pk_to_external)
        out += [AccountRef(platform, ext, name, channels.get(ext, ())) for ext, name in names.items()]
    return sorted(out, key=lambda a: (a.platform, a.account_id))
