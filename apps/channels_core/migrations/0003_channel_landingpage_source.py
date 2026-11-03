from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("channels_core", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="channel",
            name="landingpage_source",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
