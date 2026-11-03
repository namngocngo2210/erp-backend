from django.db import migrations, models


class Migration(migrations.Migration):
    """Tên hiển thị riêng cho trang public `/sp/<mã>`.

    AddField có default → an toàn chạy trước khi roll code (cột mới, code cũ
    không đụng tới). Postgres 11+ thêm cột có default hằng số không rewrite bảng.
    """

    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="productprofile",
            name="display_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
