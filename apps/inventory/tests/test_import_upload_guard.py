"""POST /api/inventory/import/parse-lines chặn file không phải .xlsx / quá 5MB
trước khi vào openpyxl (audit 2026-09-23 L4)."""

from __future__ import annotations

from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.common import upload_guards
from apps.users.tests.helpers import make_user_with_sections

URL = "/api/inventory/import/parse-lines"


class ParseImportLinesUploadGuardTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=make_user_with_sections("kho-import", ["stock-documents"]))

    def _post(self, upload):
        return self.client.post(URL, {"file": upload}, format="multipart")

    def test_non_zip_payload_named_xlsx_rejected(self):
        resp = self._post(SimpleUploadedFile("t.xlsx", b"<html>x</html>"))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Excel", resp.json()["detail"])

    def test_wrong_extension_rejected(self):
        resp = self._post(SimpleUploadedFile("t.csv", b"PK\x03\x04" + b"\x00" * 20))
        self.assertEqual(resp.status_code, 400)

    def test_oversize_rejected(self):
        # APIClient mã hoá lại multipart nên `upload.size` giả không tới view —
        # hạ trần xuống 16 byte và gửi file thật 24 byte.
        upload = SimpleUploadedFile("t.xlsx", b"PK\x03\x04" + b"\x00" * 20)
        with patch.object(upload_guards, "XLSX_MAX_BYTES", 16):
            resp = self._post(upload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("quá lớn", resp.json()["detail"])
