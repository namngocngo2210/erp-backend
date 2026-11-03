"""Nắn ngày xuất cho đơn xuất lại `<mã>.NN` đã nhận sổ trước khi có fix.

Chạy `--dry-run` trước để duyệt danh sách, rồi chạy thật:

    python manage.py backfill_sibling_dispatch_date --dry-run
    python manage.py backfill_sibling_dispatch_date
"""
import json

from django.core.management.base import BaseCommand

from apps.inventory.application.services.sibling_dispatch_date_backfill import (
    backfill_sibling_dispatch_dates,
)


class Command(BaseCommand):
    help = "Nắn `occurred_at` của sổ xuất đã chuyển sang đơn xuất lại `<mã>.NN`."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Chỉ liệt kê, không ghi"
        )
        parser.add_argument(
            "--limit", type=int, default=0, help="Giới hạn số dòng xử lý (0 = tất cả)"
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        summary = backfill_sibling_dispatch_dates(
            dry_run=dry_run, limit=int(options.get("limit") or 0)
        )
        for row in summary["rows"]:
            prefix = "DRY-RUN sẽ nắn" if dry_run else "Đã nắn"
            self.stdout.write(
                f"{prefix}: đơn #{row['order_id']} (bút toán #{row['movement_id']}) "
                f"{row['occurred_at']} → {row['new_occurred_at']} "
                f"— nguồn đơn huỷ #{row['source_order_id']}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"backfill_sibling_dispatch_date done — quét {summary['scanned']} "
                f"dòng, nắn {summary['updated']}, bỏ qua {summary['skipped']}."
            )
        )
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
