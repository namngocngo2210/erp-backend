import django.db.models.deletion
from django.db import migrations, models

# 4 loại kênh chốt với user 2026-08-21. `code` cố ý trùng SALE_CHANNEL_* trong
# apps/orders/reporting/shared.py để sau này Báo cáo tổng đọc thẳng được loại kênh.
SEED_CHANNEL_TYPES = (
    ("shopee", "Shopee", 10),
    ("tiktok", "TikTok", 20),
    ("facebook", "Facebook", 30),
    ("web", "Web", 40),
)

# Backfill BEST-EFFORT cho kênh đang có: chỉ dùng đúng 2 tín hiệu đã được kiểm
# chứng trên prod trong apps/orders/reporting/shared.py (prefix `facebook`,
# mã `google-ads-web`) cộng 2 luật hiển nhiên theo tên sàn. Kênh không khớp luật
# nào để NULL — "chưa phân loại" là trạng thái hợp lệ, admin tự gán trên giao
# diện. Cố ý KHÔNG đoán theo prefix hoá đơn (HDSPE_/HDTTS_): prefix đó nằm rải
# trên 6 kênh khác nhau nên là thuộc tính của ĐƠN, không phải của kênh.
BACKFILL_RULES = (
    ("shopee", {"code__icontains": "shopee"}),
    ("tiktok", {"code__icontains": "tiktok"}),
    ("web", {"code__iexact": "google-ads-web"}),
    ("facebook", {"code__istartswith": "facebook"}),
)


def seed_and_backfill(apps, schema_editor):
    ChannelType = apps.get_model("channels_core", "ChannelType")
    Channel = apps.get_model("channels_core", "Channel")

    types_by_code = {}
    for code, name, sort_order in SEED_CHANNEL_TYPES:
        channel_type, _ = ChannelType.objects.get_or_create(
            code=code, defaults={"name": name, "sort_order": sort_order}
        )
        types_by_code[code] = channel_type

    for code, lookup in BACKFILL_RULES:
        Channel.objects.filter(channel_type__isnull=True, **lookup).update(
            channel_type=types_by_code[code]
        )


def unseed(apps, schema_editor):
    ChannelType = apps.get_model("channels_core", "ChannelType")
    Channel = apps.get_model("channels_core", "Channel")
    seeded_codes = [code for code, _, _ in SEED_CHANNEL_TYPES]
    # Gỡ gán trước rồi mới xoá: FK để PROTECT nên xoá thẳng sẽ raise.
    Channel.objects.filter(channel_type__code__in=seeded_codes).update(channel_type=None)
    ChannelType.objects.filter(code__in=seeded_codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("channels_core", "0023_channel_price_book_to_catalog"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChannelType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "code",
                    models.SlugField(
                        help_text="Mã loại kênh, ổn định giữa các môi trường (shopee/tiktok/facebook/web).",
                        max_length=50,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(help_text="Tên hiển thị trên giao diện.", max_length=100)),
                (
                    "sort_order",
                    models.PositiveIntegerField(
                        default=0, help_text="Thứ tự hiển thị trong dropdown; nhỏ hơn đứng trước."
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Tắt để ẩn khỏi dropdown mà không xoá dữ liệu kênh đã gán.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "channel_types",
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.AddField(
            model_name="channel",
            name="channel_type",
            field=models.ForeignKey(
                blank=True,
                help_text="Loại kênh bán (Shopee/TikTok/Facebook/Web). Rỗng = chưa phân loại.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="channels",
                to="channels_core.channeltype",
            ),
        ),
        migrations.RunPython(seed_and_backfill, unseed),
    ]
