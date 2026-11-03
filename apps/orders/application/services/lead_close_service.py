"""Đóng lead đã chuyển đổi thành hóa đơn KiotViet (điều tra phồng số 2026-07-20).

Khi sale chốt khách bằng cách tạo hóa đơn TRỰC TIẾP trên KiotViet (không đẩy từ
CRM), lead gốc (invoice_status IS NULL) không bao giờ được cập nhật — cùng một
giao dịch tồn tại 2 bản ghi. Service này quét các lead còn mở mà cùng SĐT đã có
đơn hóa đơn (không hủy) trong cửa sổ [lead − 1 ngày, lead + days_window], đóng
lead (status=6) và ghi journey event ``case_closed`` để CRM còn dấu vết.

Lead đã đóng không xuất hiện ở bất kỳ bucket nào của Báo cáo tổng (xem
``summary_overview``).

Khớp theo SĐT + thời gian là CHƯA ĐỦ (sự cố 2026-09-08, đơn 560919): khách mua
hai món khác nhau trong cùng cửa sổ thì đơn thứ hai bị đóng oan — 105/268 lead
đã đóng có SKU khác hẳn hóa đơn được khớp. Từ 2026-09-08 lead chỉ được đóng khi
**tập SKU của lead nằm trọn trong tập SKU của hóa đơn** (hóa đơn gộp nhiều món
vẫn khớp; lead không có SKU thì không đủ căn cứ → giữ lại cho sale chăm tiếp).
``reopen_mismatched`` mở lại các lead đã bị đóng oan theo luật cũ.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.orders.models import Order, OrderJourneyEvent, OrderStatus
from apps.orders.order_status_constants import CANCELLED_INVOICE_STATUS
from apps.orders.sheet_rules import CASE_CLOSED_EVENT, CASE_REOPENED_EVENT

logger = logging.getLogger(__name__)

# Hóa đơn có trước lead tối đa ngần này thì vẫn coi là cùng giao dịch (lead ghi
# nhận muộn hơn hóa đơn một chút — webhook/nhập tay lệch giờ).
INVOICE_BEFORE_LEAD_GRACE_DAYS = 1
DEFAULT_DAYS_WINDOW = 7
DEFAULT_LOOKBACK_DAYS = 30

# Đơn hóa đơn bị hủy không tính là "đã chuyển đổi" — giữ lead cho sale chăm tiếp.
# Hai trục khác nhau, loại cả hai (audit 2026-09-23 T3): ``Order.status`` = trạng
# thái GIAO HÀNG (6 = "Đã hủy" — tên cũ `_CANCELLED_INVOICE_STATUSES = (6,)` gây
# hiểu nhầm là mã hóa đơn) và ``Order.invoice_status`` = trạng thái HÓA ĐƠN KiotViet
# (2 = đã hủy).
_CANCELLED_ORDER_STATUSES = (OrderStatus.CANCELLED,)

CONVERTED_INVOICE_REASON = "converted_invoice"
SKU_MISMATCH_REASON = "sku_mismatch"


def normalize_sku_set(raw: str | None) -> frozenset[str]:
    """``"BNC02, msc06"`` → ``{"BNC02", "MSC06"}``; rỗng/None → tập rỗng."""
    return frozenset(part.strip().upper() for part in (raw or "").split(",") if part.strip())


def is_same_transaction(lead_sku: str | None, invoice_sku: str | None) -> bool:
    """Lead có phải chính giao dịch đã lên hóa đơn không (theo SKU).

    Đúng khi lead có SKU và mọi SKU của lead đều nằm trong hóa đơn — bao gồm cả
    trường hợp hóa đơn gộp thêm món khác (``MSC06`` ⊆ ``MSC06, DC12``).
    """
    lead_skus = normalize_sku_set(lead_sku)
    if not lead_skus:
        return False
    return lead_skus <= normalize_sku_set(invoice_sku)


class LeadCloseService:
    """Quét + đóng lead trùng với hóa đơn KiotViet đã tồn tại."""

    @staticmethod
    def close_converted(
        *,
        days_window: int = DEFAULT_DAYS_WINDOW,
        lookback_days: int | None = DEFAULT_LOOKBACK_DAYS,
        dry_run: bool = False,
    ) -> dict:
        """Đóng các lead đã chuyển đổi; trả summary {scanned, closed, dry_run}.

        ``lookback_days``: chỉ xét lead tạo trong N ngày gần nhất (cron dùng
        mặc định 30); truyền 0/None để quét toàn bộ lịch sử (chạy hồi tố).
        """
        leads_qs = Order.objects.filter(
            deleted_at__isnull=True,
            invoice_status__isnull=True,
        ).exclude(status=OrderStatus.CANCELLED).exclude(phone_normalized="").exclude(sku="")
        if lookback_days:
            leads_qs = leads_qs.filter(
                created_at__gte=timezone.now() - timedelta(days=lookback_days)
            )
        leads = list(leads_qs.values("id", "phone_normalized", "created_at", "sku"))
        if not leads:
            return {"scanned": 0, "closed": 0, "dry_run": dry_run}

        invoices_by_phone: dict[str, list[dict]] = {}
        invoice_rows = (
            Order.objects.filter(
                deleted_at__isnull=True,
                invoice_status__isnull=False,
                phone_normalized__in={lead["phone_normalized"] for lead in leads},
            )
            .exclude(status__in=_CANCELLED_ORDER_STATUSES)
            .exclude(invoice_status=CANCELLED_INVOICE_STATUS)
            .values("id", "phone_normalized", "created_at", "invoice_code", "sku")
        )
        for row in invoice_rows:
            invoices_by_phone.setdefault(row["phone_normalized"], []).append(row)

        closed = 0
        for lead in leads:
            match = LeadCloseService._find_match(
                lead, invoices_by_phone.get(lead["phone_normalized"], []), days_window
            )
            if match is None:
                continue
            closed += 1
            if dry_run:
                continue
            LeadCloseService._close_lead(lead["id"], match)

        summary = {"scanned": len(leads), "closed": closed, "dry_run": dry_run}
        logger.info("close_converted_leads: %s", summary)
        return summary

    @staticmethod
    def reopen_mismatched(*, dry_run: bool = False) -> dict:
        """Mở lại lead bị job đóng oan theo luật cũ (chỉ SĐT + thời gian).

        Quét mọi journey event ``case_closed`` do job sinh ra; lead nào mà SKU
        KHÔNG khớp hóa đơn được ghi trong payload (hoặc hóa đơn đó không còn)
        thì trả về ``status=0`` và ghi event ``case_reopened``. Idempotent: lead
        đã mở lại không còn ``status=6`` nên lần chạy sau bỏ qua.
        """
        events = list(
            OrderJourneyEvent.objects.filter(
                event_type=CASE_CLOSED_EVENT,
                payload_json__reason=CONVERTED_INVOICE_REASON,
            ).values("id", "order_id", "payload_json")
        )
        if not events:
            return {"scanned": 0, "reopened": 0, "dry_run": dry_run}

        lead_skus = dict(
            Order.objects.filter(
                id__in={event["order_id"] for event in events},
                status=6,
                invoice_status__isnull=True,
                deleted_at__isnull=True,
            ).values_list("id", "sku")
        )
        invoice_ids = {
            event["payload_json"].get("matched_order_id") for event in events
        } - {None}
        invoice_skus = dict(
            Order.objects.filter(id__in=invoice_ids).values_list("id", "sku")
        )

        reopened_ids: list[int] = []
        for event in events:
            lead_id = event["order_id"]
            if lead_id not in lead_skus or lead_id in reopened_ids:
                continue  # đã mở lại / đã lên hóa đơn / đã xóa → không đụng vào
            invoice_id = event["payload_json"].get("matched_order_id")
            if is_same_transaction(lead_skus[lead_id], invoice_skus.get(invoice_id)):
                continue  # đóng ĐÚNG theo luật mới → giữ nguyên
            reopened_ids.append(lead_id)
            if dry_run:
                continue
            LeadCloseService._reopen_lead(lead_id, event)

        summary = {
            "scanned": len(lead_skus),
            "reopened": len(reopened_ids),
            "dry_run": dry_run,
        }
        logger.info("reopen_mismatched_leads: %s ids=%s", summary, reopened_ids)
        return {**summary, "reopened_ids": reopened_ids}

    @staticmethod
    def _find_match(lead: dict, invoices: list[dict], days_window: int) -> dict | None:
        window_start = lead["created_at"] - timedelta(days=INVOICE_BEFORE_LEAD_GRACE_DAYS)
        window_end = lead["created_at"] + timedelta(days=days_window)
        for invoice in invoices:
            if not window_start <= invoice["created_at"] <= window_end:
                continue
            if not is_same_transaction(lead["sku"], invoice["sku"]):
                continue  # khách mua món khác → đơn thật, KHÔNG phải ghi đôi
            return invoice
        return None

    @staticmethod
    def _close_lead(lead_id: int, invoice: dict) -> None:
        with transaction.atomic():
            updated = Order.objects.filter(
                pk=lead_id, invoice_status__isnull=True
            ).exclude(status=6).update(status=6, updated_at=timezone.now())
            if not updated:  # đã bị đóng/cập nhật song song → bỏ qua
                return
            OrderJourneyEvent.objects.create(
                order_id=lead_id,
                event_type=CASE_CLOSED_EVENT,
                payload_json={
                    "reason": CONVERTED_INVOICE_REASON,
                    "matched_order_id": invoice["id"],
                    "matched_invoice_code": invoice["invoice_code"] or "",
                    "auto": True,
                },
            )

    @staticmethod
    def _reopen_lead(lead_id: int, closed_event: dict) -> None:
        with transaction.atomic():
            updated = Order.objects.filter(
                pk=lead_id, status=6, invoice_status__isnull=True
            ).update(status=OrderStatus.NEW, updated_at=timezone.now())
            if not updated:  # đã đổi trạng thái song song → bỏ qua
                return
            OrderJourneyEvent.objects.create(
                order_id=lead_id,
                event_type=CASE_REOPENED_EVENT,
                payload_json={
                    "reason": SKU_MISMATCH_REASON,
                    "matched_order_id": closed_event["payload_json"].get("matched_order_id"),
                    "closed_event_id": closed_event["id"],
                    "auto": True,
                },
            )
