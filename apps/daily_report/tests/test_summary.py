from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .helpers import TOKEN, client

URL = "/api/daily-report/summary"


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class SummaryGoneTests(TestCase):
    def test_410_points_to_replacements(self):
        resp = client().get(URL, {"date": "2026-09-02"})
        self.assertEqual(resp.status_code, 410)
        detail = resp.json()["detail"]
        for path in ("/leads/stats", "/orders/stats", "/pipeline"):
            self.assertIn(path, detail)

    def test_still_requires_token(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)
