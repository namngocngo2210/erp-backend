# Địa chỉ 2 cấp (34 tỉnh, sau 01/07/2025) lưu song song với 3 cấp: hoá đơn Kiot
# đẩy theo tỉnh mới + phường mới, chi tiết đơn hiển thị cả hai.
#
# Postgres ≥ 11 thêm cột với default hằng số chỉ ghi metadata, không rewrite
# `orders` (560k dòng). Không backfill đơn cũ — chỉ điền khi tạo/sửa địa chỉ.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0141_orderitem_discount"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="address_level2",
            field=models.TextField(blank=True, db_default="", default=""),
        ),
        migrations.AddField(
            model_name="order",
            name="ward_level2",
            field=models.TextField(blank=True, db_default="", default=""),
        ),
        migrations.AddField(
            model_name="order",
            name="province_level2",
            field=models.TextField(blank=True, db_default="", default=""),
        ),
        migrations.AddField(
            model_name="order",
            name="ward_code_level2",
            field=models.CharField(blank=True, db_default="", default="", max_length=20),
        ),
        migrations.AddField(
            model_name="order",
            name="province_code_level2",
            field=models.CharField(blank=True, db_default="", default="", max_length=20),
        ),
    ]
