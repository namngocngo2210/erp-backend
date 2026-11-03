"""Import hoá đơn KiotViet ghi đè `products.name` nhưng KHÔNG đụng `short_name`
(docs/product-short-name.md) — tên ngắn là dữ liệu của người dùng CRM."""
from __future__ import annotations

from django.test import TestCase

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.infrastructure.services.import_invoices_service import ImportInvoicesService
from apps.orders.models import Order


class KiotVietImportKeepsShortNameTests(TestCase):
    def test_import_overwrites_name_but_keeps_short_name(self):
        channel = Channel.objects.create(code="kv-short-name", name="KV Short Name")
        customer = Customer.objects.create(
            full_name="KH KV Short", phone_raw="0909555333", phone_normalized="0909555333"
        )
        product = Product.objects.create(name="Máy DG11", sku="DG11", short_name="Máy DG11 gọn", price=1500000)
        order = Order.objects.create(channel=channel, customer=customer, name="KH KV Short", phone="0909555333")
        invoice = {
            "invoiceDetails": [
                {"productCode": "DG11", "productName": "MÁY DG11 - TÊN HOÁ ĐƠN MỚI", "quantity": 1, "price": 1500000}
            ]
        }

        ImportInvoicesService._upsert_products_and_items(order, invoice)

        product.refresh_from_db()
        self.assertEqual(product.name, "MÁY DG11 - TÊN HOÁ ĐƠN MỚI")
        self.assertEqual(product.short_name, "Máy DG11 gọn")
