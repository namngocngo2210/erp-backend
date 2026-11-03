"""Thêm `era` + chân dung suy luận từ hành vi vào `dwh.customer_360`.

Đo trên prod 20/08: 353.967 khách CHỈ mua ngành hàng cũ (trước 2025), 52.985 CHỈ
mua máy massage (hiện tại), và chỉ 485 khách mua cả hai (0,14% chuyển đổi). Gộp
hai tệp này vào một phân tích chân dung là làm loãng số liệu bằng 87% người
không phải khách của sản phẩm hiện tại. `era` tách hai quần thể này ra.

Matview phải DROP + tạo lại (không thể ALTER thêm cột vào matview), nên toàn bộ
SELECT của 0004 được lặp lại y hệt ở đây, chỉ thêm agg mới + cột suy ra ở cuối.

Khách chỉ có đơn huỷ (orders_gtc = 0) VẪN có mặt nhưng era/price_tier_lifetime/
purchase_rhythm_lifetime phải là NULL — họ chưa từng có hành vi mua thật để gán nhãn.

`price_tier_lifetime`/`purchase_rhythm_lifetime` (đổi tên trong fix wave khối 2A,
xem docs §11): cả hai gộp TOÀN BỘ đơn GTC bất kể era, hậu tố `_lifetime` gọi đúng
tên thứ chúng đo, tránh đọc nhầm là hành vi CHỈ của kỳ hiện tại.
"""

from django.db import migrations

from apps.analytics.sql.eras import (
    CURRENT_ERA_START,
    PRICE_TIER_HIGH_FLOOR,
    PRICE_TIER_MID_FLOOR,
    RECENCY_ACTIVE_MAX_DAYS,
    RECENCY_AT_RISK_MAX_DAYS,
    RECENCY_COOLING_MAX_DAYS,
)
from apps.analytics.sql.tz import BUSINESS_TZ

FORWARD_SQL = f"""
-- DROP+CREATE bên dưới chạy trong transaction của migration nên atomic — view
-- không bao giờ "biến mất" với người đọc. Rủi ro thật là DROP xếp hàng sau
-- reader đang giữ AccessShareLock trên customer_360 (vd. request coverage),
-- có thể treo tới statement_timeout mặc định (thường ~120s) chặn cả
-- migration lẫn mọi query khác chờ cùng lock. Thà fail nhanh, rollback, thử
-- lại lúc vắng người đọc.
SET lock_timeout = '5s';

DROP MATERIALIZED VIEW IF EXISTS dwh.customer_360;

CREATE MATERIALIZED VIEW dwh.customer_360 AS
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
        avg(f.quality_total_score) AS avg_quality_score,
        count(*) FILTER (WHERE f.is_gtc AND f.created_date <  date '{CURRENT_ERA_START}') AS gtc_legacy,
        count(*) FILTER (WHERE f.is_gtc AND f.created_date >= date '{CURRENT_ERA_START}') AS gtc_current,
        (array_agg(f.channel_name ORDER BY f.created_at DESC, f.order_id DESC) FILTER (WHERE f.is_gtc AND f.channel_name IS NOT NULL))[1] AS channel_affinity,
        (array_agg(f.primary_sku  ORDER BY f.created_at DESC, f.order_id DESC) FILTER (WHERE f.is_gtc AND f.primary_sku  IS NOT NULL))[1] AS product_affinity
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
    coalesce(p.ai_field_count, 0)::smallint AS ai_field_count,
    CASE
        WHEN a.orders_gtc = 0 THEN NULL
        WHEN a.gtc_legacy > 0 AND a.gtc_current > 0 THEN 'both'
        WHEN a.gtc_current > 0 THEN 'current'
        ELSE 'legacy'
    END AS era,
    -- Đổi tên có chủ đích từ price_tier/purchase_rhythm: cả hai đều gộp TOÀN
    -- BỘ đơn GTC bất kể era, trong khi `era` ngay bên cạnh lại tách legacy/
    -- current. 485 khách "both" (mua cả ngành cũ lẫn máy massage) sẽ bị gán
    -- nhãn pha trộn — vd. 20 đơn ngành cũ 100k + 1 đơn massage 2tr vẫn ra
    -- price_tier='low', trông như thể mô tả hành vi hiện tại trong khi thực
    -- chất là hành vi TRỌN ĐỜI. Hậu tố `_lifetime` gọi đúng tên thứ cột này
    -- đo — không đổi công thức, chỉ đổi tên cho trung thực.
    CASE
        WHEN a.orders_gtc = 0 THEN NULL
        WHEN a.avg_order_value >= {PRICE_TIER_HIGH_FLOOR} THEN 'high'
        WHEN a.avg_order_value >= {PRICE_TIER_MID_FLOOR} THEN 'mid'
        ELSE 'low'
    END AS price_tier_lifetime,
    CASE
        WHEN a.orders_gtc = 0 THEN NULL
        WHEN a.orders_gtc >= 2 THEN 'repeat'
        ELSE 'one_time'
    END AS purchase_rhythm_lifetime,
    -- Cả hai vế PHẢI quy về BUSINESS_TZ, giống hệt days_since_last_purchase ở
    -- trên — now()::date trần là ngày UTC dưới USE_TZ=True. Job refresh chạy
    -- 04:25 giờ VN (21:25 UTC hôm trước): now()::date trần khi đó là một
    -- ngày TRỄ hơn ngày kinh doanh, nên recency_bucket lệch 1 ngày so với
    -- days_since_last_purchase cho HẦU HẾT các dòng — một khách 91 ngày
    -- (phải là 'cooling') bị tính thành 90 ngày và gắn nhãn 'active'.
    CASE
        WHEN a.last_purchase_at IS NULL THEN NULL
        WHEN (
            (now() AT TIME ZONE '{BUSINESS_TZ}')::date
            - (a.last_purchase_at AT TIME ZONE '{BUSINESS_TZ}')::date
        ) <= {RECENCY_ACTIVE_MAX_DAYS} THEN 'active'
        WHEN (
            (now() AT TIME ZONE '{BUSINESS_TZ}')::date
            - (a.last_purchase_at AT TIME ZONE '{BUSINESS_TZ}')::date
        ) <= {RECENCY_COOLING_MAX_DAYS} THEN 'cooling'
        WHEN (
            (now() AT TIME ZONE '{BUSINESS_TZ}')::date
            - (a.last_purchase_at AT TIME ZONE '{BUSINESS_TZ}')::date
        ) <= {RECENCY_AT_RISK_MAX_DAYS} THEN 'at_risk'
        ELSE 'dormant'
    END AS recency_bucket,
    a.channel_affinity,
    a.product_affinity,
    CASE
        WHEN p.need_group_codes IS NULL OR p.need_group_codes = '{{}}' THEN NULL
        WHEN 'gift' = ANY(p.need_group_codes) AND 'buy_for_self' = ANY(p.need_group_codes) THEN 'mixed'
        WHEN 'gift' = ANY(p.need_group_codes) THEN 'gift'
        WHEN 'buy_for_self' = ANY(p.need_group_codes) THEN 'self'
        ELSE NULL
    END AS buyer_role
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
CREATE INDEX IF NOT EXISTS customer_360_era_idx
    ON dwh.customer_360 (era);
CREATE INDEX IF NOT EXISTS customer_360_recency_idx
    ON dwh.customer_360 (recency_bucket);
"""

REVERSE_SQL = f"""
-- Rollback chạy dưới áp lực production y hệt (thường CÒN TỆ HƠN — ai đó đang
-- chữa cháy) như migration xuôi, cùng rủi ro DROP xếp hàng sau reader đang
-- giữ AccessShareLock. Cùng lý do như FORWARD_SQL ở trên: fail nhanh thay vì
-- treo tới statement_timeout mặc định.
SET lock_timeout = '5s';

DROP MATERIALIZED VIEW IF EXISTS dwh.customer_360;

CREATE MATERIALIZED VIEW dwh.customer_360 AS
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


class Migration(migrations.Migration):
    dependencies = [("analytics", "0005_mart_refresh_log")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)
            ]
        )
    ]
