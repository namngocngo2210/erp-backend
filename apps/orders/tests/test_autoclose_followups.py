"""Command autoclose_followups — tự đóng (archive) case Sheet 3 quá 14 ngày
không liên hệ.

Phải bao đúng tập đơn mà ``sheet3_view`` hiển thị, tức dùng chung
``consulting_q()`` (xem apps.orders.sheet_rules). Trước fix 2026-08-11, command
tự lọc "raw_address và address đều rỗng" — bỏ sót vế ``has_address=False``
(cờ bất biến ghi lúc ingest): đơn đổ về không địa chỉ mà sale điền đủ thông
tin sau đó vẫn ở lại Sheet 3 (theo consulting_q), nhưng command KHÔNG đóng nó
vì address lúc kiểm tra đã có chữ → case kẹt vĩnh viễn trên board.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment, OrderJourneyEvent
from apps.orders.sheet_rules import CASE_CLOSED_EVENT, FOLLOWUP_AUTOCLOSE_DAYS


class AutocloseFollowupsCommandTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(id=940001, code="ac-ch", name="AC Channel")
        self.customer = Customer.objects.create(
            full_name="KH AC", phone_raw="0933333333", phone_normalized="0933333333"
        )

    def _order(self, *, phone="0933333333", name="Nguyen Van A", raw_address="",
               address="", has_address=True, order_source="landing",
               stale_days=FOLLOWUP_AUTOCLOSE_DAYS + 1, **kwargs):
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name=name,
            phone=phone,
            phone_normalized=phone,
            raw_address=raw_address,
            address=address,
            has_address=has_address,
            order_source=order_source,
            total_after_discount=Decimal("100000"),
            **kwargs,
        )
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(days=stale_days)
        )
        order.refresh_from_db()
        return order

    def test_archives_sticky_consulting_order_with_full_info_filled(self):
        """Đơn đổ về has_address=False, sale đã điền đủ Tên/SĐT/Địa chỉ, quá hạn
        không liên hệ → vẫn thuộc Sheet 3 (consulting_q) nên PHẢI bị archive."""
        order = self._order(
            name="Nguyen Van A", phone="0933333333",
            raw_address="12 Le Loi, Q1", address="12 Le Loi, Q1",
            has_address=False,
        )

        call_command("autoclose_followups")

        order.refresh_from_db()
        assessment = OrderAssessment.objects.get(order=order)
        self.assertEqual(assessment.sales_status_code, "archived")
        event = OrderJourneyEvent.objects.get(order=order)
        self.assertEqual(event.event_type, CASE_CLOSED_EVENT)
        self.assertEqual(event.to_code, "archived")
        self.assertTrue(event.payload_json["auto"])

    def test_does_not_archive_complete_order_outside_consulting(self):
        """Đơn đủ thông tin và has_address=True (không dính vế nào của
        consulting_q) không thuộc Sheet 3 → không được autoclose."""
        order = self._order(
            name="Nguyen Van B", phone="0933333334",
            raw_address="12 Le Loi, Q1", address="12 Le Loi, Q1",
            has_address=True,
        )

        call_command("autoclose_followups")

        self.assertFalse(OrderAssessment.objects.filter(order=order).exists())
        self.assertEqual(OrderJourneyEvent.objects.count(), 0)

    def test_still_archives_legacy_address_blank_case(self):
        """Vế thiếu Tên/SĐT/Địa chỉ (raw_address và address rỗng) — luật cũ —
        vẫn phải tiếp tục hoạt động sau khi đổi sang consulting_q()."""
        order = self._order(
            name="Nguyen Van C", phone="0933333335",
            raw_address="", address="", has_address=True,
        )

        call_command("autoclose_followups")

        order.refresh_from_db()
        assessment = OrderAssessment.objects.get(order=order)
        self.assertEqual(assessment.sales_status_code, "archived")
