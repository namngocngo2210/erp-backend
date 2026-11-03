"""Guard cho /api/orders/metrics (audit 2026-07-26 C3).

Endpoint buộc phải AllowAny (scraper Prometheus không mang JWT) nên lớp chặn chính
là nginx theo dải IP. Nhưng repo có HAI file nginx và trước đợt này chỉ file của host
mới chặn — môi trường nào dùng `nginx/conf.d/default.conf` thì metrics phơi ra
internet. `METRICS_ALLOW_TOKEN` là lớp hai để bảo mật không phụ thuộc vào việc chọn
đúng file nginx.
"""

from django.test import TestCase, override_settings
from django.urls import reverse


class MetricsEndpointGuardTests(TestCase):
    def setUp(self):
        self.url = reverse("orders-metrics")

    @override_settings(METRICS_ALLOW_TOKEN="")
    def test_no_token_configured_keeps_endpoint_open(self):
        """Mặc định trống = giữ nguyên hành vi cũ, scraper nội bộ không phải sửa gì."""
        response = self.client.get(self.url)
        # 200 khi prometheus_client có sẵn, 503 khi chưa cài — cả hai đều nghĩa là
        # request đã đi QUA guard, đó mới là thứ test này quan tâm.
        self.assertIn(response.status_code, (200, 503))

    @override_settings(METRICS_ALLOW_TOKEN="s3cret-token")
    def test_configured_token_rejects_request_without_header(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    @override_settings(METRICS_ALLOW_TOKEN="s3cret-token")
    def test_configured_token_rejects_wrong_header(self):
        response = self.client.get(self.url, headers={"x-metrics-token": "wrong"})
        self.assertEqual(response.status_code, 403)

    @override_settings(METRICS_ALLOW_TOKEN="s3cret-token")
    def test_configured_token_accepts_correct_header(self):
        response = self.client.get(self.url, headers={"x-metrics-token": "s3cret-token"})
        self.assertIn(response.status_code, (200, 503))
        self.assertNotEqual(response.status_code, 403)
