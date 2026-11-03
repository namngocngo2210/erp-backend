"""Chẩn đoán vì sao một đơn KHÔNG được chia tự động (lead auto-assign).

Dùng để xác nhận trên data thật: với mỗi đơn (theo order_id hoặc phone), in ra
mọi yếu tố mà ``LeadAssignmentService.assign_if_unassigned`` phụ thuộc vào, và
suy ra lý do skip.

Ví dụ:
    python manage.py diagnose_lead_assignment --phone 0363115097 0902391292 0973020552
    python manage.py diagnose_lead_assignment --order-id 12345 12346
    python manage.py diagnose_lead_assignment --phone 0363115097 --date 2026-07-07
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.orders.application.services.shift_service import ShiftService
from apps.orders.models import LandingIngestTrace, LeadAssignmentShare, Order
from apps.orders.sheet_rules import classify_sheet


class Command(BaseCommand):
    help = "Chẩn đoán lý do một đơn không được chia tự động (theo order_id hoặc phone)."

    def add_arguments(self, parser):
        parser.add_argument("--order-id", nargs="+", type=int, default=[])
        parser.add_argument("--phone", nargs="+", type=str, default=[])
        parser.add_argument(
            "--date",
            type=str,
            default="",
            help="Lọc thêm theo ngày tạo (YYYY-MM-DD) khi tra theo phone.",
        )

    def handle(self, *args, **options):
        orders = self._collect_orders(options)
        if not orders:
            self.stdout.write(self.style.WARNING("Không tìm thấy đơn nào khớp."))
            return
        for order in orders:
            self._diagnose(order)
            self.stdout.write("")

    def _collect_orders(self, options) -> list[Order]:
        qs = Order.objects.all().select_related("staff", "assessment")
        found: dict[int, Order] = {}
        if options["order_id"]:
            for o in qs.filter(id__in=options["order_id"]):
                found[o.id] = o
        if options["phone"]:
            phone_qs = qs.filter(phone__in=options["phone"])
            if options["date"]:
                phone_qs = phone_qs.filter(created_at__date=options["date"])
            for o in phone_qs.order_by("-created_at"):
                found[o.id] = o
        return sorted(found.values(), key=lambda o: o.created_at)

    def _diagnose(self, order: Order) -> None:
        w = self.stdout.write
        w("=" * 72)
        created_local = timezone.localtime(order.created_at)
        w(f"Order #{order.id}  |  {order.name}  |  {order.phone}")
        w(f"  created_at (local) : {created_local:%Y-%m-%d %H:%M:%S}")
        w(f"  order_source       : {order.order_source!r}")
        w(f"  staff_id           : {order.staff_id} "
          f"({order.staff.username if order.staff_id else '—'})")
        w(f"  status             : {order.status}  |  deleted_at: {order.deleted_at}")

        # 1) Nguồn đơn: có LandingIngestTrace không? (webhook landing / lark vs khác)
        traces = list(
            LandingIngestTrace.objects.filter(order_id=order.id).order_by("created_at")
        )
        if traces:
            for t in traces:
                w(f"  trace              : source={t.source} status={t.status} "
                  f"dry_run={t.is_dry_run} created={timezone.localtime(t.created_at):%Y-%m-%d %H:%M:%S}")
                staff_in_payload = t.payload.get("staff") or t.payload.get("staff_username")
                if staff_in_payload:
                    w(f"    payload.staff     : {staff_in_payload!r}  "
                      f"(→ nếu có, assign bị skip vì staff_id đã set)")
        else:
            w("  trace              : (không có) → KHÔNG vào qua webhook landing/lark; "
              "đường KiotViet/tạo tay/import KHÔNG chạy bộ chia.")

        # 2) classify_sheet — đơn có thuộc sheet nào không?
        sheet = classify_sheet(
            order_source=order.order_source,
            name=order.name,
            phone=order.phone,
            raw_address=order.raw_address,
            address=order.address,
            has_address=order.has_address,
        )
        w(f"  classify_sheet     : {sheet!r} "
          f"{'(None → skip: không thuộc sheet nào)' if sheet is None else ''}")

        # 3) Ca + ngày cấu hình suy từ created_at (xấp xỉ thời điểm task chạy).
        #    LƯU Ý: bộ chia thực tế dùng giờ SERVER lúc Celery xử lý, không phải
        #    created_at. Nếu queue trễ, giá trị thực có thể khác.
        shift, config_date = ShiftService.shift_and_date_for(created_local)
        w(f"  shift@created       : {shift!r}")
        w(f"  config_date suy ra  : {config_date}  "
          f"{'(= NGÀY HÔM SAU do ca cuộn qua đêm, đoạn trước nửa đêm)' if config_date != created_local.date() else ''}")
        if shift is None:
            w(self.style.WARNING("  → LÝ DO: ngoài giờ làm việc lúc tạo → không chia."))

        # 4) Config (share) cho (config_date, shift, sheet) có tồn tại không,
        #    và được tạo TRƯỚC hay SAU khi đơn về?
        if sheet and shift:
            shares = list(
                LeadAssignmentShare.objects.filter(
                    date=config_date, shift=shift, sheet=sheet, weight__gt=0
                ).select_related("user").order_by("id")
            )
            if not shares:
                w(self.style.ERROR(
                    f"  → LÝ DO: KHÔNG có cấu hình cho ({config_date}, {shift}, {sheet}) "
                    f"→ pick_staff_for trả None → skip."
                ))
            else:
                w(f"  shares ({config_date}, {shift}, {sheet}):")
                any_after = False
                for s in shares:
                    created_flag = ""
                    if s.created_at > order.created_at:
                        created_flag = "  ⚠️ TẠO SAU khi đơn về"
                        any_after = True
                    w(f"    - {s.user.username}: weight={s.weight} "
                      f"assigned_count={s.assigned_count} "
                      f"created={timezone.localtime(s.created_at):%Y-%m-%d %H:%M:%S}{created_flag}")
                if any_after:
                    w(self.style.ERROR(
                        "  → NGHI VẤN: cấu hình được tạo SAU thời điểm đơn về → lúc chia "
                        "chưa có share → skip (bẫy ca đêm: cần cấu hình ngày mai TRƯỚC khi ca tối bắt đầu)."
                    ))
                elif order.staff_id is None:
                    w(self.style.WARNING(
                        "  → Có cấu hình hợp lệ mà đơn vẫn trống: kiểm tra log server "
                        "'Lead auto-assigned'/'Lead skip' theo order_id để biết chính xác."
                    ))
