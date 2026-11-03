"""Thêm cột `resale` vào ma trận chia đơn (docs/foxia-resale-delivered.md). Chỉ đổi
choices — không đổi schema Postgres."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0149_remap_bomb_reason_taxonomy_v2"),
    ]

    operations = [
        migrations.AlterField(
            model_name="leadassignmentshare",
            name="sheet",
            field=models.CharField(
                choices=[
                    ("sheet1", "Landing xác nhận đơn"),
                    ("sheet2", "Messenger xác nhận đơn"),
                    ("sheet3", "Tư vấn"),
                    ("resale", "Resale (đơn giao thành công)"),
                ],
                max_length=16,
            ),
        ),
    ]
