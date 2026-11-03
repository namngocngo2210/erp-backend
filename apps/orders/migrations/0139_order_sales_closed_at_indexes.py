"""Index sắp xếp theo `sales_closed_at` — hai chiều, NULLS LAST.

Vì sao HAI index cho một cột: `DESC NULLS LAST` quét ngược ra `ASC NULLS FIRST`,
tức 62% đơn chưa chốt (NULL) sẽ dồn lên đầu khi người dùng chọn "Cũ → Mới". Cần
đủ cả hai chiều để cả hai tuỳ chọn sắp xếp đều đi bằng index thay vì sort toàn bảng.

Partial theo `deleted_at IS NULL` để khớp `_base_orders_queryset` — mọi truy vấn
danh sách đơn đều mang điều kiện này.

atomic=False + AddIndexConcurrently: bảng orders lớn, tạo index khoá ghi sẽ chặn
luồng tạo đơn. CHẠY GIỜ THẤP ĐIỂM. Đứt giữa chừng để lại index INVALID — kiểm bằng
`SELECT indisvalid FROM pg_index ...`, `DROP INDEX CONCURRENTLY` rồi migrate lại.
"""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("orders", "0138_order_sales_closed_at"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                F("sales_closed_at").desc(nulls_last=True),
                F("id").desc(),
                condition=Q(deleted_at__isnull=True),
                name="ord_sales_closed_desc_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                F("sales_closed_at").asc(nulls_last=True),
                F("id").asc(),
                condition=Q(deleted_at__isnull=True),
                name="ord_sales_closed_asc_idx",
            ),
        ),
    ]
