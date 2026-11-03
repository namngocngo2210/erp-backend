from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("kiotviet", "0007_kiotvietchannel_web_credentials"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="kiotvietchannel",
            name="channel_ids",
        ),
    ]
