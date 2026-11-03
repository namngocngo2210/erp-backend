import apps.common.fields
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("users", "0046_user_media_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MediaConnection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("base_url", models.CharField(help_text="VD: https://media.example.vn (không kèm /api/v1)", max_length=255)),
                ("shared_api_key", apps.common.fields.EncryptedCharField(blank=True, default="", max_length=1024)),
                ("is_active", models.BooleanField(default=True)),
                ("last_check_at", models.DateTimeField(blank=True, null=True)),
                ("last_check_ok", models.BooleanField(blank=True, null=True)),
                ("last_check_error", models.TextField(blank=True, default="")),
                ("shared_key_role", models.CharField(blank=True, default="", max_length=32)),
                ("shared_key_username", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "media_connections"},
        ),
        migrations.CreateModel(
            name="MediaContentOwner",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("remote_id", models.BigIntegerField(unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "media_content_owners"},
        ),
        migrations.CreateModel(
            name="MediaProposalOwner",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("remote_id", models.BigIntegerField(unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "media_proposal_owners"},
        ),
    ]
