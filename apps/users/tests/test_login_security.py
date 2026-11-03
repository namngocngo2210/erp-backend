"""Chống brute-force đăng nhập theo tài khoản (audit 2026-09-23 M5).

Ba lớp: throttle IP (có sẵn), throttle (username, IP) `LoginUserThrottle`, và
khoá tạm theo username `apps.users.login_lockout`. Test bơm LocMemCache riêng vào
cả throttle lẫn lockout — KHÔNG `cache.clear()` Redis dùng chung (conftest cảnh
báo), và không phụ thuộc rate thật của settings (patch THROTTLE_RATES).
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

from django.contrib.auth.password_validation import validate_password
from django.core.cache.backends.locmem import LocMemCache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.users import login_lockout
from apps.users.models import User
from apps.users.transport.views.users_views_impl.auth_views import (
    LoginRateThrottle,
    LoginUserThrottle,
)

LOGIN_URL = "/api/users/login"
PASSWORD = "Correct-Horse-Battery-9"


class _LoginSecurityBase(TestCase):
    ip_rate = "1000/min"
    user_rate = "1000/min"

    def setUp(self):
        self.user = User.objects.create_user(username="lock_me", password=PASSWORD)
        self.client = APIClient()
        stack = ExitStack()
        self.addCleanup(stack.close)
        # Mỗi test một LocMem riêng (tên khác nhau — cùng tên là dùng chung dict).
        throttle_cache = LocMemCache(f"login-throttle-{self.id()}", {})
        lockout_cache = LocMemCache(f"login-lockout-{self.id()}", {})
        self.lockout_cache = lockout_cache
        stack.enter_context(patch.object(LoginRateThrottle, "cache", throttle_cache))
        stack.enter_context(patch.object(LoginUserThrottle, "cache", throttle_cache))
        stack.enter_context(patch.object(login_lockout, "cache", lockout_cache))
        rates = {"login": self.ip_rate, "login_user": self.user_rate}
        stack.enter_context(patch.object(LoginRateThrottle, "THROTTLE_RATES", rates))
        stack.enter_context(patch.object(LoginUserThrottle, "THROTTLE_RATES", rates))

    def _login(self, username="lock_me", password="wrong-password-x", ip="203.0.113.7"):
        return self.client.post(
            LOGIN_URL,
            {"username": username, "password": password},
            format="json",
            HTTP_CF_CONNECTING_IP=ip,
        )


@override_settings(LOGIN_LOCKOUT_THRESHOLD=100)
class LoginUserThrottleTests(_LoginSecurityBase):
    user_rate = "5/min"

    def test_sixth_attempt_for_same_username_is_throttled(self):
        for _ in range(5):
            self.assertEqual(self._login().status_code, 400)
        resp = self._login()
        self.assertEqual(resp.status_code, 429, resp.content)
        self.assertIn("Retry-After", resp.headers)

    def test_other_username_from_same_ip_not_affected(self):
        for _ in range(6):
            self._login()
        self.assertEqual(self._login(username="someone_else").status_code, 400)

    def test_same_username_from_other_ip_has_its_own_budget(self):
        for _ in range(6):
            self._login()
        self.assertEqual(self._login(ip="198.51.100.9").status_code, 400)

    def test_username_key_is_case_and_space_insensitive(self):
        for _ in range(5):
            self._login(username="Lock_Me ")
        self.assertEqual(self._login(username="lock_me").status_code, 429)

    def test_missing_username_skips_user_throttle(self):
        throttle = LoginUserThrottle()
        request = self.client.post(LOGIN_URL, {}, format="json").wsgi_request
        request.data = {}
        self.assertIsNone(throttle.get_cache_key(request, None))


@override_settings(LOGIN_LOCKOUT_THRESHOLD=3, LOGIN_LOCKOUT_SECONDS=900)
class LoginLockoutTests(_LoginSecurityBase):
    def test_locked_after_threshold_even_with_correct_password(self):
        for _ in range(3):
            self.assertEqual(self._login().status_code, 400)
        resp = self._login(password=PASSWORD)
        self.assertEqual(resp.status_code, 429, resp.content)
        self.assertIn("Retry-After", resp.headers)
        self.assertIn("khoá", resp.json()["detail"])
        # Đổi IP không thoát khoá — khoá gắn với username.
        self.assertEqual(self._login(password=PASSWORD, ip="198.51.100.9").status_code, 429)

    def test_unlock_after_window_allows_login(self):
        for _ in range(3):
            self._login()
        self.assertTrue(login_lockout.is_locked("lock_me"))
        self.lockout_cache.clear()  # mô phỏng hết TTL 15 phút
        self.assertEqual(self._login(password=PASSWORD).status_code, 200)

    def test_successful_login_resets_failure_counter(self):
        self._login()
        self._login()
        self.assertEqual(self._login(password=PASSWORD).status_code, 200)
        # Bộ đếm về 0: cần lại đủ 3 lần sai mới khoá.
        self._login()
        self._login()
        self.assertEqual(self._login(password=PASSWORD).status_code, 200)
        self._login()
        self._login()
        self._login()
        self.assertEqual(self._login(password=PASSWORD).status_code, 429)

    def test_other_username_is_not_locked(self):
        for _ in range(3):
            self._login()
        self.assertFalse(login_lockout.is_locked("someone_else"))
        self.assertEqual(self._login(username="someone_else").status_code, 400)

    def test_empty_username_never_locks(self):
        self.assertEqual(login_lockout.record_failure(""), 0)
        self.assertFalse(login_lockout.is_locked(""))

    def test_fail_open_when_cache_is_down(self):
        class _Down:
            def get(self, *a, **k):
                raise ConnectionError("redis down")

            add = incr = set = delete = get

        with patch.object(login_lockout, "cache", _Down()):
            self.assertFalse(login_lockout.is_locked("lock_me"))
            self.assertEqual(login_lockout.record_failure("lock_me"), 0)
            login_lockout.reset("lock_me")  # không raise


class PasswordPolicyTests(TestCase):
    def test_minimum_length_is_twelve(self):
        with self.assertRaises(ValidationError):
            validate_password("Ab3$efghijk")  # 11 ký tự
        validate_password("Ab3$efghijkl")  # 12 ký tự — không raise
