"""Record-scope cho các endpoint contact trên đơn: close-case + contact-log + journey-events.

Gộp 2026-08-05 từ test_close_case_record_scope.py + test_contact_log_record_scope.py
(xem docs/test-restructure-2026-08-05.md) — cùng nhắm vào contact_log_view.py /
close_case, cùng audit item 2026-07-26 N1. Docstring gốc của từng file nằm ở
docstring lớp tương ứng bên dưới.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment, OrderJourneyEvent, OrderStatus
from apps.orders.sheet_rules import CASE_CLOSED_EVENT, SALE_CONTACT_EVENT
from apps.orders.tests.helpers import make_scoped_user
from apps.users.models import (
    DataScope,
    Department,
    Permission,
    Role,
    RolePermission,
    UserDepartment,
    UserRoleAssignment,
)
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import sync_permissions

PHONE_CLOSE_CASE = "0909444333"
PHONE_CONTACT_LOG = "0909222111"
# Nội dung PII phải KHÔNG xuất hiện trong response của user ngoài scope.
SECRET_TRANSCRIPT = "Khach hen giao thu 5 tai 12 Nguyen Trai"


class CloseCaseRecordScopeTests(TestCase):
    """Record-scope cho `close_case` (POST /api/orders/<id>/close-case).

    Trước fix, endpoint lấy đơn bằng `get_object_or_404` rồi CHỈ gate
    `OrderService.can_edit_channel`. Nhưng `UserService.can_edit_order` trả `True` VÔ
    ĐIỀU KIỆN cho scope OWN, nên một sale scope OWN ĐÓNG (archive) được đơn của bất kỳ
    ai chỉ bằng cách đổi `order_id` trong URL — cùng lỗ hổng đã bịt ở
    `update_order_inline` / `duplicate_order` / `list_order_history` (audit 2026-07-26 N1).

    Mọi test assert vào TRẠNG THÁI DB (`OrderAssessment.sales_status_code` +
    `OrderJourneyEvent`), không chỉ status code: endpoint đã có sẵn nhánh 403 từ
    `can_edit_channel` nên assert riêng status code là tautology.
    """

    @classmethod
    def setUpTestData(cls):
        # Dữ liệu bất biến dựng MỘT lần cho cả class (Django rollback từng test về
        # savepoint của class). KHÔNG cache.clear() — FLUSHDB cả redis dùng chung.
        sync_permissions()
        cls.ch_a = Channel.objects.create(code="close-a", name="Close A")
        cls.ch_b = Channel.objects.create(code="close-b", name="Close B")
        cls.customer = Customer.objects.create(
            full_name="Khach Close",
            phone_raw=PHONE_CLOSE_CASE,
            phone_normalized=PHONE_CLOSE_CASE,
        )

        cls.owner = make_scoped_user("close-owner", "orders", scope=DataScope.OWN)
        cls.teammate = make_scoped_user("close-teammate", "orders", scope=DataScope.OWN)
        cls.boss = make_scoped_user("close-boss", "orders", scope=DataScope.ALL)

        cls.own_order = cls._order(cls.ch_a, cls.owner)
        cls.other_order = cls._order(cls.ch_a, cls.teammate)
        cls.koc_order = cls._order(cls.ch_a, cls.teammate, order_source="koc")

    def setUp(self):
        self.client = APIClient()

    @classmethod
    def _order(cls, channel, staff, **extra) -> Order:
        defaults = {
            "channel": channel,
            "customer": cls.customer,
            "staff": staff,
            "name": "Don Close",
            "phone": PHONE_CLOSE_CASE,
            "total_amount": "100000.00",
            "status": OrderStatus.NEW,
        }
        defaults.update(extra)
        return Order.objects.create(**defaults)

    def _close(self, user, order: Order, payload: dict | None = None):
        self.client.force_authenticate(user=user)
        return self.client.post(
            f"/api/orders/{order.id}/close-case", payload or {}, format="json"
        )

    def _assert_not_closed(self, order: Order):
        """Không có assessment 'archived' và không có journey event nào cho đơn này."""
        assessment = OrderAssessment.objects.filter(order=order).first()
        if assessment is not None:
            self.assertNotEqual(assessment.sales_status_code, "archived")
        self.assertFalse(
            OrderJourneyEvent.objects.filter(
                order=order, event_type=CASE_CLOSED_EVENT
            ).exists()
        )

    def _assert_closed(self, order: Order):
        assessment = OrderAssessment.objects.get(order=order)
        self.assertEqual(assessment.sales_status_code, "archived")
        self.assertTrue(
            OrderJourneyEvent.objects.filter(
                order=order, event_type=CASE_CLOSED_EVENT
            ).exists()
        )

    # ── chiều CHẶN ──────────────────────────────────────────────────────────
    def test_own_scope_cannot_close_order_of_other_staff(self):
        resp = self._close(self.owner, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self._assert_not_closed(self.other_order)

    def test_own_scope_cannot_close_koc_order_of_other_staff(self):
        resp = self._close(self.owner, self.koc_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self._assert_not_closed(self.koc_order)

    def test_nonexistent_order_is_404_not_403(self):
        """Không đảo 404 thành 403 ở cả 3 endpoint record-scope của file này
        (close-case / contact-log / journey-events): id không tồn tại vẫn là 404."""
        self.client.force_authenticate(user=self.owner)
        for method, url in (
            ("post", "/api/orders/99999999/close-case"),
            ("post", "/api/orders/99999999/contact-log"),
            ("get", "/api/orders/99999999/journey-events"),
        ):
            with self.subTest(method=method, url=url):
                kwargs = {"format": "json"} if method == "post" else {}
                resp = getattr(self.client, method)(url, **({"data": {}} | kwargs))
                self.assertEqual(resp.status_code, 404, resp.content)

    def test_channel_scope_cannot_close_order_of_other_channel(self):
        order_b = self._order(self.ch_b, self.teammate)
        user = make_scoped_user("close-channel-a", "orders", scope=DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.ch_a, is_active=True)
        bump_rbac_cache_version()
        resp = self._close(user, order_b)
        self.assertEqual(resp.status_code, 403, resp.content)
        self._assert_not_closed(order_b)

    def test_channel_scope_without_any_channel_closes_nothing(self):
        """Nhánh rò rỉ dễ bỏ sót: scope CHANNEL nhưng chưa được gán kênh nào.

        Nếu quên nhánh "set rỗng → không khớp gì" thì Q sinh ra là rỗng → đóng được
        TOÀN BỘ đơn công ty.
        """
        user = make_scoped_user("close-channel-none", "orders", scope=DataScope.CHANNEL)
        for order in (self.own_order, self.other_order, self.koc_order):
            resp = self._close(user, order)
            self.assertEqual(resp.status_code, 403, resp.content)
            self._assert_not_closed(order)

    def test_koc_only_scope_all_cannot_close_non_koc_order(self):
        """Leo quyền ngang: scope all của `koc-orders` không được áp lên đơn thường."""
        user = make_scoped_user("close-koc-all", "koc-orders", scope=DataScope.ALL)
        resp = self._close(user, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self._assert_not_closed(self.other_order)

    # ── chiều CHO PHÉP ──────────────────────────────────────────────────────
    def test_own_scope_closes_own_order(self):
        resp = self._close(self.owner, self.own_order)
        self.assertEqual(resp.status_code, 200, resp.content)
        self._assert_closed(self.own_order)

    def test_all_scope_closes_any_order(self):
        resp = self._close(self.boss, self.other_order)
        self.assertEqual(resp.status_code, 200, resp.content)
        self._assert_closed(self.other_order)

    def test_channel_scope_closes_order_of_assigned_channel(self):
        user = make_scoped_user("close-channel-ok", "orders", scope=DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.ch_a, is_active=True)
        bump_rbac_cache_version()
        resp = self._close(user, self.other_order)
        self.assertEqual(resp.status_code, 200, resp.content)
        self._assert_closed(self.other_order)

    def test_department_scope_closes_order_of_department_channel(self):
        """scope DEPARTMENT mở kênh qua phòng ban (không có UserChannel) — không chặn oan."""
        dept = Department.objects.create(name="Phong Close Case")
        dept.channels.add(self.ch_a)
        user = make_scoped_user("close-dept", "orders", scope=DataScope.DEPARTMENT)
        UserDepartment.objects.create(user=user, department=dept)
        bump_rbac_cache_version()
        self.assertFalse(UserChannel.objects.filter(user=user).exists())
        resp = self._close(user, self.other_order)
        self.assertEqual(resp.status_code, 200, resp.content)
        self._assert_closed(self.other_order)

    def test_koc_only_scope_all_closes_koc_order(self):
        user = make_scoped_user("close-koc-ok", "koc-orders", scope=DataScope.ALL)
        resp = self._close(user, self.koc_order)
        self.assertEqual(resp.status_code, 200, resp.content)
        self._assert_closed(self.koc_order)


class _ContactLogScopeBase(TestCase):
    """Record-scope cho `create_contact_log` + `get_order_journey_events`.

    Hai lỗ hổng cùng nằm ở `contact_log_view.py`, cùng gốc với `close_case` (audit
    2026-07-26 N1) nhưng bị sót ở vòng vá trước:

    - `POST /api/orders/<id>/contact-log` (GHI): chỉ `get_object_or_404` +
      `OrderService.can_edit_channel`. Mà `UserService.can_edit_order` trả `can_edit=True`
      VÔ ĐIỀU KIỆN khi `scope_for("orders")` là ALL hoặc OWN, nên một sale scope OWN
      KHÔNG có UserChannel nào vẫn ghi được `OrderJourneyEvent` giả mạo và ĐỔI
      `OrderAssessment.sales_status_code` của đơn bất kỳ chỉ bằng cách đổi `order_id`.
    - `GET /api/orders/<id>/journey-events` (ĐỌC): gate bằng
      `OrderService.order_channel_codes_scope`, hàm này trả `None` cho scope OWN (channel
      code không biểu diễn được "của tôi") → điều kiện `scope is not None and ...` luôn
      False → KHÔNG check gì. Body trả `note` + `payload_json` chứa transcript cuộc gọi.

    Mọi test assert vào TRẠNG THÁI DB / NỘI DUNG trả về, không chỉ status code: cả hai
    endpoint đều có sẵn nhánh 403 khác (can_edit_channel) nên assert riêng status code là
    tautology — xem `test_order_record_scope.OrderRecordScopeQUnitTests` để biết chỗ đo
    trực tiếp record-scope Q.
    """

    @classmethod
    def setUpTestData(cls):
        # Dữ liệu bất biến dựng MỘT lần cho cả class (Django rollback từng test về
        # savepoint của class). KHÔNG cache.clear() — FLUSHDB cả redis dùng chung.
        sync_permissions()
        cls.ch_a = Channel.objects.create(code="clog-a", name="ContactLog A")
        cls.ch_b = Channel.objects.create(code="clog-b", name="ContactLog B")
        cls.customer = Customer.objects.create(
            full_name="Khach ContactLog",
            phone_raw=PHONE_CONTACT_LOG,
            phone_normalized=PHONE_CONTACT_LOG,
        )

        cls.owner = make_scoped_user("clog-owner", "orders", scope=DataScope.OWN)
        cls.teammate = make_scoped_user("clog-teammate", "orders", scope=DataScope.OWN)
        cls.boss = make_scoped_user("clog-boss", "orders", scope=DataScope.ALL)

        cls.own_order = cls._order(cls.ch_a, cls.owner)
        cls.other_order = cls._order(cls.ch_a, cls.teammate)
        cls.koc_order = cls._order(cls.ch_a, cls.teammate, order_source="koc")

    def setUp(self):
        self.client = APIClient()

    @classmethod
    def _order(cls, channel, staff, **extra) -> Order:
        defaults = {
            "channel": channel,
            "customer": cls.customer,
            "staff": staff,
            "name": "Don ContactLog",
            "phone": PHONE_CONTACT_LOG,
            "total_amount": "100000.00",
            "status": OrderStatus.NEW,
        }
        defaults.update(extra)
        return Order.objects.create(**defaults)


class CreateContactLogRecordScopeTests(_ContactLogScopeBase):
    """C1 — write IDOR: ghi journey event + đổi sales_status của đơn người khác."""

    def _post(self, user, order: Order, payload: dict | None = None):
        self.client.force_authenticate(user=user)
        return self.client.post(
            f"/api/orders/{order.id}/contact-log",
            payload if payload is not None else {"outcome": "pwned", "sales_status": "archived"},
            format="json",
        )

    def _assert_untouched(self, order: Order):
        """Không có journey event mới và sales_status KHÔNG bị đổi."""
        self.assertFalse(
            OrderJourneyEvent.objects.filter(order=order).exists(),
            f"đơn {order.id} bị chèn journey event giả mạo",
        )
        assessment = OrderAssessment.objects.filter(order=order).first()
        if assessment is not None:
            self.assertNotEqual(assessment.sales_status_code, "archived")

    # ── chiều CHẶN ──────────────────────────────────────────────────────────
    def test_own_scope_cannot_write_contact_log_of_other_staff(self):
        resp = self._post(self.owner, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self._assert_untouched(self.other_order)

    def test_own_scope_cannot_write_contact_log_of_koc_order(self):
        resp = self._post(self.owner, self.koc_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self._assert_untouched(self.koc_order)

    def test_channel_scope_cannot_write_contact_log_of_other_channel(self):
        order_b = self._order(self.ch_b, self.teammate)
        user = make_scoped_user("clog-channel-a", "orders", scope=DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.ch_a, is_active=True)
        bump_rbac_cache_version()
        resp = self._post(user, order_b)
        self.assertEqual(resp.status_code, 403, resp.content)
        self._assert_untouched(order_b)

    def test_koc_only_scope_all_cannot_write_contact_log_of_normal_order(self):
        """Leo quyền ngang: scope all của `koc-orders` không áp lên đơn thường."""
        user = make_scoped_user("clog-koc-all", "koc-orders", scope=DataScope.ALL)
        resp = self._post(user, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self._assert_untouched(self.other_order)

    # ── chiều CHO PHÉP ──────────────────────────────────────────────────────
    def test_own_scope_writes_contact_log_of_own_order(self):
        resp = self._post(
            self.owner, self.own_order, {"outcome": "goi thanh cong", "sales_status": "archived"}
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        event = OrderJourneyEvent.objects.get(order=self.own_order)
        self.assertEqual(event.event_type, SALE_CONTACT_EVENT)
        self.assertEqual(
            OrderAssessment.objects.get(order=self.own_order).sales_status_code, "archived"
        )

    def test_all_scope_writes_contact_log_of_any_order(self):
        resp = self._post(self.boss, self.other_order, {"outcome": "truong nhom goi lai"})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(OrderJourneyEvent.objects.filter(order=self.other_order).exists())

    def test_koc_only_scope_all_writes_contact_log_of_koc_order(self):
        user = make_scoped_user("clog-koc-ok", "koc-orders", scope=DataScope.ALL)
        resp = self._post(user, self.koc_order, {"outcome": "koc ok"})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(OrderJourneyEvent.objects.filter(order=self.koc_order).exists())


class JourneyEventsRecordScopeTests(_ContactLogScopeBase):
    """C2 — read IDOR: đọc transcript cuộc gọi (note/payload_json) của đơn người khác."""

    def setUp(self):
        super().setUp()
        for order in (self.own_order, self.other_order, self.koc_order):
            OrderJourneyEvent.objects.create(
                order=order,
                event_type=SALE_CONTACT_EVENT,
                actor=self.teammate,
                note=SECRET_TRANSCRIPT,
                payload_json={"outcome": "da chot", "transcript": SECRET_TRANSCRIPT},
            )

    def _get(self, user, order: Order):
        self.client.force_authenticate(user=user)
        return self.client.get(f"/api/orders/{order.id}/journey-events")

    # ── chiều CHẶN ──────────────────────────────────────────────────────────
    def test_own_scope_cannot_read_journey_events_of_other_staff(self):
        resp = self._get(self.owner, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        # PII (transcript) tuyệt đối không được xuất hiện trong body.
        self.assertNotIn(SECRET_TRANSCRIPT, resp.content.decode())

    def test_own_scope_cannot_read_journey_events_of_koc_order(self):
        resp = self._get(self.owner, self.koc_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertNotIn(SECRET_TRANSCRIPT, resp.content.decode())

    def test_channel_scope_cannot_read_journey_events_of_other_channel(self):
        order_b = self._order(self.ch_b, self.teammate)
        OrderJourneyEvent.objects.create(
            order=order_b,
            event_type=SALE_CONTACT_EVENT,
            actor=self.teammate,
            note=SECRET_TRANSCRIPT,
            payload_json={"transcript": SECRET_TRANSCRIPT},
        )
        user = make_scoped_user("clog-read-channel-a", "orders", scope=DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.ch_a, is_active=True)
        bump_rbac_cache_version()
        resp = self._get(user, order_b)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertNotIn(SECRET_TRANSCRIPT, resp.content.decode())

    def test_koc_only_scope_all_cannot_read_journey_events_of_normal_order(self):
        user = make_scoped_user("clog-read-koc-all", "koc-orders", scope=DataScope.ALL)
        resp = self._get(user, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertNotIn(SECRET_TRANSCRIPT, resp.content.decode())

    # ── chiều CHO PHÉP ──────────────────────────────────────────────────────
    def test_own_scope_reads_journey_events_of_own_order(self):
        resp = self._get(self.owner, self.own_order)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIn(SECRET_TRANSCRIPT, resp.content.decode())

    def test_all_scope_reads_journey_events_of_any_order(self):
        resp = self._get(self.boss, self.other_order)
        self.assertEqual(resp.status_code, 200, resp.content)
        # id=0 là dòng "Tạo đơn" suy ra cho đơn cũ — chỉ đếm event thật.
        self.assertEqual(len([r for r in resp.json()["results"] if r["id"]]), 1)

    def test_shipments_all_scope_reads_journey_events_of_any_order(self):
        """User kho (`shipments` scope ALL + `orders` scope OWN) vẫn đọc được timeline.

        Đây là lý do dùng `_INLINE_SCOPE_SECTIONS` (có "shipments") thay cho
        `ORDER_WRITE_SCOPE_SECTIONS`: gate đọc chi tiết đơn (`update_order_inline` GET)
        đã cho user này xem đơn, nên timeline phải mirror — nếu chỉ resolve theo
        ORDER_SECTIONS thì họ rơi về OWN và mất sạch timeline giữa luồng giao hàng.
        """
        user = make_scoped_user("clog-shipments", "orders", scope=DataScope.OWN)
        warehouse_role = Role.objects.create(
            name="role-clog-shipments-all", scope_default=DataScope.ALL
        )
        RolePermission.objects.bulk_create(
            RolePermission(role=warehouse_role, permission=p)
            for p in Permission.objects.filter(section="shipments")
        )
        UserRoleAssignment.objects.create(user=user, role=warehouse_role)
        bump_rbac_cache_version()
        resp = self._get(user, self.other_order)
        self.assertEqual(resp.status_code, 200, resp.content)
        # id=0 là dòng "Tạo đơn" suy ra cho đơn cũ — chỉ đếm event thật.
        self.assertEqual(len([r for r in resp.json()["results"] if r["id"]]), 1)
