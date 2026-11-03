from django.db import migrations, models


def seed_branch_map(apps, schema_editor):
    """Seed map từ cặp legacy (branch_id → old_sync_branch_id) cho kênh mới đã ghép Kiot cũ."""
    KiotVietChannel = apps.get_model("kiotviet", "KiotVietChannel")
    channels = KiotVietChannel.objects.filter(
        old=False,
        deleted_at__isnull=True,
        paired_old_channel__isnull=False,
    )
    for channel in channels:
        source = (channel.branch_id or "").strip()
        target = (channel.old_sync_branch_id or "").strip()
        if not source or not target or channel.old_sync_branch_map:
            continue
        channel.old_sync_branch_map = [
            {"source_branch_id": source, "target_branch_id": target}
        ]
        channel.save(update_fields=["old_sync_branch_map"])


class Migration(migrations.Migration):

    dependencies = [
        ("kiotviet", "0015_kiotvietchannel_branch_names"),
    ]

    operations = [
        migrations.AddField(
            model_name="kiotvietchannel",
            name="old_sync_branch_map",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Danh sách cặp kho đồng bộ new→old: "
                    '[{"source_branch_id": "...", "target_branch_id": "..."}]. '
                    "source_branch_id thuộc branch_ids của kênh này, target_branch_id thuộc kênh Kiot cũ đã ghép."
                ),
                verbose_name="Old Sync Branch Map",
            ),
        ),
        migrations.AlterField(
            model_name="kiotvietchannel",
            name="old_sync_branch_id",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Branch ID dùng khi đồng bộ từ kênh mới sang kênh Kiot cũ đã ghép. "
                    "Fallback khi kho nguồn không có trong old_sync_branch_map."
                ),
                max_length=100,
                null=True,
                verbose_name="Old Sync Branch ID",
            ),
        ),
        migrations.RunPython(seed_branch_map, migrations.RunPython.noop),
    ]
