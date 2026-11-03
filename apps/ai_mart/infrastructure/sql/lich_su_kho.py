"""Sổ cái kho — bất biến (append-only). Nạp tăng dần theo moc_id (spec Q8):
CHỈ INSERT id mới, không DELETE. Rebuild đêm vẫn TRUNCATE+INSERT như mọi bảng,
nhưng sau đó phải tự chốt lại moc_id = max(id) (etl_service.rebuild_all()) —
nếu không, nhịp tăng dần kế tiếp sẽ cố INSERT lại đúng các dòng vừa rebuild
(PK but_toan_id sẽ chặn, nhưng đừng dựa vào lỗi đó).

`ngay` dùng occurred_at (ngày NGHIỆP VỤ), KHÔNG dùng created_at — bài học
'phiếu kho lệch ngày nghiệp vụ' (MR !1234): chứng từ ghi lùi ngày vẫn phải
hiện đúng ngày nghiệp vụ, không phải ngày ghi sổ.

MỘT nguồn duy nhất stock_movements — bài học 'hai query dựng SL song song'
(MR !1345): mọi chế độ xem đều phải đi từ cùng một câu SELECT này.

ID_OVERLAP: id của stock_movements đến từ MỘT sequence dùng chung, và
nextval() cấp id TRƯỚC khi transaction commit — một transaction lấy id THẤP
hơn có thể commit SAU khi ta đã đọc max(id) làm mốc, nên bị `sm.id > moc_id`
bỏ sót cho tới khi rebuild đêm quét lại toàn bộ. Cùng triết lý với
etl_service.OVERLAP (biên 5 phút cho watermark theo timestamp): lùi moc_id
một khoảng ID_OVERLAP để quét lại vùng có thể bị bỏ sót, và dùng
ON CONFLICT DO NOTHING để việc quét lại là vô hại (idempotent). Độ trễ
intraday còn lại bị giới hạn trong ID_OVERLAP id gần nhất — xa hơn nữa thì
rebuild đêm (TRUNCATE+INSERT toàn bộ) vẫn là lưới an toàn cuối cùng."""

from django.db import connections, transaction

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.base import MartTable
from apps.analytics.sql.tz import BUSINESS_TZ

_WM_KEY = "ai.lich_su_kho"
ID_OVERLAP = 500


def _select(where: str) -> str:
    return f"""
SELECT
    sm.id AS but_toan_id,
    (sm.occurred_at AT TIME ZONE '{BUSINESS_TZ}')::date AS ngay,
    NULLIF(btrim(coalesce(w.name, '')), '') AS kho_ten,
    NULLIF(btrim(coalesce(p.sku, '')), '') AS sku,
    NULLIF(btrim(coalesce(p.name, '')), '') AS ten_sp,
    ai.nhan('loai_bien_dong_kho', sm.movement_type) AS loai_bien_dong_text,
    ai.nhan('trang_thai_ton', sm.state) AS trang_thai_ton_text,
    sm.qty_delta AS so_luong_thay_doi,
    sm.balance_after AS ton_sau,
    ai.nhan('loai_chung_tu_kho', sm.reference_type) AS loai_chung_tu_text,
    sm.reference_id AS chung_tu_id,
    NULLIF(btrim(coalesce(o.order_code, '')), '') AS ma_don,
    NULLIF(btrim(coalesce(sm.note, '')), '') AS ghi_chu,
    now() AS dong_bo_luc
FROM stock_movements sm
JOIN warehouses w ON w.id = sm.warehouse_id
JOIN products p ON p.id = sm.product_id
LEFT JOIN orders o ON sm.reference_type = 'order' AND o.id = sm.reference_id
WHERE {where}
"""


MART = MartTable(
    name="ai.lich_su_kho",
    key_column="but_toan_id",
    source_key="sm.id",
    group="nightly",
    select_sql=_select,
    candidate_ids_sql=None,
)


def sync_incremental() -> int:
    """INSERT bút toán mới hơn (moc_id - ID_OVERLAP). Append-only nên không
    cần DELETE; ON CONFLICT DO NOTHING làm cho việc quét lại vùng overlap vô
    hại (xem ID_OVERLAP ở module docstring)."""
    moc_id = loaders.read_watermark_id(_WM_KEY)
    scan_from = max(moc_id - ID_OVERLAP, 0)
    conn = connections[loaders.TARGET_ALIAS]
    with transaction.atomic(using=loaders.TARGET_ALIAS):
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {MART.name} {_select('sm.id > %(moc_id)s')}"
                f" ON CONFLICT ({MART.key_column}) DO NOTHING",
                {"moc_id": scan_from},
            )
            inserted = cur.rowcount
            cur.execute("SELECT coalesce(max(id), %s) FROM stock_movements", [moc_id])
            loaders.write_watermark_id(_WM_KEY, cur.fetchone()[0])
    return inserted


def reset_watermark_to_max() -> None:
    """Sau full_rebuild(), chốt moc_id = max(stock_movements.id) — không thì
    sync_incremental() kế tiếp sẽ cố quét lại đúng các dòng vừa rebuild (vô hại
    nhờ ON CONFLICT DO NOTHING, nhưng lãng phí quét)."""
    conn = connections[loaders.TARGET_ALIAS]
    with conn.cursor() as cur:
        cur.execute("SELECT coalesce(max(id), 0) FROM stock_movements")
        loaders.write_watermark_id(_WM_KEY, cur.fetchone()[0])
