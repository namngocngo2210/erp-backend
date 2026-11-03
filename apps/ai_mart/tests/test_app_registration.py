"""App phải được đăng ký và migration 0001 phải tạo đủ 3 schema.

Test schema chạy trên test DB — migration chạy trong quá trình dựng DB test,
nên chỉ cần soi pg_namespace, không cần gọi migrate thủ công.
"""

from django.apps import apps as django_apps
from django.db import connection
from django.test import TestCase


class AiMartRegistrationTests(TestCase):
    def test_app_installed(self):
        self.assertTrue(django_apps.is_installed("apps.ai_mart"))

    def test_schemas_exist(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT nspname FROM pg_namespace"
                " WHERE nspname IN ('ai', 'ai_stg', 'ai_meta')"
            )
            names = {row[0] for row in cur.fetchall()}
        self.assertEqual(names, {"ai", "ai_stg", "ai_meta"})
