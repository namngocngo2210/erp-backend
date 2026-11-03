"""Cột + trigger watermark cho ETL tăng dần (spec §5.1, quyết định Q6/Q8).

KHÔNG backfill, KHÔNG DEFAULT now() (DEFAULT sẽ rewrite cả bảng 547k dòng).
NULL = "chưa bị chạm từ khi cài trigger" — hợp lệ, initial load là full SELECT.

5 bảng — thiếu order_items là ai.dong_don_hang trễ tới rebuild đêm khi sync
KiotViet sửa item mà không chạm orders (spec §5.1).
"""

from django.db import migrations

TABLES = (
    "orders",
    "order_items",
    "customers",
    "order_assessments",
    "callcenter_call_records",
)

FORWARD = ["""
CREATE OR REPLACE FUNCTION public.ai_mart_touch_dw() RETURNS trigger AS $$
BEGIN
    NEW.dw_updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""]
REVERSE = []

for t in TABLES:
    FORWARD.append(
        f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS dw_updated_at timestamptz;"
    )
    FORWARD.append(f"""
DROP TRIGGER IF EXISTS {t}_dw_touch ON {t};
CREATE TRIGGER {t}_dw_touch BEFORE INSERT OR UPDATE ON {t}
    FOR EACH ROW EXECUTE FUNCTION public.ai_mart_touch_dw();
""")
    FORWARD.append(
        f"CREATE INDEX IF NOT EXISTS {t}_dw_updated_idx ON {t} (dw_updated_at)"
        " WHERE dw_updated_at IS NOT NULL;"
    )
    REVERSE.append(f"DROP TRIGGER IF EXISTS {t}_dw_touch ON {t};")
    REVERSE.append(f"DROP INDEX IF EXISTS {t}_dw_updated_idx;")
    REVERSE.append(f"ALTER TABLE {t} DROP COLUMN IF EXISTS dw_updated_at;")

REVERSE.append("DROP FUNCTION IF EXISTS public.ai_mart_touch_dw();")


class Migration(migrations.Migration):
    dependencies = [
        ("ai_mart", "0001_schemas"),
        ("orders", "0139_order_sales_closed_at_indexes"),
        ("customers", "0036_drop_report_cache_snapshots"),
        ("callcenter", "0003_callqualityreview_callqualityscore_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql="\n".join(FORWARD), reverse_sql="\n".join(REVERSE))
            ]
        )
    ]
