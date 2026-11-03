"""Search khách hàng: codify 2 index trgm drift + drop 2 index b-tree chết.

Audit + đo pg_stat_statements 2026-08-19:
- Ô tìm kiếm khách (repositories.py) dùng `icontains` → UPPER(col) LIKE → seq-scan
  756k dòng, 352 calls × 2,3s. Đổi sang `__ilike_contains` (ILIKE, khớp trgm).
- `cust_fullname_trgm_idx` + `cust_code_trgm_idx` TỒN TẠI TRÊN PROD (18MB/37MB)
  nhưng chưa từng có trong code — drift tạo tay. Migration này codify lại:
  CREATE ... IF NOT EXISTS nên prod no-op, env mới thì tạo thật.
- `customer_phone_raw_idx` (33MB) + `customer_code_phone_idx` (63MB): 0 scans,
  đã xác minh không còn call site (mọi exact-match đi qua phone_normalized;
  lookup theo code đã có customer_code_phone_raw_idx cùng prefix).

CONCURRENTLY → atomic=False; fail giữa chừng để lại index INVALID — dọn theo
docs/runbooks/2026-07-26-drop-dead-indexes.md.
"""

import django.contrib.postgres.indexes
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("customers", "0034_customers_fillfactor"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveIndex(model_name="customer", name="customer_phone_raw_idx"),
                migrations.RemoveIndex(model_name="customer", name="customer_code_phone_idx"),
                migrations.AddIndex(
                    model_name="customer",
                    index=django.contrib.postgres.indexes.GinIndex(
                        fields=["full_name"],
                        name="cust_fullname_trgm_idx",
                        opclasses=["gin_trgm_ops"],
                    ),
                ),
                migrations.AddIndex(
                    model_name="customer",
                    index=django.contrib.postgres.indexes.GinIndex(
                        fields=["code"],
                        name="cust_code_trgm_idx",
                        opclasses=["gin_trgm_ops"],
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="DROP INDEX CONCURRENTLY IF EXISTS customer_phone_raw_idx;",
                    reverse_sql="""
                        CREATE INDEX CONCURRENTLY IF NOT EXISTS customer_phone_raw_idx
                        ON customers (phone_raw);
                    """,
                ),
                migrations.RunSQL(
                    sql="DROP INDEX CONCURRENTLY IF EXISTS customer_code_phone_idx;",
                    reverse_sql="""
                        CREATE INDEX CONCURRENTLY IF NOT EXISTS customer_code_phone_idx
                        ON customers (code, phone_normalized);
                    """,
                ),
                migrations.RunSQL(
                    sql="""
                        CREATE INDEX CONCURRENTLY IF NOT EXISTS cust_fullname_trgm_idx
                        ON customers USING gin (full_name gin_trgm_ops);
                    """,
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS cust_fullname_trgm_idx;",
                ),
                migrations.RunSQL(
                    sql="""
                        CREATE INDEX CONCURRENTLY IF NOT EXISTS cust_code_trgm_idx
                        ON customers USING gin (code gin_trgm_ops);
                    """,
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS cust_code_trgm_idx;",
                ),
            ],
        ),
    ]
