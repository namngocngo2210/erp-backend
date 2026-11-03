"""Record-scope cho 3 endpoint ĐƠN LẺ + fix `can_edit_order` ở scope DEPARTMENT.

Trước fix:
- `update_order_inline` (GET/PATCH/DELETE `/api/orders/<id>`) và `duplicate_order`
  (`POST /api/orders/<id>/duplicate`) chỉ gate bằng `require_section` +
  `OrderService.can_edit_channel`. Nhưng `UserService.can_edit_order` trả `True` VÔ ĐIỀU
  KIỆN cho scope OWN, nên một sale scope OWN đọc/sửa/XOÁ được đơn của bất kỳ ai chỉ bằng
  cách đổi `order_id` trong URL.
- `list_order_history` (`GET /api/orders/history?phone=`) không scope gì: chỉ cần
  `orders:view` là đọc được toàn bộ lịch sử đơn (kèm PII + CRM) của SĐT bất kỳ.

Fix dùng `order_record_scope_q(user, sections)` — hợp Q theo TỪNG section user được cấp,
mỗi vế = (điều kiện nhận dạng đơn của section) AND (scope bản ghi ở section đó). Test
khoá cả hai chiều của đúng thiết kế này:
- `shipments`-only (tập cha) vẫn PATCH được trạng thái giao hàng của MỌI đơn — ca mà cách
  "chốt cứng section = orders" làm hỏng.
- `koc-orders` scope all KHÔNG chạm được đơn thường — ca mà cách "lấy section có scope
  rộng nhất" làm rò (leo quyền ngang).

Mọi test assert vào TRẠNG THÁI DB / NỘI DUNG trả về, không chỉ status code: các endpoint
này có nhánh 403 sẵn từ `can_edit_channel` nên assert riêng status code là tautology.
"""

from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderStatus
from apps.orders.tests.helpers import make_scoped_user
from apps.orders.transport.views.order_views_impl._view_helpers import (
    _SECTION_ORDER_IDENTITY_Q,
    INLINE_SCOPE_SECTIONS,
    ORDER_WRITE_SCOPE_SECTIONS,
    order_record_scope_q,
)
from apps.users.models import (
    DataScope,
    Department,
    User,
    UserDepartment,
)
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import sync_permissions
from apps.users.services import UserService
from apps.users.tests.helpers import grant_order_fields

HISTORY_URL = "/api/orders/history"
PHONE = "0909777888"

# Patch tại NƠI ĐỊNH NGHĨA để phủ cả `OrderService.push_event` (kế thừa) — nếu không,
# PATCH inline sẽ gọi channel layer thật (Redis) trong test.
PUSH_EVENT_PATH = (
    "apps.orders.application.services.order_service_commands.OrderServiceCommands.push_event"
)


class _RecordScopeBase(TestCase):
    """2 kênh + user theo từng loại scope + 3 đơn (thường của tôi / của người khác / KOC)."""

    @classmethod
    def setUpTestData(cls):
        # Dữ liệu bất biến (quyền, kênh, KH, user, 3 đơn) dựng MỘT lần cho cả class:
        # Django rollback từng test về savepoint của class nên đơn bị PATCH/DELETE
        # trong test không rò sang test khác. KHÔNG cache.clear() — FLUSHDB redis dùng chung.
        sync_permissions()
        cls.ch_a = Channel.objects.create(code="rec-a", name="Record A")
        cls.ch_b = Channel.objects.create(code="rec-b", name="Record B")
        cls.customer = Customer.objects.create(
            full_name="Khach Record",
            phone_raw=PHONE,
            phone_normalized=PHONE,
        )

        cls.owner = make_scoped_user("rec-owner", "orders", scope=DataScope.OWN)
        cls.teammate = make_scoped_user("rec-teammate", "orders", scope=DataScope.OWN)
        cls.boss = make_scoped_user("rec-boss", "orders", scope=DataScope.ALL)

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
            "name": "Don Record",
            "phone": PHONE,
            "total_amount": "100000.00",
            "status": OrderStatus.NEW,
        }
        defaults.update(extra)
        return Order.objects.create(**defaults)

    def _channel_scope_user(self, username: str, channel: Channel | None = None) -> User:
        """User scope CHANNEL; `channel=None` = bị giới hạn nhưng CHƯA được gán kênh nào.

        Nhánh `channel=None` là nhánh rò rỉ dễ bỏ sót nhất: nếu quên nhánh "set rỗng →
        không khớp gì" thì Q sinh ra là rỗng → tác động lên TOÀN BỘ đơn công ty.
        """
        user = make_scoped_user(username, "orders", scope=DataScope.CHANNEL)
        if channel is not None:
            UserChannel.objects.create(user=user, channel=channel, is_active=True)
        bump_rbac_cache_version()
        return user

    def _inline_url(self, order: Order) -> str:
        return f"/api/orders/{order.id}"

    def _patch(self, user, order: Order, payload: dict):
        self.client.force_authenticate(user=user)
        with patch(PUSH_EVENT_PATH):
            return self.client.patch(self._inline_url(order), payload, format="json")

    def _get(self, user, order: Order):
        self.client.force_authenticate(user=user)
        return self.client.get(self._inline_url(order))

    def _delete(self, user, order: Order):
        self.client.force_authenticate(user=user)
        with patch(PUSH_EVENT_PATH):
            return self.client.delete(self._inline_url(order))

    def _duplicate(self, user, order: Order):
        self.client.force_authenticate(user=user)
        with patch(PUSH_EVENT_PATH):
            return self.client.post(f"/api/orders/{order.id}/duplicate", {}, format="json")

    def _history(self, user, phone: str = PHONE):
        self.client.force_authenticate(user=user)
        return self.client.get(HISTORY_URL, {"phone": phone})


class UpdateOrderInlineRecordScopeTests(_RecordScopeBase):
    def test_own_scope_cannot_read_order_of_other_staff(self):
        resp = self._get(self.owner, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        # Không được rò PII: body 403 chỉ có `detail`, không có tên/SĐT khách.
        self.assertNotIn(PHONE, resp.content.decode())
        self.assertNotIn("Don Record", resp.content.decode())

    def test_own_scope_cannot_patch_order_of_other_staff(self):
        resp = self._patch(self.owner, self.other_order, {"status": OrderStatus.IN_DELIVERY})
        self.assertEqual(resp.status_code, 403, resp.content)
        self.other_order.refresh_from_db()
        self.assertEqual(self.other_order.status, OrderStatus.NEW)

    def test_own_scope_cannot_delete_order_of_other_staff(self):
        resp = self._delete(self.owner, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.other_order.refresh_from_db()
        self.assertIsNone(self.other_order.deleted_at)

    def test_nonexistent_order_is_404_not_403(self):
        """404 cho id không tồn tại, 403 cho id có thật nhưng ngoài scope (khuôn task 1)."""
        self.client.force_authenticate(user=self.owner)
        resp = self.client.get("/api/orders/99999999")
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_own_scope_patches_own_order(self):
        # Test đo RECORD-SCOPE (own/other), không đo quyền field — cấp đúng "status"
        # (field payload đổi thật) để guard field (Task 7) không chặn oan mục tiêu test.
        grant_order_fields(self.owner, "status")
        resp = self._patch(self.owner, self.own_order, {"status": OrderStatus.IN_DELIVERY})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.own_order.refresh_from_db()
        self.assertEqual(self.own_order.status, OrderStatus.IN_DELIVERY)

    def test_own_scope_deletes_own_order(self):
        resp = self._delete(self.owner, self.own_order)
        self.assertEqual(resp.status_code, 204, resp.content)
        self.own_order.refresh_from_db()
        self.assertIsNotNone(self.own_order.deleted_at)

    def test_all_scope_still_patches_any_order(self):
        grant_order_fields(self.boss, "status")
        resp = self._patch(self.boss, self.other_order, {"status": OrderStatus.IN_DELIVERY})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.other_order.refresh_from_db()
        self.assertEqual(self.other_order.status, OrderStatus.IN_DELIVERY)

    def test_all_scope_still_reads_any_order(self):
        resp = self._get(self.boss, self.other_order)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["id"], self.other_order.id)

    def test_channel_scope_patches_order_of_assigned_channel(self):
        user = self._channel_scope_user("rec-inline-channel", self.ch_a)
        grant_order_fields(user, "status")
        resp = self._patch(user, self.other_order, {"status": OrderStatus.IN_DELIVERY})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.other_order.refresh_from_db()
        self.assertEqual(self.other_order.status, OrderStatus.IN_DELIVERY)

    def test_channel_scope_cannot_patch_order_of_other_channel(self):
        order_b = self._order(self.ch_b, self.teammate)
        user = self._channel_scope_user("rec-inline-channel-b", self.ch_a)
        resp = self._patch(user, order_b, {"status": OrderStatus.IN_DELIVERY})
        self.assertEqual(resp.status_code, 403, resp.content)
        order_b.refresh_from_db()
        self.assertEqual(order_b.status, OrderStatus.NEW)

    def test_channel_scope_without_any_channel_patches_nothing(self):
        # LƯU Ý người sửa sau: test này (và 7 test "CHANNEL scope + 0 UserChannel" khác)
        # vẫn XANH kể cả khi xoá sạch logic record-scope, vì `can_edit_order` trả
        # can_edit=False cho scope CHANNEL không có kênh → 403 xảy ra TRƯỚC. Chỗ đo THẬT
        # nhánh đó là `OrderRecordScopeQUnitTests` cuối file (gọi thẳng
        # `order_record_scope_q`, không có gate backstop nào).
        user = self._channel_scope_user("rec-inline-nochannel")
        for order in (self.own_order, self.other_order, self.koc_order):
            resp = self._patch(user, order, {"status": OrderStatus.IN_DELIVERY})
            self.assertEqual(resp.status_code, 403, resp.content)
            order.refresh_from_db()
            self.assertEqual(order.status, OrderStatus.NEW, f"order {order.id} bị sửa oan")


class ShipmentsOnlyInlineTests(_RecordScopeBase):
    """User kho chỉ có section `shipments` — trang Vận đơn KHÔNG khoá đơn theo danh tính.

    Đây là ca mà thiết kế "chốt cứng section = orders" làm hỏng: `scope_for("orders")` của
    user kho mặc định là OWN nên họ sẽ mất quyền cập nhật trạng thái giao hàng của mọi đơn
    (họ không phụ trách đơn nào cả). Q-union theo section giữ đúng quyền này.
    """

    def setUp(self):
        super().setUp()
        self.warehouse_user = make_scoped_user("rec-shipments", "shipments", scope=DataScope.ALL)

    def test_shipments_only_user_patches_delivery_status_of_any_order(self):
        grant_order_fields(self.warehouse_user, "status")
        resp = self._patch(
            self.warehouse_user, self.other_order, {"status": OrderStatus.IN_DELIVERY}
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.other_order.refresh_from_db()
        self.assertEqual(self.other_order.status, OrderStatus.IN_DELIVERY)

    def test_shipments_only_user_still_cannot_delete_order(self):
        """Least-privilege cũ phải còn: `shipments`-only KHÔNG được xoá đơn."""
        resp = self._delete(self.warehouse_user, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.other_order.refresh_from_db()
        self.assertIsNone(self.other_order.deleted_at)


class KocOnlyInlineTests(_RecordScopeBase):
    """User chỉ có `koc-orders` scope ALL: được mọi đơn KOC, KHÔNG được đơn thường.

    Đây là ca mà thiết kế "lấy section có scope rộng nhất" làm rò: scope all của
    koc-orders bị áp lên tập cha → chạm được cả đơn thường (leo quyền ngang).
    """

    def setUp(self):
        super().setUp()
        self.koc_user = make_scoped_user("rec-koc", "koc-orders", scope=DataScope.ALL)

    def test_koc_scope_all_patches_koc_order_of_other_staff(self):
        grant_order_fields(self.koc_user, "status")
        resp = self._patch(self.koc_user, self.koc_order, {"status": OrderStatus.IN_DELIVERY})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.koc_order.refresh_from_db()
        self.assertEqual(self.koc_order.status, OrderStatus.IN_DELIVERY)

    def test_koc_scope_all_cannot_touch_non_koc_order(self):
        resp = self._patch(self.koc_user, self.other_order, {"status": OrderStatus.IN_DELIVERY})
        self.assertEqual(resp.status_code, 403, resp.content)
        self.other_order.refresh_from_db()
        self.assertEqual(self.other_order.status, OrderStatus.NEW)

    def test_koc_scope_all_cannot_delete_non_koc_order(self):
        resp = self._delete(self.koc_user, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.other_order.refresh_from_db()
        self.assertIsNone(self.other_order.deleted_at)

    def test_koc_scope_all_history_only_returns_koc_orders(self):
        resp = self._history(self.koc_user)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual([row["id"] for row in resp.json()], [self.koc_order.id])


class DuplicateOrderRecordScopeTests(_RecordScopeBase):
    def test_own_scope_cannot_duplicate_order_of_other_staff(self):
        before = Order.objects.count()
        resp = self._duplicate(self.owner, self.other_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertEqual(Order.objects.count(), before)

    def test_own_scope_duplicates_own_order(self):
        before = Order.objects.count()
        resp = self._duplicate(self.owner, self.own_order)
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(Order.objects.count(), before + 1)
        self.assertNotEqual(resp.json()["id"], self.own_order.id)

    def test_all_scope_still_duplicates_any_order(self):
        before = Order.objects.count()
        resp = self._duplicate(self.boss, self.other_order)
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(Order.objects.count(), before + 1)

    def test_channel_scope_without_any_channel_duplicates_nothing(self):
        user = self._channel_scope_user("rec-dup-nochannel")
        before = Order.objects.count()
        resp = self._duplicate(user, self.own_order)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertEqual(Order.objects.count(), before)

    def test_duplicate_copies_business_fields_but_not_vat_invoice_markers(self):
        """Endpoint dùng chung `duplicate_order` với luồng khôi phục: nhóm field
        nghiệp vụ (staff/branch_id/vat/...) phải theo đơn gốc, còn is_export_vat/
        vat_link (marker "đã xuất hóa đơn" của đơn nguồn) thì KHÔNG được copy."""
        from apps.orders.models import OrderVat

        source = self._order(
            self.ch_a,
            self.boss,
            branch_id="BR-END-01",
            vat=OrderVat.COMPANY,
            is_export_vat=True,
            vat_link="https://hoadon.example/endpoint",
        )
        resp = self._duplicate(self.boss, source)
        self.assertEqual(resp.status_code, 201, resp.content)

        body = resp.json()
        self.assertEqual(body["staff_id"], self.boss.id)
        self.assertEqual(body["branch_id"], "BR-END-01")
        self.assertEqual(body["vat"], OrderVat.COMPANY)
        self.assertFalse(body["is_export_vat"])
        self.assertEqual(body["vat_link"], "")


class ListOrderHistoryRecordScopeTests(_RecordScopeBase):
    def test_own_scope_history_hides_orders_of_other_staff(self):
        resp = self._history(self.owner)
        self.assertEqual(resp.status_code, 200, resp.content)
        returned = [row["id"] for row in resp.json()]
        self.assertEqual(returned, [self.own_order.id])
        # Trước fix, body chứa cả đơn của teammate → dò được lịch sử SĐT bất kỳ.
        self.assertNotIn(self.other_order.id, returned)
        self.assertNotIn(self.koc_order.id, returned)

    def test_all_scope_history_returns_every_order_of_phone(self):
        resp = self._history(self.boss)
        self.assertEqual(resp.status_code, 200, resp.content)
        returned = {row["id"] for row in resp.json()}
        self.assertEqual(
            returned, {self.own_order.id, self.other_order.id, self.koc_order.id}
        )

    def test_channel_scope_history_returns_only_assigned_channel(self):
        order_b = self._order(self.ch_b, self.teammate)
        user = self._channel_scope_user("rec-hist-channel", self.ch_a)
        resp = self._history(user)
        self.assertEqual(resp.status_code, 200, resp.content)
        returned = {row["id"] for row in resp.json()}
        self.assertEqual(
            returned, {self.own_order.id, self.other_order.id, self.koc_order.id}
        )
        self.assertNotIn(order_b.id, returned)


class OrderRecordScopeQUnitTests(_RecordScopeBase):
    """Đo TRỰC TIẾP `order_record_scope_q`, không đi qua endpoint nào.

    Vì sao cần: các test "CHANNEL scope + 0 UserChannel" ở tầng API
    (`test_order_bulk_action_permissions`, `test_close_case_record_scope`, và cả file
    này) là TAUTOLOGY — chúng vẫn xanh khi xoá sạch logic scope, vì
    `UserService.can_edit_order` trả `can_edit=False` cho scope CHANNEL không có kênh
    nên 403 xảy ra TRƯỚC khi record-scope kịp có ý nghĩa. Nhánh thật sự nguy hiểm
    (`_view_helpers` quên `continue` / dùng `Q()` cho set kênh rỗng → Q rỗng → mở toàn
    bộ đơn công ty) không hề được chạm tới.

    Test ở đây không có gate backstop nào: chỉ có Q và queryset.
    """

    def _all_ids(self, q) -> set[int]:
        return set(
            Order.objects.filter(deleted_at__isnull=True)
            .filter(q)
            .values_list("id", flat=True)
        )

    def test_channel_scope_without_any_channel_matches_no_order(self):
        """Ca rò rỉ chính: bị giới hạn kênh nhưng chưa được gán kênh nào → khớp RỖNG.

        Kiểm chứng bằng queryset chạy thật (0 dòng) thay vì so sánh Q, để không phụ
        thuộc cách Django rút gọn biểu thức.
        """
        user = self._channel_scope_user("q-unit-nochannel")
        scope_q = order_record_scope_q(user, ORDER_WRITE_SCOPE_SECTIONS)
        self.assertEqual(self._all_ids(scope_q), set())
        # Và phải là "không khớp gì" tường minh, không phải Q() rỗng (Q() rỗng = tất cả).
        self.assertEqual(scope_q, Q(pk__in=[]))

    def test_own_scope_matches_only_own_orders(self):
        scope_q = order_record_scope_q(self.owner, ORDER_WRITE_SCOPE_SECTIONS)
        self.assertEqual(self._all_ids(scope_q), {self.own_order.id})

    def test_all_scope_matches_every_order(self):
        scope_q = order_record_scope_q(self.boss, ORDER_WRITE_SCOPE_SECTIONS)
        self.assertEqual(
            self._all_ids(scope_q),
            {self.own_order.id, self.other_order.id, self.koc_order.id},
        )

    def test_channel_scope_with_channel_matches_that_channel_only(self):
        order_b = self._order(self.ch_b, self.teammate)
        user = self._channel_scope_user("q-unit-channel-a", self.ch_a)
        scope_q = order_record_scope_q(user, ORDER_WRITE_SCOPE_SECTIONS)
        matched = self._all_ids(scope_q)
        self.assertNotIn(order_b.id, matched)
        self.assertIn(self.other_order.id, matched)

    def test_user_without_any_view_permission_matches_no_order(self):
        user = make_scoped_user("q-unit-noperm", "shipments", scope=DataScope.ALL)
        scope_q = order_record_scope_q(user, ORDER_WRITE_SCOPE_SECTIONS)
        self.assertEqual(self._all_ids(scope_q), set())
        self.assertEqual(scope_q, Q(pk__in=[]))

    def test_undeclared_section_raises_instead_of_opening_everything(self):
        """I4 fail-closed: section chưa khai phải NỔ, không im lặng mở toàn công ty.

        Trước fix, `_SECTION_ORDER_IDENTITY_Q.get(section, Q())` trả Q() rỗng cho section
        lạ; với user scope ALL thì `section_q` cũng rỗng → hàm return Q() → mọi đơn.
        Một typo tên section là đủ để vô hiệu hoá record-scope.
        """
        with self.assertRaises(ImproperlyConfigured):
            order_record_scope_q(self.boss, ("orders", "khong-ton-tai"))

    def test_undeclared_section_raises_even_without_permission_on_it(self):
        """Raise phải xảy ra TRƯỚC check `{section}:view`.

        Nếu validate sau, section gõ sai không bao giờ có permission tương ứng nên luôn
        bị `continue` — lỗi cấu hình sẽ không bao giờ lộ ra ở test hay ở dev.
        """
        user = make_scoped_user("q-unit-typo", "orders", scope=DataScope.OWN)
        with self.assertRaises(ImproperlyConfigured):
            order_record_scope_q(user, ("koc-order",))  # thiếu chữ "s"

    def test_every_section_used_by_views_is_declared(self):
        """Mọi tập section đang được view truyền vào đều phải khai trong map.

        Chốt cứng danh sách để lần sau thêm endpoint mới (vd "returns") thì test này đỏ
        ngay tại chỗ khai báo, không phải đợi request thật ở production.
        """
        used = {
            *ORDER_WRITE_SCOPE_SECTIONS,
            *INLINE_SCOPE_SECTIONS,
            "customers",  # customer_orders
            "warranty",  # search_orders
        }
        self.assertEqual(used - set(_SECTION_ORDER_IDENTITY_Q), set())

    def test_channel_scope_without_any_channel_history_is_empty(self):
        user = self._channel_scope_user("rec-hist-nochannel")
        resp = self._history(user)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json(), [])


class DepartmentScopeCanEditOrderTests(_RecordScopeBase):
    """`can_edit_order` ở scope DEPARTMENT phải mirror read-scope (mở kênh qua phòng ban).

    Trước fix, nhánh DEPARTMENT chỉ so vào `assigned_channel_codes` (= UserChannel), nên
    user department-scope THẤY đơn kênh của phòng ban mình trên danh sách nhưng KHÔNG
    sửa/đóng case/xoá được — chặn oan. Fix hợp thêm `department_channel_codes(user)`.
    """

    def setUp(self):
        super().setUp()
        self.dept = Department.objects.create(name="Phong Record Test")
        self.dept.channels.add(self.ch_a)
        self.dept_user = make_scoped_user("rec-dept", "orders", scope=DataScope.DEPARTMENT)
        UserDepartment.objects.create(user=self.dept_user, department=self.dept)
        bump_rbac_cache_version()
        self.order_b = self._order(self.ch_b, self.teammate)

    def test_department_scope_patches_order_of_department_channel(self):
        """KHÔNG có UserChannel nào — quyền phải đến từ phòng ban."""
        self.assertFalse(UserChannel.objects.filter(user=self.dept_user).exists())
        grant_order_fields(self.dept_user, "status")
        resp = self._patch(self.dept_user, self.other_order, {"status": OrderStatus.IN_DELIVERY})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.other_order.refresh_from_db()
        self.assertEqual(self.other_order.status, OrderStatus.IN_DELIVERY)

    def test_department_scope_cannot_patch_order_outside_department(self):
        resp = self._patch(self.dept_user, self.order_b, {"status": OrderStatus.IN_DELIVERY})
        self.assertEqual(resp.status_code, 403, resp.content)
        self.order_b.refresh_from_db()
        self.assertEqual(self.order_b.status, OrderStatus.NEW)

    def test_can_edit_order_allows_department_channel_without_user_channel(self):
        """Kiểm trực tiếp service: `assigned_channel_codes` RỖNG mà vẫn phải cho phép.

        Qua API, record-scope Q cũng lọc theo cùng tập kênh phòng ban, nên nếu chỉ test
        qua API thì không phân biệt được lớp nào chặn. Assert thẳng vào service để khoá
        đúng nhánh DEPARTMENT của `can_edit_order`.
        """
        result = UserService.can_edit_order(self.dept_user, self.ch_a.code, set())
        self.assertTrue(result.can_edit, result.reason)
        self.assertEqual(result.reason, "department-channel")

    def test_can_edit_order_still_denies_channel_outside_department(self):
        result = UserService.can_edit_order(self.dept_user, self.ch_b.code, set())
        self.assertFalse(result.can_edit)
        self.assertEqual(result.reason, "channel-not-assigned")
