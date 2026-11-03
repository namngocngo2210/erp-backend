"""Kiểm mart ai.khach_hang: 1 dòng/khách, aggregate từ ai.don_hang (không đọc
lại orders). Dùng chung fixture đơn của apps/ai_mart/tests/helpers.py (mk_order)
— bám codebase, factory OrderFactory không tồn tại trong repo này."""

from django.db import connection
from django.test import TestCase

from apps.ai_mart.application import etl_service
from apps.ai_mart.tests.helpers import mk_order


class KhachHangTests(TestCase):
    def test_aggregates_from_don_hang(self):
        o1 = mk_order(status=3)
        mk_order(status=3, customer=o1.customer)
        mk_order(status=6, customer=o1.customer)
        etl_service.rebuild_all()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT so_don_tat_ca, so_don_huy, gioi_tinh_text, sdt_che"
                " FROM ai.khach_hang WHERE khach_id = %s",
                [o1.customer_id],
            )
            so_don, so_huy, gioi_tinh, sdt = cur.fetchone()
        self.assertEqual(so_don, 3)
        self.assertEqual(so_huy, 1)
        self.assertIn(gioi_tinh, (None, "Nam", "Nữ"))
        if sdt is not None:
            self.assertIn("***", sdt)

    def test_customer_only_edit_flows_into_mart(self):
        from apps.customers.models import Customer

        o = mk_order(status=3)
        etl_service.rebuild_all()
        Customer.objects.filter(id=o.customer_id).update(full_name="Tên Mới")
        etl_service.sync_hot()
        with connection.cursor() as cur:
            cur.execute("SELECT ten_khach FROM ai.khach_hang WHERE khach_id = %s", [o.customer_id])
            self.assertEqual(cur.fetchone()[0], "Tên Mới")


class KhachHangIncrementalEquivalenceTests(TestCase):
    """Audit 2026-09-23 C5: lọc id đẩy vào CTE agg/gaps phải cho kết quả Y HỆT
    full rebuild — kể cả khách có nhiều đơn GTC (gaps), đơn huỷ, khách không đơn."""

    @staticmethod
    def _rows():
        with connection.cursor() as cur:
            cur.execute("SELECT * FROM ai.khach_hang ORDER BY khach_id")
            cols = [c[0] for c in cur.description]
            return [
                {c: v for c, v in zip(cols, row) if c != "dong_bo_luc"} for row in cur.fetchall()
            ]

    def test_upsert_by_ids_matches_full_rebuild(self):
        from apps.customers.models import Customer
        from apps.ai_mart.infrastructure import loaders
        from apps.ai_mart.infrastructure.sql import khach_hang

        o1 = mk_order(status=3)
        mk_order(status=3, customer=o1.customer)
        mk_order(status=3, customer=o1.customer)
        mk_order(status=6, customer=o1.customer)
        o2 = mk_order(status=3)
        mk_order(status=0)
        Customer.objects.create(full_name="Không đơn", phone_raw="0999999999", phone_normalized="0999999999")

        etl_service.rebuild_all()
        full = self._rows()
        self.assertGreaterEqual(len(full), 4)
        self.assertIsNotNone(
            next(r for r in full if r["khach_id"] == o1.customer_id)["khoang_cach_mua_lai_trung_vi"]
        )

        all_ids = [r["khach_id"] for r in full]
        with connection.cursor() as cur:
            cur.execute("TRUNCATE ai.khach_hang")
        loaders.upsert_by_ids(khach_hang.MART, all_ids)
        self.assertEqual(self._rows(), full)

        # Upsert MỘT khách chỉ đụng dòng của khách đó, và dòng đó vẫn y hệt.
        loaders.upsert_by_ids(khach_hang.MART, [o2.customer_id])
        self.assertEqual(self._rows(), full)

    def test_incremental_sql_filters_inside_ctes(self):
        from apps.ai_mart.infrastructure.sql import khach_hang

        sql = khach_hang.MART.select_sql("c.id = ANY(%(ids)s)")
        self.assertEqual(sql.count("IN (SELECT id FROM ids)"), 2)
        self.assertIn("ids AS MATERIALIZED", sql)
        self.assertNotIn("IN (SELECT id FROM ids)", khach_hang.MART.select_sql("TRUE"))
