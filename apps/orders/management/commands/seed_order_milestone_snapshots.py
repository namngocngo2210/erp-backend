"""Seed MỘT LẦN snapshot mốc vòng đời cho đơn tạo trước khi có bảng.

Suy ngược từ dữ liệu hiện có (spec 2026-08-04-order-milestone-snapshots §3.6):
  - ``created``: đơn scope ingest (nguồn landing hoặc có LandingIngestTrace
    CREATED) với ``landing_confirmed_amount > 0`` → total = field đó.
  - ``invoiced``: đơn có ``invoice_code`` → total = ``total_after_discount``
    HIỆN TẠI (xấp xỉ — số thật tại thời điểm lên hóa đơn không còn).

``amount_source="seeded"`` đánh dấu số suy ngược để đối soát phân biệt với số
chụp tại chỗ. Idempotent nhờ get_or_create + unique (order, milestone) — chạy
lại không nhân đôi, KHÔNG đè snapshot thật đã có. Mặc định DRY-RUN, --apply để
ghi. Keyset pagination theo id (không dùng iterator() — PgBouncer).
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.orders.models import (
    LandingIngestTraceStatus,
    Order,
    OrderMilestone,
    OrderMilestoneSnapshot,
    OrderSource,
)


class Command(BaseCommand):
    help = (
        "Seed snapshot mốc created/invoiced cho đơn có trước bảng "
        "order_milestone_snapshots. Mặc định dry-run; --apply để ghi."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Ghi thật vào DB.")
        parser.add_argument(
            "--batch-size", type=int, default=500, help="Số đơn xử lý mỗi lượt."
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        batch_size = options["batch_size"]

        created_qs = (
            Order.objects.filter(deleted_at__isnull=True, landing_confirmed_amount__gt=0)
            .filter(
                Q(order_source=OrderSource.LANDING)
                | Q(landing_ingest_traces__status=LandingIngestTraceStatus.CREATED)
            )
            .only("id", "landing_confirmed_amount", "quantity", "sku")
            .distinct()
        )
        invoiced_qs = (
            Order.objects.filter(deleted_at__isnull=True)
            .exclude(invoice_code="")
            .only("id", "total_amount", "total_after_discount", "quantity", "sku")
        )

        stats = {"created": 0, "invoiced": 0}

        def _seed(qs, milestone: str, defaults_for) -> None:
            last_id = 0
            while True:
                batch = list(qs.filter(id__gt=last_id).order_by("id")[:batch_size])
                if not batch:
                    return
                last_id = batch[-1].id
                for order in batch:
                    if not apply:
                        # Dry-run: đếm đơn CHƯA có snapshot mốc này.
                        exists = OrderMilestoneSnapshot.objects.filter(
                            order=order, milestone=milestone
                        ).exists()
                        if not exists:
                            stats[milestone] += 1
                        continue
                    _, created = OrderMilestoneSnapshot.objects.get_or_create(
                        order=order, milestone=milestone, defaults=defaults_for(order)
                    )
                    if created:
                        stats[milestone] += 1

        _seed(
            created_qs,
            OrderMilestone.CREATED,
            lambda o: {
                "total_amount": o.landing_confirmed_amount,
                "total_after_discount": o.landing_confirmed_amount,
                "quantity": o.quantity,
                "sku": o.sku,
                "amount_source": "seeded",
            },
        )
        _seed(
            invoiced_qs,
            OrderMilestone.INVOICED,
            lambda o: {
                "total_amount": o.total_amount,
                "total_after_discount": o.total_after_discount,
                "quantity": o.quantity,
                "sku": o.sku,
                "amount_source": "seeded",
            },
        )

        self.stdout.write(f"Snapshot created cần seed: {stats['created']}")
        self.stdout.write(f"Snapshot invoiced cần seed: {stats['invoiced']}")
        if not apply:
            self.stdout.write("DRY-RUN: chưa ghi gì. Thêm --apply để seed.")
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Đã seed {stats['created']} created + {stats['invoiced']} invoiced."
            )
        )
