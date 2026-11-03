from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("kiotviet", "0006_kiotvietchannel_old_sync_branch_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="kiotvietchannel",
            name="password",
            field=models.CharField(blank=True, max_length=255, null=True, verbose_name="Password"),
        ),
        migrations.AddField(
            model_name="kiotvietchannel",
            name="username",
            field=models.CharField(blank=True, max_length=255, null=True, verbose_name="Username"),
        ),
    ]
