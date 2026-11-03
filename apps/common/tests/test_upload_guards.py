"""Guard file .xlsx dùng chung (audit 2026-09-23 L4) — apps/common/upload_guards.py."""

from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from apps.common.upload_guards import XLSX_MAX_BYTES, validate_xlsx_upload

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_ZIP_HEAD = b"PK\x03\x04" + b"\x00" * 60


def _upload(name="a.xlsx", content=_ZIP_HEAD, size=None):
    upload = SimpleUploadedFile(name, content, content_type=_XLSX)
    if size is not None:
        upload.size = size
    return upload


class ValidateXlsxUploadTests(SimpleTestCase):
    def test_valid_xlsx_passes_and_rewinds(self):
        upload = _upload()
        self.assertIsNone(validate_xlsx_upload(upload))
        # Parser đọc lại từ đầu — guard đã seek(0) sau khi sniff magic.
        self.assertEqual(upload.read(4), b"PK\x03\x04")

    def test_xlsm_accepted(self):
        self.assertIsNone(validate_xlsx_upload(_upload(name="macro.XLSM")))

    def test_oversize_rejected_without_reading(self):
        message = validate_xlsx_upload(_upload(size=XLSX_MAX_BYTES + 1))
        self.assertIn("quá lớn", message)
        self.assertIn("5MB", message)

    def test_wrong_extension_rejected(self):
        self.assertIn(".xlsx", validate_xlsx_upload(_upload(name="a.csv")))
        self.assertIn(".xlsx", validate_xlsx_upload(_upload(name="a.xls")))

    def test_wrong_magic_rejected(self):
        message = validate_xlsx_upload(_upload(content=b"<html>not excel</html>"))
        self.assertIn("không phải định dạng Excel", message)
        # File rỗng cũng bị chặn (openpyxl sẽ raise mù mờ).
        self.assertIsNotNone(validate_xlsx_upload(_upload(content=b"")))
