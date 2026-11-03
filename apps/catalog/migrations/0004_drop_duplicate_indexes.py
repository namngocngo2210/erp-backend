"""Bỏ 2 index trùng tuyệt đối (audit 2026-09-23 T2/P2-3, đo prod bằng so
indkey/indclass/indpred): product_group_idx ≡ products_group_id_* (FK auto),
product_package_pq_idx ≡ uniq_product_package_qty (unique). Bảng nhỏ →
RemoveIndex thường."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0003_partial_unique_soft_delete"),
    ]

    operations = [
        migrations.RemoveIndex(model_name="product", name="product_group_idx"),
        migrations.RemoveIndex(model_name="productpackage", name="product_package_pq_idx"),
    ]
