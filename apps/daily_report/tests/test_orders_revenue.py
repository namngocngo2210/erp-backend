"""Khoá Q2 của spec: dòng của API cộng lại phải BẰNG OrderRepository.summary_overview cùng kỳ."""

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.orders.models import OrderItem
from apps.orders.repositories import OrderRepository

from .helpers import TOKEN, client, fetch_all, rebuild, seed_order

ORDERS_URL = "/api/daily-report/orders/stats"
REVENUE_URL = "/api/daily-report/revenue"
D = date(2026, 9, 2)
P = {"from": "2026-09-02", "to": "2026-09-02"}


def _money(s: str | None) -> int:
    return int(Decimal(s or "0"))


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class OrderRowsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fb = Channel.objects.create(code="facebook-page-t", name="FB T")
        web = Channel.objects.create(code="google-ads-web", name="Web")
        cls.delivered = seed_order(D, status=3, invoice_status=1, amount=500_000, channel=fb, sales_status="closed")
        seed_order(D, status=2, invoice_status=1, amount=200_000, sales_status="closed")   # awaiting
        seed_order(D, status=5, invoice_status=1, amount=150_000)                          # returned
        seed_order(D, status=10, invoice_status=1, amount=120_000)                         # return risk
        seed_order(D, status=99, invoice_status=3, amount=70_000)                          # mã lạ → awaiting
        seed_order(D, status=6, invoice_status=1, amount=999)                              # huỷ POS
        seed_order(D, status=3, invoice_status=2, amount=999)                              # huỷ web KV
        cls.lead_only = seed_order(D, status=3, invoice_status=None, amount=999, sales_status="closed")  # lead, chưa HĐ
        seed_order(D, status=3, invoice_status=1, amount=999, branch_id="1000000139")      # chi nhánh ẩn
        seed_order(D, status=3, invoice_status=1, amount=999, branch_id="1000000477")      # Đại Mỗ
        seed_order(D, status=3, invoice_status=1, amount=999, order_source="foxia")        # mirror
        original = seed_order(D, status=6, invoice_status=1, amount=999)
        seed_order(D, status=3, invoice_status=1, amount=80_000, restored_from=original)   # bản khôi phục sống
        seed_order(D, status=3, invoice_status=1, amount=60_000, invoice_code="HDSPE_777", channel=fb)  # shopee trên kênh FB
        seed_order(D, status=3, invoice_status=None, amount=45_000, channel=web, sales_status="closed")  # lead-only
        prod = Product.objects.create(name="Ghế MSC06", sku="MSC06")
        gel = Product.objects.create(name="Gel", sku="GEL01")
        OrderItem.objects.create(order=cls.delivered, product=prod, sku="MSC06", quantity=1, unit_price=400_000, line_total=400_000)
        OrderItem.objects.create(order=cls.delivered, product=gel, sku="GEL01", quantity=2, unit_price=50_000, line_total=100_000)
        OrderItem.objects.create(order=cls.lead_only, product=prod, sku="MSC06", quantity=1, unit_price=999, line_total=999)
        rebuild()

    def test_matches_summary_overview(self):
        rows = fetch_all(ORDERS_URL, P)
        ref = OrderRepository.summary_overview("2026-09-02", "2026-09-02")
        by_bucket = Counter(r["order_status"]["bucket"] for r in rows)
        money = defaultdict(int)
        for r in rows:
            money[r["order_status"]["bucket"]] += r["amounts"]["total"]
        self.assertEqual(len(rows), ref["total_orders"])
        self.assertEqual(by_bucket["delivered"], ref["delivered_orders"])
        self.assertEqual(by_bucket["awaiting"], ref["awaiting_orders"])
        self.assertEqual(by_bucket["returned"], ref["returned_orders"])
        self.assertEqual(by_bucket["return_risk"], ref["return_risk_orders"])
        self.assertEqual(sum(money.values()), _money(ref["total_revenue"]))
        self.assertEqual(money["delivered"], _money(ref["delivered_revenue"]))
        self.assertEqual(money["awaiting"], _money(ref["awaiting_revenue"]))
        self.assertEqual(money["returned"], _money(ref["returned_revenue"]))
        self.assertEqual(money["return_risk"], _money(ref["return_risk_revenue"]))
        # net_revenue cũ = total − returned
        self.assertEqual(sum(money.values()) - money["returned"], _money(ref["total_revenue"]) - _money(ref["returned_revenue"]))

    def test_expected_numbers(self):
        rows = fetch_all(ORDERS_URL, P)
        self.assertEqual(len(rows), 7)
        self.assertEqual(Counter(r["order_status"]["bucket"] for r in rows), {"delivered": 3, "awaiting": 2, "returned": 1, "return_risk": 1})
        self.assertEqual(sum(r["amounts"]["total"] for r in rows), 500_000 + 200_000 + 150_000 + 120_000 + 70_000 + 80_000 + 60_000)
        self.assertNotIn("cancelled", {r["order_status"]["bucket"] for r in rows})
        by_group = Counter(r["channel_group"] for r in rows)
        self.assertEqual(by_group["facebook"], 1)
        self.assertEqual(by_group["shopee"], 1)
        self.assertEqual(by_group["off_other"], 5)

    def test_revenue_lines_follow_orders_set(self):
        lines = fetch_all(REVENUE_URL, P, limit=1)
        self.assertEqual({l["order_id"] for l in lines}, {self.delivered.id})     # lead_only bị loại
        self.assertEqual(sorted(l["sku"] for l in lines), ["GEL01", "MSC06"])
        self.assertEqual(sum(l["amount"] for l in lines), 500_000)
        order = next(r for r in fetch_all(ORDERS_URL, P) if r["id"] == self.delivered.id)
        self.assertEqual(sum(l["amount"] for l in lines), order["amounts"]["total"])  # không ship/giảm → bằng
        self.assertEqual(order["items"]["line_count"], 2)
        self.assertEqual(sorted(order["items"]["skus"]), ["GEL01", "MSC06"])
        self.assertEqual(lines[0]["order_code"], order["code"])
        self.assertEqual(set(lines[0]), {"line_id", "order_id", "order_code", "created_date", "sku", "product_name", "product_group", "quantity", "unit_price", "amount", "cursor"})

    def test_group_by_gone(self):
        self.assertEqual(client().get(ORDERS_URL, {**P, "group_by": "staff"}).status_code, 400)
        self.assertEqual(client().get(REVENUE_URL, {**P, "group_by": "day"}).status_code, 400)
