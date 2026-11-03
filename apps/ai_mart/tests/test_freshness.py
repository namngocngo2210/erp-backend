"""Freshness metadata views — empower the agent to understand ETL delays."""

from django.db import connection
from django.test import TestCase

from apps.ai_mart.application import etl_service
from .helpers import mk_order


class MetadataViewTests(TestCase):
    def test_huong_dan_bang_lists_don_hang_columns(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM ai.huong_dan_bang"
                " WHERE ten_bang = 'don_hang' AND mo_ta_cot IS NOT NULL"
            )
            self.assertGreater(cur.fetchone()[0], 50)

    def test_tinh_trang_du_lieu_reflects_run(self):
        mk_order(status=3)
        etl_service.rebuild_all()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT trang_thai_text, so_dong, du_lieu_toi_moc FROM ai.tinh_trang_du_lieu"
                " WHERE ten_bang = 'ai.don_hang'"
            )
            trang_thai, so_dong, du_lieu_toi_moc = cur.fetchone()
        self.assertEqual(trang_thai, "Bình thường")
        self.assertGreaterEqual(so_dong, 1)
        # M1: du_lieu_toi_moc = watermark __hot__ ghi bởi rebuild_all() — phải có
        # ngay sau lần chạy đầu tiên, không chỉ sau sync_hot().
        self.assertIsNotNone(du_lieu_toi_moc)
