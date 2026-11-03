"""1 dòng/(kho, SP, trạng thái tồn) — chiếu (projection) hiện tại của
stock_items, không phải sổ cái. Số liệu lịch sử/luỹ kế đọc ai.lich_su_kho."""

from apps.ai_mart.infrastructure.sql.base import MartTable


def _select(where: str) -> str:
    return f"""
SELECT
    si.id AS ton_kho_id,
    NULLIF(btrim(coalesce(w.name, '')), '') AS kho_ten,
    NULLIF(btrim(coalesce(p.sku, '')), '') AS sku,
    NULLIF(btrim(coalesce(p.name, '')), '') AS ten_sp,
    ai.nhan('trang_thai_ton', si.state) AS trang_thai_ton_text,
    si.quantity AS so_luong,
    si.updated_at AS cap_nhat_luc,
    now() AS dong_bo_luc
FROM stock_items si
JOIN warehouses w ON w.id = si.warehouse_id
JOIN products p ON p.id = si.product_id
WHERE {where}
"""


MART = MartTable(
    name="ai.ton_kho",
    key_column="ton_kho_id",
    source_key="si.id",
    group="nightly",
    select_sql=_select,
    candidate_ids_sql=None,
)
