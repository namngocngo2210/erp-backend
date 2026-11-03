"""Hợp đồng response + guard hiệu năng (CaptureQueriesContext) cho list endpoints.

Gộp từ test_order_list_serializer_trim.py, test_orders_list_query_count.py,
test_orders_status_counts.py.
"""

import pytest
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.orders.repositories import OrderRepository
from apps.orders.serializers import (
    LIST_EXCLUDED_CRM_FIELDS,
    LIST_EXCLUDED_ORDER_FIELDS,
)
from apps.users.tests.helpers import make_admin

# Đại diện những field mà bảng list / filter mirror realtime / dialog KShip đọc —
# rớt field nào ở đây là vỡ UI list (audit FE 2026-08-04, mục "chỗ nguy hiểm").
LIST_REQUIRED_FIELDS = (
    "id",
    "can_edit",
    "status",
    "invoice_status",
    "channel_code",
    "kiotviet_channel_id",
    "phone",
    "customer_phone",
    "raw_address",
    "address",
    "items",
    "weight",
    "dimensions",
    "invoice_code",
    "old_invoice_code",
    "shipping_code",
    "total_amount",
    "created_at",
    "crm",
    "recall",
    "is_kiotviet_processing",
    "latest_kiotviet_job_status",
    "latest_kiotviet_job_error",
)


class OrderListSerializerTrimTests(TestCase):
    """OrderListSerializer — hợp đồng bản gọn cho các trang danh sách đơn.

    Audit FE 2026-08-04 (docs/order-list-fanout-hardening-2026-08-04.md): các field
    trong LIST_EXCLUDED_ORDER_FIELDS chỉ trang chi tiết đọc, còn trang chi tiết LUÔN
    fetch GET /api/orders/{id} (OrderSerializer đầy đủ — FE đổi kèm cùng MR). Test
    khóa 4 mặt: list cắt đúng, list vẫn giữ field bảng/filter-mirror/KShip cần,
    crm bản gọn cho list thường, sheet3 giữ crm đầy đủ (panel BlockCrmRating dùng
    chung với detail), và detail không bị cắt.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-list-trim")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(code="lt-ch", name="LT Ch", owner="LT")
        self.customer = Customer.objects.create(
            full_name="KH LT", phone_raw="0900000030", phone_normalized="0900000030"
        )
        # Không có địa chỉ → info_complete=false → đơn cũng lọt tập sheet3.
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order LT",
            phone="0900000031",
            status=0,
            order_source="landing",
        )
        OrderAssessment.objects.create(order=self.order)

    def _first_row(self, endpoint):
        response = self.client.post(
            endpoint, {"limit": "50", "offset": "0"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        results = response.json()["results"]
        self.assertTrue(results, f"{endpoint} không trả row nào")
        return results[0]

    def test_list_row_drops_detail_only_fields(self):
        row = self._first_row("/api/orders")
        for field in LIST_EXCLUDED_ORDER_FIELDS:
            self.assertNotIn(field, row, f"field '{field}' phải bị cắt khỏi list")

    def test_list_row_keeps_fields_the_table_reads(self):
        row = self._first_row("/api/orders")
        for field in LIST_REQUIRED_FIELDS:
            self.assertIn(field, row, f"list thiếu field '{field}' mà FE đang đọc")

    def test_list_crm_is_trimmed_but_keeps_inline_edit_fields(self):
        crm = self._first_row("/api/orders")["crm"]
        self.assertIsNotNone(crm)
        for field in LIST_EXCLUDED_CRM_FIELDS:
            self.assertNotIn(field, crm, f"crm list phải cắt '{field}'")
        for field in ("sales_status_code", "delivery_business_status_code",
                      "internal_note", "needs", "next_followup_at", "next_reminder_at"):
            self.assertIn(field, crm, f"crm list thiếu '{field}'")

    def test_sheet3_keeps_full_crm_for_assessment_panel(self):
        crm = self._first_row("/api/orders/sheet3")["crm"]
        self.assertIsNotNone(crm)
        for field in ("ai_filled_fields", "extra_fields", "ai_open_insights",
                      "next_reminder_at", "sales_status_label"):
            self.assertIn(field, crm, f"crm sheet3 thiếu '{field}'")

    def test_list_query_count_constant_per_page(self):
        # lean_list_columns defer cột nặng — nếu code nào đụng field bị defer trên
        # page object thì Django query lại PER ROW (N+1 im lặng). Khóa bằng bất
        # biến: thêm đơn (kèm assessment) KHÔNG được làm tăng số query một trang.
        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.post(
                    "/api/orders", {"limit": "50", "offset": "0"}, format="json"
                )
                self.assertEqual(response.status_code, 200, response.content)
            return len(ctx.captured_queries)

        count_queries()  # warm-up: registry/label load-once của process không tính
        baseline = count_queries()
        for i in range(3):
            order = Order.objects.create(
                channel=self.channel,
                customer=self.customer,
                name=f"Order LT {i}",
                phone=f"09000000{40 + i}",
                status=0,
                order_source="landing",
            )
            OrderAssessment.objects.create(order=order)
        self.assertEqual(count_queries(), baseline)

    def test_detail_keeps_full_payload(self):
        response = self.client.get(f"/api/orders/{self.order.id}")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        for field in LIST_EXCLUDED_ORDER_FIELDS:
            self.assertIn(field, data, f"detail phải giữ field '{field}'")

    def test_order_list_exposes_koc_order_type(self):
        """Field phải có mặt trong response list, mặc định rỗng."""
        resp = self.client.post("/api/orders", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        row = next(r for r in resp.data["results"] if r["id"] == self.order.id)
        self.assertEqual(row["koc_order_type"], "")

    def test_inline_update_sets_koc_order_type(self):
        resp = self.client.patch(
            f"/api/orders/{self.order.id}", {"koc_order_type": "sample_gift"}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.order.refresh_from_db()
        self.assertEqual(self.order.koc_order_type, "sample_gift")

    def test_inline_update_rejects_unknown_koc_order_type(self):
        """Giá trị ngoài 3 mã phải bị từ chối — choices trên model lo việc này."""
        resp = self.client.patch(
            f"/api/orders/{self.order.id}", {"koc_order_type": "xyz"}, format="json"
        )
        self.assertEqual(resp.status_code, 400, resp.content)


def _create_orders(channel, count, prefix):
    """Mỗi đơn có customer riêng + 2 item riêng — tối đa hoá cơ hội lộ N+1."""
    for i in range(count):
        customer = Customer.objects.create(
            full_name=f"Khach {prefix}{i}",
            phone_raw=f"09090{prefix}{i:03d}",
            phone_normalized=f"09090{prefix}{i:03d}",
        )
        order = Order.objects.create(
            channel=channel,
            customer=customer,
            channel_owner=f"owner-{prefix}{i}",
            name=f"Order {prefix}{i}",
            phone=customer.phone_raw,
            product=f"San pham {prefix}{i}",
            total_amount="150000.00",
        )
        for j in range(2):
            product = Product.objects.create(
                name=f"SP {prefix}{i}-{j}", sku=f"SKU-{prefix}{i}-{j}", price="75000"
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=product.name,
                sku=product.sku,
                quantity=1,
                unit_price="75000.00",
                line_total="75000.00",
            )


class OrdersListQueryCountTests(TestCase):
    """Lưới chống N+1 cho endpoint danh sách đơn (audit 2026-07-23 mục T1).

    Bối cảnh: codebase có 177 điểm select_related/prefetch_related nhưng chỉ 5 file test
    đo số query — tối ưu đã làm nhưng KHÔNG có gì ngăn nó bị phá âm thầm. Thêm một field
    vào serializer là đủ để mỗi đơn sinh thêm một query, và không test nào đỏ.

    Cách đo ở đây KHÔNG chốt trần cứng theo một con số ma thuật (dễ vỡ khi thêm filter hợp
    lệ). Thay vào đó so sánh số query giữa 2 tập dữ liệu khác kích thước: số query phải
    KHÔNG tăng theo số đơn. Đó chính là định nghĩa của N+1.

    Số query của /api/orders phải độc lập với số đơn trả về.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-orders-nplus1")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(
            code="nplus1-ch", name="N+1 Channel", owner="N+1 Channel"
        )

    def _count_queries_for_list(self, limit):
        # /api/orders là POST (bộ lọc nằm trong body), không phải GET.
        payload = {"limit": str(limit), "offset": "0"}

        # Lần 1 không đo: làm nóng cache RBAC/permission để phép đo chỉ còn phần
        # query của queryset + serializer.
        response = self.client.post("/api/orders", payload, format="json")
        self.assertEqual(response.status_code, 200)

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.post("/api/orders", payload, format="json")
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries), ctx.captured_queries

    def test_query_count_does_not_grow_with_number_of_orders(self):
        _create_orders(self.channel, 3, "a")
        few_count, _ = self._count_queries_for_list(limit=50)

        _create_orders(self.channel, 12, "b")
        many_count, many_queries = self._count_queries_for_list(limit=50)

        self.assertGreater(
            many_count, 0, "không bắt được query nào — kiểm tra lại cách đo"
        )
        self.assertLessEqual(
            many_count,
            few_count,
            "Số query TĂNG khi thêm đơn → N+1 ở danh sách đơn.\n"
            f"3 đơn: {few_count} query · 15 đơn: {many_count} query.\n"
            "20 query đầu của lần chạy 15 đơn:\n"
            + "\n".join(q["sql"][:150] for q in many_queries[:20]),
        )


# Cache status_counts: LocMemCache riêng từng test (backend/conftest.py).
@pytest.mark.usefixtures("isolated_cache")
class OrdersStatusCountsTests(TestCase):
    """status_counts trên list endpoints (/api/orders, /api/orders/koc).

    Thanh chip lọc trạng thái đơn (Order.status) đọc `status_counts` từ response. Mỗi key
    lifecycle (0..5, 7..13) là số đơn non-cancelled ứng status đó; key "6" là số đơn đã hủy
    (invoice_status=2 OR status=6) — vì trường status không bao giờ lọc ra đơn hủy. Count
    được tính TRƯỚC khi áp bộ lọc status/invoice_status nên không đổi khi user chọn 1 chip.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-status-counts")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(code="sc-ch", name="SC Ch", owner="SC Ch")
        self.customer = Customer.objects.create(
            full_name="KH SC",
            phone_raw="0900000010",
            phone_normalized="0900000010",
        )
        self.o_new = self._order("0900000011", status=OrderStatus.NEW)
        self.o_delivery = self._order("0900000012", status=OrderStatus.IN_DELIVERY)
        self.o_delivered = self._order("0900000013", status=OrderStatus.DELIVERED_SUCCESS)
        # Đơn hủy nguồn hệ thống (status=6).
        self.o_cancelled_status = self._order("0900000014", status=OrderStatus.CANCELLED)
        # Đơn hủy nguồn hóa đơn (invoice_status=2) nhưng status vẫn NEW.
        self.o_cancelled_invoice = self._order(
            "0900000015", status=OrderStatus.NEW, invoice_status=2
        )

    def _order(self, phone, *, status, invoice_status=None):
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name=f"Order {phone}",
            phone=phone,
            status=status,
            invoice_status=invoice_status,
            order_source="landing",
        )

    def _counts(self, endpoint, body=None):
        response = self.client.post(
            endpoint, {"limit": "100", "offset": "0", **(body or {})}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertIn("status_counts", data)
        return data["status_counts"], {item["id"] for item in data["results"]}

    def test_lifecycle_and_cancelled_counts(self):
        counts, _ = self._counts("/api/orders")
        self.assertEqual(counts.get("0"), 1)  # chỉ o_new (o_cancelled_invoice bị loại)
        self.assertEqual(counts.get("2"), 1)
        self.assertEqual(counts.get("3"), 1)
        # "6" = cả 2 đơn hủy (status=6 và invoice_status=2).
        self.assertEqual(counts.get("6"), 2)

    def test_counts_ignore_selected_status_filter(self):
        # Chọn 1 chip lifecycle không được làm đổi số đếm (đếm trước khi áp status).
        counts, ids = self._counts("/api/orders", {"status": ["2"]})
        self.assertEqual(ids, {self.o_delivery.id})
        self.assertEqual(counts.get("0"), 1)
        self.assertEqual(counts.get("2"), 1)
        self.assertEqual(counts.get("6"), 2)

    def test_cancelled_chip_lists_cancelled_orders(self):
        # Chip "Đã hủy" gửi invoice_status=2 -> hiển thị cả status=6 và invoice_status=2.
        _, ids = self._counts("/api/orders", {"invoice_status": ["2"]})
        self.assertEqual(ids, {self.o_cancelled_status.id, self.o_cancelled_invoice.id})

    def test_status_counts_present_on_koc(self):
        counts, _ = self._counts("/api/orders/koc")
        self.assertIn("6", counts)

    def test_overlap_cancelled_order_counted_once(self):
        # Đơn hủy CẢ HAI nguồn (status=6 VÀ invoice_status=2) chỉ được đếm 1 lần ở
        # key "6" (OR chứ không phải cộng dồn 2 nhánh), và không rò vào key lifecycle
        # nào — regression cho bản aggregate 1-query (CANCELLED_ORDER_Q).
        self._order("0900000016", status=OrderStatus.CANCELLED, invoice_status=2)
        counts, _ = self._counts("/api/orders")
        # 2 đơn hủy sẵn có ở setUp + đơn overlap đếm đúng 1 lần = 3.
        self.assertEqual(counts.get("6"), 3)
        # Lifecycle không đổi so với test_lifecycle_and_cancelled_counts.
        self.assertEqual(counts.get("0"), 1)
        self.assertEqual(counts.get("2"), 1)
        self.assertEqual(counts.get("3"), 1)

    def test_status_counts_for_uses_single_query(self):
        # Audit followup 2026-07 mục 4.2: status_counts_for gộp lifecycle + cancelled
        # về 1 aggregate() thay vì 2 query materialize riêng. Khoá lại bằng
        # assertNumQueries để không vô tình quay lại pattern 2-query cũ.
        with CaptureQueriesContext(connection) as ctx:
            OrderRepository.status_counts_for({})
        self.assertEqual(len(ctx.captured_queries), 1, ctx.captured_queries)

    # Cache LocMem riêng từng test qua fixture `isolated_cache` (class) — không đụng
    # Redis thật của test env; ở đây chỉ bật TTL.
    @override_settings(ORDERS_LIST_COUNT_CACHE_TTL=30)
    def test_status_counts_cached_within_ttl(self):
        # Bão gate 2026-08-04 09:13: N tab cùng bộ lọc chạy lại aggregate toàn tập
        # y hệt nhau mỗi lần load list. Bật TTL thì lần 2 phải là cache hit (0 query)
        # và đơn tạo trong TTL chưa hiện vào chip (độ trễ chấp nhận như list_count).
        first = OrderRepository.status_counts_for({})
        with CaptureQueriesContext(connection) as ctx:
            second = OrderRepository.status_counts_for({})
        self.assertEqual(second, first)
        self.assertEqual(len(ctx.captured_queries), 0, ctx.captured_queries)

        self._order("0900000017", status=OrderStatus.NEW)
        self.assertEqual(OrderRepository.status_counts_for({}), first)

    @override_settings(ORDERS_LIST_COUNT_CACHE_TTL=30)
    def test_status_counts_cache_key_separates_filters(self):
        # Hai bộ lọc khác nhau không được đâm chung một key (channel scope nằm trong
        # filters nên key cũng tách theo scope user).
        all_counts = OrderRepository.status_counts_for({})
        filtered = OrderRepository.status_counts_for({"channel": ["kenh-khong-ton-tai"]})
        self.assertEqual(all_counts.get("6"), 2)
        self.assertEqual(filtered.get("6"), 0)

    @override_settings(ORDERS_LIST_COUNT_CACHE_TTL=30)
    def test_landing_sales_status_counts_cached_within_ttl(self):
        # Landing là list được gọi dày nhất giờ thường — badge GROUP BY + count phải
        # cache hit (0 query) ở lần gọi thứ 2 cùng bộ lọc trong TTL.
        _, total1, _, _, counts1 = OrderRepository.list_orders_with_status_counts({})
        with CaptureQueriesContext(connection) as ctx:
            _, total2, _, _, counts2 = OrderRepository.list_orders_with_status_counts({})
        self.assertEqual((total2, counts2), (total1, counts1))
        self.assertEqual(len(ctx.captured_queries), 0, ctx.captured_queries)

    @override_settings(ORDERS_LIST_COUNT_CACHE_TTL=30)
    def test_landing_sheet_stats_cached_within_ttl(self):
        # sheet_stats (uncalled/appointment_today) là aggregate toàn tập — trong TTL
        # đơn mới chưa được cộng vào (cache hit), cùng độ trễ như count/badge.
        body = {"limit": "50", "offset": "0", "sheet": "1"}
        r1 = self.client.post("/api/orders/landing", body, format="json").json()
        self.assertIn("sheet_stats", r1)
        self._order("0900000019", status=OrderStatus.NEW)
        r2 = self.client.post("/api/orders/landing", body, format="json").json()
        self.assertEqual(r2["sheet_stats"]["uncalled"], r1["sheet_stats"]["uncalled"])

    @override_settings(ORDERS_LIST_COUNT_CACHE_TTL=30)
    def test_sheet3_counts_cached_within_ttl(self):
        # Sheet 3 duyệt cả tập bằng Python cho temperature_counts — trong TTL các
        # aggregate (count/temperature/sales_status) phải đứng yên dù có đơn mới;
        # hết cache thì đơn mới hiện ra.
        body = {"limit": "50", "offset": "0"}
        r1 = self.client.post("/api/orders/sheet3", body, format="json").json()
        self._order("0900000018", status=OrderStatus.NEW)
        r2 = self.client.post("/api/orders/sheet3", body, format="json").json()
        self.assertEqual(r2["count"], r1["count"])
        self.assertEqual(r2["temperature_counts"], r1["temperature_counts"])
        self.assertEqual(r2["sales_status_counts"], r1["sales_status_counts"])
        self.isolated_cache.clear()  # hết TTL (giả lập) → đơn mới phải hiện ra
        r3 = self.client.post("/api/orders/sheet3", body, format="json").json()
        self.assertEqual(r3["count"], r1["count"] + 1)

    def test_status_counts_absent_on_landing(self):
        # Thanh chip trạng thái chỉ có ở trang Đơn hàng và KOC — trang Landing không
        # hiển thị chip nên không tốn thêm aggregate đếm. (Endpoint /api/orders/outside
        # đã bị gỡ 2026-08-11, xem test_rbac_sections.py::OutsideOrdersSectionRemovedTests.)
        response = self.client.post(
            "/api/orders/landing", {"limit": "100", "offset": "0"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("status_counts", response.json())


class LandingSheetRoutingTests(TestCase):
    """Đơn đi đúng MỘT tab Landing và không nhảy tab khi sale bổ sung thông tin.

    Khoá hành vi của yêu cầu 2026-08-11: đơn ingest về không có địa chỉ
    (has_address=False) ở lại tab Tư vấn kể cả sau khi điền đủ Tên/SĐT/Địa chỉ,
    và KHÔNG xuất hiện ở Landing/Messenger xác nhận đơn.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-sheet-routing")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(code="fb-route", name="FB Route", owner="FB Route")
        self.customer = Customer.objects.create(
            full_name="KH Route", phone_raw="0909555001", phone_normalized="0909555001"
        )

    def _order(self, **kwargs):
        defaults = {
            "channel": self.channel,
            "customer": self.customer,
            "order_source": "landing",
            "name": "Nguyen A",
            "phone": "0909555001",
            "raw_address": "1 Nguyen Trai, Q1",
        }
        return Order.objects.create(**{**defaults, **kwargs})

    def _ids(self, endpoint, body=None):
        response = self.client.post(
            endpoint, {"limit": "100", "offset": "0", **(body or {})}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {item["id"] for item in response.json()["results"]}

    def test_sticky_order_stays_in_sheet3_after_address_filled(self):
        # Đơn đổ về không địa chỉ, sale đã điền đủ thông tin sau đó.
        sticky = self._order(has_address=False)

        self.assertIn(sticky.id, self._ids("/api/orders/sheet3"))
        self.assertNotIn(sticky.id, self._ids("/api/orders/landing", {"sheet": "1"}))
        self.assertNotIn(sticky.id, self._ids("/api/orders/landing", {"sheet": "2"}))

    def test_sticky_messenger_order_not_in_sheet2(self):
        sticky = self._order(order_source="messenger", has_address=False)

        self.assertIn(sticky.id, self._ids("/api/orders/sheet3"))
        self.assertNotIn(sticky.id, self._ids("/api/orders/landing", {"sheet": "2"}))

    def test_complete_order_with_ingest_address_goes_to_sheet1_only(self):
        normal = self._order()

        self.assertIn(normal.id, self._ids("/api/orders/landing", {"sheet": "1"}))
        self.assertNotIn(normal.id, self._ids("/api/orders/sheet3"))

    def test_order_missing_phone_still_in_sheet3(self):
        no_phone = self._order(phone="")

        self.assertIn(no_phone.id, self._ids("/api/orders/sheet3"))
        self.assertNotIn(no_phone.id, self._ids("/api/orders/landing", {"sheet": "1"}))

    def test_summary_tab_shows_every_landing_order(self):
        # Tab "Tổng hợp" không gửi `sheet` → thấy cả đơn Tư vấn lẫn đơn xác nhận,
        # nên không đơn nào biến mất khỏi giao diện.
        sticky = self._order(has_address=False)
        normal = self._order()

        summary_ids = self._ids("/api/orders/landing")
        self.assertIn(sticky.id, summary_ids)
        self.assertIn(normal.id, summary_ids)

    def test_sheet1_and_sheet3_are_disjoint(self):
        self._order(has_address=False)
        self._order()
        self._order(phone="")

        sheet1 = self._ids("/api/orders/landing", {"sheet": "1"})
        sheet3 = self._ids("/api/orders/sheet3")
        self.assertEqual(sheet1 & sheet3, set())

    def test_closed_order_stays_on_consulting_board(self):
        # Yêu cầu bổ sung 2026-08-11: đơn đã chốt ("closed") vẫn ở lại board Tư
        # vấn cho sale nhìn thấy, không còn rời board như trước.
        sticky = self._order(has_address=False)
        OrderAssessment.objects.create(order=sticky, sales_status_code="closed")

        self.assertIn(sticky.id, self._ids("/api/orders/sheet3"))
        self.assertNotIn(sticky.id, self._ids("/api/orders/landing", {"sheet": "1"}))

    def test_created_order_status_stays_on_consulting_board(self):
        # "created_order" là một trạng thái chốt khác trong CLOSED_SALES_STATUSES —
        # phải cư xử giống "closed": ở lại board Tư vấn.
        sticky = self._order(has_address=False)
        OrderAssessment.objects.create(order=sticky, sales_status_code="created_order")

        self.assertIn(sticky.id, self._ids("/api/orders/sheet3"))
        self.assertNotIn(sticky.id, self._ids("/api/orders/landing", {"sheet": "1"}))

    def test_archived_order_leaves_consulting_board(self):
        # "archived" là kết quả của cron autoclose_followups — đây là đường DUY
        # NHẤT còn lại khiến đơn rời board Tư vấn.
        sticky = self._order(has_address=False)
        OrderAssessment.objects.create(order=sticky, sales_status_code="archived")

        self.assertNotIn(sticky.id, self._ids("/api/orders/sheet3"))
