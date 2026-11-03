import json
from datetime import UTC, datetime, timedelta

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.chat_tin_nhan import MART
from apps.ai_mart.tests import chat_helpers as ch


class TinNhanTests(TestCase):
    def _row(self, tin_nhan_id):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT hoi_thoai_id, page_ten, chieu_text, loai_tin_text, noi_dung,"
                " co_dinh_kem, gui_luc_ngay FROM ai.tin_nhan WHERE tin_nhan_id = %s",
                [tin_nhan_id],
            )
            return cur.fetchone()

    def test_labels_and_attachment_flag(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        mid = ch.mk_message(
            conv, page, direction="out", type="COMMENT", content="  chào bạn  ",
            attachments=json.dumps([{"type": "photo"}]),
        )
        loaders.full_rebuild(MART)
        hoi_thoai_id, page_ten, chieu, loai, noi_dung, dinh_kem, _ = self._row(mid)
        self.assertEqual(hoi_thoai_id, conv)
        self.assertEqual(page_ten, "Delax Fanpage")
        self.assertEqual(chieu, "Page gửi")
        self.assertEqual(loai, "Bình luận")
        self.assertEqual(noi_dung, "chào bạn")   # btrim, không giữ khoảng trắng thừa
        self.assertTrue(dinh_kem)

    def test_no_attachment_shapes_are_all_false(self):
        # 3 cách "không có đính kèm" trong nguồn: SQL NULL, mảng rỗng, JSON null
        # literal — jsonb_typeof('null'::jsonb) trả chuỗi 'null' (khác SQL NULL),
        # rơi vào nhánh ELSE nếu không xử lý riêng thì m.attachments IS NOT NULL
        # sẽ sai thành TRUE.
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        sql_null = ch.mk_message(conv, page, content="a", attachments=None)
        empty_array = ch.mk_message(conv, page, content="b", attachments=json.dumps([]))
        json_null = ch.mk_message(conv, page, content="c", attachments="null")
        loaders.full_rebuild(MART)
        for mid in (sql_null, empty_array, json_null):
            self.assertFalse(self._row(mid)[5], f"tin_nhan_id={mid}")

    def test_empty_content_is_excluded(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        empty = ch.mk_message(conv, page, content="   ")
        kept = ch.mk_message(conv, page, content="có nội dung")
        loaders.full_rebuild(MART)
        self.assertIsNone(self._row(empty))
        self.assertIsNotNone(self._row(kept))

    def test_date_uses_business_timezone(self):
        # 2026-08-31T18:30Z = 01/09/2026 01:30 giờ VN → phải rơi vào ngày 01/09
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        mid = ch.mk_message(
            conv, page,
            inserted_at=datetime(2026, 8, 31, 18, 30, tzinfo=UTC),
        )
        loaders.full_rebuild(MART)
        self.assertEqual(str(self._row(mid)[6]), "2026-09-01")

    def test_incremental_upsert_matches_full_rebuild(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        ch.mk_message(conv, page, content="tin 1")
        ch.mk_message(conv, page, content="tin 2")
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai.tin_nhan")
            (before,) = cur.fetchone()

        long_ago = timezone.now() - timedelta(days=1)
        ids = loaders.candidate_ids(MART, long_ago)
        loaders.upsert_by_ids(MART, ids)
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai.tin_nhan")
            (after,) = cur.fetchone()
        # upsert chạy lại trên cùng dữ liệu KHÔNG được nhân đôi dòng
        self.assertEqual(before, after)
        self.assertEqual(before, 2)
