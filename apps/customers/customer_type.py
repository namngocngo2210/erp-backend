"""Loại khách (Customer.is_new_customer): cũ = đã có ≥1 đơn GIAO THÀNH CÔNG, mới = chưa.

Đơn được tính: chưa xóa mềm, status = DELIVERED_SUCCESS (3) và hóa đơn không bị hủy
(invoice_status != 2 — loại đơn cũ khi KiotViet hủy-xuất lại hóa đơn HD→HD.01).
Vì status đơn thay đổi theo vòng đời, cờ được recompute ở nhiều điểm:

- Event ``orders.saved`` + signal post_delete (apps.customers.subscribers): tạo đơn,
  save đổi status/invoice_status/deleted_at, hoặc full save (update_fields=None —
  webhook); xoá cứng.
- bulk_delete_orders (soft delete bằng queryset.update, bypass signal) gọi thẳng
  ``refresh_customer_type``.
- ``reconcile_customer_type`` chạy kèm cron rebuild_segment_snapshot (2h) — lưới
  an toàn cho queryset.update đổi status, đổi customer của đơn, thao tác tay DB.
"""

from django.apps import apps
from django.conf import settings
from django.db import connection

from apps.common.db_guards import statement_timeout
from apps.common.order_scope import sql_exclude_mirror_orders

# = OrderStatus.DELIVERED_SUCCESS / invoice_status hủy. Literal để tránh import
# vòng (orders đã import customers qua FK Order.customer).
_DELIVERED_SUCCESS = 3
_INVOICE_CANCELLED = 2


def refresh_customer_type(customer_ids) -> int:
    """Tính lại is_new_customer cho các customer id truyền vào. Trả về số dòng đổi."""
    ids = {int(cid) for cid in customer_ids if cid}
    if not ids:
        return 0
    customer_model = apps.get_model("customers", "Customer")
    order_model = apps.get_model("orders", "Order")

    old_ids = set(
        order_model.objects.filter(
            customer_id__in=ids,
            deleted_at__isnull=True,
            status=_DELIVERED_SUCCESS,
        )
        .exclude(invoice_status=_INVOICE_CANCELLED)
        .values_list("customer_id", flat=True)
        .distinct()
    )
    new_ids = ids - old_ids

    changed = 0
    if old_ids:
        changed += customer_model.objects.filter(
            id__in=old_ids, is_new_customer=True
        ).update(is_new_customer=False)
    if new_ids:
        changed += customer_model.objects.filter(
            id__in=new_ids, is_new_customer=False
        ).update(is_new_customer=True)
    return changed


_RECONCILE_SQL = f"""
UPDATE customers c
SET is_new_customer = sub.is_new
FROM (
    SELECT c2.id, COUNT(o.id) = 0 AS is_new
    FROM customers c2
    LEFT JOIN orders o ON o.customer_id = c2.id
        AND o.deleted_at IS NULL
        AND o.status = 3
        AND (o.invoice_status IS NULL OR o.invoice_status <> 2)
        AND {sql_exclude_mirror_orders("o")}
    GROUP BY c2.id
) sub
WHERE sub.id = c.id AND c.is_new_customer IS DISTINCT FROM sub.is_new
"""


def reconcile_customer_type() -> int:
    """Quét toàn bộ và sửa mọi drift. Trả về số dòng đã sửa.

    Dùng chung trần ``REPORT_QUERY_TIMEOUT_SECONDS`` với các bước precompute khác
    trong cron ``rebuild_segment_snapshot``. Trước đây đây là bước DUY NHẤT trong 5
    bước không có guard, nên nó rơi xuống trần toàn cục ``DB_STATEMENT_TIMEOUT_MS``
    (30s) trong khi 4 bước anh em được 120s — chênh lệch này đã giết nó ở sự cố
    2026-07-22 (query đo được 2.3s khi cache ấm, nhưng chạm 30s khi Postgres vừa
    khởi động lại sau deploy và shared_buffers còn nguội).
    """
    timeout = getattr(settings, "REPORT_QUERY_TIMEOUT_SECONDS", 0)
    with statement_timeout(timeout), connection.cursor() as cursor:
        cursor.execute(_RECONCILE_SQL)
        return cursor.rowcount