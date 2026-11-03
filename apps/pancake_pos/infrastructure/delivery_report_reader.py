"""SQL báo cáo trạng thái vận chuyển Pancake (two-way-sync §5.1, MR D): đơn treo + lệch
KiotViet ↔ Pancake. Chỉ đọc.

- Đơn treo đọc `pancake_pos_inbound_orders` (1 dòng / đơn Pancake = một kiện) theo 2 index
  partial `ppos_inbound_not_sent_idx` (inserted_at, nhóm chưa gửi) và
  `ppos_inbound_shipped_idx` (shipped_at, đã gửi). Cửa sổ dưới (30/60 ngày) chặn đơn bỏ
  hoang từ lâu làm loãng báo cáo.
- Lệch: kiện đã khớp cập nhật trong cửa sổ (index `ppos_inbound_updated_idx`) → join
  `orders` (PK) + `order_assessments` (unique order_id). Luật lệch áp ở Python
  (`domain.delivery_status`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import connection

NOT_SENT_STATUSES = (0, 1, 12, 13, 8, 9)
SHIPPED_STATUS = 2

_ITEM_COLUMNS = """
       i.pancake_order_id, i.order_id,
       coalesce(nullif(o.order_code, ''), o.invoice_code, '') AS order_code,
       i.status_name, i.dvvc_status, i.warehouse_name, i.tracking_code, i.partner_name,
       i.inserted_at, i.shipped_at
"""

_NOT_SENT_WHERE = """
  FROM pancake_pos_inbound_orders i
  LEFT JOIN orders o ON o.id = i.order_id
 WHERE i.pancake_status IN (0, 1, 12, 13, 8, 9)
   AND i.inserted_at < %(cutoff)s AND i.inserted_at >= %(floor)s
   AND i.match_status <> 'ignored'
"""

_NOT_DELIVERED_WHERE = """
  FROM pancake_pos_inbound_orders i
  LEFT JOIN orders o ON o.id = i.order_id
 WHERE i.pancake_status = 2
   AND i.shipped_at < %(cutoff)s AND i.shipped_at >= %(floor)s
   AND i.match_status <> 'ignored'
"""

_MISMATCH_SQL = """
SELECT o.id,
       coalesce(nullif(o.order_code, ''), o.invoice_code, '') AS order_code,
       o.status, o.partner_delivery_status, o.partner_dvvc_delivery_status,
       coalesce(a.delivery_business_status_code, '') AS delivery_code,
       max(i.updated_at) AS updated_at,
       array_agg(i.pancake_order_id ORDER BY i.inserted_at, i.id) AS pancake_order_ids
  FROM pancake_pos_inbound_orders i
  JOIN orders o ON o.id = i.order_id
  LEFT JOIN order_assessments a ON a.order_id = o.id
 WHERE i.match_status = 'matched'
   AND i.updated_at >= %(since)s
   AND o.deleted_at IS NULL
   AND o.partner_delivery_status = ANY(%(names)s)
 GROUP BY o.id, a.delivery_business_status_code
 ORDER BY max(i.updated_at) DESC
 LIMIT %(limit)s
"""


@dataclass(frozen=True)
class StuckRow:
    pancake_order_id: str
    order_id: int | None
    order_code: str
    status_name: str
    dvvc_status: str
    warehouse_name: str
    tracking_code: str
    partner_name: str
    inserted_at: datetime | None
    shipped_at: datetime | None


@dataclass(frozen=True)
class StuckBucket:
    count: int
    by_warehouse: list[tuple[str, int]]
    items: list[StuckRow]


@dataclass(frozen=True)
class OrderDeliveryRow:
    order_id: int
    order_code: str
    crm_status: int
    partner_delivery_status: str
    partner_dvvc_delivery_status: str
    delivery_code: str
    updated_at: datetime | None
    pancake_order_ids: list[str]


class SqlDeliveryReportReader:
    def _bucket(self, where: str, params: dict, order_by: str, limit: int) -> StuckBucket:
        with connection.cursor() as cur:
            cur.execute(
                f"SELECT coalesce(nullif(i.warehouse_name, ''), '?'), count(*) {where} "  # noqa: S608 — hằng nội bộ
                "GROUP BY 1 ORDER BY 2 DESC",
                params,
            )
            by_wh = [(str(name), int(n)) for name, n in cur.fetchall()]
            cur.execute(f"SELECT {_ITEM_COLUMNS} {where} ORDER BY {order_by} LIMIT %(limit)s",  # noqa: S608
                        {**params, "limit": limit})
            items = [StuckRow(*row) for row in cur.fetchall()]
        return StuckBucket(sum(n for _, n in by_wh), by_wh, items)

    def not_sent(self, *, cutoff: datetime, floor: datetime, limit: int) -> StuckBucket:
        return self._bucket(_NOT_SENT_WHERE, {"cutoff": cutoff, "floor": floor}, "i.inserted_at, i.id", limit)

    def not_delivered(self, *, cutoff: datetime, floor: datetime, limit: int) -> StuckBucket:
        return self._bucket(_NOT_DELIVERED_WHERE, {"cutoff": cutoff, "floor": floor}, "i.shipped_at, i.id", limit)

    def matched_orders(self, *, since: datetime, names: list[str], limit: int) -> list[OrderDeliveryRow]:
        if not names:
            return []
        with connection.cursor() as cur:
            cur.execute(_MISMATCH_SQL, {"since": since, "names": names, "limit": limit})
            return [
                OrderDeliveryRow(int(r[0]), r[1] or "", int(r[2]), r[3] or "", r[4] or "", r[5] or "", r[6],
                                 list(r[7] or []))
                for r in cur.fetchall()
            ]
