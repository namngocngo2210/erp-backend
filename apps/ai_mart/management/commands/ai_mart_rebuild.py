"""Full rebuild toàn bộ bảng trong schema ai (initial load + tự lành).

Kết nối Django trên prod mang statement_timeout mặc định 30s
(DB_STATEMENT_TIMEOUT_MS, config/settings_parts/base.py) — chỉ `migrate` được
miễn. INSERT 547k dòng của ai.don_hang bị cắt ở 30s (sự cố deploy 28/08/2026),
trong khi task Celery đêm đã tự nới lên 1200s. Lệnh này phải tự nới y hệt,
không dựa vào người vận hành nhớ đặt env.
"""

from django.core.management.base import BaseCommand

from apps.ai_mart.application.etl_service import rebuild_all
from apps.common.db_guards import statement_timeout

DEFAULT_TIMEOUT_SECONDS = 3600


class Command(BaseCommand):
    help = "Full rebuild toàn bộ bảng trong schema ai (initial load + tự lành)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=DEFAULT_TIMEOUT_SECONDS,
            help="statement_timeout (giây) cho phiên chạy này; <= 0 = giữ mặc định của kết nối",
        )

    def handle(self, *args, **options):
        with statement_timeout(options["timeout"]):
            result = rebuild_all()
        for table, rows in result["tables"].items():
            self.stdout.write(f"{table}: {rows} dòng")
        self.stdout.write(f"cutoff: {result['cutoff']}")
