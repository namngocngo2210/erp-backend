"""Zenify Contact Center — đăng nhập SDK lấy access token cho webphone.

Tài liệu vendor: HuongDanTichHopWebSDKv2.3 (Bước 2). api-key/api-secret CHỈ được
dùng server-side; client tuyệt đối không được thấy. Lỗi từ Zenify giữ nguyên văn
để FE hiển thị (chuẩn error-passthrough của hệ thống).
"""

from __future__ import annotations

import time

import requests
from django.conf import settings
from django.core.cache import cache

from apps.common.http_client import HttpClient
from apps.sysconfig import sysconfig

SIGN_IN_PATH = "/api/v1/sdk/sign-in"
# Đổi token trước khi hết hạn 1h để phiên webphone không chết giữa chừng.
TOKEN_CACHE_MARGIN_SECONDS = 3600
# Single-flight khi nhiều sale cùng miss cache (nhất là khi dùng chung 1 agent email).
_LOCK_TTL_SECONDS = 15
_LOCK_WAIT_STEPS = 10
_LOCK_WAIT_INTERVAL = 0.5


class ZenifyError(Exception):
    """Lỗi khi gọi Zenify — ``status_code`` là HTTP status trả về cho FE."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def zenify_configured() -> bool:
    return bool(
        sysconfig.get("ZENIFY_DOMAIN")
        and sysconfig.get("ZENIFY_API_KEY")
        and sysconfig.get("ZENIFY_API_SECRET")
    )


def resolve_agent_email(user) -> str:
    """Email đăng nhập Zenify theo thứ tự: ``zenify_email`` admin nhập riêng cho
    user (form Người dùng) → env ZENIFY_AGENT_EMAIL (agent dùng chung) → email
    tài khoản ERP của user."""
    per_user = (getattr(user, "zenify_email", "") or "").strip()
    if per_user:
        return per_user
    configured = (settings.ZENIFY_AGENT_EMAIL or "").strip()
    if configured:
        return configured
    return (getattr(user, "email", "") or "").strip()


def _cache_key(email: str) -> str:
    return f"zenify:token:{email.strip().lower()}"


def _serve(entry: dict) -> dict:
    """Payload trả cho FE — ttl là thời gian CÒN LẠI, không phải ttl gốc."""
    remaining = max(int(entry["expires_at"] - time.time()), 0)
    return {
        "token": entry["token"],
        "ttl": remaining,
        "email": entry["email"],
        "agent_name": entry["agent_name"],
    }


def get_token(email: str, refresh: bool = False) -> dict:
    """Token Zenify cho agent ``email``: đọc cache Redis, miss thì sign-in.

    Single-flight bằng cache.add — khi cả phòng sale cùng vào sau deploy chỉ 1
    request sign-in thật, số còn lại chờ đọc cache (hết chờ thì tự sign-in để
    không treo user). ``refresh=True`` bỏ qua cache (token bị Zenify từ chối).
    """
    key = _cache_key(email)
    if not refresh:
        cached = cache.get(key)
        if cached:
            return _serve(cached)

    lock_key = f"{key}:lock"
    got_lock = cache.add(lock_key, 1, timeout=_LOCK_TTL_SECONDS)
    if not got_lock and not refresh:
        for _ in range(_LOCK_WAIT_STEPS):
            time.sleep(_LOCK_WAIT_INTERVAL)
            cached = cache.get(key)
            if cached:
                return _serve(cached)

    try:
        data = sign_in(email)
        ttl = int(data.get("ttl") or settings.ZENIFY_TOKEN_TTL_SECONDS)
        info = data.get("info") if isinstance(data.get("info"), dict) else {}
        entry = {
            "token": data["token"],
            "expires_at": time.time() + ttl,
            "email": email,
            "agent_name": str(info.get("name") or ""),
        }
        cache.set(key, entry, timeout=max(ttl - TOKEN_CACHE_MARGIN_SECONDS, 60))
        return _serve(entry)
    finally:
        if got_lock:
            cache.delete(lock_key)


def sign_in(email: str) -> dict:
    """Gọi POST {base}/api/v1/sdk/sign-in → dict ``data`` (token, ttl, info...)."""
    # Endpoint interactive (user chờ trang load) → timeout ngắn, chỉ retry 1 lần;
    # FE coi lỗi là "tính năng tắt" nên không cần cố bằng mọi giá.
    client = HttpClient(base_url=settings.ZENIFY_API_BASE_URL, timeout=10, retries=1)
    try:
        resp = client.post(
            SIGN_IN_PATH,
            headers={
                "Content-Type": "application/json",
                "domain": sysconfig.get("ZENIFY_DOMAIN"),
                "api-key": sysconfig.get("ZENIFY_API_KEY"),
                "api-secret": sysconfig.get("ZENIFY_API_SECRET"),
            },
            json={"email": email, "ttl_in_second": settings.ZENIFY_TOKEN_TTL_SECONDS},
        )
    except requests.RequestException as exc:
        raise ZenifyError(f"Không kết nối được Zenify: {exc}") from exc
    finally:
        client.close()

    try:
        payload = resp.json()
    except ValueError:
        payload = {}

    if not isinstance(payload, dict):
        payload = {}
    data = payload.get("data")
    if resp.status_code != 200 or payload.get("error") is True or not isinstance(data, dict):
        detail = _extract_error_message(payload) or resp.text[:500] or f"HTTP {resp.status_code}"
        raise ZenifyError(f"Zenify sign-in thất bại: {detail}")
    if not data.get("token"):
        raise ZenifyError("Zenify sign-in không trả về token.")
    return data


def _extract_error_message(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("message", "detail", "error_message", "msg"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    data = payload.get("data")
    if isinstance(data, str) and data.strip():
        return data.strip()
    return ""
