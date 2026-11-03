"""Báo cáo hiệu quả marketing: ghép chi phí quảng cáo với kết quả bên Foxia.

Cầu nối là `FoxiaChannelMap`: nguồn/kênh Foxia → Channel nội bộ → tài khoản
quảng cáo (`ChannelAdAccount`) → chi tiêu ngày. Nguồn chưa ánh xạ vẫn hiện đủ số
đơn, chỉ không có chi phí, và được gom vào nhóm **"Chưa ánh xạ"** kèm số lượng —
giới hạn bị nuốt lặng thì đọc như "đã phủ hết".

Mọi chỉ tiêu dựa trên MỐC THỜI GIAN (`ngayxacnhan`, `ngayhoanthanh`), không dựa
trên mã trạng thái — nên chạy được ngay khi chưa có bảng ánh xạ trạng thái.

Ghi chú số liệu phải nói rõ trên giao diện: `amount_collected` là tiền đã thu
TẠI THỜI ĐIỂM ĐỒNG BỘ, chưa trừ khấu trừ/tiền hoàn/phí ship thực tế — đối soát
kế toán ngoài phạm vi giai đoạn này.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Count, Q, Sum

from apps.channels_core.models import ChannelAdAccount
from apps.foxia.models import (
    FoxiaChannel,
    FoxiaChannelMap,
    FoxiaChannelMapKind,
    FoxiaOrderLink,
    FoxiaSource,
)

UNMAPPED_KEY = "unmapped"
UNMAPPED_LABEL = "Chưa ánh xạ"


@dataclass(frozen=True)
class ReportRow:
    key: str
    label: str
    orders: int = 0
    delivered: int = 0
    returned: int = 0
    revenue: Decimal = Decimal("0")
    collected: Decimal = Decimal("0")
    ship_fee: Decimal = Decimal("0")
    spend: Decimal = Decimal("0")
    mapped: bool = True

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "mapped": self.mapped,
            "orders": self.orders,
            "delivered": self.delivered,
            "returned": self.returned,
            "revenue": float(self.revenue),
            "collected": float(self.collected),
            "ship_fee": float(self.ship_fee),
            "spend": float(self.spend),
            "cpo": _ratio(self.spend, self.orders),
            "cps": _ratio(self.spend, self.delivered),
            "delivery_rate": _pct(self.delivered, self.orders),
            "return_rate": _pct(self.returned, self.delivered),
            "roas": _div(self.collected, self.spend),
        }


def compute(date_from: date, date_to: date, group_by: str = "source") -> dict:
    """Số liệu hiệu quả marketing trong khoảng ngày, lát theo `group_by`.

    `group_by`: source | channel | marketer | branch | brand.
    """
    field = _GROUP_FIELDS.get(group_by)
    if field is None:
        raise ValueError(f"group_by không hợp lệ: {group_by}")

    links = FoxiaOrderLink.objects.filter(
        ngayxacnhan__date__gte=date_from, ngayxacnhan__date__lte=date_to
    )
    aggregated = (
        links.values(f"{field}_id", f"{field}__name")
        .annotate(
            orders=Count("id"),
            # Giao thành công nhận biết bằng MỐC THỜI GIAN, không cần mã trạng thái.
            delivered=Count("id", filter=Q(ngayhoanthanh__isnull=False)),
            returned=Count("id", filter=~Q(return_status="")),
            revenue=Sum("amount_after_extra_discount"),
            collected=Sum("amount_collected"),
            ship_fee=Sum("order__shipping_fee"),
        )
        .order_by()
    )

    spend_by_group = _spend_by_group(field, date_from, date_to) if group_by in _SPEND_GROUPS else {}

    rows: list[ReportRow] = []
    for item in aggregated:
        group_id = item[f"{field}_id"]
        mapped = group_id is not None
        rows.append(
            ReportRow(
                key=str(group_id) if mapped else UNMAPPED_KEY,
                label=item[f"{field}__name"] or UNMAPPED_LABEL,
                mapped=mapped,
                orders=item["orders"],
                delivered=item["delivered"],
                returned=item["returned"],
                revenue=item["revenue"] or Decimal("0"),
                collected=item["collected"] or Decimal("0"),
                ship_fee=item["ship_fee"] or Decimal("0"),
                spend=spend_by_group.get(group_id, Decimal("0")),
            )
        )

    rows = _merge_unmapped(rows)
    rows.sort(key=lambda r: (r.key == UNMAPPED_KEY, -r.orders))
    totals = _totals(rows)
    return {
        "group_by": group_by,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "rows": [row.as_dict() for row in rows],
        "totals": totals.as_dict(),
        # Đếm tường minh phần chưa ghép được chi phí, để không ai đọc nhầm bảng
        # này là "đã phủ hết".
        "unmapped_orders": sum(row.orders for row in rows if not row.mapped),
    }


# ── chi phí quảng cáo ────────────────────────────────────────────────────────


def _spend_by_group(field: str, date_from: date, date_to: date) -> dict:
    """Tổng chi tiêu Ads quy về từng nguồn/kênh Foxia.

    Đường đi: nguồn Foxia → Channel nội bộ (FoxiaChannelMap) → tài khoản quảng
    cáo (ChannelAdAccount) → chi tiêu ngày. Một tài khoản gắn nhiều kênh thì chi
    tiêu bị đếm cho từng kênh — đó là hạn chế đã biết của mô hình mapping hiện
    tại, không phải lỗi ở đây; báo cáo Ads nội bộ cũng hành xử như vậy.
    """
    kind = (
        FoxiaChannelMapKind.SOURCE if field == "source" else FoxiaChannelMapKind.CHANNEL
    )
    maps = list(FoxiaChannelMap.objects.filter(kind=kind).values_list("foxia_id", "channel_id"))
    if not maps:
        return {}

    channel_ids = {channel_id for _, channel_id in maps}
    account_links = ChannelAdAccount.objects.filter(channel_id__in=channel_ids)
    fb_accounts: dict[int, set] = {}
    google_accounts: dict[int, set] = {}
    for link in account_links:
        bucket = (
            fb_accounts
            if link.provider == ChannelAdAccount.Provider.FACEBOOK
            else google_accounts
        )
        bucket.setdefault(link.channel_id, set()).add(link.account_id)

    fb_spend = _fb_spend(fb_accounts, date_from, date_to)
    google_spend = _google_spend(google_accounts, date_from, date_to)

    by_foxia_id: dict[int, Decimal] = {}
    for foxia_id, channel_id in maps:
        by_foxia_id[foxia_id] = by_foxia_id.get(foxia_id, Decimal("0")) + fb_spend.get(
            channel_id, Decimal("0")
        ) + google_spend.get(channel_id, Decimal("0"))

    # Đổi khoá sang pk nội bộ: `compute` gom nhóm bằng FK (`source_id`), còn bảng
    # ánh xạ khoá theo id BÊN FOXIA. Hai không gian id khác nhau — trả nhầm khoá
    # thì mọi con số chi phí ra 0 mà báo cáo vẫn hiển thị bình thường.
    model = FoxiaSource if field == "source" else FoxiaChannel
    pk_by_foxia_id = dict(
        model.objects.filter(foxia_id__in=by_foxia_id).values_list("foxia_id", "pk")
    )
    return {
        pk_by_foxia_id[foxia_id]: amount
        for foxia_id, amount in by_foxia_id.items()
        if foxia_id in pk_by_foxia_id
    }


def _fb_spend(accounts_by_channel: dict, date_from: date, date_to: date) -> dict:
    if not accounts_by_channel:
        return {}
    from apps.facebook_ads.models import FacebookAdAccountDailySpend

    every_id = {acc for ids in accounts_by_channel.values() for acc in ids}
    totals = dict(
        FacebookAdAccountDailySpend.objects.filter(
            ad_account_id__in=every_id, date__gte=date_from, date__lte=date_to
        )
        .values_list("ad_account_id")
        .annotate(total=Sum("spend"))
    )
    return {
        channel_id: sum((totals.get(acc, Decimal("0")) for acc in ids), Decimal("0"))
        for channel_id, ids in accounts_by_channel.items()
    }


def _google_spend(accounts_by_channel: dict, date_from: date, date_to: date) -> dict:
    if not accounts_by_channel:
        return {}
    from apps.google_ads.models import GoogleAdsAccountDailySpend

    every_id = {acc for ids in accounts_by_channel.values() for acc in ids}
    totals = dict(
        GoogleAdsAccountDailySpend.objects.filter(
            ad_account_id__in=every_id, date__gte=date_from, date__lte=date_to
        )
        .values_list("ad_account_id")
        .annotate(total=Sum("spend"))
    )
    return {
        channel_id: sum((totals.get(acc, Decimal("0")) for acc in ids), Decimal("0"))
        for channel_id, ids in accounts_by_channel.items()
    }


# ── tiện ích ─────────────────────────────────────────────────────────────────

_GROUP_FIELDS = {
    "source": "source",
    "channel": "channel",
    "marketer": "marketer",
    "branch": "branch",
    "brand": "brand",
}
# Chi phí chỉ quy được về nguồn/kênh — tiền quảng cáo gắn với tài khoản Ads, không
# gắn với cá nhân hay chi nhánh.
_SPEND_GROUPS = {"source", "channel"}


def _merge_unmapped(rows: list[ReportRow]) -> list[ReportRow]:
    """Gộp mọi dòng thiếu ánh xạ thành một dòng 'Chưa ánh xạ' duy nhất."""
    mapped = [row for row in rows if row.mapped]
    unmapped = [row for row in rows if not row.mapped]
    if not unmapped:
        return mapped
    return [*mapped, _sum_rows(unmapped, UNMAPPED_KEY, UNMAPPED_LABEL, mapped=False)]


def _totals(rows: list[ReportRow]) -> ReportRow:
    return _sum_rows(rows, "total", "Tổng")


def _sum_rows(rows, key: str, label: str, mapped: bool = True) -> ReportRow:
    return ReportRow(
        key=key,
        label=label,
        mapped=mapped,
        orders=sum(r.orders for r in rows),
        delivered=sum(r.delivered for r in rows),
        returned=sum(r.returned for r in rows),
        revenue=sum((r.revenue for r in rows), Decimal("0")),
        collected=sum((r.collected for r in rows), Decimal("0")),
        ship_fee=sum((r.ship_fee for r in rows), Decimal("0")),
        spend=sum((r.spend for r in rows), Decimal("0")),
    )


def _ratio(spend: Decimal, count: int) -> float | None:
    """Chi phí trên một đơn vị. None khi mẫu số 0 — KHÔNG trả 0.

    0 đọc như "miễn phí", còn None buộc giao diện hiển thị dấu gạch. Đây là khác
    biệt thật với người đọc báo cáo.
    """
    return float(spend / count) if count else None


def _div(numerator: Decimal, denominator: Decimal) -> float | None:
    return float(numerator / denominator) if denominator else None


def _pct(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 2) if whole else None
