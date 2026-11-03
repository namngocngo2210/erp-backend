from unittest.mock import patch

from django.db import DatabaseError, connection
from django.test import TestCase, override_settings

from .helpers import TOKEN, client, rebuild

URL = "/api/daily-report/health"
LEADS_URL = "/api/daily-report/leads/stats"


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class HealthTests(TestCase):
    def test_ok_after_mart_run(self):
        rebuild()
        resp = client().get(URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["checks"]["db"])
        self.assertTrue(body["checks"]["redis"])
        self.assertTrue(body["checks"]["mart"]["ok"])
        self.assertEqual(body["checks"]["mart"]["status"], "Bình thường")
        self.assertIsNotNone(body["checks"]["mart"]["data_as_of"])
        self.assertIsInstance(body["checks"]["mart"]["lag_minutes"], int)

    def test_503_when_mart_never_ran(self):
        resp = client().get(URL)
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(resp.json()["checks"]["mart"]["ok"])

    def test_503_when_mart_is_late(self):
        rebuild()
        with connection.cursor() as cur:
            cur.execute("UPDATE ai_meta.nhat_ky_chay SET ket_thuc = ket_thuc - interval '2 hours'")
        resp = client().get(URL)
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["checks"]["mart"]["status"], "Trễ — số liệu có thể cũ")

    def test_503_when_db_check_fails(self):
        rebuild()
        with patch("apps.daily_report.transport.views.check_db", return_value=False):
            resp = client().get(URL)
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(resp.json()["checks"]["db"])

    def test_503_not_500_when_mart_view_query_fails(self):
        """DatabaseError khi đọc ai.tinh_trang_du_lieu → freshness lỗi rõ ràng,
        health() vẫn tính ok=False, không văng 500."""
        rebuild()
        with patch("apps.daily_report.infrastructure.freshness.connection") as mock_conn:
            mock_conn.cursor.return_value.__enter__.return_value.execute.side_effect = DatabaseError("boom")
            resp = client().get(URL)
        self.assertEqual(resp.status_code, 503, resp.content)
        self.assertFalse(resp.json()["checks"]["mart"]["ok"])

    def test_leads_stats_503_not_500_when_query_fails(self):
        rebuild()
        with patch(
            "apps.daily_report.transport.views.stats_service.leads_payload",
            side_effect=DatabaseError("boom"),
        ):
            resp = client().get(LEADS_URL)
        self.assertEqual(resp.status_code, 503, resp.content)
        self.assertIn("detail", resp.json())
