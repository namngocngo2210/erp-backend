"""OAuth client cho SSO CNV (id.cnv.vn) — theo tài liệu "SSO - How to connect".

Ba việc: dựng authorize URL, đổi code lấy token, refresh token. Tài liệu CNV
dùng HTTP GET cho ``/oauth/token`` (khác chuẩn OAuth2 thường là POST) — giữ
nguyên theo tài liệu; nếu thực tế CNV yêu cầu POST thì chỉ sửa ``_token_request``.
"""

from __future__ import annotations

from urllib.parse import urlencode

import requests

from apps.cnv_loyalty.contracts.dtos import TokenBundle
from apps.cnv_loyalty.exceptions import CnvAuthError
from apps.cnv_loyalty.models import CnvConnection
from apps.common.http_client import HttpClient
from apps.sysconfig import sysconfig

# Đủ 6 scope theo tài liệu — thiếu scope nào thì API tương ứng trả 403.
SSO_SCOPE = (
    "read_products,write_products,"
    "read_customers,write_customers,"
    "read_orders,write_orders"
)
_TIMEOUT = 15


class CnvSsoClient:
    def __init__(self, connection: CnvConnection, http: HttpClient | None = None):
        self.connection = connection
        self._http = http or HttpClient(
            base_url=connection.resolve_sso_server_uri(), timeout=_TIMEOUT
        )

    def build_authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self.connection.client_id,
                "redirect_uri": sysconfig.get("CNV_SSO_REDIRECT_URI"),
                "response_type": "code",
                "scope": SSO_SCOPE,
                "state": state,
            }
        )
        return f"{self.connection.resolve_sso_server_uri()}/oauth?{query}"

    def exchange_code(self, code: str) -> TokenBundle:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": self.connection.client_id,
                "client_secret": self.connection.client_secret,
                "redirect_uri": sysconfig.get("CNV_SSO_REDIRECT_URI"),
                "code": code,
            }
        )

    def refresh(self, refresh_token: str) -> TokenBundle:
        return self._token_request(
            {
                "grant_type": "refresh_token",
                "client_id": self.connection.client_id,
                "client_secret": self.connection.client_secret,
                "redirect_uri": sysconfig.get("CNV_SSO_REDIRECT_URI"),
                "refresh_token": refresh_token,
            }
        )

    def verify(self, token_type: str, access_token: str) -> dict:
        """POST /auth/verify — lấy thông tin user của token (bước 3 tài liệu)."""
        response = self._request(
            "POST",
            "/auth/verify",
            headers={
                "Accept": "application/json",
                "Authorization": f"{token_type} {access_token}",
            },
        )
        return self._parse_json(response, "auth/verify")

    def _token_request(self, params: dict) -> TokenBundle:
        response = self._request("GET", "/oauth/token", params=params)
        data = self._parse_json(response, "oauth/token")
        access_token = str(data.get("access_token") or "").strip()
        if not access_token:
            raise CnvAuthError(f"CNV SSO không trả access_token: {str(data)[:300]}", payload=data)
        return TokenBundle(
            access_token=access_token,
            refresh_token=str(data.get("refresh_token") or "").strip(),
            token_type=str(data.get("token_type") or "Bearer").strip(),
            expires_in=int(data.get("expires_in") or 0),
            scope=str(data.get("scope") or "").strip(),
        )

    def _request(self, method: str, path: str, **kwargs):
        try:
            response = self._http.request(method, path, **kwargs)
        except requests.exceptions.RequestException as exc:
            raise CnvAuthError(f"CNV SSO không phản hồi: {exc}") from exc
        if response.status_code >= 400:
            raise CnvAuthError(
                f"CNV SSO {path} HTTP {response.status_code}: {response.text[:500]}"
            )
        return response

    @staticmethod
    def _parse_json(response, label: str) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise CnvAuthError(
                f"CNV SSO {label}: phản hồi không phải JSON: {response.text[:200]}"
            ) from exc
        if not isinstance(data, dict):
            raise CnvAuthError(f"CNV SSO {label}: phản hồi không phải object JSON")
        return data
