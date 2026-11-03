"""Tạo cơ hội resale bên Foxia từ đơn nội bộ đã giao thành công.

Thiết kế: docs/foxia-resale-delivered.md. Tóm tắt luật:

- **Đơn nào (chốt lại 2026-10-06).** Đơn nội bộ **TẠO từ ngày
  `FOXIA_RESALE_PUSH_SINCE`** (giờ VN) và **HIỆN đang** giao thành công, hoá đơn
  không huỷ, chưa xoá mềm, kênh có ô cột `resale` trong bảng liên kết Foxia (kênh
  không có ô = ngoài phạm vi, không ghi dòng nào). Trước đây lọc theo NGÀY CHUYỂN
  sang giao thành công — đơn tạo trước SINCE nhưng giao sau SINCE lọt vào, và đơn
  đổi trạng thái qua đường không phát journey thì bị sót.
- **Mốc giao** (giờ trong payload, ngày chống trùng SĐT): lần gần nhất đơn vào
  trạng thái 3 theo journey; đơn không có journey → `updated_at` của đơn.
- Không giới hạn lùi số ngày: đơn đã có dòng nhật ký bị loại ở SQL nên chạy lại vô
  hại. Lọc theo `created_at` + kênh phủ bởi index `order_channel_created_idx`.
- **Một đơn tối đa một cơ hội.** `FoxiaResalePush.order` unique, dòng ghi
  (pending) TRƯỚC khi POST. POST không idempotent nên KHÔNG bao giờ đẩy lại —
  kể cả dòng failed hay kẹt pending.
- **Một SĐT tối đa một cơ hội mỗi ngày giao.** Khách nhận 2 đơn cùng ngày thì
  sale resale chỉ cần gọi một lần.
- **Chia sale resale trước khi đẩy.** Bộ chia của trang Chia đơn
  (`LeadAssignmentService.pick_staff_for`, cột `resale` của ma trận ngày × ca)
  chọn người, payload mang `staff_id` = `foxia_id` của người đó. Ngoài giờ ca
  hoặc ca chưa cấu hình cột resale → KHÔNG đẩy, cơ hội chờ lượt có ca (POST
  không đẩy lại được, đẩy thiếu người nhận là mất luôn).
- **Không raise lỗi đối tác**, giống `FoxiaOrderPushService`: bằng chứng nằm ở
  dòng nhật ký.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.common.kiotviet_constants import CANCELLED_INVOICE_STATUS
from apps.foxia.application.services.order_push_service import (
    CHANCE_RESALE,
    PushResult,
    build_address,
    push_customer,
)
from apps.foxia.domain.datetime_utils import to_foxia_utc
from apps.orders.sheet_rules import RESALE_SHEET
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)

# Khớp `apps.orders.subscribers.STATUS_CHANGED_EVENT` / `OrderStatus.DELIVERED_SUCCESS`.
# Literal thay vì import để service không kéo subscriber của orders vào lúc nạp module.
STATUS_CHANGED_EVENT = "order_status_changed"
DELIVERED_CODE = "3"
DELIVERED_STATUS = 3

# Đẩy TUẦN TỰ (2026-10-07): 4 luồng song song làm Foxia nghẽn — lượt 00:11 ngày
# 07/10 có 8/40 request ReadTimeout theo đúng hai đợt 4 cái cùng lúc, mà timeout
# là vùng mù (Foxia có thể đã tạo cơ hội) nên không được đẩy lại tự động.
PARALLELISM = 1
# Trần mỗi lượt, tính theo đẩy tuần tự: 40 × timeout 10s = 400s < soft_time_limit
# 540s — lượt chạm trần vẫn xong trước khi bị giết (bị giết giữa chừng = dòng kẹt
# pending, không bao giờ đẩy). Job chạy mỗi phút nên thông lượng vẫn ~40 cơ hội/phút.
MAX_BATCH = 40


def parse_since(raw: str) -> date | None:
    try:
        return date.fromisoformat(str(raw or "").strip())
    except ValueError:
        return None


def format_vnd(amount) -> str:
    """1599000 → "1.599.000đ" (dấu chấm ngăn nghìn như sale Foxia đọc quen)."""
    return f"{int(round(amount or 0)):,}".replace(",", ".") + "đ"


def build_message(order, delivered_at: datetime) -> str:
    """Ô "Khách hàng ghi chú" bên Foxia: SKU + số lượng các dòng hàng và giá SAU GIẢM
    của đơn (chốt 2026-10-07), ví dụ "DT11 x1 - Giá sau giảm: 2.450.000đ".

    Giá lấy `Order.total_after_discount`, KHÔNG cộng `line_total`: dòng hàng không
    mang phần giảm cấp đơn. `delivered_at` giữ trong chữ ký cho đồng bộ với
    `build_payload` (giờ giao đã đi ở `created_at`).
    """
    skus = ", ".join(
        f"{str(item.sku or item.product_name or '').strip()} x{item.quantity}" for item in order.items.all()
    )
    price = f"Giá sau giảm: {format_vnd(order.total_after_discount)}"
    return f"{skus} - {price}" if skus else price


def foxia_staff_id(user) -> int | None:
    """`User.foxia_id` dạng số, hoặc None. Cùng luật `build_salesperson_id` của đẩy
    đơn landing: gửi sai id còn tệ hơn không gửi (cơ hội rơi vào tay người khác)."""
    raw = str(getattr(user, "foxia_id", "") or "").strip()
    return int(raw) if raw.isdigit() else None


def build_payload(order, delivered_at: datetime, staff_foxia_id: int | None = None) -> dict:
    """Cùng hợp đồng với đẩy đơn landing, `chance_id` = resale.

    `staff_id` là sale resale do bộ chia chọn (KHÔNG phải sale chốt đơn gốc), dạng
    id nhân viên bên Foxia; bỏ hẳn trường khi không có.
    """
    name = str(order.name or "").strip() or str(getattr(order.customer, "full_name", "") or "").strip()
    payload = {
        "name": name,
        "phone": str(order.phone or "").strip(),
        "address": build_address(order),
        "chance_id": CHANCE_RESALE,
        "created_at": to_foxia_utc(delivered_at),
        "message": build_message(order, delivered_at),
    }
    if staff_foxia_id is not None:
        payload["staff_id"] = staff_foxia_id
    return payload


def _current_shift_and_date():
    from apps.orders.application.services.shift_service import ShiftService

    return ShiftService.current_shift_and_date()


def _pick_resale_staff(config_date, shift):
    from apps.orders.application.services.lead_assignment_service import LeadAssignmentService

    return LeadAssignmentService.pick_staff_for(config_date, shift, RESALE_SHEET)


@dataclass
class ResaleRunResult:
    status: str = "ok"
    candidates: int = 0
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    no_opportunity: int = 0
    order_ids: list[int] = field(default_factory=list)

    def summary(self) -> str:
        if self.status != "ok":
            return self.status
        return f"ok:{self.ok}/no_opp:{self.no_opportunity}/failed:{self.failed}/skipped:{self.skipped}"


@dataclass(frozen=True)
class _Candidate:
    order: object
    delivered_at: datetime
    source_id: int


class FoxiaResalePushService:
    def __init__(
        self,
        push: Callable[[int, dict], PushResult] = push_customer,
        now: Callable[[], datetime] = timezone.now,
        shift_context: Callable[[], tuple] = _current_shift_and_date,
    ):
        self._push = push
        self._now = now
        self._shift_context = shift_context

    # ── điểm vào ─────────────────────────────────────────────────────────────

    def run(self, dry_run: bool = False) -> ResaleRunResult:
        disabled = self._disabled_reason()
        if disabled and not dry_run:
            return ResaleRunResult(status=disabled)

        since = parse_since(sysconfig.get("FOXIA_RESALE_PUSH_SINCE"))
        if since is None:
            logger.warning("Foxia resale: FOXIA_RESALE_PUSH_SINCE không phải ngày YYYY-MM-DD — bỏ qua lượt")
            return ResaleRunResult(status="bad_since")

        candidates = self._collect(self._window_start(since))
        result = ResaleRunResult(candidates=len(candidates), order_ids=[c.order.id for c in candidates])
        if dry_run or not candidates:
            return result

        # Ca/ngày-config tính MỘT lần cho cả lượt (cùng cách sweep của bộ chia sale).
        shift, config_date = self._shift_context()
        if shift is None:
            result.status = "outside_shift"
            return result
        if not self._has_resale_config(config_date, shift):
            logger.info("Foxia resale: ca %s ngày %s chưa cấu hình cột resale — chờ lượt sau", shift, config_date)
            result.status = "no_resale_config"
            return result

        to_push = self._claim(candidates, result, shift, config_date)
        self._push_all(to_push, result)
        logger.info(
            "Foxia resale: %s ung vien, ok=%s no_opp=%s failed=%s skipped=%s",
            result.candidates, result.ok, result.no_opportunity, result.failed, result.skipped,
        )
        return result

    # ── các bước ─────────────────────────────────────────────────────────────

    @staticmethod
    def _disabled_reason() -> str:
        # Staging chạy snapshot prod: lớp chặn thứ hai sau staging_forbidden của registry.
        if getattr(settings, "IS_STAGING", False):
            return "disabled"
        if not sysconfig.get("FOXIA_PUSH_ENABLED") or not sysconfig.get("FOXIA_RESALE_PUSH_ENABLED"):
            return "disabled"
        return ""

    @staticmethod
    def _has_resale_config(config_date, shift) -> bool:
        from apps.orders.models import LeadAssignmentShare

        return LeadAssignmentShare.objects.filter(
            date=config_date, shift=shift, sheet=RESALE_SHEET, weight__gt=0
        ).exists()

    @staticmethod
    def _window_start(since: date) -> datetime:
        """00:00 (giờ VN) của ngày SINCE — mốc sàn duy nhất của cửa sổ quét."""
        return timezone.make_aware(datetime.combine(since, time.min))

    def _collect(self, window_start: datetime) -> list[_Candidate]:
        """Ứng viên cũ nhất trước, tối đa MAX_BATCH. 3 câu SQL, không N+1."""
        found = self._candidate_orders(window_start)
        if found is None:
            return []
        orders, delivered_at, source_by_channel = found
        candidates = [
            _Candidate(order=order, delivered_at=delivered_at[order.id], source_id=source_by_channel[order.channel_id])
            for order in orders.select_related("customer").prefetch_related("items")
        ]
        candidates.sort(key=lambda c: (c.delivered_at, c.order.id))
        return candidates[:MAX_BATCH]

    def _candidate_orders(self, window_start: datetime):
        """(queryset đơn ứng viên, {order_id: giờ giao}, {channel_id: id nguồn}) hoặc
        None khi chắc chắn không có ứng viên — dùng chung cho lượt đẩy và thống kê.

        `window_start` là mốc sàn NGÀY TẠO đơn.
        """
        from apps.foxia.models import FoxiaLinkCell, FoxiaLinkKind
        from apps.orders.models import Order, OrderJourneyEvent

        source_by_channel = dict(
            FoxiaLinkCell.objects.filter(kind=FoxiaLinkKind.RESALE).values_list(
                "link_channel__channel_id", "foxia_source_id"
            )
        )
        if not source_by_channel:
            return None

        orders = Order.objects.filter(
            created_at__gte=window_start,
            created_at__lt=self._now(),
            channel_id__in=list(source_by_channel),
            status=DELIVERED_STATUS,
            deleted_at__isnull=True,
            foxia_resale_push__isnull=True,
        ).exclude(invoice_status=CANCELLED_INVOICE_STATUS)
        updated_at = dict(orders.values_list("id", "updated_at"))
        if not updated_at:
            return None

        # Lần GẦN NHẤT đơn vào trạng thái 3 — phủ bởi ord_journey_ord_type_cr_idx
        # (order, event_type, created_at). Không có journey → rơi về updated_at.
        delivered_at = updated_at | dict(
            OrderJourneyEvent.objects.filter(
                order_id__in=list(updated_at), event_type=STATUS_CHANGED_EVENT, to_code=DELIVERED_CODE
            )
            .values("order_id")
            .annotate(at=Max("created_at"))
            .values_list("order_id", "at")
        )
        # Chốt đúng tập đã đọc: đơn mới lọt vào giữa hai câu trên sẽ thiếu mốc giao.
        orders = orders.filter(id__in=list(updated_at))
        return orders, delivered_at, source_by_channel

    # ── thống kê cho trang Chia đơn ──────────────────────────────────────────

    def state(self) -> str:
        """Vì sao cơ hội đang chờ: disabled / bad_since / outside_shift /
        no_resale_config, hoặc "ready" (lượt kế tiếp sẽ chia và đẩy)."""
        disabled = self._disabled_reason()
        if disabled:
            return disabled
        if parse_since(sysconfig.get("FOXIA_RESALE_PUSH_SINCE")) is None:
            return "bad_since"
        shift, config_date = self._shift_context()
        if shift is None:
            return "outside_shift"
        if not self._has_resale_config(config_date, shift):
            return "no_resale_config"
        return "ready"

    def waiting_count(self) -> int:
        """Số đơn GTC đủ điều kiện CHƯA được ghi dòng (không chặn trần MAX_BATCH)."""
        since = parse_since(sysconfig.get("FOXIA_RESALE_PUSH_SINCE"))
        if since is None:
            return 0
        found = self._candidate_orders(self._window_start(since))
        return 0 if found is None else found[0].count()

    @staticmethod
    def day_summary(day: date) -> dict:
        """Dòng nhật ký GHI trong `day` (giờ VN): đếm theo trạng thái + theo sale resale.

        Hai câu GROUP BY, phủ bởi index `foxia_resale_created_idx`.
        """
        from apps.foxia.models import FoxiaResalePush, FoxiaResalePushStatus as St

        start = timezone.make_aware(datetime.combine(day, time.min))
        rows = FoxiaResalePush.objects.filter(created_at__gte=start, created_at__lt=start + timedelta(days=1))
        by_status = dict.fromkeys(St.values, 0)
        by_status.update(rows.values("status").annotate(n=Count("id")).values_list("status", "n"))
        staff = (
            rows.filter(staff__isnull=False)
            .values("staff_id", "staff__username", "staff__display_name")
            .annotate(
                total=Count("id"),
                ok=Count("id", filter=Q(status=St.OK)),
                failed=Count("id", filter=Q(status=St.FAILED)),
                pending=Count("id", filter=Q(status=St.PENDING)),
            )
            .order_by("-total", "staff__username")
        )
        return {
            "by_status": by_status,
            "by_staff": [
                {
                    "user_id": r["staff_id"],
                    "username": r["staff__username"],
                    "display_name": r["staff__display_name"] or r["staff__username"],
                    "total": r["total"],
                    "ok": r["ok"],
                    "failed": r["failed"],
                    "pending": r["pending"],
                }
                for r in staff
            ],
        }

    def _claim(self, candidates: list[_Candidate], result: ResaleRunResult, shift, config_date) -> list[tuple]:
        """Chia sale + ghi dòng cho ứng viên TRƯỚC khi gọi mạng; trả (dòng, nguồn, payload) cần POST.

        Pick (tăng `assigned_count`) và ghi dòng nằm chung một transaction: lỗi ghi
        thì bộ đếm cũng lùi, tỉ lệ chia không trôi. Pick trả None giữa lượt (cấu hình
        vừa bị xoá) → ứng viên còn lại KHÔNG ghi dòng, chờ lượt sau.
        """
        from apps.foxia.models import FoxiaResalePush, FoxiaResalePushStatus as St

        days = {timezone.localdate(c.delivered_at) for c in candidates}
        taken = set(
            FoxiaResalePush.objects.filter(
                delivered_on__in=days, status__in=(St.PENDING, St.OK)
            ).exclude(phone="").values_list("delivered_on", "phone")
        )

        rows, to_push = [], []
        # Unique(order) + single_flight của task: va chạm chỉ xảy ra khi có người chạy
        # tay song song — để IntegrityError nổi lên, lượt này bỏ, lượt sau làm tiếp.
        with transaction.atomic():
            for c in candidates:
                day = timezone.localdate(c.delivered_at)
                phone = str(c.order.phone_normalized or c.order.phone or "").strip()
                staff = None
                if not str(c.order.phone or "").strip():
                    status = St.SKIPPED_NO_PHONE
                elif (day, phone) in taken:
                    status = St.SKIPPED_DUPLICATE
                else:
                    staff = _pick_resale_staff(config_date, shift)
                    if staff is None:
                        logger.warning("Foxia resale: bộ chia trả None giữa lượt (ca %s ngày %s)", shift, config_date)
                        break
                    status = St.PENDING
                    taken.add((day, phone))

                payload = None
                if status == St.PENDING:
                    staff_foxia_id = foxia_staff_id(staff)
                    if staff_foxia_id is None:
                        # Trang Chia đơn chặn lưu người thiếu foxia_id; tới đây chỉ khi
                        # foxia_id bị xoá SAU khi cấu hình — vẫn đẩy, Foxia tự chia.
                        logger.warning("Foxia resale: staff=%s không có foxia_id số — đẩy không staff_id", staff.id)
                    payload = build_payload(c.order, c.delivered_at, staff_foxia_id)
                rows.append(FoxiaResalePush(
                    order=c.order,
                    delivered_on=day,
                    phone=phone,
                    status=status,
                    staff=staff,
                    foxia_source_id=c.source_id,
                    request_body=payload,
                ))
                if payload is not None:
                    to_push.append((rows[-1], c.source_id, payload))
                else:
                    result.skipped += 1
            FoxiaResalePush.objects.bulk_create(rows)
        return to_push

    def _push_all(self, to_push: list[tuple], result: ResaleRunResult) -> None:
        """POST song song; ghi kết quả từng dòng NGAY khi về (worker chết giữa lượt thì
        phần đã xong vẫn còn, phần dở nằm lại pending — xem docstring module)."""
        from apps.foxia.models import FoxiaResalePushStatus as St

        if not to_push:
            return
        with ThreadPoolExecutor(max_workers=min(PARALLELISM, len(to_push))) as pool:
            futures = {
                pool.submit(self._push_in_thread, source_id, payload): row
                for row, source_id, payload in to_push
            }
            for future in as_completed(futures):
                row = futures[future]
                push = future.result()
                if not push.ok:
                    row.status = St.FAILED
                elif push.cohoi_id is None:
                    row.status = St.NO_OPPORTUNITY
                else:
                    row.status = St.OK
                row.url = push.url
                row.http_status = push.http_status
                row.response_body = push.response_body
                row.error = push.error
                row.duration_ms = push.duration_ms
                row.cohoi_id = push.cohoi_id
                row.khachhang_id = push.khachhang_id
                row.save(update_fields=[
                    "status", "url", "http_status", "response_body", "error",
                    "duration_ms", "cohoi_id", "khachhang_id", "updated_at",
                ])
                if row.status == St.OK:
                    result.ok += 1
                elif row.status == St.NO_OPPORTUNITY:
                    result.no_opportunity += 1
                    logger.warning(
                        "Foxia resale: order=%s khachhang=%s — Foxia không tạo cơ hội (cohoi_id rỗng)",
                        row.order_id, push.khachhang_id,
                    )
                else:
                    result.failed += 1
                    logger.warning(
                        "Foxia resale failed: order=%s nguon=%s http=%s %sms%s",
                        row.order_id, row.foxia_source_id, push.http_status, push.duration_ms,
                        f" error={push.error}" if push.error else "",
                    )

    def _push_in_thread(self, source_id: int, payload: dict) -> PushResult:
        # `push_customer` đọc sysconfig — có thể mở connection DB trong luồng phụ.
        # Đóng lại ở đây, nếu không mỗi lượt rò connection của luồng phụ qua pgbouncer.
        try:
            return self._push(source_id, payload)
        finally:
            connection.close()  # thread-local: chỉ đóng connection của luồng phụ này
