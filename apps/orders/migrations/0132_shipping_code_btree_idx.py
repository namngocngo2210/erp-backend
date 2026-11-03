"""Trả lại btree index cho orders.shipping_code (phân tích top-query 2026-08-19).

Lịch sử: 0032 gỡ uniq_order_ship_code_active (unique bỏ vì mã vận đơn trùng thực
tế); 0057 chỉ trả lại btree cho order_code + invoice_code — shipping_code KHÔNG
bao giờ được trả lại. Hiện equality lookup phải đi qua GIN trigram
(order_ship_code_trgm_idx), EXPLAIN cost ~1081/call thay vì ~0.4 với btree.

Đây là workload ứng dụng nặng nhất DB (~25% total_exec_time tích luỹ, ~1.95M
calls): _find_existing_order của cron kv-import-create-only 5'/kv-update-invoice-
status 30'/kv-reconcile nightly (import_invoices_service.py:590), màn quét mã vận
đơn (shipping_lookup_view.py:142), warranty (claim_service.py:58,
portal_views.py:109).

Partial WHERE shipping_code > '' vì phần lớn đơn chưa có mã vận đơn (rỗng) và
không call site nào tra mã rỗng. RunSQL thuần, không đụng Meta.indexes — cùng
khuôn ord_delivered_cust_cr_idx/0131.

CONCURRENTLY: cần atomic=False; nếu fail giữa chừng để lại index INVALID — dọn
theo docs/runbooks/2026-07-26-drop-dead-indexes.md.
"""

from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("orders", "0131_drop_delivered_cust_cr_idx"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS ord_ship_code_btree_idx
                ON orders (shipping_code)
                WHERE shipping_code > '';
            """,
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS ord_ship_code_btree_idx;",
        ),
    ]
