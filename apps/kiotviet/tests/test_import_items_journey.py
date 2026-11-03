"""Đồng bộ hoá đơn KiotViet sửa dòng hàng → journey "Đổi sản phẩm / số lượng" (2026-09-25).

Ca đơn 566784: số lượng đổi 1 → 2 mà timeline trống — đồng bộ xoá/ghi lại toàn bộ
OrderItem nên không để lại vết.
"""
from __future__ import annotations

from django.test import TestCase

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.infrastructure.services.import_invoices_service import ImportInvoicesService
from apps.orders.models import Order, OrderItem, OrderJourneyEvent
from apps.orders.sheet_rules import ORDER_ITEMS_CHANGED_EVENT


class KiotVietSyncItemsJourneyTests(TestCase):
    def setUp(self):
        channel = Channel.objects.create(code="kv-journey-items", name="KV Journey Items")
        customer = Customer.objects.create(
            full_name="KH KV Items", phone_raw="0909555222", phone_normalized="0909555222"
        )
        product = Product.objects.create(name="Máy DG11", sku="DG11", price=1500000)
        self.order = Order.objects.create(
            channel=channel, customer=customer, name="KH KV Items", phone="0909555222"
        )
        OrderItem.objects.create(
            order=self.order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=1,
            unit_price=1500000,
            line_total=1500000,
        )

    def _items_events(self):
        return list(OrderJourneyEvent.objects.filter(order=self.order, event_type=ORDER_ITEMS_CHANGED_EVENT))

    def _invoice(self, quantity: int) -> dict:
        return {
            "invoiceDetails": [
                {"productCode": "DG11", "productName": "Máy DG11", "quantity": quantity, "price": 1500000}
            ]
        }

    def test_invoice_quantity_change_is_recorded_as_kiotviet(self):
        ImportInvoicesService._upsert_products_and_items(self.order, self._invoice(2))

        [event] = self._items_events()
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.payload_json["source"], "kiotviet_sync")
        self.assertIn("1 → 2", event.note)

    def test_first_sync_of_new_order_is_not_a_change(self):
        OrderItem.objects.filter(order=self.order).delete()

        ImportInvoicesService._upsert_products_and_items(self.order, self._invoice(2))

        self.assertEqual(self._items_events(), [])
