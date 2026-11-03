from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("channels_core", "0008_channel_landingpage_array"),
    ]

    operations = [
        migrations.AddField(
            model_name="channel",
            name="owner",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
