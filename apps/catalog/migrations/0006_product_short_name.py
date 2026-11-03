"""Tên ngắn sản phẩm cho bán hàng/Pancake POS (docs/product-short-name.md).

`products` ~2,7k dòng; ADD COLUMN ... NOT NULL DEFAULT '' là hằng nên PG ≥ 11 chỉ
ghi catalog, không rewrite bảng. Không index (không lọc/tìm theo cột này).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0005_product_pancake_sync_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="short_name",
            field=models.CharField(blank=True, db_default="", default="", max_length=120),
        ),
    ]
