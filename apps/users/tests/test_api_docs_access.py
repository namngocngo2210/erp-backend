"""Tests gate Swagger/OpenAPI docs (/api/docs, /api/schema).

Ngoài production docs mở tự do; trên production CHỈ superuser xem được
(session Django set lúc login ERP, hoặc JWT Bearer), kể cả staff thường cũng
nhận 404 — xem config/docs_access.py. Đặt ở apps/users vì dùng model User +
login endpoint (tránh phụ thuộc apps.common → apps.users theo import-linter).
"""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.users.jwt import VersionedRefreshToken
from apps.users.models import User

# Các URL `/api/v2/*` (django-ninja) từng nằm trong danh sách; gỡ 2026-08-20 cùng
# với django-ninja (audit 2026-08-19 mục 4.12 — strangler không tiến triển).
DOCS_URLS = ("/api/docs", "/api/schema")


class DocsAccessDevTests(TestCase):
    """Ngoài production (mặc định trong test) docs mở như trước."""

    def test_docs_open_for_anonymous(self):
        client = APIClient()
        for url in DOCS_URLS:
            self.assertEqual(client.get(url).status_code, 200, url)


@override_settings(IS_PRODUCTION=True)
class DocsAccessProductionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_user(
            username="docs_super", password="x", is_superuser=True
        )
        cls.staff = User.objects.create_user(username="docs_staff", password="x", is_staff=True)
        cls.normal = User.objects.create_user(username="docs_normal", password="x")

    def test_anonymous_gets_404(self):
        client = APIClient()
        for url in DOCS_URLS:
            self.assertEqual(client.get(url).status_code, 404, url)

    def test_non_superuser_session_gets_404(self):
        for user in (self.staff, self.normal):
            client = APIClient()
            client.force_login(user)
            for url in DOCS_URLS:
                self.assertEqual(client.get(url).status_code, 404, f"{user.username} {url}")

    def test_superuser_session_gets_200(self):
        client = APIClient()
        client.force_login(self.superuser)
        for url in DOCS_URLS:
            self.assertEqual(client.get(url).status_code, 200, url)

    def test_superuser_jwt_bearer_gets_200(self):
        token = str(VersionedRefreshToken.for_user(self.superuser).access_token)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(client.get("/api/schema").status_code, 200)

    def test_staff_jwt_bearer_gets_404(self):
        token = str(VersionedRefreshToken.for_user(self.staff).access_token)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(client.get("/api/schema").status_code, 404)


class LoginSessionCookieTests(TestCase):
    """Login ERP set session Django cho superuser (phục vụ docs gate)."""

    def test_superuser_login_sets_session_cookie(self):
        User.objects.create_user(username="sess_super", password="pw-docs-1", is_superuser=True)
        client = APIClient()
        resp = client.post(
            "/api/users/login",
            {"username": "sess_super", "password": "pw-docs-1"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("sessionid", resp.cookies)

    def test_staff_login_does_not_set_session_cookie(self):
        User.objects.create_user(username="sess_staff", password="pw-docs-2", is_staff=True)
        client = APIClient()
        resp = client.post(
            "/api/users/login",
            {"username": "sess_staff", "password": "pw-docs-2"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("sessionid", resp.cookies)

    def test_normal_login_does_not_set_session_cookie(self):
        User.objects.create_user(username="sess_normal", password="pw-docs-3")
        client = APIClient()
        resp = client.post(
            "/api/users/login",
            {"username": "sess_normal", "password": "pw-docs-3"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("sessionid", resp.cookies)
