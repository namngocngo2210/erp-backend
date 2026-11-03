"""1 dòng/sản phẩm trong đơn. key_column = don_id (không phải dong_id): upsert
xoá TOÀN BỘ dòng của đơn rồi insert lại — item bị xoá khỏi đơn được dọn đúng."""

from apps.analytics.sql.tz import BUSINESS_TZ
from apps.ai_mart.infrastructure.sql.base import MartTable
from apps.ai_mart.infrastructure.sql.don_hang import _CANDIDATES, _SCOPE


def _select(where: str) -> str:
    return f"""
SELECT
    oi.id AS dong_id,
    o.id AS don_id,
    NULLIF(btrim(coalesce(o.order_code, '')), '') AS ma_don,
    (o.created_at AT TIME ZONE '{BUSINESS_TZ}')::date AS ngay_tao_ngay,
    NULLIF(btrim(coalesce(oi.sku, '')), '') AS sku,
    NULLIF(btrim(coalesce(oi.product_name, '')), '') AS ten_sp,
    pg.name AS nhom_sp_text,
    oi.quantity AS so_luong,
    oi.unit_price AS don_gia,
    oi.line_total AS thanh_tien
FROM order_items oi
JOIN orders o ON o.id = oi.order_id
LEFT JOIN products p ON p.id = oi.product_id
LEFT JOIN product_groups pg ON pg.id = p.group_id
WHERE {_SCOPE} AND ({where})
"""


MART = MartTable(
    name="ai.dong_don_hang",
    key_column="don_id",
    source_key="o.id",
    group="hot",
    select_sql=_select,
    candidate_ids_sql=_CANDIDATES,
)
