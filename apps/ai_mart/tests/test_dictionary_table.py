from django.db import connection
from django.test import TestCase

from apps.ai_mart.domain.dictionary import dictionary_rows


def _sql_one(sql, params=None):
    with connection.cursor() as cur:
        cur.execute(sql, params or [])
        return cur.fetchone()[0]


class TuDienMaTests(TestCase):
    def test_seed_matches_domain(self):
        # nhu_cau/nhom_nhu_cau không nằm trong dictionary_rows() — chúng được
        # need_dictionary.refresh_need_dictionary() đồng bộ TRỰC TIẾP từ bảng
        # need_types/need_groups (xem apps/ai_mart/domain/need_dictionary.py),
        # nên loại 2 nhóm đó ra trước khi so khớp với domain tĩnh.
        n = _sql_one(
            "SELECT count(*) FROM ai.tu_dien_ma"
            " WHERE nhom_ma NOT IN ('nhu_cau', 'nhom_nhu_cau')"
        )
        self.assertEqual(n, len(dictionary_rows()))

    def test_nhan_function(self):
        self.assertEqual(_sql_one("SELECT ai.nhan('trang_thai_don', '3')"), "Giao thành công")
        self.assertEqual(
            _sql_one("SELECT ai.nhan('trang_thai_sale', 'no_answer_5')"),
            "Không nghe máy lần 5",
        )
        self.assertIsNone(_sql_one("SELECT ai.nhan('trang_thai_don', '999')"))
        self.assertIsNone(_sql_one("SELECT ai.nhan('trang_thai_don', NULL)"))

    def test_che_sdt(self):
        self.assertEqual(_sql_one("SELECT ai.che_sdt('0912345678')"), "0912***678")
        self.assertIsNone(_sql_one("SELECT ai.che_sdt('')"))
        self.assertIsNone(_sql_one("SELECT ai.che_sdt(NULL)"))
        # SĐT ngắn bất thường: không được lộ chữ số nào.
        self.assertEqual(_sql_one("SELECT ai.che_sdt('12345')"), "***")
