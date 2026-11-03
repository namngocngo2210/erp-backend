# Giảm giá theo dòng hàng: `discount` = tiền giảm cho MỖI sản phẩm (đúng nghĩa cột
# "Giảm giá" của KiotViet), `discount_percent` chỉ lưu khi sale nhập theo %.
#
# Postgres ≥ 11 thêm cột với default hằng số chỉ ghi metadata, không rewrite
# `order_items` — bảng lớn, đang được ghi liên tục.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0140_orderkiotvietjob_kship_service_extra"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="discount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="discount_percent",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
        ),
    ]
