"""Ghi bù sổ xuất cho đơn XUẤT LẠI bị bỏ rơi (docs/reissue-ledger-leak-2026-07-21.md).

Chạy `--dry-run` trước để duyệt danh sách cặp, rồi chạy thật:

    python manage.py repair_reissue_ledger --dry-run
    python manage.py repair_reissue_ledger
"""
import json

from django.core.management.base import BaseCommand

from apps.inventory.application.services.reissue_ledger_repair_service import (
    ReissueLedgerRepairService,
)


class Command(BaseCommand):
    help = "Ghi bù sổ xuất cho đơn xuất lại `<mã>.NN` mà đơn huỷ đã thả tồn."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Chỉ liệt kê, không ghi sổ"
        )
        parser.add_argument(
            "--limit", type=int, default=0, help="Giới hạn số cặp xử lý (0 = không giới hạn)"
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        summary = ReissueLedgerRepairService.sweep(
            dry_run=dry_run, limit=int(options.get("limit") or 0)
        )
        for pair in summary["pairs"]:
            prefix = "DRY-RUN sẽ ghi bù" if dry_run else "Đã ghi bù"
            self.stdout.write(
                f"{prefix}: đơn huỷ #{pair['cancelled_order_id']} → "
                f"đơn xuất lại #{pair['sibling_order_id']} ({pair['quantity']} sản phẩm)"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"repair_reissue_ledger done — {summary['leaks']} cặp lệch, "
                f"{summary['repaired']} cặp đã ghi bù."
            )
        )
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
