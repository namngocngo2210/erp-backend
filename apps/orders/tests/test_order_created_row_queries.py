"""`build_order_created_row` đường 1 đơn: nạp lại đơn với loader của trang list
(audit 2026-09-23 perf §2.9 — đo 9 query/event: lazy-load channel, customer,
assessment, needs, recall, recall_entries, items + reminder + duplicate map).
Sau: 1 SELECT (select_related) + 3 prefetch + duplicate_map + reminder_map = 6.
"""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.order_event_publisher import (
    build_order_created_row,
    build_order_rows_context,
)
from apps.orders.models import Order, OrderAssessment, OrderItem


class OrderCreatedRowQueryCountTests(TestCase):
    def setUp(self):
        channel = Channel.objects.create(code="rowq-ch", name="Row Q", owner="Row")
        customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000070", phone_normalized="0900000070"
        )
        self.order = Order.objects.create(
            channel=channel, customer=customer, name="O", phone="0900000071",
            status=0, order_source="landing",
        )
        product = Product.objects.create(name="SP", sku="SKU-ROWQ", price=1)
        OrderItem.objects.create(
            order=self.order, product=product, product_name="SP", sku="SKU-ROWQ",
            quantity=1, unit_price=1, line_total=1,
        )
        OrderAssessment.objects.create(order=self.order, sales_status_code="closed")

    def test_single_order_path_bounded(self):
        order = Order.objects.get(pk=self.order.pk)  # instance "trần" như call site
        with CaptureQueriesContext(connection) as ctx:
            row = build_order_created_row(order)
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], self.order.id)
        self.assertEqual(len(row["items"]), 1)
        self.assertIsNotNone(row["crm"])
        self.assertLessEqual(
            len(ctx.captured_queries), 6,
            "\n".join(q["sql"][:120] for q in ctx.captured_queries),
        )

    def test_batch_context_path_adds_no_query(self):
        from apps.orders.repositories import OrderRepository

        orders = list(
            OrderRepository._with_related_crm(
                Order.objects.filter(pk=self.order.pk), with_journey_events=False, lean_list_columns=False
            )
        )
        context = build_order_rows_context(orders)
        with CaptureQueriesContext(connection) as ctx:
            row = build_order_created_row(orders[0], context=context)
        self.assertIsNotNone(row)
        self.assertEqual(len(ctx.captured_queries), 0)
