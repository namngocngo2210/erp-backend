"""Test model PriceBook / ProductPackagePrice và service resolve bảng giá."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.db.utils import IntegrityError
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.order_service_helpers import OrderServiceHelpers
from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import process_landing_ingest
from apps.orders.models import Order, OrderItem
from apps.catalog.models import PriceBook, Product, ProductPackage, ProductPackagePrice
from apps.orders.tests.helpers import ingest_headers


class PriceBookModelTests(TestCase):
    def setUp(self):
        # Migration đã seed facebook/web — lấy ra dùng, không tạo trùng code.
        self.facebook = PriceBook.objects.get(code="facebook")
        self.web = PriceBook.objects.get(code="web")
        self.product = Product.objects.create(name="SP giá", sku="SKU-PB-01", price=Decimal("100"))
        self.package = ProductPackage.objects.create(product=self.product, quantity=1)

    def test_seed_creates_facebook_as_default(self):
        self.assertTrue(self.facebook.is_default)
        self.assertFalse(self.web.is_default)

    def test_price_unique_per_package_and_book(self):
        ProductPackagePrice.objects.create(
            package=self.package, price_book=self.facebook, price=Decimal("100")
        )
        with self.assertRaises(IntegrityError):
            ProductPackagePrice.objects.create(
                package=self.package, price_book=self.facebook, price=Decimal("200")
            )

    def test_only_one_default_book_allowed(self):
        with self.assertRaises(IntegrityError):
            PriceBook.objects.create(code="tiktok", name="TikTok", is_default=True)

    def test_code_unique_among_active_books(self):
        # facebook đang active (chưa soft-delete) → tạo trùng code phải vỡ constraint.
        with self.assertRaises(IntegrityError):
            PriceBook.objects.create(code="facebook", name="Facebook trùng")

    def test_code_can_repeat_after_soft_delete(self):
        # Chủ đích của partial unique constraint: bảng cũ đã soft-delete thì code
        # được phép tái sử dụng cho bảng mới.
        self.facebook.deleted_at = timezone.now()
        self.facebook.save(update_fields=["deleted_at"])
        reused = PriceBook.objects.create(code="facebook", name="Facebook mới")
        self.assertIsNone(reused.deleted_at)

    def test_channel_price_book_is_nullable(self):
        channel = Channel.objects.create(code="pb-chan", name="Kênh test")
        self.assertIsNone(channel.price_book)
        channel.price_book = self.web
        channel.save(update_fields=["price_book"])
        channel.refresh_from_db()
        self.assertEqual(channel.price_book.code, "web")


class PriceBookServiceTests(TestCase):
    def setUp(self):
        from apps.catalog.models import PriceBook

        self.facebook = PriceBook.objects.get(code="facebook")
        self.web = PriceBook.objects.get(code="web")
        self.product = Product.objects.create(name="SP", sku="SKU-SVC-01", price=Decimal("100"))
        self.pkg1 = ProductPackage.objects.create(product=self.product, quantity=1)
        self.pkg2 = ProductPackage.objects.create(product=self.product, quantity=2)
        ProductPackagePrice.objects.create(package=self.pkg1, price_book=self.facebook, price=Decimal("100"))
        ProductPackagePrice.objects.create(package=self.pkg2, price_book=self.facebook, price=Decimal("180"))
        # web CHỈ khai giá gói 1 → gói 2 phải fallback về facebook.
        ProductPackagePrice.objects.create(package=self.pkg1, price_book=self.web, price=Decimal("130"))

    def test_resolve_uses_channel_price_book(self):
        from apps.orders.application.services.price_book_service import resolve_price_book

        channel = Channel.objects.create(code="svc-web", name="Web", price_book=self.web)
        self.assertEqual(resolve_price_book(channel).code, "web")

    def test_resolve_falls_back_to_default_when_channel_has_none(self):
        from apps.orders.application.services.price_book_service import resolve_price_book

        channel = Channel.objects.create(code="svc-none", name="Không gán")
        self.assertEqual(resolve_price_book(channel).code, "facebook")

    def test_resolve_falls_back_when_book_soft_deleted(self):
        from django.utils import timezone

        from apps.orders.application.services.price_book_service import resolve_price_book

        channel = Channel.objects.create(code="svc-del", name="Book đã xoá", price_book=self.web)
        self.web.deleted_at = timezone.now()
        self.web.save(update_fields=["deleted_at"])
        self.assertEqual(resolve_price_book(channel).code, "facebook")

    def test_resolve_falls_back_when_book_inactive(self):
        from apps.orders.application.services.price_book_service import resolve_price_book

        channel = Channel.objects.create(code="svc-off", name="Book tắt", price_book=self.web)
        self.web.is_active = False
        self.web.save(update_fields=["is_active"])
        self.assertEqual(resolve_price_book(channel).code, "facebook")

    def test_resolve_by_code_is_lenient(self):
        from apps.orders.application.services.price_book_service import resolve_price_book_by_code

        self.assertEqual(resolve_price_book_by_code("web").code, "web")
        self.assertEqual(resolve_price_book_by_code("khong-ton-tai").code, "facebook")
        self.assertEqual(resolve_price_book_by_code(None).code, "facebook")
        self.assertEqual(resolve_price_book_by_code("  WEB  ").code, "web")

    def test_package_prices_fallback_per_tier(self):
        from apps.orders.application.services.price_book_service import package_prices

        prices = package_prices([self.product.id], self.web)
        self.assertEqual(prices[(self.product.id, 1)], Decimal("130"))  # giá web
        self.assertEqual(prices[(self.product.id, 2)], Decimal("180"))  # fallback facebook

    def test_unit_price_for_uses_qty1_of_book(self):
        from apps.orders.application.services.price_book_service import unit_price_for

        self.assertEqual(unit_price_for(self.product, self.web), Decimal("130"))
        self.assertEqual(unit_price_for(self.product, self.facebook), Decimal("100"))


class OrderPricingByPriceBookTests(TestCase):
    """Đơn của kênh gán bảng giá web phải lấy giá web, không phải giá facebook."""

    def setUp(self):
        facebook = PriceBook.objects.get(code="facebook")
        self.web = PriceBook.objects.get(code="web")
        self.product = Product.objects.create(name="SP đơn", sku="SKU-ORD-01", price=Decimal("100"))
        pkg1 = ProductPackage.objects.create(product=self.product, quantity=1)
        ProductPackagePrice.objects.create(package=pkg1, price_book=facebook, price=Decimal("100"))
        ProductPackagePrice.objects.create(package=pkg1, price_book=self.web, price=Decimal("130"))
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000001", phone_normalized="0900000001", code="KH-PB-01"
        )

    def _order_for(self, channel_code: str, price_book):
        channel = Channel.objects.create(code=channel_code, name=channel_code, price_book=price_book)
        return Order.objects.create(channel=channel, customer=self.customer)

    def test_koc_channel_uses_web_price(self):
        order = self._order_for("koc", self.web)
        OrderServiceHelpers._replace_order_items(order, [{"sku": "SKU-ORD-01", "quantity": 2}])
        item = OrderItem.objects.get(order=order)
        self.assertEqual(item.unit_price, Decimal("130.00"))
        self.assertEqual(item.line_total, Decimal("260.00"))

    def test_facebook_channel_uses_default_price(self):
        order = self._order_for("sale-fb", None)
        OrderServiceHelpers._replace_order_items(order, [{"sku": "SKU-ORD-01", "quantity": 1}])
        self.assertEqual(OrderItem.objects.get(order=order).unit_price, Decimal("100.00"))

    def test_explicit_unit_price_still_wins(self):
        order = self._order_for("koc-manual", self.web)
        OrderServiceHelpers._replace_order_items(
            order, [{"sku": "SKU-ORD-01", "quantity": 1, "unit_price": "99000"}]
        )
        self.assertEqual(OrderItem.objects.get(order=order).unit_price, Decimal("99000"))


class OrderPricingQueryCountTests(TestCase):
    """Chống tái diễn N+1: `_replace_order_items` tra giá theo LÔ (package_prices một
    lần), không gọi unit_price_for cho từng dòng hàng — xem cảnh báo hiệu năng Task 7.
    """

    def setUp(self):
        self.web = PriceBook.objects.get(code="web")
        self.products = []
        for i in range(1, 6):
            product = Product.objects.create(
                name=f"SP QC {i}", sku=f"SKU-QCOUNT-{i}", price=Decimal("100")
            )
            pkg = ProductPackage.objects.create(product=product, quantity=1)
            ProductPackagePrice.objects.create(package=pkg, price_book=self.web, price=Decimal("150"))
            self.products.append(product)
        self.customer = Customer.objects.create(
            full_name="KH QC", phone_raw="0900000099", phone_normalized="0900000099", code="KH-PB-QC"
        )

    def _order(self, code: str):
        channel = Channel.objects.create(code=code, name=code, price_book=self.web)
        return Order.objects.create(channel=channel, customer=self.customer)

    def test_price_lookup_query_count_does_not_scale_with_line_items(self):
        order_one = self._order("koc-qc-1")
        order_five = self._order("koc-qc-5")

        with CaptureQueriesContext(connection) as ctx_one:
            OrderServiceHelpers._replace_order_items(
                order_one, [{"sku": self.products[0].sku, "quantity": 1}]
            )

        with CaptureQueriesContext(connection) as ctx_five:
            OrderServiceHelpers._replace_order_items(
                order_five,
                [{"sku": p.sku, "quantity": 1} for p in self.products],
            )

        # Chênh lệch số query giữa đơn 5 dòng và đơn 1 dòng chỉ có thể đến từ việc
        # tra CỨU TỪNG sản phẩm (`_find_product`, N+1 có sẵn từ trước, ngoài phạm vi
        # task này) — resolve bảng giá + `package_prices` phải chạy ĐÚNG MỘT LẦN bất
        # kể số dòng hàng, nên KHÔNG được cộng thêm truy vấn giá nào theo số dòng.
        extra_items = len(self.products) - 1
        self.assertEqual(
            len(ctx_five.captured_queries) - len(ctx_one.captured_queries),
            extra_items,
        )


class LandingIngestPricingTests(TestCase):
    """Đơn đổ về từ landing ingest cũng phải ăn giá theo bảng giá của kênh (không
    còn hardcode `product.price`) — đúng luồng brief Step 4 chỉnh sửa.
    """

    def setUp(self):
        self.client = APIClient()
        facebook = PriceBook.objects.get(code="facebook")
        self.web = PriceBook.objects.get(code="web")
        self.product = Product.objects.create(name="SP Landing", sku="SKU-LANDING-01", price=Decimal("100"))
        pkg = ProductPackage.objects.create(product=self.product, quantity=1)
        ProductPackagePrice.objects.create(package=pkg, price_book=facebook, price=Decimal("100"))
        ProductPackagePrice.objects.create(package=pkg, price_book=self.web, price=Decimal("130"))
        self.channel = Channel.objects.create(code="koc-landing", name="koc-landing", price_book=self.web)

    def _post_ingest(self, payload):
        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_run_task,
        ):
            return self.client.post("/api/orders/ingest", payload, format="json", **ingest_headers())

    def test_landing_order_for_koc_channel_uses_web_price(self):
        resp = self._post_ingest(
            {
                "channel_id": "koc-landing",
                "phone": "0900000077",
                "product_id": "SKU-LANDING-01",
            }
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        order = Order.objects.get(phone="0900000077")
        item = OrderItem.objects.get(order=order)
        self.assertEqual(item.unit_price, Decimal("130.00"))


class AssignKocPriceBookCommandTests(TestCase):
    """Management command gán kênh KOC sang bảng giá web (cutover pha 2, Task 12)."""

    def test_command_assigns_web_book_to_koc_channels(self):
        from django.core.management import call_command

        koc = Channel.objects.create(code="koc", name="Hàng mẫu KOC")
        other = Channel.objects.create(code="sale-fb", name="Sale FB")
        call_command("assign_koc_price_book")
        koc.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(koc.price_book.code, "web")
        self.assertIsNone(other.price_book)

    def test_command_is_idempotent(self):
        from django.core.management import call_command

        koc = Channel.objects.create(code="koc", name="Hàng mẫu KOC")
        call_command("assign_koc_price_book")
        call_command("assign_koc_price_book")
        koc.refresh_from_db()
        self.assertEqual(koc.price_book.code, "web")

    def test_dry_run_reports_without_writing(self):
        from io import StringIO

        from django.core.management import call_command

        koc = Channel.objects.create(code="koc", name="Hàng mẫu KOC")
        out = StringIO()
        call_command("assign_koc_price_book", "--dry-run", stdout=out)
        koc.refresh_from_db()
        # dry-run không được ghi DB
        self.assertIsNone(koc.price_book)
        # nhưng vẫn phải báo cáo đúng kênh sẽ bị ảnh hưởng, không im lặng
        output = out.getvalue()
        self.assertIn(koc.code, output)
        self.assertIn("[dry-run]", output)
