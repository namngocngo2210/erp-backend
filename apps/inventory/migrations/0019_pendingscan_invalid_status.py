from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0018_scan_batch_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pendingshipmentscan",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Chờ khớp"),
                    ("matched", "Đã khớp"),
                    ("failed", "Lỗi"),
                    ("invalid", "Mã không đúng"),
                ],
                default="pending",
                max_length=10,
            ),
        ),
    ]
