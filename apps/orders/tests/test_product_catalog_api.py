"""Contract của endpoint catalog sản phẩm GET /api/orders/products.

Trọng tâm: chế độ payload SLIM cho các ô lọc theo sản phẩm (chỉ cần id/sku/name).
Payload đầy đủ kèm packages + giá theo bảng giá được test ở test_price_book_api.py.
"""
from __future__ import annotations

from decimal import Decimal

from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from rest_framework.test import APIClient

from apps.orders.models import Product, ProductPackage
from apps.users.tests.helpers import make_admin

_SLIM_URL = "/api/orders/products?limit=100&offset=0&slim=1"
_FULL_URL = "/api/orders/products?limit=100&offset=0"


class ProductCatalogSlimTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin("catalog-slim-admin"))
        self.product = Product.objects.create(
            name="Thảm massage TMS01", sku="TMS01", price=Decimal("100")
        )
        ProductPackage.objects.create(product=self.product, quantity=1, price=Decimal("100"))
        Product.objects.create(name="SP đã tắt", sku="OFF-01", price=Decimal("50"), is_active=False)

    def test_slim_returns_only_option_fields(self):
        response = self.client.get(_SLIM_URL)
        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json() if item["sku"] == "TMS01")
        self.assertEqual(row, {"id": self.product.id, "sku": "TMS01", "name": "Thảm massage TMS01"})

    def test_slim_keeps_active_filter_and_search(self):
        skus = {row["sku"] for row in self.client.get(_SLIM_URL).json()}
        self.assertIn("TMS01", skus)
        self.assertNotIn("OFF-01", skus)

        found = self.client.get(f"{_SLIM_URL}&q=tms").json()
        self.assertEqual([row["sku"] for row in found], ["TMS01"])

    def test_slim_skips_package_and_price_queries(self):
        """Lý do tồn tại của slim: ô lọc chỉ cần SKU, không được kéo theo gói + giá."""
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(_SLIM_URL)
        touched = " ".join(query["sql"] for query in ctx.captured_queries)
        self.assertNotIn("product_packages", touched)
        self.assertNotIn("product_package_prices", touched)

    def test_slim_and_full_have_different_etags(self):
        """Cùng tham số phân trang mà chung ETag thì client sẽ nhận 304 với payload sai shape."""
        slim = self.client.get(_SLIM_URL)
        full = self.client.get(_FULL_URL)
        self.assertNotEqual(slim["ETag"], full["ETag"])

    def test_slim_supports_conditional_request(self):
        etag = self.client.get(_SLIM_URL)["ETag"]
        again = self.client.get(_SLIM_URL, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(again.status_code, 304)
