"""Test command `export_call_transcript` — lấy transcript theo 1 SĐT ra file.

Mock toàn bộ HTTP (Zenify + tải audio + Gemini) — không gọi mạng thật.
"""

import pytest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.callcenter.management.commands.export_call_transcript import build_phone_filter
from apps.callcenter.models import CallRecord, TranscribeStatus

PHONE = "0912345678"
HOTLINE = "02471000000"


def make_record(call_id="c1", *, caller=PHONE, called=HOTLINE, status=TranscribeStatus.PENDING, **extra):
    defaults = {
        "call_type": "1",
        "agent_name": "Agent A",
        "caller": caller,
        "caller_normalized": caller if caller.startswith("0") else "",
        "called": called,
        "recording_url": f"https://rec.zenify.vn/{call_id}.mp3",
        "start_time": timezone.now(),
        "acd_time": 65,
        "transcribe_status": status,
    }
    defaults.update(extra)
    return CallRecord.objects.create(call_id=call_id, **defaults)


@override_settings(GEMINI_API_KEY="k", GEMINI_MAX_RPM=600, GEMINI_MAX_RPD=100)
# Counter quota Gemini: LocMemCache riêng từng test (backend/conftest.py).
@pytest.mark.usefixtures("isolated_cache")
class ExportCallTranscriptTests(TestCase):
    def setUp(self):
        self.out = Path(self.mktemp())

    def mktemp(self) -> str:
        import tempfile

        handle = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def run_command(self, **kwargs):
        stdout = StringIO()
        call_command(
            "export_call_transcript",
            phone=PHONE,
            out=str(self.out),
            no_sync=True,
            stdout=stdout,
            stderr=StringIO(),
            **kwargs,
        )
        return stdout.getvalue()

    def test_transcribe_ghi_file_va_luu_db(self):
        record = make_record()
        with (
            patch("apps.callcenter.management.commands.export_call_transcript.download_recording", return_value=b"audio"),
            patch(
                "apps.callcenter.management.commands.export_call_transcript.transcribe_audio",
                return_value="Nhân viên: alo\nKhách: vâng",
            ),
        ):
            self.run_command()

        record.refresh_from_db()
        self.assertEqual(record.transcribe_status, TranscribeStatus.DONE)
        content = self.out.read_text(encoding="utf-8")
        self.assertIn(PHONE, content)
        self.assertIn("Nhân viên: alo", content)

    def test_cuoc_goi_ra_khach_o_cot_called_van_lay_duoc(self):
        """Chiều gọi RA: SĐT khách nằm ở ``called``, ``caller_normalized`` rỗng."""
        make_record("c2", caller=HOTLINE, called=PHONE, caller_normalized="")
        with (
            patch("apps.callcenter.management.commands.export_call_transcript.download_recording", return_value=b"audio"),
            patch("apps.callcenter.management.commands.export_call_transcript.transcribe_audio", return_value="ok"),
        ):
            self.run_command()
        self.assertIn("c2", self.out.read_text(encoding="utf-8"))

    def test_no_transcribe_chi_xuat_cai_da_co(self):
        make_record("c3", status=TranscribeStatus.DONE, transcript="đã có sẵn")
        with patch(
            "apps.callcenter.management.commands.export_call_transcript.transcribe_audio"
        ) as gemini:
            self.run_command(no_transcribe=True)
        gemini.assert_not_called()
        self.assertIn("đã có sẵn", self.out.read_text(encoding="utf-8"))

    def test_khong_co_cuoc_goi_thi_bao_loi_ro_rang(self):
        with self.assertRaises(CommandError):
            self.run_command()

    @override_settings(GEMINI_API_KEY="k", GEMINI_MAX_RPD=0)
    def test_ignore_quota_bo_qua_tran_redis(self):
        """Trần RPD = 0 (chặn mọi request) nhưng --ignore-quota vẫn transcribe được."""
        record = make_record("c5")
        with (
            patch("apps.callcenter.management.commands.export_call_transcript.download_recording", return_value=b"audio"),
            patch("apps.callcenter.management.commands.export_call_transcript.transcribe_audio", return_value="ok"),
        ):
            self.run_command(ignore_quota=True)
        record.refresh_from_db()
        self.assertEqual(record.transcribe_status, TranscribeStatus.DONE)

    def test_build_phone_filter_khong_bat_so_khac(self):
        make_record("c4", caller="0900000001", caller_normalized="0900000001", called="0900000002")
        matched = CallRecord.objects.filter(build_phone_filter(PHONE))
        self.assertEqual(matched.count(), 0)
