from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("channels_core", "0003_channel_landingpage_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="userchannel",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userchannel",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
    ]
