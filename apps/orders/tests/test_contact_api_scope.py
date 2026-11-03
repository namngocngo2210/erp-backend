"""API test GET/POST /api/sale/contacts/<customer_id>/activities.

Xem .superpowers/sdd/task-6-brief.md. Timeline = UNION OrderJourneyEvent
(event_type in assessment_created/sales_status_changed) + ContactActivity
(kind call/note).

Gộp 2026-08-05 từ test_contact_activity_api.py + test_contact_scope.py +
test_contact_rules.py (xem docs/test-restructure-2026-08-05.md).
"""
from datetime import timedelta

import pytest
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import ContactActivity, Customer
from apps.orders.domain.contact_rules import ACTION_KEYS, derive_action_needed
from apps.orders.models import Order, OrderJourneyEvent
from apps.orders.application.services.order_service_impl import OrderService
from apps.users.models import DataScope, Permission, Role, RolePermission, RoleSectionScope, User, UserRoleAssignment
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import sync_permissions
from apps.users.tests.helpers import make_admin, make_user_with_sections


def _customer_activity(**kwargs):
    defaults = dict(full_name="KH Activity API", phone_raw="0933333333", phone_normalized="0933333333")
    defaults.update(kwargs)
    return Customer.objects.create(**defaults)


def _own_scope_user(username: str) -> User:
    """User có contacts:view nhưng scope section contacts = OWN (mirror test_contact_list_service)."""
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


class ContactActivityApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(code="contact-act-ch", name="Contact Activity Channel")

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-contact-act")
        self.customer = _customer_activity()
        self.other_customer = _customer_activity(
            full_name="KH khac", phone_raw="0933333334", phone_normalized="0933333334"
        )
        self.order = Order.objects.create(
            channel=self.channel, customer=self.customer, order_source="landing"
        )
        self.other_order = Order.objects.create(
            channel=self.channel, customer=self.other_customer, order_source="landing"
        )

    # -- GET: real journey events from _upsert_order_assessment path ----------
    def test_get_returns_status_change_from_real_assessment_flow(self):
        # First save creates assessment: fires assessment_created AND sales_status_changed
        # (sales_status_code is a "changed field" on create too, see _upsert_order_assessment).
        OrderService.update_order_inline(
            self.order, {"crm": {"sales_status_code": "scheduled"}}, self.admin
        )
        # Second save actually changes sales_status_code -> another sales_status_changed event.
        OrderService.update_order_inline(
            self.order, {"crm": {"sales_status_code": "closed"}}, self.admin
        )

        # DB thực có 3 journey event (assessment_created + sales_status_changed lần 1,
        # sales_status_changed lần 2) — KHÔNG bị sửa. Timeline chỉ DEDUPE lúc hiển thị:
        # assessment_created trùng (order, "scheduled") với sales_status_changed lần 1 → bỏ.
        self.assertEqual(
            OrderJourneyEvent.objects.filter(order=self.order).count(), 3
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/sale/contacts/{self.customer.id}/activities")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("items", data)
        self.assertIn("total", data)
        self.assertEqual(data["total"], 2)

        status_change_items = [i for i in data["items"] if i["type"] == "status_change"]
        self.assertEqual(len(status_change_items), 2)
        latest = status_change_items[0]
        self.assertEqual(latest["to_code"], "closed")
        self.assertEqual(latest["from_code"], "scheduled")
        self.assertIsInstance(latest["from_label"], str)
        self.assertIsInstance(latest["to_label"], str)
        self.assertNotEqual(latest["to_label"], "")
        self.assertEqual(latest["order_id"], self.order.id)

    def test_get_merges_activities_with_journey_events_desc(self):
        OrderService.update_order_inline(
            self.order, {"crm": {"sales_status_code": "scheduled"}}, self.admin
        )
        ContactActivity.objects.create(
            customer=self.customer, staff=self.admin, kind="note", note="Gọi thử"
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/sale/contacts/{self.customer.id}/activities")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # First save fires 2 journey events (assessment_created + sales_status_changed
        # cùng to_code) → timeline DEDUPE còn 1 status_change + 1 note = 2.
        self.assertEqual(data["total"], 2)
        types = [i["type"] for i in data["items"]]
        self.assertIn("status_change", types)
        self.assertIn("note", types)
        # Newest first.
        ats = [i["at"] for i in data["items"]]
        self.assertEqual(ats, sorted(ats, reverse=True))

    def test_first_result_click_not_duplicated(self):
        """Chốt Kết quả LẦN ĐẦU: _upsert_order_assessment ghi cả assessment_created
        và sales_status_changed cùng (order, to_code) → timeline phải chỉ hiện 1 dòng
        status_change (không phải 2 dòng '— → Đã chốt' trùng nhau)."""
        OrderService.update_order_inline(
            self.order, {"crm": {"sales_status_code": "closed"}}, self.admin
        )
        # DB có đủ 2 event (không đụng dữ liệu đơn), timeline dedupe còn 1.
        self.assertEqual(
            OrderJourneyEvent.objects.filter(
                order=self.order,
                event_type__in=["assessment_created", "sales_status_changed"],
            ).count(),
            2,
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/sale/contacts/{self.customer.id}/activities")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        status_change_items = [i for i in data["items"] if i["type"] == "status_change"]
        self.assertEqual(len(status_change_items), 1)
        self.assertEqual(data["total"], 1)
        self.assertEqual(status_change_items[0]["to_code"], "closed")

    def test_timeline_tiebreaker_is_deterministic_for_equal_timestamps(self):
        """2 item cùng `at` tuyệt đối (race trong cùng transaction/migration dồn dữ
        liệu) vẫn phải merge ra thứ tự ổn định giữa các lần gọi — tránh skip/dupe
        tại biên trang khi phân trang qua nhiều request."""
        from django.utils import timezone

        from apps.orders.application.services.contact_activity_service import ContactActivityService

        same_at = timezone.now()
        note_activity = ContactActivity.objects.create(
            customer=self.customer, staff=self.admin, kind="note", note="Ghi chu A"
        )
        call_activity = ContactActivity.objects.create(
            customer=self.customer, staff=self.admin, kind="call", note="Ghi chu B"
        )
        ContactActivity.objects.filter(id__in=[note_activity.id, call_activity.id]).update(
            created_at=same_at
        )

        result_1 = ContactActivityService.list_activities(self.customer, limit=10, offset=0)
        result_2 = ContactActivityService.list_activities(self.customer, limit=10, offset=0)

        notes_1 = [i["note"] for i in result_1["items"]]
        notes_2 = [i["note"] for i in result_2["items"]]
        self.assertEqual(notes_1, notes_2)

    def test_get_excludes_journey_events_of_marketplace_orders(self):
        # Landing-order journey event (hand-inserted: edge case per brief).
        OrderJourneyEvent.objects.create(
            order=self.order, event_type="sales_status_changed",
            from_code="scheduled", to_code="closed",
        )
        # Same customer's shopee order + its journey event must NOT leak into
        # the timeline nor inflate total.
        shopee_order = Order.objects.create(
            channel=self.channel, customer=self.customer, order_source="shopee"
        )
        OrderJourneyEvent.objects.create(
            order=shopee_order, event_type="sales_status_changed",
            from_code="scheduled", to_code="closed",
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/sale/contacts/{self.customer.id}/activities")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["order_id"], self.order.id)

    # -- POST -------------------------------------------------------------
    def test_post_note_creates_activity_visible_in_get(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/sale/contacts/{self.customer.id}/activities",
            {"kind": "note", "note": "Khách hẹn gọi lại chiều mai"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        item = response.json()
        self.assertEqual(item["type"], "note")
        self.assertEqual(item["note"], "Khách hẹn gọi lại chiều mai")

        get_response = self.client.get(f"/api/sale/contacts/{self.customer.id}/activities")
        self.assertEqual(get_response.json()["total"], 1)

    def test_post_call_with_meta(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/sale/contacts/{self.customer.id}/activities",
            {
                "kind": "call",
                "call_meta": {"direction": "outbound", "answered": True, "duration_s": 42},
                "order_id": self.order.id,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        item = response.json()
        self.assertEqual(item["type"], "call")
        self.assertEqual(item["call_meta"]["duration_s"], 42)
        self.assertEqual(item["order_id"], self.order.id)

    def test_post_invalid_kind_rejected(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/sale/contacts/{self.customer.id}/activities",
            {"kind": "status_change", "note": "x"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_post_note_kind_requires_nonempty_note(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/sale/contacts/{self.customer.id}/activities",
            {"kind": "note", "note": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_post_note_too_long_rejected(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/sale/contacts/{self.customer.id}/activities",
            {"kind": "note", "note": "x" * 4001},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_post_order_id_of_other_customer_rejected(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            f"/api/sale/contacts/{self.customer.id}/activities",
            {"kind": "note", "note": "x", "order_id": self.other_order.id},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    # -- RBAC ---------------------------------------------------------------
    def test_forbidden_without_permission(self):
        no_access_user = make_user_with_sections("no-access-contact-act", [])
        self.client.force_authenticate(user=no_access_user)
        response = self.client.get(f"/api/sale/contacts/{self.customer.id}/activities")
        self.assertEqual(response.status_code, 403)

    def test_own_scope_user_not_owning_customer_gets_404(self):
        own_user = _own_scope_user("sale-own-not-owner")
        self.client.force_authenticate(user=own_user)
        response = self.client.get(f"/api/sale/contacts/{self.customer.id}/activities")
        self.assertEqual(response.status_code, 404)

        post_response = self.client.post(
            f"/api/sale/contacts/{self.customer.id}/activities",
            {"kind": "note", "note": "x"},
            format="json",
        )
        self.assertEqual(post_response.status_code, 404)

    def test_own_scope_user_owning_customer_can_access(self):
        own_user = _own_scope_user("sale-own-owner")
        self.order.staff = own_user
        self.order.save(update_fields=["staff"])

        self.client.force_authenticate(user=own_user)
        response = self.client.get(f"/api/sale/contacts/{self.customer.id}/activities")
        self.assertEqual(response.status_code, 200)


# ══════════════════════════════════════════════════════════════════════════════
# Scope CHANNEL/DEPARTMENT cho trang Sale contacts (audit 2026-07-15b).
#
# Trước fix, service chỉ phân biệt OWN vs không-OWN: user được cấp contacts:view scope
# CHANNEL thấy toàn bộ danh bạ + đọc/ghi timeline của mọi khách; user CHANNEL chưa gán
# kênh nào (đáng lẽ 0) lại thấy tất cả. Kèm test clamp cửa sổ ngày server-side.
# (Gộp từ test_contact_scope.py.)
# ══════════════════════════════════════════════════════════════════════════════


def _customer_scope(name, phone):
    return Customer.objects.create(full_name=name, phone_raw=phone, phone_normalized=phone)


class ContactChannelScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel_a = Channel.objects.create(code="ct-scope-a", name="Kênh A", is_active=True)
        cls.channel_b = Channel.objects.create(code="ct-scope-b", name="Kênh B", is_active=True)
        cls.customer_a = _customer_scope("Khach Kenh A", "0931111111")
        cls.customer_b = _customer_scope("Khach Kenh B", "0932222222")
        cls.order_a = Order.objects.create(
            channel=cls.channel_a, customer=cls.customer_a, order_source="landing"
        )
        cls.order_b = Order.objects.create(
            channel=cls.channel_b, customer=cls.customer_b, order_source="landing"
        )

    def _channel_user(self, username, channels):
        user = make_user_with_sections(username, ["contacts"], scope=DataScope.CHANNEL)
        for channel in channels:
            UserChannel.objects.create(user=user, channel=channel, is_active=True)
        return user

    def _list(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.get("/api/sale/contacts")
        assert resp.status_code == 200, resp.content
        return resp.json()

    def test_channel_scope_sees_only_assigned_channel(self):
        user = self._channel_user("ct-chan-a", [self.channel_a])
        data = self._list(user)
        names = {item["full_name"] for item in data["items"]}
        self.assertEqual(names, {"Khach Kenh A"})

    def test_channel_scope_without_channels_sees_nothing(self):
        user = self._channel_user("ct-chan-none", [])
        data = self._list(user)
        self.assertEqual(data["items"], [])
        self.assertEqual(data["total"], 0)

    def test_channel_scope_activities_of_other_channel_404(self):
        user = self._channel_user("ct-chan-act", [self.channel_a])
        client = APIClient()
        client.force_authenticate(user=user)

        outside = client.get(f"/api/sale/contacts/{self.customer_b.id}/activities")
        self.assertEqual(outside.status_code, 404)
        outside_post = client.post(
            f"/api/sale/contacts/{self.customer_b.id}/activities",
            {"kind": "note", "note": "lén ghi"},
            format="json",
        )
        self.assertEqual(outside_post.status_code, 404)

        inside = client.get(f"/api/sale/contacts/{self.customer_a.id}/activities")
        self.assertEqual(inside.status_code, 200)

    def test_default_window_excludes_ancient_orders(self):
        """Không truyền ngày → server tự chặn cửa sổ; đơn quá cũ không được materialize."""
        ancient_customer = _customer_scope("Khach Co Dai", "0933333333")
        ancient = Order.objects.create(
            channel=self.channel_a, customer=ancient_customer, order_source="landing"
        )
        Order.objects.filter(pk=ancient.pk).update(
            created_at=timezone.now() - timedelta(days=400)
        )

        data = self._list(make_admin("ct-admin-window"))
        names = {item["full_name"] for item in data["items"]}
        self.assertNotIn("Khach Co Dai", names)

    def test_call_meta_oversize_rejected(self):
        client = APIClient()
        client.force_authenticate(user=make_admin("ct-admin-meta"))
        resp = client.post(
            f"/api/sale/contacts/{self.customer_a.id}/activities",
            {"kind": "call", "call_meta": {"blob": "x" * 5000}},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        resp2 = client.post(
            f"/api/sale/contacts/{self.customer_a.id}/activities",
            {"kind": "call", "call_meta": ["not", "a", "dict"]},
            format="json",
        )
        self.assertEqual(resp2.status_code, 400)


# ══════════════════════════════════════════════════════════════════════════════
# Pure pytest — rule table derive_action_needed (không cần DB).
# (Gộp từ test_contact_rules.py.)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "code,status,is_new,expected",
    [
        # Đơn hủy → không tác nghiệp, bất kể trạng thái sale
        ("no_answer_1", 6, True, "none"),
        # Đơn giao thành công → chăm sóc sau bán, bất kể trạng thái sale
        (None, 3, True, "post_sale"),
        ("closed", 3, False, "post_sale"),
        # Chưa có trạng thái sale
        (None, 0, True, "new_customer"),
        ("", 0, True, "new_customer"),
        (None, 0, False, "new_lead"),
        # Chuỗi gọi
        ("no_answer_1", 0, True, "call_2"),
        ("no_answer_2", 1, False, "call_3"),
        ("no_answer_3", 0, True, "call_4"),
        ("no_answer_4", 0, True, "call_5"),
        ("no_answer_5", 1, False, "call_6"),
        # Chỉ mốc gọi CUỐI CÙNG (lần 6) mới chuyển sang check lại số.
        ("no_answer_6", 0, True, "recheck_phone"),
        ("unreachable", 0, True, "recheck_phone"),
        ("wrong_number", 0, False, "recheck_phone"),
        # Chăm sóc
        ("need_more_consulting", 0, False, "nurture"),
        ("scheduled", 0, True, "follow_up"),
        ("call_back_later", 1, False, "follow_up"),
        ("closed_scheduled_date", 0, False, "follow_up"),
        ("wants_store_visit", 0, False, "follow_up"),
        # Đã chốt, chờ giao
        ("closed", 2, False, "awaiting_delivery"),
        ("closed_express", 0, True, "awaiting_delivery"),
        ("created_order", 1, False, "awaiting_delivery"),
        # Trạng thái kết thúc / rác
        ("not_buying", 0, False, "none"),
        ("underlying_condition", 0, False, "none"),
        ("spam", 0, True, "none"),
        ("duplicate", 0, False, "none"),
        ("customer_bombed", 0, False, "none"),
        ("cancelled", 0, False, "none"),
        ("warranty", 0, False, "none"),
        # Code lạ (catalog động có thể thêm) → none, không crash
        ("some_future_code", 0, True, "none"),
    ],
)
def test_derive_action_needed(code, status, is_new, expected):
    assert derive_action_needed(code, status, is_new) == expected


def test_action_keys_stable():
    assert "new_customer" in ACTION_KEYS and "none" in ACTION_KEYS
