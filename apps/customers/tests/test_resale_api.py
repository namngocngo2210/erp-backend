"""Resale API: note, channel-scope guard, appointment reminder.

Gộp từ test_customer_resale_note_api.py, test_resale_channel_scope_api.py,
test_resale_appointment_reminder.py (tái cấu trúc test 2026-08-05).
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.customer_resale_service import CustomerResaleService
from apps.customers.models import Customer, LoyalCustomerNote
from apps.orders.models import (
    Order,
    OrderSource,
    SaleReminder,
    SaleReminderKind,
    SaleReminderStatus,
)
from apps.users.models import User
from apps.users.tests.helpers import make_admin, make_user_with_sections


class CustomerResaleNoteApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-resale")
        self.customer = Customer.objects.create(code="KH1", full_name="A", phone_raw="0900", phone_normalized="0900")

    def test_update_resale_fields(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/customers/{self.customer.id}/resale-note",
            {"resale_status": "Đã mua lại 3 lần", "resale_suggestion": "BNC02"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["resale_status"], "Đã mua lại 3 lần")
        note = LoyalCustomerNote.objects.get(customer=self.customer)
        self.assertEqual(note.resale_suggestion, "BNC02")
        self.assertEqual(note.updated_by_id, self.admin.id)

    def test_requires_auth(self):
        response = self.client.patch(f"/api/customers/{self.customer.id}/resale-note", {}, format="json")
        self.assertEqual(response.status_code, 401)


class ResaleChannelScopeApiTests(TestCase):
    """Object-level (channel) authorization for resale-tracking mutations.

    Security fix H2: the resale-tracking endpoints mutate an order identified by a
    path id. They must reject the request when the order's channel is outside the
    acting user's edit scope (mirroring the order-mutation endpoints in
    apps/orders/.../order_action_view.py, which gate on
    ``OrderService.can_edit_channel``).

    NOTE: ``UserService.can_edit_order`` is currently a permissive stub (it always
    returns can_edit=True — see commit "remove userchannel"), so in the live config
    no user is actually blocked. These tests assert the *guard wiring* by patching
    ``OrderService.can_edit_channel`` — exactly the hook that will start enforcing
    again if/when channel scoping is re-enabled centrally.
    """

    def setUp(self):
        self.client = APIClient()
        # Staff with resale-tracking section access; channel scoping is exercised
        # separately by patching OrderService.can_edit_channel below.
        self.user = make_user_with_sections("resale-staff", ["resale-tracking"])
        self.client.force_authenticate(self.user)

        self.channel = Channel.objects.create(code="lp-scope", name="LP Scope")
        self.customer = Customer.objects.create(
            full_name="Khach Resale",
            phone_raw="0911111111",
            phone_normalized="0911111111",
        )
        # status=3 (delivered success), invoice not cancelled → qualifies as a
        # resale-tracking order so the happy path reaches the mutation.
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Resale",
            phone="0911111111",
            status=3,
            invoice_status=0,
        )

    # ── 3 mutation × (ngoài scope → 403 / trong scope → 2xx) ──────────────
    # patch `can_edit_channel` là chủ đích: test wiring view → scope kênh, còn
    # bản thân `can_edit_channel` đã có test riêng ở orders.
    MUTATIONS = (
        ("contact", "patch", "contact", {"contact_no": 1, "checked": True}, 200),
        ("appointment", "patch", "appointment", {"appointment_at": "2026-12-31T09:00:00Z"}, 200),
        ("order_note", "post", "order", {"product": "Bồn ngâm chân", "quantity": 1}, 201),
    )

    def test_mutations_gate_on_channel_scope(self):
        for label, method, path, payload, ok_status in self.MUTATIONS:
            for can_edit, expected in ((False, 403), (True, ok_status)):
                with self.subTest(label, can_edit=can_edit):
                    with patch(
                        "apps.customers.views.OrderService.can_edit_channel",
                        return_value=can_edit,
                    ):
                        resp = getattr(self.client, method)(
                            f"/api/customers/resale/{self.order.id}/{path}",
                            data=payload,
                            format="json",
                        )
                    self.assertEqual(resp.status_code, expected, resp.data)

    def test_missing_order_returns_404_before_mutation(self):
        resp = self.client.patch(
            "/api/customers/resale/999999/contact",
            data={"contact_no": 1, "checked": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 404, resp.data)


class UpdateResaleAppointmentSchedulesReminderTests(TestCase):
    """`update_resale_appointment` must (re)schedule a Lark reminder via the
    shared SaleReminder pipeline — covering the customers→orders wiring."""

    def setUp(self):
        self.channel = Channel.objects.create(code="lp-rs-api", name="LP Resale API")
        self.staff = User.objects.create_user(
            username="sale-rs-api", password="x", display_name="Mai"
        )
        self.customer = Customer.objects.create(
            code="KH-RS-1", full_name="A", phone_raw="0900", phone_normalized="0900"
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="A",
            phone="0900",
            staff=self.staff,
            order_source=OrderSource.LANDING,
            status=3,  # DELIVERED_SUCCESS
        )

    def test_setting_appointment_creates_pending_reminder(self):
        due = timezone.now() + timedelta(days=1)
        CustomerResaleService.update_resale_appointment(
            self.order.id,
            appointment_at=due,
            channel="Gọi điện",
            purpose="Tư vấn combo",
            updated_by=self.staff,
        )

        reminder = SaleReminder.objects.get(
            order=self.order,
            kind=SaleReminderKind.RESALE_APPOINTMENT,
            status=SaleReminderStatus.PENDING,
        )
        self.assertEqual(reminder.due_at, due)
        self.assertEqual(reminder.staff, self.staff)
        self.assertIn("Tư vấn combo", reminder.note_snapshot)
        self.assertIn("Gọi điện", reminder.note_snapshot)

    def test_clearing_appointment_cancels_reminder(self):
        CustomerResaleService.update_resale_appointment(
            self.order.id,
            appointment_at=timezone.now() + timedelta(days=1),
            updated_by=self.staff,
        )
        CustomerResaleService.update_resale_appointment(
            self.order.id, appointment_at=None, updated_by=self.staff
        )

        self.assertFalse(
            SaleReminder.objects.filter(
                order=self.order, status=SaleReminderStatus.PENDING
            ).exists()
        )
