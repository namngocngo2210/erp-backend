"""Xác thực CNV: SSO client + token provider.

Gộp từ test_sso_client.py + test_token_provider.py (tái cấu trúc 2026-08-05).
Docstring gốc của từng file giữ ở comment section tương ứng.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.utils import timezone

from apps.cnv_loyalty.contracts.dtos import TokenBundle
from apps.cnv_loyalty.exceptions import CnvAuthError
from apps.cnv_loyalty.infrastructure import token_provider
from apps.cnv_loyalty.infrastructure.sso_client import SSO_SCOPE, CnvSsoClient
from apps.cnv_loyalty.models import CnvConnection
from apps.sysconfig import override_sysconfig, sysconfig

# ---------------------------------------------------------------------------
# SSO client: authorize URL + parse token response (HTTP fake qua HttpClient stub).
# (Không chạm DB — không mang mark django_db, giữ nguyên hành vi file gốc.)
# ---------------------------------------------------------------------------


def _connection():
    return CnvConnection(client_id="client-1", client_secret="secret-1")


def test_authorize_url_contains_required_params():
    with override_sysconfig(CNV_SSO_REDIRECT_URI="https://crm.example.com/api/cnv/callback"):
        url = CnvSsoClient(_connection(), http=MagicMock()).build_authorize_url("state-abc")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert url.startswith(f"{sysconfig.get('CNV_SSO_SERVER_URI')}/oauth?")
    assert params["client_id"] == ["client-1"]
    assert params["redirect_uri"] == ["https://crm.example.com/api/cnv/callback"]
    assert params["response_type"] == ["code"]
    assert params["state"] == ["state-abc"]
    assert params["scope"] == [SSO_SCOPE]
    assert len(SSO_SCOPE.split(",")) == 6


def _http_returning(status_code=200, json_body=None, text=""):
    http = MagicMock()
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    if json_body is None:
        response.json.side_effect = ValueError("no json")
    else:
        response.json.return_value = json_body
    http.request.return_value = response
    return http


def test_exchange_code_parses_bundle():
    http = _http_returning(
        json_body={
            "access_token": "at-1",
            "refresh_token": "rt-1",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
    )
    bundle = CnvSsoClient(_connection(), http=http).exchange_code("code-1")
    assert bundle.access_token == "at-1"
    assert bundle.refresh_token == "rt-1"
    assert bundle.expires_in == 3600
    method, path = http.request.call_args[0][:2]
    assert (method, path) == ("GET", "/oauth/token")
    params = http.request.call_args.kwargs["params"]
    assert params["grant_type"] == "authorization_code"
    assert params["code"] == "code-1"


def test_refresh_sends_refresh_grant():
    http = _http_returning(json_body={"access_token": "at-2"})
    CnvSsoClient(_connection(), http=http).refresh("rt-old")
    params = http.request.call_args.kwargs["params"]
    assert params["grant_type"] == "refresh_token"
    assert params["refresh_token"] == "rt-old"


def test_missing_access_token_raises():
    http = _http_returning(json_body={"error": "invalid_grant"})
    with pytest.raises(CnvAuthError, match="access_token"):
        CnvSsoClient(_connection(), http=http).exchange_code("bad-code")


def test_http_error_raises():
    http = _http_returning(status_code=400, text="Bad request")
    with pytest.raises(CnvAuthError, match="HTTP 400"):
        CnvSsoClient(_connection(), http=http).exchange_code("code")


# ---------------------------------------------------------------------------
# Token provider: chỉ refresh khi cần, re-read DB trước khi tốn lượt refresh.
# (Mark django_db theo từng test — file gốc dùng pytestmark toàn module, nhưng ở
# đây module còn chứa test SSO không DB nên mark tại chỗ để không đổi hành vi.)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_valid_token_returned_without_refresh(cnv_connection):
    with patch.object(token_provider, "CnvSsoClient") as sso:
        token_type, token = token_provider.ensure_access_token(cnv_connection)
    assert (token_type, token) == ("Bearer", "valid-token")
    sso.assert_not_called()


@pytest.mark.django_db
def test_expired_token_refreshes_and_saves(cnv_connection):
    cnv_connection.token_expires_at = timezone.now() - timedelta(minutes=1)
    cnv_connection.save(update_fields=["token_expires_at"])

    bundle = TokenBundle(
        access_token="new-token", refresh_token="new-refresh", expires_in=7200
    )
    with patch.object(token_provider, "CnvSsoClient") as sso:
        sso.return_value.refresh.return_value = bundle
        _type, token = token_provider.ensure_access_token(cnv_connection)

    assert token == "new-token"
    fresh = CnvConnection.objects.get(pk=cnv_connection.pk)
    assert fresh.access_token == "new-token"
    assert fresh.refresh_token == "new-refresh"
    assert fresh.token_expires_at > timezone.now() + timedelta(minutes=60)


@pytest.mark.django_db
def test_rereads_db_before_refreshing(cnv_connection):
    """Worker khác vừa refresh xong → dùng token mới trong DB, không gọi SSO."""
    stale = CnvConnection.objects.get(pk=cnv_connection.pk)
    stale.access_token = "stale-token"
    stale.token_expires_at = timezone.now() - timedelta(minutes=1)

    with patch.object(token_provider, "CnvSsoClient") as sso:
        _type, token = token_provider.ensure_access_token(stale)

    assert token == "valid-token"
    sso.assert_not_called()


@pytest.mark.django_db
def test_missing_refresh_token_raises(cnv_connection):
    cnv_connection.access_token = ""
    cnv_connection.refresh_token = ""
    cnv_connection.token_expires_at = None
    cnv_connection.save(
        update_fields=["access_token", "refresh_token", "token_expires_at"]
    )
    with pytest.raises(CnvAuthError, match="refresh token"):
        token_provider.ensure_access_token(cnv_connection)


@pytest.mark.django_db
def test_force_refresh_ignores_valid_token(cnv_connection):
    bundle = TokenBundle(access_token="forced-token", expires_in=3600)
    with patch.object(token_provider, "CnvSsoClient") as sso:
        sso.return_value.refresh.return_value = bundle
        _type, token = token_provider.ensure_access_token(
            cnv_connection, force_refresh=True
        )
    assert token == "forced-token"
