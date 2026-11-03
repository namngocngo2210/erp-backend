# Data migration: backfill UserKiotvietMapping từ 2 cột legacy
# User.new_kiot_id (→ KiotVietChannel có old=False) và
# User.old_kiot_id (→ KiotVietChannel có old=True).

from django.db import migrations


def backfill_mappings(apps, schema_editor):
    User = apps.get_model("users", "User")
    KiotVietChannel = apps.get_model("kiotviet", "KiotVietChannel")
    Mapping = apps.get_model("users", "UserKiotvietMapping")

    new_kvs = list(KiotVietChannel.objects.filter(deleted_at__isnull=True, old=False))
    old_kvs = list(KiotVietChannel.objects.filter(deleted_at__isnull=True, old=True))

    if not new_kvs and not old_kvs:
        return

    users = User.objects.filter(deleted_at__isnull=True)
    for user in users:
        new_ext = (user.new_kiot_id or "").strip()
        if new_ext:
            for kv in new_kvs:
                Mapping.objects.update_or_create(
                    user=user,
                    kiotviet_channel=kv,
                    defaults={"external_kiot_user_id": new_ext},
                )

        old_ext = (user.old_kiot_id or "").strip()
        if old_ext:
            for kv in old_kvs:
                Mapping.objects.update_or_create(
                    user=user,
                    kiotviet_channel=kv,
                    defaults={"external_kiot_user_id": old_ext},
                )


def remove_mappings(apps, schema_editor):
    Mapping = apps.get_model("users", "UserKiotvietMapping")
    Mapping.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0008_userkiotvietmapping"),
    ]

    operations = [
        migrations.RunPython(backfill_mappings, remove_mappings),
    ]
