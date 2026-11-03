from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_user_deleted_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="new_kiot_id",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="user",
            name="old_kiot_id",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
