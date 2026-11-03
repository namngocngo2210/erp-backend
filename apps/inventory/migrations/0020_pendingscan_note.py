from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0019_pendingscan_invalid_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="pendingshipmentscan",
            name="note",
            field=models.TextField(blank=True, default=""),
        ),
    ]
