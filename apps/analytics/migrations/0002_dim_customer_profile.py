"""Matview chân dung khách hàng — 1 dòng/khách.

Mỗi trường lấy GIÁ TRỊ NON-EMPTY MỚI NHẤT theo `orders.created_at`, kèm cột
`*_src` cho biết giá trị đó do sale nhập (`declared`) hay AI bóc từ transcript
(`ai`, suy từ `order_assessments.ai_filled_fields`).

KHÔNG lọc GTC: khách khai tuổi ở đơn bị huỷ thì tuổi đó vẫn đúng.

Tạo WITH NO DATA để `migrate` không treo trên prod — lần nạp đầu do task
`refresh_dwh_marts` làm (và lần đầu bắt buộc refresh KHÔNG CONCURRENTLY).
"""

from django.db import migrations

from apps.common.order_scope import sql_exclude_mirror_orders

# Trường vô hướng: (tên cột nguồn, tên cột đích, khoá trong ai_filled_fields)
_SCALARS = [
    ("age_text", "age_text", "age_text"),
    ("occupation_code", "occupation_code", "occupation_code"),
    ("pain_level_code", "pain_level_code", "pain_level_code"),
    ("pain_duration_code", "pain_duration_code", "pain_duration_code"),
    ("comparing_brand_text", "comparing_brand_text", "comparing_brand_text"),
]


def _latest_non_empty(src: str, dst: str) -> str:
    val = f"NULLIF(btrim(coalesce(oa.{src}, '')), '')"
    return (
        f"(array_agg({val} ORDER BY o.created_at DESC, o.id DESC)"
        f" FILTER (WHERE {val} IS NOT NULL))[1] AS {dst}"
    )


def _latest_src(src: str, dst: str, ai_key: str) -> str:
    val = f"NULLIF(btrim(coalesce(oa.{src}, '')), '')"
    expr = (
        f"CASE WHEN coalesce(oa.ai_filled_fields, '{{}}'::jsonb) ? '{ai_key}'"
        f" THEN 'ai' ELSE 'declared' END"
    )
    return (
        f"(array_agg({expr} ORDER BY o.created_at DESC, o.id DESC)"
        f" FILTER (WHERE {val} IS NOT NULL))[1] AS {dst}_src"
    )


_SCALAR_SELECT = ",\n        ".join(
    [_latest_non_empty(s, d) for s, d, _ in _SCALARS]
    + [_latest_src(s, d, k) for s, d, k in _SCALARS]
)

# Đếm trên CHÍNH cột đã merge (sr.<dst>, sr.<dst>_src) — không phải đếm lại
# trên từng phiếu rồi lấy max(). Đếm theo phiếu giàu nhất khiến bộ đếm có thể
# thấp hơn số cột NON-NULL thật sự trên dòng cuối cùng (khách có 2 phiếu, mỗi
# phiếu góp một trường khác nhau, dòng merge có 4 cột nhưng bộ đếm theo
# max() từng phiếu chỉ ra 3). Đếm trên cột merge tự động khớp 1-1 với số cột
# NON-NULL thực sự được trả về.
_FILLED_COUNT = " + ".join(
    f"(CASE WHEN sr.{d} IS NOT NULL THEN 1 ELSE 0 END)" for _, d, _ in _SCALARS
)

_AI_COUNT = " + ".join(
    f"(CASE WHEN sr.{d}_src = 'ai' THEN 1 ELSE 0 END)" for _, d, _ in _SCALARS
)

FORWARD_SQL = f"""
CREATE MATERIALIZED VIEW IF NOT EXISTS dwh.dim_customer_profile AS
WITH src AS (
    SELECT o.customer_id, oa.*
    FROM orders o
    JOIN order_assessments oa ON oa.order_id = o.id
    WHERE o.deleted_at IS NULL
      AND o.customer_id IS NOT NULL
      AND {sql_exclude_mirror_orders("o")}
),
scalars_raw AS (
    SELECT
        o.customer_id,
        {_SCALAR_SELECT},
        bool_or(oa.used_massage_before) AS used_massage_before
    FROM orders o
    JOIN order_assessments oa ON oa.order_id = o.id
    WHERE o.deleted_at IS NULL
      AND o.customer_id IS NOT NULL
      AND {sql_exclude_mirror_orders("o")}
    GROUP BY o.customer_id
),
scalars AS (
    -- filled_field_count/ai_field_count mô tả CHÍNH dòng đã merge ở trên
    -- (sr.*): filled_field_count là số trường chân dung có giá trị trên dòng
    -- này (KỂ CẢ trường do AI điền), ai_field_count là tập con nguồn AI của
    -- CHÍNH tổng đó — không phải một hạng mục đếm riêng. Nhờ vậy hai con số
    -- này LUÔN khớp với số cột non-null thật sự trả về, kể cả khi các trường
    -- được góp từ nhiều phiếu khác nhau của cùng một khách.
    SELECT
        sr.*,
        ({_FILLED_COUNT})::smallint AS filled_field_count,
        ({_AI_COUNT})::smallint AS ai_field_count
    FROM scalars_raw sr
),
habits AS (
    SELECT s.customer_id, array_agg(DISTINCT h) AS lifestyle_habit_codes
    FROM src s, LATERAL unnest(s.lifestyle_habit_codes) AS h
    GROUP BY s.customer_id
),
relax AS (
    SELECT s.customer_id, array_agg(DISTINCT r) AS relax_time_codes
    FROM src s, LATERAL unnest(s.relax_time_codes) AS r
    GROUP BY s.customer_id
),
barriers AS (
    SELECT s.customer_id, array_agg(DISTINCT b) AS purchase_barrier_codes
    FROM src s, LATERAL unnest(s.purchase_barrier_codes) AS b
    GROUP BY s.customer_id
),
sources AS (
    SELECT s.customer_id, array_agg(DISTINCT d) AS delax_source_codes
    FROM src s, LATERAL unnest(s.delax_source_codes) AS d
    GROUP BY s.customer_id
),
needs AS (
    SELECT s.customer_id,
           array_agg(DISTINCT nt.code) AS need_codes,
           array_remove(array_agg(DISTINCT ng.code), NULL) AS need_group_codes
    FROM src s
    JOIN order_needs onn ON onn.order_assessment_id = s.id
    JOIN need_types nt ON nt.id = onn.need_type_id
    LEFT JOIN need_groups ng ON ng.id = nt.group_id
    GROUP BY s.customer_id
)
SELECT
    sc.customer_id,
    sc.age_text, sc.age_text_src,
    sc.occupation_code, sc.occupation_code_src,
    sc.pain_level_code, sc.pain_level_code_src,
    sc.pain_duration_code, sc.pain_duration_code_src,
    sc.comparing_brand_text, sc.comparing_brand_text_src,
    sc.used_massage_before,
    sc.filled_field_count::smallint AS filled_field_count,
    sc.ai_field_count::smallint AS ai_field_count,
    coalesce(h.lifestyle_habit_codes, '{{}}') AS lifestyle_habit_codes,
    coalesce(r.relax_time_codes, '{{}}') AS relax_time_codes,
    coalesce(b.purchase_barrier_codes, '{{}}') AS purchase_barrier_codes,
    coalesce(d.delax_source_codes, '{{}}') AS delax_source_codes,
    coalesce(n.need_codes, '{{}}') AS need_codes,
    coalesce(n.need_group_codes, '{{}}') AS need_group_codes
FROM scalars sc
LEFT JOIN habits h ON h.customer_id = sc.customer_id
LEFT JOIN relax r ON r.customer_id = sc.customer_id
LEFT JOIN barriers b ON b.customer_id = sc.customer_id
LEFT JOIN sources d ON d.customer_id = sc.customer_id
LEFT JOIN needs n ON n.customer_id = sc.customer_id
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS dim_customer_profile_pk
    ON dwh.dim_customer_profile (customer_id);
"""

REVERSE_SQL = "DROP MATERIALIZED VIEW IF EXISTS dwh.dim_customer_profile;"


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0001_dwh_schema"),
        # RunSQL chạm bảng của các app dưới đây → chúng phải migrate xong trước,
        # nếu không test dựng DB theo thứ tự khác sẽ nổ vì bảng chưa tồn tại.
        ("orders", "0133_drop_old_id_index"),
        ("customers", "0035_search_trgm_codify_drop_dead"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)
            ]
        )
    ]
