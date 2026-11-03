from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="WarrantyRegistration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "source",
                    models.CharField(
                        choices=[("tiktok", "TikTok"), ("shopee", "Shopee"), ("facebook", "Facebook")],
                        max_length=20,
                    ),
                ),
                ("invoice_code", models.CharField(max_length=100)),
                ("full_name", models.CharField(max_length=255)),
                ("phone", models.CharField(max_length=30)),
                ("email", models.EmailField(blank=True, default="", max_length=255)),
                ("note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["created_at"], name="warranty_created_at_idx"),
                    models.Index(fields=["phone"], name="warranty_phone_idx"),
                    models.Index(fields=["invoice_code"], name="warranty_invoice_idx"),
                ],
            },
        ),
    ]
