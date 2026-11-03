"""Chạy tay MỘT lượt job cơ hội resale (cùng thân với cron `push_resale_delivered`).

    manage.py foxia_resale_push --dry-run   # chỉ đếm/in ứng viên, không ghi, không gọi Foxia
    manage.py foxia_resale_push             # đẩy thật — vẫn tôn trọng hai cờ FOXIA_*_ENABLED

Dry-run chạy được cả khi cờ tắt: dùng để xem trước số đơn sẽ đẩy TRƯỚC khi bật.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.foxia.application.services.resale_push_service import FoxiaResalePushService


class Command(BaseCommand):
    help = "Đẩy đơn giao thành công sang Foxia thành cơ hội resale (một lượt)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Chỉ liệt kê ứng viên")

    def handle(self, *args, **options):
        result = FoxiaResalePushService().run(dry_run=options["dry_run"])
        if options["dry_run"]:
            self.stdout.write(f"Ứng viên (tối đa 1 lượt): {result.candidates}")
            if result.order_ids:
                self.stdout.write("order_id: " + ", ".join(map(str, result.order_ids)))
            return
        self.stdout.write(result.summary())
