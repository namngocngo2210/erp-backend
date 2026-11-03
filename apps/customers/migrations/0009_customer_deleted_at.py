from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0008_customer_gender"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["deleted_at"], name="customer_deleted_idx"),
        ),
    ]

