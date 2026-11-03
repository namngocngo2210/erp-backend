from datetime import datetime

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, ChannelType
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin


class OrderListFiltersApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-order-list-filters")
        self.channel = Channel.objects.create(code="lp-order-filters", name="Landing Order Filters")
        self.customer = Customer.objects.create(
            full_name="Khach Loc San Pham",
            phone_raw="0909000001",
            phone_normalized="0909000001",
        )
        self.product_a = Product.objects.create(name="Bot ngam chan A", sku="BNC-A", price="120000")
        self.product_b = Product.objects.create(name="Bot ngam chan B", sku="BNC-B", price="90000")

        self.order_a = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            channel_owner="owner-a",
            name="Order A",
            phone="0909000001",
            shipping_code="SHIP-A-001",
            product="Bot ngam chan A",
            total_amount="120000.00",
        )
        OrderItem.objects.create(
            order=self.order_a,
            product=self.product_a,
            product_name=self.product_a.name,
            sku=self.product_a.sku,
            quantity=1,
            unit_price="120000.00",
            line_total="120000.00",
        )

        self.order_b = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            channel_owner="owner-b",
            name="Order B",
            phone="0909000002",
            shipping_code="SHIP-B-002",
            product="Bot ngam chan B",
            total_amount="90000.00",
        )
        OrderItem.objects.create(
            order=self.order_b,
            product=self.product_b,
            product_name=self.product_b.name,
            sku=self.product_b.sku,
            quantity=1,
            unit_price="90000.00",
            line_total="90000.00",
        )

        self.order_without_items = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            channel_owner="owner-c",
            name="Order Combo",
            phone="0909000003",
            shipping_code="SHIP-C-003",
            product="Combo Dac Biet",
            total_amount="150000.00",
        )

    def test_filter_orders_by_product_id(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders",
            {"product": str(self.product_a.id), "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertEqual(ids, {self.order_a.id})

    def test_filter_orders_by_product_keyword(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders",
            {"product": "Combo", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertEqual(ids, {self.order_without_items.id})

    def test_filter_orders_by_product_sku(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders",
            {"product": self.product_b.sku, "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertEqual(ids, {self.order_b.id})

    def test_search_orders_by_shipping_code(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders",
            {"q": "SHIP-B-002", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertEqual(ids, {self.order_b.id})

    def test_search_orders_by_phone(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders",
            {"q": "0909000001", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertIn(self.order_a.id, ids)

    def test_search_orders_by_phone_international_variants(self):
        # A phone stored in domestic 0xxxxxxxxx form must also be findable when
        # the user types it with the +84 / 84 / 0084 country-code prefix or with
        # spaces — those are common copy-paste shapes from CRM/landing forms.
        self.client.force_authenticate(user=self.admin)
        for variant in ("+84909000001", "84909000001", "0084909000001", "0909 000 001"):
            with self.subTest(variant=variant):
                response = self.client.post(
                    "/api/orders",
                    {"q": variant, "limit": "100", "offset": "0"},
                    format="json",
                )
                self.assertEqual(response.status_code, 200)
                ids = {item["id"] for item in response.json()["results"]}
                self.assertIn(self.order_a.id, ids, f"variant={variant!r} did not match order_a")

    def test_search_orders_by_partial_phone(self):
        # Typing only part of a number (common while typing) must still match via the
        # phone_normalized substring branch.
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders",
            {"q": "0909000", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertIn(self.order_a.id, ids)
        self.assertIn(self.order_b.id, ids)

    def test_partial_phone_matches_when_stored_formatted(self):
        # The order's raw phone is stored with +84/spaces, but phone_normalized holds the
        # canonical digits — a domestic partial search must still find it.
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Formatted Phone",
            phone="+84 912 345 678",
            total_amount="0.00",
        )
        self.assertEqual(order.phone_normalized, "0912345678")
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders",
            {"q": "091234", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertIn(order.id, ids)

    def test_search_orders_by_invoice_code(self):
        Order.objects.filter(pk=self.order_a.pk).update(invoice_code="INV-A-7788")
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders",
            {"q": "INV-A-7788", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertEqual(ids, {self.order_a.id})

    def test_search_orders_by_old_invoice_code(self):
        Order.objects.filter(pk=self.order_b.pk).update(old_invoice_code="OLD-B-9911")
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders",
            {"q": "OLD-B-9911", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertEqual(ids, {self.order_b.id})

    def test_short_code_keyword_is_ignored(self):
        # A 1-2 char code keyword can't use the trigram GIN index and would seq-scan the
        # whole table while matching a huge slice, so it's skipped. The same substring at
        # >= 3 chars must still match — this pins the guard exactly at the 3-char boundary.
        Order.objects.filter(pk=self.order_a.pk).update(invoice_code="HD123456")
        self.client.force_authenticate(user=self.admin)

        too_short = self.client.post(
            "/api/orders",
            {"q": "HD", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(too_short.status_code, 200)
        self.assertEqual([item["id"] for item in too_short.json()["results"]], [])

        long_enough = self.client.post(
            "/api/orders",
            {"q": "HD1", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(long_enough.status_code, 200)
        ids = {item["id"] for item in long_enough.json()["results"]}
        self.assertEqual(ids, {self.order_a.id})

    def test_save_keeps_phone_normalized_in_sync(self):
        # Full save normalizes any input shape to the canonical 0xxxxxxxxx form.
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Norm",
            phone="+84 912 345 678",
            total_amount="0.00",
        )
        order.refresh_from_db()
        self.assertEqual(order.phone_normalized, "0912345678")

        # save(update_fields=["phone"]) must still persist the recomputed value.
        order.phone = "0987654321"
        order.save(update_fields=["phone", "updated_at"])
        order.refresh_from_db()
        self.assertEqual(order.phone_normalized, "0987654321")

        # And the order is then findable via the canonical phone search.
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders",
            {"q": "0987654321", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertIn(order.id, ids)

    def test_date_filter_uses_local_day_boundaries(self):
        # Không có search: filter ngày hoạt động bình thường, biên ngày tính theo
        # múi giờ local (order ở 30/04 23:59 local nằm ngoài khoảng tháng 5).
        self.client.force_authenticate(user=self.admin)
        may_first_local = timezone.make_aware(datetime(2026, 5, 1, 0, 30), timezone.get_current_timezone())
        april_last_local = timezone.make_aware(datetime(2026, 4, 30, 23, 59), timezone.get_current_timezone())
        Order.objects.filter(pk=self.order_a.pk).update(created_at=may_first_local)
        Order.objects.filter(pk=self.order_b.pk).update(created_at=april_last_local)

        response = self.client.post(
            "/api/orders",
            {
                "date_from": "2026-05-01",
                "date_to": "2026-05-31",
                "limit": "100",
                "offset": "0",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertIn(self.order_a.id, ids)
        self.assertNotIn(self.order_b.id, ids)

    def test_search_by_phone_ignores_date_filter(self):
        # Khi search (q) theo SĐT, filter ngày bị bỏ qua để luôn quét toàn bộ
        # lịch sử — tránh tình huống đơn ngoài tháng hiện tại (hoặc created_at lỗi)
        # "có trong DB mà search không ra". Đây là hành vi cố ý, đồng nhất với
        # cách show_orders cũng bị bỏ qua khi đang search.
        self.client.force_authenticate(user=self.admin)
        may_first_local = timezone.make_aware(datetime(2026, 5, 1, 0, 30), timezone.get_current_timezone())
        april_last_local = timezone.make_aware(datetime(2026, 4, 30, 23, 59), timezone.get_current_timezone())
        Order.objects.filter(pk=self.order_a.pk).update(created_at=may_first_local)
        Order.objects.filter(pk=self.order_b.pk).update(created_at=april_last_local, phone="0909000001")

        response = self.client.post(
            "/api/orders",
            {
                "q": "0909000001",
                "date_from": "2026-05-01",
                "date_to": "2026-05-31",
                "limit": "100",
                "offset": "0",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        # Cả hai đơn đều ra dù order_b nằm ngoài khoảng ngày đã truyền.
        self.assertIn(self.order_a.id, ids)
        self.assertIn(self.order_b.id, ids)

    def test_filter_orders_by_customer_return_yes(self):
        self.client.force_authenticate(user=self.admin)
        # show_orders=True is required: /api/orders defaults show_orders="true" and
        # only returns visible orders. The model default is True but we set it
        # explicitly so a future default change cannot silently break this test.
        Order.objects.filter(
            id__in=[self.order_a.id, self.order_b.id, self.order_without_items.id]
        ).update(show_orders=True)
        Order.objects.filter(id=self.order_a.id).update(customer_return=True)
        response = self.client.post(
            "/api/orders",
            {"customer_return": "yes", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        ids = {item["id"] for item in results}
        self.assertEqual(ids, {self.order_a.id})
        self.assertTrue(results[0]["customer_return"])

    def test_filter_orders_by_customer_return_no_and_empty(self):
        self.client.force_authenticate(user=self.admin)
        # show_orders=True is required: /api/orders defaults show_orders="true" and
        # only returns visible orders. The model default is True but we set it
        # explicitly so a future default change cannot silently break this test.
        Order.objects.filter(
            id__in=[self.order_a.id, self.order_b.id, self.order_without_items.id]
        ).update(show_orders=True)
        Order.objects.filter(id=self.order_a.id).update(customer_return=True)
        resp_no = self.client.post(
            "/api/orders",
            {"customer_return": "no", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(resp_no.status_code, 200)
        ids_no = {item["id"] for item in resp_no.json()["results"]}
        self.assertEqual(ids_no, {self.order_b.id, self.order_without_items.id})

        resp_all = self.client.post(
            "/api/orders",
            {"customer_return": "", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(resp_all.status_code, 200)
        ids_all = {item["id"] for item in resp_all.json()["results"]}
        self.assertEqual(
            ids_all, {self.order_a.id, self.order_b.id, self.order_without_items.id}
        )

    def test_inline_update_sets_customer_return(self):
        self.client.force_authenticate(user=self.admin)
        # show_orders=True is required: /api/orders defaults show_orders="true" and
        # only returns visible orders. The model default is True but we set it
        # explicitly so a future default change cannot silently break this test.
        Order.objects.filter(
            id__in=[self.order_a.id, self.order_b.id, self.order_without_items.id]
        ).update(show_orders=True)

        # True path: PATCH sets customer_return=True and persists it.
        response = self.client.patch(
            f"/api/orders/{self.order_a.id}",
            {"customer_return": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["customer_return"])
        self.order_a.refresh_from_db()
        self.assertTrue(self.order_a.customer_return)

        # False (round-trip): PATCH sets customer_return back to False and persists it.
        response_false = self.client.patch(
            f"/api/orders/{self.order_a.id}",
            {"customer_return": False},
            format="json",
        )
        self.assertEqual(response_false.status_code, 200)
        self.assertFalse(response_false.json()["customer_return"])
        self.order_a.refresh_from_db()
        self.assertIs(self.order_a.customer_return, False)


class KocOrderCustomerReturnFilterTests(TestCase):
    """Verify that /api/orders/koc honours the customer_return filter."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-koc-cr")
        # /api/orders/koc hard-locks order_source="koc", so the channel must carry
        # id=KOC_CHANNEL_ID (mirroring the setup in test_koc_orders_api.py).
        self.koc_channel = Channel.objects.create(
            id=settings.KOC_CHANNEL_ID, code="koc", name="hàng mẫu koc"
        )
        self.customer = Customer.objects.create(
            full_name="Khach KOC CR",
            phone_raw="0922222222",
            phone_normalized="0922222222",
        )
        # KOC order with customer_return=True — must appear in the filtered result.
        self.koc_cr_order = Order.objects.create(
            channel=self.koc_channel,
            customer=self.customer,
            order_source="koc",
            customer_return=True,
            name="KOC CR Order",
            phone="0922222222",
            shipping_code="SHIPKOCCR",
        )
        # KOC order without customer_return — must NOT appear in the filtered result.
        self.koc_no_cr_order = Order.objects.create(
            channel=self.koc_channel,
            customer=self.customer,
            order_source="koc",
            customer_return=False,
            name="KOC No CR Order",
            phone="0922222223",
            shipping_code="SHIPKOCNOCR",
        )

    def test_filter_koc_orders_by_customer_return(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/orders/koc",
            {"customer_return": "yes", "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertIn(self.koc_cr_order.id, ids)
        self.assertNotIn(self.koc_no_cr_order.id, ids)


class OrdersListNewFiltersTests(TestCase):
    """Bộ lọc order_type trên list endpoints (/api/orders, /api/orders/landing).
    Idiom mirror từ test_return_slip_filter.py: POST body {limit, offset, **filter},
    response {"results": [...], "count": n}.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-list-newfilters")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(code="lst-ch", name="List Ch", owner="List Ch")
        self.customer = Customer.objects.create(
            full_name="KH List",
            phone_raw="0900000003",
            phone_normalized="0900000003",
        )
        self.o_two_way = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Two Way",
            phone="0900000003",
            order_type="same_product",
            order_source="landing",
        )
        self.o_plain = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Plain",
            phone="0900000004",
            order_type="normal",
            order_source="landing",
        )

    def _ids(self, endpoint, body):
        response = self.client.post(
            endpoint, {"limit": "100", "offset": "0", **body}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {item["id"] for item in response.json()["results"]}

    def test_filter_order_type_on_orders_list(self):
        self.assertEqual(
            self._ids("/api/orders", {"order_type": ["same_product"]}),
            {self.o_two_way.id},
        )

    def test_filter_order_type_on_landing_list_parity(self):
        ids = self._ids("/api/orders/landing", {"order_type": ["same_product"]})
        self.assertNotIn(self.o_plain.id, ids)
        self.assertIn(self.o_two_way.id, ids)


class OrdersListKocOrderTypeFilterTests(TestCase):
    """Bộ lọc koc_order_type trên /api/orders (Task 4 — mirror order_type ở trên,
    khác biệt duy nhất: koc_order_type không có token "none" đặc biệt như
    shipping_type nên rỗng luôn nghĩa là "không lọc").
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-koc-order-type")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(code="lst-koc-type", name="List Koc Type")
        self.customer = Customer.objects.create(
            full_name="KH Koc Type",
            phone_raw="0900000005",
            phone_normalized="0900000005",
        )
        self._order_seq = 0

    def _make_order(self, koc_order_type=""):
        self._order_seq += 1
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name=f"Order Koc Type {self._order_seq}",
            phone=f"09000005{self._order_seq:02d}",
            koc_order_type=koc_order_type,
        )

    def test_filter_koc_order_type_single_value(self):
        sample = self._make_order(koc_order_type="sample_gift")
        self._make_order(koc_order_type="renewal")
        resp = self.client.post("/api/orders", {"koc_order_type": ["sample_gift"]}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([r["id"] for r in resp.data["results"]], [sample.id])

    def test_filter_koc_order_type_multiple_values_is_or(self):
        sample = self._make_order(koc_order_type="sample_gift")
        renewal = self._make_order(koc_order_type="renewal")
        self._make_order(koc_order_type="recall")
        resp = self.client.post(
            "/api/orders", {"koc_order_type": ["sample_gift", "renewal"]}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            sorted(r["id"] for r in resp.data["results"]), sorted([sample.id, renewal.id])
        )

    def test_filter_koc_order_type_blank_means_no_filter(self):
        """Chuỗi/list rỗng = không lọc, KHÔNG phải lọc ra đơn có nhãn rỗng."""
        a = self._make_order(koc_order_type="sample_gift")
        b = self._make_order()
        resp = self.client.post("/api/orders", {"koc_order_type": []}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(sorted(r["id"] for r in resp.data["results"]), sorted([a.id, b.id]))


class KocOrdersListKocOrderTypeFilterTests(TestCase):
    """Parity test cho finding review: FE chỉ hiện ô lọc koc_order_type khi isKoc,
    mà khi isKoc thì use-orders-table.ts bắn thẳng vào /api/orders/koc — không phải
    /api/orders. list_koc_orders dựng dict filter RIÊNG (không dùng build_orders_filters)
    nên từng thiếu key koc_order_type: chọn "Tặng mẫu" trên đúng trang KOC thì danh
    sách không đổi, không lỗi, không log. Test này bắn thẳng vào /api/orders/koc để
    bắt hồi quy nếu key filter bị xoá lần nữa.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-koc-list-koc-order-type")
        self.client.force_authenticate(user=self.admin)
        # /api/orders/koc hard-locks order_source="koc", channel phải mang
        # id=KOC_CHANNEL_ID (mirror KocOrderCustomerReturnFilterTests ở trên).
        self.koc_channel = Channel.objects.create(
            id=settings.KOC_CHANNEL_ID, code="koc-type-filter", name="hàng mẫu koc"
        )
        self.customer = Customer.objects.create(
            full_name="KH Koc Type Endpoint",
            phone_raw="0900000006",
            phone_normalized="0900000006",
        )
        self._order_seq = 0

    def _make_order(self, koc_order_type=""):
        self._order_seq += 1
        return Order.objects.create(
            channel=self.koc_channel,
            customer=self.customer,
            order_source="koc",
            name=f"Order Koc Endpoint Type {self._order_seq}",
            phone=f"09000006{self._order_seq:02d}",
            koc_order_type=koc_order_type,
        )

    def test_filter_koc_order_type_on_koc_orders_endpoint(self):
        sample = self._make_order(koc_order_type="sample_gift")
        self._make_order(koc_order_type="renewal")
        resp = self.client.post(
            "/api/orders/koc",
            {"koc_order_type": ["sample_gift"], "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([r["id"] for r in resp.data["results"]], [sample.id])


class OrdersListChannelTypeFilterTests(TestCase):
    """Bộ lọc `channel_type` (Shopee/TikTok/Facebook/Web) trên /api/orders.

    Lọc theo CODE của loại kênh chứ không theo id (id sinh tự động, khác nhau
    giữa các môi trường). Token "none" = kênh CHƯA phân loại — backfill của
    migration channels_core/0024 cố ý để một phần kênh NULL nên nhóm này có thật.
    """

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=make_admin("admin-channel-type-filter"))
        self.shopee = ChannelType.objects.get(code="shopee")
        self.tiktok = ChannelType.objects.get(code="tiktok")
        self.ch_shopee = Channel.objects.create(
            code="ct-f-shopee", name="Shopee CH", channel_type=self.shopee
        )
        self.ch_tiktok = Channel.objects.create(
            code="ct-f-tiktok", name="TikTok CH", channel_type=self.tiktok
        )
        self.ch_none = Channel.objects.create(code="ct-f-none", name="Chưa phân loại")
        self.customer = Customer.objects.create(
            full_name="KH Channel Type",
            phone_raw="0900000007",
            phone_normalized="0900000007",
        )
        self._seq = 0

    def _make_order(self, channel):
        self._seq += 1
        return Order.objects.create(
            channel=channel,
            customer=self.customer,
            name=f"Order CT {self._seq}",
            phone=f"09000007{self._seq:02d}",
        )

    def test_filter_single_channel_type(self):
        shopee_order = self._make_order(self.ch_shopee)
        self._make_order(self.ch_tiktok)
        resp = self.client.post("/api/orders", {"channel_type": ["shopee"]}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([r["id"] for r in resp.data["results"]], [shopee_order.id])

    def test_filter_multiple_values_is_or(self):
        shopee_order = self._make_order(self.ch_shopee)
        tiktok_order = self._make_order(self.ch_tiktok)
        self._make_order(self.ch_none)
        resp = self.client.post(
            "/api/orders", {"channel_type": ["shopee", "tiktok"]}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            sorted(r["id"] for r in resp.data["results"]),
            sorted([shopee_order.id, tiktok_order.id]),
        )

    def test_none_token_matches_unclassified_channels(self):
        self._make_order(self.ch_shopee)
        unclassified = self._make_order(self.ch_none)
        resp = self.client.post("/api/orders", {"channel_type": ["none"]}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([r["id"] for r in resp.data["results"]], [unclassified.id])

    def test_none_combined_with_concrete_value(self):
        shopee_order = self._make_order(self.ch_shopee)
        unclassified = self._make_order(self.ch_none)
        self._make_order(self.ch_tiktok)
        resp = self.client.post(
            "/api/orders", {"channel_type": ["shopee", "none"]}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            sorted(r["id"] for r in resp.data["results"]),
            sorted([shopee_order.id, unclassified.id]),
        )

    def test_blank_means_no_filter(self):
        a = self._make_order(self.ch_shopee)
        b = self._make_order(self.ch_none)
        resp = self.client.post("/api/orders", {"channel_type": []}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(sorted(r["id"] for r in resp.data["results"]), sorted([a.id, b.id]))


class ChannelTypeFilterEndpointParityTests(TestCase):
    """Bộ lọc `channel_type` phải chạy trên CẢ BỐN endpoint danh sách đơn.

    Bài học đã cắn 3 lần (`has_recall_code`, `shipping_type`, `koc_order_type`):
    mỗi endpoint chép tay một dict filter riêng, thiếu một chỗ thì bộ lọc IM LẶNG
    không chạy — không lỗi, không log, danh sách y nguyên. Suite cũ vẫn xanh vì
    test filter chỉ bắn vào /api/orders.

    (`/api/orders/shipments` cố ý ngoài phạm vi: trang Vận đơn không có bộ lọc
    kênh nào cả.)
    """

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=make_admin("admin-ct-parity"))
        self.shopee = ChannelType.objects.get(code="shopee")
        self.tiktok = ChannelType.objects.get(code="tiktok")
        self.customer = Customer.objects.create(
            full_name="KH CT Parity",
            phone_raw="0900000008",
            phone_normalized="0900000008",
        )
        self._seq = 0

    def _make_order(self, channel, **kwargs):
        self._seq += 1
        return Order.objects.create(
            channel=channel,
            customer=self.customer,
            name=f"Order CT Parity {self._seq}",
            phone=f"09000008{self._seq:02d}",
            **kwargs,
        )

    def _assert_filters(self, url, keep_id, extra=None):
        payload = {"channel_type": ["shopee"], "limit": "100", "offset": "0"}
        payload.update(extra or {})
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual([r["id"] for r in resp.data["results"]], [keep_id], url)

    def test_koc_endpoint(self):
        # /api/orders/koc khoá cứng order_source="koc" + channel id=KOC_CHANNEL_ID
        # nên mọi đơn đều nằm trên đúng một kênh: không thể phân biệt hai đơn bằng
        # loại kênh. Thay vào đó kiểm "lọc shopee giữ đơn" và "lọc tiktok bỏ đơn"
        # trên cùng tập dữ liệu.
        koc_channel = Channel.objects.create(
            id=settings.KOC_CHANNEL_ID, code="ctp-koc", name="KOC", channel_type=self.shopee
        )
        keep = self._make_order(koc_channel, order_source="koc")
        self._assert_filters("/api/orders/koc", keep.id)

        resp = self.client.post(
            "/api/orders/koc",
            {"channel_type": ["tiktok"], "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["results"], [])

    def test_list_endpoints_filter_by_channel_type(self):
        """/api/orders, /landing, /sheet3 cùng một ngữ nghĩa lọc `channel_type`.
        Landing/sheet3 gom đơn CÓ order_source (trừ koc và đơn sàn) nên cần
        order_source="landing"; /api/orders không."""
        for url, order_source in (
            ("/api/orders", None),
            ("/api/orders/landing", "landing"),
            ("/api/orders/sheet3", "landing"),
        ):
            with self.subTest(url=url):
                extra = {"order_source": order_source} if order_source else {}
                tag = url.rsplit("/", 1)[-1]
                keep = self._make_order(
                    Channel.objects.create(code=f"ctp-{tag}-a", name="A", channel_type=self.shopee),
                    **extra,
                )
                other = self._make_order(
                    Channel.objects.create(code=f"ctp-{tag}-b", name="B", channel_type=self.tiktok),
                    **extra,
                )
                self._assert_filters(url, keep.id)
                # Landing và sheet3 cùng đọc một tập đơn → xoá mềm đơn của vòng này để
                # vòng sau không thấy đơn shopee của vòng trước.
                Order.objects.filter(pk__in=[keep.pk, other.pk]).update(deleted_at=timezone.now())

    def test_export_filters_carry_channel_type(self):
        """Xuất Excel theo bộ lọc dùng build_orders_filters — phải cùng tập với list."""
        from apps.orders.transport.views.order_views_impl._view_helpers import (
            build_orders_filters,
        )

        class _Req:
            method = "POST"
            data = {"channel_type": ["shopee", "none"]}

        self.assertEqual(build_orders_filters(_Req())["channel_type"], ["shopee", "none"])


class AssessmentStatusFilterTests(TestCase):
    """Tests cho tối ưu bộ lọc trạng thái (assessment) và bổ sung search trang Đơn hàng.

    Nhánh perf/orders-list-query-optimization:
    - #1: bộ lọc sales_status / delivery_business_status với "__empty__" phải giữ NGUYÊN
      ngữ nghĩa sau khi đổi sang dạng ~Exists (bỏ seq-scan toàn bảng order_assessments).
    - #3: search (q) trang Đơn hàng phải tìm được theo TÊN khách và chạm được đơn đã huỷ.

    `__empty__` = đơn không có assessment HOẶC có nhưng code null/rỗng.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-assessment-status")
        self.channel = Channel.objects.create(code="lp-assess", name="Assess Channel")
        self.customer = Customer.objects.create(
            full_name="Khach Assess",
            phone_raw="0911000001",
            phone_normalized="0911000001",
        )

        def make_order(name, sales_code):
            order = Order.objects.create(
                channel=self.channel,
                customer=self.customer,
                name=name,
                phone="0911000001",
                total_amount="0.00",
                show_orders=True,
            )
            if sales_code is not None:
                OrderAssessment.objects.create(order=order, sales_status_code=sales_code)
            return order

        # concrete "called"
        self.o_called = make_order("Called", "called")
        # concrete "created_order"
        self.o_created = make_order("Created", "created_order")
        # có assessment nhưng code rỗng -> tính là empty
        self.o_blank = make_order("Blank", "")
        # có assessment nhưng code null -> empty
        self.o_null = make_order("Null", None)
        OrderAssessment.objects.create(order=self.o_null, sales_status_code=None)
        # không có assessment nào -> empty
        self.o_no_assessment = make_order("NoAssessment", None)

    def _post(self, sales_status):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            "/api/orders",
            {"sales_status": sales_status, "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        return {item["id"] for item in resp.json()["results"]}

    def test_empty_matches_no_assessment_and_blank_and_null(self):
        ids = self._post(["__empty__"])
        self.assertEqual(ids, {self.o_blank.id, self.o_null.id, self.o_no_assessment.id})

    def test_concrete_only(self):
        ids = self._post(["called"])
        self.assertEqual(ids, {self.o_called.id})

    def test_empty_plus_concrete_union(self):
        ids = self._post(["__empty__", "created_order"])
        self.assertEqual(
            ids,
            {self.o_created.id, self.o_blank.id, self.o_null.id, self.o_no_assessment.id},
        )

    def test_multiple_concrete(self):
        ids = self._post(["called", "created_order"])
        self.assertEqual(ids, {self.o_called.id, self.o_created.id})


class OrderSearchNameAndCancelledTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-search-name")
        self.channel = Channel.objects.create(code="lp-search", name="Search Channel")
        self.customer = Customer.objects.create(
            full_name="Nguyen Van Search",
            phone_raw="0912000001",
            phone_normalized="0912000001",
        )
        self.order_named = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Nguyen Van Đặc Biệt",
            phone="0912000001",
            total_amount="0.00",
            show_orders=True,
        )
        # Đơn đã huỷ (status=6) — search theo SĐT phải tìm được dù base list loại đơn huỷ.
        self.order_cancelled = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Cancelled One",
            phone="0912000009",
            status=OrderStatus.CANCELLED,
            total_amount="0.00",
            show_orders=True,
        )

    def _search(self, q):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            "/api/orders",
            {"q": q, "limit": "100", "offset": "0"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        return {item["id"] for item in resp.json()["results"]}

    def test_search_by_customer_name_fragment(self):
        # Trước đây list `q` không tìm theo name -> gõ tên trả rỗng ("search không ra").
        ids = self._search("Đặc Biệt")
        self.assertIn(self.order_named.id, ids)

    def test_search_reaches_cancelled_order(self):
        ids = self._search("0912000009")
        self.assertIn(self.order_cancelled.id, ids)


class OrderCodeAndWarrantySearchTests(TestCase):
    """Round 2: order_code vào bộ search list; endpoint /api/orders/search (warranty) tái
    dùng search đã tối ưu index — tìm theo tên/mã/SĐT, chạm cả đơn huỷ."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-search-round2")
        self.channel = Channel.objects.create(code="lp-r2", name="R2")
        self.customer = Customer.objects.create(
            full_name="Tran Thi R2", phone_raw="0913000001", phone_normalized="0913000001"
        )
        self.order = Order.objects.create(
            channel=self.channel, customer=self.customer, name="Tran Thi R2",
            phone="0913000001", order_code="ORD-R2-8899", total_amount="0.00", show_orders=True,
        )
        self.cancelled = Order.objects.create(
            channel=self.channel, customer=self.customer, name="Cancelled R2",
            phone="0913000099", status=OrderStatus.CANCELLED, total_amount="0.00", show_orders=True,
        )

    def test_list_search_by_order_code(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.post(
            "/api/orders", {"q": "ORD-R2-8899", "limit": "100", "offset": "0"}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.order.id, {i["id"] for i in r.json()["results"]})

    def test_warranty_search_endpoint_by_name_code_phone(self):
        self.client.force_authenticate(user=self.admin)
        for q in ["ORD-R2-8899", "Tran Thi", "0913000001"]:
            r = self.client.get("/api/orders/search", {"q": q})
            self.assertEqual(r.status_code, 200)
            self.assertIn(self.order.id, {i["id"] for i in r.json()}, f"q={q!r}")

    def test_warranty_search_endpoint_reaches_cancelled(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get("/api/orders/search", {"q": "0913000099"})
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.cancelled.id, {i["id"] for i in r.json()})


class KiotVietPurchaseDateFilterTests(TestCase):
    """Bộ lọc "Ngày tạo trên KiotViet" (`kiotviet_purchase_date_from/to`).

    Cùng kiểu biên ngày local như `date_from/date_to` (to = hết ngày, exclusive),
    nhưng KHÁC ở chỗ vẫn áp khi đang search (q) — giống `return_date_*`. Đơn chưa
    có hoá đơn KiotViet (NULL) bị loại khi bật lọc. Phải chạy trên CẢ NĂM endpoint
    danh sách đơn (mỗi endpoint chép tay một dict filter riêng).
    """

    RANGE = {"kiotviet_purchase_date_from": "2026-05-01", "kiotviet_purchase_date_to": "2026-05-31"}

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=make_admin("admin-kv-purchase-date"))
        self.channel = Channel.objects.create(code="kvpd", name="KV Purchase Date")
        self.customer = Customer.objects.create(
            full_name="KH KV Purchase Date",
            phone_raw="0900000009",
            phone_normalized="0900000009",
        )
        self._seq = 0
        tz = timezone.get_current_timezone()
        self.in_range = timezone.make_aware(datetime(2026, 5, 1, 0, 30), tz)
        self.before_range = timezone.make_aware(datetime(2026, 4, 30, 23, 59), tz)

    def _make_order(self, purchase_dt, **kwargs):
        self._seq += 1
        kwargs.setdefault("channel", self.channel)
        kwargs.setdefault("address", "1 Đường Test")
        kwargs.setdefault("phone", f"09000009{self._seq:02d}")
        order = Order.objects.create(
            customer=self.customer,
            name=f"Order KVPD {self._seq}",
            **kwargs,
        )
        if purchase_dt is not None:
            Order.objects.filter(pk=order.pk).update(kiotviet_purchase_date=purchase_dt)
        return order

    def _ids(self, url, payload):
        body = {"limit": "100", "offset": "0", **payload}
        resp = self.client.post(url, body, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        return {r["id"] for r in resp.data["results"]}

    def test_orders_endpoint_uses_local_day_boundaries_and_excludes_null(self):
        keep = self._make_order(self.in_range)
        self._make_order(self.before_range)
        self._make_order(None)
        self.assertEqual(self._ids("/api/orders", self.RANGE), {keep.id})

    def test_to_boundary_is_end_of_day_inclusive(self):
        tz = timezone.get_current_timezone()
        keep = self._make_order(timezone.make_aware(datetime(2026, 5, 31, 23, 59), tz))
        self._make_order(timezone.make_aware(datetime(2026, 6, 1, 0, 0), tz))
        self.assertEqual(self._ids("/api/orders", self.RANGE), {keep.id})

    def test_only_from_or_only_to_filters_one_side(self):
        keep = self._make_order(self.in_range)
        early = self._make_order(self.before_range)
        self.assertEqual(
            self._ids("/api/orders", {"kiotviet_purchase_date_from": "2026-05-01"}), {keep.id}
        )
        self.assertEqual(
            self._ids("/api/orders", {"kiotviet_purchase_date_to": "2026-04-30"}), {early.id}
        )

    def test_invalid_value_is_ignored(self):
        a = self._make_order(self.in_range)
        b = self._make_order(None)
        ids = self._ids("/api/orders", {"kiotviet_purchase_date_from": "abc"})
        self.assertEqual(ids, {a.id, b.id})

    def test_applies_even_when_searching(self):
        # Khác date_from/date_to: search (q) KHÔNG tắt bộ lọc này.
        keep = self._make_order(self.in_range, phone="0911222333")
        self._make_order(self.before_range, phone="0911222333")
        self.assertEqual(self._ids("/api/orders", {"q": "0911222333", **self.RANGE}), {keep.id})

    def test_koc_endpoint(self):
        koc_channel = Channel.objects.create(id=settings.KOC_CHANNEL_ID, code="kvpd-koc", name="KOC")
        keep = self._make_order(self.in_range, order_source="koc", channel=koc_channel)
        self._make_order(self.before_range, order_source="koc", channel=koc_channel)
        self.assertEqual(self._ids("/api/orders/koc", self.RANGE), {keep.id})

    def test_landing_endpoint(self):
        keep = self._make_order(self.in_range, order_source="landing")
        self._make_order(self.before_range, order_source="landing")
        self.assertEqual(self._ids("/api/orders/landing", self.RANGE), {keep.id})

    def test_sheet3_endpoint(self):
        # Board Sheet 3 = đơn thiếu thông tin (không địa chỉ).
        keep = self._make_order(self.in_range, order_source="landing", address="")
        self._make_order(self.before_range, order_source="landing", address="")
        self.assertEqual(self._ids("/api/orders/sheet3", self.RANGE), {keep.id})

    def test_shipments_endpoint(self):
        keep = self._make_order(self.in_range, shipping_code="SHIP-KVPD-1")
        drop = self._make_order(self.before_range, shipping_code="SHIP-KVPD-2")
        for order in (keep, drop):
            OrderAssessment.objects.create(order=order, sales_status_code="created_order")
        self.assertEqual(self._ids("/api/orders/shipments", self.RANGE), {keep.id})

    def test_export_filters_carry_kiotviet_purchase_date(self):
        """Xuất Excel theo bộ lọc dùng build_orders_filters — phải cùng tập với list."""
        from apps.orders.transport.views.order_views_impl._view_helpers import (
            build_orders_filters,
        )

        class _Req:
            method = "POST"
            data = dict(KiotVietPurchaseDateFilterTests.RANGE)

        filters = build_orders_filters(_Req())
        self.assertEqual(filters["kiotviet_purchase_date_from"], "2026-05-01")
        self.assertEqual(filters["kiotviet_purchase_date_to"], "2026-05-31")


class FulfillmentWarehouseFilterTests(TestCase):
    """Bộ lọc "Kho xuất" (`fulfillment_warehouse`, nhiều giá trị) trên các list
    endpoint. Token "none" = đơn chưa gán kho — cùng quy ước với kiotviet_channel.
    Kho đã vô hiệu hoá vẫn lọc được: đơn cũ giữ FK tới kho đó.
    """

    def setUp(self):
        from apps.warehouses.models import Warehouse

        self.client = APIClient()
        self.admin = make_admin("admin-wh-filter")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(code="lst-wh", name="List Wh", owner="List Wh")
        self.customer = Customer.objects.create(
            full_name="KH Wh",
            phone_raw="0900000006",
            phone_normalized="0900000006",
        )
        self.wh_a = Warehouse.objects.create(code="WH-FLT-A", name="Kho A")
        self.wh_b = Warehouse.objects.create(code="WH-FLT-B", name="Kho B", is_active=False)
        self.o_a = self._make_order("A", self.wh_a)
        self.o_b = self._make_order("B", self.wh_b)
        self.o_none = self._make_order("N", None)

    def _make_order(self, tag, warehouse):
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name=f"Order Wh {tag}",
            phone=f"09000006{ord(tag):02d}",
            order_source="landing",
            fulfillment_warehouse=warehouse,
        )

    def _ids(self, endpoint, body):
        response = self.client.post(
            endpoint, {"limit": "100", "offset": "0", **body}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {item["id"] for item in response.json()["results"]}

    def test_filter_single_warehouse(self):
        self.assertEqual(
            self._ids("/api/orders", {"fulfillment_warehouse": [str(self.wh_a.id)]}),
            {self.o_a.id},
        )

    def test_filter_multiple_warehouses_including_inactive(self):
        self.assertEqual(
            self._ids(
                "/api/orders",
                {"fulfillment_warehouse": [str(self.wh_a.id), str(self.wh_b.id)]},
            ),
            {self.o_a.id, self.o_b.id},
        )

    def test_filter_none_token_returns_unassigned(self):
        self.assertEqual(
            self._ids("/api/orders", {"fulfillment_warehouse": ["none"]}),
            {self.o_none.id},
        )

    def test_filter_none_combined_with_warehouse(self):
        self.assertEqual(
            self._ids("/api/orders", {"fulfillment_warehouse": ["none", str(self.wh_b.id)]}),
            {self.o_none.id, self.o_b.id},
        )

    def test_invalid_only_values_return_nothing(self):
        self.assertEqual(
            self._ids("/api/orders", {"fulfillment_warehouse": ["abc"]}),
            set(),
        )

    def test_landing_list_parity(self):
        self.assertEqual(
            self._ids("/api/orders/landing", {"fulfillment_warehouse": [str(self.wh_a.id)]}),
            {self.o_a.id},
        )
