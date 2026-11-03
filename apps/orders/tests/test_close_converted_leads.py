"""Job đóng lead đã chuyển đổi — LeadCloseService.close_converted.

Lead (invoice_status IS NULL) mà cùng SĐT đã có đơn hóa đơn KiotViet trong cửa
sổ [lead - 1 ngày, lead + days_window] là một giao dịch bị ghi ĐÔI (điều tra
2026-07-20: 96 đơn/149tr): sale chốt trên KiotViet trực tiếp nên lead không bao
giờ được cập nhật. Job đóng lead (status=6) + ghi journey event để CRM còn dấu
vết; báo cáo tổng loại lead đã đóng khỏi mọi bucket.

Từ 2026-09-08 (sự cố đơn 560919) khớp SĐT + thời gian là chưa đủ: lead chỉ đóng
khi tập SKU của lead nằm trọn trong hóa đơn. ``reopen_mismatched`` mở lại lead
đã bị luật cũ đóng oan.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.lead_close_service import LeadCloseService
from apps.orders.models import Order, OrderJourneyEvent
from apps.orders.sheet_rules import CASE_CLOSED_EVENT, CASE_REOPENED_EVENT
from apps.orders.tasks import close_converted_leads


class LeadCloseFixtureMixin:
    def setUp(self):
        self.channel = Channel.objects.create(id=930001, code="lc-ch", name="LC Channel")
        self.customer = Customer.objects.create(
            full_name="KH LC", phone_raw="0911111111", phone_normalized="0911111111"
        )

    def _order(self, *, phone="0911111111", status=0, invoice_status=None,
               invoice_code="", created_delta_days=0, sku="MSC06", **kwargs):
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="X",
            phone=phone,
            phone_normalized=phone,
            status=status,
            invoice_status=invoice_status,
            invoice_code=invoice_code,
            sku=sku,
            total_after_discount=Decimal("100000"),
            **kwargs,
        )
        if created_delta_days:
            Order.objects.filter(pk=order.pk).update(
                created_at=timezone.now() + timedelta(days=created_delta_days)
            )
            order.refresh_from_db()
        return order


class LeadCloseServiceTests(LeadCloseFixtureMixin, TestCase):
    def test_closes_lead_with_matching_invoice_in_window(self):
        lead = self._order(created_delta_days=-3)
        invoice = self._order(status=3, invoice_status=3, invoice_code="HD123")

        summary = LeadCloseService.close_converted()

        lead.refresh_from_db()
        self.assertEqual(summary["closed"], 1)
        self.assertEqual(lead.status, 6)
        event = OrderJourneyEvent.objects.get(order=lead)
        self.assertEqual(event.event_type, CASE_CLOSED_EVENT)
        self.assertEqual(event.payload_json["reason"], "converted_invoice")
        self.assertEqual(event.payload_json["matched_order_id"], invoice.id)
        self.assertEqual(event.payload_json["matched_invoice_code"], "HD123")
        self.assertTrue(event.payload_json["auto"])

    def test_ignores_invoice_outside_window_or_cancelled(self):
        # Hóa đơn có TRƯỚC lead quá 1 ngày → khách cũ mua lại, lead mới là thật.
        stale_lead = self._order()
        self._order(status=3, invoice_status=3, created_delta_days=-10)
        # Hóa đơn trong cửa sổ nhưng ĐÃ HỦY → giữ lead để sale chăm tiếp.
        cancelled_phone = "0922222222"
        kept_lead = self._order(phone=cancelled_phone)
        self._order(phone=cancelled_phone, status=6, invoice_status=2)

        summary = LeadCloseService.close_converted()

        stale_lead.refresh_from_db()
        kept_lead.refresh_from_db()
        self.assertEqual(summary["closed"], 0)
        self.assertEqual(stale_lead.status, 0)
        self.assertEqual(kept_lead.status, 0)
        self.assertEqual(OrderJourneyEvent.objects.count(), 0)

    def test_each_cancel_axis_alone_keeps_lead(self):
        """Hai trục hủy độc lập: hóa đơn KiotViet hủy (invoice_status=2) dù đơn chưa
        lật status, hoặc đơn hủy giao (status=6) dù hóa đơn còn mã sống — đều KHÔNG
        tính là chuyển đổi (khóa hành vi sau khi đổi tên hằng, audit 2026-09-23)."""
        invoice_cancelled_phone = "0933333333"
        lead_a = self._order(phone=invoice_cancelled_phone)
        self._order(phone=invoice_cancelled_phone, status=3, invoice_status=2)
        delivery_cancelled_phone = "0944444444"
        lead_b = self._order(phone=delivery_cancelled_phone)
        self._order(phone=delivery_cancelled_phone, status=6, invoice_status=3)

        summary = LeadCloseService.close_converted()

        lead_a.refresh_from_db()
        lead_b.refresh_from_db()
        self.assertEqual(summary["closed"], 0)
        self.assertEqual(lead_a.status, 0)
        self.assertEqual(lead_b.status, 0)

    def test_skips_leads_without_phone_and_already_closed(self):
        self._order(phone="", status=0)  # không SĐT → bỏ qua, không crash
        closed = self._order(status=6, created_delta_days=-2)  # đã đóng → giữ nguyên
        self._order(status=3, invoice_status=3)

        summary = LeadCloseService.close_converted()

        closed.refresh_from_db()
        self.assertEqual(summary["closed"], 0)
        self.assertEqual(OrderJourneyEvent.objects.count(), 0)

    def test_dry_run_reports_without_mutating(self):
        lead = self._order(created_delta_days=-3)
        self._order(status=3, invoice_status=3)

        summary = LeadCloseService.close_converted(dry_run=True)

        lead.refresh_from_db()
        self.assertEqual(summary["closed"], 1)
        self.assertTrue(summary["dry_run"])
        self.assertEqual(lead.status, 0)
        self.assertEqual(OrderJourneyEvent.objects.count(), 0)

    def test_lookback_limits_lead_scope(self):
        old_lead = self._order(created_delta_days=-40)
        self._order(status=3, invoice_status=3, created_delta_days=-40)

        summary = LeadCloseService.close_converted(lookback_days=30)
        old_lead.refresh_from_db()
        self.assertEqual(summary["closed"], 0)
        self.assertEqual(old_lead.status, 0)

        # lookback_days=0 → quét toàn bộ lịch sử (chạy hồi tố sau deploy).
        summary = LeadCloseService.close_converted(lookback_days=0)
        old_lead.refresh_from_db()
        self.assertEqual(summary["closed"], 1)
        self.assertEqual(old_lead.status, 6)

    def test_management_command_wraps_service(self):
        self._order(created_delta_days=-3)
        self._order(status=3, invoice_status=3)

        call_command("close_converted_leads")

        self.assertEqual(Order.objects.filter(status=6, invoice_status__isnull=True).count(), 1)


class LeadCloseSkuMatchTests(LeadCloseFixtureMixin, TestCase):
    """SKU là điều kiện bắt buộc — khách mua món khác KHÔNG phải ghi đôi."""

    def test_keeps_lead_when_invoice_is_a_different_product(self):
        # Shape của đơn 560919: cùng SĐT, cách nhau 8 phút, hai món khác nhau.
        lead = self._order(sku="TMS01-PROMAX", created_delta_days=-1)
        self._order(sku="DC12", status=3, invoice_status=3, invoice_code="HD018244")

        summary = LeadCloseService.close_converted()

        lead.refresh_from_db()
        self.assertEqual(summary["closed"], 0)
        self.assertEqual(lead.status, 0)
        self.assertEqual(OrderJourneyEvent.objects.count(), 0)

    def test_closes_lead_when_invoice_bundles_more_products(self):
        # Hóa đơn gộp: lead MSC06 vẫn là chính giao dịch đó.
        lead = self._order(sku="MSC06", created_delta_days=-1)
        self._order(sku="BNC02, msc06", status=3, invoice_status=3)

        summary = LeadCloseService.close_converted()

        lead.refresh_from_db()
        self.assertEqual(summary["closed"], 1)
        self.assertEqual(lead.status, 6)

    def test_keeps_lead_without_sku(self):
        # Lead trống sản phẩm (0đ) → không đủ căn cứ, để sale chăm tiếp.
        lead = self._order(sku="", created_delta_days=-1)
        self._order(sku="DC12", status=3, invoice_status=3)

        summary = LeadCloseService.close_converted()

        lead.refresh_from_db()
        self.assertEqual(summary["closed"], 0)
        self.assertEqual(lead.status, 0)


class LeadReopenMismatchedTests(LeadCloseFixtureMixin, TestCase):
    """Mở lại lead đã bị luật cũ (chỉ SĐT + thời gian) đóng oan."""

    def _closed_by_old_rule(self, *, lead_sku, invoice_sku):
        lead = self._order(sku=lead_sku, status=6, created_delta_days=-1)
        invoice = self._order(
            sku=invoice_sku, status=3, invoice_status=3, invoice_code="HD999"
        )
        OrderJourneyEvent.objects.create(
            order=lead,
            event_type=CASE_CLOSED_EVENT,
            payload_json={
                "reason": "converted_invoice",
                "matched_order_id": invoice.id,
                "matched_invoice_code": "HD999",
                "auto": True,
            },
        )
        return lead, invoice

    def test_reopens_lead_whose_sku_differs(self):
        lead, invoice = self._closed_by_old_rule(
            lead_sku="TMS01-PROMAX", invoice_sku="DC12"
        )

        summary = LeadCloseService.reopen_mismatched()

        lead.refresh_from_db()
        self.assertEqual(summary["reopened"], 1)
        self.assertEqual(summary["reopened_ids"], [lead.id])
        self.assertEqual(lead.status, 0)
        event = OrderJourneyEvent.objects.get(event_type=CASE_REOPENED_EVENT)
        self.assertEqual(event.order_id, lead.id)
        self.assertEqual(event.payload_json["reason"], "sku_mismatch")
        self.assertEqual(event.payload_json["matched_order_id"], invoice.id)

    def test_keeps_lead_closed_correctly(self):
        lead, _ = self._closed_by_old_rule(lead_sku="MSC06", invoice_sku="MSC06, DC12")

        summary = LeadCloseService.reopen_mismatched()

        lead.refresh_from_db()
        self.assertEqual(summary["reopened"], 0)
        self.assertEqual(lead.status, 6)
        self.assertFalse(
            OrderJourneyEvent.objects.filter(event_type=CASE_REOPENED_EVENT).exists()
        )

    def test_dry_run_and_idempotent(self):
        lead, _ = self._closed_by_old_rule(lead_sku="TMS01-PROMAX", invoice_sku="DC12")

        dry = LeadCloseService.reopen_mismatched(dry_run=True)
        lead.refresh_from_db()
        self.assertEqual(dry["reopened"], 1)
        self.assertEqual(lead.status, 6)

        LeadCloseService.reopen_mismatched()
        again = LeadCloseService.reopen_mismatched()
        self.assertEqual(again["reopened"], 0)
        self.assertEqual(
            OrderJourneyEvent.objects.filter(event_type=CASE_REOPENED_EVENT).count(), 1
        )

    def test_management_command_wraps_service(self):
        lead, _ = self._closed_by_old_rule(lead_sku="TMS01-PROMAX", invoice_sku="DC12")

        call_command("reopen_mismatched_leads")

        lead.refresh_from_db()
        self.assertEqual(lead.status, 0)


class LeadCloseCronDisabledTests(LeadCloseFixtureMixin, TestCase):
    """Cron tắt mặc định từ 2026-09-08 — chỉ chạy khi bật cờ có chủ ý."""

    def test_task_noops_when_flag_off(self):
        lead = self._order(created_delta_days=-1)
        self._order(status=3, invoice_status=3)

        with override_settings(LEAD_AUTO_CLOSE_ENABLED=False):
            self.assertEqual(close_converted_leads(), {"skipped": "disabled"})

        lead.refresh_from_db()
        self.assertEqual(lead.status, 0)

    def test_task_runs_when_flag_on(self):
        lead = self._order(created_delta_days=-1)
        self._order(status=3, invoice_status=3)

        with override_settings(LEAD_AUTO_CLOSE_ENABLED=True):
            summary = close_converted_leads()

        lead.refresh_from_db()
        self.assertEqual(summary["closed"], 1)
        self.assertEqual(lead.status, 6)
