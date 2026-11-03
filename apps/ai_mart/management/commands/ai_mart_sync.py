"""Chạy một nhịp upsert tăng dần nhóm nóng.

Cùng lý do với ai_mart_rebuild: nới statement_timeout của phiên vì mặc định
prod là 30s. Trần 600s = gấp đôi task Celery (180s) để chạy tay lúc đang điều
tra vẫn đủ rộng; nhịp bình thường phải xong dưới 60s (runbook bước 3).
"""

from django.core.management.base import BaseCommand

from apps.ai_mart.application.etl_service import sync_hot
from apps.common.db_guards import statement_timeout

DEFAULT_TIMEOUT_SECONDS = 600


class Command(BaseCommand):
    help = "Chạy một nhịp upsert tăng dần nhóm nóng"

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=int,
            default=DEFAULT_TIMEOUT_SECONDS,
            help="statement_timeout (giây) cho phiên chạy này; <= 0 = giữ mặc định của kết nối",
        )

    def handle(self, *args, **options):
        with statement_timeout(options["timeout"]):
            result = sync_hot()
        for table, rows in result["tables"].items():
            self.stdout.write(f"{table}: {rows} dòng")
        self.stdout.write(f"cutoff: {result['cutoff']}")
