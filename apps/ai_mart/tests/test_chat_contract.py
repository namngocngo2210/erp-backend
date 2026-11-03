"""Hai điều test_contract chung không bắt được:
1. Ngoại lệ PII phải được TUYÊN BỐ trong COMMENT, không im lặng.
2. Mọi mã LLM có thể sinh ra đều phải dịch được — cột _ma có giá trị mà _text
   NULL nghĩa là từ điển thiếu, đúng bệnh spec ai_mart sinh ra để chữa.
"""

import json

from django.db import connection
from django.test import TestCase

from apps.ai_mart.domain.chat_dictionary import GIAI_DOAN_CHAT, MUC_DO_QUAN_TAM
from apps.ai_mart.domain.chat_extract import load_need_labels, load_reason_labels
from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.chat_hoi_thoai import MART
from apps.ai_mart.tests import chat_helpers as ch
from apps.ai_mart.tests.test_masking import SKIP_COLUMNS

# Đọc THẲNG từ SKIP_COLUMNS của test_masking, trừ đi 2 cột cuoc_goi (ngoại lệ
# của cuộc gọi, khai theo spec khác) — để hai danh sách KHÔNG THỂ lệch nhau
# âm thầm. Ai đó nới SKIP_COLUMNS thêm 1 cột mà quên viết COMMENT thì test này
# tự động bắt được cột mới, không phải sửa lại PII_EXCEPTIONS bằng tay.
PII_EXCEPTIONS = SKIP_COLUMNS - {("cuoc_goi", "transcript"), ("cuoc_goi", "tom_tat_ai")}


class ChatContractTests(TestCase):
    def test_pii_exception_columns_declare_the_ban_in_their_comment(self):
        missing = []
        with connection.cursor() as cur:
            for table, col in PII_EXCEPTIONS:
                cur.execute(
                    "SELECT col_description(a.attrelid, a.attnum) FROM pg_attribute a"
                    " WHERE a.attrelid = ('ai.' || quote_ident(%s))::regclass"
                    " AND a.attname = %s",
                    [table, col],
                )
                comment = (cur.fetchone() or [""])[0] or ""
                if "KHÔNG trích SĐT/địa chỉ từ cột này" not in comment:
                    missing.append(f"ai.{table}.{col}")
        self.assertEqual(missing, [], f"ngoại lệ PII chưa tuyên bố trong COMMENT: {missing}")

    def test_don_id_comment_warns_about_low_coverage(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT col_description(a.attrelid, a.attnum) FROM pg_attribute a"
                " WHERE a.attrelid = 'ai.hoi_thoai'::regclass AND a.attname = 'don_id'"
            )
            (comment,) = cur.fetchone()
        # C7: agent KHÔNG được đọc don_id IS NULL thành "khách không mua"
        self.assertIn("KHÔNG có nghĩa là", comment)

    def test_every_need_and_reason_code_resolves_in_the_dictionary(self):
        # nhu_cau_ai_text/ly_do_khong_mua_text dùng coalesce(ai.nhan(...), x.ma)
        # (chat_hoi_thoai.py) — mã thiếu trong từ điển vẫn tạo ra MỘT phần tử
        # trong string_agg (chính mã đó), nên đếm số phần tử KHÔNG bao giờ bắt
        # được mã mồ côi. Phải hỏi thẳng ai.nhan() cho từng mã một.
        missing = []
        with connection.cursor() as cur:
            for code in sorted(load_need_labels()):
                cur.execute("SELECT ai.nhan('nhu_cau', %s)", [code])
                if cur.fetchone()[0] is None:
                    missing.append(f"nhu_cau:{code}")
            for code in sorted(load_reason_labels()):
                cur.execute("SELECT ai.nhan('ly_do_khong_chot', %s)", [code])
                if cur.fetchone()[0] is None:
                    missing.append(f"ly_do_khong_chot:{code}")
        self.assertEqual(missing, [], f"mã mồ côi trong ai.tu_dien_ma: {missing}")

    def test_every_label_code_resolves(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        ch.mk_message(conv, page, content="test")
        clean = {
            "nhu_cau": sorted(load_need_labels()),
            "ly_do_khong_mua": sorted(load_reason_labels()),
            "giai_doan": GIAI_DOAN_CHAT[0][0],
            "muc_do_quan_tam": MUC_DO_QUAN_TAM[0][0],
        }
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO ai_stg.hoi_thoai_nhan (hoi_thoai_id, extract_payload, extract_status)"
                " VALUES (%s, %s, 'done')",
                [conv, json.dumps({"clean": clean})],
            )
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT nhu_cau_ai, nhu_cau_ai_text, ly_do_khong_mua_ma, ly_do_khong_mua_text,"
                " giai_doan_text, muc_do_quan_tam_text FROM ai.hoi_thoai WHERE hoi_thoai_id = %s",
                [conv],
            )
            nhu_cau, nhu_cau_text, ly_do, ly_do_text, giai_doan, muc_do = cur.fetchone()
        # Số nhãn phải bằng số mã: chứng minh đường join không rớt/nhân đôi mã
        # nào (test_every_need_and_reason_code_resolves_in_the_dictionary ở
        # trên mới là test bắt mã mồ côi thật — xem comment ở đó).
        self.assertEqual(len(nhu_cau_text.split(", ")), len(nhu_cau))
        self.assertEqual(len(ly_do_text.split(", ")), len(ly_do))
        self.assertIsNotNone(giai_doan)
        self.assertIsNotNone(muc_do)
