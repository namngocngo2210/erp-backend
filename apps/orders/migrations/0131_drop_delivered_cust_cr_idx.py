"""Drop index chết ord_delivered_cust_cr_idx (audit DB 2026-08-19, mục 3b.1).

Index (customer_id, created_at) WHERE deleted_at IS NULL AND status = 3 bị
ord_gtc_customer_idx thay thế hoàn toàn: cùng cột, cùng predicate, chỉ khác
chiều DESC (btree quét được 2 chiều). Số liệu prod: ord_delivered_cust_cr_idx
0 scans / ord_gtc_customer_idx 249M scans, thu hồi 29MB trên bảng ghi nóng nhất.

Index chỉ tồn tại qua RunSQL (0059 + bản squash), KHÔNG nằm trong Meta.indexes
→ drop bằng RunSQL thuần, không đụng migration state.

CONCURRENTLY: không khoá bảng nhưng cần atomic=False; nếu fail giữa chừng sẽ
để lại index INVALID — cách phát hiện/dọn xem docs/runbooks/2026-07-26-drop-dead-indexes.md.
"""

from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("orders", "0130_order_source_cnv"),
    ]

    operations = [
        migrations.RunSQL(
            sql="DROP INDEX CONCURRENTLY IF EXISTS ord_delivered_cust_cr_idx;",
            reverse_sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ord_delivered_cust_cr_idx
                ON orders (customer_id, created_at)
                WHERE deleted_at IS NULL AND status = 3;
            """,
        ),
    ]
