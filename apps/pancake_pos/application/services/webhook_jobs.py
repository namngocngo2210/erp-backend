"""Nhận + điều phối xử lý webhook Pancake POS (two-way-sync §5.1, §7 B1).

- `ingest(payload)`: phân loại + lưu thô (chống trùng). KHÔNG xử lý ở request — trả 200
  nhanh; sự kiện `orders` được xếp task `process_webhook_event` sau commit.
- `process_one(id)`: task theo từng sự kiện (khoá dòng sự kiện + dòng đơn Pancake).
- `run_cycle()`: beat 1 phút (single_flight) — vớt sự kiện đơn còn pending / lỗi còn
  lượt, khớp lại đơn chờ (quá hạn → Chờ khớp + Lark), dọn sự kiện cũ đã xử lý.

B1 chỉ xử lý `orders`. `variations_warehouses` / `products` lưu `pending` chờ B2 (worker
không lấy); `customers` / không rõ lưu `ignored` để còn dấu vết.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.common.db_guards import single_flight
from apps.pancake_pos.application.services.factory import build_inbound_order_service
from apps.pancake_pos.domain import inbound_order as rules
from apps.pancake_pos.inbound_repositories import WebhookEventRepository
from apps.pancake_pos.metrics import webhook_received_total
from apps.pancake_pos.models import WebhookEventStatus

logger = logging.getLogger(__name__)

LOCK_KEY = "lock:pancake_pos_webhook_cycle"
LOCK_TTL_SECONDS = 300
CYCLE_BATCH = 500
REMATCH_BATCH = 200
MAX_ATTEMPTS = 5
RETRY_AFTER = timedelta(minutes=5)
RETENTION = timedelta(days=30)
MAX_EVENTS_PER_REQUEST = 100

# B1 xử lý đơn; tồn/sản phẩm giữ pending cho B2; loại khác chỉ lưu dấu vết.
_PENDING_TYPES = frozenset({rules.EVENT_ORDERS, rules.EVENT_INVENTORY, rules.EVENT_PRODUCTS})


def ingest(payload: Any) -> list[tuple[int | None, str]]:
    """Lưu từng sự kiện của request (object hoặc mảng ≤ 100). Trả [(id | None nếu trùng, loại)]."""
    items = payload if isinstance(payload, list) else [payload]
    out: list[tuple[int | None, str]] = []
    for item in items[:MAX_EVENTS_PER_REQUEST]:
        classified = rules.classify_payload(item)
        status = WebhookEventStatus.PENDING if classified.event_type in _PENDING_TYPES else WebhookEventStatus.IGNORED
        event_id = WebhookEventRepository.store(
            event_type=classified.event_type, record_id=classified.record_id, version=classified.version,
            payload=item if isinstance(item, (dict, list)) else {"raw": item},
            payload_hash=rules.payload_hash(item), status=status,
        )
        webhook_received_total.labels(classified.event_type, "stored" if event_id else "duplicate").inc()
        out.append((event_id, classified.event_type))
    order_ids = [eid for eid, kind in out if eid and kind == rules.EVENT_ORDERS]
    if order_ids:
        transaction.on_commit(lambda: _enqueue(order_ids))
    return out


def store_order_payload(raw: Any) -> int | None:
    """Lưu một đơn Pancake lấy bằng GET (backfill) như sự kiện `orders` — KHÔNG xếp task:
    người gọi xử lý ngay. None = đã có bản y hệt / không phải đơn."""
    classified = rules.classify_payload(raw)
    if classified.event_type != rules.EVENT_ORDERS:
        return None
    return WebhookEventRepository.store(
        event_type=classified.event_type, record_id=classified.record_id, version=classified.version,
        payload=raw, payload_hash=rules.payload_hash(raw),
    )


def _enqueue(event_ids: Sequence[int]) -> None:
    from apps.pancake_pos.tasks import process_webhook_event

    for event_id in event_ids:
        try:
            process_webhook_event.delay(event_id)
        except Exception:  # noqa: BLE001 — broker lỗi: beat 1 phút vớt
            logger.exception("pancake_pos: không xếp được task xử lý webhook #%s", event_id)


def process_one(event_id: int) -> str:
    return build_inbound_order_service().process_event(int(event_id))


def run_cycle() -> dict[str, Any]:
    with single_flight(LOCK_KEY, LOCK_TTL_SECONDS) as acquired:
        if not acquired:
            return {"reason": "locked"}
        service = build_inbound_order_service()
        processed: dict[str, int] = {}
        for event_id in WebhookEventRepository.due_order_events(
            limit=CYCLE_BATCH, max_attempts=MAX_ATTEMPTS, retry_after=RETRY_AFTER
        ):
            outcome = service.process_event(event_id).split(":", 1)[0]
            processed[outcome] = processed.get(outcome, 0) + 1
        rematched = service.rematch(limit=REMATCH_BATCH)
        purged = WebhookEventRepository.purge(older_than=timezone.now() - RETENTION)
        return {"processed": processed, "rematch": rematched, "purged": purged}


def retry_errors() -> int:
    """Nút "Xử lý lại sự kiện lỗi": đưa về pending rồi xếp task ngay."""
    ids = WebhookEventRepository.reset_errors()
    if ids:
        _enqueue(ids)
    return len(ids)
