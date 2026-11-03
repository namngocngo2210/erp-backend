"""Khoá payload THỰC với serializer định nghĩa schema (transport/serializers.py):
lệch giữa hai bên nghĩa là OpenAPI đang nói dối trợ lý. Serializer chỉ phục vụ
drf-spectacular — nếu test này đỏ, SỬA SERIALIZER (schema theo payload), không
sửa test để "cho qua"."""

import pytest
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from apps.catalog.models import Product
from apps.daily_report.transport import serializers as s
from apps.facebook_ads.models import FacebookAdAccount, FacebookAdAccountDailySpend, FacebookApp
from apps.orders.models import OrderItem

from .helpers import TOKEN, client, rebuild, seed_order

D = date(2026, 9, 2)
P = {"from": "2026-09-02", "to": "2026-09-02"}
PAGE_KEYS = {"meta", "data", "limit", "next_cursor"}


def _fields(serializer, *, exclude: tuple[str, ...] = ()) -> set[str]:
    inst = serializer() if isinstance(serializer, type) else serializer
    return set(inst.fields.keys()) - set(exclude)


def _nested_fields(serializer_cls, name: str) -> set[str]:
    return set(serializer_cls().fields[name].fields.keys())


# Cache report/endpoint: mỗi test một LocMemCache riêng (backend/conftest.py), không cache.clear() Redis dùng chung.
pytestmark = pytest.mark.usefixtures("isolated_cache")


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class PayloadShapeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        order = seed_order(D, status=3, invoice_status=1, sales_status="closed")
        prod = Product.objects.create(name="Ghế", sku="MSC06")
        OrderItem.objects.create(order=order, product=prod, sku="MSC06", quantity=1, unit_price=1, line_total=1)
        app = FacebookApp.objects.create(name="A", app_id="a", app_secret="s", access_token="t")
        acc = FacebookAdAccount.objects.create(app=app, name="TK", account_id="1", fb_id="act_1")
        FacebookAdAccountDailySpend.objects.create(ad_account=acc, date=D, spend=Decimal("1000"))
        rebuild()

    def _first_row(self, url, params=P):
        body = client().get(url, params).json()
        self.assertEqual(set(body.keys()), PAGE_KEYS, url)
        self.assertTrue(body["data"], f"{url} không có dòng để so shape")
        return body["data"][0]

    def test_order_row_matches_serializer(self):
        for url in ("/api/daily-report/leads/stats", "/api/daily-report/orders/stats"):
            row = self._first_row(url)
            self.assertEqual(set(row.keys()), _fields(s.OrderRowSerializer), url)
            for nested in ("order_status", "invoice_status", "customer", "amounts", "items", "sales_status", "needs", "age", "calls"):
                self.assertEqual(set(row[nested].keys()), _nested_fields(s.OrderRowSerializer, nested), f"{url} {nested}")
            self.assertEqual(set(row["channel"].keys()), _nested_fields(s.OrderRowSerializer, "channel"))

    def test_revenue_row_matches_serializer(self):
        self.assertEqual(set(self._first_row("/api/daily-report/revenue").keys()), _fields(s.OrderLineRowSerializer))

    def test_pipeline_row_matches_serializer(self):
        seed_order(D, sales_status="call_back_later")
        rebuild()
        row = self._first_row("/api/daily-report/pipeline", {"as_of": "2026-09-02"})
        self.assertEqual(set(row.keys()), _fields(s.OpenLeadRowSerializer))

    def test_ads_row_matches_serializer(self):
        body = client().get("/api/daily-report/ads", P).json()
        self.assertEqual(set(body["data"][0].keys()), _fields(s.AdSpendRowSerializer))
        self.assertEqual(set(body["meta"].keys()), _fields(s.MetaSerializer))

    def test_definitions_matches_serializer(self):
        data = client().get("/api/daily-report/definitions").json()["data"]
        self.assertEqual(set(data.keys()), _fields(s.DefinitionsSerializer))

    def test_health_matches_serializer(self):
        body = client().get("/api/daily-report/health").json()
        self.assertEqual(set(body.keys()), _fields(s.HealthSerializer))

    def test_meta_matches_serializer_outside_ads(self):
        meta = client().get("/api/daily-report/leads/stats", P).json()["meta"]
        self.assertEqual(set(meta.keys()), _fields(s.MetaSerializer, exclude=("spend_as_of", "attribution")))
