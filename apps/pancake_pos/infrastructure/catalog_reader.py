"""Đọc SKU CRM kèm sự kiện cho luật chọn — MỘT câu SQL cho cả danh mục, không N+1.

Đọc thẳng bảng `products`/`product_groups`/`product_components`/`stock_items`/
`orders`/`order_items` bằng SQL (mẫu `conversion_feed.infrastructure.queries`) để
không import ORM của catalog/inventory/orders. Luật chọn KHÔNG nằm ở đây — chỉ
cung cấp sự kiện; quyết định ở `domain.eligibility`.

Đo prod 29/09/2026 (EXPLAIN ANALYZE read-only): CTE `sold` (đơn 180 ngày ⋈
order_items, 564k/732k dòng) chạy ~290 ms — Parallel Hash Join trên index
`order_shiptype_created_idx` + index-only `uniq_order_item_order_product`, ra 475 SP.
`stock_items` 486 dòng, `product_components` 9 dòng — EXISTS rẻ.

`products.product_type` (MR !1504) có thể CHƯA có trên DB: kiểm bằng catalog của PG
(`pg_attribute`, một lần mỗi reader) rồi mới SELECT cột; không có thì trả NULL —
nhánh linh kiện (two-way-sync §6b) tự tắt.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.db import connection

from apps.common.db_guards import statement_timeout
from apps.common.order_scope import sql_exclude_mirror_orders
from apps.pancake_pos.domain.entities import SYNC_MODE_AUTO, CatalogProduct
from apps.pancake_pos.domain.mapping import normalize_key

_SQL = f"""
WITH sold AS (
    SELECT oi.product_id, max(o.created_at) AS last_sold_at
      FROM orders o
      JOIN order_items oi ON oi.order_id = o.id
     WHERE o.deleted_at IS NULL
       AND o.created_at >= %(sold_since)s
       AND {sql_exclude_mirror_orders("o")}
     GROUP BY oi.product_id
)
SELECT p.id, p.sku, p.name, p.price, p.weight, p.length, p.width, p.height,
       p.is_active, p.pancake_sync_mode, {{product_type}} AS product_type,
       g.code AS group_code,
       EXISTS (SELECT 1 FROM product_components c WHERE c.parent_id = p.id) AS is_combo,
       EXISTS (SELECT 1 FROM stock_items s WHERE s.product_id = p.id AND s.quantity <> 0) AS has_stock,
       sold.last_sold_at, p.short_name
  FROM products p
  LEFT JOIN product_groups g ON g.id = p.group_id AND g.deleted_at IS NULL
  LEFT JOIN sold ON sold.product_id = p.id
 WHERE p.deleted_at IS NULL
   {{sku_filter}}
 ORDER BY p.id
"""

# Chọn theo SKU thì kéo CẢ nhóm của SKU đó: nhóm là đơn vị đồng bộ (một sản phẩm
# Pancake), PUT thiếu mẫu mã anh em là rủi ro chưa kiểm chứng (§7).
_SKU_FILTER = """
   AND (
        upper(p.sku) = ANY(%(skus)s)
        OR p.group_id IN (
            SELECT q.group_id FROM products q
             WHERE q.deleted_at IS NULL AND q.group_id IS NOT NULL AND upper(q.sku) = ANY(%(skus)s)
        )
   )
"""


# Đồng bộ tăng dần theo id: id đã xoá mềm vẫn kéo nhóm của nó (anh em cần cập nhật,
# chính nó thì service ẩn trên Pancake — §3.5).
_ID_FILTER = """
   AND (
        p.id = ANY(%(ids)s)
        OR p.group_id IN (
            SELECT q.group_id FROM products q WHERE q.group_id IS NOT NULL AND q.id = ANY(%(ids)s)
        )
   )
"""


_HAS_PRODUCT_TYPE_SQL = """
SELECT EXISTS (
    SELECT 1 FROM pg_attribute
     WHERE attrelid = to_regclass('products') AND attname = 'product_type'
       AND attnum > 0 AND NOT attisdropped
)
"""


def products_have_product_type() -> bool:
    with connection.cursor() as cur:
        cur.execute(_HAS_PRODUCT_TYPE_SQL)
        return bool(cur.fetchone()[0])


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


class SqlCatalogReader:
    """Cài đặt `contracts.ports.CatalogReader`."""

    def __init__(self) -> None:
        self._has_product_type: bool | None = None

    def has_product_type(self) -> bool:
        if self._has_product_type is None:
            self._has_product_type = products_have_product_type()
        return self._has_product_type

    def list_candidates(
        self,
        *,
        sold_since: datetime,
        skus: Sequence[str] | None = None,
        product_ids: Sequence[int] | None = None,
    ) -> list[CatalogProduct]:
        params: dict = {"sold_since": sold_since}
        sku_filter = ""
        if skus:
            params["skus"] = sorted({normalize_key(s) for s in skus if normalize_key(s)})
            sku_filter = _SKU_FILTER
        if product_ids is not None:
            params["ids"] = sorted({int(i) for i in product_ids})
            sku_filter += _ID_FILTER
        with statement_timeout(settings.REPORT_QUERY_TIMEOUT_SECONDS), connection.cursor() as cur:
            product_type = "p.product_type" if self.has_product_type() else "NULL::varchar"
            cur.execute(_SQL.format(sku_filter=sku_filter, product_type=product_type), params)
            rows = cur.fetchall()
        return [
            CatalogProduct(
                id=row[0],
                sku=row[1] or "",
                name=row[2] or "",
                price=_dec(row[3]),
                weight=_dec(row[4]),
                length=_dec(row[5]),
                width=_dec(row[6]),
                height=_dec(row[7]),
                is_active=bool(row[8]),
                sync_mode=row[9] or SYNC_MODE_AUTO,
                product_type=row[10],
                group_code=row[11],
                is_combo=bool(row[12]),
                has_stock=bool(row[13]),
                last_sold_at=row[14],
                short_name=row[15] or "",
            )
            for row in rows
        ]
