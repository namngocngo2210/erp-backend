import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.orders.application.services.order_service_helpers import OrderServiceHelpers
from apps.orders.application.services.shift_service import ShiftService
from apps.orders.models import LeadAssignmentShare, Order, ShiftWindow
from apps.orders.sheet_rules import (
    ASSIGNMENT_SHEETS,
    STAFF_ASSIGNED_EVENT,
    classify_sheet,
    sheet_q,
)

logger = logging.getLogger(__name__)

# Đơn "chưa có trạng thái sale": assessment null hoặc sales_status_code rỗng.
_NO_SALES_STATUS_Q = (
    Q(assessment__isnull=True)
    | Q(assessment__sales_status_code__isnull=True)
    | Q(assessment__sales_status_code="")
)

# Trần số đơn sweep chia lại mỗi tick: backlog đột biến (vd config nhập muộn cả
# ngày) không được kéo dài 1 tick vô hạn — mỗi đơn là 1 transaction + lock riêng.
# Cũ nhất trước; phần dư tick sau vớt (audit 2026-07-28 DB-14).
SWEEP_MAX_ORDERS_PER_TICK = 500


class LeadAssignmentService:
    """Chia đơn tự động theo (ngày, ca, sheet) + công cụ xem tải & chuyển đơn."""

    @staticmethod
    def pick_staff_for(date, shift, sheet):
        """Chọn 1 nhân viên cho (date, shift, sheet) theo largest-remainder, tăng bộ đếm.

        Trả None nếu chưa cấu hình. ``select_for_update`` serialize ingest đồng thời.
        """
        with transaction.atomic():
            # KHÔNG dùng skip_locked dù sweep nền và landing-ingest cùng tranh các
            # share row này: largest-remainder phải NHÌN ĐỦ mọi share của (ngày, ca,
            # sheet) để tính thâm hụt — bỏ qua row đang bị khoá sẽ tính sai
            # total_weight/assigned (chia lệch người) hoặc trả None dù có config
            # (đơn bị bỏ đói). Thay vào đó rút ngắn critical section: caller sweep
            # hoist lookup ca/ngày ra ngoài vòng lặp nên lock chỉ ôm đúng đoạn
            # pick + save (audit 2026-07-28 DB-14).
            shares = list(
                LeadAssignmentShare.objects.select_for_update()
                .filter(date=date, shift=shift, sheet=sheet, weight__gt=0)
                .order_by("id")
            )
            if not shares:
                return None
            total_weight = sum(s.weight for s in shares)
            total_assigned = sum(s.assigned_count for s in shares) + 1

            # tie-break: thâm hụt lớn hơn trước, rồi weight cao hơn, rồi id nhỏ hơn (tất định)
            def deficit(s):
                target = s.weight / total_weight * total_assigned
                return (target - s.assigned_count, s.weight, -s.id)

            best = max(shares, key=deficit)
            best.assigned_count += 1
            best.save(update_fields=["assigned_count", "updated_at"])
            return best.user

    @staticmethod
    def assign_if_unassigned(order, shift_context: tuple | None = None, actor=None) -> None:
        """Gán staff cho order nếu đang trống. No-op nếu đã có staff hoặc không thuộc sheet.

        ``shift_context``: (shift, config_date) tính sẵn — sweep truyền vào để khỏi
        lookup ca/ngày lại cho TỪNG đơn (bất biến trong 1 tick) và để lock Order
        không phải ôm thêm đoạn lookup đó (audit 2026-07-28 DB-14). Ingest đơn lẻ
        bỏ trống → tự tính như cũ.

        ``actor``: người bấm "Chia đơn tự động" trên form tạo đơn. Ingest và sweep
        chạy nền nên bỏ trống — chỉ đường form mới có người thao tác thật, và đó
        cũng là dấu vết DUY NHẤT truy ngược được ai đẩy đơn sang tay người khác
        qua cờ này (cờ mở cho cả người thiếu quyền `sale-assignment:edit`).
        """
        if order.staff_id is not None:
            return
        sheet = classify_sheet(
            order_source=order.order_source,
            name=order.name,
            phone=order.phone,
            raw_address=order.raw_address,
            address=order.address,
            has_address=order.has_address,
        )
        if sheet is None:
            return
        # Gói pick (tăng assigned_count) + save staff trong cùng transaction để
        # counter và staff commit nguyên tử (tránh drift nếu save lỗi). Khi caller
        # đã ở trong atomic (luồng landing), đây trở thành savepoint lồng.
        # config_date ≠ hôm nay khi ca cuộn qua đêm: đoạn 21:30→23:59 thuộc ca
        # của ngày hôm sau nên phải ăn config ngày hôm sau.
        shift, config_date = shift_context or ShiftService.current_shift_and_date()
        if shift is None:
            # Ngoài giờ làm việc: để trống staff, không chia tự động.
            logger.info("Lead skip auto-assign (ngoài giờ làm việc): order_id=%s", order.id)
            return
        with transaction.atomic():
            user = LeadAssignmentService.pick_staff_for(config_date, shift, sheet)
            if user is None:
                return
            previous_staff_id = order.staff_id
            order.staff = user
            order.save(update_fields=["staff", "updated_at"])
            # actor None khi bộ chia chạy nền (ingest/sweep); có giá trị khi người
            # dùng tự bật cờ chia trên form tạo đơn.
            OrderServiceHelpers.log_staff_change(
                order=order,
                from_staff_id=previous_staff_id,
                to_staff_id=user.id,
                actor=actor,
                event_type=STAFF_ASSIGNED_EVENT,
            )
            logger.info(
                "Lead auto-assigned: order_id=%s shift=%s sheet=%s staff_id=%s",
                order.id, shift, sheet, user.id,
            )

    @staticmethod
    def sweep_unassigned() -> dict:
        """Chia lại các đơn thuộc sheet còn trống staff trong cửa sổ lookback.

        Lưới đỡ cho mọi đường "skip im lặng" của bộ chia lúc ingest: config
        nhập muộn, đơn tạo qua import/KiotViet (không qua webhook nên không
        chạy assign), đơn về ngoài giờ hoặc ca chưa cấu hình. Đơn sót được
        chia theo (ca, ngày-config) tại thời điểm sweep chạy.
        """
        # Hoist ca/ngày-config ra ngoài vòng lặp: bất biến trong 1 tick, khỏi gọi
        # lại cho từng đơn (audit 2026-07-28 DB-14).
        shift, config_date = ShiftService.current_shift_and_date()
        if shift is None:
            return {"skipped": "outside_working_hours"}
        lookback_hours = getattr(settings, "LEAD_ASSIGNMENT_SWEEP_LOOKBACK_HOURS", 48)
        since = timezone.now() - timedelta(hours=lookback_hours)
        any_sheet_q = sheet_q("sheet1") | sheet_q("sheet2") | sheet_q("sheet3")
        candidate_ids = list(
            Order.objects.filter(
                staff_id__isnull=True,
                deleted_at__isnull=True,
                created_at__gte=since,
            )
            .filter(any_sheet_q)
            .order_by("created_at")
            # Trần mỗi tick, cũ nhất trước — xem SWEEP_MAX_ORDERS_PER_TICK.
            .values_list("id", flat=True)[:SWEEP_MAX_ORDERS_PER_TICK]
        )
        scanned = assigned = 0
        for order_id in candidate_ids:
            # Khoá từng đơn để không đè lên gán tay/ingest đang chạy song song;
            # skip_locked: đơn đang bị luồng khác giữ thì bỏ qua, tick sau vớt.
            with transaction.atomic():
                order = (
                    Order.objects.select_for_update(skip_locked=True)
                    .filter(pk=order_id, staff_id__isnull=True, deleted_at__isnull=True)
                    .first()
                )
                if order is None:
                    continue
                scanned += 1
                LeadAssignmentService.assign_if_unassigned(
                    order, shift_context=(shift, config_date)
                )
                if order.staff_id is not None:
                    assigned += 1
        if assigned:
            logger.info(
                "Lead sweep: assigned=%s/%s (lookback=%sh)",
                assigned, scanned, lookback_hours,
            )
        return {"scanned": scanned, "assigned": assigned}

    @staticmethod
    def _pending_base(sheet, date=None):
        """Queryset đơn thuộc sheet, chưa có trạng thái sale, chưa xoá.

        ``date`` (nếu có) giới hạn theo ngày tạo đơn (``created_at::date``).
        """
        qs = (
            Order.objects.filter(sheet_q(sheet))
            .filter(_NO_SALES_STATUS_Q)
            .filter(deleted_at__isnull=True)
        )
        if date is not None:
            qs = qs.filter(created_at__date=date)
        return qs

    @staticmethod
    def workload(sheet, date):
        """Số đơn đang chờ (no sales_status) theo từng NV trong sheet, lọc theo ngày tạo."""
        rows = (
            LeadAssignmentService._pending_base(sheet, date)
            .filter(staff__isnull=False)
            .values("staff_id")
            .annotate(pending_count=Count("id"))
        )
        return {r["staff_id"]: r["pending_count"] for r in rows}

    @staticmethod
    def transfer(sheet, from_user_id, to_user_id, count, date, actor=None) -> int:
        """Chuyển N đơn cũ nhất (FIFO) của from_user trong sheet (theo ngày) sang to_user.

        Chỉ đơn chưa có trạng thái sale, tạo trong ``date``. KHÔNG sửa assigned_count.

        ``actor``: người bấm chuyển (None nếu chạy nền) — ghi vào journey để còn truy
        ngược. Đường này dùng ``.update()`` hàng loạt nên phải ghi event thủ công;
        không có signal nào bắt hộ.
        """
        if from_user_id == to_user_id or count < 1:
            return 0
        with transaction.atomic():
            # ``_pending_base`` LEFT JOIN sang assessment ⇒ Postgres cấm FOR UPDATE
            # trên nhánh nullable của outer join. Vật chất hoá id (không lock) trước,
            # rồi re-fetch + lock chính các Order đó để serialize chuyển đơn.
            candidate_ids = list(
                LeadAssignmentService._pending_base(sheet, date)
                .filter(staff_id=from_user_id)
                .order_by("created_at", "id")
                .values_list("id", flat=True)[:count]
            )
            if not candidate_ids:
                return 0
            ids = list(
                Order.objects.select_for_update()
                .filter(id__in=candidate_ids, staff_id=from_user_id)
                .values_list("id", flat=True)
            )
            if not ids:
                return 0
            Order.objects.filter(id__in=ids).update(staff_id=to_user_id, updated_at=timezone.now())
            # MỘT INSERT cho cả lô thay vì re-fetch Order + INSERT từng đơn trong lúc
            # vẫn giữ lock các dòng `orders` (audit 2026-09-23 T1 §2.6) — chỉ cần id.
            OrderServiceHelpers.log_staff_change_bulk(
                order_ids=ids,
                from_staff_id=from_user_id,
                to_staff_id=to_user_id,
                actor=actor,
            )
            return len(ids)

    # ── Cấu hình ma trận chia đơn / cửa sổ ca (audit 2026-09-23 T3/P2-1) ──────────
    # Ba thao tác ghi dưới đây từng nằm thẳng trong lead_assignment_view kèm
    # ``transaction.atomic``; view giờ chỉ validate → gọi → serialize.

    @staticmethod
    def upsert_shares(date, shift, rows: list[dict]) -> None:
        """Ghi đè ma trận (NV × sheet) của MỘT (ngày, ca) theo payload đã validate.

        ``rows``: ``[{"user_id": int, "weights": {sheet: weight}}]``. Reconcile trong
        phạm vi (ngày, ca): NV không còn trong payload bị xoá; ô weight ≤ 0 bị xoá;
        ô weight > 0 upsert. Cả lô trong một transaction để UI không thấy nửa ma trận.
        """
        keep_user_ids = {r["user_id"] for r in rows}
        with transaction.atomic():
            LeadAssignmentShare.objects.filter(date=date, shift=shift).exclude(
                user_id__in=keep_user_ids
            ).delete()
            for r in rows:
                uid = r["user_id"]
                for sheet in ASSIGNMENT_SHEETS:
                    weight = int(r["weights"].get(sheet, 0) or 0)
                    if weight > 0:
                        LeadAssignmentShare.objects.update_or_create(
                            date=date, shift=shift, sheet=sheet, user_id=uid,
                            defaults={"weight": weight},
                        )
                    else:
                        LeadAssignmentShare.objects.filter(
                            date=date, shift=shift, sheet=sheet, user_id=uid
                        ).delete()

    @staticmethod
    def copy_shares(*, source_date, target_date, allowed_user_ids: set[int]) -> int:
        """Copy nguyên ma trận (cả 3 ca × 3 sheet, weight > 0) từ ``source_date`` sang
        ``target_date``; ``assigned_count`` reset 0. NV ngoài ``allowed_user_ids``
        (đã rời phòng Sale / bị khoá) bị bỏ qua. Trả về số ô đã copy.

        Caller đã kiểm tra ngày đích trống và ngày nguồn tồn tại trước khi gọi.
        """
        copied = 0
        with transaction.atomic():
            for row in LeadAssignmentShare.objects.filter(date=source_date, weight__gt=0):
                if row.user_id not in allowed_user_ids:
                    continue
                LeadAssignmentShare.objects.create(
                    date=target_date, shift=row.shift, sheet=row.sheet,
                    user_id=row.user_id, weight=row.weight,
                )
                copied += 1
        return copied

    @staticmethod
    def save_shift_windows(windows: list[dict]) -> None:
        """Ghi cửa sổ giờ của các ca rồi xoá cache ca hiện tại.

        ``windows``: ``[{"shift": str, "start_time": time|None, "end_time": time|None}]``.
        Ca có cả hai mốc ``None`` = ca tắt → xoá dòng để ``shift_for_time`` không xét tới.
        """
        with transaction.atomic():
            for w in windows:
                start, end = w.get("start_time"), w.get("end_time")
                if start is None and end is None:
                    ShiftWindow.objects.filter(shift=w["shift"]).delete()
                else:
                    ShiftWindow.objects.update_or_create(
                        shift=w["shift"],
                        defaults={"start_time": start, "end_time": end},
                    )
        ShiftService.invalidate_cache()
