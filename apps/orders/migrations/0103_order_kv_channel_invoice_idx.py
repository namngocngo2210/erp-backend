from django.db import migrations, models


class Migration(migrations.Migration):
    # CONCURRENTLY (atomic=False) để không khóa bảng orders (~500k dòng) khi deploy.
    # returns_sync_service._find_order lọc theo (kiotviet_channel, invoice_id) mỗi lần
    # đồng bộ trạng thái trả hàng — audit followup 2026-07 mục 4.1. PHẢI land trước
    # khi chạy backfill returns.
    atomic = False

    dependencies = [
        ('orders', '0102_kship_sender_address'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name='order',
                    index=models.Index(
                        condition=models.Q(('deleted_at__isnull', True), ('invoice_id__isnull', False)),
                        fields=['kiotviet_channel', 'invoice_id'],
                        name='ord_kv_channel_invoice_idx',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ord_kv_channel_invoice_idx "
                        "ON orders (kiotviet_channel_id, invoice_id) "
                        "WHERE invoice_id IS NOT NULL AND deleted_at IS NULL;"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS ord_kv_channel_invoice_idx;",
                ),
            ],
        ),
    ]
