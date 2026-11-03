"""Ghép SQL + luật domain → payload feed conversion kênh Google Ads Web."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from apps.conversion_feed.domain import lead
from apps.conversion_feed.infrastructure import queries
from apps.orders.crm_catalog import CLOSED_SALES_STATUS_CODES, SALES_STATUS_LABELS
from apps.orders.vietnam_regions import canonical_province, province_from_location


def _province(row: dict) -> str:
    raw = (row["province"] or "").strip() or (row["province_level2"] or "").strip()
    if raw:
        return canonical_province(raw)
    return province_from_location(row["kiotviet_location"] or "")


def to_item(row: dict, *, tz: ZoneInfo) -> dict:
    first_name, last_name = lead.split_name(row["name"])
    province = _province(row)
    order_id = str(row["id"])
    status_code = row["sales_status_code"]
    return {
        "time": row["created_at"].astimezone(tz).isoformat(),
        "first_name": first_name,
        "last_name": last_name,
        "phone": (row["phone_normalized"] or row["phone"] or "").strip(),
        "province": province,
        "district": (row["district"] or "").strip(),
        "sku": lead.join_skus(row["item_skus"], row["sku"]),
        "value": lead.to_vnd(row["total_after_discount"]),
        "currency": lead.CURRENCY,
        "status": SALES_STATUS_LABELS.get(status_code, status_code),
        "status_code": status_code,
        "source": lead.map_source(row["order_source"]),
        "zip": lead.postal_code(province),
        "event_id": order_id,
        "order_id": order_id,
        **dict.fromkeys(lead.TRACKING_FIELDS, ""),
    }


def feed_payload(
    *, channel_id: int, start: datetime, end: datetime, after: int | None, limit: int, tz: ZoneInfo
) -> dict:
    status_codes = list(CLOSED_SALES_STATUS_CODES)
    rows = queries.closed_lead_rows(
        channel_id=channel_id,
        start=start,
        end=end,
        status_codes=status_codes,
        after=after,
        limit=limit + 1,
    )
    page, has_more = rows[:limit], len(rows) > limit
    return {
        "items": [to_item(row, tz=tz) for row in page],
        "next_cursor": page[-1]["id"] if has_more else None,
        "limit": limit,
        "meta": {
            "channel_id": channel_id,
            "from": start.astimezone(tz).isoformat(),
            "to": end.astimezone(tz).isoformat(),
            "status_codes": status_codes,
        },
    }
