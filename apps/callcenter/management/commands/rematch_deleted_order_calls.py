"""Chuyển ghi âm đang gắn đơn XOÁ MỀM sang đơn tạo lại cùng SĐT.

Cron sync Zenify đã tự làm việc này mỗi tick (rematch_records_of_deleted_orders);
command để xem trước / chạy tay ngay sau deploy thay vì đợi tick.

    python manage.py rematch_deleted_order_calls --dry-run   # liệt kê record → đơn mới
    python manage.py rematch_deleted_order_calls             # ghi thật
"""

from django.core.management.base import BaseCommand

from apps.callcenter.sync_service import rematch_records_of_deleted_orders


class Command(BaseCommand):
    help = "Chuyển CallRecord đang gắn đơn xoá mềm sang đơn còn sống cùng SĐT"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Chỉ liệt kê, không ghi")

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        summary = rematch_records_of_deleted_orders(dry_run=dry_run)
        if dry_run:
            for record_id, old_order_id, new_order_id in summary["moves"]:
                self.stdout.write(f"record {record_id}: đơn {old_order_id} → {new_order_id}")
            self.stdout.write(
                f"DRY-RUN: quét {summary['scanned']} record gắn đơn xoá, "
                f"sẽ chuyển {summary['moved']}"
            )
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Quét {summary['scanned']} record gắn đơn xoá, đã chuyển {summary['moved']}"
            )
        )
