"""Cửa xác thực của API Daily Report — chỗ dễ hỏng nhất, hỏng thì lộ số liệu
công ty ra ngoài. Bộ test đầy đủ cho helper gốc ở callcenter (Hermes); ở đây
smoke + ca riêng + guard mọi route."""

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.daily_report import urls as daily_report_urls

from .helpers import TOKEN

URL = "/api/daily-report/health"


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class DailyReportAuthTests(TestCase):
    """Bộ đầy đủ cho cửa token (thiếu/sai token, Bearer, JWT CRM không đủ, fail-closed
    prod) là `apps/callcenter/tests/test_hermes_transcript_api.py::HermesAuthTests`
    — helper gốc mà `require_api_token` chép lại. Ở đây: 1 smoke "đi qua cửa" +
    các ca riêng của bản sao này (non-ASCII, dev mở khi thiếu env, mọi route)."""

    def test_token_gate_smoke(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)
        resp = APIClient().get(URL, HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        self.assertIn(resp.status_code, (200, 503))  # 503 = mart chưa chạy, vẫn là đã qua cửa

    def test_non_ascii_token_is_401_not_500(self):
        """`compare_digest` với str non-ASCII ném TypeError → phải encode trước."""
        self.assertEqual(APIClient().get(URL, HTTP_X_API_TOKEN="tôken").status_code, 401)


class DailyReportUnconfiguredTests(TestCase):
    @override_settings(DAILY_REPORT_API_TOKEN="", IS_PRODUCTION=False)
    def test_dev_without_token_is_open(self):
        self.assertIn(APIClient().get(URL).status_code, (200, 503))


class DailyReportAllEndpointsAuthTests(TestCase):
    """Mọi route trong urls.py phải qua cùng cửa xác thực — thêm endpoint mới mà
    quên bọc @daily_report_view thì test này đỏ thay vì âm thầm lộ số liệu."""

    @staticmethod
    def _routes() -> list[tuple[str, str]]:
        return [(p.name, reverse(p.name)) for p in daily_report_urls.urlpatterns]

    @override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
    def test_every_route_rejects_missing_token(self):
        for name, url in self._routes():
            with self.subTest(route=name):
                self.assertEqual(APIClient().get(url).status_code, 401)

    @override_settings(DAILY_REPORT_API_TOKEN="", IS_PRODUCTION=True)
    def test_every_route_fails_closed_in_production_without_token(self):
        for name, url in self._routes():
            with self.subTest(route=name):
                self.assertEqual(APIClient().get(url).status_code, 503)


@override_settings(DAILY_REPORT_API_TOKEN=TOKEN)
class DailyReportThrottleTests(TestCase):
    def test_scope_rate_is_enforced(self):
        from django.conf import settings

        rates = dict(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
        self.assertIn("daily_report", rates)
        rates["daily_report"] = "2/hour"
        # IP riêng cho test: bộ đếm nằm trong Redis thật, dùng chung giữa các lần chạy.
        import random

        ip = f"10.77.{random.randint(0, 255)}.{random.randint(1, 254)}"
        with override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": rates}):
            c = APIClient()
            codes = [
                c.get(URL, HTTP_X_API_TOKEN=TOKEN, REMOTE_ADDR=ip).status_code for _ in range(3)
            ]
        self.assertEqual(codes[2], 429, codes)
