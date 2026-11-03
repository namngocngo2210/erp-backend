"""Backfill: ép show_orders=False cho các đơn đã ĐỔ QUA INGEST nhưng bị lọt vào
trang đơn hàng thường (show_orders=True).

Bối cảnh: đơn đổ qua /api/orders/ingest (hoặc lark-ingest) là lead của trang
Landing nên phải có show_orders=False. Code hiện tại đã ép đúng, nhưng các đơn
tạo bằng bản backend cũ (trước khi có guard) vẫn còn show_orders=True nên hiện ở
/orders và không nằm đúng tab Landing (vd "Messenger xác nhận đơn").

Phạm vi AN TOÀN: chỉ đụng tới đơn có LandingIngestTrace status=CREATED (tức chắc
chắn sinh ra từ luồng ingest). KHÔNG đụng tới đơn tạo tay ở /orders dù đơn đó có
order_source — vì đơn tạo tay không có ingest trace.

Mặc định chạy DRY-RUN (chỉ đếm). Thêm --apply để ghi thật.

Ví dụ:
    python manage.py backfill_ingest_show_orders                 # dry-run, mọi nguồn
    python manage.py backfill_ingest_show_orders --source messenger
    python manage.py backfill_ingest_show_orders --apply         # ghi thật
"""
from django.core.management.base import BaseCommand

from apps.orders.models import LandingIngestTraceStatus, Order, OrderSource


class Command(BaseCommand):
    help = (
        "Đặt show_orders=False cho đơn đổ qua ingest đang bị lọt vào /orders "
        "(show_orders=True). Mặc định dry-run; dùng --apply để ghi."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Ghi thật vào DB. Bỏ qua = chỉ dry-run (đếm).",
        )
        parser.add_argument(
            "--source",
            default="",
            help="Giới hạn theo một order_source (vd: messenger). Bỏ trống = mọi nguồn ingest.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        source = str(options.get("source") or "").strip().lower()

        qs = (
            Order.objects.filter(
                deleted_at__isnull=True,
                show_orders=True,
                landing_ingest_traces__status=LandingIngestTraceStatus.CREATED,
            )
            .exclude(order_source=OrderSource.KOC)
            .distinct()
        )
        if source:
            if source not in set(OrderSource.values):
                self.stderr.write(f"order_source không hợp lệ: {source}")
                return
            qs = qs.filter(order_source=source)

        ids = list(qs.values_list("id", flat=True))
        total = len(ids)

        # Thống kê theo nguồn để dễ rà soát trước khi ghi.
        by_source: dict[str, int] = {}
        for src in (
            Order.objects.filter(id__in=ids).values_list("order_source", flat=True)
        ):
            key = src or "(null)"
            by_source[key] = by_source.get(key, 0) + 1

        self.stdout.write(f"Đơn ingest bị lọt vào /orders (show_orders=True): {total}")
        for key in sorted(by_source):
            self.stdout.write(f"  - {key}: {by_source[key]}")

        if total == 0:
            self.stdout.write("Không có đơn cần backfill.")
            return

        if not apply:
            self.stdout.write("DRY-RUN: chưa ghi gì. Thêm --apply để cập nhật show_orders=False.")
            return

        updated = Order.objects.filter(id__in=ids).update(show_orders=False)
        self.stdout.write(self.style.SUCCESS(f"Đã cập nhật show_orders=False cho {updated} đơn."))
