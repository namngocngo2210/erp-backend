"""GET /ads — từng dòng chi tiêu quảng cáo: ngày × nền tảng × TKQC × chiến dịch, kèm dòng dư.

Grain tài khoản (``ads_spend.account_spend``, Max bản sao) là nguồn tổng; grain chiến
dịch (``ads_spend.campaign_spend``) chia nhỏ bên trong. Mỗi (ngày × nền tảng × TKQC)
có đúng MỘT dòng dư = TKQC − Σ chiến dịch, có thể âm (sync lệch nhịp, ad chưa có chiến
dịch) — nói ra ở ``is_remainder`` thay vì biến mất. Hệ số chi phí chọn theo ``from``
của kỳ, cùng luật với Báo cáo Ads.

Đơn/lead quy về chiến dịch (``leads``/``closed``/``orders``/``revenue``) đọc mart
``ai.don_hang`` theo ``chien_dich_fb_id`` × ngày TẠO — cùng định nghĩa với
/leads/stats và /orders/stats. Chiến dịch có đơn nhưng không tiêu tiền trong ngày
vẫn được một dòng (spend 0) để tổng lead/đơn không mất; dòng dư để null.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from apps.daily_report.application.stats_service import page_envelope
from apps.daily_report.domain import paging
from apps.daily_report.domain.periods import TZ, ParamError, iso
from apps.daily_report.infrastructure import ads_spend, queries
from apps.daily_report.infrastructure.freshness import read_freshness
from apps.orders.reporting.shared import GOOGLE_ADS_DAILY_COST_MULTIPLIER, facebook_ads_cost_multiplier

CURSOR_SEP = "|"
_BAD_CURSOR = "Tham số `cursor` không hợp lệ — dùng `next_cursor` của trang trước."

RowKey = tuple[str, str, str, str]  # (day ISO, platform, account_id, campaign_id hoặc "")


def _vnd(x: Decimal) -> int:
    return int(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _multipliers(d_from: date) -> dict[str, Decimal]:
    return {
        ads_spend.FACEBOOK: facebook_ads_cost_multiplier(d_from),
        ads_spend.GOOGLE: GOOGLE_ADS_DAILY_COST_MULTIPLIER,
    }


def _key(day: date, platform: str, account_id: str, campaign_id: str | None) -> RowKey:
    return (iso(day), platform, account_id, campaign_id or "")


def parse_cursor(raw: str | None) -> RowKey | None:
    if not raw:
        return None
    parts = raw.split(CURSOR_SEP)
    if len(parts) != 4:
        raise ParamError(_BAD_CURSOR)
    return (parts[0], parts[1], parts[2], parts[3])


_NO_ATTRIBUTION = {"leads": None, "closed": None, "orders": None, "revenue": None}
_ZERO_ATTRIBUTION = {"leads": 0, "closed": 0, "orders": 0, "revenue": 0}


def _row(
    key: RowKey, ref: ads_spend.AccountRef | None, *, campaign_name: str | None, sku: str | None,
    is_remainder: bool, spend: Decimal, impressions: int, clicks: int, multiplier: Decimal,
    attribution: dict | None = None,
) -> dict:
    day, platform, account_id, campaign_id = key
    return {
        "day": day,
        "platform": platform,
        "account_id": account_id,
        "account_name": ref.name if ref else "",
        "channels": list(ref.channels) if ref else [],
        "campaign_id": campaign_id or None,
        "campaign_name": campaign_name,
        "sku": sku,
        "is_remainder": is_remainder,
        "spend": _vnd(spend),
        "multiplier": float(multiplier),
        "spend_adjusted": _vnd(spend * multiplier),
        "impressions": impressions,
        "clicks": clicks,
        **(_NO_ATTRIBUTION if is_remainder else (attribution or _ZERO_ATTRIBUTION)),
        "cursor": CURSOR_SEP.join(key),
    }


def ad_spend_rows(d_from: date, d_to: date) -> tuple[list[dict], dict[str, datetime | None]]:
    """Mọi dòng của kỳ, đã sắp theo key. Mỗi (ngày × nền tảng × TKQC) có đúng MỘT dòng dư
    = chi tiêu TKQC (grain tài khoản, Max bản sao) − Σ chi tiêu chiến dịch; có thể âm."""
    multipliers = _multipliers(d_from)
    refs = {(a.platform, a.account_id): a for a in ads_spend.account_directory()}
    spend = ads_spend.account_spend(d_from, d_to)
    campaigns = ads_spend.campaign_spend(d_from, d_to)
    attribution = _attribution_by_day_campaign(d_from, d_to)

    account_level: dict[tuple[str, str, str], ads_spend.AccountDay] = {
        (iso(r.day), r.platform, r.account_id): r for r in spend.rows
    }
    used: dict[tuple[str, str, str], list[Decimal | int]] = {}  # → [spend, impressions, clicks]
    rows: list[dict] = []
    for c in campaigns:
        group = (iso(c.day), c.platform, c.account_id)
        acc = used.setdefault(group, [Decimal("0"), 0, 0])
        acc[0] += c.spend
        acc[1] += c.impressions
        acc[2] += c.clicks
        rows.append(
            _row(
                _key(c.day, c.platform, c.account_id, c.campaign_id), refs.get((c.platform, c.account_id)),
                campaign_name=c.name, sku=c.sku, is_remainder=False,
                spend=c.spend, impressions=c.impressions, clicks=c.clicks, multiplier=multipliers[c.platform],
                attribution=attribution.pop((iso(c.day), c.campaign_id), None) if c.platform == ads_spend.FACEBOOK else None,
            )
        )
    # Chiến dịch có đơn nhưng không có dòng chi tiêu ngày đó (tạm dừng, sync trễ,
    # chưa sync danh mục) → dòng spend 0, không ảnh hưởng dòng dư của TKQC.
    directory = ads_spend.facebook_campaign_directory({cid for _, cid in attribution})
    for (day, campaign_id), attr in attribution.items():
        ref = directory.get(campaign_id)
        account_id = ref.account_id if ref else ""
        rows.append(
            _row(
                (day, ads_spend.FACEBOOK, account_id, campaign_id), refs.get((ads_spend.FACEBOOK, account_id)),
                campaign_name=ref.name if ref else None, sku=ref.sku if ref else None, is_remainder=False,
                spend=Decimal("0"), impressions=0, clicks=0, multiplier=multipliers[ads_spend.FACEBOOK],
                attribution=attr,
            )
        )
    for group in set(account_level) | set(used):
        day, platform, account_id = group
        a = account_level.get(group)
        u = used.get(group, [Decimal("0"), 0, 0])
        rows.append(
            _row(
                (day, platform, account_id, ""), refs.get((platform, account_id)),
                campaign_name=None, sku=None, is_remainder=True,
                spend=(a.spend if a else Decimal("0")) - u[0],
                impressions=(a.impressions if a else 0) - int(u[1]),
                clicks=(a.clicks if a else 0) - int(u[2]),
                multiplier=multipliers[platform],
            )
        )
    rows.sort(key=_row_key)
    return rows, spend.synced_at


def _attribution_by_day_campaign(d_from: date, d_to: date) -> dict[tuple[str, str], dict]:
    """(ngày ISO, campaign_id) → leads/closed/orders/revenue từ mart. Chỉ Facebook:
    utm_id trên URL landing là campaign.id của Meta (Google chưa gắn tag)."""
    return {
        (iso(r["ngay"]), r["chien_dich_fb_id"]): {
            "leads": r["leads"], "closed": r["closed"], "orders": r["orders"], "revenue": _vnd(Decimal(r["revenue"])),
        }
        for r in queries.campaign_attribution_rows(d_from, d_to)
    }


def _row_key(r: dict) -> RowKey:
    return (r["day"], r["platform"], r["account_id"], r["campaign_id"] or "")


def ads_payload(d_from: date, d_to: date, *, after: RowKey | None, limit: int) -> dict:
    fresh = read_freshness()
    all_rows, synced_at = ad_spend_rows(d_from, d_to)
    if after is not None:
        all_rows = [r for r in all_rows if _row_key(r) > after]
    page, has_more = paging.slice_page(all_rows[: limit + 1], limit)
    body = page_envelope(
        page, d_from=d_from, d_to=d_to, fresh=fresh, limit=limit,
        next_cursor=page[-1]["cursor"] if has_more else None,
    )
    body["meta"]["spend_as_of"] = {k: (v.astimezone(TZ).isoformat() if v else None) for k, v in synced_at.items()}
    body["meta"]["attribution"] = queries.attribution_coverage(d_from, d_to)
    return body
