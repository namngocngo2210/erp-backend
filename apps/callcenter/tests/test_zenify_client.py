"""Zenify token plumbing: services.sign_in (unit, no DB) + webphone token API.

Gộp từ test_services.py và test_webphone_token_api.py (tái cấu trúc test
2026-08-05). Hai file nguồn khai báo hai dict ZENIFY_ENV KHÔNG giống hệt nhau
(bên sign-in có ZENIFY_API_BASE_URL, bên token API có ZENIFY_AGENT_EMAIL="") —
giữ nguyên hành vi override_settings của từng class bằng phần chung + 2 biến thể.
"""
import pytest
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.callcenter import services
from apps.users.tests.helpers import make_user_with_sections

_ZENIFY_ENV_COMMON = {
    "ZENIFY_DOMAIN": "desi",
    "ZENIFY_API_KEY": "key",
    "ZENIFY_API_SECRET": "secret",
    "ZENIFY_TOKEN_TTL_SECONDS": 259200,
}

# ZENIFY_ENV của test_services.py cũ
ZENIFY_SIGNIN_ENV = {
    **_ZENIFY_ENV_COMMON,
    "ZENIFY_API_BASE_URL": "https://api.zenify.vn",
}

# ZENIFY_ENV của test_webphone_token_api.py cũ
ZENIFY_TOKEN_ENV = {
    **_ZENIFY_ENV_COMMON,
    "ZENIFY_AGENT_EMAIL": "",
}

URL = "/api/callcenter/token"

FAKE_DATA = {
    "token": "jwt-token",
    "tenant_id": "t1",
    "user_id": "u1",
    "ttl": 259200,
    "info": {"name": "Agent Demo"},
}


def _resp(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is None:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = json_data
    return resp


@override_settings(**ZENIFY_SIGNIN_ENV)
class ZenifySignInTests(SimpleTestCase):
    def test_sign_in_success_sends_credentials_headers(self):
        ok = {"error": False, "status": 200, "data": {"token": "jwt", "ttl": 259200}}
        with patch("apps.callcenter.services.HttpClient") as mock_client_cls:
            mock_client_cls.return_value.post.return_value = _resp(200, ok)
            data = services.sign_in("a@b.vn")
        self.assertEqual(data["token"], "jwt")
        _, kwargs = mock_client_cls.return_value.post.call_args
        self.assertEqual(kwargs["headers"]["domain"], "desi")
        self.assertEqual(kwargs["headers"]["api-key"], "key")
        self.assertEqual(kwargs["headers"]["api-secret"], "secret")
        self.assertEqual(kwargs["json"], {"email": "a@b.vn", "ttl_in_second": 259200})

    def test_sign_in_error_flag_raises_with_vendor_message(self):
        bad = {"error": True, "status": 401, "message": "invalid api key"}
        with patch("apps.callcenter.services.HttpClient") as mock_client_cls:
            mock_client_cls.return_value.post.return_value = _resp(200, bad)
            with self.assertRaises(services.ZenifyError) as ctx:
                services.sign_in("a@b.vn")
        self.assertIn("invalid api key", str(ctx.exception))

    def test_sign_in_http_error_raises(self):
        with patch("apps.callcenter.services.HttpClient") as mock_client_cls:
            mock_client_cls.return_value.post.return_value = _resp(500, None, text="boom")
            with self.assertRaises(services.ZenifyError):
                services.sign_in("a@b.vn")

    def test_sign_in_missing_token_raises(self):
        no_token = {"error": False, "status": 200, "data": {"ttl": 1}}
        with patch("apps.callcenter.services.HttpClient") as mock_client_cls:
            mock_client_cls.return_value.post.return_value = _resp(200, no_token)
            with self.assertRaises(services.ZenifyError):
                services.sign_in("a@b.vn")

    def test_sign_in_network_error_raises(self):
        with patch("apps.callcenter.services.HttpClient") as mock_client_cls:
            mock_client_cls.return_value.post.side_effect = requests.ConnectionError("down")
            with self.assertRaises(services.ZenifyError) as ctx:
                services.sign_in("a@b.vn")
        self.assertIn("Không kết nối được Zenify", str(ctx.exception))

    def test_resolve_agent_email_fallback_to_user_email(self):
        user = MagicMock(email=" user@desi.vn ", zenify_email="")
        with override_settings(ZENIFY_AGENT_EMAIL=""):
            self.assertEqual(services.resolve_agent_email(user), "user@desi.vn")
        with override_settings(ZENIFY_AGENT_EMAIL="shared@desi.vn"):
            self.assertEqual(services.resolve_agent_email(user), "shared@desi.vn")

    def test_resolve_agent_email_prefers_user_zenify_email(self):
        # Email Zenify admin nhập cho từng user thắng cả env dùng chung lẫn email ERP.
        user = MagicMock(email="user@desi.vn", zenify_email=" agent1@zenify.vn ")
        with override_settings(ZENIFY_AGENT_EMAIL=""):
            self.assertEqual(services.resolve_agent_email(user), "agent1@zenify.vn")
        with override_settings(ZENIFY_AGENT_EMAIL="shared@desi.vn"):
            self.assertEqual(services.resolve_agent_email(user), "agent1@zenify.vn")


# Token cache: LocMemCache riêng từng test (backend/conftest.py).
@pytest.mark.usefixtures("isolated_cache")
class WebphoneTokenApiTests(TestCase):
    def setUp(self):
        self.user = make_user_with_sections("sale1", ["landing-orders"])
        self.user.email = "sale1@desi.vn"
        self.user.save(update_fields=["email"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_requires_auth(self):
        resp = APIClient().post(URL)
        self.assertEqual(resp.status_code, 401)

    def test_requires_landing_orders_permission(self):
        other = make_user_with_sections("kho1", ["inventory"])
        client = APIClient()
        client.force_authenticate(other)
        resp = client.post(URL)
        self.assertEqual(resp.status_code, 403)

    def test_503_when_not_configured(self):
        resp = self.client.post(URL)
        self.assertEqual(resp.status_code, 503)
        self.assertIn("Zenify", resp.data["detail"])

    @override_settings(**ZENIFY_TOKEN_ENV)
    def test_returns_token_using_user_email(self):
        with patch("apps.callcenter.services.sign_in", return_value=dict(FAKE_DATA)) as mock_sign_in:
            resp = self.client.post(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["token"], "jwt-token")
        self.assertEqual(resp.data["email"], "sale1@desi.vn")
        self.assertEqual(resp.data["agent_name"], "Agent Demo")
        mock_sign_in.assert_called_once_with("sale1@desi.vn")

    @override_settings(**{**ZENIFY_TOKEN_ENV, "ZENIFY_AGENT_EMAIL": "shared@desi.vn"})
    def test_env_agent_email_takes_precedence(self):
        with patch("apps.callcenter.services.sign_in", return_value=dict(FAKE_DATA)) as mock_sign_in:
            resp = self.client.post(URL)
        self.assertEqual(resp.status_code, 200)
        mock_sign_in.assert_called_once_with("shared@desi.vn")

    @override_settings(**ZENIFY_TOKEN_ENV)
    def test_400_when_user_has_no_email(self):
        self.user.email = ""
        self.user.save(update_fields=["email"])
        resp = self.client.post(URL)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Email Zenify", resp.data["detail"])

    @override_settings(**ZENIFY_TOKEN_ENV)
    def test_uses_user_zenify_email_when_set(self):
        self.user.email = ""
        self.user.zenify_email = "sale1@zenify.vn"
        self.user.save(update_fields=["email", "zenify_email"])
        with patch("apps.callcenter.services.sign_in", return_value=dict(FAKE_DATA)) as mock_sign_in:
            resp = self.client.post(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["email"], "sale1@zenify.vn")
        mock_sign_in.assert_called_once_with("sale1@zenify.vn")

    @override_settings(**ZENIFY_TOKEN_ENV)
    def test_token_cached_between_calls(self):
        with patch("apps.callcenter.services.sign_in", return_value=dict(FAKE_DATA)) as mock_sign_in:
            first = self.client.post(URL)
            second = self.client.post(URL)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        mock_sign_in.assert_called_once()

    @override_settings(**ZENIFY_TOKEN_ENV)
    def test_refresh_bypasses_cache(self):
        with patch("apps.callcenter.services.sign_in", return_value=dict(FAKE_DATA)) as mock_sign_in:
            self.client.post(URL)
            self.client.post(URL + "?refresh=1")
        self.assertEqual(mock_sign_in.call_count, 2)

    @override_settings(**ZENIFY_TOKEN_ENV)
    def test_zenify_error_passthrough(self):
        err = services.ZenifyError("Zenify sign-in thất bại: sai api-key")
        with patch("apps.callcenter.services.sign_in", side_effect=err):
            resp = self.client.post(URL)
        self.assertEqual(resp.status_code, 502)
        self.assertIn("sai api-key", resp.data["detail"])
