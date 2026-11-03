"""Backfill chi phí grain chiến dịch theo khoảng ngày, chia nhỏ theo tháng.

Chạy TAY, không đưa vào cron: một lượt 365 ngày × cả cây MCC vượt xa soft_time_limit
của task định kỳ và dễ đụng quota Google Ads API.

    python manage.py backfill_gads_campaign_spend --since 2025-08-07 --until 2026-08-07
"""

import calendar
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.google_ads.infrastructure.campaign_sync import sync_campaign_spend_for_config
from apps.google_ads.models import GoogleAdsConfig


def month_chunks(since: date, until: date) -> list[tuple[str, str]]:
    """Cắt [since, until] thành các đoạn không vượt qua ranh giới tháng."""
    chunks: list[tuple[str, str]] = []
    cursor = since
    while cursor <= until:
        last_day = date(
            cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1],
        )
        end = min(last_day, until)
        chunks.append((cursor.isoformat(), end.isoformat()))
        cursor = end + timedelta(days=1)
    return chunks


class Command(BaseCommand):
    help = "Backfill chi phí Google Ads ở grain chiến dịch theo khoảng ngày."

    def add_arguments(self, parser):
        parser.add_argument("--since", required=True, help="YYYY-MM-DD")
        parser.add_argument("--until", required=True, help="YYYY-MM-DD")
        parser.add_argument("--config-id", type=int, default=None, help="Chỉ chạy 1 config")

    def handle(self, *args, **options):
        try:
            since = date.fromisoformat(options["since"])
            until = date.fromisoformat(options["until"])
        except ValueError as exc:
            raise CommandError(f"Ngày không hợp lệ: {exc}") from exc
        if since > until:
            raise CommandError("--since phải trước hoặc bằng --until")

        configs = GoogleAdsConfig.objects.filter(deleted_at__isnull=True)
        if options["config_id"]:
            configs = configs.filter(pk=options["config_id"])

        chunks = month_chunks(since, until)
        total_rows = 0
        for config in configs:
            for chunk_since, chunk_until in chunks:
                result = sync_campaign_spend_for_config(
                    config, since=chunk_since, until=chunk_until,
                )
                total_rows += result["rows_upserted"]
                self.stdout.write(
                    f"config {config.pk} {chunk_since}..{chunk_until}: "
                    f"{result['rows_upserted']} dòng, "
                    f"{result['failed_accounts']} tài khoản lỗi"
                )
        self.stdout.write(self.style.SUCCESS(f"Xong. Tổng {total_rows} dòng."))
