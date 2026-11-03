"""Gắn cờ ``has_address=False`` cho đơn tạo tay chưa có địa chỉ (trước fix MR !1522).

Trước fix, ``create_order_manual`` để ``has_address`` default True nên đơn tạo tay
không địa chỉ nhảy từ Tư vấn sang Landing/Messenger XN đơn khi sale điền địa chỉ
(sự cố HD001316). Command khôi phục cờ bất biến cho các đơn đó.

Phạm vi: đơn nguồn landing/hotline/zalo/messenger, tạo trong [--since, --until]
(ngày giờ VN), KHÔNG qua ingest (không có LandingIngestTrace — luồng ingest đã
gắn cờ đúng), đang ``has_address=True`` và có dấu vết tạo khi thiếu địa chỉ:
  - journey có lần đổi "Địa chỉ" từ rỗng sang có giá trị, HOẶC
  - hiện vẫn trống cả raw_address lẫn address.

Idempotent, mặc định DRY-RUN; --apply để ghi. Update bằng queryset.update() —
không bắn signal/WS event.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.orders.models import LandingIngestTrace, Order, OrderJourneyEvent
from apps.orders.sheet_rules import ORDER_FIELD_CHANGED_EVENT, SHEET3_SOURCES

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _vn_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=VN_TZ)


class Command(BaseCommand):
    help = "Gắn has_address=False cho đơn tạo tay thiếu địa chỉ. Mặc định dry-run; --apply để ghi."

    def add_arguments(self, parser):
        parser.add_argument("--since", type=date.fromisoformat, required=True, help="Ngày bắt đầu (YYYY-MM-DD, giờ VN).")
        parser.add_argument("--until", type=date.fromisoformat, required=True, help="Ngày kết thúc, tính cả ngày này.")
        parser.add_argument("--apply", action="store_true", help="Ghi thật vào DB.")

    @staticmethod
    def build_queryset(since: date, until: date):
        address_filled_later = OrderJourneyEvent.objects.filter(
            order=OuterRef("pk"),
            event_type=ORDER_FIELD_CHANGED_EVENT,
            payload_json__field="address",
            payload_json__from="",
        )
        still_blank = (Q(raw_address__isnull=True) | Q(raw_address="")) & (Q(address__isnull=True) | Q(address=""))
        return (
            Order.objects.filter(
                deleted_at__isnull=True,
                has_address=True,
                order_source__in=SHEET3_SOURCES,
                created_at__gte=_vn_start(since),
                created_at__lt=_vn_start(until + timedelta(days=1)),
            )
            .exclude(Exists(LandingIngestTrace.objects.filter(order=OuterRef("pk"))))
            .filter(Exists(address_filled_later) | still_blank)
        )

    def handle(self, *args, **options):
        since, until = options["since"], options["until"]
        if since > until:
            self.stderr.write("--since phải <= --until.")
            return

        rows = list(
            self.build_queryset(since, until)
            .order_by("id")
            .values_list("id", "invoice_code", "order_source")
        )
        self.stdout.write(f"Đơn tạo tay thiếu địa chỉ cần gắn cờ ({since} → {until}): {len(rows)}")
        for order_id, invoice_code, source in rows:
            self.stdout.write(f"  - {order_id}\t{invoice_code or '-'}\t{source}")

        if not options["apply"]:
            self.stdout.write("DRY-RUN: chưa ghi gì. Thêm --apply để cập nhật.")
            return

        updated = Order.objects.filter(id__in=[r[0] for r in rows], has_address=True).update(
            has_address=False, updated_at=timezone.now()
        )
        self.stdout.write(self.style.SUCCESS(f"Đã gắn has_address=False cho {updated} đơn."))
