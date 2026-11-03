"""Bảng liên kết Foxia đổi trục: bỏ hàng SKU + ô "Mặc định", cột giờ là loại
nguồn cố định (landing/messenger/hotline/zalo/resale) — quyết định 2026-09-16.

Toàn bộ id đã điền bị bỏ theo yêu cầu của chủ hệ thống; danh sách kênh giữ lại
để người vận hành chỉ phải điền lại id.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("foxia", "0006_foxialinkchannel_default_source_id"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.DeleteModel(name="FoxiaLinkCell"),
        migrations.DeleteModel(name="FoxiaLinkProduct"),
        migrations.RemoveField(model_name="foxialinkchannel", name="default_source_id"),
        migrations.CreateModel(
            name="FoxiaLinkCell",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("landing", "Landing"),
                            ("messenger", "Messenger"),
                            ("hotline", "Hotline"),
                            ("zalo", "Zalo"),
                            ("resale", "Resale"),
                        ],
                        max_length=16,
                    ),
                ),
                ("foxia_source_id", models.BigIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "link_channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cells",
                        to="foxia.foxialinkchannel",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "foxia_link_cells"},
        ),
        migrations.AddConstraint(
            model_name="foxialinkcell",
            constraint=models.UniqueConstraint(
                fields=("link_channel", "kind"), name="foxia_link_cell_uniq"
            ),
        ),
    ]
