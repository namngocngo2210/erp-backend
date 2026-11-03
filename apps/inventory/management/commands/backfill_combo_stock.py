"""Chuyển sổ tồn từ SKU combo sang mã lẻ — chạy lại được sau khi khai thêm combo.

Migration 0038 chỉ chạy MỘT lần và chỉ xử lý các combo đã khai thành phần TẠI
THỜI ĐIỂM ĐÓ. Trên prod ngày 20/08 mới có 3/121 SKU dạng combo được khai, nên mỗi
lần vận hành khai thêm một combo thì phần tồn cũ của nó vẫn nằm trên SKU combo và
không có đường nào chuyển — lệnh này là đường đó. Xem
docs/combo-stock-expansion-2026-08-20.md.

Lệnh SỐNG, idempotent, chạy lại bao nhiêu lần cũng được: lượt trước đã đưa
outstanding của SKU combo về 0 nên lượt sau là no-op.

    python manage.py backfill_combo_stock --dry-run
    python manage.py backfill_combo_stock
"""
import json

from django.core.management.base import BaseCommand

from apps.inventory.application.services.combo_stock_backfill import (
    backfill_combo_stock,
    combo_outstanding_report,
)


class Command(BaseCommand):
    help = "Chuyển tồn đã trừ trên SKU combo sang các mã lẻ đã khai thành phần."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Chỉ liệt kê, không ghi sổ"
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        before = combo_outstanding_report()
        self.stdout.write(
            f"Trước: {len(before)} SKU combo còn hàng ngoài kho — {json.dumps(before)}"
        )
        summary = backfill_combo_stock(dry_run=dry_run)
        for line in summary["lines"]:
            self.stdout.write(
                f"  đơn {line['order_id']} · kho {line['warehouse_id']} · "
                f"combo #{line['combo_product_id']} ×{line['quantity']} → "
                + ", ".join(
                    f"#{c['product_id']}×{c['quantity']}" for c in line["components"]
                )
            )
        if summary["skipped_returned"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Bỏ qua {summary['skipped_returned']} đơn vị đã hoàn về kho dưới "
                    "chính SKU combo — cần phiếu chuyển đổi tay nếu muốn tách ra mã lẻ."
                )
            )
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY-RUN: chưa ghi gì."))
            return
        after = combo_outstanding_report()
        self.stdout.write(
            self.style.SUCCESS(
                f"Xong: {summary['combos']} combo · {summary['orders']} đơn · "
                f"{summary['moved']} đơn vị đã chuyển. Còn lại: {json.dumps(after)}"
            )
        )
