"""MediaProxy: một đối tượng cho một request — giữ credential + client, gói forward,
cache taxonomy và bảng owner (spec §4.5)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.core.cache import cache

from apps.media.application.services.access_policy import authorize
from apps.media.application.services.credential_resolver import is_provisioned, resolve
from apps.media.contracts.dtos import Credential
from apps.media.domain.roles import Action
from apps.media.exceptions import MediaError
from apps.media.infrastructure.ads_crm_client import AdsCrmClient
from apps.media.models import MediaConnection, MediaContentOwner, MediaProposalOwner

ME_CACHE_TTL = 600


def _tax_key(group: str, include_inactive: bool) -> str:
    return f"media:tax:{group}:{int(include_inactive)}"


class MediaProxy:
    def __init__(self, user):
        self.user = user
        self.cred: Credential = resolve(user)
        self.client = AdsCrmClient(self.cred.base_url, self.cred.api_key)

    # ── forward ─────────────────────────────────────────────────────────────
    def forward(
        self,
        method: str,
        path: str,
        *,
        action: Action,
        params: list[tuple[str, str]] | None = None,
        json: Any = None,
        owner_check: Callable[[], bool] | None = None,
    ) -> tuple[int, Any]:
        authorize(self.cred, action, owner_check=owner_check)
        return self.client.json_request(method, path, params=params or None, json=json)

    def allows(self, action: Action) -> bool:
        try:
            authorize(self.cred, action, owner_check=lambda: True)
        except MediaError:
            return False
        return True

    # ── taxonomy cache ──────────────────────────────────────────────────────
    def taxonomy(self, group: str, include_inactive: bool) -> Any:
        """Chỉ cache biến thể `include_inactive=False`.

        Với key riêng, `authorize()` là no-op (ads-crm tự phân quyền theo key đó),
        nên `allows(TAXONOMY_INCLUDE_INACTIVE)` luôn True bất kể vai trò thật của
        key trên ads-crm. Nếu cache biến thể include_inactive=True dùng chung cho
        mọi credential, một key riêng không có quyền vẫn đọc được dữ liệu inactive
        do người khác làm nóng cache. Vì vậy include_inactive=True luôn gọi thẳng
        ads-crm — để chính ads-crm phân quyền lại trên từng request — và không ghi cache.
        """
        include_inactive = include_inactive and self.allows(Action.TAXONOMY_INCLUDE_INACTIVE)
        if include_inactive:
            _, data = self.forward(
                "GET", f"/{group}", action=Action.READ, params=[("include_inactive", "true")]
            )
            return data
        key = _tax_key(group, False)
        data = cache.get(key)
        if data is None:
            _, data = self.forward("GET", f"/{group}", action=Action.READ, params=None)
            cache.set(key, data, int(getattr(settings, "MEDIA_TAXONOMY_CACHE_TTL", 300)))
        return data

    @staticmethod
    def invalidate_taxonomy(group: str) -> None:
        cache.delete_many([_tax_key(group, False), _tax_key(group, True)])

    # ── me ──────────────────────────────────────────────────────────────────
    def remote_me(self) -> dict[str, Any] | None:
        """Role của key riêng, cache 10 phút theo sha256(key). None nếu upstream từ chối."""
        key = "media:me:" + hashlib.sha256(self.cred.api_key.encode()).hexdigest()
        data = cache.get(key)
        if data is None:
            try:
                data = self.client.me()
            except MediaError:
                return None
            cache.set(key, data, ME_CACHE_TTL)
        return data

    # ── ownership (chỉ key chung) ───────────────────────────────────────────
    def owns_content(self, remote_id: int) -> bool:
        return MediaContentOwner.objects.filter(remote_id=remote_id, user=self.user).exists()

    def owns_proposal(self, remote_id: int) -> bool:
        return MediaProposalOwner.objects.filter(remote_id=remote_id, user=self.user).exists()

    def record_content_owner(self, remote_id: int) -> None:
        if self.cred.kind == "shared":
            MediaContentOwner.objects.get_or_create(remote_id=remote_id, defaults={"user": self.user})

    def record_proposal_owner(self, remote_id: int) -> None:
        if self.cred.kind == "shared":
            MediaProposalOwner.objects.get_or_create(remote_id=remote_id, defaults={"user": self.user})


def describe_me(user) -> dict[str, Any]:
    """Payload GET /api/media/me — không raise khi chưa provisioned (spec §4.5).

    Nếu user đã provisioned nhưng khởi tạo MediaProxy vẫn ném MediaError (VD: key
    chung để trống dù connection đang bật), trả 200 với connection_ok=False thay vì
    để lỗi 503 lọt ra — vì đây là câu hỏi "tôi có quyền gì", không phải một lệnh gọi upstream.
    """
    conn = MediaConnection.get_active()
    base = conn.resolve_base_url() if conn else ""
    empty = {"provisioned": False, "mode": None, "role": None, "remote_user": None,
             "public_base_url": base, "connection_ok": conn is not None}
    if conn is None or not is_provisioned(user):
        return empty
    try:
        proxy = MediaProxy(user)
    except MediaError:
        return {**empty, "provisioned": True, "mode": None, "connection_ok": False}
    if proxy.cred.kind == "shared":
        return {**empty, "provisioned": True, "mode": "shared", "role": proxy.cred.role}
    remote = proxy.remote_me()
    if remote is None:
        return {**empty, "provisioned": True, "mode": "personal", "connection_ok": False}
    return {
        **empty, "provisioned": True, "mode": "personal", "role": remote.get("role"),
        "remote_user": {"id": remote.get("id"), "username": remote.get("username"), "name": remote.get("name")},
    }
