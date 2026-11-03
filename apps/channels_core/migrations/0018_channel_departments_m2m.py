from django.db import migrations, models


def copy_fk_to_m2m(apps, schema_editor):
    """Backfill: mỗi kênh đang gán 1 phòng ban (FK) → thêm vào M2M departments."""
    Channel = apps.get_model("channels_core", "Channel")
    for channel in Channel.objects.exclude(department__isnull=True).only("id", "department_id").iterator():
        channel.departments.add(channel.department_id)


def copy_m2m_to_fk(apps, schema_editor):
    """Reverse: lấy phòng ban đầu tiên trong M2M gán lại vào FK (best-effort)."""
    Channel = apps.get_model("channels_core", "Channel")
    for channel in Channel.objects.all().iterator():
        first = channel.departments.first()
        channel.department = first
        channel.save(update_fields=["department"])


class Migration(migrations.Migration):

    dependencies = [
        ("channels_core", "0017_channel_department_channel_channel_department_idx"),
        ("users", "0012_user_token_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="channel",
            name="departments",
            field=models.ManyToManyField(
                blank=True,
                help_text="Các phòng ban được dùng kênh này (lọc kênh theo phòng ban ở form tạo đơn). Rỗng = dùng chung.",
                related_name="channels",
                to="users.department",
            ),
        ),
        migrations.RunPython(copy_fk_to_m2m, copy_m2m_to_fk),
        migrations.RemoveIndex(
            model_name="channel",
            name="channel_department_idx",
        ),
        migrations.RemoveField(
            model_name="channel",
            name="department",
        ),
    ]
