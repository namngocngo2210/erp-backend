"""Audit middleware phải giữ được request body của POST/PUT/PATCH JSON.

Bug thực tế: middleware chỉ chạm ``request.body`` ở ``process_response`` — tức là
SAU khi DRF đã đọc stream qua ``request.data``. Django không cache ``_body`` trong
trường hợp đó nên ``request.body`` ném ``RawPostDataException``; middleware nuốt
exception ở mức debug và ghi curl KHÔNG có ``-d``. Hệ quả: mọi PATCH đơn hàng
trong audit_logs đều rỗng ruột, không truy vết được ai đổi field gì.

Test dưới đây mô phỏng đúng thứ tự thật (process_request → DRF đọc data →
process_response), khác các test cũ vốn gọi thẳng middleware nên không tái hiện được.
"""
import json
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.request import Request as DrfRequest

from apps.audit.middleware_impl import AuditLogMiddleware


class AuditRequestBodyCaptureTests(SimpleTestCase):
    """PATCH JSON: body phải còn nguyên trong curl + metadata sau khi DRF đọc stream."""

    def _run_json_patch(self, payload: dict) -> dict:
        middleware = AuditLogMiddleware(lambda request: None)
        request = RequestFactory().patch(
            "/api/orders/123",
            data=json.dumps(payload),
            content_type="application/json",
        )
        request.user = mock.Mock(is_authenticated=False)
        middleware.process_request(request)

        # View DRF đọc body qua request.data → stream bị consume trước process_response.
        drf_request = DrfRequest(request, parsers=[JSONParser()])
        self.assertEqual(drf_request.data, payload)

        response = HttpResponse('{"id": 123}', content_type="application/json", status=200)
        captured: dict = {}
        with mock.patch.object(
            AuditLogMiddleware,
            "_write_audit_log",
            side_effect=lambda **kw: captured.update(kw),
        ):
            middleware.process_response(request, response)
        return captured

    def test_curl_keeps_body_after_drf_consumed_stream(self):
        captured = self._run_json_patch({"staff_id": 42})

        self.assertIn("-d '", captured["curl_command"])
        self.assertIn('"staff_id": 42', captured["curl_command"])

    def test_metadata_request_size_after_drf_consumed_stream(self):
        captured = self._run_json_patch({"staff_id": 42})

        self.assertGreater(captured["metadata"]["request_size_bytes"], 0)

    def test_sensitive_fields_still_redacted_in_body(self):
        captured = self._run_json_patch({"staff_id": 42, "password": "hunter2"})

        self.assertNotIn("hunter2", captured["curl_command"])
        self.assertIn("***REDACTED***", captured["curl_command"])


class AuditRequestBodyLimitTests(SimpleTestCase):
    """Không được buffer multipart (upload ảnh) và payload vượt ngưỡng."""

    def test_multipart_upload_still_parses_after_middleware(self):
        middleware = AuditLogMiddleware(lambda request: None)
        upload = SimpleUploadedFile("check-in.jpg", b"\xff\xd8\xff-fake-jpeg", "image/jpeg")
        request = RequestFactory().post("/api/hr/attendance", data={"photo": upload})
        request.user = mock.Mock(is_authenticated=False)

        middleware.process_request(request)

        # Stream chưa bị đọc → DRF vẫn parse được file upload như thường.
        drf_request = DrfRequest(request, parsers=[MultiPartParser()])
        self.assertIn("photo", drf_request.FILES)
        self.assertEqual(getattr(request, "audit_body", b""), b"")

    def test_oversized_json_body_is_not_buffered(self):
        middleware = AuditLogMiddleware(lambda request: None)
        big = json.dumps({"note": "x" * (2 * 1024 * 1024)})
        request = RequestFactory().patch(
            "/api/orders/123", data=big, content_type="application/json"
        )
        request.user = mock.Mock(is_authenticated=False)

        middleware.process_request(request)

        self.assertEqual(getattr(request, "audit_body", b""), b"")


# Các lớp dưới kiểm tra NỘI DUNG dòng audit (curl/metadata/redaction) qua request GET
# 2xx. Từ audit 2026-09-23 T1 §2.10, mặc định middleware KHÔNG ghi GET/POST-list 2xx
# (AUDIT_LOG_SKIP_READS) — tắt cờ ở đây để vẫn có dòng mà soi; hành vi bỏ qua đọc
# có test riêng ở test_skip_reads.py.
@override_settings(AUDIT_LOG_SKIP_READS=False)
class CurlHeaderWhitelistTests(SimpleTestCase):
    """`curl_command` chỉ giữ header ảnh hưởng hành vi API, không chép cả header trình duyệt.

    Đo trên prod 2026-08-17: `curl_command` trung bình 756 byte/dòng, ~1.6GB trên
    tổng 3.4GB heap của `audit_logs` — phần lớn là `User-Agent`/`Sec-Ch-Ua-*`/
    `Accept-*` của trình duyệt, lặp lại y hệt trên hàng triệu dòng và không dùng
    để tái hiện request. `User-Agent` và `Referer` vẫn được giữ ở `metadata`
    (jsonb, query được), nên đây là bỏ TRÙNG LẶP chứ không mất thông tin.
    """

    def _run_get(self, **extra) -> dict:
        middleware = AuditLogMiddleware(lambda request: None)
        request = RequestFactory().get("/api/orders/", **extra)
        request.user = mock.Mock(is_authenticated=False)
        middleware.process_request(request)

        response = HttpResponse("{}", content_type="application/json", status=200)
        captured: dict = {}
        with mock.patch.object(
            AuditLogMiddleware,
            "_write_audit_log",
            side_effect=lambda **kw: captured.update(kw),
        ):
            middleware.process_response(request, response)
        return captured

    def test_curl_drops_user_agent_header(self):
        captured = self._run_get(HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

        self.assertNotIn("Mozilla/5.0", captured["curl_command"])
        self.assertNotIn("User-Agent", captured["curl_command"])

    def test_curl_drops_browser_hint_and_language_headers(self):
        captured = self._run_get(
            HTTP_ACCEPT_LANGUAGE="vi-VN,vi;q=0.9,en-US;q=0.8",
            HTTP_SEC_CH_UA='"Chromium";v="128", "Not;A=Brand";v="24"',
            HTTP_REFERER="https://app.example.com/orders?page=3",
        )

        self.assertNotIn("vi-VN", captured["curl_command"])
        self.assertNotIn("Chromium", captured["curl_command"])
        self.assertNotIn("app.example.com", captured["curl_command"])

    def test_curl_keeps_headers_that_change_api_behaviour(self):
        captured = self._run_get(HTTP_ACCEPT="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        self.assertIn("-H 'Accept: application/json'", captured["curl_command"])
        self.assertIn("-H 'X-Requested-With: XMLHttpRequest'", captured["curl_command"])

    def test_metadata_still_records_user_agent_and_referer(self):
        captured = self._run_get(
            HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            HTTP_REFERER="https://app.example.com/orders?page=3",
        )

        self.assertEqual(
            captured["metadata"]["user_agent"], "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        )
        self.assertEqual(captured["metadata"]["referer"], "https://app.example.com/orders?page=3")

    def test_curl_still_reproduces_method_and_url(self):
        captured = self._run_get(HTTP_USER_AGENT="Mozilla/5.0")

        self.assertIn("curl -X GET", captured["curl_command"])
        self.assertIn("/api/orders/", captured["curl_command"])


@override_settings(AUDIT_LOG_SKIP_READS=False)
class QueryParamRedactionTests(SimpleTestCase):
    """Redact query_params trong audit log (task 2.3).

    Trước fix: `_build_metadata` lưu `request.GET.lists()` NGUYÊN VĂN — token/mật khẩu
    truyền qua query string lộ thẳng vào audit_logs. Fix chạy query_params qua cùng bộ
    lọc `_redact_sensitive` dùng cho curl body; riêng SĐT (`phone`) mask giữ 3 số cuối
    (không che trắng) vì vẫn cần đối soát được.
    """

    def _run(self, path: str) -> dict:
        middleware = AuditLogMiddleware(lambda request: None)
        factory = RequestFactory()
        request = factory.get(path)
        request.user = mock.Mock(is_authenticated=False)
        middleware.process_request(request)

        response = HttpResponse("{}", content_type="application/json", status=200)
        captured = {}
        with mock.patch.object(
            AuditLogMiddleware, "_write_audit_log",
            side_effect=lambda **kw: captured.update(kw),
        ):
            middleware.process_response(request, response)
        return captured

    CASES = (
        ("phone giữ 3 số cuối", "phone=0912345678", {"phone": ["*******678"]}),
        ("phone ngắn che hết", "phone=12", {"phone": ["**"]}),
        ("token che trắng", "token=abc123secretvalue", {"token": ["***REDACTED***"]}),
        ("password che trắng", "password=hunter2", {"password": ["***REDACTED***"]}),
        ("param thường giữ nguyên", "status=1&q=abc", {"status": ["1"], "q": ["abc"]}),
    )

    def test_query_params_redacted_by_key(self):
        for label, query, expected in self.CASES:
            with self.subTest(label, query=query):
                captured = self._run(f"/api/orders/?{query}")
                assert captured["metadata"]["query_params"] == expected
