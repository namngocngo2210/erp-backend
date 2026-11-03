"""Đọc đơn CRM cho luồng đẩy đơn sang Pancake (two-way-sync §4.1, §7 A2).

SQL thẳng các bảng `orders` / `order_assessments` / `order_items` / `foxia_*` /
`stock_items` (mẫu `stock_reader`) để không import ORM của orders/foxia/inventory —
foxia cùng tầng với pancake_pos nên import-linter cấm import trực tiếp.

Mỗi hàm = MỘT câu truy vấn theo lô id (không N+1). Quét ứng viên chỉ nhìn cửa sổ
`sales_closed_at` / `order_assessments.updated_at >= since` (~136 đơn chốt/ngày prod
30/09/2026) và đơn Foxia theo `ord_src_created_idx`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from django.db import connection

from apps.pancake_pos.domain.order_push import (
    CLOSED_SALES_STATUSES,
    CRM_STATUS_CANCELLED,
    FOXIA_SOURCE,
    PUSHABLE_CRM_STATUSES,
    CrmOrder,
    OrderLine,
)

SELLABLE_STATE = "sellable"

_ORDERS_SQL = """
SELECT o.id, coalesce(o.order_source, ''), o.status, coalesce(a.sales_status_code, ''),
       o.deleted_at IS NOT NULL,
       CASE WHEN o.order_source = %(foxia)s THEN o.created_at
            ELSE greatest(o.sales_closed_at, a.updated_at) END,
       CASE WHEN o.order_source = %(foxia)s THEN o.created_at
            ELSE coalesce(o.sales_closed_at, a.updated_at) END,
       o.name, o.phone, coalesce(nullif(o.address, ''), o.raw_address, ''), coalesce(c.name, ''),
       o.discount_amount + o.booking_voucher_discount, o.total_after_discount, o.customer_prepaid
  FROM orders o
  LEFT JOIN order_assessments a ON a.order_id = o.id
  LEFT JOIN channels c ON c.id = o.channel_id
 WHERE o.id = ANY(%(ids)s)
"""

_ITEMS_SQL = """
SELECT oi.order_id, oi.product_id, coalesce(nullif(oi.sku, ''), p.sku, ''),
       coalesce(nullif(oi.product_name, ''), p.name, ''), oi.quantity, oi.unit_price, oi.discount,
       coalesce(l.pancake_variation_id, '')
  FROM order_items oi
  JOIN products p ON p.id = oi.product_id
  LEFT JOIN pancake_pos_variation_links l ON l.product_id = oi.product_id
 WHERE oi.order_id = ANY(%(ids)s)
 ORDER BY oi.order_id, oi.id
"""

# Cầu landing ↔ Foxia: cơ hội do CRM đẩy (`foxia_lead_links.order_id` = đơn CRM gốc) ↔
# đơn Foxia mirror kéo về (`foxia_order_links.cohoi_id`). Hai chiều trong MỘT câu.
_FOXIA_SQL = """
SELECT ll.order_id, fol.order_id, fo.deleted_at IS NULL AND fo.status <> %(cancelled)s
  FROM foxia_lead_links ll
  JOIN foxia_order_links fol ON fol.cohoi_id = ll.cohoi_id
  JOIN orders fo ON fo.id = fol.order_id
 WHERE ll.order_id = ANY(%(ids)s) OR fol.order_id = ANY(%(ids)s)
"""

# Hai nhánh UNION (mỗi nhánh một index): mốc chốt mới (`ord_sales_closed_desc_idx`)
# HOẶC đánh giá sale vừa đổi (`ord_assessment_updated_idx`). Nhánh 2 bắt đơn
# `closed_scheduled_date` → `closed` sau nhiều ngày: đi trong nội bộ nhóm chốt nên
# `sales_closed_at` KHÔNG đổi (22 đơn hẹn ngày đang mở, 2 ca/30 ngày chốt lại > 72h).
_SCAN_CRM_SQL = """
WITH recent AS (
    SELECT o.id FROM orders o WHERE o.sales_closed_at >= %(since)s
    UNION
    SELECT a.order_id FROM order_assessments a WHERE a.updated_at >= %(since)s
)
SELECT o.id
  FROM recent r
  JOIN orders o ON o.id = r.id
  JOIN order_assessments a ON a.order_id = o.id
 WHERE o.deleted_at IS NULL
   AND o.order_source = ANY(%(sources)s)
   AND o.status = ANY(%(statuses)s)
   AND a.sales_status_code = ANY(%(closed)s)
   AND NOT EXISTS (SELECT 1 FROM pancake_pos_order_links l WHERE l.order_id = o.id)
 ORDER BY greatest(o.sales_closed_at, a.updated_at), o.id
 LIMIT %(limit)s
"""

_SCAN_FOXIA_SQL = """
SELECT o.id
  FROM orders o
 WHERE o.deleted_at IS NULL
   AND o.order_source = %(foxia)s
   AND o.created_at >= %(since)s
   AND o.status = ANY(%(statuses)s)
   AND NOT EXISTS (SELECT 1 FROM pancake_pos_order_links l WHERE l.order_id = o.id)
 ORDER BY o.created_at, o.id
 LIMIT %(limit)s
"""

# Đơn đã đẩy (còn sống trên Pancake, đẩy trong cửa sổ) mà đơn/dòng hàng/đánh giá CRM
# đổi sau lần đối chiếu gần nhất. Index partial `ppos_ordlink_live_idx` (pushed_at).
_CHANGED_SQL = """
SELECT DISTINCT l.order_id
  FROM pancake_pos_order_links l
  JOIN orders o ON o.id = l.order_id
 WHERE l.status IN ('pushed', 'updated')
   AND l.pushed_at >= %(since)s
   AND (%(ids)s::bigint[] IS NULL OR l.order_id = ANY(%(ids)s))
   AND (
        l.checked_at IS NULL
        OR o.updated_at > l.checked_at
        OR EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id AND oi.updated_at > l.checked_at)
        OR EXISTS (SELECT 1 FROM order_assessments a WHERE a.order_id = o.id AND a.updated_at > l.checked_at)
   )
 LIMIT %(limit)s
"""

_COMPONENTS_SQL = """
SELECT parent_id, component_id, quantity FROM product_components WHERE parent_id = ANY(%(ids)s)
"""

_STOCK_SQL = """
SELECT warehouse_id, product_id, sum(quantity)::bigint
  FROM stock_items
 WHERE state = %(state)s AND warehouse_id = ANY(%(warehouses)s) AND product_id = ANY(%(products)s)
 GROUP BY warehouse_id, product_id
"""


def _ids(values: Sequence[int]) -> list[int]:
    return sorted({int(v) for v in values if v})


class SqlOrderReader:
    """Đọc đơn CRM + ngữ cảnh Foxia + tồn Hàng hoá cho `OrderPushService`."""

    def load_orders(self, order_ids: Sequence[int]) -> dict[int, CrmOrder]:
        ids = _ids(order_ids)
        if not ids:
            return {}
        with connection.cursor() as cur:
            cur.execute(_ORDERS_SQL, {"ids": ids, "foxia": FOXIA_SOURCE})
            order_rows = cur.fetchall()
            cur.execute(_ITEMS_SQL, {"ids": ids})
            item_rows = cur.fetchall()
            cur.execute(_FOXIA_SQL, {"ids": ids, "cancelled": CRM_STATUS_CANCELLED})
            foxia_rows = cur.fetchall()

        lines: dict[int, list[OrderLine]] = {}
        for oid, pid, sku, name, qty, price, discount, variation_id in item_rows:
            lines.setdefault(oid, []).append(OrderLine(
                product_id=pid, sku=sku, name=name, quantity=int(qty or 0),
                unit_price=Decimal(price or 0), discount=Decimal(discount or 0), variation_id=variation_id or "",
            ))
        foxia_of: dict[int, set[int]] = {}
        origin_of: dict[int, set[int]] = {}
        for origin_id, foxia_id, foxia_alive in foxia_rows:
            if foxia_alive:
                foxia_of.setdefault(origin_id, set()).add(foxia_id)
            origin_of.setdefault(foxia_id, set()).add(origin_id)

        out: dict[int, CrmOrder] = {}
        for (oid, source, status, sales_status, deleted, closed_at, first_closed_at, name, phone, address,
             channel, discount, total_after, prepaid) in order_rows:
            out[oid] = CrmOrder(
                id=oid, order_source=source, status=int(status), sales_status_code=sales_status,
                is_deleted=bool(deleted), closed_at=closed_at, first_closed_at=first_closed_at,
                name=name or "", phone=phone or "", address=address or "", channel_name=channel,
                discount_amount=Decimal(discount or 0), total_after_discount=Decimal(total_after or 0),
                customer_prepaid=Decimal(prepaid or 0),
                lines=tuple(lines.get(oid, ())),
                foxia_order_ids=tuple(sorted(foxia_of.get(oid, ()))),
                origin_order_ids=tuple(sorted(origin_of.get(oid, ()))),
            )
        return out

    def scan_candidates(self, *, sources: Sequence[str], since: datetime, limit: int) -> list[int]:
        """Đơn đã chốt (hoặc đơn Foxia) trong cửa sổ, chưa có liên kết đẩy."""
        sources = sorted({s.strip().lower() for s in sources if s.strip()})
        if not sources:
            return []
        statuses = sorted(PUSHABLE_CRM_STATUSES)
        found: list[int] = []
        with connection.cursor() as cur:
            crm_sources = [s for s in sources if s != FOXIA_SOURCE]
            if crm_sources:
                cur.execute(_SCAN_CRM_SQL, {
                    "since": since, "sources": crm_sources, "statuses": statuses,
                    "closed": sorted(CLOSED_SALES_STATUSES), "limit": limit,
                })
                found += [r[0] for r in cur.fetchall()]
            if FOXIA_SOURCE in sources and len(found) < limit:
                cur.execute(_SCAN_FOXIA_SQL, {
                    "foxia": FOXIA_SOURCE, "since": since, "statuses": statuses, "limit": limit - len(found),
                })
                found += [r[0] for r in cur.fetchall()]
        return found

    def changed_linked_orders(
        self, *, pushed_since: datetime, limit: int, order_ids: Sequence[int] | None = None
    ) -> list[int]:
        with connection.cursor() as cur:
            cur.execute(_CHANGED_SQL, {
                "since": pushed_since, "limit": limit, "ids": _ids(order_ids) if order_ids else None,
            })
            return sorted(r[0] for r in cur.fetchall())

    def components(self, product_ids: Sequence[int]) -> dict[int, list[tuple[int, int]]]:
        """Thành phần combo (1 cấp) của các sản phẩm cho trước."""
        ids = _ids(product_ids)
        if not ids:
            return {}
        with connection.cursor() as cur:
            cur.execute(_COMPONENTS_SQL, {"ids": ids})
            rows = cur.fetchall()
        out: dict[int, list[tuple[int, int]]] = {}
        for parent, component, qty in rows:
            out.setdefault(parent, []).append((component, int(qty or 0)))
        return out

    def warehouse_names(self) -> dict[int, str]:
        with connection.cursor() as cur:
            cur.execute("SELECT id, name FROM warehouses")
            return {r[0]: r[1] or "" for r in cur.fetchall()}

    def sellable_stock(self, *, warehouse_ids: Sequence[int], product_ids: Sequence[int]) -> dict[tuple[int, int], int]:
        """Tồn Hàng hoá theo (kho, sản phẩm) — chọn kho đủ hàng."""
        warehouses, products = _ids(warehouse_ids), _ids(product_ids)
        if not warehouses or not products:
            return {}
        with connection.cursor() as cur:
            cur.execute(_STOCK_SQL, {"state": SELLABLE_STATE, "warehouses": warehouses, "products": products})
            return {(r[0], r[1]): int(r[2]) for r in cur.fetchall()}
