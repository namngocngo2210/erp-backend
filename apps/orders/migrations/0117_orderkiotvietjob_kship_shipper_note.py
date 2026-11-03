# Thêm `order_kiotviet_jobs.kship_shipper_note` — quy định xem hàng người dùng chọn
# khi tạo mã vận đơn KShip.
#
# `db_default=''` để Postgres ghi default vào catalog thay vì rewrite toàn bảng: khoá
# ACCESS EXCLUSIVE chỉ giữ vài mili giây. `order_kiotviet_jobs` là bảng hàng đợi, ghi
# liên tục trong giờ làm việc nên vẫn không được phép khoá lâu dù nhỏ hơn `orders`.
#
# Không backfill: job cũ để rỗng, payload builder tự rơi về mặc định. Backfill một giá
# trị vào job đã chạy xong sẽ bịa ra lựa chọn mà người dùng chưa từng thực hiện.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0116_order_fb_ad_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderkiotvietjob',
            name='kship_shipper_note',
            field=models.CharField(blank=True, db_default='', default='', max_length=32),
        ),
    ]
