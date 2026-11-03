# Data migration: backfill ChannelKiotvietMapping từ 2 cột legacy
# Channel.channel_id (→ KiotVietChannel có old=False) và
# Channel.old_channel_id (→ KiotVietChannel có old=True).

from django.db import migrations


def backfill_mappings(apps, schema_editor):
    Channel = apps.get_model("channels_core", "Channel")
    KiotVietChannel = apps.get_model("kiotviet", "KiotVietChannel")
    Mapping = apps.get_model("channels_core", "ChannelKiotvietMapping")

    new_kvs = list(KiotVietChannel.objects.filter(deleted_at__isnull=True, old=False))
    old_kvs = list(KiotVietChannel.objects.filter(deleted_at__isnull=True, old=True))

    if not new_kvs and not old_kvs:
        return

    channels = Channel.objects.filter(deleted_at__isnull=True)
    for channel in channels:
        new_ext = (channel.channel_id or "").strip()
        if new_ext:
            for kv in new_kvs:
                Mapping.objects.update_or_create(
                    channel=channel,
                    kiotviet_channel=kv,
                    defaults={"external_sale_channel_id": new_ext},
                )

        old_ext = (channel.old_channel_id or "").strip()
        if old_ext:
            for kv in old_kvs:
                Mapping.objects.update_or_create(
                    channel=channel,
                    kiotviet_channel=kv,
                    defaults={"external_sale_channel_id": old_ext},
                )


def remove_mappings(apps, schema_editor):
    Mapping = apps.get_model("channels_core", "ChannelKiotvietMapping")
    Mapping.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("channels_core", "0015_channelkiotvietmapping"),
    ]

    operations = [
        migrations.RunPython(backfill_mappings, remove_mappings),
    ]
