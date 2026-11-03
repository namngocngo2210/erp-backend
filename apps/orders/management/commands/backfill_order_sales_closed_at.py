"""Backfill Order.sales_closed_at từ order_journey_events.

Dựng lại ĐÚNG luật ghi của `OrderServiceHelpers._touch_sales_closed_at`: mốc chốt
là event đổi trạng thái sale MỚI NHẤT có `to_code` thuộc nhóm chốt và `from_code`
KHÔNG thuộc nhóm chốt (tức lằn ranh NGOÀI -> TRONG). Lấy MAX chứ không MIN vì đơn
rơi ra khỏi nhóm rồi chốt lại phải mang mốc lần chốt gần nhất.

`from_code` NULL được tính là "ngoài nhóm": đó là event `assessment_created` (lần
đầu lưu đánh giá) hoặc event cũ ghi trước khi có cột from_code.

Giới hạn dữ liệu: order_journey_events chỉ có từ 2026-04-01. Đơn chốt trước mốc
đó sẽ mãi NULL — không có nguồn nào khác để suy ra, và KHÔNG được lấy tạm
created_at/kiotviet_purchase_date thay thế (sẽ tạo ra ngày chốt giả).

Mặc định DRY-RUN, --apply để ghi thật. Idempotent: chạy lại chỉ ghi thêm phần
còn thiếu, trừ khi --force. Ghi bằng bulk_update — không bắn signal/WS event.
"""
from django.core.management.base import BaseCommand
from django.db.models import Max, Q

from apps.orders.crm_catalog import CLOSED_SALES_STATUS_CODES
from apps.orders.models import Order, OrderJourneyEvent

# Cùng danh sách event_type mà _upsert_order_assessment ghi khi sales_status_code
# đổi: "sales_status_changed" cho mọi lần đổi sau đó, "assessment_created" cho lần
# lưu đánh giá ĐẦU TIÊN (lần này không bắn sales_status_changed).
_CLOSING_EVENT_TYPES = ("sales_status_changed", "assessment_created")


class Command(BaseCommand):
    help = (
        "Backfill Order.sales_closed_at từ order_journey_events. "
        "Mặc định dry-run; --apply để ghi."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Ghi thật vào DB.")
        parser.add_argument(
            "--batch-size", type=int, default=5000, help="Số đơn ghi mỗi lô bulk_update."
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ghi đè cả ô đã có giá trị (mặc định chỉ điền ô đang NULL).",
        )

    @staticmethod
    def _closed_at_by_order():
        """(order_id, mốc chốt) — event vào-nhóm-chốt mới nhất của từng đơn."""
        return (
            OrderJourneyEvent.objects.filter(
                Q(event_type__in=_CLOSING_EVENT_TYPES),
                Q(to_code__in=CLOSED_SALES_STATUS_CODES),
            )
            .filter(Q(from_code__isnull=True) | ~Q(from_code__in=CLOSED_SALES_STATUS_CODES))
            .values("order_id")
            .annotate(closed_at=Max("created_at"))
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        batch_size = max(1, options["batch_size"])
        force = options["force"]

        base = self._closed_at_by_order()
        # Keyset pagination theo order_id thay vì .iterator(): iterator() dùng
        # server-side cursor, prod đi qua PgBouncer transaction pooling nên cursor
        # chết giữa chừng (cùng lý do với backfill_landing_confirmed_amount).
        last_order_id = 0
        stats = {"matched": 0, "written": 0, "skipped_filled": 0, "missing_order": 0}

        while True:
            rows = list(
                base.filter(order_id__gt=last_order_id).order_by("order_id")[:batch_size]
            )
            if not rows:
                break
            last_order_id = rows[-1]["order_id"]
            stats["matched"] += len(rows)

            closed_at_by_id = {row["order_id"]: row["closed_at"] for row in rows}
            # Một query duy nhất rồi quyết ở Python: `Order.objects` đã lọc đơn
            # mirror nên phần hụt chính là đơn ngoài phạm vi backfill.
            orders = list(
                Order.objects.filter(id__in=list(closed_at_by_id)).only("id", "sales_closed_at")
            )
            stats["missing_order"] += len(closed_at_by_id) - len(orders)

            to_write = []
            for order in orders:
                if order.sales_closed_at is not None and not force:
                    stats["skipped_filled"] += 1
                    continue
                new_value = closed_at_by_id[order.id]
                if order.sales_closed_at == new_value:
                    continue
                order.sales_closed_at = new_value
                to_write.append(order)

            if to_write and apply_changes:
                Order.objects.bulk_update(to_write, ["sales_closed_at"], batch_size=batch_size)
            stats["written"] += len(to_write)
            self.stdout.write(
                f"...đã duyệt tới order_id={last_order_id} "
                f"(khớp {stats['matched']}, sẽ ghi {stats['written']})"
            )

        mode = "ĐÃ GHI" if apply_changes else "DRY-RUN (chưa ghi)"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: {stats['written']} đơn được điền mốc chốt "
                f"/ {stats['matched']} đơn có event chốt "
                f"(bỏ qua vì đã có mốc: {stats['skipped_filled']}, "
                f"ngoài phạm vi: {stats['missing_order']})."
            )
        )
        if not apply_changes:
            self.stdout.write("Chạy lại kèm --apply để ghi thật.")
