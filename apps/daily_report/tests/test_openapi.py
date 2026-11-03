"""File OpenAPI tĩnh là thứ sếp gửi cho trợ lý — lệch code là lệch hợp đồng."""

from io import StringIO
from pathlib import Path

import pytest
import yaml
from django.core.management import call_command
from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator

SCHEMA_FILE = Path(__file__).resolve().parents[4] / "docs" / "openapi" / "daily-report.yaml"
REGEN = (
    "python manage.py spectacular --urlconf apps.daily_report.urls"
    " --custom-settings apps.daily_report.openapi.SPECTACULAR_SETTINGS"
    " --file ../docs/openapi/daily-report.yaml"
)


def _generate() -> str:
    out = StringIO()
    call_command(
        "spectacular",
        urlconf="apps.daily_report.urls",
        custom_settings="apps.daily_report.openapi.SPECTACULAR_SETTINGS",
        stdout=out,
    )
    return out.getvalue()


class OpenApiTests(SimpleTestCase):
    def test_committed_file_matches_code(self):
        if not SCHEMA_FILE.parent.is_dir():
            # Chỉ mount backend/ (container test) thì không có docs/ ở thư mục cha —
            # skip kèm lý do; CI repo đầy đủ vẫn chạy (audit 2026-09-23 C9).
            pytest.skip(f"không thấy {SCHEMA_FILE.parent} — cần mount cả repo")
        self.assertTrue(SCHEMA_FILE.exists(), f"thiếu {SCHEMA_FILE}; chạy: {REGEN}")
        self.assertEqual(yaml.safe_load(SCHEMA_FILE.read_text()), yaml.safe_load(_generate()), f"lệch; chạy: {REGEN}")

    def test_all_eight_endpoints_documented_with_api_key(self):
        schema = yaml.safe_load(_generate())
        self.assertEqual(
            set(schema["paths"]),
            {"/health", "/definitions", "/leads/stats", "/orders/stats", "/revenue", "/pipeline", "/summary", "/ads"},
        )
        self.assertIn("ApiKeyAuth", schema["components"]["securitySchemes"])
        for path, ops in schema["paths"].items():
            self.assertEqual(ops["get"]["security"], [{"ApiKeyAuth": []}], path)
            expected = "410" if path == "/summary" else "200"
            self.assertIn(expected, ops["get"]["responses"], path)
        self.assertEqual(schema["servers"][0]["url"], "{base}/api/daily-report")

    def test_main_schema_excludes_daily_report(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        leaked = [p for p in schema["paths"] if p.startswith("/api/daily-report")]
        self.assertEqual(leaked, [])
