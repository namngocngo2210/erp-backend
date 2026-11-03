"""Requeue record transcribe FAILED về PENDING (reset attempts).

Trước fix transient-không-đốt-attempt, một đợt Gemini/CDN chập chờn 15-20' có thể
đóng đinh hàng loạt record FAILED vĩnh viễn — command này là đường cứu chính thức
thay vì shell tay (audit 2026-07-18).

    python manage.py requeue_failed_transcripts            # toàn bộ FAILED
    python manage.py requeue_failed_transcripts --days 3   # FAILED trong 3 ngày gần đây
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.callcenter.models import CallRecord, TranscribeStatus


class Command(BaseCommand):
    help = "Requeue CallRecord transcribe FAILED → PENDING (reset attempts)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=0,
            help="Chỉ requeue record FAILED có updated_at trong N ngày gần đây (0 = tất cả)",
        )
        parser.add_argument("--dry-run", action="store_true", help="Chỉ đếm, không ghi")

    def handle(self, *args, **options):
        qs = CallRecord.objects.filter(transcribe_status=TranscribeStatus.FAILED)
        days = int(options.get("days") or 0)
        if days > 0:
            qs = qs.filter(updated_at__gte=timezone.now() - timedelta(days=days))

        if options.get("dry_run"):
            self.stdout.write(f"DRY-RUN: sẽ requeue {qs.count()} record FAILED")
            return

        updated = qs.update(
            transcribe_status=TranscribeStatus.PENDING,
            transcribe_attempts=0,
            transcribe_error="",
        )
        self.stdout.write(self.style.SUCCESS(f"Đã requeue {updated} record FAILED → PENDING"))
