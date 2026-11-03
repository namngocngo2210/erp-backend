"""Journey event cho các thay đổi NGOÀI khối CRM.

Trước đây ``_upsert_assessment`` thoát ngay khi payload không có ``crm`` nên mọi
thao tác đổi sale phụ trách, trạng thái đơn, mã vận đơn... không để lại dấu vết
nào: journey trống, audit_logs chỉ có 1 dòng PATCH không kèm body. Hệ quả thực tế
là không trả lời được câu "ai đã chuyển đơn này sang sale khác, lúc nào".

File này gộp thêm (tái cấu trúc 2026-08-05): API đọc timeline
``GET /api/orders/:id/journey-events`` (OrderJourneyEventsApiTests) và nhãn
tiếng Việt của event_type (EventTypeLabelTests) — docstring gốc giữ ở từng class.
"""
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import Customer
from apps.orders.application.services.lead_assignment_service import LeadAssignmentService
from apps.orders.crm_catalog import EVENT_TYPE_LABELS
from apps.orders.models import (
    LeadAssignmentShare,
    Order,
    OrderJourneyEvent,
    OrderStatus,
)
from apps.orders.sheet_rules import (
    CASE_CLOSED_EVENT,
    ORDER_CREATED_EVENT,
    ORDER_FIELD_CHANGED_EVENT,
    ORDER_ITEMS_CHANGED_EVENT,
    SALE_CONTACT_EVENT,
    STAFF_ASSIGNED_EVENT,
    STAFF_CHANGED_EVENT,
    STAFF_INGEST_DESIGNATED_EVENT,
)
from apps.users.constants import DepartmentID
from apps.users.models import Department, User
from apps.users.tests.helpers import make_admin

_SHIFT_PATH = (
    "apps.orders.application.services.lead_assignment_service."
    "ShiftService.current_shift_and_date"
)


def _make_sale(username: str) -> User:
    Department.objects.get_or_create(id=DepartmentID.SALES, defaults={"name": "Sales"})
    return User.objects.create(
        username=username, department_id=DepartmentID.SALES, is_active=True
    )


class PatchOrderJourneyEventTests(TestCase):
    """PATCH /api/orders/:id — field nghiệp vụ phải sinh journey event."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-field-journey")
        self.sale_a = _make_sale("sale-journey-a")
        self.sale_b = _make_sale("sale-journey-b")
        self.channel = Channel.objects.create(code="lp-field", name="Landing Field")
        self.customer = Customer.objects.create(
            full_name="Khach Field",
            phone_raw="0909111222",
            phone_normalized="0909111222",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Field",
            phone="0909111222",
            status=OrderStatus.NEW,
            staff=self.sale_a,
        )
        self.client.force_authenticate(user=self.admin)

    def _events(self, event_type: str):
        return list(
            OrderJourneyEvent.objects.filter(order=self.order, event_type=event_type)
        )

    def test_patch_staff_creates_staff_changed_event(self):
        response = self.client.patch(
            f"/api/orders/{self.order.id}",
            {"staff_id": self.sale_b.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        events = self._events(STAFF_CHANGED_EVENT)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].from_code, str(self.sale_a.id))
        self.assertEqual(events[0].to_code, str(self.sale_b.id))
        self.assertEqual(events[0].actor_id, self.admin.id)

    def test_staff_changed_event_records_names_for_reading(self):
        self.client.patch(
            f"/api/orders/{self.order.id}", {"staff_id": self.sale_b.id}, format="json"
        )

        event = self._events(STAFF_CHANGED_EVENT)[0]
        self.assertEqual(event.payload_json.get("from_name"), self.sale_a.username)
        self.assertEqual(event.payload_json.get("to_name"), self.sale_b.username)

    def test_patch_same_staff_creates_no_event(self):
        self.client.patch(
            f"/api/orders/{self.order.id}", {"staff_id": self.sale_a.id}, format="json"
        )

        self.assertEqual(self._events(STAFF_CHANGED_EVENT), [])

    def test_patch_tracked_field_creates_field_changed_event(self):
        response = self.client.patch(
            f"/api/orders/{self.order.id}",
            {"shipping_code": "BEST123456"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        events = self._events(ORDER_FIELD_CHANGED_EVENT)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload_json.get("field"), "shipping_code")
        self.assertEqual(events[0].to_code, "BEST123456")
        self.assertEqual(events[0].actor_id, self.admin.id)

    def test_address_rewritten_from_raw_address_is_tracked(self):
        """PATCH gửi `raw_address`, service tự nắn lại `address` → vẫn phải có event.

        Giá trị cuối mới là thứ nhân viên giao hàng nhìn thấy; chụp theo payload
        gốc sẽ bỏ sót đúng nhánh này.
        """
        response = self.client.patch(
            f"/api/orders/{self.order.id}",
            {"raw_address": "12 Le Loi, Quan 1, TP Ho Chi Minh"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        events = [
            e for e in self._events(ORDER_FIELD_CHANGED_EVENT)
            if e.payload_json.get("field") == "address"
        ]
        self.assertEqual(len(events), 1)
        self.order.refresh_from_db()
        self.assertEqual(events[0].to_code, self.order.address[:64])

    def test_patch_untracked_field_creates_no_event(self):
        self.client.patch(
            f"/api/orders/{self.order.id}",
            {"url_page": "https://landing.example.com/promo"},
            format="json",
        )

        self.assertEqual(self._events(ORDER_FIELD_CHANGED_EVENT), [])

    def test_patch_tracked_field_with_same_value_creates_no_event(self):
        Order.objects.filter(pk=self.order.pk).update(shipping_code="BEST123456")

        self.client.patch(
            f"/api/orders/{self.order.id}",
            {"shipping_code": "BEST123456"},
            format="json",
        )

        self.assertEqual(self._events(ORDER_FIELD_CHANGED_EVENT), [])

    def test_clearing_staff_creates_event_with_empty_target(self):
        response = self.client.patch(
            f"/api/orders/{self.order.id}", {"staff_id": None}, format="json"
        )
        self.assertEqual(response.status_code, 200)

        event = self._events(STAFF_CHANGED_EVENT)[0]
        self.assertEqual(event.from_code, str(self.sale_a.id))
        self.assertIsNone(event.to_code)


class LeadAssignmentJourneyEventTests(TestCase):
    """Bộ chia tự động + chuyển đơn hàng loạt cũng phải để lại dấu vết."""

    def setUp(self):
        self.sale = _make_sale("sale-auto-journey")
        self.receiver = _make_sale("sale-receiver-journey")
        self.channel = Channel.objects.create(code="lp-auto", name="Landing Auto")
        LeadAssignmentShare.objects.create(
            date=timezone.localdate(),
            shift="morning",
            sheet="sheet1",
            user=self.sale,
            weight=1,
        )

    def _make_order(self, phone: str) -> Order:
        customer = Customer.objects.create(
            full_name="KH Auto", phone_raw=phone, phone_normalized=phone
        )
        return Order.objects.create(
            channel=self.channel,
            customer=customer,
            name="Nguyen Van A",
            phone=phone,
            raw_address="1 Nguyen Trai, Q1",
            order_source="landing",
        )

    @patch(_SHIFT_PATH, side_effect=lambda: ("morning", timezone.localdate()))
    def test_auto_assign_creates_staff_assigned_event(self, _shift):
        order = self._make_order("0909222333")

        LeadAssignmentService.assign_if_unassigned(order)

        events = list(
            OrderJourneyEvent.objects.filter(order=order, event_type=STAFF_ASSIGNED_EVENT)
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].to_code, str(self.sale.id))
        # Bộ chia tự động chạy nền: không có người thao tác.
        self.assertIsNone(events[0].actor_id)

    @patch(_SHIFT_PATH, side_effect=lambda: ("morning", timezone.localdate()))
    def test_transfer_creates_staff_changed_event_per_order(self, _shift):
        first = self._make_order("0909222444")
        second = self._make_order("0909222555")
        Order.objects.filter(id__in=[first.id, second.id]).update(staff_id=self.sale.id)

        moved = LeadAssignmentService.transfer(
            sheet="sheet1",
            from_user_id=self.sale.id,
            to_user_id=self.receiver.id,
            count=2,
            date=timezone.localdate(),
            actor=None,
        )

        self.assertEqual(moved, 2)
        events = OrderJourneyEvent.objects.filter(
            order_id__in=[first.id, second.id], event_type=STAFF_CHANGED_EVENT
        )
        self.assertEqual(events.count(), 2)
        for event in events:
            self.assertEqual(event.from_code, str(self.sale.id))
            self.assertEqual(event.to_code, str(self.receiver.id))

    @patch(_SHIFT_PATH, side_effect=lambda: ("morning", timezone.localdate()))
    def test_transfer_endpoint_records_who_pressed_the_button(self, _shift):
        order = self._make_order("0909222666")
        Order.objects.filter(pk=order.pk).update(staff_id=self.sale.id)
        admin = make_admin("admin-transfer-journey")
        client = APIClient()
        client.force_authenticate(user=admin)

        response = client.post(
            "/api/orders/lead-assignment/transfer",
            {
                "sheet": "sheet1",
                "from_user_id": self.sale.id,
                "to_user_id": self.receiver.id,
                "count": 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        event = OrderJourneyEvent.objects.get(
            order=order, event_type=STAFF_CHANGED_EVENT
        )
        self.assertEqual(event.actor_id, admin.id)


class OrderJourneyEventsApiTests(TestCase):
    """GET /api/orders/:id/journey-events — timeline load-on-demand.

    Regression: board Tư vấn annotate contact_count riêng nhưng KHÔNG kèm
    journey events, nên card từng báo "chưa có lịch sử" dù đã gọi >0 lần.
    Endpoint này cấp timeline thật để side panel hiển thị đúng.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-journey")
        self.channel = Channel.objects.create(code="lp-journey", name="Landing Journey")
        self.customer = Customer.objects.create(
            full_name="Khach Journey",
            phone_raw="0909000111",
            phone_normalized="0909000111",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Journey",
            phone="0909000111",
            status=OrderStatus.NEW,
        )

    def test_returns_contact_events_newest_first(self):
        OrderJourneyEvent.objects.create(
            order=self.order, event_type=SALE_CONTACT_EVENT, note="Lần 1"
        )
        OrderJourneyEvent.objects.create(
            order=self.order, event_type=SALE_CONTACT_EVENT, note="Lần 2"
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/{self.order.id}/journey-events")

        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        # 2 lần liên hệ + dòng "Tạo đơn" suy ra (đơn không có event order_created).
        self.assertEqual(len(results), 3)
        # order_by("-created_at") → bản ghi mới nhất ("Lần 2") đứng trước.
        self.assertEqual(results[0]["note"], "Lần 2")
        self.assertEqual(results[0]["event_type"], SALE_CONTACT_EVENT)
        self.assertEqual(results[-1]["event_type"], ORDER_CREATED_EVENT)

    def test_order_without_created_event_gets_legacy_created_row(self):
        """Đơn tạo trước khi có event order_created: vẫn có mốc "Tạo đơn" ở cuối,
        người tạo không rõ (source=legacy) chứ không giả làm "Hệ thống"."""
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/{self.order.id}/journey-events")

        self.assertEqual(response.status_code, 200)
        [row] = response.data["results"]
        self.assertEqual(row["event_type"], ORDER_CREATED_EVENT)
        self.assertEqual(row["event_type_label"], "Tạo đơn")
        self.assertIsNone(row["actor_name"])
        self.assertEqual(row["payload_json"]["source"], "legacy")

    def test_real_created_event_suppresses_legacy_row(self):
        OrderJourneyEvent.objects.create(
            order=self.order,
            event_type=ORDER_CREATED_EVENT,
            actor=self.admin,
            payload_json={"source": "manual"},
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/{self.order.id}/journey-events")

        [row] = response.data["results"]
        self.assertEqual(row["payload_json"]["source"], "manual")
        self.assertTrue(row["actor_name"])

    def test_scoped_user_cannot_read_other_channel(self):
        staff = User.objects.create_user(username="staff-journey", password="x")
        other_channel = Channel.objects.create(code="lp-journey-other", name="Other")
        UserChannel.objects.create(user=staff, channel=other_channel, is_active=True)
        OrderJourneyEvent.objects.create(
            order=self.order, event_type=SALE_CONTACT_EVENT, note="Bí mật"
        )

        self.client.force_authenticate(user=staff)
        response = self.client.get(f"/api/orders/{self.order.id}/journey-events")

        self.assertEqual(response.status_code, 403)


EMITTED_EVENT_TYPES = (
    "assessment_created",
    "assessment_note_updated",
    "customer_classification_changed",
    "sales_status_changed",
    "not_closed_reason_changed",
    "delivery_business_status_changed",
    "potential_level_changed",
    SALE_CONTACT_EVENT,
    CASE_CLOSED_EVENT,
    STAFF_ASSIGNED_EVENT,
    STAFF_CHANGED_EVENT,
    STAFF_INGEST_DESIGNATED_EVENT,
    ORDER_FIELD_CHANGED_EVENT,
    ORDER_CREATED_EVENT,
    ORDER_ITEMS_CHANGED_EVENT,
)


class EventTypeLabelTests(SimpleTestCase):
    """Mọi event_type được ghi vào order_journey_events phải có nhãn tiếng Việt.

    ``sale_contact`` và ``case_closed`` đã tồn tại từ lâu nhưng chưa bao giờ được khai
    trong ``EVENT_TYPE_OPTIONS`` → ``event_type_label`` trả None và UI phải fallback về
    mã raw. Test này khoá lại: thêm event_type mới mà quên nhãn là fail.
    """

    def test_every_emitted_event_type_has_label(self):
        missing = [e for e in EMITTED_EVENT_TYPES if not EVENT_TYPE_LABELS.get(e)]

        self.assertEqual(missing, [])


class OrderJourneyEventsLimitTests(OrderJourneyEventsApiTests):
    """Trần dòng `?limit=` cho timeline một đơn (audit 2026-09-23 L6)."""

    def test_limit_keeps_newest_events(self):
        for i in range(3):
            OrderJourneyEvent.objects.create(
                order=self.order, event_type=SALE_CONTACT_EVENT, note=f"Lần {i}"
            )
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/{self.order.id}/journey-events?limit=2")
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertEqual([r["note"] for r in results], ["Lần 2", "Lần 1"])

    def test_invalid_limit_is_400(self):
        self.client.force_authenticate(user=self.admin)
        for query in ("?limit=abc", "?limit=0", "?limit=501"):
            response = self.client.get(f"/api/orders/{self.order.id}/journey-events{query}")
            self.assertEqual(response.status_code, 400, query)
