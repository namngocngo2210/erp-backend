from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("facebook_ads", "0003_alter_facebookadaccount_table_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="facebookapp",
            name="token_status",
            field=models.CharField(
                choices=[
                    ("unknown", "Chưa kiểm tra"),
                    ("ok", "Hợp lệ"),
                    ("expiring", "Sắp hết hạn"),
                    ("invalid", "Hỏng / hết hạn"),
                ],
                default="unknown",
                max_length=16,
                verbose_name="Trạng thái token",
            ),
        ),
        migrations.AddField(
            model_name="facebookapp",
            name="token_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="facebookapp",
            name="data_access_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="facebookapp",
            name="token_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="facebookapp",
            name="token_invalid_since",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="facebookapp",
            name="token_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddIndex(
            model_name="facebookapp",
            index=models.Index(fields=["token_status"], name="fb_app_token_status_idx"),
        ),
    ]
