"""HTTP client sang ads-crm `/api/v1` (tài liệu "Tích hợp API đối ngoại").

Hai HttpClient: `reader` có retry (429/5xx là transient — tài liệu khuyên backoff),
`writer` retries=0 vì POST/PATCH/DELETE không idempotent (xem apps.common.http_client).
"""

from __future__ import annotations

import uuid
from typing import Any

import requests
from django.conf import settings

from apps.common.http_client import HttpClient
from apps.media.exceptions import AdsCrmApiError, AdsCrmUnavailable, MediaStagingBlocked

API_PREFIX = "/api/v1"
_READ_METHODS = frozenset({"GET", "HEAD"})


class AdsCrmClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float | None = None,
        reader: HttpClient | None = None,
        writer: HttpClient | None = None,
    ):
        self._base = base_url.rstrip("/") + API_PREFIX
        self._api_key = api_key
        timeout = timeout or float(getattr(settings, "MEDIA_HTTP_TIMEOUT", 30.0))
        self._reader = reader or HttpClient(timeout=timeout, retries=3)
        self._writer = writer or HttpClient(timeout=timeout, retries=0)

    # ── vận chuyển ──────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._api_key,
            "Accept": "application/json",
            "X-Request-ID": uuid.uuid4().hex,
        }

    def _send(
        self, method: str, path: str, *, extra_headers: dict[str, str] | None = None, **kwargs: Any
    ) -> requests.Response:
        if getattr(settings, "IS_STAGING", False):
            raise MediaStagingBlocked()
        http = self._reader if method in _READ_METHODS else self._writer
        url = f"{self._base}/{path.lstrip('/')}"
        headers = self._headers()
        if extra_headers:
            headers.update(extra_headers)
        try:
            return http.request(method, url, headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise AdsCrmUnavailable(f"ads-crm không phản hồi: {exc}") from exc

    def _raise_for_error(self, response: requests.Response) -> None:
        if response.status_code < 400:
            return
        try:
            body = response.json()
        except ValueError as exc:
            raise AdsCrmUnavailable(
                f"ads-crm trả HTTP {response.status_code} không phải JSON: {response.text[:200]}"
            ) from exc
        code = body.get("error") if isinstance(body, dict) else None
        raise AdsCrmApiError(str(code or "upstream_error"), response.status_code)

    # ── API ─────────────────────────────────────────────────────────────────
    def json_request(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str]] | dict[str, str] | None = None,
        json: Any = None,
    ) -> tuple[int, Any]:
        response = self._send(method, path, params=params, json=json)
        self._raise_for_error(response)
        if response.status_code == 204:
            return response.status_code, None
        try:
            return response.status_code, response.json()
        except ValueError as exc:
            raise AdsCrmUnavailable("ads-crm trả body không phải JSON.") from exc

    def stream_get(self, path: str, *, timeout: float) -> requests.Response:
        response = self._send("GET", path, stream=True, timeout=timeout)
        self._raise_for_error(response)
        return response

    def stream_media(
        self, path: str, *, timeout: float, range_header: str | None = None
    ) -> requests.Response:
        """GET streaming CÓ theo redirect (khác `video_location`) để lấy thẳng bytes
        video từ R2 cho content_video_stream bơm về trình duyệt qua chính ERP —
        dùng khi mạng của trình duyệt không tới được `*.r2.cloudflarestorage.com`.

        Forward `Range` (nếu có) để hỗ trợ tua/seek: `requests` GIỮ NGUYÊN header
        Range khi theo redirect sang host khác (chỉ Authorization/Cookie mới bị
        `requests` tự strip qua redirect cross-host), nên R2 vẫn nhận đúng byte
        range dù ta không tự gắn lại sau redirect. Header `X-API-Key` chỉ tới
        ads-crm ở request đầu — không sao vì R2 không cần auth (URL đích đã ký sẵn
        do ads-crm redirect tới). Không đọc body."""
        extra = {"Range": range_header} if range_header else None
        response = self._send(
            "GET", path, stream=True, timeout=timeout, allow_redirects=True, extra_headers=extra
        )
        self._raise_for_error(response)
        return response

    def video_location(self, path: str) -> tuple[int, str | None]:
        """GET không theo redirect: ads-crm trả 302 + Location (URL R2 ký sẵn, ~15
        phút, để trình duyệt phát/tua trực tiếp) hoặc 200 nếu cấu hình fallback tự
        stream. 302 KHÔNG phải lỗi — chỉ status >= 400 mới qua `_raise_for_error`
        (401/404/... như các lệnh gọi khác). Không đọc body."""
        response = self._send("GET", path, allow_redirects=False)
        self._raise_for_error(response)
        return response.status_code, response.headers.get("Location")

    def me(self) -> dict[str, Any]:
        _, data = self.json_request("GET", "/auth/me")
        return data if isinstance(data, dict) else {}
