"""Vòng chạy gán nhãn — mock Gemini, không gọi mạng."""

import json
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings

from apps.ai_mart.infrastructure import chat_extract_runner as runner
from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.chat_hoi_thoai import MART
from apps.ai_mart.tests import chat_helpers as ch

_GOOD = '{"tom_tat": "khách đau vai", "nhu_cau": ["neck_shoulder_nape"], "giai_doan": "hoi_gia"}'


def _queue_row(conv_id):
    # Django đăng ký typecaster jsonb với loads=identity trên connection (để
    # JSONField tự decode bằng decoder riêng, tránh decode 2 lần) — cursor trần
    # vì vậy LUÔN trả extract_payload dưới dạng str, không phải dict. Tự
    # json.loads ở đây, không phải bug của runner.
    with connection.cursor() as cur:
        cur.execute(
            "SELECT extract_status, extract_payload, noi_dung_hash, model"
            " FROM ai_stg.hoi_thoai_nhan WHERE hoi_thoai_id = %s", [conv_id]
        )
        row = cur.fetchone()
    if row is None:
        return None
    status, payload, noi_dung_hash, model = row
    if isinstance(payload, str):
        payload = json.loads(payload)
    return status, payload, noi_dung_hash, model


def _attempts(conv_id):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT extract_attempts FROM ai_stg.hoi_thoai_nhan WHERE hoi_thoai_id = %s",
            [conv_id],
        )
        row = cur.fetchone()
    return row[0] if row else None


@override_settings(AI_CHAT_EXTRACT_ENABLED=True, GEMINI_API_KEY="test-key",
                   AI_CHAT_EXTRACT_MIN_CHARS=1)
class ChatExtractRunnerTests(TestCase):
    def _conversation(self, content="đau vai gáy quá"):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        ch.mk_message(conv, page, direction="in", content=content)
        loaders.full_rebuild(MART)
        return conv

    def test_enqueue_only_conversations_with_customer_messages(self):
        with_customer = self._conversation()
        page = ch.mk_page()
        silent = ch.mk_conversation(page)
        ch.mk_message(silent, page, direction="out", content="chào bạn")
        loaders.full_rebuild(MART)

        runner.enqueue_pending()
        self.assertIsNotNone(_queue_row(with_customer))
        # page nhắn một chiều: không có gì để đọc, không được đốt token
        self.assertIsNone(_queue_row(silent))

    def test_run_batch_stores_clean_payload_and_marks_done(self):
        conv = self._conversation()
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", return_value=_GOOD):
            summary = runner.run_batch(limit=10)
        self.assertEqual(summary["done"], 1)
        status, payload, _, model = _queue_row(conv)
        self.assertEqual(status, "done")
        self.assertEqual(payload["clean"]["nhu_cau"], ["neck_shoulder_nape"])
        self.assertIn("raw", payload)
        self.assertIn("rejected", payload)
        self.assertTrue(model)

    def test_labels_reach_the_mart_after_rebuild(self):
        conv = self._conversation()
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", return_value=_GOOD):
            runner.run_batch(limit=10)
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT nhu_cau_ai_text, giai_doan_text, trang_thai_gan_nhan_text"
                " FROM ai.hoi_thoai WHERE hoi_thoai_id = %s", [conv]
            )
            nhu_cau, giai_doan, trang_thai = cur.fetchone()
        self.assertEqual(nhu_cau, "Cổ - vai - gáy")
        self.assertEqual(giai_doan, "Hỏi giá")
        self.assertEqual(trang_thai, "Đã gán nhãn")

    def test_unchanged_conversation_is_not_relabelled(self):
        conv = self._conversation()
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", return_value=_GOOD) as gen:
            runner.run_batch(limit=10)
            runner.enqueue_pending()          # chạy lại: nội dung không đổi
            runner.run_batch(limit=10)
        self.assertEqual(gen.call_count, 1)
        self.assertEqual(_queue_row(conv)[0], "done")

    def test_changed_conversation_is_relabelled(self):
        conv = self._conversation()
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", return_value=_GOOD) as gen:
            runner.run_batch(limit=10)
            page_id = ch.mk_page()
            ch.mk_message(conv, page_id, direction="in", content="thêm câu mới")
            loaders.full_rebuild(MART)
            runner.enqueue_pending()
            runner.run_batch(limit=10)
        self.assertEqual(gen.call_count, 2)

    def test_api_error_records_error_without_blocking_others(self):
        first = self._conversation("câu một")
        second = self._conversation("câu hai")
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", side_effect=[RuntimeError("bùm"), _GOOD]):
            summary = runner.run_batch(limit=10)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["done"], 1)
        statuses = {_queue_row(first)[0], _queue_row(second)[0]}
        self.assertEqual(statuses, {"pending", "done"})   # lỗi 1 lần → về pending, còn lượt thử

    def test_disabled_flag_stops_everything(self):
        self._conversation()
        runner.enqueue_pending()               # có dòng pending thật sự chờ sẵn
        with override_settings(AI_CHAT_EXTRACT_ENABLED=False):
            self.assertEqual(runner.run_batch(limit=10)["claimed"], 0)
        # Đối chứng: CÙNG dòng pending đó, bật cờ lại thì claim được thật —
        # chứng minh claimed=0 ở trên là do cờ chặn, không phải hàng đợi rỗng.
        with patch.object(runner, "_generate_json", return_value=_GOOD):
            self.assertEqual(runner.run_batch(limit=10)["claimed"], 1)

    def test_transient_infrastructure_error_does_not_burn_attempt(self):
        conv = self._conversation()
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json",
                          side_effect=RuntimeError("503 unavailable")):
            summary = runner.run_batch(limit=10)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(_queue_row(conv)[0], "pending")
        self.assertEqual(_attempts(conv), 0)

    def test_non_transient_error_burns_attempt(self):
        conv = self._conversation()
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", side_effect=RuntimeError("bùm")):
            summary = runner.run_batch(limit=10)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(_queue_row(conv)[0], "pending")
        self.assertEqual(_attempts(conv), 1)

    def test_quota_exhausted_stops_batch_and_blocks_next_run_before_claim(self):
        first = self._conversation("câu một")
        second = self._conversation("câu hai")
        runner.enqueue_pending()
        self.addCleanup(cache.delete, runner.COOLDOWN_KEY)

        with patch.object(runner, "_generate_json",
                          side_effect=RuntimeError("429 resource_exhausted")):
            summary = runner.run_batch(limit=10)
        self.assertTrue(summary["quota_stopped"])
        self.assertEqual(summary["done"], 0)
        self.assertEqual(summary["errors"], 0)
        # dòng vừa lỗi quota lẫn dòng chưa kịp xử lý đều về lại pending, còn
        # nguyên lượt thử — hết quota không phải lỗi của nội dung hội thoại
        statuses = {_queue_row(first)[0], _queue_row(second)[0]}
        self.assertEqual(statuses, {"pending"})
        self.assertEqual(_attempts(first), 0)
        self.assertEqual(_attempts(second), 0)
        self.assertIsNotNone(cache.get(runner.COOLDOWN_KEY))

        # Lượt chạy kế tiếp, còn trong cửa sổ cooldown: chặn TRƯỚC khi claim —
        # _generate_json không được gọi lại, 2 dòng pending vẫn nằm nguyên đó.
        with patch.object(runner, "_generate_json", return_value=_GOOD) as gen:
            summary2 = runner.run_batch(limit=10)
        gen.assert_not_called()
        self.assertEqual(summary2["claimed"], 0)
        self.assertTrue(summary2["quota_stopped"])

    def test_finished_label_is_visible_to_incremental_candidate_scan(self):
        # Cơ chế đồng bộ tăng dần (chat_hoi_thoai._CANDIDATES) chọn hội thoại
        # theo hoi_thoai_nhan.updated_at > cutoff — quên set updated_at ở bất kỳ
        # UPDATE nào trong runner là nhãn hoàn tất âm thầm không tới được mart
        # tới lần rebuild đầy đủ tiếp theo. full_rebuild() không đi qua đường
        # này (TRUNCATE+INSERT toàn bộ) nên phải kiểm candidate_ids() trực tiếp.
        # Đây là bài kiểm tra HẾT PIPELINE (run_batch) — dây nối candidate_ids
        # ↔ updated_at ↔ hoi_thoai_id thật sự khớp đầu-cuối. Kiểm riêng
        # _save_done/_save_error tự set updated_at (bất kể _claim) nằm ở
        # test_save_done_and_save_error_bump_updated_at_independent_of_claim
        # bên dưới — _claim() cũng tự set updated_at nên 1 mình test này
        # KHÔNG phân biệt được nếu chỉ riêng _save_done quên set.
        #
        # timezone.now() bên Python KHÔNG dùng làm cutoff được ở đây: Postgres
        # now() bị ĐÓNG BĂNG ở mốc bắt-đầu-transaction suốt cả test (TestCase
        # bọc mỗi test trong 1 transaction), nên MỌI updated_at=now() ghi
        # trong run_batch() đều bằng đúng 1 giá trị — luôn SỚM HƠN một
        # timezone.now() lấy giữa chừng test. Lùi tay updated_at về 1 giờ
        # trước NGAY TRONG transaction đó rồi đặt cutoff ở giữa: nếu KHÔNG
        # UPDATE nào trong toàn bộ run_batch() còn set lại updated_at = now(),
        # dòng vẫn nằm ở mốc lùi tay (< cutoff) và assertIn bên dưới sẽ FAIL —
        # đúng ý test_loaders.test_candidate_ids đã làm với timestamp Python
        # thường.
        #
        # PHẢI lùi CẢ HAI vế UNION của _CANDIDATES: chat_conversations.updated_at
        # (set bằng timezone.now() thật lúc tạo hội thoại — luôn "gần đây",
        # không liên quan gì tới runner) tự nó đã đủ thoả cutoff nếu bỏ qua,
        # khiến test đậu dù hoi_thoai_nhan.updated_at có đúng hay không (đã tự
        # tay xác nhận: bỏ mỗi cột này, test vẫn xanh).
        conv = self._conversation()
        runner.enqueue_pending()
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE ai_stg.chat_conversations SET updated_at = now() - interval '1 hour'"
                " WHERE id = %s", [conv]
            )
            cur.execute(
                "UPDATE ai_stg.hoi_thoai_nhan SET updated_at = now() - interval '1 hour'"
                " WHERE hoi_thoai_id = %s", [conv]
            )
            cur.execute("SELECT now() - interval '30 minutes'")
            cutoff = cur.fetchone()[0]

        with patch.object(runner, "_generate_json", return_value=_GOOD):
            runner.run_batch(limit=10)

        self.assertIn(conv, loaders.candidate_ids(MART, cutoff))

    def test_save_done_and_save_error_bump_updated_at_independent_of_claim(self):
        # _claim() CŨNG tự set updated_at=now() khi chuyển pending->processing,
        # nên một hội thoại chạy trọn qua run_batch() LUÔN có updated_at mới
        # bất kể _save_done/_save_error có set lại hay không — bài test
        # test_finished_label_is_visible_to_incremental_candidate_scan ở trên
        # KHÔNG tự mình bắt được lỗi "quên set updated_at" nếu lỗi đó chỉ nằm
        # riêng ở 2 hàm này (đã tự tay xác nhận bằng cách bỏ updated_at khỏi
        # _save_done rồi chạy lại — bài test kia vẫn xanh). Bài này mô phỏng
        # đúng tình huống rủi ro thật: hội thoại đã 'processing' từ một nhịp
        # trước (claim đã chạy, updated_at lúc đó đã cũ), rồi gọi thẳng
        # _save_done/_save_error KHÔNG qua _claim() — bỏ "updated_at = now()"
        # khỏi 1 trong 2 hàm là test dưới FAIL ngay lập tức.
        # Cùng lý do backdate chat_conversations.updated_at như bài test trên:
        # nếu không, vế đó của UNION tự nhiên "gần đây" (timezone.now() lúc tạo
        # hội thoại) và che mất kết quả thật của _save_done/_save_error.
        done_conv = self._conversation("câu một")
        error_conv = self._conversation("câu hai")
        runner.enqueue_pending()
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE ai_stg.chat_conversations SET updated_at = now() - interval '1 hour'"
                " WHERE id = ANY(%s)", [[done_conv, error_conv]]
            )
            cur.execute(
                "UPDATE ai_stg.hoi_thoai_nhan SET extract_status = 'processing',"
                " updated_at = now() - interval '1 hour'"
                " WHERE hoi_thoai_id = ANY(%s)", [[done_conv, error_conv]]
            )
            cur.execute("SELECT now() - interval '30 minutes'")
            cutoff = cur.fetchone()[0]

        runner._save_done(done_conv, {"raw": True}, {"clean": True}, {})
        runner._save_error(error_conv, "lỗi test", transient=False)

        self.assertIn(done_conv, loaders.candidate_ids(MART, cutoff))
        self.assertIn(error_conv, loaders.candidate_ids(MART, cutoff))
        # Không chỉ updated_at mới — trạng thái cũng phải đổi đúng ý, chứng
        # minh "AND extract_status = 'processing'" trong 2 hàm khớp được dòng
        # đã backdate ở trên (guard không vô tình chặn luôn ghi hợp lệ).
        self.assertEqual(_queue_row(done_conv)[0], "done")
        self.assertEqual(_queue_row(error_conv)[0], "pending")


@override_settings(AI_CHAT_EXTRACT_ENABLED=True, GEMINI_API_KEY="test-key",
                   AI_CHAT_EXTRACT_MIN_CHARS=1, AI_CHAT_EXTRACT_MAX_CHARS=100)
class OversizedAndBackoffTests(TestCase):
    """Ba lỗi đo trên prod 07/09/2026, cùng một gốc: hội thoại quá lớn."""

    def _conversation(self, content):
        page = ch.mk_page()
        conv = ch.mk_conversation(page)
        ch.mk_message(conv, page, direction="in", content=content)
        loaders.full_rebuild(MART)
        return conv

    def test_oversized_conversation_is_truncated_to_the_tail_not_dropped(self):
        conv = self._conversation("A" * 300 + " ĐOẠN CUỐI QUAN TRỌNG")
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", return_value=_GOOD) as gen:
            summary = runner.run_batch(limit=10)

        self.assertEqual(summary["done"], 1)
        self.assertEqual(summary["truncated"], 1)
        prompt = gen.call_args[0][0]
        # giữ ĐUÔI: lý do không mua nằm ở đoạn hội thoại gần nhất
        self.assertIn("ĐOẠN CUỐI QUAN TRỌNG", prompt)
        self.assertEqual(_queue_row(conv)[0], "done")

    def test_truncation_is_recorded_in_the_payload(self):
        conv = self._conversation("A" * 300)
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", return_value=_GOOD):
            runner.run_batch(limit=10)
        self.assertIs(_queue_row(conv)[1]["cat_bot"], True)

    def test_short_conversation_is_not_flagged_as_truncated(self):
        conv = self._conversation("ngắn thôi")
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", return_value=_GOOD):
            summary = runner.run_batch(limit=10)
        self.assertEqual(summary["truncated"], 0)
        self.assertIs(_queue_row(conv)[1]["cat_bot"], False)

    def test_soft_time_limit_is_not_swallowed_into_a_burned_attempt(self):
        """Guard phải nằm TRONG _extract_one, không chỉ ở run_batch.

        Prod 07/09/2026: 8 dòng `failed` mang lỗi "Gemini lỗi:
        SoftTimeLimitExceeded()" — tức nó đã bị `except Exception` của
        _extract_one bọc lại trước khi guard ngoài kịp thấy.
        """
        from celery.exceptions import SoftTimeLimitExceeded

        conv = self._conversation("nội dung vừa đủ")
        runner.enqueue_pending()
        with patch.object(runner, "_generate_json", side_effect=SoftTimeLimitExceeded()):
            with self.assertRaises(SoftTimeLimitExceeded):
                runner.run_batch(limit=10)

        status, _, _, _ = _queue_row(conv)
        self.assertEqual(status, "pending", "dòng phải được trả về hàng đợi")
        with connection.cursor() as cur:
            cur.execute(
                "SELECT extract_attempts, extract_error FROM ai_stg.hoi_thoai_nhan"
                " WHERE hoi_thoai_id = %s", [conv]
            )
            attempts, error = cur.fetchone()
        self.assertEqual(attempts, 0, "KHÔNG được đốt attempt vì worker hết giờ")
        self.assertEqual(error, "", "KHÔNG được ghi lỗi lên dòng vô can")

    def test_recently_failed_row_is_claimed_last(self):
        """Hội thoại lỗi không được chặn đầu hàng đợi ở mọi lượt."""
        page = ch.mk_page()
        chan_dau = ch.mk_conversation(page)   # nhiều tin khách nhất -> đứng đầu
        binh_thuong = ch.mk_conversation(page)
        for _ in range(5):
            ch.mk_message(chan_dau, page, direction="in", content="tin khách")
        ch.mk_message(binh_thuong, page, direction="in", content="tin khách")
        loaders.full_rebuild(MART)
        runner.enqueue_pending()
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE ai_stg.hoi_thoai_nhan SET extract_error = 'Gemini lỗi: 504',"
                " updated_at = now() WHERE hoi_thoai_id = %s", [chan_dau]
            )
        with patch.object(runner, "_generate_json", return_value=_GOOD):
            runner.run_batch(limit=1)   # chỉ 1 slot: ai được chọn?
        self.assertEqual(_queue_row(binh_thuong)[0], "done",
                         "dòng lành phải được ưu tiên hơn dòng vừa lỗi")
        self.assertEqual(_queue_row(chan_dau)[0], "pending")


class TatLuongGanNhanTests(SimpleTestCase):
    """Cờ tắt phải chặn TRƯỚC enqueue_pending, không chỉ chặn run_batch.

    run_batch() tự kiểm cờ và trả về rỗng, nhưng enqueue_pending() thì KHÔNG:
    để nguyên thì một luồng ĐÃ TẮT vẫn quét + upsert toàn bộ ai.hoi_thoai
    (95k dòng trên prod) mỗi 10 phút, vô ích và tốn IO.
    """

    def test_tat_co_thi_khong_enqueue(self):
        from apps.ai_mart.infrastructure import tasks as ai_tasks

        with override_settings(AI_CHAT_EXTRACT_ENABLED=False), \
                patch.object(runner, "enqueue_pending") as enqueue, \
                patch.object(runner, "run_batch") as run_batch:
            ket_qua = ai_tasks.extract_chat_labels()

        enqueue.assert_not_called()
        run_batch.assert_not_called()
        self.assertTrue(ket_qua["disabled"])
