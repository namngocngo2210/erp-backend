"""Xóa tính năng Đơn gửi ngoài — drop cột `outside` và `shipping_form`.

KHÔNG HOÀN TÁC ĐƯỢC: dữ liệu hai cột `outside` và `shipping_form` mất vĩnh viễn,
không có bản sao lưu. Chủ sản phẩm đã chấp nhận đánh đổi này (không dump CSV).

`AlterField` trên `order_type` chỉ đổi `choices` (metadata Python), không đụng
dữ liệu — đơn cũ mang `same_product` / `different_product` / `purchase` /
`warranty_return` giữ nguyên chuỗi trong DB và sẽ hiển thị mã thô thay vì nhãn
tiếng Việt. Đánh đổi đã được chốt.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0119_alter_order_options_alter_order_managers_and_more'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='order',
            name='ord_outside_cr_idx',
        ),
        migrations.RemoveField(
            model_name='order',
            name='outside',
        ),
        migrations.RemoveField(
            model_name='order',
            name='shipping_form',
        ),
        migrations.AlterField(
            model_name='order',
            name='order_type',
            field=models.CharField(choices=[('normal', 'Normal'), ('no_return', 'No Return'), ('has_return', 'Has Return')], default='normal', max_length=30),
        ),
    ]
