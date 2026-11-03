"""
Push KiotViet daily revenue **aggregates** to a Lark Bitable.

For each date we sum every channel + branch row in ``kiotviet_daily_revenues``
and emit a single Bitable record (one row per date). The local
``KiotVietRevenueLarkRecord`` table stores the ``date -> lark_record_id``
mapping so subsequent runs PUT the same record instead of inserting duplicates.

Field names below MUST match the columns in your Lark Bitable. Adjust
``LARK_FIELD_MAP`` if your table uses different labels.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, UTC
from collections.abc import Iterable

from django.db import transaction
from django.db.models import Sum

from apps.kiotviet.infrastructure.services.lark_bitable_client import (
    batch_create_records,
    batch_update_records,
)
from apps.kiotviet.models import KiotVietDailyRevenue, KiotVietRevenueLarkRecord
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)


# Local key -> Lark Bitable column name. Adjust right-hand side if the columns
# in your Bitable use different labels.
LARK_FIELD_MAP: dict[str, str] = {
    "date": "Date",
    "total_amount": "Total Amount",
    "discount": "Discount",
    "revenue": "Revenue",
    "total_cost": "Total Cost",
    "gross_profit": "Gross Profit",
}

_NUMERIC_FIELDS = ("total_amount", "discount", "revenue", "total_cost", "gross_profit")


def _date_to_lark_ms(d: date) -> int:
    """Lark Date field expects ms-since-epoch. Noon UTC keeps the date stable across TZs."""
    return int(datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=UTC).timestamp() * 1000)


def _aggregate_to_fields(agg: dict) -> dict:
    return {
        LARK_FIELD_MAP["date"]: _date_to_lark_ms(agg["date"]),
        LARK_FIELD_MAP["total_amount"]: float(agg["total_amount"] or 0),
        LARK_FIELD_MAP["discount"]: float(agg["discount"] or 0),
        LARK_FIELD_MAP["revenue"]: float(agg["revenue"] or 0),
        LARK_FIELD_MAP["total_cost"]: float(agg["total_cost"] or 0),
        LARK_FIELD_MAP["gross_profit"]: float(agg["gross_profit"] or 0),
    }


def _bitable_config() -> tuple[str, str] | None:
    app_token = sysconfig.get("LARK_BITABLE_APP_TOKEN")
    table_id = sysconfig.get("LARK_BITABLE_REVENUE_TABLE_ID")
    if not app_token or not table_id:
        return None
    return app_token, table_id


def _fetch_aggregates_for_dates(dates: Iterable[date]) -> list[dict]:
    """Sum every channel + branch row in ``kiotviet_daily_revenues`` for the given dates."""
    return list(
        KiotVietDailyRevenue.objects.filter(date__in=list(dates))
        .values("date")
        .annotate(
            total_amount=Sum("total_amount"),
            discount=Sum("discount"),
            revenue=Sum("revenue"),
            total_cost=Sum("total_cost"),
            gross_profit=Sum("gross_profit"),
        )
        .order_by("date")
    )


def push_daily_aggregates_to_lark(dates: Iterable[date]) -> dict:
    """Aggregate the given dates across all channels and push one record per date.

    Returns a summary ``{"created": N, "updated": M, "skipped": K}``.
    """
    date_list = sorted({d for d in dates if d is not None})
    if not date_list:
        return {"created": 0, "updated": 0, "skipped": 0, "reason": "no_dates"}

    config = _bitable_config()
    if config is None:
        logger.info("Lark Bitable not configured; skipping push.")
        return {"created": 0, "updated": 0, "skipped": len(date_list), "reason": "not_configured"}
    app_token, table_id = config

    aggregates = _fetch_aggregates_for_dates(date_list)
    if not aggregates:
        return {"created": 0, "updated": 0, "skipped": 0, "reason": "no_rows"}

    existing = {
        m.date: m
        for m in KiotVietRevenueLarkRecord.objects.filter(date__in=date_list)
    }

    to_create: list[tuple[dict, KiotVietRevenueLarkRecord | None]] = []
    to_update: list[tuple[dict, KiotVietRevenueLarkRecord]] = []
    for agg in aggregates:
        mapping = existing.get(agg["date"])
        if mapping and mapping.lark_record_id:
            to_update.append((agg, mapping))
        else:
            to_create.append((agg, mapping))

    created_count = 0
    updated_count = 0

    if to_create:
        payload = [{"fields": _aggregate_to_fields(agg)} for agg, _ in to_create]
        created_records = batch_create_records(app_token, table_id, payload)
        with transaction.atomic():
            for (agg, mapping), created_record in zip(to_create, created_records):
                rec_id = created_record.get("record_id")
                if not rec_id:
                    continue
                if mapping is None:
                    KiotVietRevenueLarkRecord.objects.update_or_create(
                        date=agg["date"],
                        defaults={"lark_record_id": rec_id},
                    )
                else:
                    mapping.lark_record_id = rec_id
                    mapping.save(update_fields=["lark_record_id"])
                created_count += 1

    if to_update:
        payload = [
            {"record_id": mapping.lark_record_id, "fields": _aggregate_to_fields(agg)}
            for agg, mapping in to_update
        ]
        updated_records = batch_update_records(app_token, table_id, payload)
        updated_count = len(updated_records)

    return {"created": created_count, "updated": updated_count, "skipped": 0}
