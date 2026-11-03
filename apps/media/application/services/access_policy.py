"""Enforce ma trận role Media khi dùng key chung (spec §5.1 lớp 2)."""

from __future__ import annotations

from collections.abc import Callable

from apps.media.contracts.dtos import Credential
from apps.media.domain.roles import Action, Verdict, check, is_owner_scoped
from apps.media.exceptions import MediaForbidden, MediaNotOwner


def authorize(
    cred: Credential,
    action: Action,
    *,
    owner_check: Callable[[], bool] | None = None,
) -> None:
    if cred.kind == "personal":
        return  # ads-crm tự phân quyền theo role của key riêng
    role = cred.role or ""
    needs_owner = is_owner_scoped(role, action)
    is_owner = bool(owner_check()) if (needs_owner and owner_check is not None) else False
    verdict = check(role, action, is_owner=is_owner)
    if verdict is Verdict.DENY_ROLE:
        raise MediaForbidden()
    if verdict is Verdict.DENY_OWNER:
        raise MediaNotOwner()
