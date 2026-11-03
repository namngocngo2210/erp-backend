"""SQL dòng: đúng tập lọc của số gộp cũ, keyset đi hết không sót/lặp."""

from datetime import date

from django.test import TestCase

from apps.catalog.models import Product
from apps.daily_report.infrastructure import queries
from apps.orders.models import OrderItem

from .helpers import rebuild, seed_order

D = date(2026, 9, 2)


class OrderRowsQueryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.delivered = seed_order(D, status=3, invoice_status=1, amount=500_000, sales_status="closed")
        seed_order(D, status=2, invoice_status=1, amount=200_000)                 # awaiting
        seed_order(D, status=6, invoice_status=1, amount=999)                      # huỷ POS → không phải đơn
        cls.lead_only = seed_order(D, status=3, invoice_status=None, amount=999)  # lead chưa HĐ
        seed_order(D, status=3, invoice_status=1, amount=999, branch_id="1000000139")  # chi nhánh ẩn
        seed_order(date(2026, 9, 1), status=3, invoice_status=1, amount=1)        # ngoài kỳ
        prod = Product.objects.create(name="Ghế MSC06", sku="MSC06")
        OrderItem.objects.create(order=cls.delivered, product=prod, sku="MSC06", quantity=1, unit_price=500_000, line_total=500_000)
        OrderItem.objects.create(order=cls.lead_only, product=prod, sku="MSC06", quantity=1, unit_price=999, line_total=999)
        rebuild()

    def test_leads_vs_orders_filter(self):
        leads = queries.order_rows(D, D, invoiced_only=False, after=None, limit=100)
        orders = queries.order_rows(D, D, invoiced_only=True, after=None, limit=100)
        self.assertEqual(len(leads), 5)
        order_ids = {r["don_id"] for r in orders}
        self.assertEqual(len(orders), 2)
        self.assertIn(self.delivered.id, order_ids)
        self.assertNotIn(self.lead_only.id, order_ids)
        self.assertEqual(set(queries.ORDER_COLUMNS), set(leads[0].keys()))
        self.assertIn("nhom_kenh_ban", leads[0])

    def test_keyset_walks_all_without_dup(self):
        seen, after = [], None
        while True:
            page = queries.order_rows(D, D, invoiced_only=False, after=after, limit=2)
            seen += [r["don_id"] for r in page]
            if len(page) < 2:
                break
            after = page[-1]["don_id"]
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), 5)

    def test_order_lines_follow_orders_filter(self):
        lines = queries.order_line_rows(D, D, after=None, limit=100)
        self.assertEqual([r["don_id"] for r in lines], [self.delivered.id])
        self.assertEqual(lines[0]["thanh_tien"], 500_000)
        self.assertEqual(lines[0]["sku"], "MSC06")
        self.assertIn("dong_id", lines[0])

    def test_open_leads_have_full_columns_and_age(self):
        rows = queries.open_lead_rows(D, 30, after=None, limit=100)
        self.assertEqual([r["don_id"] for r in rows], [self.lead_only.id])
        self.assertEqual(rows[0]["tuoi"], 0)
        self.assertIn("ten_khach", rows[0])
