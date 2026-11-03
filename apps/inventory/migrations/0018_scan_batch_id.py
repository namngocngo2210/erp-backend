from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0017_pendingshipmentscan"),
    ]

    operations = [
        migrations.AddField(
            model_name="returnnote",
            name="scan_batch_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pendingshipmentscan",
            name="scan_batch_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="returnnote",
            index=models.Index(
                fields=["scan_batch_id"], name="returnnote_scanbatch_idx"
            ),
        ),
    ]
