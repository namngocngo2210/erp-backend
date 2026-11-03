"""Cron auto_sync_invoice_full cập nhật đơn giá dòng theo hoá đơn KV: phải giữ
giảm giá dòng khi tính lại line_total và nhận giảm giá mới từ KV."""
from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.infrastructure.tasks.order_tasks_impl.auto_sync_invoice_full import (
    _apply_invoice_detail_price,
)
from apps.orders.models import Order, OrderItem


class ApplyInvoiceDetailPriceTests(TestCase):
    def setUp(self):
        channel = Channel.objects.create(code="sync-ch", name="Sync")
        customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000011", phone_normalized="0900000011", code="KH-SYNC-01"
        )
        self.order = Order.objects.create(channel=channel, customer=customer)
        product = Product.objects.create(name="SP", sku="SKU-SYNC-01", price=Decimal("100000"))
        self.item = OrderItem.objects.create(
            order=self.order,
            product=product,
            product_name=product.name,
            sku=product.sku,
            quantity=2,
            unit_price=Decimal("100000"),
            discount=Decimal("5000"),
            line_total=Decimal("190000"),
        )

    def test_new_price_keeps_existing_discount(self):
        changed = _apply_invoice_detail_price(self.order, {"productCode": "SKU-SYNC-01", "price": 120000})
        self.item.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(self.item.unit_price, Decimal("120000"))
        self.assertEqual(self.item.discount, Decimal("5000"))
        self.assertEqual(self.item.line_total, Decimal("230000"))

    def test_discount_from_invoice_is_applied(self):
        changed = _apply_invoice_detail_price(
            self.order, {"productCode": "SKU-SYNC-01", "price": 100000, "discount": 8000}
        )
        self.item.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(self.item.discount, Decimal("8000"))
        self.assertEqual(self.item.line_total, Decimal("184000"))

    def test_unchanged_price_and_discount_is_noop(self):
        changed = _apply_invoice_detail_price(
            self.order, {"productCode": "SKU-SYNC-01", "price": 100000, "discount": 5000}
        )
        self.assertFalse(changed)

    def test_price_after_tax_is_preferred(self):
        changed = _apply_invoice_detail_price(
            self.order,
            {"productCode": "SKU-SYNC-01", "price": 92593, "invoiceDetailTaxs": [{"priceAfterTax": 100000}]},
        )
        self.item.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(self.item.unit_price, Decimal("100000"))

    def test_unknown_sku_is_noop(self):
        self.assertFalse(_apply_invoice_detail_price(self.order, {"productCode": "SKU-NOPE", "price": 1}))
