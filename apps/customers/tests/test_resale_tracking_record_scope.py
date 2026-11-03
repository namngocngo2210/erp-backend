"""Record-scope cho 3 endpoint GHI của trang Theo dõi tái mua.

    PATCH /api/customers/resale/<order_id>/contact
    PATCH /api/customers/resale/<order_id>/appointment
    POST  /api/customers/resale/<order_id>/order

Trước fix, cả ba chỉ đi qua `_resale_channel_scope_error` → `OrderService.can_edit_channel`
→ `UserService.can_edit_order`, mà hàm cuối trả `True` VÔ ĐIỀU KIỆN cho scope OWN/ALL
(gốc rễ audit 2026-07-26 N1). Hệ quả: một sale scope OWN chỉ cần đổi `order_id` trên URL
là tick liên hệ / đặt lịch hẹn / ghi đơn tái mua LÊN ĐƠN CỦA SALE KHÁC — vừa sửa dữ liệu
của người khác, vừa cướp công chăm sóc khách.

`test_resale_channel_scope_api.py` (fix H2) đã ghi nhận đúng lỗ hổng này trong docstring
("no user is actually blocked") nhưng chỉ khoá được phần *wiring* bằng cách patch
`can_edit_channel`. File này khoá phần còn thiếu: scope THẬT theo bản ghi, không patch gì.

Mọi test assert vào TRẠNG THÁI DB (`CustomerResaleFollowUp`) chứ không chỉ status code —
endpoint đã có sẵn nhánh 403/404 nên assert riêng status code là tautology.
"""

from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer, CustomerResaleFollowUp
from apps.orders.models import Order
from apps.users.models import DataScope, Permission, Role, RolePermission, User, UserRoleAssignment
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import sync_permissions

# status=3 (giao thành công) + invoice_status=0 là điều kiện để đơn nằm trên trang tái
# mua (`CustomerResaleService._get_resale_tracking_order`); không thoả sẽ ra 404 vì lý do
# khác, làm test mất ý nghĩa.
DELIVERED = 3


def _make_user(username: str, *sections: str, scope=DataScope.ALL) -> User:
    """User có role cấp toàn bộ permission của các section, scope_default=scope.

    KHÔNG đặt tên role "Administrator" — migration users/0015 đã seed sẵn tên đó.
    """
    user = User.objects.create_user(username=username, password="x")
    role = Role.objects.create(name=f"role-{username}", scope_default=scope)
    RolePermission.objects.bulk_create(
        RolePermission(role=role, permission=p)
        for p in Permission.objects.filter(section__in=sections)
    )
    UserRoleAssignment.objects.create(user=user, role=role)
    bump_rbac_cache_version()
    return user


class ResaleTrackingRecordScopeTests(TestCase):
    def setUp(self):
        # KHÔNG cache.clear() — RedisCache.clear() FLUSHDB cả redis dùng chung.
        sync_permissions()
        self.client = APIClient()
        self.channel = Channel.objects.create(code="resale-scope", name="Resale Scope")
        self.customer = Customer.objects.create(
            full_name="Khach Resale Scope",
            phone_raw="0912345678",
            phone_normalized="0912345678",
        )
        self.owner = _make_user("resale-owner", "resale-tracking", scope=DataScope.OWN)
        self.teammate = _make_user("resale-teammate", "resale-tracking", scope=DataScope.OWN)
        self.boss = _make_user("resale-boss", "resale-tracking", scope=DataScope.ALL)

        self.own_order = self._order(self.owner)
        self.other_order = self._order(self.teammate)
        # Bản ghi "của người khác" có sẵn giá trị đã biết → assert được là KHÔNG đổi,
        # thay vì chỉ assert "không tồn tại" (yếu hơn: một fix nửa vời vẫn qua).
        self.other_followup = CustomerResaleFollowUp.objects.create(
            order=self.other_order,
            appointment_note="ghi chu goc",
            contact_logs=[],
            resale_orders=[],
        )

    def _order(self, staff: User) -> Order:
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            staff=staff,
            name="Don Resale",
            phone="0912345678",
            status=DELIVERED,
            invoice_status=0,
        )

    def _contact(self, user: User, order: Order):
        self.client.force_authenticate(user=user)
        return self.client.patch(
            f"/api/customers/resale/{order.id}/contact",
            {"contact_no": 1, "checked": True, "result": "da goi"},
            format="json",
        )

    def _appointment(self, user: User, order: Order):
        self.client.force_authenticate(user=user)
        return self.client.patch(
            f"/api/customers/resale/{order.id}/appointment",
            {"appointment_at": "2026-12-31T09:00:00Z", "note": "ghi chu moi"},
            format="json",
        )

    def _order_note(self, user: User, order: Order):
        self.client.force_authenticate(user=user)
        return self.client.post(
            f"/api/customers/resale/{order.id}/order",
            {"product": "Bon ngam chan", "quantity": 1},
            format="json",
        )

    def _assert_other_followup_untouched(self):
        followup = CustomerResaleFollowUp.objects.get(pk=self.other_followup.pk)
        self.assertIsNone(followup.contact_1_at)
        self.assertEqual(followup.contact_logs, [])
        self.assertIsNone(followup.appointment_at)
        self.assertEqual(followup.appointment_note, "ghi chu goc")
        self.assertEqual(followup.resale_orders, [])
        self.assertIsNone(followup.updated_by_id)

    # ── chiều CHẶN ──────────────────────────────────────────────────────────
    def test_own_scope_cannot_tick_contact_of_other_staff_order(self):
        resp = self._contact(self.owner, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self._assert_other_followup_untouched()

    def test_own_scope_cannot_set_appointment_on_other_staff_order(self):
        resp = self._appointment(self.owner, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self._assert_other_followup_untouched()

    def test_own_scope_cannot_add_resale_order_note_on_other_staff_order(self):
        resp = self._order_note(self.owner, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self._assert_other_followup_untouched()

    def test_nonexistent_order_is_404_not_403(self):
        """Không đảo 404 thành 403: id không tồn tại vẫn phải là 404 như trước."""
        self.client.force_authenticate(user=self.owner)
        resp = self.client.patch(
            "/api/customers/resale/99999999/contact",
            {"contact_no": 1, "checked": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    # ── chiều CHO PHÉP ──────────────────────────────────────────────────────
    def test_own_scope_ticks_contact_on_own_order(self):
        resp = self._contact(self.owner, self.own_order)
        self.assertEqual(resp.status_code, 200, resp.content)
        followup = CustomerResaleFollowUp.objects.get(order=self.own_order)
        self.assertIsNotNone(followup.contact_1_at)
        self.assertEqual(len(followup.contact_logs), 1)
        self.assertEqual(followup.updated_by_id, self.owner.id)

    def test_own_scope_sets_appointment_on_own_order(self):
        resp = self._appointment(self.owner, self.own_order)
        self.assertEqual(resp.status_code, 200, resp.content)
        followup = CustomerResaleFollowUp.objects.get(order=self.own_order)
        self.assertIsNotNone(followup.appointment_at)
        self.assertEqual(followup.appointment_note, "ghi chu moi")

    def test_own_scope_adds_resale_order_note_on_own_order(self):
        resp = self._order_note(self.owner, self.own_order)
        self.assertEqual(resp.status_code, 201, resp.content)
        followup = CustomerResaleFollowUp.objects.get(order=self.own_order)
        self.assertEqual(len(followup.resale_orders), 1)

    def test_all_scope_can_update_any_order(self):
        """Trưởng nhóm scope ALL vẫn chăm được đơn của mọi sale — không chặn oan."""
        resp = self._contact(self.boss, self.other_order)
        self.assertEqual(resp.status_code, 200, resp.content)
        followup = CustomerResaleFollowUp.objects.get(pk=self.other_followup.pk)
        self.assertIsNotNone(followup.contact_1_at)
