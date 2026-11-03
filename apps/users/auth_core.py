"""Lõi xác thực & phân quyền framework-agnostic (D8).

MỘT nguồn chân lý cho "token nào hợp lệ" và "user thấy được gì" — DRF
(``VersionedJWTAuthentication`` + ``require_permission``) ủy quyền về đây.
Từng phục vụ thêm django-ninja (strangler D8); ninja gỡ 2026-08-20 (audit
2026-08-19 mục 4.12), lõi này giữ nguyên cho mọi consumer tương lai.

- Token: ủy quyền cho chính ``VersionedJWTAuthentication`` (cùng kiểm tra
  ``token_version``), không tự decode lại để tránh lệch hành vi.
- Quyền: ủy quyền cho ``rbac.resolve_effective_permissions`` (RBAC+ABAC, có cache,
  admin/role is_system short-circuit).

Module không import gì từ DRF view/request → gọi được từ mọi tầng.
"""

from __future__ import annotations

from rest_framework_simplejwt.exceptions import (
    AuthenticationFailed,
    InvalidToken,
    TokenError,
)

from .jwt import VersionedJWTAuthentication
from .rbac import resolve_effective_permissions


def authenticate_bearer_token(raw_token: str):
    """Xác thực 1 access token (chuỗi) → trả về user, hoặc ``None`` nếu không hợp lệ.

    Tái dùng đúng pipeline của ``VersionedJWTAuthentication`` (validate chữ ký/hạn
    + kiểm tra ``token_version``) nên mọi consumer có hành vi token y hệt DRF.
    """
    if not raw_token:
        return None

    authenticator = VersionedJWTAuthentication()
    try:
        validated = authenticator.get_validated_token(raw_token.encode())
        return authenticator.get_user(validated)
    except (InvalidToken, AuthenticationFailed, TokenError):
        return None


def user_has_any_permission(user, *codes: str) -> bool:
    """True nếu user có BẤT KỲ permission code nào trong ``codes``.

    Nguồn sự thật duy nhất cho mọi check quyền.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if not codes:
        return False
    eff = resolve_effective_permissions(user)
    return any(eff.can(code) for code in codes)


def user_has_section(user, *sections: str) -> bool:
    """'Có truy cập section' = có quyền ``{section}:view`` — mirror ``require_section``."""
    return user_has_any_permission(user, *[f"{section}:view" for section in sections])
