"""1 dòng/SP. la_combo suy từ product_components (catalog CỐ Ý không có cờ
is_combo — mart vật chất hoá luật đó, spec §3.2). ten_hien_thi từ
product_profiles.display_name — rỗng nghĩa là dùng ten_sp."""

from apps.ai_mart.infrastructure.sql.base import MartTable


def _select(where: str) -> str:
    return f"""
WITH combo AS (
    SELECT pc.parent_id,
           array_agg(child.sku ORDER BY child.sku) AS thanh_phan
    FROM product_components pc
    JOIN products child ON child.id = pc.component_id
    GROUP BY pc.parent_id
)
SELECT
    p.id AS san_pham_id,
    NULLIF(btrim(coalesce(p.sku, '')), '') AS sku,
    NULLIF(btrim(coalesce(p.name, '')), '') AS ten_sp,
    NULLIF(btrim(coalesce(pp.display_name, '')), '') AS ten_hien_thi,
    NULLIF(btrim(coalesce(pg.name, '')), '') AS nhom_sp_text,
    p.price AS gia_ban,
    p.is_active AS dang_kinh_doanh,
    (cb.parent_id IS NOT NULL) AS la_combo,
    coalesce(cb.thanh_phan, '{{}}') AS thanh_phan_combo,
    now() AS dong_bo_luc
FROM products p
LEFT JOIN product_groups pg ON pg.id = p.group_id
LEFT JOIN product_profiles pp ON pp.product_id = p.id
LEFT JOIN combo cb ON cb.parent_id = p.id
WHERE p.deleted_at IS NULL AND ({where})
"""


MART = MartTable(
    name="ai.san_pham",
    key_column="san_pham_id",
    source_key="p.id",
    group="nightly",
    select_sql=_select,
    candidate_ids_sql=None,
)
