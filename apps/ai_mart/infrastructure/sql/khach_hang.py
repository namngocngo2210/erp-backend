"""1 dòng/khách. Aggregate từ ai.don_hang — một tầng, không đọc lại orders
(khuôn customer_360 ← fct_order_enriched). Candidate: khách bị sửa TRỰC TIẾP
hoặc khách của đơn vừa đổi — cả hai đều qua dw_updated_at."""

from apps.ai_mart.infrastructure.sql.base import MartTable
from apps.orders.models import OrderStatus

CANCELLED_STATUS = OrderStatus.CANCELLED

_CANDIDATES = """
SELECT c.id FROM customers c WHERE c.dw_updated_at > %(cutoff)s
UNION
SELECT o.customer_id FROM orders o
 WHERE o.dw_updated_at > %(cutoff)s AND o.customer_id IS NOT NULL
UNION
SELECT o2.customer_id FROM order_assessments oa
 JOIN orders o2 ON o2.id = oa.order_id
 WHERE oa.dw_updated_at > %(cutoff)s AND o2.customer_id IS NOT NULL
UNION
SELECT o3.customer_id FROM callcenter_call_records r
 JOIN orders o3 ON o3.id = r.order_id
 WHERE r.dw_updated_at > %(cutoff)s AND o3.customer_id IS NOT NULL
"""


def _select(where: str) -> str:
    """Sinh SELECT cho cả full rebuild (where="TRUE") lẫn upsert theo id.

    Đường tăng dần (audit 2026-09-23 C5/P1-3): trước đây `where` chỉ lọc ở
    SELECT ngoài cùng, còn hai CTE agg/gaps vẫn aggregate + window TOÀN BỘ
    ai.don_hang (560k dòng, 3,7s + 63MB temp mỗi 30 phút — mean-time cao nhất
    DB). Nay tập khách trong scope được vật chất hoá MỘT lần ở CTE `ids`
    (MATERIALIZED để planner không inline lại `= ANY(%(ids)s)` vào từng chỗ)
    rồi đẩy vào cả agg lẫn gaps bằng `khach_id IN (SELECT id FROM ids)`.
    Kết quả không đổi: agg/gaps GROUP BY khach_id nên lọc khách trước hay
    sau khi gộp cho cùng một dòng — test_khach_hang so sánh full vs upsert.

    Full rebuild bỏ qua bộ lọc (không có gì để thu hẹp, tránh một semi-join
    768k id vô ích).
    """
    scope = f"c.deleted_at IS NULL AND ({where})"
    if where.strip() == "TRUE":
        ids_cte = ""
        id_filter = ""
    else:
        ids_cte = f"ids AS MATERIALIZED (SELECT c.id FROM customers c WHERE {scope}),\n"
        id_filter = " AND d.khach_id IN (SELECT id FROM ids)"
    return f"""
WITH {ids_cte}agg AS (
    SELECT d.khach_id,
           count(*) FILTER (WHERE d.la_don_gtc)::int AS so_don_gtc,
           count(*)::int AS so_don_tat_ca,
           count(*) FILTER (WHERE d.trang_thai_don_ma = {CANCELLED_STATUS})::int AS so_don_huy,
           coalesce(sum(d.tong_sau_giam) FILTER (WHERE d.la_don_gtc), 0) AS tong_chi_tieu,
           avg(d.tong_sau_giam) FILTER (WHERE d.la_don_gtc) AS gia_tri_don_tb,
           min(d.ngay_tao) FILTER (WHERE d.la_don_gtc) AS lan_mua_dau,
           max(d.ngay_tao) FILTER (WHERE d.la_don_gtc) AS lan_mua_cuoi,
           array_remove(array_agg(DISTINCT d.kenh_ten), NULL) AS kenh_da_mua,
           array_remove(array_agg(DISTINCT d.tinh_thanh), NULL) AS tinh_da_mua,
           array_remove(array_agg(DISTINCT d.sku_chinh), NULL) AS sku_hay_mua,
           coalesce(sum(d.so_cuoc_goi), 0)::int AS so_cuoc_goi,
           avg(d.diem_chat_luong) AS diem_chat_luong_tb
    FROM ai.don_hang d
    WHERE d.khach_id IS NOT NULL{id_filter}
    GROUP BY d.khach_id
),
gaps AS (
    SELECT khach_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_days)
               AS khoang_cach_mua_lai_trung_vi
    FROM (
        SELECT d.khach_id,
               EXTRACT(EPOCH FROM (d.ngay_tao - lag(d.ngay_tao) OVER (
                   PARTITION BY d.khach_id ORDER BY d.ngay_tao))) / 86400.0 AS gap_days
        FROM ai.don_hang d WHERE d.la_don_gtc AND d.khach_id IS NOT NULL{id_filter}
    ) t WHERE gap_days IS NOT NULL GROUP BY khach_id
)
SELECT
    c.id AS khach_id,
    NULLIF(btrim(coalesce(c.code, '')), '') AS ma_khach,
    NULLIF(btrim(coalesce(c.full_name, '')), '') AS ten_khach,
    ai.che_sdt(c.phone_normalized) AS sdt_che,
    ai.nhan('gioi_tinh', c.gender::text) AS gioi_tinh_text,
    css.segment_code AS phan_khuc_ma,
    cat.name AS phan_khuc_text,
    c.is_new_customer AS la_khach_moi,
    coalesce(a.so_don_gtc, 0) AS so_don_gtc,
    coalesce(a.so_don_tat_ca, 0) AS so_don_tat_ca,
    coalesce(a.so_don_huy, 0) AS so_don_huy,
    coalesce(a.tong_chi_tieu, 0) AS tong_chi_tieu,
    a.gia_tri_don_tb,
    a.lan_mua_dau,
    a.lan_mua_cuoi,
    CASE WHEN a.lan_mua_cuoi IS NULL THEN NULL
         ELSE floor(EXTRACT(EPOCH FROM (now() - a.lan_mua_cuoi)) / 86400)::int
    END AS so_ngay_tu_lan_mua_cuoi,
    g.khoang_cach_mua_lai_trung_vi,
    coalesce(a.kenh_da_mua, '{{}}') AS kenh_da_mua,
    coalesce(a.tinh_da_mua, '{{}}') AS tinh_da_mua,
    coalesce(a.sku_hay_mua, '{{}}') AS sku_hay_mua,
    coalesce(a.so_cuoc_goi, 0) AS so_cuoc_goi,
    a.diem_chat_luong_tb,
    now() AS dong_bo_luc
FROM customers c
LEFT JOIN customer_segment_snapshot css ON css.customer_id = c.id
LEFT JOIN customer_segment_categories cat ON cat.code = css.segment_code
LEFT JOIN agg a ON a.khach_id = c.id
LEFT JOIN gaps g ON g.khach_id = c.id
WHERE {scope}
"""


MART = MartTable(
    name="ai.khach_hang",
    key_column="khach_id",
    source_key="c.id",
    group="hot",
    select_sql=_select,
    candidate_ids_sql=_CANDIDATES,
)
