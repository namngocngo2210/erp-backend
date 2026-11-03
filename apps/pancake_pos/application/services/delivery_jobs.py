"""Điều phối trạng thái vận chuyển Pancake (two-way-sync §5.1, MR D): đồng bộ mã "Trạng
thái vận chuyển thực" một đơn (task sau webhook), cảnh báo đơn treo hằng ngày (beat) và
backfill N ngày (lệnh `pancake_pos_backfill_delivery_status`)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.utils import timezone

from apps.common.ops_alerts import send_ops_alert
from apps.pancake_pos.application.services import webhook_jobs
from apps.pancake_pos.application.services.delivery_sync_service import SyncOutcome
from apps.pancake_pos.application.services.factory import (
    build_client,
    build_delivery_report_service,
    build_delivery_sync_service,
    build_inbound_order_service,
)
from apps.pancake_pos.domain import delivery_status as delivery
from apps.pancake_pos.domain import inbound_order as rules


def sync_one(order_id: int) -> SyncOutcome:
    return build_delivery_sync_service().sync(order_id, allow_dispatch=True)


def alert_stuck() -> bool:
    return build_delivery_report_service().alert_stuck(send_ops_alert)


@dataclass
class BackfillReport:
    fetched: int = 0
    stored: int = 0
    duplicate: int = 0
    outcomes: Counter = field(default_factory=Counter)
    delivery: Counter = field(default_factory=Counter)
    statuses: Counter = field(default_factory=Counter)
    dvvc: Counter = field(default_factory=Counter)


def backfill(
    *, days: int, dry_run: bool, now: datetime | None = None, client=None,
    on_progress: Callable[[BackfillReport], None] | None = None,
) -> BackfillReport:
    """Kéo đơn Pancake cập nhật trong N ngày (GET read-only) và áp qua ĐÚNG pipeline webhook:
    lưu như sự kiện `orders` → `InboundOrderService.process_event` (khớp, 2 field partner) →
    mã "Trạng thái vận chuyển thực" với `allow_dispatch=False` — backfill KHÔNG BAO GIỜ xuất
    kho/trừ tồn, kể cả khi bật PANCAKE_POS_DISPATCH_ENABLED (đơn cần xuất kho nằm ở báo cáo
    lệch). `--dry-run`: chỉ đếm trạng thái + thử khớp (không ghi gì)."""
    now = now or timezone.now()
    client = client or build_client()
    sync = build_delivery_sync_service()
    report = BackfillReport()

    def after_commit(order_id: int) -> None:
        outcome = sync.sync(order_id, allow_dispatch=False)
        report.delivery[str(outcome)] += 1

    service = build_inbound_order_service(on_matched=after_commit)
    for raw in client.iter_orders_raw(since=int((now - timedelta(days=days)).timestamp()), until=int(now.timestamp())):
        report.fetched += 1
        snap = rules.parse_order_snapshot(raw)
        if snap is not None:
            report.statuses[delivery.status_label(snap.status, None, snap.status_name)] += 1
            report.dvvc[snap.dvvc.label or "(trống)"] += 1
        if dry_run:
            match = service.preview_match(raw)
            report.outcomes["match:" + (match.method if match and match.order_id else "none")] += 1
            continue
        stored = webhook_jobs.store_order_payload(raw)
        if stored is None:
            report.duplicate += 1
            continue
        report.stored += 1
        report.outcomes[service.process_event(stored).split(":", 1)[0]] += 1
        if on_progress is not None and report.fetched % 200 == 0:
            on_progress(report)
    return report
