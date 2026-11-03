"""Bỏ cust_resale_order_idx — trùng tuyệt đối với unique index tự sinh của
OneToOneField `order` (customer_resale_followups_order_id_key). Audit
2026-09-23 T2/P2-3. Bảng nhỏ (16kB) → RemoveIndex thường."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0036_drop_report_cache_snapshots"),
    ]

    operations = [
        migrations.RemoveIndex(model_name="customerresalefollowup", name="cust_resale_order_idx"),
    ]
