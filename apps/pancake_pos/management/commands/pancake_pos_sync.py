"""Đồng bộ đầy đủ sản phẩm CRM → Pancake POS (docs/pancake-pos-product-sync.md §3.3).

    manage.py pancake_pos_sync --dry-run                 # bắt buộc chạy trước lần đầu
    manage.py pancake_pos_sync --dry-run --limit 20
    manage.py pancake_pos_sync --sku OHI01-DEN --sku MDD01-XAM   # kéo cả nhóm của SKU
    manage.py pancake_pos_sync --limit 10                # ghi thật, 10 nhóm đầu
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.pancake_pos.application.services.factory import build_product_sync_service
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosError


class Command(BaseCommand):
    help = "Đồng bộ sản phẩm CRM lên Pancake POS (full sync; --dry-run không gọi API ghi)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Chỉ báo cáo, không gọi API ghi")
        parser.add_argument("--limit", type=int, default=None, help="Số NHÓM tối đa xử lý")
        parser.add_argument("--sku", action="append", default=[], help="Chỉ đồng bộ nhóm chứa SKU này (lặp được)")
        parser.add_argument("--show-samples", action="store_true", help="In payload mẫu (tối đa 20)")

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        limit = options["limit"]
        if limit is not None and limit <= 0:
            raise CommandError("--limit phải > 0")
        try:
            service = build_product_sync_service(dry_run=dry_run)
            report = service.full_sync(dry_run=dry_run, limit=limit, skus=options["sku"] or None)
        except PancakePosError as exc:
            raise CommandError(str(exc)) from exc

        c = report.counters
        self.stdout.write(
            f"Lượt #{report.run_id} ({report.mode}): ứng viên {report.candidates}, đủ điều kiện {report.eligible}, "
            f"nhóm {report.groups}, mẫu mã trên Pancake {report.pancake_variations}"
        )
        if report.ineligible:
            reasons = ", ".join(f"{k}={v}" for k, v in sorted(report.ineligible.items()))
            self.stdout.write(f"Bị loại: {reasons}")
        self.stdout.write(
            f"Tạo {c.created} · Liên kết {c.linked} · Cập nhật {c.updated} · Không đổi {c.skipped} · "
            f"Ẩn {c.hidden} · Xung đột {c.conflicts} · Lỗi {c.errors}"
        )
        for item in report.conflicts[:20]:
            self.stdout.write(self.style.WARNING(f"  xung đột {item['group_key']}/{item['sku']}: {item['message']}"))
        for item in report.errors[:20]:
            self.stdout.write(self.style.ERROR(f"  lỗi {item['group_key']}: {item['message']}"))
        if options["show_samples"]:
            self.stdout.write(json.dumps(report.samples, ensure_ascii=False, indent=2))
        style = self.style.WARNING if (c.conflicts or c.errors) else self.style.SUCCESS
        self.stdout.write(style("Xong (dry-run, không ghi gì lên Pancake)." if dry_run else "Xong."))
