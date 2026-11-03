import json

from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.chat_hoi_thoai import MART
from apps.ai_mart.tests import chat_helpers as ch
from apps.ai_mart.tests.helpers import mk_order


def _label(conv_id, *cols):
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(cols)} FROM ai.hoi_thoai WHERE hoi_thoai_id = %s", [conv_id]
        )
        return cur.fetchone()


def _set_labels(conv_id, clean, status="done"):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO ai_stg.hoi_thoai_nhan (hoi_thoai_id, extract_payload, extract_status)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT (hoi_thoai_id) DO UPDATE SET extract_payload = EXCLUDED.extract_payload,"
            " extract_status = EXCLUDED.extract_status, updated_at = now()",
            [conv_id, json.dumps({"clean": clean}), status],
        )


class HoiThoaiShapeTests(TestCase):
    def test_counts_and_transcript_roles(self):
        page = ch.mk_page(platform="personal_zalo", name="Zalo Delax")
        cust = ch.mk_customer(page, name="Chị Lan", phone="0912345678")
        conv = ch.mk_conversation(page, customer_id=cust, type="INBOX")
        ch.mk_message(conv, page, direction="in", content="ghế này bao nhiêu")
        ch.mk_message(conv, page, direction="out", content="dạ 20 triệu ạ")
        ch.mk_message(conv, page, direction="in", content="đắt quá")
        loaders.full_rebuild(MART)

        row = _label(
            conv, "so_tin", "so_tin_khach", "so_tin_page", "co_khach_tra_loi",
            "nen_tang_text", "loai_kenh_text", "khach_ten", "sdt_che", "noi_dung",
        )
        so_tin, khach, page_msg, co_tra_loi, nen_tang, kenh, ten, sdt, noi_dung = row
        self.assertEqual((so_tin, khach, page_msg), (3, 2, 1))
        self.assertTrue(co_tra_loi)
        self.assertEqual(nen_tang, "Zalo cá nhân")
        self.assertEqual(kenh, "Tin nhắn")
        self.assertEqual(ten, "Chị Lan")
        self.assertEqual(sdt, "0912***678")          # luật che PII
        self.assertEqual(
            noi_dung,
            "Khách: ghế này bao nhiêu\nPage: dạ 20 triệu ạ\nKhách: đắt quá",
        )

    def test_conversation_without_messages_survives_with_zero_counts(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        loaders.full_rebuild(MART)
        so_tin, khach, co_tra_loi, noi_dung, do_bot_xu_ly = _label(
            conv, "so_tin", "so_tin_khach", "co_khach_tra_loi", "noi_dung", "do_bot_xu_ly"
        )
        # 465 hội thoại rỗng trên nguồn — phải là 0, KHÔNG phải NULL, và không bị rơi khỏi bảng
        self.assertEqual((so_tin, khach), (0, 0))
        self.assertFalse(co_tra_loi)
        self.assertIsNone(noi_dung)
        # coalesce(g.do_bot_xu_ly, false): hội thoại chưa gắn thẻ nào phải đọc
        # đúng false, KHÔNG phải NULL — LEFT JOIN LATERAL không trả dòng nào.
        self.assertIs(do_bot_xu_ly, False)

    def test_bot_tag_flag(self):
        page = ch.mk_page()
        bot_conv = ch.mk_conversation(page)
        human_conv = ch.mk_conversation(page)
        ch.tag_conversation(bot_conv, ch.mk_tag(page, "BOT-MSC06"))
        ch.tag_conversation(human_conv, ch.mk_tag(page, "Đã tư vấn"))
        loaders.full_rebuild(MART)
        self.assertTrue(_label(bot_conv, "do_bot_xu_ly")[0])
        self.assertFalse(_label(human_conv, "do_bot_xu_ly")[0])
        nhan_tag, nhan_tag_text = _label(human_conv, "nhan_tag", "nhan_tag_text")
        self.assertEqual(nhan_tag, ["Đã tư vấn"])
        self.assertEqual(nhan_tag_text, "Đã tư vấn")


class HoiThoaiLabelTests(TestCase):
    def test_ai_labels_are_translated_and_grouped(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        ch.mk_message(conv, page, content="đau vai gáy")
        _set_labels(conv, {
            "tom_tat": "khách đau vai gáy, chê đắt",
            "nhu_cau": ["neck_shoulder_nape"],
            "ly_do_khong_mua": ["too_expensive"],
            "san_pham_quan_tam": ["Ghế massage X10"],
            "giai_doan": "dang_can_nhac",
            "muc_do_quan_tam": "cao",
        })
        loaders.full_rebuild(MART)
        row = _label(
            conv, "tom_tat_ai", "nhu_cau_ai", "nhu_cau_ai_text", "nhom_nhu_cau_ai",
            "ly_do_khong_mua_ma", "ly_do_khong_mua_text", "san_pham_quan_tam",
            "giai_doan_text", "muc_do_quan_tam_text", "trang_thai_gan_nhan_text",
        )
        (tom_tat, nhu_cau, nhu_cau_text, nhom, ly_do, ly_do_text, san_pham,
         giai_doan, muc_do, tt) = row
        self.assertEqual(tom_tat, "khách đau vai gáy, chê đắt")
        self.assertEqual(nhu_cau, ["neck_shoulder_nape"])
        self.assertEqual(nhu_cau_text, "Cổ - vai - gáy")
        self.assertEqual(nhom, ["pain_relief"])
        self.assertEqual(ly_do, ["too_expensive"])
        self.assertEqual(ly_do_text, "Đắt")
        self.assertEqual(san_pham, ["Ghế massage X10"])
        self.assertEqual(giai_doan, "Đang cân nhắc")
        self.assertEqual(muc_do, "Cao")
        self.assertEqual(tt, "Đã gán nhãn")

    def test_unlabelled_conversation_reads_chua_xep_hang(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        loaders.full_rebuild(MART)
        tt, nhu_cau, ly_do = _label(
            conv, "trang_thai_gan_nhan_text", "nhu_cau_ai", "ly_do_khong_mua_ma"
        )
        self.assertEqual(tt, "Chưa xếp hàng")
        # mảng RỖNG chứ không NULL: "chưa gán nhãn" khác "gán nhãn xong, không có nhu cầu nào"
        self.assertEqual(nhu_cau, [])
        self.assertEqual(ly_do, [])

    def test_order_link_by_extracted_phone(self):
        order = mk_order(status=3, order_code="DH-CHAT-1")
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        ch.mk_message(conv, page, content="số em là ...")
        _set_labels(conv, {"sdt_khach": order.customer.phone_normalized})
        loaders.full_rebuild(MART)
        don_id, ma_don, khach_id = _label(conv, "don_id", "ma_don", "khach_id")
        self.assertEqual(don_id, order.id)
        self.assertEqual(ma_don, "DH-CHAT-1")
        self.assertEqual(khach_id, order.customer_id)

    def test_no_order_link_when_phone_absent(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        _set_labels(conv, {"tom_tat": "không nói số"})
        loaders.full_rebuild(MART)
        self.assertEqual(_label(conv, "don_id", "ma_don"), (None, None))


class CandidateScopeTests(TestCase):
    """Lượt xếp hàng hàng loạt KHÔNG được kéo cả mart vào một lượt sync.

    Sự cố prod 05/09/2026: `enqueue_pending()` ghi updated_at lên 60.435 dòng, lượt
    sync kế tiếp upsert lại toàn bộ mart mất 587s — quá xa statement_timeout(180)
    của beat, watermark không tiến, lặp vô hạn.
    """

    def _candidates(self, cutoff):
        from apps.ai_mart.infrastructure import loaders

        return set(loaders.candidate_ids(MART, cutoff))

    def test_pending_rows_do_not_enter_the_sync_batch(self):
        from datetime import timedelta

        from django.utils import timezone

        page = ch.mk_page()
        cho_gan_nhan = ch.mk_conversation(page)
        da_gan_nhan = ch.mk_conversation(page)
        cutoff = timezone.now() - timedelta(minutes=5)

        with connection.cursor() as cur:
            # lùi updated_at của chính hội thoại: nếu không, nhánh UNION thứ nhất
            # (chat_conversations.updated_at) kéo cả hai vào và test luôn xanh giả
            cur.execute(
                "UPDATE ai_stg.chat_conversations SET updated_at = now() - interval '1 day'"
                " WHERE id = ANY(%s)", [[cho_gan_nhan, da_gan_nhan]]
            )
            cur.execute(
                "INSERT INTO ai_stg.hoi_thoai_nhan (hoi_thoai_id, extract_status, updated_at)"
                " VALUES (%s, 'pending', now()), (%s, 'done', now())",
                [cho_gan_nhan, da_gan_nhan],
            )
        found = self._candidates(cutoff)
        self.assertIn(da_gan_nhan, found, "nhãn xong PHẢI lên mart ngay")
        self.assertNotIn(cho_gan_nhan, found, "dòng mới xếp hàng KHÔNG được kéo vào sync")

    def test_row_requeued_after_an_error_still_reaches_the_mart(self):
        """`_save_error` trả trạng thái về 'pending' — dòng đó KHÔNG được biến mất."""
        from datetime import timedelta

        from django.utils import timezone

        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        cutoff = timezone.now() - timedelta(minutes=5)
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE ai_stg.chat_conversations SET updated_at = now() - interval '1 day'"
                " WHERE id = %s", [conv]
            )
            cur.execute(
                "INSERT INTO ai_stg.hoi_thoai_nhan (hoi_thoai_id, extract_status, extract_error,"
                " updated_at) VALUES (%s, 'pending', 'Gemini lỗi: timeout', now())", [conv]
            )
        self.assertIn(conv, self._candidates(cutoff))

    def test_failed_and_skipped_still_reach_the_mart(self):
        from datetime import timedelta

        from django.utils import timezone

        page = ch.mk_page()
        loi = ch.mk_conversation(page)
        bo_qua = ch.mk_conversation(page)
        cutoff = timezone.now() - timedelta(minutes=5)
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE ai_stg.chat_conversations SET updated_at = now() - interval '1 day'"
                " WHERE id = ANY(%s)", [[loi, bo_qua]]
            )
            cur.execute(
                "INSERT INTO ai_stg.hoi_thoai_nhan (hoi_thoai_id, extract_status, updated_at)"
                " VALUES (%s, 'failed', now()), (%s, 'skipped', now())",
                [loi, bo_qua],
            )
        found = self._candidates(cutoff)
        self.assertIn(loi, found)
        self.assertIn(bo_qua, found)
