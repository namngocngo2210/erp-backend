"""Đối soát danh sách "đơn huỷ chờ hàng về" với sổ kho.

Dọn các dòng kẹt `waiting` dù đơn không còn nợ kho — chủ yếu do sổ xuất đã được
chuyển sang đơn xuất lại `<mã>.NN` (sweep `repair_reissue_ledger`) mà dòng theo
dõi không ai đụng tới. Lệnh SỐNG, chạy lại được, không phải migration.

    python manage.py reconcile_awaiting_return --dry-run
    python manage.py reconcile_awaiting_return
"""
import json

from django.core.management.base import BaseCommand

from apps.inventory.application.services.awaiting_return_service import (
    AwaitingReturnService,
)


class Command(BaseCommand):
    help = "Đối soát dòng 'chờ hàng về' với sổ: đóng dòng đã hết nợ kho."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Chỉ liệt kê, không ghi"
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Giới hạn số dòng xử lý (0 = không giới hạn)",
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        summary = AwaitingReturnService.reconcile_waiting(
            dry_run=dry_run, limit=int(options.get("limit") or 0)
        )
        label = {
            "reissued": "sổ xuất đã chuyển sang đơn xuất lại → đóng",
            "returned": "hàng đã về đủ → 'hàng đã về'",
        }
        for row in summary["rows"]:
            prefix = "DRY-RUN" if dry_run else "Đã sửa"
            self.stdout.write(
                f"{prefix}: đơn #{row['order_id']} — {label[row['action']]}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"reconcile_awaiting_return done — quét {summary['scanned']} dòng, "
                f"giữ chờ {summary['still_waiting']}, đóng (xuất lại) "
                f"{summary['reissued']}, chuyển 'hàng đã về' {summary['returned']}."
            )
        )
        self.stdout.write(json.dumps(summary, ensure_ascii=False))
