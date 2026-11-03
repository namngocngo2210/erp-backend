"""Index `(channel_id, created_at) WHERE deleted_at IS NULL` cho feed conversion
theo kênh (`apps.conversion_feed`, docs/google-ads-web-conversion-feed.md).

atomic=False + AddIndexConcurrently: bảng orders nóng, không khoá ghi. CHẠY GIỜ THẤP
ĐIỂM. Đứt giữa chừng để lại index INVALID — `DROP INDEX CONCURRENTLY` rồi migrate lại.
"""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("orders", "0147_order_total_after_discount_index"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                fields=["channel", "created_at"],
                condition=Q(deleted_at__isnull=True),
                name="ord_channel_created_idx",
            ),
        ),
    ]
