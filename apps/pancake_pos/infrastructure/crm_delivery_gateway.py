"""Ghi "Trạng thái vận chuyển thực" CRM (`order_assessments.delivery_business_status_code`)
theo trạng thái Pancake — two-way-sync §5.1, MR D.

Không UPDATE thẳng: mã này kéo theo journey + (với `dispatched`) trừ tồn / chặn xuất trùng.
Mọi ghi đi đúng cửa của module đơn hàng:
- mã thường → `OrderService.update_order_inline(crm=…)` (journey `delivery_business_status_changed`,
  `payload_json.source = "pancake_pos"`, actor rỗng = hệ thống);
- `dispatched` → `OrderService.dispatch_orders_batch` — CÙNG đường quét kho / cron khớp mã
  chờ dùng: xác minh huỷ với KiotViet NGOÀI transaction, trừ tồn `issue_for_order`
  idempotent (đơn đã trừ → no-op), chặn đơn huỷ. Gọi NGOÀI transaction của người gọi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.db import connection, transaction

logger = logging.getLogger(__name__)

JOURNEY_SOURCE = "pancake_pos"


@dataclass(frozen=True)
class DeliveryState:
    order_id: int
    code: str
    # Mã hiện tại do Pancake đặt (journey đổi mã gần nhất có source pancake_pos).
    owned_by_pancake: bool
    fulfillment_warehouse_id: int | None
    deleted: bool


# PK đơn + unique `order_assessments.order_id` + index (order, event_type, created_at) của journey.
_STATE_SQL = """
SELECT o.id, coalesce(a.delivery_business_status_code, ''), o.fulfillment_warehouse_id,
       o.deleted_at IS NOT NULL,
       (SELECT e.payload_json ->> 'source'
          FROM order_journey_events e
         WHERE e.order_id = o.id
           AND e.event_type IN ('delivery_business_status_changed', 'assessment_created')
         ORDER BY e.created_at DESC, e.id DESC
         LIMIT 1)
  FROM orders o
  LEFT JOIN order_assessments a ON a.order_id = o.id
 WHERE o.id = %(id)s
"""

_WAREHOUSE_SQL = """
SELECT warehouse_id FROM pancake_pos_warehouse_links WHERE pancake_warehouse_id = %(pid)s
"""


class SqlCrmDeliveryGateway:
    def load(self, order_id: int) -> DeliveryState | None:
        with connection.cursor() as cur:
            cur.execute(_STATE_SQL, {"id": order_id})
            row = cur.fetchone()
        if row is None:
            return None
        return DeliveryState(
            order_id=int(row[0]), code=row[1] or "", owned_by_pancake=row[4] == JOURNEY_SOURCE,
            fulfillment_warehouse_id=int(row[2]) if row[2] is not None else None, deleted=bool(row[3]),
        )

    def crm_warehouse_for(self, pancake_warehouse_id: str) -> int | None:
        """Kho CRM ghép với kho Pancake (`pancake_pos_warehouse_links`, ~3 dòng)."""
        if not pancake_warehouse_id:
            return None
        with connection.cursor() as cur:
            cur.execute(_WAREHOUSE_SQL, {"pid": pancake_warehouse_id})
            row = cur.fetchone()
        return int(row[0]) if row else None

    def set_code(self, order_id: int, code: str) -> bool:
        """Đổi mã (không phải `dispatched`) qua đường sửa đánh giá. True nếu đổi."""
        from apps.orders.application.services.order_service_impl import OrderService
        from apps.orders.models import Order

        with transaction.atomic():
            order = (
                Order.objects.select_related("channel", "customer", "assessment")
                .select_for_update(of=("self",))
                .filter(id=order_id, deleted_at__isnull=True)
                .first()
            )
            if order is None:
                return False
            before = getattr(getattr(order, "assessment", None), "delivery_business_status_code", None) or ""
            if before == code:
                return False
            OrderService.update_order_inline(
                order, {"crm": {"delivery_business_status_code": code}}, None, journey_source=JOURNEY_SOURCE
            )
        return True

    def dispatch(self, order_id: int, *, warehouse_id: int, occurred_at: datetime | None) -> tuple[bool, str]:
        """Xuất kho qua đúng luồng quét kho (trừ tồn). (ok, mã lỗi | "")."""
        from apps.orders.application.services.order_service_impl import OrderService

        results = OrderService.dispatch_orders_batch(
            order_ids=[order_id], warehouse_id=warehouse_id, actor=None, occurred_at=occurred_at,
            journey_source=JOURNEY_SOURCE,
        )
        result = results[0] if results else {"ok": False, "error": "no_result"}
        if not result.get("ok"):
            return False, str(result.get("error") or "error")
        return True, ""
