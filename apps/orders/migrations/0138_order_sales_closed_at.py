"""Thêm `Order.sales_closed_at` — mốc sale chốt đơn.

Chỉ ADD COLUMN NULL: tức thì trên PostgreSQL 11+, không rewrite bảng, an toàn chạy
giờ cao điểm. Index tách sang 0139 vì phải tạo CONCURRENTLY.

Cột này KHÔNG tự có giá trị cho đơn cũ — chạy `manage.py backfill_order_sales_closed_at`
sau khi roll code để dựng lại mốc từ `order_journey_events`.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0137_order_direct_deferred_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="sales_closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
