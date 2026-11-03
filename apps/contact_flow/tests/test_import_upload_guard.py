"""POST /api/contact-flow/import chặn file không phải .xlsx / quá 5MB trước khi vào
openpyxl (audit 2026-09-23 L4). Fixture thật vẫn đi qua (test_config_api)."""

from __future__ import annotations

from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.common import upload_guards
from apps.contact_flow.models import OperationStage
from apps.users.tests.helpers import make_user_with_sections

URL = "/api/contact-flow/import"


class ImportWorkbookUploadGuardTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=make_user_with_sections("cf-import", ["contact_flow"]))

    def _post(self, upload):
        return self.client.post(URL, {"file": upload}, format="multipart")

    def test_non_zip_payload_rejected_and_db_clean(self):
        # DB có thể đã có seed OperationStage (migration/fixture) → so trước/sau
        # thay vì đòi bằng 0.
        before = OperationStage.objects.count()
        resp = self._post(SimpleUploadedFile("bad.xlsx", b"not a workbook"))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Excel", resp.json()["detail"])
        self.assertEqual(OperationStage.objects.count(), before)

    def test_oversize_rejected(self):
        upload = SimpleUploadedFile("big.xlsx", b"PK\x03\x04" + b"\x00" * 20)
        with patch.object(upload_guards, "XLSX_MAX_BYTES", 16):
            self.assertEqual(self._post(upload).status_code, 400)
