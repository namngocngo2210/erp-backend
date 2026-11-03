"""Record-scope cho các endpoint ĐỌC đơn theo tham số client chọn: customer_orders + search_orders.

Gộp 2026-08-05 từ test_customer_orders_record_scope.py + test_order_search_record_scope.py
(xem docs/test-restructure-2026-08-05.md). Docstring gốc của từng file nằm ở docstring
lớp tương ứng bên dưới.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderStatus
from apps.orders.tests.helpers import make_scoped_user
from apps.users.models import DataScope
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import sync_permissions

PHONE_CUSTOMER_ORDERS = "0909222111"

SEARCH_URL = "/api/orders/search"
# SĐT dùng chung để 1 lần `q` kéo về cả 3 đơn — đúng khuôn PoC.
PHONE_SEARCH = "0909555666"
OTHER_ADDRESS = "So 7 Ngo Quyen, Ha Noi"


class _CustomerOrdersBase(TestCase):
    """Record-scope cho `customer_orders` (GET /api/customers/<id>/orders).

    `OrderRepository.list_customer_orders` là ANH EM của `list_history_by_phone` (đã siết
    ở `list_order_history`): cùng trả nguyên bản ghi đơn kèm PII khách + CRM, cùng nhận
    một tham số THUẦN do client chọn (`customer_id` thay vì `phone`). Trước fix, endpoint
    chỉ gate `require_section("customers", *ORDER_SECTIONS)` nên bất kỳ ai mở được trang
    Khách hàng đều đọc được TOÀN BỘ đơn của mọi khách chỉ bằng cách đổi `customer_id`
    (audit 2026-07-26 N1, phía ĐỌC).

    Fix áp ở TẦNG VIEW (không đổi chữ ký repository) vì `list_customer_orders` còn một
    call-site nữa — `CustomerService.build_customer_order_aggregate`, phục vụ endpoint
    `customer_order_aggregate` — nơi việc siết scope sẽ làm HỎNG nghiệp vụ (xem
    `CustomerOrderAggregateStaysUnscopedTests` bên dưới).

    `sections` truyền vào phải khớp ĐÚNG danh sách của `require_section`, tức
    `("customers", *ORDER_SECTIONS)`: nếu chỉ truyền ORDER_SECTIONS thì user CSKH chỉ
    được cấp section "customers" sẽ mất sạch danh sách đơn của khách — chặn oan.
    """

    def setUp(self):
        # KHÔNG cache.clear() — RedisCache.clear() FLUSHDB cả redis dùng chung.
        sync_permissions()
        self.client = APIClient()
        self.ch_a = Channel.objects.create(code="cust-a", name="Cust A")
        self.ch_b = Channel.objects.create(code="cust-b", name="Cust B")
        self.customer = Customer.objects.create(
            full_name="Khach Chung",
            phone_raw=PHONE_CUSTOMER_ORDERS,
            phone_normalized=PHONE_CUSTOMER_ORDERS,
        )

        self.owner = make_scoped_user("cust-owner", "orders", "customers", scope=DataScope.OWN)
        self.teammate = make_scoped_user("cust-teammate", "orders", "customers", scope=DataScope.OWN)
        self.boss = make_scoped_user("cust-boss", "orders", "customers", scope=DataScope.ALL)

        self.own_order = self._order(self.ch_a, self.owner)
        self.other_order = self._order(self.ch_a, self.teammate)
        self.order_b = self._order(self.ch_b, self.teammate)

    def _order(self, channel, staff, **extra) -> Order:
        defaults = {
            "channel": channel,
            "customer": self.customer,
            "staff": staff,
            "name": "Don Cust",
            "phone": PHONE_CUSTOMER_ORDERS,
            "total_amount": "100000.00",
            "status": OrderStatus.NEW,
        }
        defaults.update(extra)
        return Order.objects.create(**defaults)

    def _list(self, user):
        self.client.force_authenticate(user=user)
        return self.client.get(f"/api/customers/{self.customer.id}/orders")


class CustomerOrdersRecordScopeTests(_CustomerOrdersBase):
    def test_own_scope_hides_orders_of_other_staff(self):
        resp = self._list(self.owner)
        self.assertEqual(resp.status_code, 200, resp.content)
        returned = [row["id"] for row in resp.json()]
        self.assertEqual(returned, [self.own_order.id])
        # Trước fix, body chứa cả đơn của teammate → dò được đơn của khách bất kỳ.
        self.assertNotIn(self.other_order.id, returned)
        self.assertNotIn(self.order_b.id, returned)

    def test_all_scope_still_returns_every_order_of_customer(self):
        resp = self._list(self.boss)
        self.assertEqual(resp.status_code, 200, resp.content)
        returned = {row["id"] for row in resp.json()}
        self.assertEqual(
            returned, {self.own_order.id, self.other_order.id, self.order_b.id}
        )

    def test_channel_scope_returns_only_assigned_channel(self):
        user = make_scoped_user("cust-channel", "orders", "customers", scope=DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.ch_a, is_active=True)
        bump_rbac_cache_version()
        resp = self._list(user)
        self.assertEqual(resp.status_code, 200, resp.content)
        returned = {row["id"] for row in resp.json()}
        self.assertEqual(returned, {self.own_order.id, self.other_order.id})
        self.assertNotIn(self.order_b.id, returned)

    def test_channel_scope_without_any_channel_returns_empty(self):
        """Nhánh rò rỉ dễ bỏ sót: scope CHANNEL nhưng chưa được gán kênh nào."""
        user = make_scoped_user("cust-channel-none", "orders", "customers", scope=DataScope.CHANNEL)
        resp = self._list(user)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json(), [])

    def test_customers_only_section_scope_all_still_sees_every_order(self):
        """CSKH chỉ có section `customers` (không có section đơn nào) — KHÔNG chặn oan.

        Endpoint guard bằng `require_section("customers", *ORDER_SECTIONS)` nên
        record-scope phải hợp cả section "customers"; nếu chỉ truyền ORDER_SECTIONS,
        user này rơi vào nhánh "không section nào" → mất sạch danh sách đơn.
        """
        user = make_scoped_user("cust-only-all", "customers", scope=DataScope.ALL)
        resp = self._list(user)
        self.assertEqual(resp.status_code, 200, resp.content)
        returned = {row["id"] for row in resp.json()}
        self.assertEqual(
            returned, {self.own_order.id, self.other_order.id, self.order_b.id}
        )

    def test_customers_only_section_scope_own_sees_only_own_orders(self):
        user = make_scoped_user("cust-only-own", "customers", scope=DataScope.OWN)
        self.own_order.staff = user
        self.own_order.save(update_fields=["staff"])
        resp = self._list(user)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual([row["id"] for row in resp.json()], [self.own_order.id])


class CustomerOrderAggregateStaysUnscopedTests(_CustomerOrdersBase):
    """`customer_order_aggregate` CỐ Ý không siết record-scope — quyết định nghiệp vụ.

    Aggregate chỉ trả ĐẾM (bombed / unreachable / delivered) + mã phân loại khách,
    KHÔNG trả bản ghi đơn nào, không PII, không id. Đây là tín hiệu CẢNH BÁO liên
    kênh: mục đích của nó là để sale biết khách này đã bom hàng của NGƯỜI KHÁC. Siết
    theo scope OWN sẽ luôn trả bombed_count=0 cho đúng những ca cần cảnh báo nhất →
    mất tính năng nghiệp vụ, không phải bịt lỗ hổng.

    Test này khoá quyết định đó lại để lần siết sau phải cân nhắc có ý thức.
    """

    def test_aggregate_counts_orders_outside_caller_scope(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.get(f"/api/customers/{self.customer.id}/orders/aggregate")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["total_orders"], 3)


class OrderSearchRecordScopeTests(TestCase):
    """Record-scope cho `search_orders` (GET /api/orders/search?q=).

    Trước fix endpoint chỉ gate `require_section("warranty", *ORDER_SECTIONS)` rồi
    `_apply_list_filters(base, {"q": q})` — KHÔNG scope bản ghi. Vì `q` khớp
    phone/shipping_code/invoice_code/order_code/name, endpoint trở thành oracle liệt kê
    CSDL khách: bất kỳ user nào có 1 section đơn đều dò được `name` + `phone` (KHÔNG mask)
    + `address` + `total_after_discount` của mọi đơn công ty, 20 dòng/lượt.

    Test assert vào NỘI DUNG trả về (SĐT/tên/địa chỉ của đơn người khác không được xuất
    hiện), không chỉ đếm dòng — status code luôn 200 nên assert status là tautology.

    Chiều CHO PHÉP quan trọng nhất: user `warranty` phải tra được MỌI đơn. Khách mang máy
    đến bảo hành có thể là đơn của bất kỳ sale nào, và cả section warranty
    (`apps/warranty/claim_views.py`) vốn không record-scope gì — nên `"warranty"` được khai
    tường minh là tập CHA trong `_SECTION_ORDER_IDENTITY_Q`.
    """

    def setUp(self):
        # KHÔNG cache.clear() — RedisCache.clear() FLUSHDB cả redis dùng chung.
        sync_permissions()
        self.client = APIClient()
        self.ch_a = Channel.objects.create(code="srch-a", name="Search A")
        self.ch_b = Channel.objects.create(code="srch-b", name="Search B")
        self.customer = Customer.objects.create(
            full_name="Khach Search",
            phone_raw=PHONE_SEARCH,
            phone_normalized=PHONE_SEARCH,
        )

        self.owner = make_scoped_user("srch-owner", "orders", scope=DataScope.OWN)
        self.teammate = make_scoped_user("srch-teammate", "orders", scope=DataScope.OWN)
        self.boss = make_scoped_user("srch-boss", "orders", scope=DataScope.ALL)

        self.own_order = self._order(self.ch_a, self.owner, name="Don Cua Toi")
        self.other_order = self._order(
            self.ch_a, self.teammate, name="Don Nguoi Khac", address=OTHER_ADDRESS
        )
        self.koc_order = self._order(
            self.ch_a, self.teammate, name="Don KOC Nguoi Khac", order_source="koc"
        )

    def _order(self, channel, staff, **extra) -> Order:
        defaults = {
            "channel": channel,
            "customer": self.customer,
            "staff": staff,
            "name": "Don Search",
            "phone": PHONE_SEARCH,
            "total_amount": "100000.00",
            "status": OrderStatus.NEW,
        }
        defaults.update(extra)
        return Order.objects.create(**defaults)

    def _search(self, user, q: str = PHONE_SEARCH):
        self.client.force_authenticate(user=user)
        return self.client.get(SEARCH_URL, {"q": q})

    # ── chiều CHẶN ──────────────────────────────────────────────────────────
    def test_own_scope_search_hides_orders_of_other_staff(self):
        resp = self._search(self.owner)
        self.assertEqual(resp.status_code, 200, resp.content)
        returned = [row["id"] for row in resp.json()]
        self.assertEqual(returned, [self.own_order.id])
        # PII của đơn người khác tuyệt đối không được lọt vào body.
        body = resp.content.decode()
        self.assertNotIn("Don Nguoi Khac", body)
        self.assertNotIn("Don KOC Nguoi Khac", body)
        self.assertNotIn(OTHER_ADDRESS, body)

    def test_channel_scope_search_returns_only_assigned_channel(self):
        order_b = self._order(self.ch_b, self.teammate, name="Don Kenh B")
        user = make_scoped_user("srch-channel-a", "orders", scope=DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.ch_a, is_active=True)
        bump_rbac_cache_version()
        resp = self._search(user)
        self.assertEqual(resp.status_code, 200, resp.content)
        returned = {row["id"] for row in resp.json()}
        self.assertNotIn(order_b.id, returned)
        self.assertNotIn("Don Kenh B", resp.content.decode())

    def test_channel_scope_without_any_channel_searches_nothing(self):
        user = make_scoped_user("srch-channel-none", "orders", scope=DataScope.CHANNEL)
        resp = self._search(user)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json(), [])
        self.assertNotIn(PHONE_SEARCH, resp.content.decode())

    def test_koc_only_scope_all_search_returns_only_koc_orders(self):
        """Leo quyền ngang: scope all của `koc-orders` không mở ra đơn thường."""
        user = make_scoped_user("srch-koc-all", "koc-orders", scope=DataScope.ALL)
        resp = self._search(user)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual([row["id"] for row in resp.json()], [self.koc_order.id])
        self.assertNotIn("Don Nguoi Khac", resp.content.decode())

    # ── chiều CHO PHÉP ──────────────────────────────────────────────────────
    def test_all_scope_search_returns_every_matching_order(self):
        resp = self._search(self.boss)
        self.assertEqual(resp.status_code, 200, resp.content)
        returned = {row["id"] for row in resp.json()}
        self.assertEqual(
            returned, {self.own_order.id, self.other_order.id, self.koc_order.id}
        )

    def test_warranty_only_user_searches_every_order(self):
        """Nghiệp vụ bảo hành phải tra được đơn của MỌI sale — không được chặn oan.

        Section warranty là tập CHA (khách mang máy tới bảo hành có thể là đơn của bất
        kỳ ai); nếu `"warranty"` bị map nhầm thành một điều kiện danh tính nào đó,
        luồng tạo phiếu bảo hành sẽ không tìm thấy đơn.
        """
        user = make_scoped_user("srch-warranty", "warranty", scope=DataScope.ALL)
        resp = self._search(user)
        self.assertEqual(resp.status_code, 200, resp.content)
        returned = {row["id"] for row in resp.json()}
        self.assertEqual(
            returned, {self.own_order.id, self.other_order.id, self.koc_order.id}
        )
