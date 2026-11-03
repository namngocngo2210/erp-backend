"""user → Credential theo thứ tự D3: key riêng → key chung + vai trò → từ chối."""

from __future__ import annotations

from apps.media.contracts.dtos import Credential
from apps.media.exceptions import MediaDisabled, MediaNotProvisioned
from apps.media.models import MediaConnection


def resolve(user) -> Credential:
    conn = MediaConnection.get_active()
    if conn is None:
        raise MediaDisabled()
    base_url = conn.resolve_base_url()
    personal = (getattr(user, "media_api_key", "") or "").strip()
    if personal:
        return Credential(kind="personal", api_key=personal, role=None, base_url=base_url)
    role = (getattr(user, "media_role", "") or "").strip()
    if role:
        shared = (conn.shared_api_key or "").strip()
        if not shared:
            raise MediaDisabled("Chưa nhập API key chung cho kết nối Media.")
        return Credential(kind="shared", api_key=shared, role=role, base_url=base_url)
    raise MediaNotProvisioned()


def is_provisioned(user) -> bool:
    """Dùng cho allowed_sections: không cần connection, chỉ cần user có key/role."""
    return bool((getattr(user, "media_api_key", "") or "").strip() or (getattr(user, "media_role", "") or "").strip())
