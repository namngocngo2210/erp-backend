"""Cờ ghi đè đồng bộ Pancake POS trên sản phẩm (docs/pancake-pos-product-sync.md §3.4).

`products` ~2,7k dòng; ADD COLUMN ... NOT NULL DEFAULT 'auto' là hằng nên PG ≥ 11
chỉ ghi catalog, không rewrite bảng.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_drop_duplicate_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="pancake_sync_mode",
            field=models.CharField(
                choices=[("auto", "Theo luật"), ("always", "Luôn đồng bộ"), ("never", "Không đồng bộ")],
                db_default="auto",
                default="auto",
                max_length=10,
            ),
        ),
    ]
