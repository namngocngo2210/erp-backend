"""Index sắp xếp danh sách đơn theo "Tổng sau giảm giá" (`total_after_discount`).

Một index cho cả hai chiều: cột NOT NULL nên quét ngược `(total_after_discount,
id)` cho đúng thứ tự DESC — không vướng bài toán NULLS như `sales_closed_at` (0139).
Partial theo `deleted_at IS NULL` để khớp `_base_orders_queryset`.

atomic=False + AddIndexConcurrently: bảng orders nóng, không khoá ghi. CHẠY GIỜ THẤP
ĐIỂM. Đứt giữa chừng để lại index INVALID — `DROP INDEX CONCURRENTLY` rồi migrate lại.
"""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("orders", "0146_drop_duplicate_indexes"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                fields=["total_after_discount", "id"],
                condition=Q(deleted_at__isnull=True),
                name="ord_total_after_disc_idx",
            ),
        ),
    ]
