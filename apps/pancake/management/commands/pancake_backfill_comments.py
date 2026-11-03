"""Backfill toàn bộ comment Facebook cũ qua Pancake vào `pancake_comments`.

Chạy tay (idempotent, chạy lại được sau khi đứt):
    python manage.py pancake_backfill_comments                       # mọi page, từ 2020
    python manage.py pancake_backfill_comments --page 518854254652984 --since 2026-01-01
"""

from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.pancake.application.services.comment_backfill_service import PancakeCommentBackfillService
from apps.pancake.application.services.page_service import get_client
from apps.pancake.models import PancakePage


class Command(BaseCommand):
    help = "Backfill comment Facebook cũ từ Pancake (chỉ ghi pancake_posts/pancake_comments)."

    def add_arguments(self, parser):
        parser.add_argument("--since", default="2020-01-01", help="Lấy hội thoại cập nhật từ YYYY-MM-DD")
        parser.add_argument("--page", action="append", default=[], help="pancake_page_id, lặp được; mặc định mọi page")
        parser.add_argument("--refresh", action="store_true", help="Lấy lại cả hội thoại đã có comment")
        parser.add_argument("--sleep", type=float, default=0.2, help="Nghỉ giữa 2 request chi tiết (giây)")

    def handle(self, *args, **options):
        try:
            since = timezone.make_aware(datetime.fromisoformat(options["since"]))
        except ValueError as exc:
            raise CommandError(f"--since không hợp lệ: {options['since']}") from exc

        client = get_client()
        if client is None:
            raise CommandError("Chưa có PancakeConnection active.")

        pages = PancakePage.objects.filter(is_activated=True).order_by("id")
        if options["page"]:
            pages = pages.filter(pancake_page_id__in=options["page"])

        service = PancakeCommentBackfillService(client, sleep=options["sleep"])
        total = 0
        for page in pages:
            self.stdout.write(f"→ {page.name} ({page.pancake_page_id}) …")
            result = service.backfill_page(page, since=since, refresh=options["refresh"])
            total += result.comments_saved
            self.stdout.write(
                f"  hội thoại={result.conversations} comment={result.comment_conversations} "
                f"bỏ_qua_đã_có={result.skipped_stored} đã_lưu={result.comments_saved} "
                f"lỗi={result.errors} {result.error}"
            )
        self.stdout.write(self.style.SUCCESS(f"Xong: {total} comment đã lưu."))
