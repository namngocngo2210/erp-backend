"""Endpoint snapshot tests cho api_exception_handler (audit 2026-07 mục 4.8).

Đặt ở apps/users vì dùng endpoint + helper của users — tránh tạo phụ thuộc
apps.common → apps.users trong contract import-linter (mục 4.3).
Unit tests của handler nằm ở apps/common/tests/test_exception_handler.py.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.rbac_catalog import sync_permissions
from apps.users.tests.helpers import make_admin, make_user_with_permissions


class ExceptionHandlerEndpointTests(TestCase):
    """Xác nhận handler đã đăng ký vào REST_FRAMEWORK qua endpoint thật."""

    def setUp(self):
        self.client = APIClient()
        sync_permissions()
        self.admin = make_admin("exc_admin")

    def test_validation_400_via_endpoint(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post("/api/users/departments", {}, format="json")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("name", body)  # format cũ của serializer errors
        self.assertEqual(body["error"]["code"], "validation_error")

    def test_permission_403_via_endpoint(self):
        staff = make_user_with_permissions("exc_staff", ["orders:view"])
        self.client.force_authenticate(user=staff)
        resp = self.client.get("/api/users/departments")
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertIn("detail", body)
        self.assertEqual(body["error"]["code"], "permission_denied")

    def test_not_found_404_via_endpoint(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch("/api/users/departments/999999", {}, format="json")
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertIn("detail", body)
        self.assertEqual(body["error"]["code"], "http404")
