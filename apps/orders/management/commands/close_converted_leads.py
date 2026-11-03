from django.core.management.base import BaseCommand

from apps.orders.application.services.lead_close_service import (
    DEFAULT_DAYS_WINDOW,
    DEFAULT_LOOKBACK_DAYS,
    LeadCloseService,
)


class Command(BaseCommand):
    help = (
        "Đóng lead (chưa lên hóa đơn) mà cùng SĐT đã có hóa đơn KiotViet trong "
        "cửa sổ khớp — dọn giao dịch bị ghi đôi. Chạy hồi tố: --lookback-days 0."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days-window", type=int, default=DEFAULT_DAYS_WINDOW)
        parser.add_argument(
            "--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
            help="Chỉ xét lead tạo trong N ngày gần nhất; 0 = toàn bộ lịch sử.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        summary = LeadCloseService.close_converted(
            days_window=options["days_window"],
            lookback_days=options["lookback_days"],
            dry_run=options["dry_run"],
        )
        self.stdout.write(
            f"scanned={summary['scanned']} closed={summary['closed']} dry_run={summary['dry_run']}"
        )
