"""DEPRECATED — alias của ``backfill_ads_campaign_products --provider google``.

Giữ tên cũ để runbook/lịch sử shell còn chạy; logic sống ở
``apps/orders/management/commands/backfill_ads_campaign_products.py`` (gộp hai bản
Facebook/Google 2026-09-23, audit T3/P2-5). Dùng lệnh mới cho việc mới.
"""

from apps.orders.management.commands.backfill_ads_campaign_products import (
    Command as _BackfillAdsCampaignProductsCommand,
)


class Command(_BackfillAdsCampaignProductsCommand):
    help = "(Deprecated) = backfill_ads_campaign_products --provider google"
    fixed_provider = "google"
