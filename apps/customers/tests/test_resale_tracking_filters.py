from datetime import datetime, timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer, CustomerResaleFollowUp, CustomerSegmentSnapshot
from apps.customers.segment_snapshot_service import SegmentSnapshotService
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.catalog.models import Product
from apps.users.models import User
from apps.users.tests.helpers import make_admin


# count/facets của trang resale cache trên Redis (shared dev/prod) — dùng DummyCache
# để mỗi test recompute, KHÔNG đụng (và không FLUSHDB) Redis thật.
@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
)
class ResaleTrackingFiltersApiTests(TestCase):
    """Filter ngày giao / tình trạng lạnh / sale / liên hệ + facets cho trang resale.

    Phân khúc (``segment``) và số ngày chưa mua (``min_days``) được đẩy xuống SQL
    qua bảng ``customer_segment_snapshot`` (nguồn sự thật duy nhất) — vừa lọc vừa
    hiển thị. Nên test phải rebuild snapshot sau khi seed đơn.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-resale")
        self.sale_a = User.objects.create_user(username="sale-a", password="x")
        self.sale_b = User.objects.create_user(username="sale-b", password="x")
        self.channel = Channel.objects.create(code="lp-resale", name="Landing Resale")
        self.product = Product.objects.create(name="Ghế massage", sku="GM-RS", price="0")
        self.client.force_authenticate(user=self.admin)

    def _customer(self, code, name, phone):
        return Customer.objects.create(code=code, full_name=name, phone_raw=phone, phone_normalized=phone)

    def _delivered_order(self, customer, amount, *, sale, created_days_ago, delivered_at=None, order_source="landing"):
        order = Order.objects.create(
            channel=self.channel,
            customer=customer,
            staff=sale,
            name=customer.full_name,
            phone=customer.phone_raw,
            status=OrderStatus.DELIVERED_SUCCESS,
            total_after_discount=amount,
            order_source=order_source,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=1,
            unit_price=amount,
            line_total=amount,
        )
        # created_at quyết định days_since_last_purchase / phân khúc;
        # updated_at đóng vai trò delivered_at cho bộ lọc ngày.
        created = timezone.now() - timedelta(days=created_days_ago)
        Order.objects.filter(pk=order.pk).update(
            created_at=created,
            updated_at=delivered_at or created,
        )
        return order

    def _rebuild_snapshots(self):
        """Dựng lại snapshot phân khúc cho toàn bộ khách đã seed (như cron 2h prod)."""
        SegmentSnapshotService.rebuild()

    def _get(self, **params):
        params.setdefault("limit", 100)
        params.setdefault("offset", 0)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        response = self.client.get(f"/api/customers/resale?{query}")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def _seed_segments(self):
        # S4 ngủ đông: 1 đơn, 200 ngày không mua, giá trị nhỏ.
        cold = self._customer("KH-COLD", "Khach Lanh", "0900000001")
        self._delivered_order(cold, "500000", sale=self.sale_a, created_days_ago=200)
        # S2 mới mua: 1 đơn, 5 ngày, giá trị nhỏ.
        warm = self._customer("KH-WARM", "Khach Moi", "0900000002")
        self._delivered_order(warm, "500000", sale=self.sale_b, created_days_ago=5)
        return cold, warm

    def test_min_days_filters_cold_customers_across_dataset(self):
        cold, _warm = self._seed_segments()
        self._rebuild_snapshots()
        data = self._get(min_days=180)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["results"][0]["customer"]["id"], cold.id)
        self.assertGreaterEqual(data["results"][0]["days_since_last_purchase"], 180)

    def test_segment_filter(self):
        cold, _warm = self._seed_segments()
        self._rebuild_snapshots()
        data = self._get(segment="S4")
        self.assertEqual([r["customer"]["id"] for r in data["results"]], [cold.id])

    def test_segment_filter_requires_snapshot(self):
        """Không có snapshot ⇒ khách không lọt filter phân khúc (nguồn sự thật = snapshot)."""
        self._seed_segments()  # cố tình KHÔNG rebuild snapshot
        data = self._get(segment="S4")
        self.assertEqual(data["results"], [])
        self.assertEqual(data["total"], 0)

    def test_display_segment_comes_from_snapshot(self):
        """Phân khúc hiển thị lấy TỪ snapshot, không tính lại realtime từ lịch sử."""
        cold, _warm = self._seed_segments()
        self._rebuild_snapshots()
        # Ghi đè snapshot của "cold" thành S6 dù lịch sử realtime là S4.
        CustomerSegmentSnapshot.objects.filter(customer_id=cold.id).update(segment_code="S6")
        data = self._get()
        row = next(r for r in data["results"] if r["customer"]["id"] == cold.id)
        self.assertEqual(row["segment_code"], "S6")
        # Và filter cũng theo snapshot đã ghi đè.
        s6 = self._get(segment="S6")
        self.assertEqual([r["customer"]["id"] for r in s6["results"]], [cold.id])

    def test_delivered_date_range_filter(self):
        old_day = timezone.make_aware(datetime(2026, 1, 10, 9, 0))
        new_day = timezone.make_aware(datetime(2026, 6, 1, 9, 0))
        a = self._customer("KH-OLD", "Giao Cu", "0900000010")
        b = self._customer("KH-NEW", "Giao Moi", "0900000011")
        self._delivered_order(a, "500000", sale=self.sale_a, created_days_ago=20, delivered_at=old_day)
        self._delivered_order(b, "500000", sale=self.sale_a, created_days_ago=20, delivered_at=new_day)

        data = self._get(date_from="2026-05-01", date_to="2026-06-01")
        self.assertEqual([r["customer"]["id"] for r in data["results"]], [b.id])

    def test_contact_status_filter(self):
        cold, warm = self._seed_segments()
        order = Order.objects.get(customer=cold)
        CustomerResaleFollowUp.objects.create(order=order, contact_1_at=timezone.now())

        contacted = self._get(contact_status="contacted")
        self.assertEqual([r["customer"]["id"] for r in contacted["results"]], [cold.id])

        none = self._get(contact_status="none")
        self.assertEqual([r["customer"]["id"] for r in none["results"]], [warm.id])

    def test_orders_without_order_source_are_excluded(self):
        with_source = self._customer("KH-SRC", "Co Nguon", "0900000020")
        without_source = self._customer("KH-NOSRC", "Khong Nguon", "0900000021")
        empty_source = self._customer("KH-EMPTY", "Nguon Rong", "0900000022")
        self._delivered_order(with_source, "500000", sale=self.sale_a, created_days_ago=5)
        self._delivered_order(without_source, "500000", sale=self.sale_a, created_days_ago=5, order_source=None)
        self._delivered_order(empty_source, "500000", sale=self.sale_a, created_days_ago=5, order_source="")

        data = self._get()
        self.assertEqual([r["customer"]["id"] for r in data["results"]], [with_source.id])
        self.assertEqual(data["total"], 1)

    def test_sale_filter(self):
        cold, warm = self._seed_segments()
        data = self._get(sale=self.sale_b.id)
        self.assertEqual([r["customer"]["id"] for r in data["results"]], [warm.id])

    def test_facets_expose_sales_and_segments(self):
        self._seed_segments()
        # Lọc theo 1 sale nhưng facet.sales vẫn liệt kê đủ cả 2 sale (không bị kẹt).
        data = self._get(sale=self.sale_a.id)
        sale_ids = {s["id"] for s in data["facets"]["sales"]}
        self.assertEqual(sale_ids, {self.sale_a.id, self.sale_b.id})
        segment_codes = [s["code"] for s in data["facets"]["segments"]]
        self.assertEqual(segment_codes, ["S2", "S3", "S4", "S5", "S6"])
