"""Ô tìm kiếm phiếu bảo hành phải sinh `ILIKE` trên cột có GIN trigram bare-column.

Audit 2026-09-23 C1: `order__phone__icontains` sinh `UPPER(phone) LIKE UPPER(%s)` →
Parallel Seq Scan `orders` (EXPLAIN prod), còn `phone ILIKE %s` đi Bitmap
`order_phone_trgm_idx`. Test này chốt dạng SQL để không ai đổi ngược về `icontains`
cho các cột bare-column; riêng `name` có functional index `UPPER(name::text)` nên
`UPPER(` được phép xuất hiện ĐÚNG MỘT lần.
"""
from django.test import SimpleTestCase

from apps.warranty.claim_repository import WarrantyClaimRepository


class ClaimSearchSqlTests(SimpleTestCase):
    def test_search_uses_ilike_on_trigram_columns(self):
        sql = str(WarrantyClaimRepository.claims_queryset(q="0909", status=None).query)

        for column in ("phone", "phone_normalized", "shipping_code", "order_code"):
            self.assertIn(f'"orders"."{column}" ILIKE', sql, column)
            self.assertNotIn(f'UPPER("orders"."{column}"', sql, column)
        # `name`: functional index trên UPPER(name::text) — giữ icontains.
        self.assertEqual(sql.count("UPPER("), 2, sql)  # UPPER(name) LIKE UPPER(%s)
