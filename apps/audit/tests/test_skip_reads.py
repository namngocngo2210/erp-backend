"""Middleware bỏ ghi audit cho request ĐỌC thành công (audit 2026-09-23 T1 §2.10).

GET 2xx và POST-list 2xx không ghi; đường đọc nhạy cảm, ghi/sửa/xoá, và mọi
response ngoài 2xx vẫn ghi như cũ. AUDIT_LOG_SKIP_READS=0 = ghi tất cả.
"""
import json
from unittest import mock

from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from apps.audit.middleware_impl import AuditLogMiddleware


class SkipReadsTests(TestCase):
    def _run(self, method: str, path: str, status: int = 200) -> bool:
        """True nếu middleware GHI audit cho request này."""
        middleware = AuditLogMiddleware(lambda request: None)
        factory = RequestFactory()
        if method == "GET":
            request = factory.get(path)
        else:
            request = factory.post(path, data=json.dumps({"limit": 20}), content_type="application/json")
        request.user = mock.Mock(is_authenticated=False)
        middleware.process_request(request)
        response = HttpResponse('{"ok": true}', content_type="application/json", status=status)
        with mock.patch.object(AuditLogMiddleware, "_write_audit_log") as write:
            middleware.process_response(request, response)
        return write.called

    def test_get_2xx_not_logged(self):
        self.assertFalse(self._run("GET", "/api/orders/123"))
        self.assertFalse(self._run("GET", "/api/customers/"))
        self.assertFalse(self._run("GET", "/api/orders/dashboard/summary"))

    def test_post_list_2xx_not_logged(self):
        for path in ("/api/orders", "/api/orders/", "/api/orders/landing", "/api/orders/sheet3", "/api/orders/shipments"):
            self.assertFalse(self._run("POST", path), path)

    def test_post_non_list_logged(self):
        self.assertTrue(self._run("POST", "/api/orders/123/assign"))
        self.assertTrue(self._run("POST", "/api/orders/transfer-landing"))

    def test_sensitive_reads_still_logged(self):
        for path in (
            "/api/orders/123/customer-phone",
            "/api/customers/5/customer-phone",
            "/api/orders/123/invoice",
            "/api/orders/print?ids=1",
            "/api/exports/abc/download",
            "/api/audit-logs/",
            "/admin/users/user/",
            "/api/users/",
        ):
            self.assertTrue(self._run("GET", path), path)

    def test_non_2xx_reads_logged(self):
        self.assertTrue(self._run("GET", "/api/orders/123", status=403))
        self.assertTrue(self._run("POST", "/api/orders", status=400))

    @override_settings(AUDIT_LOG_SKIP_READS=False)
    def test_flag_off_restores_old_behaviour(self):
        self.assertTrue(self._run("GET", "/api/orders/123"))
        self.assertTrue(self._run("POST", "/api/orders"))
