"""Snapshot tests cho api_exception_handler (audit 2026-07 mục 4.8).

Bảo đảm format lỗi CŨ giữ nguyên từng key — handler chỉ BỔ SUNG key ``error``
(khi body là dict) và không đụng body dạng list.
"""

from django.test import SimpleTestCase
from rest_framework.exceptions import (
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.common.exceptions import api_exception_handler


def _context():
    factory = APIRequestFactory()
    return {"view": APIView(), "request": factory.get("/")}


class ExceptionHandlerUnitTests(SimpleTestCase):
    def test_validation_error_dict_body_keeps_old_keys_and_adds_error(self):
        exc = ValidationError({"name": ["Trường này là bắt buộc."]})
        response = api_exception_handler(exc, _context())
        self.assertEqual(response.status_code, 400)
        # Key cũ giữ nguyên y hệt.
        self.assertEqual(response.data["name"], ["Trường này là bắt buộc."])
        # Key mới bổ sung, đúng format.
        self.assertEqual(
            response.data["error"],
            {"code": "validation_error", "detail": "Trường này là bắt buộc."},
        )
        self.assertEqual(set(response.data.keys()), {"name", "error"})

    def test_validation_error_list_body_unchanged(self):
        exc = ValidationError(["Sai dữ liệu."])
        response = api_exception_handler(exc, _context())
        self.assertEqual(response.status_code, 400)
        # Body list giữ nguyên — KHÔNG chèn error.
        self.assertEqual(list(response.data), ["Sai dữ liệu."])

    def test_permission_denied_403(self):
        response = api_exception_handler(PermissionDenied(), _context())
        self.assertEqual(response.status_code, 403)
        self.assertIn("detail", response.data)
        self.assertEqual(response.data["error"]["code"], "permission_denied")
        self.assertEqual(response.data["error"]["detail"], str(response.data["detail"]))
        self.assertEqual(set(response.data.keys()), {"detail", "error"})

    def test_not_found_404(self):
        response = api_exception_handler(NotFound(), _context())
        self.assertEqual(response.status_code, 404)
        self.assertIn("detail", response.data)
        self.assertEqual(response.data["error"]["code"], "not_found")
        self.assertEqual(set(response.data.keys()), {"detail", "error"})

    def test_unhandled_exception_returns_none(self):
        # DRF không xử lý → handler log rồi trả None (Django giữ hành vi 500 cũ).
        with self.assertLogs("apps.common.exceptions", level="ERROR") as captured:
            result = api_exception_handler(RuntimeError("boom"), _context())
        self.assertIsNone(result)
        self.assertIn("request_id=", captured.output[0])
