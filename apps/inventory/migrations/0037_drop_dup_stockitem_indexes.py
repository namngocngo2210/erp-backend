"""Drop stockitem_wh_idx + stockitem_prod_idx: trùng tuyệt đối với index tự sinh của ForeignKey.

Audit DB 2026-08-19 mục 3d: index trùng lặp tuyệt đối với auto FK index của Django — mỗi UPDATE
phải maintain gấp đôi trên bảng ghi nóng stock_items. Đã đối chiếu: planner dùng bản còn lại.
CONCURRENTLY -> atomic=False; fail giữa chừng để lại index INVALID — dọn theo
docs/runbooks/2026-07-26-drop-dead-indexes.md.
"""

from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("inventory", "0036_void_duplicate_return_scans"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveIndex(model_name="stockitem", name="stockitem_wh_idx"),
                migrations.RemoveIndex(model_name="stockitem", name="stockitem_prod_idx"),
        ],
            database_operations=[
                migrations.RunSQL(
                    sql="DROP INDEX CONCURRENTLY IF EXISTS stockitem_wh_idx;",
                    reverse_sql="""CREATE INDEX CONCURRENTLY IF NOT EXISTS stockitem_wh_idx ON stock_items (warehouse_id);""",
                ),
                migrations.RunSQL(
                    sql="DROP INDEX CONCURRENTLY IF EXISTS stockitem_prod_idx;",
                    reverse_sql="""CREATE INDEX CONCURRENTLY IF NOT EXISTS stockitem_prod_idx ON stock_items (product_id);""",
                ),
        ],
        ),
    ]
