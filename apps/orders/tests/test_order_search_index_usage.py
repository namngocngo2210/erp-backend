"""Tìm kiếm phải dùng được index (audit 2026-07-26 mục C2) + RBAC scope job KiotViet (T3).

Nguyên lý đã ghi tại `repositories.py` (khối comment trong `_apply_list_filters`):
`__icontains` sinh `UPPER(col) LIKE UPPER(%s)` → KHÔNG khớp được trigram GIN
bare-column (`order_invoice_code_trgm_idx`, `order_ship_code_trgm_idx`,
`order_phone_norm_trgm_idx`, ...) → seq-scan 500k dòng `orders`. `__contains`
(case-sensitive LIKE) thì khớp. NGOẠI LỆ DUY NHẤT: tên khách — có functional index
`order_name_upper_trgm_idx` trên `Upper(Cast(name AS text))` nên `name__icontains`
vẫn index-scan được và phải GIỮ (tên tiếng Việt cần case-insensitive).

Vì sao assert vào SQL sinh ra chứ không đo thời gian: thời gian query phụ thuộc máy
CI/dữ liệu nên flaky; còn "có UPPER( trên cột code/phone hay không" là điều kiện ĐỦ
để planner bỏ trigram index — kiểm được tất định, không cần 500k dòng.

Mọi test tối ưu đều đi kèm test HÀNH VI: tối ưu mà làm hỏng kết quả tìm kiếm thì
là bug, không phải tối ưu.
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.customers.customer_resale_service import CustomerResaleService
from apps.customers.models import Customer
from apps.orders.models import Order, OrderKiotVietJob, OrderKiotVietJobStatus, OrderStatus
from apps.orders.tests.helpers import make_scoped_user
from apps.orders.repositories import OrderRepository
from apps.users.models import (
    DataScope,
    Department,
    Permission,
    Role,
    RolePermission,
    User,
    UserDepartment,
    UserRoleAssignment,
)
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import sync_permissions

JOBS_URL = "/api/orders/kiotviet-jobs"

# Các cột code/SĐT PHẢI được so bằng LIKE trần (không UPPER) thì trigram GIN mới vào.
# Viết HOA sẵn vì assertion chạy trên SQL đã `.upper()`.
INDEXED_BARE_COLUMNS = (
    ("ORDERS", "ORDER_CODE"),
    ("ORDERS", "INVOICE_CODE"),
    ("ORDERS", "OLD_INVOICE_CODE"),
    ("ORDERS", "SHIPPING_CODE"),
    ("ORDERS", "PHONE"),
    ("ORDERS", "PHONE_NORMALIZED"),
    ("CUSTOMERS", "PHONE_RAW"),
    ("CUSTOMERS", "PHONE_NORMALIZED"),
)


def _assert_no_upper_on_indexed_columns(testcase: TestCase, sql: str):
    """`sql` (đã `.upper()`) không được chứa `UPPER("<bảng>"."<cột code/phone>"`.

    Assert theo TỪNG cột (thay vì chỉ đếm tổng số `UPPER(`) để thông báo lỗi chỉ đúng
    cột bị hỏng, và để không vô tình bắt nhầm `UPPER` hợp lệ của cột `name`.
    """
    for table, column in INDEXED_BARE_COLUMNS:
        needle = f'UPPER("{table}"."{column}"'
        testcase.assertNotIn(
            needle,
            sql,
            f"{table}.{column} bị bọc UPPER() → planner bỏ trigram GIN, seq-scan bảng",
        )


class _SearchDataMixin:
    """Dữ liệu chung: 2 đơn có mã IN HOA + SĐT lưu dạng `09…`, tên có dấu tiếng Việt."""

    def build_orders(self):
        self.channel = Channel.objects.create(code="srch-a", name="Search A")
        self.other_channel = Channel.objects.create(code="srch-b", name="Search B")
        self.customer = Customer.objects.create(
            full_name="Nguyễn Thị Khách",
            phone_raw="0901234567",
            phone_normalized="0901234567",
        )
        # Khách khác hẳn số — dùng cho đơn lưu SĐT dạng `+84…` để chứng minh nhánh
        # `phone_normalized` CỦA CHÍNH ĐƠN mới là thứ cứu được kết quả (đơn có thể gắn
        # customer khác sau khi gộp/sửa khách).
        self.other_customer = Customer.objects.create(
            full_name="Khach Khac",
            phone_raw="0977000111",
            phone_normalized="0977000111",
        )
        self.staff = make_scoped_user("srch-staff", "orders", scope=DataScope.ALL)

        # Mã đơn/hóa đơn/vận đơn trong hệ này là CHỮ IN + số (xem fixture của
        # apps/inventory/tests, apps/kiotviet/tests) → `__contains` case-sensitive an toàn.
        self.order_a = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            staff=self.staff,
            name="Nguyễn Văn Ánh",
            phone="0901234567",
            invoice_code="HD1234567",
            order_code="DH-SEARCH-001",
            shipping_code="VDSEARCH01",
            external_order_id="EXT-SEARCH-001",
            total_amount="100000.00",
            status=OrderStatus.NEW,
        )
        # Đơn lưu SĐT dạng `+84…` — `list_history_by_phone` cũ so `phone__icontains`
        # với số ĐÃ chuẩn hóa (`09…`) nên BỎ SÓT đúng nhóm đơn này.
        self.order_b = Order.objects.create(
            channel=self.other_channel,
            customer=self.other_customer,
            name="trần thị bích",
            phone="+84901234567",
            invoice_code="HD7654321",
            order_code="DH-SEARCH-002",
            total_amount="200000.00",
            status=OrderStatus.NEW,
        )


class KiotVietJobSearchSqlTests(_SearchDataMixin, TestCase):
    """`list_kiotviet_jobs`: ô tìm kiếm không được bọc UPPER lên cột code/SĐT."""

    def setUp(self):
        sync_permissions()
        self.build_orders()

    def test_search_sql_has_no_upper_on_code_columns(self):
        queryset = OrderRepository.list_kiotviet_jobs({"q": "HD1234567"})
        sql = str(queryset.query).upper()
        _assert_no_upper_on_indexed_columns(self, sql)
        # UPPER duy nhất được phép trong SQL của bảng job là trên `error_message`
        # (TextField không có index nào → UPPER không làm mất index).
        self.assertLessEqual(sql.count("UPPER("), 2)
        self.assertNotIn('UPPER("ORDERS"', sql)

    def test_search_orders_side_query_uses_at_most_one_upper_per_indexed_table(self):
        """Toàn bộ SQL THỰC THI: `UPPER(` trên `orders` chỉ được ở cột `name`."""
        with CaptureQueriesContext(connection) as ctx:
            list(OrderRepository.list_kiotviet_jobs({"q": "HD1234567"}))
        sql = " ".join(entry["sql"] for entry in ctx.captured_queries).upper()
        _assert_no_upper_on_indexed_columns(self, sql)
        # Nhánh tên khách vẫn phải còn (functional index order_name_upper_trgm_idx).
        self.assertIn('UPPER("ORDERS"."NAME"', sql)
        self.assertEqual(
            sql.count('UPPER("ORDERS"'),
            1,
            "chỉ nhánh name__icontains được phép sinh UPPER() trên bảng orders",
        )

    def test_search_does_not_or_across_the_job_join(self):
        """Điều kiện đơn phải thu về `order_id IN (...)`, không OR xuyên join.

        OR xuyên join `order__*` khiến Postgres không BitmapOr được, phải nối rồi
        lọc — seq-scan cả `orders` lẫn `order_kiotviet_jobs`.
        """
        queryset = OrderRepository.list_kiotviet_jobs({"q": "HD1234567"})
        # Chỉ soi mệnh đề WHERE: danh sách cột SELECT vẫn có `"orders"."invoice_code"`
        # do select_related("order__channel") — đó là join hiển thị, không phải điều kiện.
        where = str(queryset.query).split(" WHERE ", 1)[1]
        self.assertIn('"order_kiotviet_jobs"."order_id" IN', where)
        self.assertNotIn('"orders"."invoice_code"', where)
        self.assertNotIn('"orders"."name"', where)


class HistoryByPhoneSqlTests(_SearchDataMixin, TestCase):
    def setUp(self):
        sync_permissions()
        self.build_orders()

    def test_history_by_phone_compares_normalized_column(self):
        queryset = OrderRepository.list_history_by_phone("0901234567")
        sql = str(queryset.query)
        self.assertIn('"orders"."phone_normalized"', sql)
        _assert_no_upper_on_indexed_columns(self, sql.upper())
        self.assertNotIn("UPPER(", sql.upper())
        # So sánh BẰNG trên cột chuẩn hóa (B-tree order_phone_norm_idx), không LIKE.
        self.assertNotIn('"orders"."phone" LIKE', sql)


class KiotVietJobSearchBehaviourTests(_SearchDataMixin, TestCase):
    """Tối ưu không được làm mất kết quả tìm kiếm."""

    def setUp(self):
        sync_permissions()
        self.build_orders()
        self.job_a = OrderKiotVietJob.objects.create(
            order=self.order_a,
            action=OrderKiotVietJob.ACTION_PUSH,
            status=OrderKiotVietJobStatus.SUCCESS,
            requested_by=self.staff,
        )
        self.job_b = OrderKiotVietJob.objects.create(
            order=self.order_b,
            action=OrderKiotVietJob.ACTION_PUSH,
            status=OrderKiotVietJobStatus.FAILED,
            requested_by=self.staff,
            error_message="Kênh KiotViet trả lỗi TIMEOUT",
        )
        # Đơn có ô SĐT gõ 2 số → `normalize_phone` trả "" → `phone_normalized` RỖNG.
        # Dữ liệu CRM thật có nhóm này; nếu chỉ tìm trên cột chuẩn hóa thì đơn biến mất.
        self.order_c = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            staff=self.staff,
            name="Khach Hai So",
            phone="0933111222 / 0944333555",
            invoice_code="HD5550001",
            total_amount="300000.00",
            status=OrderStatus.NEW,
        )
        self.job_c = OrderKiotVietJob.objects.create(
            order=self.order_c,
            action=OrderKiotVietJob.ACTION_PUSH,
            status=OrderKiotVietJobStatus.SUCCESS,
            requested_by=self.staff,
        )

    def _search(self, q: str) -> list[int]:
        return [job.id for job in OrderRepository.list_kiotviet_jobs({"q": q})]

    def test_middle_substring_of_invoice_code_still_matches(self):
        self.assertEqual(self._search("D12345"), [self.job_a.id])

    def test_code_keyword_does_not_drag_in_phone_rows(self):
        """Gõ mã `HD1234567` không được kéo về đơn chỉ vì SĐT chứa dãy "1234567".

        `order_b` có `phone_normalized="0901234567"`; nếu nhánh SĐT không bỏ qua từ
        khóa CÓ CHỮ CÁI thì mọi lần tra mã hóa đơn sẽ lẫn đơn của khách khác.
        """
        self.assertEqual(self._search("HD1234567"), [self.job_a.id])

    def test_middle_substring_of_shipping_code_still_matches(self):
        self.assertEqual(self._search("SEARCH0"), [self.job_a.id])

    def test_order_code_still_matches(self):
        self.assertEqual(self._search("SEARCH-002"), [self.job_b.id])

    def test_international_phone_form_matches_order_stored_as_local(self):
        """Gõ `+84 901 234 567` phải ra đơn lưu `0901234567`."""
        self.assertIn(self.job_a.id, self._search("+84 901 234 567"))

    def test_partial_phone_still_matches(self):
        self.assertIn(self.job_a.id, self._search("1234567"))

    def test_lowercase_vietnamese_name_still_matches(self):
        """Nhánh dễ vỡ nhất khi đổi icontains→contains: tên khách."""
        self.assertEqual(self._search("nguyễn văn"), [self.job_a.id])
        self.assertEqual(self._search("TRẦN THỊ"), [self.job_b.id])

    def test_phone_that_cannot_be_normalized_still_matches(self):
        self.assertEqual(self.order_c.phone_normalized, "")
        self.assertEqual(self._search("0944333555"), [self.job_c.id])

    def test_error_message_still_matches(self):
        self.assertEqual(self._search("TIMEOUT"), [self.job_b.id])

    def test_external_order_id_still_matches(self):
        self.assertEqual(self._search("EXT-SEARCH-001"), [self.job_a.id])

    def test_unknown_keyword_returns_nothing(self):
        self.assertEqual(self._search("KHONGCOMA"), [])


class HistoryByPhoneBehaviourTests(_SearchDataMixin, TestCase):
    def setUp(self):
        sync_permissions()
        self.build_orders()

    def test_returns_orders_matching_customer_and_raw_phone_forms(self):
        ids = {order.id for order in OrderRepository.list_history_by_phone("0901234567")}
        # order_a: khớp qua customer.phone_normalized (đã đúng từ trước).
        self.assertIn(self.order_a.id, ids)
        # order_b: lưu `+84901234567`, customer gắn số KHÁC → chỉ khớp nếu so trên
        # `phone_normalized` của chính đơn (fix). Cách cũ (`phone__icontains` với số đã
        # chuẩn hóa) sót đúng nhóm này.
        self.assertIn(self.order_b.id, ids)

    def test_does_not_return_orders_of_other_phone(self):
        other = Order.objects.create(
            channel=self.channel,
            customer=self.other_customer,
            name="Khach Khac",
            phone="0988888888",
            total_amount="1000.00",
            status=OrderStatus.NEW,
        )
        ids = {order.id for order in OrderRepository.list_history_by_phone("0901234567")}
        self.assertNotIn(other.id, ids)

    def test_empty_phone_returns_nothing(self):
        self.assertEqual(list(OrderRepository.list_history_by_phone("")), [])


class ResaleSearchTests(TestCase):
    """Trang resale (`CustomerResaleService.list_resale_tracking`) — cùng nguyên lý."""

    def setUp(self):
        sync_permissions()
        self.channel = Channel.objects.create(code="resale-srch", name="Resale Search")
        self.customer = Customer.objects.create(
            full_name="Lê Thị Resale",
            phone_raw="0912345678",
            phone_normalized="0912345678",
        )
        self.staff = make_scoped_user("resale-staff", "orders", scope=DataScope.ALL)
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            staff=self.staff,
            name="Lê Thị Resale",
            phone="0912345678",
            invoice_code="HD9998887",
            order_code="DH-RESALE-001",
            shipping_code="VDRESALE01",
            order_source="landing",
            total_amount="100000.00",
            status=3,  # DELIVERED_SUCCESS_ORDER_STATUS
        )

    def _search_ids(self, q: str) -> list[int]:
        payload = CustomerResaleService.list_resale_tracking(limit=20, offset=0, q=q)
        return [row["order_id"] for row in payload["results"]]

    def test_search_sql_has_no_upper_on_code_columns(self):
        with CaptureQueriesContext(connection) as ctx:
            CustomerResaleService.list_resale_tracking(limit=20, offset=0, q="HD9998887")
        sql = " ".join(entry["sql"] for entry in ctx.captured_queries).upper()
        _assert_no_upper_on_indexed_columns(self, sql)

    def test_middle_substring_of_invoice_code_still_matches(self):
        self.assertEqual(self._search_ids("99988"), [self.order.id])

    def test_international_phone_form_matches(self):
        self.assertEqual(self._search_ids("+84 912 345 678"), [self.order.id])

    def test_lowercase_vietnamese_name_still_matches(self):
        self.assertEqual(self._search_ids("lê thị"), [self.order.id])

    def test_phone_that_cannot_be_normalized_still_matches(self):
        """Đơn có ô SĐT gõ 2 số (`phone_normalized` rỗng) vẫn phải tra được."""
        messy = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            staff=self.staff,
            name="Khach Hai So",
            phone="0912345678 / 0987654321",
            order_code="DH-RESALE-002",
            order_source="landing",
            total_amount="100000.00",
            status=3,
        )
        self.assertEqual(messy.phone_normalized, "")
        self.assertIn(messy.id, self._search_ids("0987654321"))

    def test_unknown_keyword_returns_nothing(self):
        self.assertEqual(self._search_ids("KHONGCOMA"), [])


class KiotVietJobsScopeApiTests(TestCase):
    """T3: `GET /api/orders/kiotviet-jobs` phải theo RBAC scope THẬT.

    Trước fix: mọi non-superuser bị ép `requested_by = request.user.id`, nên trưởng
    nhóm scope department/all không xem được job đồng bộ của nhân viên mình để xử lý
    sự cố; ngược lại `channel_codes=None` cứng khiến không có lớp lọc kênh nào.
    """

    def setUp(self):
        sync_permissions()
        self.client = APIClient()
        self.ch_a = Channel.objects.create(code="job-a", name="Job A")
        self.ch_b = Channel.objects.create(code="job-b", name="Job B")

        self.customer = Customer.objects.create(
            full_name="Khach Job",
            phone_raw="0900000000",
            phone_normalized="0900000000",
        )
        self.owner = make_scoped_user("job-owner", "orders", scope=DataScope.OWN)
        self.teammate = make_scoped_user("job-teammate", "orders", scope=DataScope.OWN)

        self.own_order = self._order(self.ch_a, self.owner)
        self.teammate_order = self._order(self.ch_a, self.teammate)
        self.outside_order = self._order(self.ch_b, self.teammate)

        self.own_job = self._job(self.own_order, self.owner)
        self.teammate_job = self._job(self.teammate_order, self.teammate)
        self.outside_job = self._job(self.outside_order, self.teammate)

    def _order(self, channel, staff) -> Order:
        return Order.objects.create(
            channel=channel,
            customer=self.customer,
            staff=staff,
            name="Don Job",
            phone="0900000000",
            total_amount="1000.00",
            status=OrderStatus.NEW,
        )

    def _job(self, order: Order, user: User) -> OrderKiotVietJob:
        return OrderKiotVietJob.objects.create(
            order=order,
            action=OrderKiotVietJob.ACTION_PUSH,
            status=OrderKiotVietJobStatus.SUCCESS,
            requested_by=user,
        )

    def _list(self, user, **params) -> list[int]:
        self.client.force_authenticate(user=user)
        resp = self.client.get(JOBS_URL, params)
        self.assertEqual(resp.status_code, 200, resp.content)
        return [row["id"] for row in resp.json()]

    def test_order_phone_is_masked_in_response(self):
        """SĐT trong log job phải được mask — bù lớp chắn PII đã mất khi bỏ
        `filters["requested_by"] = request.user.id`.

        Endpoint này KHÔNG áp field_policy, và sau khi record-scope thay cho khoá cứng
        "chỉ job của chính mình", trưởng nhóm/ALL-scope đọc được job của người khác. SĐT
        thô chỉ để đối soát nên mask giữ 3 số cuối là đủ (khuôn `flow_history_views`).
        """
        boss = make_scoped_user("job-boss-mask", "orders", scope=DataScope.ALL)
        self.client.force_authenticate(user=boss)
        resp = self.client.get(JOBS_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.content.decode()
        self.assertNotIn("0900000000", body)
        for row in resp.json():
            self.assertEqual(row["order_phone"], "*******000")

    def test_own_scope_sees_only_jobs_of_own_orders(self):
        ids = self._list(self.owner)
        self.assertEqual(set(ids), {self.own_job.id})

    def test_all_scope_sees_jobs_requested_by_others(self):
        boss = make_scoped_user("job-boss", "orders", scope=DataScope.ALL)
        ids = self._list(boss)
        self.assertEqual(
            set(ids), {self.own_job.id, self.teammate_job.id, self.outside_job.id}
        )

    def test_department_scope_sees_jobs_of_department_channels_only(self):
        dept = Department.objects.create(name="Phong Job Test")
        dept.channels.add(self.ch_a)
        lead = make_scoped_user("job-lead", "orders", scope=DataScope.DEPARTMENT)
        UserDepartment.objects.create(user=lead, department=dept)
        bump_rbac_cache_version()

        ids = self._list(lead)
        self.assertIn(self.teammate_job.id, ids)
        self.assertIn(self.own_job.id, ids)
        self.assertNotIn(self.outside_job.id, ids)

    def test_channel_scope_without_channel_sees_nothing(self):
        """Bị giới hạn kênh nhưng chưa được gán kênh nào → không thấy job nào.

        Nhánh rò rỉ dễ bỏ sót: nếu coi "set rỗng" là "không giới hạn" thì user này
        thấy toàn bộ job của công ty.
        """
        user = make_scoped_user("job-nochannel", "orders", scope=DataScope.CHANNEL)
        self.assertEqual(self._list(user), [])

    def test_channel_scope_sees_jobs_of_assigned_channel(self):
        user = make_scoped_user("job-channel", "orders", scope=DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.ch_a, is_active=True)
        bump_rbac_cache_version()
        ids = self._list(user)
        self.assertIn(self.teammate_job.id, ids)
        self.assertNotIn(self.outside_job.id, ids)

    def test_requested_by_query_param_is_no_longer_overridden(self):
        """Filter `requested_by` do người dùng chọn phải có tác dụng.

        Trước fix, view ghi đè `filters["requested_by"] = request.user.id` cho mọi
        non-superuser nên bộ lọc "người thực hiện" trên UI vô nghĩa.
        """
        boss = make_scoped_user("job-boss-filter", "orders", scope=DataScope.ALL)
        ids = self._list(boss, requested_by=self.teammate.id)
        self.assertEqual(set(ids), {self.teammate_job.id, self.outside_job.id})
