"""Ô tìm kiếm Danh sách contact phải sinh `ILIKE` (GIN trigram bare-column `customers`).

Audit 2026-09-23 C1: `customer__full_name__icontains` / `phone_*__icontains` sinh
`UPPER(col) LIKE UPPER(%s)` nên planner bỏ `cust_fullname_trgm_idx`,
`cust_phone_norm_trgm_idx`, `cust_phone_raw_trgm_idx` → seq-scan. Chốt dạng SQL.
"""
from django.test import TestCase

from apps.orders.application.services.contact_list_service import ContactListService
from apps.users.tests.helpers import make_admin


class ContactListSearchSqlTests(TestCase):
    def test_search_uses_ilike_without_upper(self):
        admin = make_admin("contact-search-sql-admin")
        queryset = ContactListService._base_queryset(
            user=admin, date_from=None, date_to=None, q="Nguyễn"
        )
        sql = str(queryset.query)

        for column in ("full_name", "phone_normalized", "phone_raw"):
            self.assertIn(f'"customers"."{column}" ILIKE', sql, column)
        self.assertNotIn("UPPER(", sql, sql)
