"""Test luồng chấm chất lượng cuộc gọi (Gemini structured output).

Mock toàn bộ Gemini — không gọi mạng thật.
"""

import json
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from apps.callcenter.models import (
    CallQualityScore,
    CallRecord,
    ScoreStatus,
    TranscribeStatus,
)
from apps.callcenter.qa_rubric import CRITERIA, RUBRIC_VERSION
from apps.callcenter.scoring import (
    SCORE_COOLDOWN_KEY,
    ScoreError,
    compute_speech_metrics,
    drain_pending_calls,
    score_pending_calls,
)

TRANSCRIPT = (
    "Nhân viên: Dạ em chào anh chị ạ, em gọi từ nhà DELAX ạ.\n"
    "Khách: Ừ.\n"
    "Nhân viên: Không biết anh đang bị mỏi nhiều ở vùng nào để em tư vấn ạ?\n"
    "Khách: Anh đau vai gáy.\n"
) + ("Nhân viên: Dạ em ghi nhận thông tin của mình ạ.\n" * 20)


def _gemini_payload(**criteria_overrides) -> str:
    criteria = {c.code: {"score": "na", "evidence": ""} for c in CRITERIA}
    criteria.update(criteria_overrides)
    return json.dumps(
        {
            "criteria": criteria,
            "red_flags": [],
            "contraindications": [],
            "not_closed_reason": "",
            "summary": "Sale chào hỏi tốt nhưng chưa khai thác đủ nhu cầu.",
            "coaching": "Hỏi thêm độ tuổi và đã đau bao lâu.",
        },
        ensure_ascii=False,
    )


def _make_record(**kwargs) -> CallRecord:
    defaults = {
        "call_id": "qa-1",
        "transcribe_status": TranscribeStatus.DONE,
        "transcript": TRANSCRIPT,
        "acd_time": 300,
        "agent_id": "6863",
        "agent_name": "",
    }
    defaults.update(kwargs)
    return CallRecord.objects.create(**defaults)


class SpeechMetricsTests(SimpleTestCase):
    def test_talk_ratio_va_wpm_tinh_tu_transcript_phan_vai(self):
        transcript = (
            "Nhân viên: một hai ba bốn\n"  # 4 từ
            "Khách: năm sáu\n"  # 2 từ
        )
        wpm, talk_ratio = compute_speech_metrics(transcript, duration_seconds=60)

        self.assertEqual(wpm, 4)
        self.assertAlmostEqual(talk_ratio, 0.667, places=3)

    def test_thoi_luong_0_khong_chia_cho_0(self):
        wpm, talk_ratio = compute_speech_metrics("Nhân viên: alo alo", duration_seconds=0)

        self.assertEqual(wpm, 0)
        self.assertAlmostEqual(talk_ratio, 1.0)

    def test_transcript_rong_tra_ve_0(self):
        self.assertEqual(compute_speech_metrics("", 100), (0, 0.0))


@override_settings(
    QA_SCORING_ENABLED=True,
    GEMINI_API_KEY="test-key",
    QA_MIN_DURATION_SECONDS=120,
    QA_MIN_TRANSCRIPT_CHARS=100,
)
class ScorePendingCallsTests(TestCase):
    def setUp(self):
        cache.delete(SCORE_COOLDOWN_KEY)

    def tearDown(self):
        cache.delete(SCORE_COOLDOWN_KEY)

    def test_cham_xong_thi_luu_diem_va_doi_trang_thai(self):
        record = _make_record()
        payload = _gemini_payload(
            a1_greeting={
                "score": "2",
                "evidence": "Dạ em chào anh chị ạ, em gọi từ nhà DELAX ạ.",
            }
        )

        with patch("apps.callcenter.scoring._generate_json", return_value=payload):
            summary = score_pending_calls()

        self.assertEqual(summary["done"], 1)
        record.refresh_from_db()
        self.assertEqual(record.score_status, ScoreStatus.DONE)

        score = CallQualityScore.objects.get(call_record=record)
        self.assertEqual(score.rubric_version, RUBRIC_VERSION)
        # Chỉ khối A sống ⇒ trọng số chia lại thành 100.
        self.assertEqual(float(score.total_score), 100.0)
        self.assertEqual(score.criteria["a1_greeting"]["score"], 2)
        self.assertEqual(score.agent_id, "6863")
        self.assertGreater(score.wpm, 0)

    def test_evidence_bia_bi_ha_ve_0_truoc_khi_luu(self):
        record = _make_record(call_id="qa-bia")
        payload = _gemini_payload(
            a1_greeting={"score": "2", "evidence": "Câu này không hề có trong transcript đâu ạ"}
        )

        with patch("apps.callcenter.scoring._generate_json", return_value=payload):
            score_pending_calls()

        score = CallQualityScore.objects.get(call_record=record)
        self.assertEqual(score.criteria["a1_greeting"]["score"], 0)
        self.assertIn("a1_greeting", score.rejected)

    def test_cuoc_ngan_hon_nguong_bi_skip_khong_goi_gemini(self):
        record = _make_record(call_id="qa-ngan", acd_time=30)

        with patch("apps.callcenter.scoring._generate_json") as generate:
            summary = score_pending_calls()

        generate.assert_not_called()
        self.assertEqual(summary["skipped"], 1)
        record.refresh_from_db()
        self.assertEqual(record.score_status, ScoreStatus.SKIPPED)
        self.assertFalse(CallQualityScore.objects.filter(call_record=record).exists())

    def test_transcript_ngan_bi_skip(self):
        record = _make_record(call_id="qa-transcript-ngan", transcript="Nhân viên: alo")

        summary = score_pending_calls()

        self.assertEqual(summary["skipped"], 1)
        record.refresh_from_db()
        self.assertEqual(record.score_status, ScoreStatus.SKIPPED)

    def test_loi_nhat_thoi_khong_dot_attempt(self):
        record = _make_record(call_id="qa-transient")

        with patch(
            "apps.callcenter.scoring._generate_json",
            side_effect=RuntimeError("503 Service Unavailable"),
        ):
            summary = score_pending_calls()

        self.assertEqual(summary["errors"], 1)
        record.refresh_from_db()
        self.assertEqual(record.score_attempts, 0)
        self.assertEqual(record.score_status, ScoreStatus.PENDING)

    def test_loi_noi_dung_dot_attempt_va_fail_khi_het_luot(self):
        record = _make_record(call_id="qa-fail", score_attempts=2)

        with patch(
            "apps.callcenter.scoring._generate_json", return_value="đây không phải json"
        ):
            summary = score_pending_calls()

        self.assertEqual(summary["errors"], 1)
        record.refresh_from_db()
        self.assertEqual(record.score_attempts, 3)
        self.assertEqual(record.score_status, ScoreStatus.FAILED)

    def test_quota_can_thi_bat_cooldown_va_tra_record_ve_pending(self):
        _make_record(call_id="qa-quota-1")
        _make_record(call_id="qa-quota-2")

        with patch(
            "apps.callcenter.scoring._generate_json",
            side_effect=RuntimeError("429 RESOURCE_EXHAUSTED"),
        ):
            summary = score_pending_calls()

        self.assertTrue(summary["quota_stopped"])
        self.assertIsNotNone(cache.get(SCORE_COOLDOWN_KEY))
        self.assertEqual(
            CallRecord.objects.filter(score_status=ScoreStatus.PENDING).count(), 2
        )

    def test_dang_cooldown_thi_khong_claim_record_nao(self):
        _make_record(call_id="qa-cooldown")
        cache.set(SCORE_COOLDOWN_KEY, 1, timeout=60)

        with patch("apps.callcenter.scoring._generate_json") as generate:
            summary = score_pending_calls()

        generate.assert_not_called()
        self.assertTrue(summary["quota_stopped"])
        self.assertEqual(summary["claimed"], 0)

    def test_tat_kill_switch_thi_khong_lam_gi(self):
        _make_record(call_id="qa-off")

        with override_settings(QA_SCORING_ENABLED=False):
            summary = score_pending_calls()

        self.assertEqual(summary["claimed"], 0)

    def test_cuon_chieu_cham_het_hang_doi_qua_nhieu_lo(self):
        """Batch nhỏ nhưng drain lặp tới khi sạch — không dừng ở một lô cố định."""
        for index in range(5):
            _make_record(call_id=f"qa-drain-{index}")
        payload = _gemini_payload(
            a1_greeting={
                "score": "2",
                "evidence": "Dạ em chào anh chị ạ, em gọi từ nhà DELAX ạ.",
            }
        )

        with override_settings(QA_SCORING_BATCH=2):
            with patch("apps.callcenter.scoring._generate_json", return_value=payload):
                summary = drain_pending_calls()

        self.assertEqual(summary["done"], 5)
        self.assertEqual(summary["stopped_by"], "empty")
        self.assertGreaterEqual(summary["rounds"], 3)
        self.assertEqual(
            CallRecord.objects.filter(score_status=ScoreStatus.PENDING).count(), 0
        )

    def test_cuon_chieu_khong_cham_lai_cuoc_da_cham(self):
        record = _make_record(call_id="qa-drain-once")
        payload = _gemini_payload(
            a1_greeting={
                "score": "2",
                "evidence": "Dạ em chào anh chị ạ, em gọi từ nhà DELAX ạ.",
            }
        )

        with patch("apps.callcenter.scoring._generate_json", return_value=payload) as generate:
            drain_pending_calls()
            calls_after_first = generate.call_count
            drain_pending_calls()

        self.assertEqual(generate.call_count, calls_after_first)
        self.assertEqual(CallQualityScore.objects.filter(call_record=record).count(), 1)

    def test_cuon_chieu_dung_khi_het_quota(self):
        for index in range(3):
            _make_record(call_id=f"qa-drain-quota-{index}")

        with patch(
            "apps.callcenter.scoring._generate_json",
            side_effect=RuntimeError("429 RESOURCE_EXHAUSTED"),
        ):
            summary = drain_pending_calls()

        self.assertEqual(summary["stopped_by"], "quota")
        self.assertTrue(summary["quota_stopped"])
        # Không quay vô hạn: cooldown bật sau vòng đầu là dừng luôn.
        self.assertEqual(summary["rounds"], 1)

    def test_cuon_chieu_dung_khi_cham_tran_thoi_gian(self):
        _make_record(call_id="qa-drain-deadline")

        # Số âm cho deadline ở quá khứ ⇒ dừng ngay, không phải chờ thật. Đây
        # cũng là hàng rào chống gõ nhầm env: âm KHÔNG được hiểu là vô hạn.
        with patch("apps.callcenter.scoring._generate_json") as generate:
            summary = drain_pending_calls(max_minutes=-1)

        generate.assert_not_called()
        self.assertEqual(summary["stopped_by"], "time_budget")
        self.assertEqual(summary["rounds"], 0)

    def test_force_bo_qua_kill_switch(self):
        """Chạy tay một lô để calibrate mà không phải bật beat lên chạy tự động."""
        _make_record(call_id="qa-force")
        payload = _gemini_payload(
            a1_greeting={
                "score": "2",
                "evidence": "Dạ em chào anh chị ạ, em gọi từ nhà DELAX ạ.",
            }
        )

        with override_settings(QA_SCORING_ENABLED=False):
            with patch("apps.callcenter.scoring._generate_json", return_value=payload):
                summary = score_pending_calls(force=True)

        self.assertEqual(summary["done"], 1)

    def test_force_van_can_api_key(self):
        _make_record(call_id="qa-force-nokey")

        with override_settings(QA_SCORING_ENABLED=False, GEMINI_API_KEY=""):
            summary = score_pending_calls(force=True)

        self.assertEqual(summary["claimed"], 0)

    def test_limit_0_claim_dung_0_record(self):
        _make_record(call_id="qa-limit-0")

        summary = score_pending_calls(limit=0)

        self.assertEqual(summary["claimed"], 0)

    def test_cham_lai_cung_rubric_khong_sinh_ban_ghi_trung(self):
        record = _make_record(call_id="qa-rescore")
        payload = _gemini_payload(
            a1_greeting={
                "score": "2",
                "evidence": "Dạ em chào anh chị ạ, em gọi từ nhà DELAX ạ.",
            }
        )

        with patch("apps.callcenter.scoring._generate_json", return_value=payload):
            score_pending_calls()
            CallRecord.objects.filter(id=record.id).update(score_status=ScoreStatus.PENDING)
            score_pending_calls()

        self.assertEqual(CallQualityScore.objects.filter(call_record=record).count(), 1)

    def test_cuoc_chua_khop_don_van_cham_duoc(self):
        """Khác extract: chấm điểm không ghi ngược vào đơn nên không cần order_id."""
        record = _make_record(call_id="qa-khong-don", order=None)
        payload = _gemini_payload(
            a1_greeting={
                "score": "1",
                "evidence": "Dạ em chào anh chị ạ, em gọi từ nhà DELAX ạ.",
            }
        )

        with patch("apps.callcenter.scoring._generate_json", return_value=payload):
            summary = score_pending_calls()

        self.assertEqual(summary["done"], 1)
        self.assertTrue(CallQualityScore.objects.filter(call_record=record).exists())


class ScoreErrorTests(SimpleTestCase):
    def test_error_giu_co_transient_va_quota(self):
        error = ScoreError("429 quá tải", transient=True, quota_exhausted=True)

        self.assertTrue(error.transient)
        self.assertTrue(error.quota_exhausted)


@override_settings(
    QA_SCORING_ENABLED=False,
    GEMINI_API_KEY="",
    QA_MIN_DURATION_SECONDS=60,
    QA_MIN_TRANSCRIPT_CHARS=400,
)
class ReScoreSkippedCommandTests(TestCase):
    """`--re-score-skipped`: kéo lại đúng nhóm bị bỏ qua nay đã lọt ngưỡng.

    Bối cảnh: đo prod 29/08 cho thấy 4.505/6.352 cuộc có transcript bị SKIPPED chỉ
    vì ngưỡng thời lượng. Hạ ngưỡng KHÔNG tự chấm lại — record đã SKIPPED nằm im
    mãi vì hàng đợi chỉ lấy PENDING. Cờ cũ `--re-score` thì kéo cả nhóm đã DONE về
    PENDING, tức gọi lại Gemini cho hàng nghìn cuộc đã chấm mà kết quả không đổi.
    """

    def _run(self, *args) -> str:
        out = StringIO()
        call_command("score_call_quality", *args, stdout=out)
        return out.getvalue()

    def test_chi_keo_lai_record_skipped_nay_du_nguong(self):
        du_nguong = _make_record(
            call_id="qa-rs-du", acd_time=90, score_status=ScoreStatus.SKIPPED
        )
        van_ngan = _make_record(
            call_id="qa-rs-ngan", acd_time=30, score_status=ScoreStatus.SKIPPED
        )
        transcript_ngan = _make_record(
            call_id="qa-rs-it-chu",
            acd_time=300,
            transcript="Nhân viên: Alo ạ.\n",
            score_status=ScoreStatus.SKIPPED,
        )

        self._run("--re-score-skipped")

        du_nguong.refresh_from_db()
        van_ngan.refresh_from_db()
        transcript_ngan.refresh_from_db()
        self.assertEqual(du_nguong.score_status, ScoreStatus.PENDING)
        self.assertEqual(van_ngan.score_status, ScoreStatus.SKIPPED)
        self.assertEqual(transcript_ngan.score_status, ScoreStatus.SKIPPED)

    def test_khong_dung_toi_cuoc_da_cham(self):
        """Khác `--re-score`: cuộc đã DONE phải nằm yên, không gọi lại Gemini."""
        da_cham = _make_record(
            call_id="qa-rs-done", acd_time=300, score_status=ScoreStatus.DONE
        )

        self._run("--re-score-skipped")

        da_cham.refresh_from_db()
        self.assertEqual(da_cham.score_status, ScoreStatus.DONE)

    def test_xoa_loi_va_dem_luot_thu_cua_record_duoc_keo_lai(self):
        record = _make_record(
            call_id="qa-rs-reset",
            acd_time=120,
            score_status=ScoreStatus.SKIPPED,
            score_attempts=2,
            score_error="Gemini lỗi cũ",
        )

        self._run("--re-score-skipped")

        record.refresh_from_db()
        self.assertEqual(record.score_attempts, 0)
        self.assertEqual(record.score_error, "")
