import django.contrib.postgres.fields
from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models


class Migration(migrations.Migration):
    """Mã hoá đơn cũ giữ lại khi KiotViet huỷ-xuất lại hoá đơn (`X` → `X.01`).

    ADD COLUMN nullable-with-default là metadata-only trên Postgres 11+ (không rewrite
    bảng), nhưng vẫn cần ACCESS EXCLUSIVE trong chốc lát: `lock_timeout` để fail nhanh
    thay vì xếp hàng chặn cả bảng `orders` sau một query dài (audit 2026-07-14).
    Index GIN phục vụ lookup `reattached_invoice_codes__contains` ở webhook/import.
    """

    atomic = False

    dependencies = [
        ("orders", "0104_order_inv_status_cancel_idx"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="order",
                    name="reattached_invoice_codes",
                    field=django.contrib.postgres.fields.ArrayField(
                        base_field=models.CharField(max_length=100),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "SET LOCAL lock_timeout = '5s';"
                        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS "
                        "reattached_invoice_codes varchar(100)[] NOT NULL DEFAULT '{}';"
                    ),
                    reverse_sql=(
                        "SET LOCAL lock_timeout = '5s';"
                        "ALTER TABLE orders DROP COLUMN IF EXISTS reattached_invoice_codes;"
                    ),
                ),
            ],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="order",
                    index=GinIndex(
                        fields=["reattached_invoice_codes"],
                        name="ord_reattached_codes_gin",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ord_reattached_codes_gin "
                        "ON orders USING gin (reattached_invoice_codes);"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS ord_reattached_codes_gin;",
                ),
            ],
        ),
    ]
