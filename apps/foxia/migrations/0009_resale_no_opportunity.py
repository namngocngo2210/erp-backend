"""Trạng thái `no_opportunity` cho nhật ký resale + sửa các dòng đã ghi sai.

07/10/2026: Foxia trả `"cohoi_id": false` (ghi nhận khách, KHÔNG tạo cơ hội) cho
158 lần đẩy lại; parser cũ đọc `int(False) == 0` nên các dòng mang status `ok`,
cohoi_id 0. Chuyển chúng sang `no_opportunity`, cohoi_id NULL (và khachhang_id 0
nếu có) — đúng sự thật, và để thống kê không tính chúng là thành công.
"""

from django.db import migrations, models


def fix_false_cohoi(apps, schema_editor):
    FoxiaResalePush = apps.get_model("foxia", "FoxiaResalePush")
    FoxiaResalePush.objects.filter(status="ok", cohoi_id=0).update(status="no_opportunity", cohoi_id=None)
    FoxiaResalePush.objects.filter(khachhang_id=0).update(khachhang_id=None)


class Migration(migrations.Migration):

    dependencies = [
        ("foxia", "0008_foxia_resale_push"),
    ]

    operations = [
        migrations.AlterField(
            model_name="foxiaresalepush",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Đang đẩy"),
                    ("ok", "Thành công"),
                    ("failed", "Thất bại"),
                    ("skipped_no_phone", "Bỏ qua — đơn không có SĐT"),
                    ("skipped_duplicate", "Bỏ qua — SĐT đã có cơ hội resale trong ngày"),
                    ("no_opportunity", "Foxia không tạo cơ hội"),
                ],
                max_length=32,
            ),
        ),
        migrations.RunPython(fix_false_cohoi, migrations.RunPython.noop),
    ]
