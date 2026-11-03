"""Bỏ 3 index trùng tuyệt đối với index FK Django tự sinh (audit 2026-09-23
T2/P2-3, đo prod bằng so indkey/indclass/indpred):

- order_items.order_item_product_idx ≡ orders_orderitem_product_id_* (5MB,
  bảng 1,1M dòng ghi nóng) → DROP INDEX CONCURRENTLY (khuôn 0109, atomic=False).
- order_needs.order_need_assessment_idx / order_need_type_idx ≡ FK auto
  (376kB/152kB) → RemoveIndex thường, khoá trong mili-giây.

SeparateDatabaseAndState để phía state vẫn là RemoveIndex (khớp Meta.indexes).
"""

from django.db import migrations


class Migration(migrations.Migration):
    # DROP INDEX CONCURRENTLY không chạy được trong transaction block.
    atomic = False

    dependencies = [
        ("orders", "0145_money_check_constraints"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="DROP INDEX CONCURRENTLY IF EXISTS order_item_product_idx",
                    reverse_sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS order_item_product_idx "
                        "ON order_items (product_id)"
                    ),
                ),
            ],
            state_operations=[
                migrations.RemoveIndex(model_name="orderitem", name="order_item_product_idx"),
            ],
        ),
        migrations.RemoveIndex(model_name="orderneed", name="order_need_assessment_idx"),
        migrations.RemoveIndex(model_name="orderneed", name="order_need_type_idx"),
    ]
