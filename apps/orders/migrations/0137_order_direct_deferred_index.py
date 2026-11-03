"""Partial index cho hàng đợi "Chờ xuất kho".

Partial vì tập direct_dispatch_deferred=True rất nhỏ so với bảng đơn — index đầy đủ
là lãng phí dung lượng và chi phí ghi trên mọi INSERT đơn.

atomic=False + AddIndexConcurrently: bảng orders lớn, tạo index khoá ghi sẽ chặn luồng
tạo đơn. CHẠY GIỜ THẤP ĐIỂM. Đứt giữa chừng để lại index INVALID — kiểm bằng
`SELECT indisvalid FROM pg_index ...`, `DROP INDEX CONCURRENTLY` rồi migrate lại.
"""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("orders", "0136_order_direct_dispatch_deferred"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                fields=["-created_at"],
                name="order_direct_deferred_idx",
                condition=models.Q(direct_dispatch_deferred=True),
            ),
        ),
    ]
