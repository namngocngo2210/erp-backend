"""Import hoá đơn KiotViet nạp trước Product/Customer theo trang (audit 2026-09-23 T1 §2.4).

Trước: mỗi hoá đơn 3-5 query Customer + 1-3 query Product mỗi dòng → page 100 hoá
đơn ≈ 1000-1500 query. Sau: số SELECT trên `products`/`customers` là hằng theo số
hoá đơn trong trang khi danh mục đã có; kết quả import không đổi.
"""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.catalog.models import Product
from apps.customers.models import Customer
from apps.kiotviet.infrastructure.services._import_service_upsert_mixin import ImportPageCache
from apps.kiotviet.infrastructure.services.import_invoices_service import ImportInvoicesService
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order, OrderItem

_REQUEST = "apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request"


class _MockResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"
        self.text = "{}"

    def json(self):
        return self._payload


def _invoice(i: int, *, sku_count: int = 2, prefix: str = "P") -> dict:
    return {
        "id": 700000 + i,
        "code": f"INV-{prefix}-{i}",
        "customerId": 5000 + i,
        "customerCode": f"C-{prefix}-{i}",
        "customerName": f"Khach {prefix} {i}",
        "customerPhone": f"09{i:08d}",
        "total": 100000 * sku_count,
        "invoiceDelivery": {"address": f"{i} Prefetch St", "price": 10000, "status": 1},
        "invoiceDetails": [
            {"productCode": f"SKU-{prefix}-{k}", "productName": f"Product {k}", "price": 100000, "quantity": 1}
            for k in range(sku_count)
        ],
    }


class ImportPagePrefetchQueryTests(TestCase):
    def setUp(self):
        self.channel = KiotVietChannel.objects.create(
            name="KV Prefetch", branch_id="231174", branch_ids=["231174"],
            client_id="c", client_secret="s", retailer="r", old=False,
        )

    def _run(self, invoices: list[dict]) -> int:
        with patch(_REQUEST, return_value=_MockResponse({"data": invoices})), \
             patch("apps.kiotviet.infrastructure.services.import_invoices_service.map_to_catalog", return_value={}), \
             patch(
                 "apps.kiotviet.infrastructure.services.import_invoices_service.parse_legacy_level3",
                 return_value=dict.fromkeys(("address", "ward", "district", "province", "ward_code", "district_code", "province_code"), ""),
             ):
            with CaptureQueriesContext(connection) as ctx:
                call_command(
                    "import_kiotviet_invoices", "--channel-id", str(self.channel.id),
                    "--branch-ids", "231174", stdout=StringIO(),
                )
        catalog_selects = [
            q["sql"] for q in ctx.captured_queries
            if q["sql"].startswith("SELECT") and ('FROM "products"' in q["sql"] or 'FROM "customers"' in q["sql"])
        ]
        return len(catalog_selects)

    def test_catalog_lookups_constant_n5_vs_n15_when_rows_exist(self):
        # Lần 1 tạo danh mục; lần 2 (rerun) mọi Product/Customer đã có → chỉ 1 query
        # `sku__in` + 1 `code__in` + 1 `phone_normalized__in` cho cả trang.
        small = [_invoice(i, prefix="S") for i in range(5)]
        self._run(small)
        small_selects = self._run(small)

        large = [_invoice(i, prefix="L") for i in range(15)]
        self._run(large)
        large_selects = self._run(large)

        self.assertEqual(
            large_selects, small_selects,
            f"N+1 danh mục: 5 hoá đơn = {small_selects} SELECT products/customers, "
            f"15 hoá đơn = {large_selects}.",
        )
        self.assertLessEqual(small_selects, 3)

    def test_import_result_unchanged(self):
        invoices = [_invoice(i, prefix="R") for i in range(3)]
        self._run(invoices)
        self.assertEqual(Order.objects.filter(kiotviet_channel=self.channel).count(), 3)
        self.assertEqual(Customer.objects.filter(code__startswith="C-R-").count(), 3)
        self.assertEqual(Product.objects.filter(sku__startswith="SKU-R-").count(), 2)
        self.assertEqual(OrderItem.objects.filter(order__kiotviet_channel=self.channel).count(), 6)
        # Rerun với tên SP đổi → cập nhật tên, không tạo trùng.
        for inv in invoices:
            inv["invoiceDetails"][0]["productName"] = "Product 0 renamed"
        self._run(invoices)
        self.assertEqual(Product.objects.get(sku="SKU-R-0").name, "Product 0 renamed")
        self.assertEqual(Product.objects.filter(sku__startswith="SKU-R-").count(), 2)
        self.assertEqual(Order.objects.filter(kiotviet_channel=self.channel).count(), 3)

    def test_same_customer_across_invoices_reuses_created_row(self):
        # Hai hoá đơn cùng khách mới trong một trang: hoá đơn 2 dùng khách hoá đơn 1
        # vừa tạo (qua cache đã chốt), không tạo khách trùng.
        a = _invoice(1, prefix="D")
        b = _invoice(2, prefix="D")
        b["customerId"], b["customerCode"], b["customerPhone"], b["customerName"] = (
            a["customerId"], a["customerCode"], a["customerPhone"], a["customerName"]
        )
        self._run([a, b])
        self.assertEqual(Customer.objects.filter(code=a["customerCode"]).count(), 1)
        self.assertEqual(
            Order.objects.filter(kiotviet_channel=self.channel).values("customer_id").distinct().count(), 1
        )

    def test_discard_evicts_touched_rows_after_failed_invoice(self):
        cache = ImportPageCache.build([_invoice(9, prefix="E")], ImportInvoicesService)
        self.assertTrue(cache.has_sku("SKU-E-0"))
        self.assertIsNone(cache.product("SKU-E-0"))
        product = Product.objects.create(sku="SKU-E-0", name="E0", price=1)
        cache.touch_product(product)
        self.assertIs(cache.product("SKU-E-0"), product)
        cache.discard_invoice()
        # Sau rollback: không còn biết SKU này → hoá đơn sau tra lại DB.
        self.assertFalse(cache.has_sku("SKU-E-0"))
