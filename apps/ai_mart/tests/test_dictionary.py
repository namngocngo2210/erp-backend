"""Mọi mã closed-set xuất hiện trong ai.don_hang phải có nhãn — cột _ma có giá trị
mà _text NULL nghĩa là từ điển thiếu mã: chính vấn đề spec sinh ra để giải."""

from django.db import connection
from django.test import TestCase

from apps.ai_mart.domain.coverage import PAIRS
from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.don_hang import MART
from apps.ai_mart.tests.helpers import mk_order


class DictionaryCoverageTests(TestCase):
    def test_no_untranslated_codes(self):
        """Dựng dữ liệu qua fixture chung rồi rebuild — dữ liệu test nào lọt vào
        mart cũng phải dịch được hết."""
        order = mk_order(status=3)
        loaders.full_rebuild(MART)
        gaps = []
        with connection.cursor() as cur:
            for ma, text in PAIRS:
                cur.execute(
                    f"SELECT DISTINCT {ma} FROM ai.don_hang"
                    f" WHERE {ma} IS NOT NULL AND {text} IS NULL"
                )
                gaps.extend((ma, row[0]) for row in cur.fetchall())
        self.assertEqual(gaps, [], f"mã chưa có nhãn: {gaps}")
