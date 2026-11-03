"""
KiotViet Web reporting client for the daily revenue report
(`Sale.BigSaleProfitByInvoice`).

Flow (per branch):
  1. POST /reportapi/report/reports/clients               -> clientId
  2. POST /reportapi/report/reports/clients/{cid}/instances
                                                          -> instanceId
  3. POST /reportapi/report/reports/clients/{cid}/instances/{iid}/documents
                                                          -> documentId
  4. GET  /reportapi/report/reports/clients/{cid}/instances/{iid}/documents/{did}/info
                                                          -> pageCount, ready
  5. GET  /reportapi/report/reports/clients/{cid}/instances/{iid}/documents/{did}/pages/{n}
                                                          -> pageContent HTML

The HTML page lays out values as absolutely positioned <div>s.
We map each cell by its `left:` offset to a column.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, UTC
from decimal import Decimal, InvalidOperation
from collections.abc import Iterable

import logging
import re
import time

from django.db import transaction
from django.utils import timezone

from apps.common.dates import parse_ddmmyyyy

from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import (
    _kiotviet_web_request,
)
from apps.kiotviet.infrastructure.services.lark_revenue_sync import (
    push_daily_aggregates_to_lark,
)
from apps.kiotviet.models import KiotVietChannel, KiotVietDailyRevenue


logger = logging.getLogger(__name__)


REPORT_NAME = "Sale.BigSaleProfitByInvoice"


# Fixed (channel_id, branch_id) pairs to sync daily revenue from.
#   HKD: channel 6 - branch 89084
#   New: channel 4 - branch 231174
#   Old: channel 5 - branch 30965
SYNC_TARGETS: tuple[tuple[int, str], ...] = (
    (6, "89084"),
    (4, "231174"),
    (5, "30965"),
)

# Map column `left:NNNpx` -> field name. Stable because the report layout
# is fixed by KiotViet. Verified against a live response (see CLAUDE prompt
# in PR description).
LEFT_COLUMN_MAP: dict[int, str] = {
    10: "date",            # textBox17 - Thời gian
    278: "total_amount",   # textBox16 - Tổng tiền hàng
    419: "discount",       # textBox15 - Giảm giá
    552: "revenue",        # textBox13 - Doanh thu
    705: "total_cost",     # textBox12 - Tổng giá vốn
    863: "gross_profit",   # textBox11 - Lợi nhuận gộp
}

# A row cell in the report:
#   <div title="" class="textBoxNN s..." style="position:absolute;overflow:hidden;left:NNNpx;top:NNNpx;width:..px;height:..px;" [data-reporting-action="..."]>
#       <div style="position:absolute;top:NNpx;white-space:pre;...">VALUE</div>
#   ...
_ROW_CELL_RE = re.compile(
    r'<div title=""\s+class="textBox\d+[^"]*"\s+'
    r'style="position:absolute;overflow:hidden;left:(\d+)px;top:(\d+)px[^"]*"'
    r'(?:\s+data-reporting-action="[^"]*")?\s*>\s*'
    r'<div\s+style="position:absolute;top:\d+px;white-space:pre;[^"]*">'
    r'([^<]+)</div>',
    re.DOTALL,
)

# Single-day reports (today/yesterday) break down by hour: the "Thời gian" cell
# is ``HH:MM`` instead of ``dd/mm/yyyy``, so we read the date from the header
# text ``Từ ngày DD/MM/YYYY đến ngày DD/MM/YYYY`` and sum the hourly rows.
_REPORT_DATE_RE = re.compile(
    r"Từ ngày\s+(\d{2}/\d{2}/\d{4})\s+đến ngày\s+(\d{2}/\d{2}/\d{4})"
)
_HOUR_RE = re.compile(r"^\d{1,2}:\d{2}$")

_NUMERIC_FIELDS: tuple[str, ...] = (
    "total_amount",
    "discount",
    "revenue",
    "total_cost",
    "gross_profit",
)


def _zero_aggregate() -> dict[str, Decimal]:
    return {k: Decimal(0) for k in _NUMERIC_FIELDS}


# Time range presets that KiotViet web reports accept. ``TimeRange`` is the
# machine value; ``dateLable`` is the human label shown to the user. KiotViet
# echoes both back even though only ``TimeRange`` drives the filter.
TIME_RANGE_PRESETS: dict[str, tuple[str, str]] = {
    "today": ("today", "Hôm nay"),
    "yesterday": ("yesterday", "Hôm qua"),
    "thisweek": ("week", "Tuần này"),
    "lastweek": ("lastweek", "Tuần trước"),
    "thismonth": ("month", "Tháng này"),
    "lastmonth": ("lastmonth", "Tháng trước"),
    "last7days": ("last7days", "7 ngày qua"),
    "last30days": ("last30days", "30 ngày qua"),
    "thisyear": ("year", "Năm này"),
    "lastyear": ("lastyear", "Năm trước"),
}


class RevenueReportError(Exception):
    """Raised when the reporting flow fails for a branch."""


@dataclass
class RevenueRow:
    date: date
    total_amount: Decimal
    discount: Decimal
    revenue: Decimal
    total_cost: Decimal
    gross_profit: Decimal


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_amount(raw: str) -> Decimal:
    """Parse KiotViet number strings like ``204,279,296`` or ``-329,629``."""
    text = (raw or "").replace(",", "").strip()
    if not text:
        return Decimal(0)
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def parse_page_content(
    page_content: str,
    report_date: date | None = None,
) -> tuple[list[RevenueRow], dict[date, dict[str, Decimal]]]:
    """Extract daily rows and per-date hourly aggregates from one page's HTML.

    Iterates every ``textBoxNN`` cell, groups by ``top`` (each detail row sits
    at a unique ``top`` offset), then maps by ``left`` -> column. Rows whose
    "Thời gian" cell is ``dd/mm/yyyy`` go straight into ``daily_rows``; rows
    whose cell is ``HH:MM`` (single-day breakdown) are summed under
    ``report_date`` so the caller can merge across pages.
    """
    cells_by_top: dict[int, dict[int, str]] = {}
    for match in _ROW_CELL_RE.finditer(page_content or ""):
        left = int(match.group(1))
        top = int(match.group(2))
        value = match.group(3).strip()
        if left not in LEFT_COLUMN_MAP:
            continue
        cells_by_top.setdefault(top, {})[left] = value

    daily_rows: list[RevenueRow] = []
    hourly_by_date: dict[date, dict[str, Decimal]] = {}
    for top in sorted(cells_by_top.keys()):
        cells = cells_by_top[top]
        time_text = (cells.get(10) or "").strip()

        parsed_date = parse_ddmmyyyy(time_text)
        if parsed_date is not None:
            daily_rows.append(
                RevenueRow(
                    date=parsed_date,
                    total_amount=_parse_amount(cells.get(278, "")),
                    discount=_parse_amount(cells.get(419, "")),
                    revenue=_parse_amount(cells.get(552, "")),
                    total_cost=_parse_amount(cells.get(705, "")),
                    gross_profit=_parse_amount(cells.get(863, "")),
                )
            )
            continue

        if report_date is not None and _HOUR_RE.match(time_text):
            agg = hourly_by_date.setdefault(report_date, _zero_aggregate())
            agg["total_amount"] += _parse_amount(cells.get(278, ""))
            agg["discount"] += _parse_amount(cells.get(419, ""))
            agg["revenue"] += _parse_amount(cells.get(552, ""))
            agg["total_cost"] += _parse_amount(cells.get(705, ""))
            agg["gross_profit"] += _parse_amount(cells.get(863, ""))
        # Otherwise skip: header row ("Thời gian"), totals row, "Từ ngày..." banner, etc.
    return daily_rows, hourly_by_date


def _extract_report_date(page_content: str) -> date | None:
    """Pull the report date from the ``Từ ngày DD/MM/YYYY đến ngày DD/MM/YYYY`` banner."""
    match = _REPORT_DATE_RE.search(page_content or "")
    if not match:
        return None
    return parse_ddmmyyyy(match.group(1))


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------


def _build_filter_payload(branch_id: str, time_range: str, date_label: str) -> str:
    """Build the JSON string the reporting API expects in ``filter``."""
    now_utc = timezone.now().astimezone(UTC)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now_utc.microsecond // 1000:03d}Z"
    filter_dict = {
        "BranchIds": [int(branch_id)],
        "TimeRange": time_range,
        "TableIds": [],
        "PriceBookIds": [],
        "FilterDataRefreshPromiseQueue": [],
        "GetTableFromAllBranches": True,
        "TimeZone": "Asia/Bangkok",
        "typeDate": "range",
        "dateLable": date_label,
        "DeliveryMethodIds": [],
        "timeStamp": now_iso,
        "WarehouseIds": None,
    }
    import json as _json
    return _json.dumps(filter_dict, ensure_ascii=False)


def _post_clients(channel: KiotVietChannel) -> str:
    payload = {"timeStamp": int(time.time() * 1000)}
    resp = _kiotviet_web_request(
        channel=channel,
        method="POST",
        endpoint="/reportapi/report/reports/clients",
        json=payload,
    )
    if not (200 <= resp.status_code < 300):
        raise RevenueReportError(
            f"clients call failed: status={resp.status_code} body={(resp.text or '')[:300]}"
        )
    client_id = (resp.json() or {}).get("clientId")
    if not client_id:
        raise RevenueReportError(f"clients response missing clientId: {resp.text[:300]}")
    return str(client_id)


def _post_instances(
    channel: KiotVietChannel,
    client_id: str,
    branch_id: str,
    time_range: str,
    date_label: str,
) -> str:
    payload = {
        "report": REPORT_NAME,
        "parameterValues": {
            "filter": _build_filter_payload(branch_id, time_range, date_label),
            "time": None,
        },
    }
    resp = _kiotviet_web_request(
        channel=channel,
        method="POST",
        endpoint=f"/reportapi/report/reports/clients/{client_id}/instances",
        json=payload,
    )
    if not (200 <= resp.status_code < 300):
        raise RevenueReportError(
            f"instances call failed: status={resp.status_code} body={(resp.text or '')[:300]}"
        )
    instance_id = (resp.json() or {}).get("instanceId")
    if not instance_id:
        raise RevenueReportError(f"instances response missing instanceId: {resp.text[:300]}")
    return str(instance_id)


def _post_documents(channel: KiotVietChannel, client_id: str, instance_id: str) -> str:
    payload = {
        "format": "HTML5Interactive",
        "deviceInfo": {
            "ContentOnly": True,
            "UseSVG": True,
            "BasePath": "https://api-man1.kiotviet.vn/reportapi/report/reports",
        },
        "useCache": True,
    }
    resp = _kiotviet_web_request(
        channel=channel,
        method="POST",
        endpoint=f"/reportapi/report/reports/clients/{client_id}/instances/{instance_id}/documents",
        json=payload,
    )
    if not (200 <= resp.status_code < 300):
        raise RevenueReportError(
            f"documents call failed: status={resp.status_code} body={(resp.text or '')[:300]}"
        )
    document_id = (resp.json() or {}).get("documentId")
    if not document_id:
        raise RevenueReportError(f"documents response missing documentId: {resp.text[:300]}")
    return str(document_id)


def _get_document_info(
    channel: KiotVietChannel,
    client_id: str,
    instance_id: str,
    document_id: str,
    max_wait_seconds: float = 30.0,
    poll_interval: float = 0.5,
) -> dict:
    """Poll the document info endpoint until the report is rendered."""
    deadline = time.monotonic() + max_wait_seconds
    while True:
        resp = _kiotviet_web_request(
            channel=channel,
            method="GET",
            endpoint=(
                f"/reportapi/report/reports/clients/{client_id}"
                f"/instances/{instance_id}/documents/{document_id}/info"
            ),
        )
        if not (200 <= resp.status_code < 300):
            raise RevenueReportError(
                f"document info failed: status={resp.status_code} body={(resp.text or '')[:300]}"
            )
        info = resp.json() or {}
        if info.get("documentReady"):
            return info
        if time.monotonic() >= deadline:
            raise RevenueReportError("document not ready within timeout")
        time.sleep(poll_interval)


def _get_page(
    channel: KiotVietChannel,
    client_id: str,
    instance_id: str,
    document_id: str,
    page_number: int,
) -> dict:
    resp = _kiotviet_web_request(
        channel=channel,
        method="GET",
        endpoint=(
            f"/reportapi/report/reports/clients/{client_id}"
            f"/instances/{instance_id}/documents/{document_id}/pages/{page_number}"
        ),
    )
    if not (200 <= resp.status_code < 300):
        raise RevenueReportError(
            f"page {page_number} failed: status={resp.status_code} body={(resp.text or '')[:300]}"
        )
    return resp.json() or {}


# ---------------------------------------------------------------------------
# Public service
# ---------------------------------------------------------------------------


def fetch_daily_revenue_rows(
    channel: KiotVietChannel,
    branch_id: str,
    time_range: str = "yesterday",
    debug_dir: str | None = None,
) -> list[RevenueRow]:
    """Run the full reporting flow for one branch and return parsed rows.

    When ``debug_dir`` is set, dumps each page's HTML there and logs cell/row
    counts so callers can diagnose why parsing returned no rows.
    """
    range_value, label = TIME_RANGE_PRESETS.get(time_range, TIME_RANGE_PRESETS["yesterday"])

    client_id = _post_clients(channel)
    instance_id = _post_instances(channel, client_id, branch_id, range_value, label)
    document_id = _post_documents(channel, client_id, instance_id)
    info = _get_document_info(channel, client_id, instance_id, document_id)

    page_count = int(info.get("pageCount") or 0)
    if debug_dir:
        _debug_print(
            f"[revenue-debug] channel={channel.id} branch={branch_id} "
            f"time_range={time_range} pageCount={page_count} "
            f"info_keys={sorted((info or {}).keys())}"
        )
    daily_rows: list[RevenueRow] = []
    hourly_aggregates: dict[date, dict[str, Decimal]] = {}
    report_date: date | None = None
    for page_number in range(1, page_count + 1):
        page = _get_page(channel, client_id, instance_id, document_id, page_number)
        page_content = page.get("pageContent") or ""

        if report_date is None:
            report_date = _extract_report_date(page_content)

        page_daily, page_hourly = parse_page_content(page_content, report_date=report_date)
        daily_rows.extend(page_daily)
        for d, agg in page_hourly.items():
            existing = hourly_aggregates.setdefault(d, _zero_aggregate())
            for key in _NUMERIC_FIELDS:
                existing[key] += agg[key]

        if debug_dir:
            _dump_debug_page(
                debug_dir=debug_dir,
                channel_id=channel.id,
                branch_id=branch_id,
                time_range=time_range,
                page_number=page_number,
                page_content=page_content,
                parsed_rows=len(page_daily) + len(page_hourly),
                report_date=report_date,
            )

    for d in sorted(hourly_aggregates.keys()):
        agg = hourly_aggregates[d]
        daily_rows.append(RevenueRow(date=d, **agg))
    return daily_rows


def _dump_debug_page(
    *,
    debug_dir: str,
    channel_id: int,
    branch_id: str,
    time_range: str,
    page_number: int,
    page_content: str,
    parsed_rows: int,
    report_date: date | None = None,
) -> None:
    """Write the raw page HTML to ``debug_dir`` and log parse stats."""
    import os

    os.makedirs(debug_dir, exist_ok=True)
    cell_matches = list(_ROW_CELL_RE.finditer(page_content or ""))
    mapped_cells = sum(1 for m in cell_matches if int(m.group(1)) in LEFT_COLUMN_MAP)
    unique_left = sorted({int(m.group(1)) for m in cell_matches})
    file_path = os.path.join(
        debug_dir,
        f"kiotviet_revenue_ch{channel_id}_br{branch_id}_{time_range}_p{page_number}.html",
    )
    try:
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write(page_content or "")
    except OSError as exc:
        _debug_print(f"[revenue-debug] failed to write {file_path}: {exc}")
    _debug_print(
        f"[revenue-debug] channel={channel_id} branch={branch_id} page={page_number} "
        f"html_len={len(page_content or '')} cell_matches={len(cell_matches)} "
        f"mapped_cells={mapped_cells} parsed_rows={parsed_rows} "
        f"report_date={report_date} unique_left={unique_left} dump={file_path}"
    )


def _debug_print(message: str) -> None:
    """Print debug info to stderr so it's visible from management commands."""
    import sys

    print(message, file=sys.stderr, flush=True)


def _branch_ids_for_channel(channel: KiotVietChannel) -> list[str]:
    """Resolve which branches to sync for a channel.

    Prefer ``branch_ids`` array; fall back to single ``branch_id``.
    """
    branch_ids = [str(b).strip() for b in (channel.branch_ids or []) if str(b).strip()]
    if branch_ids:
        return branch_ids
    single = str(channel.branch_id or "").strip()
    return [single] if single else []


def upsert_revenue_rows(
    channel: KiotVietChannel,
    branch_id: str,
    rows: Iterable[RevenueRow],
) -> list[KiotVietDailyRevenue]:
    """Insert/update daily revenue rows. Returns the upserted ORM objects."""
    objects: list[KiotVietDailyRevenue] = []
    with transaction.atomic():
        for row in rows:
            obj, _ = KiotVietDailyRevenue.objects.update_or_create(
                channel=channel,
                branch_id=branch_id,
                date=row.date,
                defaults={
                    "total_amount": row.total_amount,
                    "discount": row.discount,
                    "revenue": row.revenue,
                    "total_cost": row.total_cost,
                    "gross_profit": row.gross_profit,
                },
            )
            objects.append(obj)
    return objects


def sync_daily_revenue_for_channel(
    channel: KiotVietChannel,
    time_range: str = "yesterday",
    branch_id: str | None = None,
    debug_dir: str | None = None,
) -> dict:
    """Sync daily revenue for one channel.

    If ``branch_id`` is provided, only that branch is synced; otherwise every
    branch_id configured on the channel is synced one by one.
    """
    target_branches = [branch_id] if branch_id else _branch_ids_for_channel(channel)
    summary: dict = {
        "channel_id": channel.id,
        "channel_name": channel.name,
        "time_range": time_range,
        "branches": [],
        "rows_upserted": 0,
        "errors": [],
        "_objects": [],  # internal; caller may pop and forward to Lark push
    }
    for branch in target_branches:
        if not branch:
            continue
        try:
            rows = fetch_daily_revenue_rows(
                channel, branch, time_range=time_range, debug_dir=debug_dir
            )
            objects = upsert_revenue_rows(channel, branch, rows)
            summary["branches"].append(
                {"branch_id": branch, "rows": len(rows), "upserted": len(objects)}
            )
            summary["rows_upserted"] += len(objects)
            summary["_objects"].extend(objects)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Daily revenue sync failed for channel=%s branch=%s",
                channel.id,
                branch,
            )
            summary["errors"].append(
                {"branch_id": branch, "error": str(exc)}
            )
    return summary


def sync_daily_revenue_all_channels(
    time_range: str = "yesterday",
    push_to_lark: bool = True,
    debug_dir: str | None = None,
) -> dict:
    """Sync daily revenue for the fixed (channel_id, branch_id) pairs in ``SYNC_TARGETS``.

    After DB upsert, the upserted rows are pushed into the configured Lark Bitable
    (controlled by ``push_to_lark`` and the ``LARK_BITABLE_*`` settings).
    """
    summary: dict = {
        "time_range": time_range,
        "channels": 0,
        "rows_upserted": 0,
        "errors": [],
        "details": [],
    }
    channels_by_id = {
        c.id: c
        for c in KiotVietChannel.objects.filter(
            id__in=[cid for cid, _ in SYNC_TARGETS],
            deleted_at__isnull=True,
        )
    }
    all_objects: list[KiotVietDailyRevenue] = []
    for channel_id, branch_id in SYNC_TARGETS:
        channel = channels_by_id.get(channel_id)
        if channel is None:
            summary["errors"].append(
                {
                    "channel_id": channel_id,
                    "branch_id": branch_id,
                    "error": "KiotVietChannel not found or deleted",
                }
            )
            continue
        result = sync_daily_revenue_for_channel(
            channel,
            time_range=time_range,
            branch_id=branch_id,
            debug_dir=debug_dir,
        )
        all_objects.extend(result.pop("_objects", []))
        summary["channels"] += 1
        summary["rows_upserted"] += result["rows_upserted"]
        summary["errors"].extend(result["errors"])
        summary["details"].append(result)

    if push_to_lark and all_objects:
        dates_touched = {obj.date for obj in all_objects}
        try:
            summary["lark"] = push_daily_aggregates_to_lark(dates_touched)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Push to Lark Bitable failed")
            summary["lark"] = {"error": str(exc)}

    return summary
