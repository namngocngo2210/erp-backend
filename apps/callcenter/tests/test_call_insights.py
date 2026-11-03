"""Test luồng extract chân dung khách từ transcript (Gemini structured output).

Mock toàn bộ Gemini — không gọi mạng thật.
"""

import json
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.callcenter.extract import (
    EXTRACT_COOLDOWN_KEY,
    ExtractError,
    extract_insights,
    extract_pending_insights,
)
from apps.callcenter.models import CallRecord, ExtractStatus, TranscribeStatus
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import NeedGroup, NeedType, Order, OrderAssessment


class ExtractStatusFieldsTests(TestCase):
    def test_new_record_defaults_to_pending_extract(self):
        record = CallRecord.objects.create(
            call_id="c-extract-1",
            transcribe_status=TranscribeStatus.DONE,
            transcript="Nhân viên: alo ạ",
        )
        record.refresh_from_db()
        self.assertEqual(record.extract_status, ExtractStatus.PENDING)
        self.assertEqual(record.extract_payload, {})
        self.assertEqual(record.extract_error, "")
        self.assertEqual(record.extract_attempts, 0)


GEMINI_JSON = json.dumps(
    {
        "age_text": "45_54",
        "pain_level_code": "severe",
        "lifestyle_habit_codes": ["sit_a_lot", "ma_khong_ton_tai"],
        "need_type_codes": ["knee_pain"],
        "evidence": [
            {"field": "age_text", "quote": "em 45 tuổi"},
            {"field": "pain_level_code", "quote": "đau nặng lắm"},
        ],
    }
)


@override_settings(GEMINI_API_KEY="k", GEMINI_EXTRACT_MODEL="gemini-flash-latest")
class ExtractInsightsTests(TestCase):
    def _run(self, response_text):
        with patch("apps.callcenter.extract._generate_json", return_value=response_text) as mock:
            return extract_insights("Nhân viên: alo ạ" * 30), mock

    def test_parses_and_normalizes(self):
        (raw, clean, rejected), _ = self._run(GEMINI_JSON)
        self.assertEqual(raw["age_text"], "45_54")
        self.assertEqual(clean["pain_level_code"], "severe")
        self.assertEqual(clean["lifestyle_habit_codes"], ["sit_a_lot"])
        self.assertEqual(clean["evidence"]["age_text"], "em 45 tuổi")
        self.assertIn("ma_khong_ton_tai", rejected["lifestyle_habit_codes"])

    def test_invalid_json_is_content_error(self):
        with patch("apps.callcenter.extract._generate_json", return_value="khong phai json"):
            with self.assertRaises(ExtractError) as ctx:
                extract_insights("x" * 300)
        self.assertFalse(ctx.exception.transient)

    def test_empty_response_is_content_error(self):
        with patch("apps.callcenter.extract._generate_json", return_value="  "):
            with self.assertRaises(ExtractError) as ctx:
                extract_insights("x" * 300)
        self.assertFalse(ctx.exception.transient)

    def test_api_429_is_transient(self):
        with patch(
            "apps.callcenter.extract._generate_json",
            side_effect=RuntimeError("429 RESOURCE_EXHAUSTED"),
        ):
            with self.assertRaises(ExtractError) as ctx:
                extract_insights("x" * 300)
        self.assertTrue(ctx.exception.transient)
        self.assertTrue(ctx.exception.quota_exhausted)

    def test_api_503_is_transient_but_not_quota_exhausted(self):
        # 503 UNAVAILABLE là hạ tầng Gemini chập chờn, không phải Google từ
        # chối do hết quota — Task 6 chỉ nghỉ trọn cooldown khi quota_exhausted,
        # nên 2 cờ này phải tách biệt. Nếu _QUOTA_MARKERS lỡ bị mở rộng trùng
        # _TRANSIENT_MARKERS thì test này phải fail để bắt lỗi ngay.
        with patch(
            "apps.callcenter.extract._generate_json",
            side_effect=RuntimeError("503 UNAVAILABLE"),
        ):
            with self.assertRaises(ExtractError) as ctx:
                extract_insights("x" * 300)
        self.assertTrue(ctx.exception.transient)
        self.assertFalse(ctx.exception.quota_exhausted)

    def test_extract_error_from_generate_json_passes_through_unchanged(self):
        # Nhánh thiếu SDK trong _generate_json tự raise ExtractError đã phân
        # loại sẵn (transient=False). extract_insights có `except ExtractError:
        # raise` để giữ nguyên, không bọc lại qua nhánh `except Exception`
        # (sẽ đổi message thành "Gemini lỗi: ..." và phân loại lại theo
        # _TRANSIENT_MARKERS/_QUOTA_MARKERS — sai vì lỗi thiếu thư viện không
        # phải lỗi mạng). Xoá guard clause đó sẽ khiến test này fail.
        original = ExtractError("Thiếu thư viện google-genai: no module named 'google'", transient=False)
        with patch("apps.callcenter.extract._generate_json", side_effect=original):
            with self.assertRaises(ExtractError) as ctx:
                extract_insights("x" * 300)
        self.assertIs(ctx.exception, original)
        self.assertFalse(ctx.exception.transient)
        self.assertFalse(ctx.exception.quota_exhausted)


def make_order(code: str) -> Order:
    """Order.channel và Order.customer đều là FK NOT NULL (PROTECT) —
    thiếu 1 trong 2 là IntegrityError."""
    channel = Channel.objects.create(code=code, name=f"Landing {code}")
    customer = Customer.objects.create(
        full_name="Khách A", phone_raw="0912345678", phone_normalized="0912345678"
    )
    return Order.objects.create(
        channel=channel, customer=customer, name="Khách A", phone="0912345678", status=0
    )


@override_settings(
    GEMINI_API_KEY="k",
    GEMINI_EXTRACT_ENABLED=True,
    GEMINI_EXTRACT_BATCH=10,
    GEMINI_EXTRACT_MAX_ATTEMPTS=3,
    GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS=200,
)
class ExtractBatchTests(TestCase):
    def setUp(self):
        self.order = make_order("lp-ai-batch")
        # "pain_relief"/"knee_pain" đã có sẵn từ data migration catalog (0026,
        # 0043) — get_or_create để không đụng unique constraint khi chạy full suite.
        group, _ = NeedGroup.objects.get_or_create(
            code="pain_relief", defaults={"name_vi": "Giảm đau nhức", "sort_order": 10}
        )
        NeedType.objects.get_or_create(
            code="knee_pain", defaults={"group": group, "name_vi": "Đau đầu gối", "sort_order": 80}
        )

    def _record(self, **kwargs):
        defaults = {
            "call_id": "c-batch-1",
            "transcribe_status": TranscribeStatus.DONE,
            "transcript": "Khách: em 45 tuổi, đau nặng lắm. " * 20,
            "order": self.order,
        }
        defaults.update(kwargs)
        return CallRecord.objects.create(**defaults)

    def test_done_record_fills_assessment(self):
        record = self._record()
        with patch("apps.callcenter.extract._generate_json", return_value=GEMINI_JSON):
            summary = extract_pending_insights()
        record.refresh_from_db()
        self.assertEqual(summary["done"], 1)
        self.assertEqual(record.extract_status, ExtractStatus.DONE)
        self.assertEqual(record.extract_payload["clean"]["pain_level_code"], "severe")
        self.assertIn("ma_khong_ton_tai", record.extract_payload["rejected"]["lifestyle_habit_codes"])
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.pain_level_code, "severe")

    def test_unmatched_record_is_not_claimed(self):
        self._record(call_id="c-nomatch", order=None)
        with patch("apps.callcenter.extract._generate_json", return_value=GEMINI_JSON) as mock:
            summary = extract_pending_insights()
        self.assertEqual(summary["claimed"], 0)
        mock.assert_not_called()

    def test_short_transcript_is_skipped_without_calling_gemini(self):
        record = self._record(call_id="c-short", transcript="alo")
        with patch("apps.callcenter.extract._generate_json") as mock:
            extract_pending_insights()
        record.refresh_from_db()
        self.assertEqual(record.extract_status, ExtractStatus.SKIPPED)
        mock.assert_not_called()

    def test_transient_error_does_not_burn_attempt(self):
        record = self._record(call_id="c-transient")
        with patch(
            "apps.callcenter.extract._generate_json", side_effect=RuntimeError("503 unavailable")
        ):
            extract_pending_insights()
        record.refresh_from_db()
        self.assertEqual(record.extract_attempts, 0)
        self.assertEqual(record.extract_status, ExtractStatus.PENDING)

    def test_content_error_burns_attempt_and_fails_at_limit(self):
        record = self._record(call_id="c-bad-json", extract_attempts=2)
        with patch("apps.callcenter.extract._generate_json", return_value="not json"):
            extract_pending_insights()
        record.refresh_from_db()
        self.assertEqual(record.extract_attempts, 3)
        self.assertEqual(record.extract_status, ExtractStatus.FAILED)

    def test_quota_error_enters_cooldown_and_requeues(self):
        record = self._record(call_id="c-quota")
        with patch(
            "apps.callcenter.extract._generate_json",
            side_effect=RuntimeError("429 RESOURCE_EXHAUSTED"),
        ):
            summary = extract_pending_insights()
        record.refresh_from_db()
        self.assertTrue(summary["quota_stopped"])
        self.assertEqual(record.extract_status, ExtractStatus.PENDING)
        self.assertEqual(record.extract_attempts, 0)
        self.assertIsNotNone(cache.get(EXTRACT_COOLDOWN_KEY))
        cache.delete(EXTRACT_COOLDOWN_KEY)

    def test_cooldown_blocks_claim_before_batch_is_taken(self):
        # Regression cho sự cố 2026-07-22: cooldown phải được check TRƯỚC khi
        # claim batch. Test bắt đầu với cooldown đã tồn tại sẵn (khác với test
        # ở trên chỉ set cooldown NHƯ hệ quả của lỗi quota) — nếu code bị đảo
        # thứ tự claim-trước-check thì record sẽ bị claim (PROCESSING) và
        # Gemini sẽ bị gọi, test này sẽ fail.
        record = self._record(call_id="c-cooldown")
        cache.set(EXTRACT_COOLDOWN_KEY, 1, timeout=60)
        try:
            with patch("apps.callcenter.extract._generate_json") as mock:
                summary = extract_pending_insights()
            mock.assert_not_called()
        finally:
            cache.delete(EXTRACT_COOLDOWN_KEY)
        record.refresh_from_db()
        self.assertEqual(summary["claimed"], 0)
        self.assertTrue(summary["quota_stopped"])
        self.assertEqual(record.extract_status, ExtractStatus.PENDING)

    def test_limit_zero_claims_nothing(self):
        # limit=0 là giá trị hợp lệ khác biệt với limit=None (mặc định batch
        # size) — `0 or X` trong Python coi 0 là falsy nên dễ bị nuốt mất ý
        # nghĩa "không claim gì" nếu code dùng `limit or settings...`.
        self._record(call_id="c-limit-zero")
        with patch("apps.callcenter.extract._generate_json") as mock:
            summary = extract_pending_insights(limit=0)
        mock.assert_not_called()
        self.assertEqual(summary["claimed"], 0)

    def test_disabled_flag_is_noop(self):
        self._record(call_id="c-disabled")
        with override_settings(GEMINI_EXTRACT_ENABLED=False):
            with patch("apps.callcenter.extract._generate_json") as mock:
                summary = extract_pending_insights()
        self.assertEqual(summary["claimed"], 0)
        mock.assert_not_called()


@override_settings(GEMINI_API_KEY="k", GEMINI_EXTRACT_ENABLED=True,
                   GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS=200)
class ExtractCommandTests(TestCase):
    def setUp(self):
        self.order = make_order("lp-ai-cmd")
        # pain_relief/knee_pain đã có sẵn từ data migration catalog — get_or_create
        # để không đụng unique constraint khi chạy full suite (như ExtractBatchTests).
        group, _ = NeedGroup.objects.get_or_create(
            code="pain_relief", defaults={"name_vi": "Giảm đau nhức", "sort_order": 10}
        )
        NeedType.objects.get_or_create(
            code="knee_pain", defaults={"group": group, "name_vi": "Đau đầu gối", "sort_order": 80}
        )
        self.record = CallRecord.objects.create(
            call_id="c-cmd-1",
            transcribe_status=TranscribeStatus.DONE,
            transcript="Khách: em 45 tuổi, đau nặng lắm. " * 20,
            order=self.order,
        )

    def test_dry_run_does_not_write_anything(self):
        out = StringIO()
        with patch("apps.callcenter.extract._generate_json", return_value=GEMINI_JSON):
            call_command("extract_call_insights", "--call-record", str(self.record.id),
                         "--dry-run", stdout=out)
        self.record.refresh_from_db()
        self.assertEqual(self.record.extract_status, ExtractStatus.PENDING)
        self.assertFalse(OrderAssessment.objects.filter(order=self.order).exists())
        self.assertIn("pain_level_code", out.getvalue())

    def test_re_extract_resets_done_record_to_pending(self):
        self.record.extract_status = ExtractStatus.DONE
        self.record.save(update_fields=["extract_status"])
        with patch("apps.callcenter.extract._generate_json", return_value=GEMINI_JSON):
            call_command("extract_call_insights", "--order", str(self.order.id), "--re-extract",
                         stdout=StringIO())
        self.record.refresh_from_db()
        self.assertEqual(self.record.extract_status, ExtractStatus.DONE)
        self.assertEqual(OrderAssessment.objects.get(order=self.order).pain_level_code, "severe")

    def test_re_extract_does_not_reset_processing_record(self):
        # PROCESSING nghĩa là beat extract_pending_call_insights đang xử lý dở dang.
        # --re-extract không được kéo nó về PENDING, nếu không batch chạy ngay sau
        # sẽ claim lại record này và gọi Gemini lần 2 trên cùng transcript.
        self.record.extract_status = ExtractStatus.PROCESSING
        self.record.save(update_fields=["extract_status"])
        with patch("apps.callcenter.extract._generate_json", return_value=GEMINI_JSON):
            call_command("extract_call_insights", "--order", str(self.order.id), "--re-extract",
                         stdout=StringIO())
        self.record.refresh_from_db()
        self.assertEqual(self.record.extract_status, ExtractStatus.PROCESSING)
