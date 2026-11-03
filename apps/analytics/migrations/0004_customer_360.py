"""Matview 1 dòng/khách — nguồn cho màn Hồ sơ 360 và tab Khách hàng.

Đọc từ hai matview kia nên THỨ TỰ REFRESH là bắt buộc:
dim_customer_profile → fct_order_enriched → customer_360.
Postgres không tự suy ra phụ thuộc giữa các matview.

Khách chỉ có đơn huỷ VẪN có mặt (orders_gtc = 0) — họ là tệp remarketing.
"""

from django.db import migrations

from apps.analytics.sql.tz import BUSINESS_TZ

FORWARD_SQL = f"""
CREATE MATERIALIZED VIEW IF NOT EXISTS dwh.customer_360 AS
WITH agg AS (
    SELECT
        f.customer_id,
        count(*) FILTER (WHERE f.is_gtc)::int AS orders_gtc,
        count(*)::int AS orders_all,
        count(*) FILTER (WHERE f.status = 6)::int AS orders_cancelled,
        coalesce(sum(f.total_after_discount) FILTER (WHERE f.is_gtc), 0)
            AS total_spent,
        avg(f.total_after_discount) FILTER (WHERE f.is_gtc) AS avg_order_value,
        min(f.created_at) FILTER (WHERE f.is_gtc) AS first_purchase_at,
        max(f.created_at) FILTER (WHERE f.is_gtc) AS last_purchase_at,
        array_remove(array_agg(DISTINCT f.channel_name), NULL) AS channels,
        array_remove(array_agg(DISTINCT f.province), NULL) AS provinces,
        array_remove(array_agg(DISTINCT f.primary_sku), NULL) AS top_skus,
        count(DISTINCT f.primary_sku)::int AS distinct_products,
        coalesce(sum(f.call_count), 0)::int AS call_count,
        avg(f.quality_total_score) AS avg_quality_score
    FROM dwh.fct_order_enriched f
    WHERE f.customer_id IS NOT NULL
    GROUP BY f.customer_id
),
gaps AS (
    SELECT customer_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_days) AS repurchase_gap_median_days
    FROM (
        SELECT f.customer_id,
               EXTRACT(EPOCH FROM (
                   f.created_at - lag(f.created_at) OVER (
                       PARTITION BY f.customer_id ORDER BY f.created_at
                   )
               )) / 86400.0 AS gap_days
        FROM dwh.fct_order_enriched f
        WHERE f.is_gtc AND f.customer_id IS NOT NULL
    ) p
    WHERE gap_days IS NOT NULL
    GROUP BY customer_id
)
SELECT
    a.customer_id,
    c.code,
    c.gender,
    c.created_at,
    c.is_new_customer,
    css.segment_code,
    cat.name AS segment_name,
    cat.care_priority,
    css.has_problem,
    css.latest_sales_status_code,
    a.orders_gtc,
    a.orders_all,
    a.orders_cancelled,
    a.total_spent,
    a.avg_order_value,
    a.first_purchase_at,
    a.last_purchase_at,
    -- Cả hai vế PHẢI quy về cùng một "ngày địa phương": USE_TZ=True ghim
    -- session ở UTC nên now()::date trần là ngày UTC, còn created_date của
    -- fct_order_enriched đã quy đổi qua BUSINESS_TZ. Không đổi cả hai vế thì
    -- một đơn mua 05:00 giờ VN rơi sang ngày UTC hôm trước, ra
    -- days_since_last_purchase=1 trong khi is_gtc/created_date coi là hôm nay.
    CASE
        WHEN a.last_purchase_at IS NULL THEN NULL
        ELSE (
            (now() AT TIME ZONE '{BUSINESS_TZ}')::date
            - (a.last_purchase_at AT TIME ZONE '{BUSINESS_TZ}')::date
        )
    END AS days_since_last_purchase,
    g.repurchase_gap_median_days,
    a.channels,
    a.provinces,
    a.top_skus,
    a.distinct_products,
    a.call_count,
    a.avg_quality_score,
    p.age_text, p.age_text_src,
    p.occupation_code, p.occupation_code_src,
    p.pain_level_code, p.pain_level_code_src,
    p.pain_duration_code,
    p.used_massage_before,
    p.lifestyle_habit_codes,
    p.relax_time_codes,
    p.purchase_barrier_codes,
    p.delax_source_codes,
    p.need_codes,
    p.need_group_codes,
    coalesce(p.filled_field_count, 0)::smallint AS filled_field_count,
    coalesce(p.ai_field_count, 0)::smallint AS ai_field_count
FROM agg a
LEFT JOIN customers c ON c.id = a.customer_id
LEFT JOIN customer_segment_snapshot css ON css.customer_id = a.customer_id
LEFT JOIN customer_segment_categories cat ON cat.code = css.segment_code
LEFT JOIN dwh.dim_customer_profile p ON p.customer_id = a.customer_id
LEFT JOIN gaps g ON g.customer_id = a.customer_id
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS customer_360_pk
    ON dwh.customer_360 (customer_id);
CREATE INDEX IF NOT EXISTS customer_360_segment_idx
    ON dwh.customer_360 (segment_code);
CREATE INDEX IF NOT EXISTS customer_360_last_purchase_idx
    ON dwh.customer_360 (last_purchase_at);
"""

REVERSE_SQL = "DROP MATERIALIZED VIEW IF EXISTS dwh.customer_360;"


class Migration(migrations.Migration):
    dependencies = [("analytics", "0003_fct_order_enriched")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)
            ]
        )
    ]
