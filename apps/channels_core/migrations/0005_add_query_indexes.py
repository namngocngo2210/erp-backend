from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("channels_core", "0004_userchannel_soft_delete"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="userchannel",
            index=models.Index(fields=["user", "is_active"], name="userchannel_user_active_idx"),
        ),
        migrations.AddIndex(
            model_name="userchannel",
            index=models.Index(fields=["channel", "is_active"], name="userchannel_channel_active_idx"),
        ),
        migrations.AddIndex(
            model_name="channelapitoken",
            index=models.Index(fields=["channel", "is_active"], name="channel_token_active_idx"),
        ),
        migrations.AddIndex(
            model_name="channelapitoken",
            index=models.Index(fields=["token_hash"], name="channel_token_hash_idx"),
        ),
    ]
