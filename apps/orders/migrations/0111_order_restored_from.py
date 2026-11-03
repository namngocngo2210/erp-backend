import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Liên kết đơn khôi phục → đơn hủy gốc (`Order.restored_from`).

    ADD COLUMN nullable không default là metadata-only trên Postgres 11+, nhưng
    vẫn cần ACCESS EXCLUSIVE trong chốc lát → `lock_timeout` để fail nhanh thay
    vì xếp hàng chặn cả bảng `orders`. Index tạo CONCURRENTLY. FK thêm dạng
    NOT VALID rồi VALIDATE riêng để không scan toàn bảng khi đang giữ lock.
    """

    atomic = False

    dependencies = [
        ("orders", "0110_backfill_age_group"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="order",
                    name="restored_from",
                    field=models.ForeignKey(
                        blank=True,
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="restored_orders",
                        to="orders.order",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "SET LOCAL lock_timeout = '5s';"
                        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS restored_from_id bigint NULL;"
                    ),
                    reverse_sql=(
                        "SET LOCAL lock_timeout = '5s';"
                        "ALTER TABLE orders DROP COLUMN IF EXISTS restored_from_id;"
                    ),
                ),
            ],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="order",
                    index=models.Index(
                        condition=models.Q(("restored_from__isnull", False)),
                        fields=["restored_from"],
                        name="ord_restored_from_idx",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ord_restored_from_idx "
                        "ON orders (restored_from_id) WHERE restored_from_id IS NOT NULL;"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS ord_restored_from_idx;",
                ),
            ],
        ),
        migrations.RunSQL(
            sql=(
                "SET LOCAL lock_timeout = '5s';"
                "DO $$ BEGIN "
                "IF NOT EXISTS ("
                "SELECT 1 FROM pg_constraint WHERE conname = 'orders_restored_from_id_fk'"
                ") THEN "
                "ALTER TABLE orders ADD CONSTRAINT orders_restored_from_id_fk "
                "FOREIGN KEY (restored_from_id) REFERENCES orders(id) "
                "DEFERRABLE INITIALLY DEFERRED NOT VALID; "
                "END IF; "
                "END $$;"
            ),
            reverse_sql="ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_restored_from_id_fk;",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE orders VALIDATE CONSTRAINT orders_restored_from_id_fk;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
