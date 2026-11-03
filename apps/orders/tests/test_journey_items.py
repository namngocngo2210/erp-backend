"""Journey "Tạo đơn" + "Đổi sản phẩm / số lượng" (2026-09-25).

Sinh ra từ đơn 566784: số lượng DG11 đổi 1 → 2 mà timeline trống, và không trả lời
được "ai tạo đơn" — dòng hàng bị xoá/ghi lại toàn bộ nên không để lại vết nào.
"""
from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services import journey_items_service
from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.models import Order, OrderItem, OrderJourneyEvent, OrderStatus
from apps.orders.sheet_rules import ORDER_CREATED_EVENT, ORDER_ITEMS_CHANGED_EVENT
from apps.users.tests.helpers import make_admin


class DescribeItemsDiffTests(SimpleTestCase):
    def test_quantity_change(self):
        changes = journey_items_service.describe_items_diff({"DG11": ("Máy DG11", 1)}, {"DG11": ("Máy DG11", 2)})
        self.assertEqual(changes, ["Máy DG11 (DG11): 1 → 2"])

    def test_added_and_removed(self):
        changes = journey_items_service.describe_items_diff({"A": ("SP A", 1)}, {"B": ("SP B", 3)})
        self.assertEqual(changes, ["Bỏ SP A (A) x1", "Thêm SP B (B) x3"])

    def test_same_items_no_change(self):
        snapshot = {"A": ("SP A", 2)}
        self.assertEqual(journey_items_service.describe_items_diff(snapshot, dict(snapshot)), [])

    def test_long_name_is_shortened(self):
        name = "Máy massage đầu gối 5 chế độ hoạt động, dùng để massage thư giãn"
        [change] = journey_items_service.describe_items_diff({"DG11": (name, 1)}, {"DG11": (name, 2)})
        self.assertTrue(change.startswith("Máy massage đầu gối 5 chế độ hoạt động,…"))
        self.assertTrue(change.endswith("(DG11): 1 → 2"))


class _OrderFixture(TestCase):
    def setUp(self):
        self.actor = make_admin("journey-items-admin")
        self.channel = Channel.objects.create(code="journey-items", name="Journey Items")
        self.customer = Customer.objects.create(
            full_name="KH Items", phone_raw="0909555111", phone_normalized="0909555111"
        )
        self.product = Product.objects.create(name="Máy DG11", sku="DG11", price=1500000)
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="KH Items",
            phone="0909555111",
            status=OrderStatus.NEW,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=1,
            unit_price=1500000,
            line_total=1500000,
        )

    def _items_events(self):
        return list(OrderJourneyEvent.objects.filter(order=self.order, event_type=ORDER_ITEMS_CHANGED_EVENT))


class InlineUpdateItemsJourneyTests(_OrderFixture):
    def test_quantity_change_records_actor_and_diff(self):
        OrderService.update_order_inline(
            self.order, {"items": [{"product_id": self.product.id, "quantity": 2}]}, self.actor
        )

        [event] = self._items_events()
        self.assertEqual(event.actor_id, self.actor.id)
        self.assertEqual(event.note, "Máy DG11 (DG11): 1 → 2")
        self.assertEqual(event.payload_json["before"], {"DG11": 1})
        self.assertEqual(event.payload_json["after"], {"DG11": 2})

    def test_unchanged_items_write_no_event(self):
        OrderService.update_order_inline(
            self.order, {"items": [{"product_id": self.product.id, "quantity": 1}]}, self.actor
        )

        self.assertEqual(self._items_events(), [])


class OrderCreatedJourneyTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="journey-created", name="Journey Created")

    def test_manual_create_records_creator(self):
        actor = make_admin("journey-creator")
        order = OrderService.create_order_manual(
            {"channel": self.channel, "name": "Tay", "phone": "0909123459", "raw_address": "x"},
            actor,
        )

        event = OrderJourneyEvent.objects.get(order=order, event_type=ORDER_CREATED_EVENT)
        self.assertEqual(event.actor_id, actor.id)
        self.assertEqual(event.payload_json["source"], "manual")

    def test_created_via_is_recorded_as_source(self):
        order = OrderService.create_order_manual(
            {"channel": self.channel, "name": "Tay", "phone": "0909123460", "raw_address": "x"},
            None,
            created_via="lark",
        )

        event = OrderJourneyEvent.objects.get(order=order, event_type=ORDER_CREATED_EVENT)
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.payload_json["source"], "lark")
