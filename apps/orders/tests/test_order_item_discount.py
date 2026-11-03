"""Giảm giá theo dòng hàng (OrderItem.discount / discount_percent).

`discount` là tiền giảm cho MỖI sản phẩm (cùng nghĩa với cột "Giảm giá" trên
từng dòng hoá đơn KiotViet): line_total = (unit_price − discount) × quantity.
`discount_percent` chỉ lưu khi sale nhập theo %, để form mở lại vẫn thấy đúng %;
tiền giảm luôn được quy ra `discount` ở backend.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.catalog.models import PriceBook, Product, ProductPackage, ProductPackagePrice
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.order_service_helpers import OrderServiceHelpers
from apps.orders.field_diff import field_changed
from apps.orders.models import Order, OrderItem
from apps.orders.serializers import OrderItemInputSerializer
from apps.orders.application.services.order_service_impl import OrderService
from apps.users.tests.helpers import make_admin

PUSH_EVENT_PATH = "apps.orders.application.services.order_service_helpers.OrderServiceHelpers.push_event"


class _ItemDiscountBase(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="disc-ch", name="Kênh giảm giá")
        self.product = Product.objects.create(name="SP giảm", sku="SKU-DISC-01", price=Decimal("100000"))
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000009", phone_normalized="0900000009", code="KH-DISC-01"
        )
        self.order = Order.objects.create(channel=self.channel, customer=self.customer)


class ReplaceOrderItemsDiscountTests(_ItemDiscountBase):
    def test_discount_amount_reduces_line_total_per_unit(self):
        OrderServiceHelpers._replace_order_items(
            self.order,
            [{"sku": "SKU-DISC-01", "quantity": 2, "unit_price": "100000", "discount": "10000"}],
        )
        item = OrderItem.objects.get(order=self.order)
        self.assertEqual(item.discount, Decimal("10000"))
        self.assertIsNone(item.discount_percent)
        self.assertEqual(item.line_total, Decimal("180000"))

    def test_discount_percent_is_converted_to_amount_and_kept(self):
        OrderServiceHelpers._replace_order_items(
            self.order,
            [{"sku": "SKU-DISC-01", "quantity": 3, "unit_price": "99000", "discount_percent": "10"}],
        )
        item = OrderItem.objects.get(order=self.order)
        self.assertEqual(item.discount_percent, Decimal("10"))
        self.assertEqual(item.discount, Decimal("9900"))
        self.assertEqual(item.line_total, Decimal("267300"))

    def test_discount_percent_rounds_half_up_to_two_decimals(self):
        OrderServiceHelpers._replace_order_items(
            self.order,
            [{"sku": "SKU-DISC-01", "quantity": 1, "unit_price": "33.33", "discount_percent": "33.33"}],
        )
        item = OrderItem.objects.get(order=self.order)
        # 33.33 × 33.33% = 11.108889 → 11.11
        self.assertEqual(item.discount, Decimal("11.11"))
        self.assertEqual(item.line_total, Decimal("22.22"))

    def test_discount_never_exceeds_unit_price(self):
        OrderServiceHelpers._replace_order_items(
            self.order,
            [{"sku": "SKU-DISC-01", "quantity": 2, "unit_price": "50000", "discount": "70000"}],
        )
        item = OrderItem.objects.get(order=self.order)
        self.assertEqual(item.discount, Decimal("50000"))
        self.assertEqual(item.line_total, Decimal("0"))

    def test_grand_total_sums_discounted_lines(self):
        _, grand_total = OrderServiceHelpers._replace_order_items(
            self.order,
            [{"sku": "SKU-DISC-01", "quantity": 2, "unit_price": "100000", "discount": "5000"}],
        )
        self.assertEqual(grand_total, Decimal("190000"))

    def test_no_discount_keeps_previous_behaviour(self):
        OrderServiceHelpers._replace_order_items(
            self.order, [{"sku": "SKU-DISC-01", "quantity": 2, "unit_price": "100000"}]
        )
        item = OrderItem.objects.get(order=self.order)
        self.assertEqual(item.discount, Decimal("0"))
        self.assertIsNone(item.discount_percent)
        self.assertEqual(item.line_total, Decimal("200000"))


class OrderItemInputSerializerDiscountTests(SimpleTestCase):
    def test_accepts_discount_and_percent(self):
        serializer = OrderItemInputSerializer(
            data={"sku": "X", "quantity": 1, "discount": "1000", "discount_percent": "12.5"}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["discount"], Decimal("1000"))
        self.assertEqual(serializer.validated_data["discount_percent"], Decimal("12.5"))

    def test_rejects_out_of_range_discounts(self):
        for field, value in (("discount", "-1"), ("discount_percent", "101")):
            with self.subTest(field=field, value=value):
                serializer = OrderItemInputSerializer(data={"sku": "X", "quantity": 1, field: value})
                self.assertFalse(serializer.is_valid())
                self.assertIn(field, serializer.errors)

    def test_percent_may_be_null_to_clear(self):
        serializer = OrderItemInputSerializer(data={"sku": "X", "quantity": 1, "discount_percent": None})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIsNone(serializer.validated_data["discount_percent"])


class ItemsChangedDiscountTests(_ItemDiscountBase):
    def setUp(self):
        super().setUp()
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=2,
            unit_price=Decimal("100000"),
            discount=Decimal("5000"),
            line_total=Decimal("190000"),
        )

    def test_same_discount_is_not_a_change(self):
        payload = [{"sku": "SKU-DISC-01", "quantity": 2, "unit_price": "100000", "discount": "5000"}]
        self.assertFalse(field_changed(self.order, "items", payload))

    def test_changed_discount_is_a_change(self):
        payload = [{"sku": "SKU-DISC-01", "quantity": 2, "unit_price": "100000", "discount": "6000"}]
        self.assertTrue(field_changed(self.order, "items", payload))

    def test_omitted_discount_means_zero(self):
        # Không gửi discount = bỏ giảm giá (khác unit_price: không gửi = tự tính theo bảng giá).
        payload = [{"sku": "SKU-DISC-01", "quantity": 2, "unit_price": "100000"}]
        self.assertTrue(field_changed(self.order, "items", payload))


class DuplicateOrderDiscountTests(_ItemDiscountBase):
    def test_duplicate_copies_line_discount(self):
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=2,
            unit_price=Decimal("100000"),
            discount=Decimal("5000"),
            discount_percent=Decimal("5"),
            line_total=Decimal("190000"),
        )
        actor = make_admin()
        clone = OrderService.duplicate_order(self.order, actor, emit_event=False)
        item = OrderItem.objects.get(order=clone)
        self.assertEqual(item.discount, Decimal("5000"))
        self.assertEqual(item.discount_percent, Decimal("5"))
        self.assertEqual(item.line_total, Decimal("190000"))


class RestoreRepriceDiscountTests(TestCase):
    """Khôi phục tính lại giá theo bảng giá hiện tại → giảm giá dòng phải đi theo:
    nhập theo % thì % bám giá mới, nhập tiền thì giữ tiền."""

    def setUp(self):
        self.actor = make_admin("restore-discount-actor")
        web = PriceBook.objects.get(code="web")
        self.channel = Channel.objects.create(code="koc-disc", name="KOC Disc", price_book=web)
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000010", phone_normalized="0900000010", code="KH-DISC-02"
        )
        self.product = Product.objects.create(name="SP KOC", sku="SKU-DISC-KOC")
        pkg1 = ProductPackage.objects.create(product=self.product, quantity=1)
        ProductPackagePrice.objects.create(package=pkg1, price_book=web, price=Decimal("200"))

    def _order_with_item(self, **item_kwargs):
        order = Order.objects.create(channel=self.channel, customer=self.customer)
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=2,
            unit_price=Decimal("100"),
            **item_kwargs,
        )
        return order

    def test_percent_discount_follows_new_price(self):
        order = self._order_with_item(discount=Decimal("10"), discount_percent=Decimal("10"), line_total=Decimal("180"))
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(order, self.actor, restored_from=order)
        item = clone.items.get()
        self.assertEqual(item.unit_price, Decimal("200.00"))
        self.assertEqual(item.discount, Decimal("20.00"))
        self.assertEqual(item.discount_percent, Decimal("10"))
        self.assertEqual(item.line_total, Decimal("360.00"))
        self.assertEqual(clone.total_amount, Decimal("360.00"))

    def test_amount_discount_is_kept_on_new_price(self):
        order = self._order_with_item(discount=Decimal("30"), line_total=Decimal("140"))
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(order, self.actor, restored_from=order)
        item = clone.items.get()
        self.assertEqual(item.discount, Decimal("30"))
        self.assertIsNone(item.discount_percent)
        self.assertEqual(item.line_total, Decimal("340.00"))
