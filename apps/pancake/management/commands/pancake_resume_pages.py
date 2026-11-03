"""Khôi phục page Pancake bị TỰ tắt (token lỗi) và quét bù lead trong cửa sổ cho số.

Page bị poll tự tắt mang `token_error` khác rỗng; page do người tắt tay thì không.
Bật lại qua UI đặt cursor = now → bỏ sót lead cho số trong lúc page tắt. Lệnh này
sinh lại token, đặt cursor = now − PHONE_WINDOW (24h — số cũ hơn poll vốn bỏ qua) và
chạy poll ngay, ngoài Celery (không vướng time limit 150s khi tồn nhiều hội thoại).

    python manage.py pancake_resume_pages                  # dry-run: liệt kê page
    python manage.py pancake_resume_pages --apply
    python manage.py pancake_resume_pages --apply --page 451593898044414
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.common.db_guards import single_flight
from apps.pancake.application.services.page_service import PageNotEligible, PancakePageService, get_client
from apps.pancake.application.services.poll_service import PancakePollService
from apps.pancake.infrastructure.pancake_client import PancakeError
from apps.pancake.models import PancakePage
from apps.pancake.tasks import POLL_LOCK_KEY, POLL_LOCK_TTL


class Command(BaseCommand):
    help = "Bật lại page Pancake bị tự tắt do lỗi token và quét bù lead 24h gần nhất."

    def add_arguments(self, parser):
        parser.add_argument("--page", action="append", default=[], help="pancake_page_id, lặp được")
        parser.add_argument("--apply", action="store_true", help="Thực hiện (mặc định chỉ liệt kê)")

    def handle(self, *args, **options):
        pages = PancakePage.objects.filter(is_enabled=False, channel__isnull=False).exclude(token_error="")
        if options["page"]:
            pages = pages.filter(pancake_page_id__in=options["page"])
        pages = list(pages.order_by("id"))
        for page in pages:
            self.stdout.write(f"#{page.id} {page.name} ({page.pancake_page_id}) cursor={page.polled_until} lỗi={page.token_error}")
        if not pages:
            self.stdout.write("Không có page bị tự tắt.")
            return
        if not options["apply"]:
            self.stdout.write(self.style.WARNING(f"Dry-run: {len(pages)} page. Thêm --apply để bật lại + quét bù."))
            return

        client = get_client()
        if client is None:
            raise CommandError("Chưa có PancakeConnection active.")
        page_service, poll_service = PancakePageService(client), PancakePollService(client)
        resume_from = timezone.now() - PancakePollService.PHONE_WINDOW
        # Giữ lock của task poll: lượt Celery chen giữa sẽ bỏ qua thay vì quét trùng.
        with single_flight(POLL_LOCK_KEY, POLL_LOCK_TTL) as acquired:
            if not acquired:
                raise CommandError("Lượt poll Celery đang chạy — thử lại sau ít phút.")
            for page in pages:
                try:
                    page_service.enable_page(page, resume_from=resume_from)
                except (PageNotEligible, PancakeError) as exc:
                    self.stdout.write(self.style.ERROR(f"#{page.id} không bật được: {exc}"))
                    continue
                result = poll_service.poll_page(page)
                self.stdout.write(
                    f"#{page.id} fetched={result.fetched} leads={result.leads_created} "
                    f"orders={result.orders_created} skipped={result.skipped} {result.error}"
                )
        self.stdout.write(self.style.SUCCESS("Xong."))
