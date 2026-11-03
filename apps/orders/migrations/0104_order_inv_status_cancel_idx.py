from django.db import migrations, models


class Migration(migrations.Migration):
    # CONCURRENTLY (atomic=False) để không khóa bảng orders (~500k dòng) khi deploy.
    # status_counts_for / tab "Đã hủy" lọc invoice_status=2 — tập rất nhỏ so với toàn
    # bảng — audit followup 2026-07 mục 4.2.
    atomic = False

    dependencies = [
        ('orders', '0103_order_kv_channel_invoice_idx'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name='order',
                    index=models.Index(
                        condition=models.Q(('invoice_status', 2)),
                        fields=['invoice_status'],
                        name='ord_inv_status_cancel_idx',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ord_inv_status_cancel_idx "
                        "ON orders (invoice_status) WHERE invoice_status = 2;"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS ord_inv_status_cancel_idx;",
                ),
            ],
        ),
    ]
