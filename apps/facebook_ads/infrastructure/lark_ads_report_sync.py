"""Push the ads report (rolling 7-day window) to two Lark Bitable tables.

Faithful Python port of the former Go handler ``ads.report_lark_sync``
(go-services/.../handlers/ads_report_lark_sync.go). Two grains:

  - channel grain  (day × channel)          -> ads_daily_channel_lark_records
  - product grain  (day × channel × product) -> ads_daily_product_lark_records

Idempotency is via ``record_key`` (``YYYY-MM-DD|channel_id`` /
``...|product_id``); the local mapping tables hold ``record_key -> lark_record_id``
so reruns PUT the same Bitable record. Keys that were in the window but no longer
have data (orders all cancelled) are *zeroed-out* rather than left stale.

Aggregation SQL mirrors the Go handler byte-for-byte so the Lark numbers match.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, UTC
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q

from apps.channels_core.models import Channel
from apps.common.db_guards import statement_timeout
from apps.common.ops_alerts import send_ops_alert
from apps.facebook_ads.models import FacebookApp
from apps.kiotviet.infrastructure.services.lark_bitable_client import (
    batch_create_records,
    batch_update_records,
)
from apps.orders.models import AdsDailyChannelLarkRecord, AdsDailyProductLarkRecord
from apps.orders._repositories_reports import facebook_ads_cost_multiplier
from apps.common.order_scope import sql_exclude_mirror_orders
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

ADS_TIME_ZONE = "Asia/Bangkok"
ADS_WINDOW_DAYS = 7
# Đọc từ settings.SALE_FB_CHANNEL_IDS (env) — trước đây hard-code 24 tại đây,
# lệch nguồn với orders/_repositories_reports (mìn channel-id, audit 2026-08-19).
ADS_EXCLUDED_BRANCH_ID = "1000000139"

# sales_status_code treated as "closed" (đã chốt). Matches the Go handler list.
ADS_CLOSED_SALES_STATUS = ["closed", "closed_express", "closed_scheduled_date", "created_order"]


def _ads_excluded_channel_ids() -> list[int]:
    """Channel ids loại khỏi báo cáo ads: Sale FB + KOC (hàng mẫu).

    KOC nhận diện theo ``KOC_CHANNEL_ID`` hoặc mã ``koc`` — match cả code vì id
    channel sinh tự động nên có thể khác giữa môi trường (xem ``Channel.is_koc``).
    """
    ids = set(settings.SALE_FB_CHANNEL_IDS)
    ids.update(
        Channel.objects.filter(
            Q(id=getattr(settings, "KOC_CHANNEL_ID", 15)) | Q(code__iexact=Channel.KOC_CODE),
            deleted_at__isnull=True,
        ).values_list("id", flat=True)
    )
    return list(ids)

# Lark Bitable column names — channel grain.
F_DATE = "Ngày"
F_CHANNEL = "Kênh"
F_CHANNEL_CODE = "Mã kênh"
F_REVENUE = "Doanh thu"
F_CLOSED_ORDERS = "Số đơn chốt"
F_COST = "Chi phí"
F_RATIO = "Tỷ lệ CP/DT (%)"
F_RECORD_KEY = "record_key"

# Product grain extra columns.
F_PRODUCT = "Sản phẩm"
F_SKU = "SKU"
F_PRODUCT_REVENUE = "Doanh thu SP"


def _date_to_lark_ms(d: date) -> int:
    """Lark Date field expects ms-since-epoch. Noon UTC keeps the date stable across TZs."""
    return int(datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=UTC).timestamp() * 1000)


def _ads_table_config() -> tuple[str, str, str] | None:
    """Return (app_token, channel_table_id, product_table_id) or None if unconfigured."""
    app_token = sysconfig.get("LARK_BITABLE_APP_TOKEN")
    channel_table = sysconfig.get("LARK_BITABLE_ADS_CHANNEL_TABLE_ID")
    product_table = sysconfig.get("LARK_BITABLE_ADS_PRODUCT_TABLE_ID")
    if not app_token or not channel_table or not product_table:
        return None
    return app_token, channel_table, product_table


# ── aggregation ──────────────────────────────────────────────────────────────

def _aggregate_channel(from_day: date, to_day: date) -> list[dict]:
    """Day × channel: closed-order count + revenue, plus allocated FB ad cost.

    Hệ số chi phí FB theo mốc thời gian — xem ``facebook_ads_cost_multiplier``.
    """
    rev_sql = f"""
SELECT (o.created_at AT TIME ZONE %s)::date AS d,
       o.channel_id AS channel_id, ch.code AS code, ch.name AS name,
       COUNT(o.id) AS closed_orders,
       COALESCE(SUM(o.total_after_discount), 0) AS revenue
FROM orders o
JOIN order_assessments oa ON oa.order_id = o.id
JOIN channels ch ON ch.id = o.channel_id
WHERE o.deleted_at IS NULL
  AND o.order_source IS NOT NULL AND o.order_source <> ''
  AND {sql_exclude_mirror_orders("o")}
  AND o.status <> 6
  AND (o.invoice_status IS NULL OR o.invoice_status <> 2)
  AND o.channel_id <> ALL(%s)
  AND o.branch_id <> %s
  AND oa.sales_status_code = ANY(%s)
  AND (o.created_at AT TIME ZONE %s)::date BETWEEN %s AND %s
GROUP BY d, o.channel_id, ch.code, ch.name
"""
    cost_sql = """
WITH acc_chan AS (
    SELECT ch.id AS channel_id, ch.code, ch.name, caa.account_id AS ad_account_id
    FROM channels ch
    JOIN channel_ad_accounts caa
      ON caa.channel_id = ch.id AND caa.provider = 'fb'
    WHERE ch.deleted_at IS NULL AND ch.id <> ALL(%s)
),
acc_count AS (
    SELECT ad_account_id, COUNT(*) AS n FROM acc_chan GROUP BY ad_account_id
)
SELECT s.date AS d,
       ac.channel_id AS channel_id, ac.code AS code, ac.name AS name,
       COALESCE(SUM(s.spend::numeric / acc_count.n) * %s, 0) AS cost
FROM facebook_ad_account_daily_spends s
JOIN acc_chan ac ON ac.ad_account_id = s.ad_account_id
JOIN acc_count ON acc_count.ad_account_id = s.ad_account_id
WHERE s.date BETWEEN %s AND %s
GROUP BY s.date, ac.channel_id, ac.code, ac.name
"""
    merged: dict[str, dict] = {}
    excluded_ids = _ads_excluded_channel_ids()

    def key_of(d: date, ch: int) -> str:
        return f"{d.isoformat()}|{ch}"

    with statement_timeout(getattr(settings, "REPORT_QUERY_TIMEOUT_SECONDS", 0)), connection.cursor() as cur:
        cur.execute(
            rev_sql,
            [ADS_TIME_ZONE, excluded_ids, ADS_EXCLUDED_BRANCH_ID,
             ADS_CLOSED_SALES_STATUS, ADS_TIME_ZONE, from_day, to_day],
        )
        for d, channel_id, code, name, closed_orders, revenue in cur.fetchall():
            merged[key_of(d, channel_id)] = {
                "date": d, "channel_id": channel_id, "code": code or "", "name": name or "",
                "closed_orders": int(closed_orders or 0), "revenue": float(revenue or 0), "cost": 0.0,
            }
        # Hệ số chi phí FB đổi theo mốc 01/08/2026 và chọn theo NGÀY BẮT ĐẦU cửa
        # sổ — DÙNG CHUNG helper với báo cáo trong CRM, không hard-code lại ở đây:
        # bảng Lark và màn báo cáo là hai con số người ta đặt cạnh nhau để đối chiếu.
        cur.execute(
            cost_sql,
            [excluded_ids, facebook_ads_cost_multiplier(from_day), from_day, to_day],
        )
        for d, channel_id, code, name, cost in cur.fetchall():
            k = key_of(d, channel_id)
            if k in merged:
                merged[k]["cost"] = float(cost or 0)
            else:
                merged[k] = {
                    "date": d, "channel_id": channel_id, "code": code or "", "name": name or "",
                    "closed_orders": 0, "revenue": 0.0, "cost": float(cost or 0),
                }
    return list(merged.values())


def _aggregate_product(from_day: date, to_day: date) -> list[dict]:
    """Day × channel × product: distinct closed-order count + product revenue."""
    sql = f"""
SELECT (o.created_at AT TIME ZONE %s)::date AS d,
       o.channel_id AS channel_id, ch.name AS channel_name,
       oi.product_id AS product_id, p.name AS product_name, p.sku AS sku,
       COUNT(DISTINCT oi.order_id) AS closed_orders,
       COALESCE(SUM(oi.line_total), 0) AS revenue
FROM order_items oi
JOIN orders o ON o.id = oi.order_id
JOIN order_assessments oa ON oa.order_id = o.id
JOIN channels ch ON ch.id = o.channel_id
JOIN products p ON p.id = oi.product_id
WHERE o.deleted_at IS NULL
  AND o.order_source IS NOT NULL AND o.order_source <> ''
  AND {sql_exclude_mirror_orders("o")}
  AND o.status <> 6
  AND (o.invoice_status IS NULL OR o.invoice_status <> 2)
  AND o.channel_id <> ALL(%s)
  AND o.branch_id <> %s
  AND oa.sales_status_code = ANY(%s)
  AND (o.created_at AT TIME ZONE %s)::date BETWEEN %s AND %s
GROUP BY d, o.channel_id, ch.name, oi.product_id, p.name, p.sku
"""
    rows: list[dict] = []
    with statement_timeout(getattr(settings, "REPORT_QUERY_TIMEOUT_SECONDS", 0)), connection.cursor() as cur:
        cur.execute(
            sql,
            [ADS_TIME_ZONE, _ads_excluded_channel_ids(), ADS_EXCLUDED_BRANCH_ID,
             ADS_CLOSED_SALES_STATUS, ADS_TIME_ZONE, from_day, to_day],
        )
        for d, channel_id, channel_name, product_id, product_name, sku, closed_orders, revenue in cur.fetchall():
            rows.append({
                "date": d, "channel_id": channel_id, "channel_name": channel_name or "",
                "product_id": product_id, "product_name": product_name or "", "sku": sku or "",
                "closed_orders": int(closed_orders or 0), "revenue": float(revenue or 0),
            })
    return rows


# ── field + key builders ─────────────────────────────────────────────────────

def _channel_fields(r: dict) -> dict:
    ratio = (r["cost"] / r["revenue"] * 100) if r["revenue"] > 0 else 0.0
    return {
        F_DATE: _date_to_lark_ms(r["date"]),
        F_CHANNEL: r["name"],
        F_CHANNEL_CODE: r["code"],
        F_REVENUE: r["revenue"],
        F_CLOSED_ORDERS: r["closed_orders"],
        F_COST: r["cost"],
        F_RATIO: ratio,
    }


def _product_fields(r: dict) -> dict:
    return {
        F_DATE: _date_to_lark_ms(r["date"]),
        F_CHANNEL: r["channel_name"],
        F_PRODUCT: r["product_name"],
        F_SKU: r["sku"],
        F_CLOSED_ORDERS: r["closed_orders"],
        F_PRODUCT_REVENUE: r["revenue"],
    }


def _channel_key(r: dict) -> str:
    return f"{r['date'].isoformat()}|{r['channel_id']}"


def _product_key(r: dict) -> str:
    return f"{r['date'].isoformat()}|{r['channel_id']}|{r['product_id']}"


def _zero_channel() -> dict:
    return {F_REVENUE: 0, F_CLOSED_ORDERS: 0, F_COST: 0, F_RATIO: 0}


def _zero_product() -> dict:
    return {F_CLOSED_ORDERS: 0, F_PRODUCT_REVENUE: 0}


# ── upsert one table ─────────────────────────────────────────────────────────

def _upsert_table(*, app_token, table_id, desired, window_dates, mapping_model, zero_fields) -> dict:
    """Classify desired rows into create/update, zero-out stale keys, push to Lark, save mappings.

    ``desired`` is a list of (key, fields). ``mapping_model`` is the Django model whose
    rows hold ``record_key -> lark_record_id`` for this grain.
    """
    res = {"created": 0, "updated": 0, "zeroed": 0}

    # window_keys: every mapped record_key whose date-prefix is inside the window.
    # Only keys that already have a lark_record_id can be zeroed-out.
    prefix_q = Q()
    for d in window_dates:
        prefix_q |= Q(record_key__startswith=d.isoformat())
    existing: dict[str, str] = {}
    for m in mapping_model.objects.filter(prefix_q):
        if m.lark_record_id:
            existing[m.record_key] = m.lark_record_id

    desired_keys = {k for k, _ in desired}
    to_create: list[tuple[str, dict]] = []
    to_update: list[dict] = []
    for key, fields in desired:
        rec_id = existing.get(key)
        if rec_id:
            to_update.append({"record_id": rec_id, "fields": fields})
        else:
            to_create.append((key, fields))

    zero: list[dict] = []
    for key, rec_id in existing.items():
        if key not in desired_keys:
            zero.append({"record_id": rec_id, "fields": zero_fields()})

    if to_update:
        batch_update_records(app_token, table_id, to_update)
        res["updated"] = len(to_update)
    if zero:
        batch_update_records(app_token, table_id, zero)
        res["zeroed"] = len(zero)
    if to_create:
        payload = [{"fields": fields} for _, fields in to_create]
        created_records = batch_create_records(app_token, table_id, payload)
        saved, failed = 0, 0
        with transaction.atomic():
            for (key, _fields), created in zip(to_create, created_records):
                rec_id = created.get("record_id")
                if not rec_id:
                    continue
                try:
                    mapping_model.objects.update_or_create(
                        record_key=key, defaults={"lark_record_id": rec_id},
                    )
                    saved += 1
                except Exception:  # noqa: BLE001
                    logger.exception("ads lark mapping save failed (key=%s)", key)
                    failed += 1
        res["created"] = saved
        if failed:
            res["mapping_save_failed"] = failed
    return res


def _apps_with_dead_tokens() -> list[FacebookApp]:
    return list(
        FacebookApp.objects.filter(
            deleted_at__isnull=True, token_status=FacebookApp.TOKEN_INVALID,
        ).order_by("id")
    )


def sync_ads_report_to_lark(*, force: bool = False) -> dict:
    """Aggregate the rolling 7-day window and push both grains to Lark. Idempotent.

    ``force=True`` bỏ qua chốt chặn dữ liệu suy giảm — chỉ dùng khi đẩy tay và đã
    hiểu rõ tình trạng token.
    """
    config = _ads_table_config()
    if config is None:
        logger.info("Lark ads tables not configured; skipping push.")
        return {"skipped": True, "reason": "lark_not_configured"}
    app_token, channel_table, product_table = config

    # Chốt chặn dữ liệu suy giảm: token FB hỏng ⇒ bảng chi tiêu thiếu ngày mới, mà
    # luồng zero-out bên dưới còn ghi thẳng "Chi phí = 0" cho ngày trống. Kết quả là
    # tỷ lệ CP/DT đẹp hơn thực tế — sai kiểu này nguy hiểm hơn báo cáo đứng yên, vì
    # không ai nghi ngờ. Giữ số cũ và gọi người (sự cố 25–27/07/2026).
    if not force:
        dead = _apps_with_dead_tokens()
        if dead:
            names = ", ".join(f"{a.name} (id {a.pk})" for a in dead)
            send_ops_alert(
                key="fb:ads_report_blocked",
                title="[Facebook Ads] Đã CHẶN đẩy báo cáo ads sang Lark — dữ liệu chi tiêu không đầy đủ",
                lines=[
                    f"App có token hỏng: {names}",
                    "Bitable giữ nguyên số cũ để không ghi đè bằng chi phí thiếu.",
                    "Đẩy lại tự động ngay sau khi token được cấp lại.",
                ],
                dedupe_seconds=6 * 3600,
            )
            logger.warning("ads report bị chặn — token hỏng ở app: %s", names)
            return {
                "skipped": True,
                "reason": "facebook_token_invalid",
                "apps": [a.pk for a in dead],
            }

    now_local = datetime.now(ZoneInfo(ADS_TIME_ZONE))
    to_day = now_local.date()
    from_day = to_day - timedelta(days=ADS_WINDOW_DAYS - 1)
    window_dates = [from_day + timedelta(days=i) for i in range((to_day - from_day).days + 1)]

    summary = {"from": from_day.isoformat(), "to": to_day.isoformat()}

    ch_rows = _aggregate_channel(from_day, to_day)
    ch_desired = []
    for r in ch_rows:
        key = _channel_key(r)
        fields = _channel_fields(r)
        fields[F_RECORD_KEY] = key
        ch_desired.append((key, fields))
    summary["channel"] = _upsert_table(
        app_token=app_token, table_id=channel_table, desired=ch_desired,
        window_dates=window_dates, mapping_model=AdsDailyChannelLarkRecord,
        zero_fields=_zero_channel,
    )

    p_rows = _aggregate_product(from_day, to_day)
    p_desired = []
    for r in p_rows:
        key = _product_key(r)
        fields = _product_fields(r)
        fields[F_RECORD_KEY] = key
        p_desired.append((key, fields))
    summary["product"] = _upsert_table(
        app_token=app_token, table_id=product_table, desired=p_desired,
        window_dates=window_dates, mapping_model=AdsDailyProductLarkRecord,
        zero_fields=_zero_product,
    )

    return summary