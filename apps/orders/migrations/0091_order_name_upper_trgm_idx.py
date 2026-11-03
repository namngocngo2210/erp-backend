from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import migrations
from django.db.models.functions import Upper


class Migration(migrations.Migration):
    # Functional trigram GIN trên UPPER(name) để search theo TÊN khách (`q`) trên trang
    # Đơn hàng dùng `name__icontains` (→ UPPER(name) LIKE UPPER(%s)) mà không seq-scan
    # toàn bảng orders (~500k dòng). CONCURRENTLY không chạy trong transaction (atomic=
    # False); IF NOT EXISTS để an toàn khi deploy áp lại một phần.
    atomic = False

    dependencies = [
        ("orders", "0090_order_ord_gtc_customer_idx"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="order",
                    index=GinIndex(
                        OpClass(Upper("name"), name="gin_trgm_ops"),
                        name="order_name_upper_trgm_idx",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS order_name_upper_trgm_idx "
                        "ON orders USING gin (UPPER(name) gin_trgm_ops);"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS order_name_upper_trgm_idx;",
                ),
            ],
        ),
    ]
