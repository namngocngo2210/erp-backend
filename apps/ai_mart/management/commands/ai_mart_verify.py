"""Liệt kê mã closed-set có trong dữ liệu nhưng thiếu nhãn — chạy trên PROD
trước khi trỏ MCP vào schema ai. Mã lạ (vd invoice_status = 4/5): HỎI user
nghĩa là gì rồi thêm vào domain/dictionary.py + data migration, KHÔNG đoán."""

from django.core.management.base import BaseCommand
from django.db import connection

from apps.ai_mart.domain.coverage import PAIRS


class Command(BaseCommand):
    help = "Soi mã chưa có nhãn trong ai.don_hang"

    def handle(self, *args, **options):
        gaps = []
        with connection.cursor() as cur:
            for ma, text in PAIRS:
                cur.execute(
                    f"SELECT DISTINCT {ma} FROM ai.don_hang"
                    f" WHERE {ma} IS NOT NULL AND {text} IS NULL"
                )
                gaps.extend((ma, row[0]) for row in cur.fetchall())
        for ma, val in gaps:
            self.stdout.write(self.style.WARNING(f"{ma}: {val}"))
        self.stdout.write(f"{len(gaps)} mã chưa có nhãn")
