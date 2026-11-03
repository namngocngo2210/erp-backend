"""Đồng bộ "Trạng thái vận chuyển thực" CRM theo Pancake (two-way-sync §5.1, MR D).

Chạy SAU commit của sự kiện webhook (task `sync_delivery_code`) hoặc trong backfill. Đọc
các kiện Pancake đã khớp của đơn CRM (`pancake_pos_inbound_orders`), lấy kiện chậm nhất,
tra mã đích ở `status_map.crm_delivery_code`, rồi `delivery_status.decide_delivery_code`
quyết định (chỉ tiến, không ghi đè mã người nhập, `dispatched` chỉ qua luồng xuất kho khi
bật `PANCAKE_POS_DISPATCH_ENABLED`).

Idempotent: chạy lại cùng dữ liệu → không đổi gì (mã đã bằng đích; xuất kho
`issue_for_order` no-op khi đã trừ).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from apps.pancake_pos.domain import delivery_status as delivery
from apps.pancake_pos.inbound_repositories import InboundOrderRepository, status_map
from apps.pancake_pos.infrastructure.crm_delivery_gateway import DeliveryState

logger = logging.getLogger(__name__)


class CrmDeliveryPort(Protocol):
    def load(self, order_id: int) -> DeliveryState | None: ...

    def crm_warehouse_for(self, pancake_warehouse_id: str) -> int | None: ...

    def set_code(self, order_id: int, code: str) -> bool: ...

    def dispatch(self, order_id: int, *, warehouse_id: int, occurred_at: datetime | None) -> tuple[bool, str]: ...


@dataclass(frozen=True)
class SyncOutcome:
    """`result`: noop | written | dispatched | blocked | error. `detail` = mã/lý do."""

    result: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.result}:{self.detail}" if self.detail else self.result


class DeliverySyncService:
    def __init__(self, *, crm: CrmDeliveryPort, dispatch_enabled: bool) -> None:
        self._crm = crm
        self._dispatch_enabled = dispatch_enabled
        self._codes: dict[int, str] | None = None

    def _code_map(self) -> dict[int, str]:
        if self._codes is None:
            self._codes = {code: entry.delivery_code for code, entry in status_map().items()}
        return self._codes

    def target_for(self, order_id: int) -> tuple[str, delivery.PartState | None, datetime | None, str]:
        """(mã đích, kiện đại diện, mốc gửi của kiện, kho Pancake của kiện)."""
        parts = InboundOrderRepository.parts_for_order(order_id)
        chosen = delivery.slowest_part([p.state for p in parts])
        if chosen is None:
            return "", None, None, ""
        part = next(p for p in parts if p.state is chosen)
        return (
            self._code_map().get(chosen.pancake_status if chosen.pancake_status is not None else -1, ""),
            chosen, part.shipped_at, part.pancake_warehouse_id,
        )

    def sync(self, order_id: int, *, allow_dispatch: bool = True) -> SyncOutcome:
        target, chosen, shipped_at, pancake_wh = self.target_for(order_id)
        if not target:
            return SyncOutcome("noop", "no_target")
        state = self._crm.load(order_id)
        if state is None or state.deleted:
            return SyncOutcome("noop", "order_missing")
        decision = delivery.decide_delivery_code(
            state.code, target, owned_by_pancake=state.owned_by_pancake,
            dispatch_enabled=self._dispatch_enabled, allow_dispatch=allow_dispatch,
        )
        if decision.blocked:
            return SyncOutcome("blocked", decision.blocked)
        if decision.dispatch:
            warehouse_id = self._crm.crm_warehouse_for(pancake_wh) or state.fulfillment_warehouse_id
            if warehouse_id is None:
                return SyncOutcome("blocked", "no_warehouse")
            ok, error = self._crm.dispatch(order_id, warehouse_id=warehouse_id, occurred_at=shipped_at)
            if not ok:
                logger.warning("pancake_pos: xuất kho đơn %s theo Pancake lỗi: %s", order_id, error)
                return SyncOutcome("error", error)
            if decision.write is None:
                return SyncOutcome("dispatched", delivery.DISPATCHED)
        if decision.write is None:
            return SyncOutcome("noop")
        written = self._crm.set_code(order_id, decision.write)
        if decision.dispatch:
            return SyncOutcome("dispatched", decision.write)
        return SyncOutcome("written" if written else "noop", decision.write)
