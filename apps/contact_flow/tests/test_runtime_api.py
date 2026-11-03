"""API test POST /api/contact-flow/customers/<id>/result và .../due (task S2-1).

Xem .superpowers/sdd/task-S2-1-brief.md — case list: RBAC, scope OWN, apply
result hợp lệ, requires_schedule thiếu giờ, edit due không cho phép, bucket_of.
Scope test setup mirror `_own_scope_user` của
apps/orders/tests/test_contact_activity_api.py (quyền `contacts`).
"""
from __future__ import annotations

import datetime as dt
from datetime import timedelta

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.contact_flow.application.services.operation_engine import OperationEngine
from apps.contact_flow.care_stages import SEEDED_CARE_STAGE_NAMES
from apps.contact_flow.models import OperationResult, OperationStage, OperationTransition
from apps.contact_flow.transport.serializers.runtime_serializers import bucket_of
from apps.customers.models import Customer
from apps.orders.models import Order
from apps.users.models import (
    DataScope, Permission, Role, RolePermission, RoleSectionScope, User, UserRoleAssignment,
)
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import sync_permissions
from apps.users.tests.helpers import make_admin, make_user_with_sections


def _own_scope_user(username: str) -> User:
    """User có contacts:* nhưng scope section contacts = OWN (mirror
    test_contact_activity_api._own_scope_user)."""
    sync_permissions()
    user = User.objects.create_user(username=username, password="x")
    role = Role.objects.create(name=f"role-{username}", scope_default=DataScope.ALL)
    RolePermission.objects.bulk_create(
        RolePermission(role=role, permission=p)
        for p in Permission.objects.filter(section="contacts")
    )
    RoleSectionScope.objects.create(role=role, section="contacts", scope=DataScope.OWN)
    UserRoleAssignment.objects.create(user=user, role=role)
    bump_rbac_cache_version()
    return user


class ContactFlowRuntimeApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(code="cf-runtime-ch", name="CF Runtime Channel")

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-cf-runtime")
        self.customer = Customer.objects.create(
            full_name="KH Runtime", phone_raw="0955555555", phone_normalized="0955555555"
        )
        self.order = Order.objects.create(
            channel=self.channel, customer=self.customer, order_source="landing"
        )
        self.start = OperationStage.objects.create(name="Gọi lần đầu", is_start=True)
        self.knm1 = OperationStage.objects.create(name="KNM 1", color="#00ff00")
        self.r_knm = OperationResult.objects.create(name="KNM / TB")
        self.r_sched = OperationResult.objects.create(name="Hẹn gọi lại", requires_schedule=True)
        OperationTransition.objects.create(
            from_stage=self.start, result=self.r_knm, to_stage=self.knm1, delay_minutes=30
        )

    # -- RBAC -----------------------------------------------------------------
    def test_result_requires_auth_and_contacts_permission(self):
        response = self.client.post(
            f"/api/contact-flow/customers/{self.customer.id}/result",
            {"result_id": self.r_knm.id}, format="json",
        )
        self.assertEqual(response.status_code, 401)

        no_access = make_user_with_sections("cf-runtime-no-access", [])
        self.client.force_authenticate(user=no_access)
        response = self.client.post(
            f"/api/contact-flow/customers/{self.customer.id}/result",
            {"result_id": self.r_knm.id}, format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_due_requires_auth_and_contacts_permission(self):
        response = self.client.post(
            f"/api/contact-flow/customers/{self.customer.id}/due",
            {"due_at": (timezone.now() + timedelta(hours=1)).isoformat()}, format="json",
        )
        self.assertEqual(response.status_code, 401)

        no_access = make_user_with_sections("cf-runtime-no-access-due", [])
        self.client.force_authenticate(user=no_access)
        response = self.client.post(
            f"/api/contact-flow/customers/{self.customer.id}/due",
            {"due_at": (timezone.now() + timedelta(hours=1)).isoformat()}, format="json",
        )
        self.assertEqual(response.status_code, 403)

    # -- Scope ------------------------------------------------------------------
    def test_own_scope_user_not_owning_customer_gets_404(self):
        own_user = _own_scope_user("cf-runtime-own-not-owner")
        self.client.force_authenticate(user=own_user)
        response = self.client.post(
            f"/api/contact-flow/customers/{self.customer.id}/result",
            {"result_id": self.r_knm.id}, format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_own_scope_user_owning_customer_can_access(self):
        own_user = _own_scope_user("cf-runtime-own-owner")
        self.order.staff = own_user
        self.order.save(update_fields=["staff"])

        self.client.force_authenticate(user=own_user)
        response = self.client.post(
            f"/api/contact-flow/customers/{self.customer.id}/result",
            {"result_id": self.r_knm.id}, format="json",
        )
        self.assertEqual(response.status_code, 200)

    # -- apply_result -------------------------------------------------------
    def test_apply_result_valid_returns_200_and_updates_state(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/contact-flow/customers/{self.customer.id}/result",
            {"result_id": self.r_knm.id}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["stage_name"], "KNM 1")
        self.assertEqual(body["stage_color"], "#00ff00")
        self.assertIn("due_at", body)
        # due_at = "now" tại lúc chạy test + 30' (delay_minutes transition) — hardcode
        # "today" flake ở biên nửa đêm (due_at lỡ sang ngày hôm sau). Recompute bucket kỳ
        # vọng bằng chính bucket_of trên due_at server trả về (cùng hàm server dùng) thay
        # vì đoán trước bucket cố định.
        self.assertEqual(body["bucket"], bucket_of(parse_datetime(body["due_at"])))

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.operation_state.current_stage_id, self.knm1.id)

    def test_apply_result_requires_schedule_missing_returns_400_vietnamese(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/contact-flow/customers/{self.customer.id}/result",
            {"result_id": self.r_sched.id}, format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Kết quả này bắt buộc chọn thời gian hẹn.")

    def test_apply_result_unknown_customer_returns_404(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/contact-flow/customers/999999/result",
            {"result_id": self.r_knm.id}, format="json",
        )
        self.assertEqual(response.status_code, 404)

    # -- edit_due -------------------------------------------------------------
    def test_edit_due_stage_not_allowed_returns_400(self):
        OperationEngine.enter_start(self.customer)

        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/contact-flow/customers/{self.customer.id}/due",
            {"due_at": (timezone.now() + timedelta(hours=1)).isoformat()}, format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Bước hiện tại không cho phép sửa giờ.")

    def test_edit_due_valid_returns_200(self):
        OperationEngine.enter_start(self.customer)
        OperationStage.objects.filter(pk=self.start.id).update(allow_edit_due=True)

        self.client.force_authenticate(user=self.admin)
        new_due = timezone.now() + timedelta(hours=2)
        response = self.client.post(
            f"/api/contact-flow/customers/{self.customer.id}/due",
            {"due_at": new_due.isoformat()}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["stage_name"], "Gọi lần đầu")


class ContactFlowRuntimeChannelScopeTests(TestCase):
    """Scope CHANNEL cho 2 endpoint runtime — mirror
    apps/orders/tests/test_contact_scope.py::ContactChannelScopeTests (audit
    2026-07-15b: mọi mức DataScope phải enforce, không chỉ OWN)."""

    @classmethod
    def setUpTestData(cls):
        cls.channel_a = Channel.objects.create(code="cf-chan-a", name="CF Kenh A")
        cls.channel_b = Channel.objects.create(code="cf-chan-b", name="CF Kenh B")
        cls.customer_a = Customer.objects.create(
            full_name="KH Kenh A", phone_raw="0956666666", phone_normalized="0956666666"
        )
        cls.customer_b = Customer.objects.create(
            full_name="KH Kenh B", phone_raw="0957777777", phone_normalized="0957777777"
        )
        Order.objects.create(channel=cls.channel_a, customer=cls.customer_a, order_source="landing")
        Order.objects.create(channel=cls.channel_b, customer=cls.customer_b, order_source="landing")

    def setUp(self):
        self.start = OperationStage.objects.create(name="Gọi lần đầu", is_start=True)
        self.knm1 = OperationStage.objects.create(name="KNM 1")
        self.r_knm = OperationResult.objects.create(name="KNM / TB")
        OperationTransition.objects.create(
            from_stage=self.start, result=self.r_knm, to_stage=self.knm1, delay_minutes=30
        )

    def _channel_user(self, username, channels):
        user = make_user_with_sections(username, ["contacts"], scope=DataScope.CHANNEL)
        for channel in channels:
            UserChannel.objects.create(user=user, channel=channel, is_active=True)
        return user

    def test_channel_scope_with_customer_channel_can_apply_result(self):
        user = self._channel_user("cf-chan-a-user", [self.channel_a])
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            f"/api/contact-flow/customers/{self.customer_a.id}/result",
            {"result_id": self.r_knm.id}, format="json",
        )
        self.assertEqual(response.status_code, 200)

    def test_channel_scope_without_customer_channel_gets_404(self):
        user = self._channel_user("cf-chan-a-only-user", [self.channel_a])
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            f"/api/contact-flow/customers/{self.customer_b.id}/result",
            {"result_id": self.r_knm.id}, format="json",
        )
        self.assertEqual(response.status_code, 404)

        due_response = client.post(
            f"/api/contact-flow/customers/{self.customer_b.id}/due",
            {"due_at": (timezone.now() + timedelta(hours=1)).isoformat()}, format="json",
        )
        self.assertEqual(due_response.status_code, 404)


class SetStageApiTests(TestCase):
    """POST /api/contact-flow/customers/<id>/stage — đặt bước tay từ dropdown cột Tác nghiệp."""

    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(code="cf-stage-ch", name="CF Stage Channel")

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-cf-stage")
        self.customer = Customer.objects.create(
            full_name="KH Stage", phone_raw="0966666666", phone_normalized="0966666666"
        )
        self.order = Order.objects.create(
            channel=self.channel, customer=self.customer, order_source="landing"
        )
        self.start = OperationStage.objects.create(name="Gọi lần đầu CF Stage", is_start=True)
        self.care = OperationStage.objects.create(name="D7 CF Stage", color="#14b8a6")

    def _url(self, customer_id=None):
        return f"/api/contact-flow/customers/{customer_id or self.customer.id}/stage"

    def _body(self, stage=None, hours=24):
        return {
            "stage_id": (stage or self.care).id,
            "due_at": (timezone.now() + timedelta(hours=hours)).isoformat(),
        }

    def test_requires_auth_and_contacts_permission(self):
        response = self.client.post(self._url(), self._body(), format="json")
        self.assertEqual(response.status_code, 401)

        no_access = make_user_with_sections("cf-stage-no-access", [])
        self.client.force_authenticate(user=no_access)
        response = self.client.post(self._url(), self._body(), format="json")
        self.assertEqual(response.status_code, 403)

    def test_own_scope_user_not_owning_customer_gets_404(self):
        self.client.force_authenticate(user=_own_scope_user("cf-stage-own-not-owner"))
        response = self.client.post(self._url(), self._body(), format="json")
        self.assertEqual(response.status_code, 404)

    def test_own_scope_user_owning_customer_can_set_stage(self):
        own_user = _own_scope_user("cf-stage-own-owner")
        self.order.staff = own_user
        self.order.save(update_fields=["staff"])

        self.client.force_authenticate(user=own_user)
        response = self.client.post(self._url(), self._body(), format="json")
        self.assertEqual(response.status_code, 200)

    def test_valid_returns_new_state_payload(self):
        self.client.force_authenticate(user=self.admin)
        OperationEngine.enter_start(self.customer)

        response = self.client.post(self._url(), self._body(hours=24 * 7), format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["stage_name"], "D7 CF Stage")
        self.assertEqual(response.data["stage_color"], "#14b8a6")
        self.assertEqual(response.data["bucket"], "upcoming")

    def test_customer_not_in_flow_can_be_set(self):
        """Trước đây cột Tác nghiệp của khách này hiện "—" và không thao tác được."""
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self._url(), self._body(), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["stage_name"], "D7 CF Stage")

    def test_unknown_stage_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            self._url(),
            {"stage_id": 999999, "due_at": (timezone.now() + timedelta(hours=1)).isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_past_due_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self._url(), self._body(hours=-1), format="json")
        self.assertEqual(response.status_code, 400)

    def test_missing_due_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self._url(), {"stage_id": self.care.id}, format="json")
        self.assertEqual(response.status_code, 400)


class StagesListForContactsSectionTests(TestCase):
    """GET /api/contact-flow/stages phải mở cho section `contacts`.

    Không mở thì dropdown cột Tác nghiệp rỗng với đúng nhóm dùng nó nhiều nhất (sale
    không có section contact_flow). Ghi vẫn phải đòi `contact_flow:edit`.
    """

    def setUp(self):
        self.client = APIClient()
        OperationStage.objects.create(name="Gọi lần đầu CF List", is_start=True)

    def test_contacts_only_user_can_read_stages(self):
        self.client.force_authenticate(user=make_user_with_sections("cf-stages-contacts", ["contacts"]))
        response = self.client.get("/api/contact-flow/stages")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(s["name"] == "Gọi lần đầu CF List" for s in response.data))

    def test_contacts_only_user_cannot_create_stage(self):
        self.client.force_authenticate(user=make_user_with_sections("cf-stages-contacts-w", ["contacts"]))
        response = self.client.post("/api/contact-flow/stages", {"name": "Bước lén"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(OperationStage.objects.filter(name="Bước lén").exists())

    def test_user_without_either_section_gets_403(self):
        self.client.force_authenticate(user=make_user_with_sections("cf-stages-none", []))
        response = self.client.get("/api/contact-flow/stages")
        self.assertEqual(response.status_code, 403)


class SeededCareStagesTests(TestCase):
    """Migration contact_flow/0002 seed 5 bước chăm sóc — chạy theo migration của test DB."""

    def test_five_care_stages_exist_and_allow_edit_due(self):
        stages = {
            s.name: s
            for s in OperationStage.objects.filter(name__in=SEEDED_CARE_STAGE_NAMES)
        }
        # Khoá luôn drift giữa hằng số dùng chung và danh sách THẬT trong migration 0002.
        self.assertEqual(set(stages), set(SEEDED_CARE_STAGE_NAMES))
        for stage in stages.values():
            self.assertTrue(stage.is_active)
            self.assertTrue(stage.allow_edit_due, stage.name)
            self.assertFalse(stage.is_start, stage.name)


class BucketOfTests(SimpleTestCase):
    """bucket_of là nguồn sự thật cho phân loại due_at (S2-2 tái dùng)."""

    _NOW = timezone.make_aware(dt.datetime(2026, 7, 18, 10, 0, 0))

    def test_past_due_is_overdue(self):
        self.assertEqual(bucket_of(self._NOW - timedelta(hours=1), now=self._NOW), "overdue")

    def test_today_future_is_today(self):
        self.assertEqual(bucket_of(self._NOW + timedelta(hours=2), now=self._NOW), "today")

    def test_tomorrow_is_upcoming(self):
        self.assertEqual(bucket_of(self._NOW + timedelta(days=1), now=self._NOW), "upcoming")

    def test_null_due_is_ended(self):
        self.assertEqual(bucket_of(None, now=self._NOW), "ended")
