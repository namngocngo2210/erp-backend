import apps.common.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("users", "0045_user_zenify_email")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="media_role",
            field=models.CharField(
                blank=True,
                choices=[("", "Không có"), ("viewer", "Viewer"), ("marketing", "Marketing"), ("uploader", "Uploader"), ("admin", "Admin")],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="media_api_key",
            field=apps.common.fields.EncryptedCharField(blank=True, default="", max_length=1024),
        ),
    ]
