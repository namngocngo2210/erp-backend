# Generated for: tách channel_id/old_channel_id ra bảng mapping riêng để hỗ trợ
# nhiều KiotVietChannel cho mỗi Channel.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("channels_core", "0014_alter_channel_table_alter_channelapitoken_table_and_more"),
        ("kiotviet", "0011_alter_kiotvietaccesstoken_table_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChannelKiotvietMapping",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "external_sale_channel_id",
                    models.CharField(
                        help_text="ID saleChannel bên KiotViet tương ứng với cặp (Channel, KiotVietChannel) này",
                        max_length=255,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="kiotviet_mappings",
                        to="channels_core.channel",
                    ),
                ),
                (
                    "kiotviet_channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="channel_mappings",
                        to="kiotviet.kiotvietchannel",
                    ),
                ),
            ],
            options={
                "db_table": "channel_kiotviet_mappings",
                "indexes": [
                    models.Index(
                        fields=["kiotviet_channel", "external_sale_channel_id"],
                        name="ckvm_kv_extid_idx",
                    ),
                ],
                "unique_together": {("channel", "kiotviet_channel")},
            },
        ),
    ]
