from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.customers.profile_service import CustomerProfileService
from apps.orders.models import NeedGroup, NeedType, Order, OrderAssessment, OrderItem, OrderNeed, OrderStatus
from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin


class CustomerProfileServiceTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="lp-prof", name="Landing Profile")
        self.product = Product.objects.create(name="Ghế massage", sku="GM-001", price="0")

    def _customer(self, code, name, phone):
        return Customer.objects.create(code=code, full_name=name, phone_raw=phone, phone_normalized=phone)

    def _order(self, customer, amount, *, status=OrderStatus.DELIVERED_SUCCESS, days_ago=0, sku="GM-001",
               invoice_status=0):
        order = Order.objects.create(
            channel=self.channel, customer=customer, name=customer.full_name, phone=customer.phone_raw,
            status=status, total_after_discount=amount, invoice_status=invoice_status, sku=sku,
            product="Ghế massage", address="123 Lê Lợi", province="TP.HCM",
        )
        # created_at là auto_now_add → set lại để kiểm soát thứ tự "đơn mới nhất".
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        order.refresh_from_db()
        OrderItem.objects.create(order=order, product=self.product, product_name="Ghế massage",
                                 sku=sku, quantity=1, unit_price=amount, line_total=amount)
        return order

    def test_metrics_exclude_cancelled_and_count_orders(self):
        cus = self._customer("KH1", "Nguyen Van A", "0900000001")
        self._order(cus, "1000000", days_ago=30)
        self._order(cus, "2000000", days_ago=5)
        # Đơn hủy hóa đơn: không tính vào metrics.
        self._order(cus, "9000000", days_ago=1, invoice_status=2)
        # Đơn hoàn: không tính vào metrics.
        self._order(cus, "5000000", status=OrderStatus.RETURNED, days_ago=2)

        profiles = CustomerProfileService.list_profiles(q="", limit=10, offset=0)
        row = next(r for r in profiles["results"] if r["identity"]["id"] == cus.id)
        self.assertEqual(row["purchase"]["total_orders"], 2)
        self.assertEqual(row["purchase"]["total_spent"], "3000000.00")
        self.assertEqual(profiles["total"], 1)

    def test_latest_order_persona_and_demographics(self):
        cus = self._customer("KH2", "Tran Thi B", "0900000002")
        self._order(cus, "1000000", days_ago=20)
        latest = self._order(cus, "2000000", days_ago=1)
        OrderAssessment.objects.create(
            order=latest,
            sales_status_code="closed",
            customer_classification_code="easy_close",
            pain_level_code="moderate",
            occupation_code="teacher",
            age_text="45_54",
            purchase_barrier_codes=["price"],
            delax_source_codes=["facebook"],
        )

        profiles = CustomerProfileService.list_profiles(q="0900000002", limit=10, offset=0)
        row = profiles["results"][0]
        self.assertEqual(row["latest_order"]["sales_status_code"], "closed")
        self.assertEqual(row["latest_order"]["last_order_amount"], "2000000.00")
        self.assertEqual(row["latest_order"]["last_order_status"], OrderStatus.DELIVERED_SUCCESS.value)
        self.assertEqual(row["demographics"]["province"], "TP.HCM")
        self.assertEqual(row["demographics"]["address"], "123 Lê Lợi")
        self.assertEqual(row["demographics"]["occupation_code"], "teacher")
        self.assertEqual(row["demographics"]["age_text"], "45_54")
        self.assertEqual(row["demographics"]["age_group_label"], "45-54")
        self.assertEqual(row["channel"]["channel_name"], "Landing Profile")
        self.assertEqual(row["persona"]["customer_classification_code"], "easy_close")
        self.assertEqual(row["persona"]["pain_level_code"], "moderate")
        self.assertEqual(row["persona"]["purchase_barrier_codes"], ["price"])

    def test_latest_order_needs(self):
        cus = self._customer("KH4", "Vo Thi D", "0900000004")
        self._order(cus, "1000000", days_ago=20)
        latest = self._order(cus, "2000000", days_ago=1)
        assessment = OrderAssessment.objects.create(order=latest, sales_status_code="closed")
        group_pain = NeedGroup.objects.create(code="t_pain", name_vi="Giảm đau")
        group_relax = NeedGroup.objects.create(code="t_relax", name_vi="Thư giãn")
        type_back = NeedType.objects.create(code="t_back_pain", name_vi="Đau lưng", group=group_pain)
        type_sleep = NeedType.objects.create(code="t_sleep", name_vi="Ngủ ngon", group=group_relax)
        OrderNeed.objects.create(order_assessment=assessment, need_type=type_back)
        OrderNeed.objects.create(order_assessment=assessment, need_type=type_sleep)

        profiles = CustomerProfileService.list_profiles(q="0900000004", limit=10, offset=0)
        row = profiles["results"][0]
        needs = row["needs"]
        self.assertEqual(needs["need_group_code"], "t_pain")
        self.assertEqual(needs["need_group_name"], "Giảm đau")
        self.assertEqual(len(needs["items"]), 2)
        self.assertEqual(
            needs["items"],
            [
                {"group_code": "t_pain", "group_name": "Giảm đau",
                 "type_code": "t_back_pain", "type_name": "Đau lưng"},
                {"group_code": "t_relax", "group_name": "Thư giãn",
                 "type_code": "t_sleep", "type_name": "Ngủ ngon"},
            ],
        )

    def test_distinct_skus(self):
        cus = self._customer("KH5", "Hoang Van E", "0900000005")
        self._order(cus, "1000000", days_ago=20, sku="A")
        self._order(cus, "2000000", days_ago=10, sku="B")
        # Đơn lặp lại SKU "A" → không nhân đôi trong distinct.
        self._order(cus, "3000000", days_ago=1, sku="A")

        profiles = CustomerProfileService.list_profiles(q="0900000005", limit=10, offset=0)
        row = profiles["results"][0]
        self.assertEqual(sorted(row["purchase"]["distinct_skus"]), ["A", "B"])

        # Khách không có đơn → list rỗng.
        no_order = self._customer("KH6", "Dang Thi F", "0900000006")
        profiles = CustomerProfileService.list_profiles(q="0900000006", limit=10, offset=0)
        row = next(r for r in profiles["results"] if r["identity"]["id"] == no_order.id)
        self.assertEqual(row["purchase"]["distinct_skus"], [])

    def test_customer_without_orders_has_empty_enrichment(self):
        cus = self._customer("KH3", "Le Van C", "0900000003")
        profiles = CustomerProfileService.list_profiles(q="0900000003", limit=10, offset=0)
        row = profiles["results"][0]
        self.assertEqual(row["purchase"]["total_orders"], 0)
        self.assertIsNone(row["latest_order"]["sales_status_code"])
        self.assertIsNone(row["resale"]["last_resale_at"])
        self.assertEqual(row["demographics"]["province"], "")


class CustomerProfilesApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-prof")
        self.channel = Channel.objects.create(code="lp-api", name="Landing API")

    def test_requires_auth(self):
        response = self.client.get("/api/customers/profiles?limit=10&offset=0")
        self.assertEqual(response.status_code, 401)

    def test_returns_results_and_total_with_labels(self):
        cus = Customer.objects.create(code="KH9", full_name="Pham D", phone_raw="0900000009",
                                      phone_normalized="0900000009")
        order = Order.objects.create(channel=self.channel, customer=cus, name="Pham D",
                                     phone="0900000009", status=3, total_after_discount="1500000")
        OrderAssessment.objects.create(order=order, sales_status_code="closed", occupation_code="teacher")

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/customers/profiles?limit=10&offset=0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["identity"]["code"], "KH9")
        self.assertEqual(row["latest_order"]["sales_status_code"], "closed")
        self.assertEqual(row["latest_order"]["sales_status_label"], "Đã chốt")
        self.assertEqual(row["demographics"]["occupation_label"], "Giáo viên")
        self.assertEqual(row["demographics"]["gender_label"], None)

    def test_filter_by_channel_and_sales_status(self):
        other = Channel.objects.create(code="lp-other", name="Other")
        c1 = Customer.objects.create(code="KHA", full_name="A", phone_raw="0911", phone_normalized="0911")
        c2 = Customer.objects.create(code="KHB", full_name="B", phone_raw="0922", phone_normalized="0922")
        o1 = Order.objects.create(channel=self.channel, customer=c1, name="A", phone="0911", status=3, total_after_discount="100")
        OrderAssessment.objects.create(order=o1, sales_status_code="closed")
        o2 = Order.objects.create(channel=other, customer=c2, name="B", phone="0922", status=3, total_after_discount="100")
        OrderAssessment.objects.create(order=o2, sales_status_code="not_buying")

        self.client.force_authenticate(user=self.admin)
        by_channel = self.client.get(f"/api/customers/profiles?limit=10&offset=0&channel={self.channel.id}")
        codes = [r["identity"]["code"] for r in by_channel.data["results"]]
        self.assertEqual(codes, ["KHA"])
        by_status = self.client.get("/api/customers/profiles?limit=10&offset=0&sales_status=not_buying")
        codes = [r["identity"]["code"] for r in by_status.data["results"]]
        self.assertEqual(codes, ["KHB"])

    def test_segment_s1_for_nurture_non_buyer(self):
        # Đơn duy nhất bị HỦY (status 6) → _metrics_map loại → total_orders=0; status 6 không
        # phải tín hiệu "có vấn đề" (chỉ 4,5) → has_problem=False. Đơn này vẫn là đơn mới nhất
        # nên sales_status_code="call_back_later" dẫn tới S1.
        cus = Customer.objects.create(code="KHN", full_name="Nurture", phone_raw="0955",
                                      phone_normalized="0955")
        order = Order.objects.create(channel=self.channel, customer=cus, name="Nurture", phone="0955",
                                     status=OrderStatus.CANCELLED, total_after_discount="100000")
        OrderAssessment.objects.create(order=order, sales_status_code="call_back_later")

        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/api/customers/profiles?limit=10&offset=0&q=0955")
        row = resp.data["results"][0]
        self.assertEqual(row["segment"]["segment_code"], "S1")

    def test_no_answer_1_non_buyer_is_not_s1(self):
        cus = Customer.objects.create(code="KHNA1", full_name="No Answer", phone_raw="0956",
                                      phone_normalized="0956")
        order = Order.objects.create(channel=self.channel, customer=cus, name="No Answer", phone="0956",
                                     status=OrderStatus.CANCELLED, total_after_discount="100000")
        OrderAssessment.objects.create(order=order, sales_status_code="no_answer_1")

        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/api/customers/profiles?limit=10&offset=0&q=0956")
        row = resp.data["results"][0]
        self.assertEqual(row["latest_order"]["sales_status_code"], "no_answer_1")
        self.assertEqual(row["segment"]["segment_code"], "")

    def test_segment_s5_for_problem_customer(self):
        # Đơn HOÀN (status 5) → tín hiệu "có vấn đề" → S5 (ưu tiên cao nhất).
        cus = Customer.objects.create(code="KHP", full_name="Problem", phone_raw="0966",
                                      phone_normalized="0966")
        Order.objects.create(channel=self.channel, customer=cus, name="Problem", phone="0966",
                             status=OrderStatus.RETURNED, total_after_discount="100000")

        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/api/customers/profiles?limit=10&offset=0&q=0966")
        row = resp.data["results"][0]
        self.assertEqual(row["segment"]["segment_code"], "S5")

    def test_filter_by_segment_s5(self):
        problem = Customer.objects.create(code="KHP2", full_name="Problem2", phone_raw="0977",
                                          phone_normalized="0977")
        Order.objects.create(channel=self.channel, customer=problem, name="Problem2", phone="0977",
                             status=OrderStatus.RETURNED, total_after_discount="100000")
        normal = Customer.objects.create(code="KHND", full_name="Normal", phone_raw="0988",
                                         phone_normalized="0988")
        Order.objects.create(channel=self.channel, customer=normal, name="Normal", phone="0988",
                             status=3, total_after_discount="100000")

        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/api/customers/profiles?limit=10&offset=0&segment=S5")
        codes = [r["identity"]["code"] for r in resp.data["results"]]
        self.assertEqual(codes, ["KHP2"])

    def test_filter_by_segment(self):
        loyal = Customer.objects.create(code="KHL", full_name="Loyal", phone_raw="0933", phone_normalized="0933")
        once = Customer.objects.create(code="KHO", full_name="Once", phone_raw="0944", phone_normalized="0944")
        for amount in ("1000000", "1000000"):
            Order.objects.create(channel=self.channel, customer=loyal, name="Loyal", phone="0933", status=3, total_after_discount=amount)
        Order.objects.create(channel=self.channel, customer=once, name="Once", phone="0944", status=3, total_after_discount="100000")

        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/api/customers/profiles?limit=10&offset=0&segment=S3")
        codes = [r["identity"]["code"] for r in resp.data["results"]]
        self.assertEqual(codes, ["KHL"])  # 2 đơn GTC → S3 trung thành
