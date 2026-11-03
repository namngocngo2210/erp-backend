"""Thêm lựa chọn `cnv` vào OrderSource (spec 2026-08-19).

`choices` không đụng schema Postgres — AlterField này chỉ đồng bộ state của
Django. Nhưng roll code kèm nó ĐỔI HÀNH VI `Order.objects`: `"cnv"` vào
`MIRROR_ORDER_SOURCES` nên đơn nguồn này bị loại khỏi mọi báo cáo/danh sách nội
bộ. Tác động thực tế lúc deploy bằng không vì chưa có đơn CNV nào trong DB.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0129_remove_legacy_recall_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="order_source",
            field=models.CharField(
                blank=True,
                choices=[
                    ("landing", "Landing"),
                    ("kol", "KOL"),
                    ("zalo", "Zalo"),
                    ("messenger", "Messenger"),
                    ("hotline", "Hotline"),
                    ("koc", "KOC"),
                    ("shopee", "Shopee"),
                    ("tiktok", "TikTok"),
                    ("foxia", "Foxia"),
                    ("cnv", "CNV Loyalty"),
                ],
                max_length=30,
                null=True,
            ),
        ),
    ]
