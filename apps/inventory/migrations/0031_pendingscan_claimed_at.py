from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0030_cancelled_dispatch_watch"),
    ]

    operations = [
        migrations.AddField(
            model_name="pendingshipmentscan",
            name="claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
