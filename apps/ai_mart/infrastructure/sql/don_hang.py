"""Transform ai.don_hang — 1 dòng/đơn nội bộ. Cấu trúc CTE mượn từ
analytics/0003_fct_order_enriched (call_scores/calls/items/needs đã được
soi kỹ ở đó — giữ nguyên các quyết định: điểm cuộc gọi GẦN NHẤT chứ không
max; DISTINCT ON trước khi JOIN để không nhân dòng theo lần chấm lại)."""

from apps.ai_mart.infrastructure.sql.base import MartTable
from apps.analytics.sql.gtc import gtc_where
from apps.analytics.sql.tz import BUSINESS_TZ
from apps.common.order_scope import sql_exclude_mirror_orders
from apps.orders.models import INVOICE_CODE_SOURCE_PREFIXES, OrderSource
from apps.orders.reporting.shared import FACEBOOK_CHANNEL_CODE_PREFIX, WEB_CHANNEL_CODE

# Đơn gốc bị huỷ mà có bản khôi phục còn sống: loại, cùng luật summary_overview
# (overview.py) — bản sao kế thừa created_at nên gốc + sao rơi cùng kỳ báo cáo.
_SCOPE = (
    f"o.deleted_at IS NULL AND {sql_exclude_mirror_orders('o')}"
    " AND NOT EXISTS (SELECT 1 FROM orders r"
    "                 WHERE r.restored_from_id = o.id AND r.deleted_at IS NULL)"
)

_CANDIDATES = """
SELECT o.id FROM orders o WHERE o.dw_updated_at > %(cutoff)s
UNION
SELECT oa.order_id FROM order_assessments oa WHERE oa.dw_updated_at > %(cutoff)s
UNION
SELECT oi.order_id FROM order_items oi WHERE oi.dw_updated_at > %(cutoff)s
UNION
SELECT r.order_id FROM callcenter_call_records r
 WHERE r.dw_updated_at > %(cutoff)s AND r.order_id IS NOT NULL
UNION
SELECT r.restored_from_id FROM orders r
 WHERE r.dw_updated_at > %(cutoff)s AND r.restored_from_id IS NOT NULL
"""


def _marketplace_cond(source: str) -> str:
    """order_source = sàn HOẶC mã hoá đơn mang prefix của sàn (chép _marketplace_order_q)."""
    conds = [f"o.order_source = '{source}'"]
    for prefix, prefix_source in INVOICE_CODE_SOURCE_PREFIXES.items():
        if prefix_source == source:
            conds.append(f"left(coalesce(o.invoice_code, ''), {len(prefix)}) = '{prefix}'")
    return " OR ".join(conds)


# Thứ tự nhánh là luật nghiệp vụ (SALE_CHANNEL_CASE_BRANCHES): sàn trước, kênh sau.
_NHOM_KENH_BAN = f"""CASE
        WHEN {_marketplace_cond(OrderSource.SHOPEE.value)} THEN 'shopee'
        WHEN {_marketplace_cond(OrderSource.TIKTOK.value)} THEN 'tiktok'
        WHEN left(lower(coalesce(ch.code, '')), {len(FACEBOOK_CHANNEL_CODE_PREFIX)})
             = '{FACEBOOK_CHANNEL_CODE_PREFIX}' THEN 'facebook'
        WHEN lower(coalesce(ch.code, '')) = '{WEB_CHANNEL_CODE}' THEN 'web'
        ELSE 'off_other'
    END"""


def _select(where: str) -> str:
    return f"""
WITH items AS (
    SELECT oi.order_id,
           count(*)::int AS so_dong_sp,
           array_remove(
               array_agg(NULLIF(btrim(coalesce(oi.sku, '')), '')), NULL
           ) AS skus,
           (array_agg(NULLIF(btrim(coalesce(oi.sku, '')), '')
               ORDER BY (NULLIF(btrim(coalesce(oi.sku, '')), '') IS NULL),
                        oi.line_total DESC NULLS LAST))[1] AS sku_chinh
    FROM order_items oi GROUP BY oi.order_id
),
call_scores AS (
    SELECT DISTINCT ON (s.call_record_id) s.call_record_id, s.total_score
    FROM callcenter_call_quality_scores s
    ORDER BY s.call_record_id, s.created_at DESC, s.id DESC
),
calls AS (
    SELECT r.order_id,
           count(*)::int AS so_cuoc_goi,
           coalesce(sum(r.acd_time), 0)::int AS tong_giay_goi,
           bool_or(NULLIF(btrim(coalesce(r.transcript, '')), '') IS NOT NULL) AS co_transcript,
           bool_or(r.extract_payload IS NOT NULL
                   AND r.extract_payload <> '{{}}'::jsonb) AS da_boc_ai,
           (array_agg(cs.total_score ORDER BY r.start_time DESC NULLS LAST, r.id DESC)
               FILTER (WHERE cs.total_score IS NOT NULL))[1] AS diem_chat_luong
    FROM callcenter_call_records r
    LEFT JOIN call_scores cs ON cs.call_record_id = r.id
    WHERE r.order_id IS NOT NULL
    GROUP BY r.order_id
),
needs AS (
    SELECT oa.order_id,
           array_agg(DISTINCT nt.code) AS nhu_cau_ma,
           string_agg(DISTINCT nt.name_vi, ', ') AS nhu_cau_text
    FROM order_assessments oa
    JOIN order_needs onn ON onn.order_assessment_id = oa.id
    JOIN need_types nt ON nt.id = onn.need_type_id
    GROUP BY oa.order_id
)
SELECT
    o.id AS don_id,
    NULLIF(btrim(coalesce(o.order_code, '')), '') AS ma_don,
    o.created_at AS ngay_tao,
    (o.created_at AT TIME ZONE '{BUSINESS_TZ}')::date AS ngay_tao_ngay,
    o.sales_closed_at AS ngay_chot,
    (o.sales_closed_at AT TIME ZONE '{BUSINESS_TZ}')::date AS ngay_chot_ngay,
    o.status AS trang_thai_don_ma,
    ai.nhan('trang_thai_don', o.status::text) AS trang_thai_don_text,
    o.invoice_status AS trang_thai_hoa_don_ma,
    ai.nhan('trang_thai_hoa_don', o.invoice_status::text) AS trang_thai_hoa_don_text,
    ({gtc_where("o")}) AS la_don_gtc,
    NULLIF(btrim(coalesce(o.order_type, '')), '') AS loai_don_ma,
    ai.nhan('loai_don', NULLIF(btrim(coalesce(o.order_type, '')), '')) AS loai_don_text,
    NULLIF(btrim(coalesce(o.koc_order_type, '')), '') AS loai_don_koc_ma,
    ai.nhan('loai_don_koc', NULLIF(btrim(coalesce(o.koc_order_type, '')), '')) AS loai_don_koc_text,
    NULLIF(btrim(coalesce(o.order_source, '')), '') AS nguon_don_ma,
    coalesce(ai.nhan('nguon_don', NULLIF(btrim(coalesce(o.order_source, '')), '')),
             NULLIF(btrim(coalesce(o.order_source, '')), '')) AS nguon_don_text,
    o.shipping_type AS hinh_thuc_giao_ma,
    ai.nhan('hinh_thuc_giao', o.shipping_type) AS hinh_thuc_giao_text,
    NULLIF(btrim(coalesce(o.payment_method, '')), '') AS phuong_thuc_tt,
    o.channel_id AS kenh_id,
    ch.name AS kenh_ten,
    ct.name AS loai_kenh_text,
    o.staff_id AS nhan_vien_id,
    NULLIF(btrim(concat_ws(' ', u.last_name, u.first_name)), '') AS nhan_vien_ten,
    o.customer_id AS khach_id,
    c.code AS ma_khach,
    NULLIF(btrim(coalesce(c.full_name, '')), '') AS ten_khach,
    ai.che_sdt(c.phone_normalized) AS sdt_che,
    NULLIF(btrim(coalesce(o.province, '')), '') AS tinh_thanh,
    NULLIF(btrim(coalesce(o.district, '')), '') AS quan_huyen,
    c.is_new_customer AS la_khach_moi,
    (o.total_after_discount + coalesce(o.discount_amount, 0)) AS tien_hang,
    o.discount_amount AS giam_gia,
    o.shipping_fee AS phi_ship,
    o.total_after_discount AS tong_sau_giam,
    o.quantity AS so_luong,
    coalesce(i.so_dong_sp, 0) AS so_dong_sp,
    coalesce(i.skus, '{{}}') AS danh_sach_sku,
    NULLIF(array_to_string(i.skus, ', '), '') AS danh_sach_sku_text,
    i.sku_chinh,
    NULLIF(btrim(coalesce(oa.sales_status_code, '')), '') AS trang_thai_sale_ma,
    ai.nhan('trang_thai_sale', NULLIF(btrim(coalesce(oa.sales_status_code, '')), '')) AS trang_thai_sale_text,
    oa.not_closed_reason_code AS ly_do_khong_chot_ma,
    ai.nhan('ly_do_khong_chot', oa.not_closed_reason_code) AS ly_do_khong_chot_text,
    oa.delivery_business_status_code AS trang_thai_giao_nghiep_vu_ma,
    ai.nhan('trang_thai_giao_nghiep_vu', oa.delivery_business_status_code)
        AS trang_thai_giao_nghiep_vu_text,
    oa.bomb_reason_group_code AS nhom_ly_do_bom_ma,
    ai.nhan('nhom_ly_do_bom', oa.bomb_reason_group_code) AS nhom_ly_do_bom_text,
    oa.customer_classification_code AS phan_loai_khach_ma,
    ai.nhan('phan_loai_khach', oa.customer_classification_code) AS phan_loai_khach_text,
    coalesce(n.nhu_cau_ma, '{{}}') AS nhu_cau_ma,
    n.nhu_cau_text,
    ai.nhan('nhom_tuoi', NULLIF(btrim(coalesce(oa.age_text, '')), '')) AS tuoi_text,
    CASE
        WHEN NULLIF(btrim(coalesce(oa.age_text, '')), '') IS NULL THEN NULL
        WHEN coalesce(oa.ai_filled_fields, '{{}}'::jsonb) ? 'age_text'
            THEN 'AI bóc từ ghi âm'
        ELSE 'Sale nhập'
    END AS tuoi_nguon_text,
    oa.occupation_code AS nghe_nghiep_ma,
    ai.nhan('nghe_nghiep', oa.occupation_code) AS nghe_nghiep_text,
    oa.pain_level_code AS muc_do_dau_ma,
    ai.nhan('muc_do_dau', oa.pain_level_code) AS muc_do_dau_text,
    coalesce(cl.so_cuoc_goi, 0) AS so_cuoc_goi,
    coalesce(cl.tong_giay_goi, 0) AS tong_giay_goi,
    coalesce(cl.co_transcript, false) AS co_transcript,
    coalesce(cl.da_boc_ai, false) AS da_boc_ai,
    cl.diem_chat_luong,
    now() AS dong_bo_luc,
    NULLIF(btrim(coalesce(o.branch_id, '')), '') AS chi_nhanh_id,
    {_NHOM_KENH_BAN} AS nhom_kenh_ban,
    NULLIF(o.fb_campaign_id, '') AS chien_dich_fb_id,
    NULLIF(o.fb_ad_id, '') AS quang_cao_fb_id
FROM orders o
LEFT JOIN customers c ON c.id = o.customer_id
LEFT JOIN channels ch ON ch.id = o.channel_id
LEFT JOIN channel_types ct ON ct.id = ch.channel_type_id
LEFT JOIN users u ON u.id = o.staff_id
LEFT JOIN order_assessments oa ON oa.order_id = o.id
LEFT JOIN items i ON i.order_id = o.id
LEFT JOIN calls cl ON cl.order_id = o.id
LEFT JOIN needs n ON n.order_id = o.id
WHERE {_SCOPE} AND ({where})
"""


MART = MartTable(
    name="ai.don_hang",
    key_column="don_id",
    source_key="o.id",
    group="hot",
    select_sql=_select,
    candidate_ids_sql=_CANDIDATES,
)
