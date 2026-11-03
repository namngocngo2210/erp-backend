from django.db import migrations, models


class Migration(migrations.Migration):
    # CONCURRENTLY (atomic=False) để không khóa bảng orders khi deploy.
    # Partial index cho bộ lọc "Không có địa chỉ" — tập has_address=false rất nhỏ,
    # index theo created_at phục vụ cả lọc lẫn sort ngày trên các trang danh sách.
    atomic = False

    dependencies = [
        ('orders', '0094_order_has_address'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name='order',
                    index=models.Index(
                        condition=models.Q(('has_address', False)),
                        fields=['created_at'],
                        name='ord_no_address_created_idx',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ord_no_address_created_idx "
                        "ON orders (created_at) WHERE NOT has_address;"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS ord_no_address_created_idx;",
                ),
            ],
        ),
    ]
