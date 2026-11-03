# Thêm `orders.fb_campaign_id` để quy đơn/lead landing về chiến dịch Facebook
# (đọc `utm_id` trên url_page — xem apps/orders/meta_attribution.py). Cùng cách làm
# với 0116 (fb_ad_id): AddField db_default (chỉ sửa catalog) tách khỏi index partial
# build CONCURRENTLY, `atomic = False` vì CONCURRENTLY không chạy trong transaction.
#
# Không backfill trong migration — chạy tay `backfill_fb_attribution --apply` sau
# deploy (đọc url_page của đơn cũ từ 07/2026, ~1.1k đơn/tháng có id).

from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("orders", "0142_order_address_level2"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="fb_campaign_id",
            field=models.CharField(blank=True, db_default="", default="", max_length=64),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ord_fb_campaign_id_idx "
                        "ON orders (fb_campaign_id) WHERE fb_campaign_id > ''"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS ord_fb_campaign_id_idx",
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="order",
                    index=models.Index(
                        condition=models.Q(("fb_campaign_id__gt", "")),
                        fields=["fb_campaign_id"],
                        name="ord_fb_campaign_id_idx",
                    ),
                ),
            ],
        ),
    ]
