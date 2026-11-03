"""API sản phẩm nhận/trả `short_name` (docs/product-short-name.md)."""
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.users.tests.helpers import make_admin


class ProductShortNameApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin("short-name-admin"))

    def _create(self, **extra):
        payload = {
            "name": "GHẾ MASSAGE TOÀN THÂN CAO CẤP OHI01 (HOÁ ĐƠN)",
            "sku": "SN-OHI01",
            "packages": [{"quantity": 1, "prices": {"facebook": "100000"}}],
            **extra,
        }
        return self.client.post("/api/orders/products", payload, format="json")

    def test_create_and_detail_return_short_name(self):
        response = self._create(short_name="  Ghế OHI01  ")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["short_name"], "Ghế OHI01")
        self.assertEqual(Product.objects.get(sku="SN-OHI01").short_name, "Ghế OHI01")

    def test_create_without_short_name_defaults_empty(self):
        response = self._create()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["short_name"], "")

    def test_patch_updates_short_name_only(self):
        created = self._create().json()
        response = self.client.patch(
            f"/api/orders/products/{created['id']}", {"short_name": "Ghế OHI01"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        product = Product.objects.get(pk=created["id"])
        self.assertEqual(product.short_name, "Ghế OHI01")
        self.assertEqual(product.name, "GHẾ MASSAGE TOÀN THÂN CAO CẤP OHI01 (HOÁ ĐƠN)")

    def test_short_name_too_long_returns_400(self):
        response = self._create(short_name="x" * 121)
        self.assertEqual(response.status_code, 400)
        self.assertIn("short_name", response.json())

    def test_list_returns_short_name(self):
        self._create(short_name="Ghế OHI01")
        response = self.client.get("/api/orders/products?limit=100&offset=0")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        rows = body["results"] if isinstance(body, dict) else body
        [row] = [r for r in rows if r["sku"] == "SN-OHI01"]
        self.assertEqual(row["short_name"], "Ghế OHI01")
