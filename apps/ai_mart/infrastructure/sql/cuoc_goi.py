"""1 dòng/cuộc gọi. Quy tắc chiều gọi chép từ export_call_transcript.py:76:
'1' = Gọi ra, còn lại (không rỗng) = Gọi vào. Tóm tắt/nhu cầu AI lấy từ
extract_payload->'clean' (đường dẫn JSON đã đo trong spec dwh §0.4)."""

from apps.analytics.sql.tz import BUSINESS_TZ
from apps.ai_mart.infrastructure.sql.base import MartTable

_CANDIDATES = "SELECT r.id FROM callcenter_call_records r WHERE r.dw_updated_at > %(cutoff)s"


def _select(where: str) -> str:
    return f"""
WITH call_scores AS (
    SELECT DISTINCT ON (s.call_record_id) s.call_record_id, s.total_score
    FROM callcenter_call_quality_scores s
    ORDER BY s.call_record_id, s.created_at DESC, s.id DESC
)
SELECT
    r.id AS cuoc_goi_id,
    r.order_id AS don_id,
    NULLIF(btrim(coalesce(o.order_code, '')), '') AS ma_don,
    o.customer_id AS khach_id,
    r.start_time AS bat_dau,
    (r.start_time AT TIME ZONE '{BUSINESS_TZ}')::date AS bat_dau_ngay,
    r.acd_time AS thoi_luong_giay,
    NULLIF(btrim(coalesce(r.call_type, '')), '') AS loai_cuoc_goi_ma,
    CASE
        WHEN NULLIF(btrim(coalesce(r.call_type, '')), '') IS NULL THEN NULL
        WHEN r.call_type = '1' THEN 'Gọi ra'
        ELSE 'Gọi vào'
    END AS loai_cuoc_goi_text,
    NULLIF(btrim(coalesce(r.agent_name, '')), '') AS agent_ten,
    ai.nhan('trang_thai_boc_bang', r.transcribe_status::text) AS trang_thai_boc_bang_text,
    NULLIF(btrim(coalesce(r.transcript, '')), '') AS transcript,
    NULLIF(btrim(coalesce(r.extract_payload #>> '{{clean,call_summary}}', '')), '')
        AS tom_tat_ai,
    coalesce(
        ARRAY(SELECT jsonb_array_elements_text(
            CASE jsonb_typeof(r.extract_payload #> '{{clean,need_type_codes}}')
                WHEN 'array' THEN r.extract_payload #> '{{clean,need_type_codes}}'
                ELSE '[]'::jsonb END)),
        '{{}}') AS nhu_cau_ai,
    cs.total_score AS diem_chat_luong,
    ai.che_sdt(coalesce(NULLIF(btrim(r.caller_normalized), ''), r.caller)) AS sdt_che,
    now() AS dong_bo_luc
FROM callcenter_call_records r
LEFT JOIN orders o ON o.id = r.order_id
LEFT JOIN call_scores cs ON cs.call_record_id = r.id
WHERE {where}
"""


MART = MartTable(
    name="ai.cuoc_goi",
    key_column="cuoc_goi_id",
    source_key="r.id",
    group="hot",
    select_sql=_select,
    candidate_ids_sql=_CANDIDATES,
)
