# Thêm `order_kiotviet_jobs.kship_service_extra` — lựa chọn cho các SERVICE_EXTRA mà
# hãng vận chuyển bắt buộc, hiện là ca lấy hàng của SPX ("PickShift").
#
# `null=True` (không db_default) để Postgres chỉ ghi metadata, không rewrite bảng:
# `order_kiotviet_jobs` là bảng hàng đợi, ghi liên tục trong giờ làm việc.
#
# Không backfill: job cũ để NULL, gateway tự lấy giá trị đầu tiên KShip còn chào.
# Backfill sẽ bịa ra một ca lấy hàng mà người dùng chưa từng chọn.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0139_order_sales_closed_at_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderkiotvietjob',
            name='kship_service_extra',
            field=models.JSONField(blank=True, default=dict, null=True),
        ),
    ]
