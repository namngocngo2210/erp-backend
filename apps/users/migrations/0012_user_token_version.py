from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0011_grant_resale_tracking_to_customer_depts"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="token_version",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
