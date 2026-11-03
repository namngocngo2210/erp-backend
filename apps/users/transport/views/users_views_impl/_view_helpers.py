"""Helper dùng chung cho các view của app users.

Gom cookie refresh token, payload `/me` và guard admin — dùng chéo giữa nhóm
view auth và các nhóm view quản trị (user / phòng ban / RBAC).
"""

from django.conf import settings
from rest_framework.response import Response

from apps.common.pagination import clamp_int_param
from apps.users.models import VALID_SECTIONS, User, UserKiotvietMapping
from apps.users.rbac import resolve_effective_permissions
from apps.users.serializers import UserSerializer


def _refresh_cookie_max_age_seconds() -> int:
    return int(settings.JWT_REFRESH_DAYS) * 24 * 60 * 60


def _set_refresh_cookie(response: Response, refresh_value: str) -> None:
    response.set_cookie(
        key="refresh_token",
        value=str(refresh_value),
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        max_age=_refresh_cookie_max_age_seconds(),
    )


def _me_payload(user) -> dict:
    eff = resolve_effective_permissions(user)
    data = dict(UserSerializer(user).data)
    # KHÔNG tính lại allowed_sections ở đây: `UserSerializer.get_allowed_sections`
    # còn gộp thêm các section Media cho user đã được cấp quyền Media (vai trò
    # hoặc API key riêng) — trước đây chỗ này ghi đè bằng bản chỉ suy từ
    # permission_codes, nên user thường có media_role vẫn không vào được trang
    # Media (superuser không lộ vì nhận trọn VALID_SECTIONS).
    allowed_sections = list(data["allowed_sections"])
    if eff.is_superuser:
        permissions: list[str] = []  # superuser: FE dựa is_superuser, không cần liệt kê
        scopes = dict.fromkeys(VALID_SECTIONS, "all")
        field_policy: dict = {}
    else:
        permissions = sorted(eff.permission_codes)
        scopes = dict(eff.scope_by_section)
        field_policy = dict(eff.field_policy)
    data.update(
        {
            "permissions": permissions,
            "allowed_sections": allowed_sections,
            "scopes": scopes,
            "field_policy": field_policy,
            "is_superuser": eff.is_superuser,
        }
    )
    return data


def _is_admin(user: User) -> bool:
    return resolve_effective_permissions(user).is_superuser


def _replace_kiotviet_mappings(user: User, raw_mappings: list[dict]) -> None:
    desired_kv_ids = {entry["kiotviet_channel_id"] for entry in raw_mappings}
    UserKiotvietMapping.objects.filter(user=user).exclude(
        kiotviet_channel_id__in=desired_kv_ids
    ).delete()
    for entry in raw_mappings:
        UserKiotvietMapping.objects.update_or_create(
            user=user,
            kiotviet_channel_id=entry["kiotviet_channel_id"],
            defaults={"external_kiot_user_id": entry["external_kiot_user_id"]},
        )


def _parse_page_param(raw, default: int, minimum: int, maximum: int | None = None) -> int:
    return clamp_int_param(raw, default=default, minimum=minimum, maximum=maximum)
