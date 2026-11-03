from django.db import migrations, models


def _remap_forward(apps, schema_editor):
    """Remap mã cũ → mã mới theo flow: 2 (Hoàn thành)→9, 3 (Hủy)→11.

    Chạy 3→11 TRƯỚC 2→9 (tường minh, dù 9/11 không phải mã nguồn nên không va).
    1 (Đang xử lý) giữ số 1, chỉ đổi nghĩa nhãn thành "Đợi khách XN".
    """
    WarrantyClaim = apps.get_model("warranty", "WarrantyClaim")
    WarrantyClaim.objects.filter(status=3).update(status=11)
    WarrantyClaim.objects.filter(status=2).update(status=9)


def _remap_backward(apps, schema_editor):
    WarrantyClaim = apps.get_model("warranty", "WarrantyClaim")
    WarrantyClaim.objects.filter(status=9).update(status=2)
    WarrantyClaim.objects.filter(status=11).update(status=3)
    # Các trạng thái mới (4-8, 10) không có ánh xạ cũ → về "Đang xử lý".
    WarrantyClaim.objects.filter(status__gt=3).update(status=1)


class Migration(migrations.Migration):
    dependencies = [
        ("warranty", "0013_merge_20260711"),
    ]

    operations = [
        migrations.RunPython(_remap_forward, _remap_backward),
        migrations.AlterField(
            model_name="warrantyclaim",
            name="status",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (1, "Đợi khách XN"),
                    (2, "Chờ tạo đơn"),
                    (3, "Chờ vận chuyển"),
                    (4, "Đang sửa chữa"),
                    (5, "Chờ chốt phương án"),
                    (6, "Chờ tạo đơn gửi lại"),
                    (7, "Đang gửi lại khách"),
                    (8, "Xin feedback"),
                    (9, "Hoàn thành"),
                    (10, "Thất lạc"),
                    (11, "Hủy"),
                ],
                default=1,
            ),
        ),
    ]
