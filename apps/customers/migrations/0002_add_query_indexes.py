from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0001_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["phone_raw"], name="customer_phone_raw_idx"),
        ),
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["created_at"], name="customer_created_at_idx"),
        ),
    ]
