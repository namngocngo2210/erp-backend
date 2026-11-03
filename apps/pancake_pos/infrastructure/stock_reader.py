"""Đọc kho CRM + tồn Hàng hoá theo (kho, SKU) cho ghép kho và đẩy tồn đầu kỳ
(docs/pancake-pos-two-way-sync.md §3, §7 A1).

SQL thẳng bảng `warehouses`/`stock_items`/`products` (mẫu `catalog_reader`) để không
import ORM của warehouses/inventory/catalog. Mỗi hàm = MỘT câu truy vấn, không N+1.

`stock_items` UNIQUE (warehouse, product, state) nên mỗi (kho, SKU, sellable) tối đa
một dòng — vẫn `sum` để không phụ thuộc ràng buộc đó. Bảng nhỏ (~500 dòng prod
29/09/2026), lọc `state` + `warehouse_id = ANY` quét rẻ.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.conf import settings
from django.db import connection

from apps.common.db_guards import statement_timeout
from apps.pancake_pos.domain.entities import CrmStockRow, CrmWarehouse, LinkedVariation
from apps.pancake_pos.domain.mapping import normalize_key

SELLABLE_STATE = "sellable"

_WAREHOUSES_SQL = """
SELECT w.id, w.code, w.name, w.is_active
  FROM warehouses w
 WHERE w.deleted_at IS NULL
 ORDER BY w.name, w.id
"""

_STOCK_SQL = """
SELECT s.warehouse_id, s.product_id, p.sku, sum(s.quantity)::bigint AS qty
  FROM stock_items s
  JOIN products p ON p.id = s.product_id
 WHERE s.state = %(state)s
   AND s.warehouse_id = ANY(%(warehouse_ids)s)
   {sku_filter}
 GROUP BY s.warehouse_id, s.product_id, p.sku
HAVING sum(s.quantity) <> 0
 ORDER BY p.sku, s.warehouse_id
"""

_LINKED_SQL = """
SELECT l.product_id, p.sku, l.pancake_variation_id
  FROM pancake_pos_variation_links l
  JOIN products p ON p.id = l.product_id
 WHERE l.pancake_variation_id IS NOT NULL AND l.pancake_variation_id <> ''
   {sku_filter}
 ORDER BY p.sku, l.product_id
"""

_SKU_FILTER = "AND upper(p.sku) = ANY(%(skus)s)"


def _sku_params(skus: Sequence[str] | None) -> tuple[str, dict]:
    if not skus:
        return "", {}
    return _SKU_FILTER, {"skus": sorted({normalize_key(s) for s in skus if normalize_key(s)})}


class SqlWarehouseReader:
    """Cài đặt `contracts.ports.WarehouseReader`."""

    def list_warehouses(self) -> list[CrmWarehouse]:
        with connection.cursor() as cur:
            cur.execute(_WAREHOUSES_SQL)
            rows = cur.fetchall()
        return [CrmWarehouse(id=r[0], code=r[1] or "", name=r[2] or "", is_active=bool(r[3])) for r in rows]


class SqlStockReader:
    """Cài đặt `contracts.ports.StockReader`."""

    def sellable_stock(
        self, *, warehouse_ids: Sequence[int], skus: Sequence[str] | None = None
    ) -> list[CrmStockRow]:
        ids = sorted({int(i) for i in warehouse_ids})
        if not ids:
            return []
        sku_filter, params = _sku_params(skus)
        params.update({"state": SELLABLE_STATE, "warehouse_ids": ids})
        with statement_timeout(settings.REPORT_QUERY_TIMEOUT_SECONDS), connection.cursor() as cur:
            cur.execute(_STOCK_SQL.format(sku_filter=sku_filter), params)
            rows = cur.fetchall()
        return [CrmStockRow(warehouse_id=r[0], product_id=r[1], sku=r[2] or "", quantity=int(r[3])) for r in rows]

    def linked_variations(self, *, skus: Sequence[str] | None = None) -> list[LinkedVariation]:
        sku_filter, params = _sku_params(skus)
        with connection.cursor() as cur:
            cur.execute(_LINKED_SQL.format(sku_filter=sku_filter), params)
            rows = cur.fetchall()
        return [LinkedVariation(product_id=r[0], sku=r[1] or "", pancake_variation_id=r[2]) for r in rows]
