"""`LeadAssignmentService.transfer`: journey event ghi MỘT INSERT cho cả lô (audit 2026-09-23 T1 §2.6).

Trước: sau `.update()` hàng loạt (đang giữ lock các dòng `orders`) là vòng
`for order in Order.objects.filter(id__in=ids): log_staff_change(...)` → 1 SELECT +
N INSERT tuần tự trong lock. Sau: `log_staff_change_bulk` → 1 INSERT, số query
không đổi theo số đơn, payload journey y hệt đường lẻ.
"""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.lead_assignment_service import LeadAssignmentService
from apps.orders.application.services.order_service_helpers import OrderServiceHelpers
from apps.orders.models import Order, OrderJourneyEvent
from apps.users.constants import DepartmentID
from apps.users.models import Department, User


class LeadTransferQueryCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
        cls.a = User.objects.create(
            username="xfer-a", display_name="Sale A", department_id=DepartmentID.SALES
        )
        cls.b = User.objects.create(
            username="xfer-b", display_name="Sale B", department_id=DepartmentID.SALES
        )
        cls.channel = Channel.objects.create(code="xfer-ch", name="Xfer", owner="X")

    def _seed(self, count: int, prefix: str) -> None:
        for i in range(count):
            phone = f"09{prefix}{i:05d}"
            customer = Customer.objects.create(
                full_name="KH", phone_raw=phone, phone_normalized=phone
            )
            Order.objects.create(
                channel=self.channel, customer=customer, name="KH", phone=phone,
                raw_address="HN", order_source="landing", staff=self.a,
            )

    def _transfer(self, count: int) -> int:
        with CaptureQueriesContext(connection) as ctx:
            moved = LeadAssignmentService.transfer(
                "sheet1", self.a.id, self.b.id, count, None, actor=self.b
            )
        self.assertEqual(moved, count)
        return len(ctx.captured_queries)

    def test_transfer_query_count_constant_n5_vs_n15(self):
        self._seed(5, "60")
        small = self._transfer(5)
        self._seed(15, "61")
        large = self._transfer(15)
        self.assertEqual(
            large, small, f"N+1 transfer: 5 đơn = {small} query, 15 đơn = {large} query."
        )
        self.assertEqual(Order.objects.filter(staff=self.b).count(), 20)
        self.assertEqual(
            OrderJourneyEvent.objects.filter(order__staff=self.b, event_type="staff_changed").count()
            if OrderJourneyEvent.objects.filter(event_type="staff_changed").exists()
            else OrderJourneyEvent.objects.filter(order__staff=self.b).count(),
            20,
        )

    def test_bulk_event_payload_matches_single(self):
        self._seed(2, "62")
        first, second = list(Order.objects.filter(staff=self.a).order_by("id"))
        OrderServiceHelpers.log_staff_change(
            order=first, from_staff_id=self.a.id, to_staff_id=self.b.id, actor=self.b
        )
        OrderServiceHelpers.log_staff_change_bulk(
            order_ids=[second.id], from_staff_id=self.a.id, to_staff_id=self.b.id, actor=self.b
        )
        single = OrderJourneyEvent.objects.get(order=first)
        bulk = OrderJourneyEvent.objects.get(order=second)
        for field in ("event_type", "from_code", "to_code", "note", "actor_id", "payload_json"):
            self.assertEqual(getattr(single, field), getattr(bulk, field), field)
        self.assertEqual(bulk.note, "Sale A → Sale B")

    def test_bulk_noop_when_staff_unchanged(self):
        self._seed(1, "63")
        order = Order.objects.get(staff=self.a)
        written = OrderServiceHelpers.log_staff_change_bulk(
            order_ids=[order.id], from_staff_id=self.a.id, to_staff_id=self.a.id
        )
        self.assertEqual(written, 0)
        self.assertFalse(OrderJourneyEvent.objects.filter(order=order).exists())
