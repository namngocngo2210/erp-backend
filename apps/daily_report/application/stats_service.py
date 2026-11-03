"""Payload từng endpoint: envelope + cache + định dạng. Không có SQL ở đây."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from django.core.cache import cache
from django.utils import timezone

from apps.daily_report.application import rows
from apps.daily_report.domain import definitions as d
from apps.daily_report.domain import paging
from apps.daily_report.domain.periods import TZ, iso
from apps.daily_report.infrastructure import ads_spend, queries
from apps.daily_report.infrastructure.freshness import Freshness, read_freshness

DEFINITIONS_URL = "/api/daily-report/definitions"


def envelope(data, *, d_from: date, d_to: date, fresh: Freshness) -> dict:
    return {
        "meta": {
            "timezone": str(TZ.key),
            "range": {"from": iso(d_from), "to": iso(d_to)},
            "data_as_of": fresh.data_as_of.astimezone(TZ).isoformat() if fresh.data_as_of else None,
            "data_status": fresh.status,
            "definitions_url": DEFINITIONS_URL,
            "generated_at": timezone.now().astimezone(TZ).isoformat(),
        },
        "data": data,
    }


def page_envelope(items: list[dict], *, d_from: date, d_to: date, fresh: Freshness, limit: int, next_cursor) -> dict:
    body = envelope(items, d_from=d_from, d_to=d_to, fresh=fresh)
    body["limit"] = limit
    body["next_cursor"] = next_cursor
    return body


def _page(fetch: Callable[[int], list[dict]], mapper: Callable[[dict], dict], limit: int) -> tuple[list[dict], object]:
    """Fetch limit+1 → biết còn trang sau mà không count(*) toàn tập."""
    raw, has_more = paging.slice_page(fetch(limit + 1), limit)
    items = [mapper(r) for r in raw]
    return items, (items[-1]["cursor"] if has_more else None)


def leads_payload(d_from: date, d_to: date, *, after: int | None, limit: int) -> dict:
    fresh = read_freshness()
    items, nxt = _page(
        lambda n: queries.order_rows(d_from, d_to, invoiced_only=False, after=after, limit=n), rows.order_row, limit
    )
    return page_envelope(items, d_from=d_from, d_to=d_to, fresh=fresh, limit=limit, next_cursor=nxt)


def orders_payload(d_from: date, d_to: date, *, after: int | None, limit: int) -> dict:
    fresh = read_freshness()
    items, nxt = _page(
        lambda n: queries.order_rows(d_from, d_to, invoiced_only=True, after=after, limit=n), rows.order_row, limit
    )
    return page_envelope(items, d_from=d_from, d_to=d_to, fresh=fresh, limit=limit, next_cursor=nxt)


def revenue_payload(d_from: date, d_to: date, *, after: int | None, limit: int) -> dict:
    fresh = read_freshness()
    items, nxt = _page(
        lambda n: queries.order_line_rows(d_from, d_to, after=after, limit=n), rows.order_line_row, limit
    )
    return page_envelope(items, d_from=d_from, d_to=d_to, fresh=fresh, limit=limit, next_cursor=nxt)


DEFAULT_LOOKBACK_DAYS = 30


def pipeline_payload(as_of: date, lookback_days: int, *, after: int | None, limit: int) -> dict:
    fresh = read_freshness()
    items, nxt = _page(
        lambda n: queries.open_lead_rows(as_of, lookback_days, after=after, limit=n), rows.open_lead_row, limit
    )
    return page_envelope(
        items, d_from=as_of - timedelta(days=lookback_days), d_to=as_of, fresh=fresh, limit=limit, next_cursor=nxt
    )


def definitions() -> dict:
    groups: dict[str, list[dict]] = {g: [] for g in queries.DICTIONARY_GROUPS}
    for r in queries.dictionary_rows():
        groups[r["nhom_ma"]].append({"code": r["ma"], "label": r["nhan"]})
    return {
        "order_status": [
            {"code": int(x["code"]), "label": x["label"], "bucket": d.order_bucket_of(int(x["code"]))}
            for x in groups["trang_thai_don"]
        ],
        "invoice_status": [{"code": int(x["code"]), "label": x["label"]} for x in groups["trang_thai_hoa_don"]],
        "sales_status": [{**x, "bucket": d.bucket_of(x["code"])} for x in groups["trang_thai_sale"]],
        "order_source": groups["nguon_don"],
        "channel_group": list(d.CHANNEL_GROUPS),
        "order_buckets": d.ORDER_BUCKET_LABELS,
        "lead_buckets": list(d.LEAD_BUCKETS) + ["other"],
        "excluded_branches": list(d.EXCLUDED_BRANCHES),
        "metrics": d.METRIC_GLOSSARY,
        "ad_account_channels": [
            {"platform": a.platform, "account_id": a.account_id, "name": a.name, "channels": list(a.channels)}
            for a in ads_spend.account_directory()
        ],
        "cost_multiplier": d.cost_multiplier_rules(),
    }


def definitions_payload() -> dict:
    fresh = read_freshness()
    today = timezone.now().astimezone(TZ).date()
    data = cache.get_or_set("daily_report:definitions", definitions, 60 * 60)
    return envelope(data, d_from=today, d_to=today, fresh=fresh)
