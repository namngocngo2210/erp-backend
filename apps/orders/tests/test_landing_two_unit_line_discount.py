"""Dòng hàng SL 2 luôn được giảm 200k cho cả dòng (100k mỗi sản phẩm) khi ingest."""

from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.orders.application.services.order_service_commands import OrderServiceCommands
from apps.orders.domain.line_discount import auto_line_unit_discount


class AutoLineUnitDiscountTests(SimpleTestCase):
    def test_only_quantity_two_gets_discount(self):
        price = Decimal("1500000")
        self.assertEqual(auto_line_unit_discount(2, price), Decimal("100000"))
        for qty in (1, 3, 4):
            self.assertEqual(auto_line_unit_discount(qty, price), Decimal("0"))

    def test_clamped_to_unit_price(self):
        self.assertEqual(auto_line_unit_discount(2, Decimal("60000")), Decimal("60000"))
        self.assertEqual(auto_line_unit_discount(2, Decimal("0")), Decimal("0"))


class LandingTwoUnitLineDiscountTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="fb-2sp", name="FB 2SP", owner="FB")
        self.product = Product.objects.create(name="Thảm massage", sku="TMS01", price="1500000")

    def _create(self, **extra):
        payload = {
            "channel_id": self.channel.id,
            "name": "Khách A",
            "phone": "0987654321",
            "product_id": self.product.id,
            **extra,
        }
        return OrderServiceCommands.create_order_from_landing(payload)

    def test_quantity_two_line_gets_200k_off(self):
        order = self._create(quantity=2)
        item = order.items.get()
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.discount, Decimal("100000"))
        self.assertEqual(item.line_total, Decimal("2800000"))
        order.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal("2800000"))
        self.assertEqual(order.total_after_discount, Decimal("2800000"))

    def test_quantity_one_line_untouched(self):
        order = self._create(quantity=1)
        item = order.items.get()
        self.assertEqual(item.discount, Decimal("0"))
        self.assertEqual(item.line_total, Decimal("1500000"))

    def test_explicit_total_from_sender_wins(self):
        order = self._create(quantity=2, total_amount="3000000")
        item = order.items.get()
        self.assertEqual(item.discount, Decimal("0"))
        self.assertEqual(item.line_total, Decimal("3000000"))
        order.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal("3000000"))
