"""Matview 1 dòng/đơn — bảng rộng phục vụ tab Đơn hàng và phân tích theo thời gian.

`is_gtc` là CỜ, không phải bộ lọc: mart giữ mọi đơn nội bộ (kể cả huỷ) để tab
Đơn hàng xem được đơn huỷ, còn mọi phép tính doanh thu lọc `WHERE is_gtc`.
Đơn mirror (`order_source IN ('foxia','cnv')`) bị loại hẳn — chúng là bản sao.
"""

from django.db import migrations

from apps.analytics.sql.gtc import gtc_where
from apps.analytics.sql.tz import BUSINESS_TZ
from apps.common.order_scope import sql_exclude_mirror_orders

FORWARD_SQL = f"""
CREATE MATERIALIZED VIEW IF NOT EXISTS dwh.fct_order_enriched AS
WITH items AS (
    SELECT oi.order_id,
           count(*)::int AS item_count,
           array_remove(array_agg(oi.sku), NULL) AS product_skus,
           array_remove(array_agg(oi.product_name), NULL) AS product_names,
           (array_agg(oi.sku ORDER BY oi.line_total DESC NULLS LAST))[1] AS primary_sku
    FROM order_items oi
    GROUP BY oi.order_id
),
-- CallQualityScore.call_record là FK (unique thật là call_record+rubric_version),
-- KHÔNG phải OneToOne: 1 cuộc gọi có thể được chấm lại dưới rubric mới, sinh
-- N dòng điểm cho 1 call record. Rút gọn còn đúng 1 dòng/call_record TRƯỚC khi
-- JOIN, nếu không count(*)/sum(acd_time) ở CTE `calls` bên dưới sẽ nhân theo số
-- lần chấm lại. Lấy lần chấm GẦN NHẤT (created_at DESC, id DESC): chấm lại dưới
-- rubric mới thay thế kết quả cũ, không phải để chọn điểm cao nhất.
call_scores AS (
    SELECT DISTINCT ON (s.call_record_id)
           s.call_record_id, s.total_score
    FROM callcenter_call_quality_scores s
    ORDER BY s.call_record_id, s.created_at DESC, s.id DESC
),
calls AS (
    SELECT r.order_id,
           count(*)::int AS call_count,
           coalesce(sum(r.acd_time), 0)::int AS call_total_seconds,
           bool_or(NULLIF(btrim(coalesce(r.transcript, '')), '') IS NOT NULL)
               AS has_transcript,
           bool_or(r.extract_payload IS NOT NULL
                   AND r.extract_payload <> '{{}}'::jsonb) AS ai_extracted,
           -- Cùng luật với call_scores ở trên nhưng một tầng cao hơn: đơn có
           -- NHIỀU cuộc gọi thì lấy điểm của cuộc gọi GẦN NHẤT
           -- (r.start_time DESC, r.id DESC), KHÔNG PHẢI max(total_score).
           -- max() thổi điểm đẹp cho agent nếu cuộc gọi cũ từng được 90 rồi
           -- cuộc gọi sau chỉ 30 — "gần nhất" mới phản ánh chất lượng hiện tại.
           -- FILTER loại NULL: nếu cuộc gọi gần nhất CHƯA được chấm, lấy điểm
           -- của cuộc gọi gần nhất KẾ TIẾP đã được chấm, thay vì trả NULL oan
           -- trong khi đơn vẫn có điểm cũ hợp lệ.
           (array_agg(cs.total_score ORDER BY r.start_time DESC NULLS LAST, r.id DESC)
               FILTER (WHERE cs.total_score IS NOT NULL))[1] AS quality_total_score
    FROM callcenter_call_records r
    LEFT JOIN call_scores cs ON cs.call_record_id = r.id
    WHERE r.order_id IS NOT NULL
    GROUP BY r.order_id
),
needs AS (
    SELECT oa.order_id,
           array_agg(DISTINCT nt.code) AS need_codes,
           array_remove(array_agg(DISTINCT ng.code), NULL) AS need_group_codes
    FROM order_assessments oa
    JOIN order_needs onn ON onn.order_assessment_id = oa.id
    JOIN need_types nt ON nt.id = onn.need_type_id
    LEFT JOIN need_groups ng ON ng.id = nt.group_id
    GROUP BY oa.order_id
)
SELECT
    o.id AS order_id,
    o.order_code,
    o.created_at,
    (o.created_at AT TIME ZONE '{BUSINESS_TZ}')::date AS created_date,
    o.status,
    o.invoice_status,
    ({gtc_where("o")}) AS is_gtc,
    o.order_type,
    o.order_source,
    o.shipping_type,
    o.payment_method,
    o.total_after_discount,
    o.discount_amount,
    o.shipping_fee,
    o.quantity,
    o.customer_id,
    c.code AS customer_code,
    c.gender,
    c.is_new_customer,
    css.segment_code,
    cat.name AS segment_name,
    o.channel_id,
    ch.name AS channel_name,
    o.channel_owner,
    o.staff_id,
    nullif(btrim(concat_ws(' ', u.last_name, u.first_name)), '') AS staff_name,
    o.fb_ad_id,
    o.province,
    o.district,
    coalesce(i.item_count, 0) AS item_count,
    coalesce(i.product_skus, '{{}}') AS product_skus,
    coalesce(i.product_names, '{{}}') AS product_names,
    i.primary_sku,
    oa.sales_status_code,
    oa.not_closed_reason_code,
    oa.delivery_business_status_code,
    oa.bomb_reason_group_code,
    oa.customer_classification_code,
    NULLIF(btrim(coalesce(oa.age_text, '')), '') AS age_text,
    CASE
        WHEN NULLIF(btrim(coalesce(oa.age_text, '')), '') IS NULL THEN NULL
        WHEN coalesce(oa.ai_filled_fields, '{{}}'::jsonb) ? 'age_text' THEN 'ai'
        ELSE 'declared'
    END AS age_text_src,
    NULLIF(btrim(coalesce(oa.occupation_code, '')), '') AS occupation_code,
    NULLIF(btrim(coalesce(oa.pain_level_code, '')), '') AS pain_level_code,
    coalesce(n.need_codes, '{{}}') AS need_codes,
    coalesce(n.need_group_codes, '{{}}') AS need_group_codes,
    coalesce(cl.call_count, 0) AS call_count,
    coalesce(cl.call_total_seconds, 0) AS call_total_seconds,
    coalesce(cl.has_transcript, false) AS has_transcript,
    coalesce(cl.ai_extracted, false) AS ai_extracted,
    cl.quality_total_score
FROM orders o
LEFT JOIN customers c ON c.id = o.customer_id
LEFT JOIN customer_segment_snapshot css ON css.customer_id = o.customer_id
LEFT JOIN customer_segment_categories cat ON cat.code = css.segment_code
LEFT JOIN channels ch ON ch.id = o.channel_id
LEFT JOIN users u ON u.id = o.staff_id
LEFT JOIN order_assessments oa ON oa.order_id = o.id
LEFT JOIN items i ON i.order_id = o.id
LEFT JOIN calls cl ON cl.order_id = o.id
LEFT JOIN needs n ON n.order_id = o.id
WHERE o.deleted_at IS NULL
  AND {sql_exclude_mirror_orders("o")}
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS fct_order_enriched_pk
    ON dwh.fct_order_enriched (order_id);
CREATE INDEX IF NOT EXISTS fct_order_enriched_created_date_idx
    ON dwh.fct_order_enriched (created_date);
CREATE INDEX IF NOT EXISTS fct_order_enriched_customer_idx
    ON dwh.fct_order_enriched (customer_id);
CREATE INDEX IF NOT EXISTS fct_order_enriched_gtc_date_idx
    ON dwh.fct_order_enriched (created_date) WHERE is_gtc;
"""

REVERSE_SQL = "DROP MATERIALIZED VIEW IF EXISTS dwh.fct_order_enriched;"


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0002_dim_customer_profile"),
        ("callcenter", "0003_callqualityreview_callqualityscore_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)
            ]
        )
    ]
