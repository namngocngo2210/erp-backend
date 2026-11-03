import django.contrib.postgres.indexes
import django.db.models.functions.comparison
import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):
    # CONCURRENTLY (atomic=False) để không khóa bảng orders (~500k dòng) khi deploy.
    # - order_name_upper_trgm_idx: 0091 tạo dạng UPPER(name) (thiếu ::text) → KHÔNG khớp
    #   predicate `UPPER(name::text)` mà Django sinh cho name__icontains → planner bỏ index
    #   (name search seq-scan ~444ms). Drop rồi tạo lại đúng dạng UPPER((name)::text).
    # - order_order_code_trgm_idx: phục vụ search order_code qua LIKE '%...%'.
    atomic = False

    dependencies = [
        ('orders', '0091_order_name_upper_trgm_idx'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveIndex(model_name='order', name='order_name_upper_trgm_idx'),
                migrations.AddIndex(
                    model_name='order',
                    index=django.contrib.postgres.indexes.GinIndex(
                        django.contrib.postgres.indexes.OpClass(
                            django.db.models.functions.text.Upper(
                                django.db.models.functions.comparison.Cast('name', output_field=models.TextField())
                            ),
                            name='gin_trgm_ops',
                        ),
                        name='order_name_upper_trgm_idx',
                    ),
                ),
                migrations.AddIndex(
                    model_name='order',
                    index=django.contrib.postgres.indexes.GinIndex(
                        fields=['order_code'], name='order_order_code_trgm_idx', opclasses=['gin_trgm_ops']
                    ),
                ),
                migrations.AddIndex(
                    model_name='order',
                    index=django.contrib.postgres.indexes.GinIndex(
                        django.contrib.postgres.indexes.OpClass(
                            django.db.models.functions.text.Upper('product'), name='gin_trgm_ops'
                        ),
                        name='order_product_upper_trgm_idx',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS order_name_upper_trgm_idx;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS order_name_upper_trgm_idx "
                        "ON orders USING gin ((UPPER((name)::text)) gin_trgm_ops);"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS order_name_upper_trgm_idx;",
                ),
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS order_order_code_trgm_idx "
                        "ON orders USING gin (order_code gin_trgm_ops);"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS order_order_code_trgm_idx;",
                ),
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS order_product_upper_trgm_idx "
                        "ON orders USING gin ((UPPER(product)) gin_trgm_ops);"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS order_product_upper_trgm_idx;",
                ),
            ],
        ),
    ]
