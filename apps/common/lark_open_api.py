"""Shared Lark Open API plumbing: tenant_access_token + authorized requests.

Tách từ ``apps.kiotviet.infrastructure.services.lark_bitable_client`` để các
module khác (hr nhắc chấm công, ads sync) gọi Lark mà không phụ thuộc app
kiotviet. Giữ nguyên cache key token cũ nên token vẫn được share toàn process.

Docs:
  - https://open.larksuite.com/document/server-docs/authentication-management/access-token/tenant_access_token_internal
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from django.core.cache import cache

from apps.common import runtime_config
from apps.common.http_client import HttpClient

logger = logging.getLogger(__name__)

# Shared pooled client (retry on 429/5xx + per-call timeout override).
_client = HttpClient()

# Token cache theo app_id — tenant có thể chạy nhiều app (Bitable revenue vs
# bot nhắc chấm công), token của app này không dùng được cho app kia.
_TOKEN_CACHE_KEY_PREFIX = "lark:tenant_access_token"
# Lark tokens last 7200s; refresh slightly before expiry.
_TOKEN_SAFETY_MARGIN_S = 300

# 99991663 = access token expired, 99991668 = invalid token.
_TOKEN_RETRY_CODES = (99991663, 99991668)


class LarkOpenAPIError(Exception):
    """Raised when a Lark Open API call fails or returns a non-zero code."""


class LarkCredentials(NamedTuple):
    app_id: str
    app_secret: str


def _resolve_credentials(credentials: LarkCredentials | None) -> LarkCredentials:
    """Dùng credentials truyền vào, không có thì rơi về app mặc định (LARK_APP_ID)."""
    if credentials is not None:
        return credentials
    return LarkCredentials(
        app_id=str(runtime_config.get("LARK_APP_ID")),
        app_secret=str(runtime_config.get("LARK_APP_SECRET")),
    )


def base_url() -> str:
    return str(runtime_config.get("LARK_OPEN_BASE_URL")).rstrip("/")


def timeout_seconds() -> float:
    return float(runtime_config.get("LARK_OPEN_API_TIMEOUT_SECONDS") or 15)


def _fetch_tenant_access_token(creds: LarkCredentials) -> tuple[str, int]:
    """POST credentials to Lark and return (token, expire_seconds)."""
    if not creds.app_id or not creds.app_secret:
        raise LarkOpenAPIError("LARK_APP_ID hoặc LARK_APP_SECRET chưa cấu hình.")

    url = f"{base_url()}/auth/v3/tenant_access_token/internal"
    resp = _client.post(
        url,
        json={"app_id": creds.app_id, "app_secret": creds.app_secret},
        timeout=timeout_seconds(),
    )
    if resp.status_code != 200:
        raise LarkOpenAPIError(
            f"tenant_access_token HTTP {resp.status_code}: {(resp.text or '')[:300]}"
        )
    data = resp.json() or {}
    if data.get("code") != 0:
        raise LarkOpenAPIError(
            f"tenant_access_token error code={data.get('code')} msg={data.get('msg')}"
        )
    token = data.get("tenant_access_token")
    expire = int(data.get("expire") or 7200)
    if not token:
        raise LarkOpenAPIError("tenant_access_token response missing token.")
    return token, expire


def _token_cache_key(creds: LarkCredentials) -> str:
    return f"{_TOKEN_CACHE_KEY_PREFIX}:{creds.app_id}"


def get_tenant_access_token(credentials: LarkCredentials | None = None) -> str:
    creds = _resolve_credentials(credentials)
    cache_key = _token_cache_key(creds)
    cached = cache.get(cache_key)
    if cached:
        return cached
    token, expire = _fetch_tenant_access_token(creds)
    ttl = max(60, expire - _TOKEN_SAFETY_MARGIN_S)
    cache.set(cache_key, token, ttl)
    return token


def invalidate_tenant_access_token(credentials: LarkCredentials | None = None) -> None:
    cache.delete(_token_cache_key(_resolve_credentials(credentials)))


def lark_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    credentials: LarkCredentials | None = None,
) -> dict:
    """Call a Lark Open API endpoint, return ``data``; auto-retry once on token expiry."""
    for attempt in (1, 2):
        token = get_tenant_access_token(credentials)
        url = f"{base_url()}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        resp = _client.request(
            method, url, headers=headers, json=json_body, params=params, timeout=timeout_seconds()
        )
        if resp.status_code != 200:
            raise LarkOpenAPIError(
                f"Lark {method} {path} HTTP {resp.status_code}: {(resp.text or '')[:300]}"
            )
        data = resp.json() or {}
        code = data.get("code")
        if code == 0:
            return data.get("data") or {}
        if code in _TOKEN_RETRY_CODES and attempt == 1:
            invalidate_tenant_access_token(credentials)
            continue
        raise LarkOpenAPIError(
            f"Lark {method} {path} code={code} msg={data.get('msg')} body={str(data)[:300]}"
        )
    raise LarkOpenAPIError(f"Lark {method} {path} failed after retry")
