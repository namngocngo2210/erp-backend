"""Backfill cờ has_address=False cho đơn tạo tay thiếu địa chỉ (sự cố HD001316)."""
from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import LandingIngestTrace, Order, OrderJourneyEvent
from apps.orders.sheet_rules import ORDER_FIELD_CHANGED_EVENT

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class BackfillManualOrderHasAddressTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="bf-noaddr", name="BF Noaddr")
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000020", phone_normalized="0900000020"
        )

    def _order(self, *, created=datetime(2026, 9, 15, 10, 0, tzinfo=VN_TZ), source="zalo", raw_address="", **kwargs):
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="KH",
            phone="0900000020",
            order_source=source,
            raw_address=raw_address,
            **kwargs,
        )
        Order.objects.filter(pk=order.pk).update(created_at=created)
        return order

    def _fill_address_later(self, order):
        OrderJourneyEvent.objects.create(
            order=order,
            event_type=ORDER_FIELD_CHANGED_EVENT,
            payload_json={"field": "address", "from": "", "to": "122 Nguyen Van Hoi"},
        )
        Order.objects.filter(pk=order.pk).update(raw_address="122 Nguyen Van Hoi", address="122 Nguyen Van Hoi")

    def _run(self, *args):
        out = StringIO()
        call_command("backfill_manual_order_has_address", "--since=2026-09-01", "--until=2026-09-30", *args, stdout=out)
        return out.getvalue()

    def _has_address(self, order):
        order.refresh_from_db(fields=["has_address"])
        return order.has_address

    def test_dry_run_writes_nothing(self):
        order = self._order()
        self._fill_address_later(order)

        output = self._run()

        self.assertIn("DRY-RUN", output)
        self.assertTrue(self._has_address(order))

    def test_apply_flags_filled_later_and_still_blank_orders(self):
        filled = self._order()
        self._fill_address_later(filled)
        blank = self._order(source="messenger")

        self._run("--apply")

        self.assertFalse(self._has_address(filled))
        self.assertFalse(self._has_address(blank))

    def test_apply_skips_orders_out_of_scope(self):
        created_with_address = self._order(raw_address="123 Nguyen Trai")
        outside_month = self._order(created=datetime(2026, 10, 1, 0, 5, tzinfo=VN_TZ))
        koc = self._order(source="koc")
        ingested = self._order()
        LandingIngestTrace.objects.create(order=ingested, phone="0900000020", payload={})

        self._run("--apply")

        for order in (created_with_address, outside_month, koc, ingested):
            self.assertTrue(self._has_address(order), order.pk)

    def test_september_bounds_use_vietnam_time(self):
        last_minute = self._order(created=datetime(2026, 9, 30, 23, 59, tzinfo=VN_TZ))

        self._run("--apply")

        self.assertFalse(self._has_address(last_minute))
