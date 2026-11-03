"""Nhịp ETL của hai bảng chat.

Bài học sự cố statement timeout 2026-07-22 + quyết định C2: nguồn không đọc
được thì job PHẢI raise. FDW chết trên prod và bảng nguồn biến mất trong test
cùng biểu hiện: relation/connection lỗi ngay ở câu SELECT đầu tiên.
"""

from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.ai_mart.application import etl_service
from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.tests import chat_helpers as ch


class ChatEtlTests(TestCase):
    def _counts(self):
        with connection.cursor() as cur:
            cur.execute("SELECT (SELECT count(*) FROM ai.hoi_thoai),"
                        " (SELECT count(*) FROM ai.tin_nhan)")
            return cur.fetchone()

    def test_chat_marts_are_in_hot_group(self):
        names = [m.name for m in etl_service.HOT_MARTS]
        self.assertIn("ai.hoi_thoai", names)
        self.assertIn("ai.tin_nhan", names)

    def test_chat_marts_are_last_in_hot_sync_order(self):
        # sync_hot() lặp HOT_MARTS — nguồn FDW chết không được kéo theo 4 bảng
        # nội bộ (C2 vẫn raise, chỉ raise SAU CÙNG).
        external = [m.name for m in etl_service.EXTERNAL_MARTS]
        names = [m.name for m in etl_service.HOT_MARTS]
        self.assertEqual(names[-len(external):], external)

    def test_rebuild_all_loads_external_marts_last(self):
        """Bảng chat (phụ thuộc cầu FDW) phải nạp SAU nhóm đêm.

        Đo trên THỨ TỰ GỌI THẬT của rebuild_all(), không so với một hằng số
        chép lại: bug đã có đúng là "hằng số xếp đúng cho sync_hot() nhưng vòng
        lặp của rebuild_all() lại ghép HOT_MARTS + NIGHTLY_MARTS". Nguồn chết ở
        vị trí giữa vòng = san_pham/ton_kho/lich_su_kho/chi_phi_quang_cao không
        được nạp, mà chúng KHÔNG có đường nạp tăng dần nào khác.
        """
        goi: list[str] = []

        def _ghi(mart):
            goi.append(mart.name)
            return 0

        with mock.patch("apps.ai_mart.infrastructure.loaders.full_rebuild", side_effect=_ghi):
            etl_service.rebuild_all()

        external = [m.name for m in etl_service.EXTERNAL_MARTS]
        self.assertEqual(goi[-len(external):], external, f"thứ tự rebuild: {goi}")
        # ...và không bảng nào bị bỏ quên khi tách nhóm.
        self.assertEqual(
            sorted(goi),
            sorted(m.name for m in etl_service.HOT_MARTS + etl_service.NIGHTLY_MARTS),
        )

    def test_rebuild_survives_dead_chat_bridge_and_still_loads_nightly(self):
        """Cầu chat chết KHÔNG được làm hỏng lượt rebuild đêm.

        Sự cố 22/09/2026: role chat_ro bị mất khi DB crawler dựng lại; rebuild
        nạp xong đủ nhóm đêm rồi chết ở ai.tin_nhan (mart CUỐI) → task Celery
        FAILED → không có event task-succeeded → AiMartRebuildSilent nổ vĩnh
        viễn kèm mô tả SAI ("3 bảng đóng băng") trong khi chúng vẫn tươi.
        """
        with connection.cursor() as cur:
            cur.execute("DROP TABLE ai_stg.chat_conversations CASCADE")

        ket_qua = etl_service.rebuild_all()   # KHÔNG được raise

        ten_ngoai = {m.name for m in etl_service.EXTERNAL_MARTS}
        self.assertTrue(ket_qua["ngoai_hong"], "lỗi cầu chat phải được ghi nhận")
        self.assertTrue(set(ket_qua["ngoai_hong"]) <= ten_ngoai)
        # ...và nhóm đêm vẫn phải có mặt đầy đủ trong kết quả.
        for mart in etl_service.NIGHTLY_MARTS:
            self.assertIn(mart.name, ket_qua["tables"])

    def test_rebuild_still_raises_when_a_local_mart_fails(self):
        """Chỉ external được miễn raise — lỗi bảng nội bộ vẫn phải nổ."""
        noi_bo = etl_service.LOCAL_HOT_MARTS[0]

        def _hong(mart):
            if mart.name == noi_bo.name:
                raise RuntimeError("bảng nội bộ hỏng")
            return 0

        with mock.patch("apps.ai_mart.infrastructure.loaders.full_rebuild", side_effect=_hong):
            with self.assertRaises(etl_service.EtlFailed):
                etl_service.rebuild_all()

    def test_incremental_sync_equals_full_rebuild(self):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        ch.mk_message(conv, page, content="tin 1")
        ch.mk_message(conv, page, direction="out", content="tin 2")

        etl_service.rebuild_all()
        full = self._counts()

        # Đẩy watermark về quá khứ để mọi hội thoại thành "vừa đổi"
        loaders.write_watermark(etl_service.HOT_KEY, timezone.now() - timedelta(days=2))
        etl_service.sync_hot()
        self.assertEqual(self._counts(), full)

    def test_sync_raises_when_source_unreachable(self):
        loaders.write_watermark(etl_service.HOT_KEY, timezone.now())
        with connection.cursor() as cur:
            cur.execute("DROP TABLE ai_stg.chat_conversations CASCADE")
        with self.assertRaises(etl_service.EtlFailed):
            etl_service.sync_hot()

    def test_failed_sync_is_logged_as_failed_not_ok(self):
        # loaders.log_run tự nuốt lỗi ghi log của chính nó (xem loaders.py) —
        # nếu để test chạy thật qua TestCase (transaction), câu SELECT hỏng ở
        # trên làm abort transaction, kéo theo INSERT log_run cũng hỏng, và
        # ai_meta.nhat_ky_chay trống rỗng vì lý do KHÔNG liên quan gì tới hành
        # vi đang kiểm tra. Patch log_run để kiểm hợp đồng thật: "ghi failed,
        # không bao giờ ghi ok" cho bảng chat, độc lập với transaction state.
        loaders.write_watermark(etl_service.HOT_KEY, timezone.now())
        with connection.cursor() as cur:
            cur.execute("DROP TABLE ai_stg.chat_conversations CASCADE")
        with mock.patch("apps.ai_mart.infrastructure.loaders.log_run") as log_run:
            with self.assertRaises(etl_service.EtlFailed):
                etl_service.sync_hot()
        chat_calls = [c for c in log_run.call_args_list if c.args[1] == "ai.tin_nhan"]
        self.assertTrue(chat_calls, "log_run không được gọi cho ai.tin_nhan")
        statuses = [c.args[5] for c in chat_calls]
        self.assertIn("failed", statuses)
        self.assertNotIn("ok", statuses)

    def test_rebuild_refreshes_need_dictionary(self):
        from apps.orders.models import NeedGroup, NeedType

        group = NeedGroup.objects.create(code="grp_rb", name_vi="Nhóm rebuild", sort_order=902)
        NeedType.objects.create(code="nc_rb", group=group, name_vi="Nhu cầu rebuild",
                                sort_order=912, is_active=True)
        etl_service.rebuild_all()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT nhan FROM ai.tu_dien_ma WHERE nhom_ma = 'nhu_cau' AND ma = 'nc_rb'"
            )
            row = cur.fetchone()
        self.assertEqual(row and row[0], "Nhu cầu rebuild")
