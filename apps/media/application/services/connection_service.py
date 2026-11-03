"""Đọc/ghi MediaConnection và kiểm tra key chung bằng GET /auth/me (spec §4.5)."""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.media.exceptions import MediaError
from apps.media.infrastructure.ads_crm_client import AdsCrmClient
from apps.media.models import MediaConnection

NON_ADMIN_WARNING = (
    "Key chung không phải role admin: người dùng key chung sẽ bị 403 khi sửa nội dung không do key tạo."
)


def current() -> MediaConnection | None:
    return MediaConnection.objects.order_by("-updated_at").first()


def to_payload(conn: MediaConnection | None) -> dict[str, Any]:
    if conn is None:
        return {"base_url": "", "shared_api_key_set": False, "shared_api_key_prefix": "", "is_active": False,
                "last_check_at": None, "last_check_ok": None, "last_check_error": "",
                "shared_key_role": "", "shared_key_username": ""}
    key = (conn.shared_api_key or "").strip()
    return {
        "base_url": conn.resolve_base_url(), "shared_api_key_set": bool(key), "shared_api_key_prefix": key[:12],
        "is_active": conn.is_active, "last_check_at": conn.last_check_at, "last_check_ok": conn.last_check_ok,
        "last_check_error": conn.last_check_error, "shared_key_role": conn.shared_key_role,
        "shared_key_username": conn.shared_key_username,
    }


def update(validated: dict[str, Any]) -> MediaConnection:
    conn = current() or MediaConnection(base_url="", shared_api_key="")
    if "base_url" in validated:
        conn.base_url = validated["base_url"].strip().rstrip("/")
    if "shared_api_key" in validated:
        key = validated["shared_api_key"]
        if key is None:
            conn.shared_api_key = ""
        elif key:
            conn.shared_api_key = key
    if "is_active" in validated:
        conn.is_active = validated["is_active"]
    conn.save()
    return conn


def test_shared_key(conn: MediaConnection) -> dict[str, Any]:
    client = AdsCrmClient(conn.resolve_base_url(), conn.shared_api_key)
    conn.last_check_at = timezone.now()
    try:
        me = client.me()
    except MediaError as exc:
        conn.last_check_ok, conn.last_check_error = False, exc.detail
        conn.save(update_fields=["last_check_at", "last_check_ok", "last_check_error", "updated_at"])
        return {"ok": False, "error": exc.detail}
    role, username = str(me.get("role") or ""), str(me.get("username") or "")
    conn.last_check_ok, conn.last_check_error = True, ""
    conn.shared_key_role, conn.shared_key_username = role, username
    conn.save()
    payload: dict[str, Any] = {"ok": True, "role": role, "username": username}
    if role != "admin":
        payload["warning"] = NON_ADMIN_WARNING
    return payload
