import json

from django.core.management.base import BaseCommand, CommandError

from apps.kiotviet.infrastructure.services.returns_sync_service import (
    sync_returns_all_channels,
    sync_returns_for_channel,
)
from apps.kiotviet.models import KiotVietChannel


class Command(BaseCommand):
    help = "Đồng bộ mã phiếu trả hàng từ KiotViet (/returns) vào OrderRecall.return_slip_code."

    def add_arguments(self, parser):
        parser.add_argument("--channel-id", type=int, default=None, help="Chỉ chạy 1 KiotVietChannel")
        parser.add_argument("--from", dest="date_from", default="", help="Lọc returnDate từ (YYYY-MM-DD)")
        parser.add_argument("--to", dest="date_to", default="", help="Lọc returnDate đến (YYYY-MM-DD)")
        parser.add_argument("--dry-run", action="store_true", help="Chỉ log, không ghi DB")

    def handle(self, *args, **options):
        channel_id = options.get("channel_id")
        date_from = str(options.get("date_from") or "").strip() or None
        date_to = str(options.get("date_to") or "").strip() or None
        dry_run = bool(options.get("dry_run"))

        if channel_id is None:
            summary = sync_returns_all_channels(date_from=date_from, date_to=date_to, dry_run=dry_run)
        else:
            try:
                channel = KiotVietChannel.objects.get(id=channel_id, deleted_at__isnull=True)
            except KiotVietChannel.DoesNotExist as exc:
                raise CommandError(f"Không tìm thấy KiotVietChannel id={channel_id}") from exc
            summary = sync_returns_for_channel(
                channel, date_from=date_from, date_to=date_to, dry_run=dry_run
            )

        self.stdout.write(self.style.SUCCESS("Sync KiotViet returns completed."))
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
