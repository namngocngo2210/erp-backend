"""Ô tìm kiếm trang Resale: `customers.full_name` phải đi `ILIKE` (GIN bare-column).

Audit 2026-09-23 C1. `orders.name` giữ `icontains` vì có functional index
`order_name_upper_trgm_idx` (UPPER(name::text)) — nên `UPPER(` xuất hiện đúng cho
cột đó, KHÔNG được xuất hiện cho `customers.full_name`.
"""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.customers.customer_resale_service import CustomerResaleService
from apps.users.tests.helpers import make_admin


class ResaleSearchSqlTests(TestCase):
    def test_customer_full_name_uses_ilike(self):
        admin = make_admin("resale-search-sql-admin")
        with CaptureQueriesContext(connection) as ctx:
            CustomerResaleService.list_resale_tracking(limit=20, offset=0, q="Nguyễn", user=admin)

        listing_sql = [q["sql"] for q in ctx.captured_queries if "ILIKE" in q["sql"]]
        self.assertTrue(listing_sql, [q["sql"][:160] for q in ctx.captured_queries])
        for sql in listing_sql:
            self.assertIn('"customers"."full_name" ILIKE', sql)
            self.assertNotIn('UPPER("customers"."full_name"', sql)
            self.assertNotIn("UPPER(\"customers\"", sql)
