"""Backfill nguồn đơn sàn (Shopee/TikTok) từ prefix mã hoá đơn.

Đơn sàn đổ về qua KiotViet mang mã hoá đơn prefix nhận diện (HDTTS_ = TikTok,
HDSPE_ = Shopee) nhưng dữ liệu cũ chưa có nguồn hoặc gắn nhầm nguồn khác.
Command này GHI ĐÈ order_source theo prefix — prefix là bằng chứng chắc chắn
đơn từ sàn (đã chốt với nghiệp vụ). Đơn mới đã được auto-detect ở Order.save().

Mặc định chạy DRY-RUN (chỉ đếm, thống kê theo nguồn hiện tại). Thêm --apply để ghi.

Ví dụ:
    python manage.py backfill_marketplace_order_source           # dry-run
    python manage.py backfill_marketplace_order_source --apply   # ghi thật
"""
from django.core.management.base import BaseCommand

from apps.orders.models import INVOICE_CODE_SOURCE_PREFIXES, Order


class Command(BaseCommand):
    help = (
        "Gắn order_source shopee/tiktok cho đơn có mã hoá đơn prefix HDSPE_/HDTTS_ "
        "(ghi đè nguồn cũ nếu khác). Mặc định dry-run; dùng --apply để ghi."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Ghi thật vào DB. Bỏ qua = chỉ dry-run (đếm).",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        total_updated = 0

        for prefix, source in INVOICE_CODE_SOURCE_PREFIXES.items():
            qs = Order.objects.filter(invoice_code__istartswith=prefix).exclude(
                order_source=source
            )

            # Thống kê theo nguồn hiện tại để thấy rõ sẽ ghi đè bao nhiêu đơn.
            by_source: dict[str, int] = {}
            for src in qs.values_list("order_source", flat=True):
                key = src or "(null)"
                by_source[key] = by_source.get(key, 0) + 1
            count = sum(by_source.values())

            self.stdout.write(f"{prefix}* -> {source}: {count} đơn cần cập nhật")
            for key in sorted(by_source):
                self.stdout.write(f"  - đang là {key}: {by_source[key]}")

            if apply and count:
                updated = qs.update(order_source=source)
                total_updated += updated
                self.stdout.write(self.style.SUCCESS(f"  Đã cập nhật {updated} đơn."))

        if not apply:
            self.stdout.write("DRY-RUN: chưa ghi gì. Thêm --apply để cập nhật order_source.")
            return

        self.stdout.write(self.style.SUCCESS(f"Hoàn tất: {total_updated} đơn được gắn nguồn sàn."))
