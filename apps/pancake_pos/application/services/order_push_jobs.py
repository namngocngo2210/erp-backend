"""Điều phối đẩy đơn chạy nền (Celery) — two-way-sync §4.1, §7 A2.

- `run_cycle()` (beat 2 phút): quét bù đơn chốt chưa đẩy → đẩy hàng đợi → đối chiếu
  sửa/huỷ. Khoá `single_flight` để hai beat không chồng nhau (tiết kiệm request); an
  toàn dữ liệu KHÔNG dựa vào khoá này mà vào UNIQUE (order, part) + SKIP LOCKED.
- `run_for_orders(ids)` (event đổi trạng thái / chốt đơn, hoặc sau khi người điều phối
  chọn cách xuất): chỉ xử lý các đơn đó, không khoá toàn cục.
Cờ tắt (`PANCAKE_POS_ENABLED` hoặc `PANCAKE_POS_PUSH_ORDERS_ENABLED`) → no-op.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from apps.common.db_guards import single_flight
from apps.pancake_pos.application.services.factory import build_order_push_service, push_orders_enabled
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosError

logger = logging.getLogger(__name__)

LOCK_KEY = "lock:pancake_pos_push_orders"
LOCK_TTL_SECONDS = 600  # > time_limit task beat


def _run(order_ids: Sequence[int] | None, *, manual: bool = False, force: bool = False) -> dict[str, Any]:
    try:
        service = build_order_push_service()
    except PancakePosError as exc:  # thiếu key/shop
        logger.warning("pancake_pos: không dựng được dịch vụ đẩy đơn: %s", exc)
        return {"reason": "error", "detail": str(exc)}
    return {
        "discover": service.discover(
            order_ids, ignore_lookback=manual, force_ids=order_ids if (force and order_ids) else ()
        ),
        "push": service.push(order_ids),
        "changes": service.sync_changes(order_ids),
    }


def run_cycle() -> dict[str, Any]:
    if not push_orders_enabled():
        return {"reason": "disabled"}
    with single_flight(LOCK_KEY, LOCK_TTL_SECONDS) as acquired:
        if not acquired:
            return {"reason": "locked"}
        return _run(None)


def run_for_orders(order_ids: Sequence[int], *, manual: bool = False, force: bool = False) -> dict[str, Any]:
    """`manual` = người điều phối bấm (chọn cách xuất / thử lại): bỏ cửa sổ quét.
    `force` = "Vẫn đẩy" đơn bị luật chống trùng chặn (người điều phối đã kiểm tra)."""
    ids = sorted({int(i) for i in order_ids if i})
    if not ids:
        return {"reason": "empty"}
    if not push_orders_enabled():
        return {"reason": "disabled"}
    return _run(ids, manual=manual, force=force)
