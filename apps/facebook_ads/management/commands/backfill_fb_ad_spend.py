"""Backfill chi tiêu grain ad theo khoảng ngày, chia nhỏ theo tháng.

Chạy TAY, không đưa vào cron: một lượt 365 ngày × vài trăm ad vượt xa
soft_time_limit của task định kỳ và dễ đụng rate limit Graph API.

    python manage.py backfill_fb_ad_spend --since 2025-07-27 --until 2026-07-27
"""

import calendar
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.facebook_ads.infrastructure.ad_spend_sync import sync_ad_spend_for_app
from apps.facebook_ads.models import FacebookApp


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
    help = "Backfill chi tiêu Facebook Ads ở grain ad theo khoảng ngày."

    def add_arguments(self, parser):
        parser.add_argument("--since", required=True, help="YYYY-MM-DD")
        parser.add_argument("--until", required=True, help="YYYY-MM-DD")
        parser.add_argument("--app-id", type=int, default=None, help="Chỉ chạy 1 FacebookApp")

    def handle(self, *args, **options):
        try:
            since = date.fromisoformat(options["since"])
            until = date.fromisoformat(options["until"])
        except ValueError as exc:
            raise CommandError(f"Ngày không hợp lệ: {exc}") from exc
        if since > until:
            raise CommandError("--since phải trước hoặc bằng --until")

        apps_qs = FacebookApp.objects.filter(deleted_at__isnull=True)
        if options["app_id"]:
            apps_qs = apps_qs.filter(pk=options["app_id"])

        chunks = month_chunks(since, until)
        total_rows = 0
        for app in apps_qs:
            if app.token_status == FacebookApp.TOKEN_INVALID:
                self.stdout.write(f"Bỏ qua app {app.pk} ({app.name}) — token hỏng.")
                continue
            for chunk_since, chunk_until in chunks:
                result = sync_ad_spend_for_app(app, since=chunk_since, until=chunk_until)
                total_rows += result["rows_upserted"]
                self.stdout.write(
                    f"app {app.pk} {chunk_since}..{chunk_until}: "
                    f"{result['rows_upserted']} dòng, "
                    f"{result['lazy_fetched_ads']} ad bổ sung"
                )
        self.stdout.write(self.style.SUCCESS(f"Xong. Tổng {total_rows} dòng."))
