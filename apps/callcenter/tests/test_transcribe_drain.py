"""Test command `drain_transcribe` — chạy tay để tiêu hết hàng đợi transcribe.

Bối cảnh: cron `*/5` claim 8 record/tick và xử lý tuần tự nên chỉ đạt ~50 record/ngày;
backlog 685 file (đo prod 2026-07-27) cần ~13 ngày. Command này chạy song song nhiều
luồng cho tới khi hết hàng đợi. Xem docs/superpowers/specs/2026-07-27-transcribe-drain-command-design.md.

Mock toàn bộ HTTP (tải audio + Gemini) — không gọi mạng thật.
"""

import pytest
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.callcenter.models import CallRecord, TranscribeStatus
from apps.callcenter.transcribe import TranscribeError

PHONE = "0912345678"


def _reset_quota_counters():
    """Xoá đích danh counter quota phút/ngày hiện tại — KHÔNG cache.clear()
    (FLUSHDB đè Redis thật, xem docs/audit-followups-2026-07-06.md)."""
    now = timezone.now()
    cache.delete(f"gemini:transcribe:rpm:{now.strftime('%Y%m%d%H%M')}")
    cache.delete(f"gemini:transcribe:rpd:{now.strftime('%Y%m%d')}")
    cache.delete("gemini:transcribe:cooldown")


def _make_record(call_id, **kwargs):
    defaults = dict(
        call_id=call_id,
        caller=PHONE,
        caller_normalized=PHONE,
        recording_url=f"https://rec.zenify.vn/{call_id}.mp3",
        acd_time=60,
        transcribe_status=TranscribeStatus.PENDING,
    )
    defaults.update(kwargs)
    return CallRecord.objects.create(**defaults)


def _drain(**options):
    """Chạy command với concurrency=1 (inline, không tạo thread) và trả stdout.

    concurrency=1 là bắt buộc trong TestCase: test chạy trong transaction chưa
    commit nên thread khác — dùng connection khác — không thấy record vừa tạo.
    """
    out = StringIO()
    options.setdefault("concurrency", 1)
    call_command("drain_transcribe", stdout=out, **options)
    return out.getvalue()


@override_settings(GEMINI_API_KEY="test-key", GEMINI_MAX_RPM=100, GEMINI_MAX_RPD=100)
class DrainTranscribeTests(TestCase):
    def setUp(self):
        _reset_quota_counters()

    def test_drains_whole_queue_across_batches(self):
        """Command phải chạy tới khi hết PENDING, không dừng ở một batch."""
        for i in range(5):
            _make_record(f"c{i}")
        with (
            patch("apps.callcenter.transcribe.download_recording", return_value=b"audio"),
            patch("apps.callcenter.transcribe.transcribe_audio", return_value="Nhân viên: alo"),
        ):
            _drain()
        statuses = list(CallRecord.objects.values_list("transcribe_status", flat=True))
        self.assertEqual(statuses, [TranscribeStatus.DONE] * 5)

    def test_limit_stops_early(self):
        for i in range(5):
            _make_record(f"c{i}")
        with (
            patch("apps.callcenter.transcribe.download_recording", return_value=b"audio"),
            patch("apps.callcenter.transcribe.transcribe_audio", return_value="ok"),
        ):
            _drain(limit=2)
        self.assertEqual(
            CallRecord.objects.filter(transcribe_status=TranscribeStatus.DONE).count(), 2
        )
        self.assertEqual(
            CallRecord.objects.filter(transcribe_status=TranscribeStatus.PENDING).count(), 3
        )

    def test_dry_run_changes_nothing(self):
        _make_record("c1")
        with (
            patch("apps.callcenter.transcribe.download_recording") as download,
            patch("apps.callcenter.transcribe.transcribe_audio") as gemini,
        ):
            output = _drain(dry_run=True)
        download.assert_not_called()
        gemini.assert_not_called()
        record = CallRecord.objects.get(call_id="c1")
        self.assertEqual(record.transcribe_status, TranscribeStatus.PENDING)
        self.assertIn("1", output)

    def test_error_record_does_not_stop_the_run(self):
        """Một record hỏng không được chặn phần còn lại của hàng đợi."""
        _make_record("c1")
        _make_record("c2")

        def fake_download(url):
            if "c1" in url:
                raise TranscribeError("Tải file ghi âm thất bại: HTTP 404")
            return b"audio"

        with (
            patch("apps.callcenter.transcribe.download_recording", side_effect=fake_download),
            patch("apps.callcenter.transcribe.transcribe_audio", return_value="ok"),
        ):
            _drain()
        self.assertEqual(CallRecord.objects.get(call_id="c2").transcribe_status,
                         TranscribeStatus.DONE)
        broken = CallRecord.objects.get(call_id="c1")
        self.assertEqual(broken.transcribe_attempts, 1)
        self.assertEqual(broken.transcribe_status, TranscribeStatus.PENDING)

    def test_transient_error_does_not_loop_forever(self):
        """Lỗi transient không đốt attempt nên record quay lại PENDING mãi. Nếu vòng
        drain claim lại chính nó thì command chạy vô tận — mỗi record chỉ được thử
        một lần trong một lần chạy, lần sau để cron lo."""
        _make_record("c1")
        calls = {"n": 0}

        def flaky_download(url):
            calls["n"] += 1
            raise TranscribeError("Không tải được file ghi âm: timeout", transient=True)

        with patch(
            "apps.callcenter.transcribe.download_recording", side_effect=flaky_download
        ):
            _drain()
        self.assertEqual(calls["n"], 1, "record lỗi không được thử lại trong cùng lần drain")
        record = CallRecord.objects.get(call_id="c1")
        self.assertEqual(record.transcribe_attempts, 0, "transient không đốt lượt")
        self.assertEqual(record.transcribe_status, TranscribeStatus.PENDING)

    @override_settings(GEMINI_MAX_RPD=1)
    def test_quota_stops_run_and_leaves_nothing_processing(self):
        for i in range(4):
            _make_record(f"c{i}")
        with (
            patch("apps.callcenter.transcribe.download_recording", return_value=b"audio"),
            patch("apps.callcenter.transcribe.transcribe_audio", return_value="ok"),
        ):
            _drain()
        self.assertEqual(
            CallRecord.objects.filter(transcribe_status=TranscribeStatus.DONE).count(), 1
        )
        self.assertEqual(
            CallRecord.objects.filter(transcribe_status=TranscribeStatus.PROCESSING).count(),
            0,
            "dừng vì quota không được để record kẹt PROCESSING",
        )
        self.assertEqual(
            CallRecord.objects.filter(transcribe_status=TranscribeStatus.PENDING).count(), 3
        )

    @override_settings(GEMINI_MAX_RPD=1)
    def test_rpd_override_lifts_configured_ceiling(self):
        """--rpd nâng trần trong tiến trình drain, khỏi sửa .env + restart worker."""
        for i in range(3):
            _make_record(f"c{i}")
        with (
            patch("apps.callcenter.transcribe.download_recording", return_value=b"audio"),
            patch("apps.callcenter.transcribe.transcribe_audio", return_value="ok"),
        ):
            _drain(rpd=50)
        self.assertEqual(
            CallRecord.objects.filter(transcribe_status=TranscribeStatus.DONE).count(), 3
        )

    def test_skips_records_without_recording_url(self):
        _make_record("c1", recording_url="")
        with (
            patch("apps.callcenter.transcribe.download_recording", return_value=b"audio"),
            patch("apps.callcenter.transcribe.transcribe_audio", return_value="ok"),
        ):
            _drain()
        self.assertEqual(
            CallRecord.objects.get(call_id="c1").transcribe_status, TranscribeStatus.PENDING
        )

    @override_settings(GEMINI_API_KEY="")
    def test_refuses_to_run_without_api_key(self):
        _make_record("c1")
        with patch("apps.callcenter.transcribe.download_recording") as download:
            _drain()
        download.assert_not_called()


@override_settings(GEMINI_API_KEY="test-key", GEMINI_MAX_RPM=100, GEMINI_MAX_RPD=100)
@pytest.mark.slow  # race/timeout thật ≥5 s — CI vẫn chạy
class DrainTranscribeConcurrencyTests(TransactionTestCase):
    """Thread pool cần commit thật để thread con (connection riêng) thấy record —
    TestCase bọc transaction chưa commit nên phải dùng TransactionTestCase."""

    def setUp(self):
        _reset_quota_counters()

    def tearDown(self):
        _reset_quota_counters()

    def test_concurrent_run_processes_every_record(self):
        for i in range(6):
            _make_record(f"c{i}")
        out = StringIO()
        with (
            patch("apps.callcenter.transcribe.download_recording", return_value=b"audio"),
            patch("apps.callcenter.transcribe.transcribe_audio", return_value="ok"),
        ):
            call_command("drain_transcribe", concurrency=2, stdout=out)
        statuses = set(CallRecord.objects.values_list("transcribe_status", flat=True))
        self.assertEqual(statuses, {TranscribeStatus.DONE})
