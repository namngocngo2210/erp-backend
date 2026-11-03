"""Drop order_old_id_idx (22MB, 0 scans): old_id chỉ được ghi/đọc thuộc tính, không có filter nào.

Audit DB 2026-08-19 mục 3d: index trùng lặp tuyệt đối với (không trùng — index chết: không call site) — mỗi UPDATE
phải maintain gấp đôi trên bảng ghi nóng nhất orders. Đã đối chiếu: planner dùng bản còn lại.
CONCURRENTLY -> atomic=False; fail giữa chừng để lại index INVALID — dọn theo
docs/runbooks/2026-07-26-drop-dead-indexes.md.
"""

from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("orders", "0132_shipping_code_btree_idx"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveIndex(model_name="order", name="order_old_id_idx"),
        ],
            database_operations=[
                migrations.RunSQL(
                    sql="DROP INDEX CONCURRENTLY IF EXISTS order_old_id_idx;",
                    reverse_sql="""CREATE INDEX CONCURRENTLY IF NOT EXISTS order_old_id_idx ON orders (old_id);""",
                ),
        ],
        ),
    ]
