"""Báo cáo trạng thái vận chuyển Pancake (two-way-sync §5.1, MR D) — chỉ đọc, không sửa đơn.

1. Đơn treo (theo kho Pancake):
   - CHƯA GỬI quá N giờ (`PANCAKE_POS_STUCK_NOT_SENT_HOURS`, mặc định 24) tính từ lúc tạo
     đơn Pancake: Mới / Đã xác nhận / Chờ in / Đã in / Đang đóng hàng / Chờ chuyển hàng;
   - ĐÃ GỬI quá M ngày (`PANCAKE_POS_STUCK_NOT_DELIVERED_DAYS`, mặc định 5) chưa giao, tính
     từ mốc gửi.
   Cảnh báo Lark mỗi ngày một lần lúc `PANCAKE_POS_STUCK_ALERT_HOUR` (giờ VN).
2. Lệch KiotViet ↔ Pancake: đơn CRM mà `orders.status` (KiotViet) mâu thuẫn rõ với trạng thái
   Pancake (`delivery_status.MISMATCH_RULES`), và đơn Pancake đã gửi/kết thúc mà "Trạng thái
   vận chuyển thực" CRM còn trước xuất kho (cờ xuất kho tắt / backfill / xuất kho lỗi).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from django.utils import timezone

from apps.pancake_pos.domain import delivery_status as delivery
from apps.pancake_pos.infrastructure.delivery_report_reader import OrderDeliveryRow, StuckBucket

ITEM_LIMIT = 200
NOT_SENT_FLOOR = timedelta(days=30)
NOT_DELIVERED_FLOOR = timedelta(days=60)
MISMATCH_WINDOW_DAYS = 30
MISMATCH_LIMIT = 500
# CRM OrderStatus (apps.orders.models.OrderStatus) — nhãn cho báo cáo.
CRM_STATUS_LABELS = {
    0: "Mới", 1: "Chờ xử lý", 2: "Đang giao", 3: "Giao thành công", 4: "Đang hoàn", 5: "Đã hoàn",
    6: "Đã huỷ", 7: "Đang lấy hàng", 8: "Chờ lấy lại", 9: "Đã lấy hàng", 10: "Chờ giao lại",
    11: "Chờ giao", 12: "Chờ chuyển hoàn", 13: "Chờ xác nhận hoàn",
}
NOT_DISPATCHED_RULE = "pancake_shipped_crm_not_dispatched"
NOT_DISPATCHED_LABEL = "Pancake đã gửi nhưng CRM chưa xuất kho (Trạng thái vận chuyển thực)"
_AFTER_SHIPPED = frozenset({2, 3, 16, 4, 5, 15})
_PRE_DISPATCH = frozenset({"", delivery.WAREHOUSE_PROCESSING})


class ReportReader(Protocol):
    def not_sent(self, *, cutoff: datetime, floor: datetime, limit: int) -> StuckBucket: ...

    def not_delivered(self, *, cutoff: datetime, floor: datetime, limit: int) -> StuckBucket: ...

    def matched_orders(self, *, since: datetime, names: list[str], limit: int) -> list[OrderDeliveryRow]: ...


class Alerter(Protocol):
    def __call__(self, *, key: str, title: str, lines: Sequence[str] = (), dedupe_seconds: int = ...) -> Any: ...


@dataclass(frozen=True)
class StuckConfig:
    not_sent_hours: int = 24
    not_delivered_days: int = 5
    alert_hour: int = 9


class DeliveryReportService:
    def __init__(
        self, *, reader: ReportReader, config: StuckConfig, status_names: dict[int, str],
        clock: Callable[[], datetime] = timezone.now,
    ) -> None:
        self._reader = reader
        self._config = config
        self._names = status_names
        self._clock = clock

    # ── đơn treo ────────────────────────────────────────────────────────────
    def stuck(self) -> dict[str, Any]:
        now = self._clock()
        cfg = self._config
        not_sent = self._reader.not_sent(
            cutoff=now - timedelta(hours=cfg.not_sent_hours), floor=now - NOT_SENT_FLOOR, limit=ITEM_LIMIT
        )
        not_delivered = self._reader.not_delivered(
            cutoff=now - timedelta(days=cfg.not_delivered_days), floor=now - NOT_DELIVERED_FLOOR, limit=ITEM_LIMIT
        )
        return {
            "generated_at": now,
            "not_sent_hours": cfg.not_sent_hours,
            "not_delivered_days": cfg.not_delivered_days,
            "not_sent": _bucket_dict(not_sent, now, use_shipped=False),
            "not_delivered": _bucket_dict(not_delivered, now, use_shipped=True),
        }

    def alert_stuck(self, alert: Alerter, *, force: bool = False) -> bool:
        """Lark một lần/ngày đúng giờ cấu hình (beat chạy mỗi giờ; khoá dedupe theo ngày)."""
        now_local = timezone.localtime(self._clock())
        if not force and (self._config.alert_hour < 0 or now_local.hour != self._config.alert_hour):
            return False
        report = self.stuck()
        ns, nd = report["not_sent"], report["not_delivered"]
        if not ns["count"] and not nd["count"]:
            return False
        lines = [
            f"Chưa gửi quá {report['not_sent_hours']} giờ: {ns['count']} đơn"
            + (f" ({_wh_text(ns['by_warehouse'])})" if ns["count"] else ""),
            f"Đã gửi quá {report['not_delivered_days']} ngày chưa giao: {nd['count']} đơn"
            + (f" ({_wh_text(nd['by_warehouse'])})" if nd["count"] else ""),
            *[f"• {it['pancake_order_id']} {it['status_name']} · {it['warehouse_name'] or '?'} · "
              f"{_age_text(it['age_hours'])}" for it in (ns["items"][:5] + nd["items"][:5])],
            "Chi tiết: Cài đặt → Pancake POS → Đơn treo.",
        ]
        return bool(alert(
            key=f"pancake_pos:stuck:{now_local.date().isoformat()}",
            title="Pancake POS: đơn treo vận chuyển",
            lines=lines,
            dedupe_seconds=20 * 3600,
        ))

    # ── lệch KiotViet ↔ Pancake ─────────────────────────────────────────────
    def mismatches(self) -> dict[str, Any]:
        now = self._clock()
        codes = delivery.codes_by_name(self._names)
        watched: set[int] = set(_AFTER_SHIPPED)
        for rule in delivery.MISMATCH_RULES:
            watched |= rule.pancake_statuses
        rows = self._reader.matched_orders(
            since=now - timedelta(days=MISMATCH_WINDOW_DAYS), names=delivery.names_for(watched, self._names),
            limit=MISMATCH_LIMIT,
        )
        items = []
        for row in rows:
            code = codes.get(row.partner_delivery_status)
            rule = delivery.mismatch(row.crm_status, code)
            if rule is not None:
                rule_code, rule_label = rule.code, rule.label
            elif code in _AFTER_SHIPPED and row.delivery_code in _PRE_DISPATCH:
                rule_code, rule_label = NOT_DISPATCHED_RULE, NOT_DISPATCHED_LABEL
            else:
                continue
            items.append({
                "order_id": row.order_id,
                "order_code": row.order_code,
                "crm_status": row.crm_status,
                "crm_status_label": CRM_STATUS_LABELS.get(row.crm_status, str(row.crm_status)),
                "delivery_business_status_code": row.delivery_code,
                "partner_delivery_status": row.partner_delivery_status,
                "partner_dvvc_delivery_status": row.partner_dvvc_delivery_status,
                "rule": rule_code,
                "rule_label": rule_label,
                "pancake_order_ids": row.pancake_order_ids,
                "updated_at": row.updated_at,
            })
        return {"window_days": MISMATCH_WINDOW_DAYS, "count": len(items), "items": items}


def _age_text(hours: float) -> str:
    return f"{hours / 24:.1f} ngày" if hours >= 48 else f"{hours:.0f} giờ"


def _wh_text(by_warehouse: list[dict[str, Any]]) -> str:
    return ", ".join(f"{w['warehouse_name']}: {w['count']}" for w in by_warehouse[:5])


def _bucket_dict(bucket: StuckBucket, now: datetime, *, use_shipped: bool) -> dict[str, Any]:
    def age(row) -> float:
        since = row.shipped_at if use_shipped else row.inserted_at
        return round((now - since).total_seconds() / 3600, 1) if since else 0.0

    return {
        "count": bucket.count,
        "by_warehouse": [{"warehouse_name": name, "count": n} for name, n in bucket.by_warehouse],
        "items": [
            {
                "pancake_order_id": r.pancake_order_id, "order_id": r.order_id, "order_code": r.order_code,
                "status_name": r.status_name, "dvvc_status": r.dvvc_status, "warehouse_name": r.warehouse_name,
                "tracking_code": r.tracking_code, "partner_name": r.partner_name, "inserted_at": r.inserted_at,
                "shipped_at": r.shipped_at, "age_hours": age(r),
            }
            for r in bucket.items
        ],
    }
