from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("channels_core", "0005_add_query_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="channel",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="channel",
            index=models.Index(fields=["deleted_at"], name="channel_deleted_idx"),
        ),
    ]

