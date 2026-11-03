"""ContactActivity: log liên hệ append-only cấp khách hàng (call/note).

Lịch sử đổi trạng thái sale KHÔNG lưu ở đây — đọc từ OrderJourneyEvent
(event_type=sales_status_changed).
"""

from django.test import TestCase

from apps.customers.models import ContactActivity, ContactActivityKind, Customer


class ContactActivityModelTest(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            full_name="KH contact activity",
            phone_raw="0911000002",
            phone_normalized="0911000002",
        )

    def test_create_note_activity(self):
        activity = ContactActivity.objects.create(
            customer=self.customer,
            kind=ContactActivityKind.NOTE,
            note="Đã gọi hỏi thăm, khách hẹn tuần sau",
        )

        activity.refresh_from_db()
        self.assertEqual(activity.kind, ContactActivityKind.NOTE)
        self.assertEqual(activity.note, "Đã gọi hỏi thăm, khách hẹn tuần sau")
        self.assertIsNone(activity.call_meta)
        self.assertIsNone(activity.order_id)
        self.assertIsNone(activity.staff_id)
        self.assertIsNotNone(activity.created_at)

    def test_create_call_activity_with_call_meta_roundtrip(self):
        call_meta = {"duration_sec": 42, "outcome": "answered", "provider": "zenify"}
        activity = ContactActivity.objects.create(
            customer=self.customer,
            kind=ContactActivityKind.CALL,
            call_meta=call_meta,
        )

        activity.refresh_from_db()
        self.assertEqual(activity.kind, ContactActivityKind.CALL)
        self.assertEqual(activity.call_meta, call_meta)

    def test_ordering_by_created_at_desc(self):
        first = ContactActivity.objects.create(
            customer=self.customer, kind=ContactActivityKind.NOTE, note="first"
        )
        second = ContactActivity.objects.create(
            customer=self.customer, kind=ContactActivityKind.NOTE, note="second"
        )

        activities = list(
            ContactActivity.objects.filter(customer=self.customer).order_by("-created_at")
        )
        self.assertEqual(activities, [second, first])
