from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kiotviet", "0005_kiotvietchannel_paired_old_channel"),
    ]

    operations = [
        migrations.AddField(
            model_name="kiotvietchannel",
            name="old_sync_branch_id",
            field=models.CharField(
                blank=True,
                help_text="Branch ID dùng khi đồng bộ từ kênh mới sang kênh Kiot cũ đã ghép",
                max_length=100,
                null=True,
                verbose_name="Old Sync Branch ID",
            ),
        ),
    ]
