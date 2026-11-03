"""Tổng quát hóa record-level scope. Trả tập channel code được phép theo scope
hiệu dụng của user trên 1 section.

Quy ước trả về của resolved_channel_codes:
- None              → không giới hạn (all, hoặc admin/superuser).
- set[str]          → chỉ các channel code này (channel / department scope).
- OWN_SCOPE_SENTINEL→ scope theo người tạo/sở hữu; caller phải tự lọc theo owner field
                      (channel code không biểu diễn được "của tôi").

Không hardcode phòng ban — mọi quyết định lấy từ resolver + UserChannel/UserDepartment.
"""

from apps.channels_core.models import Channel, UserChannel

from .models import DataScope, UserDepartment
from .rbac import resolve_effective_permissions


class _OwnScope:
    __slots__ = ()

    def __repr__(self) -> str:
        return "OWN_SCOPE_SENTINEL"


OWN_SCOPE_SENTINEL = _OwnScope()


def _user_channel_codes(user) -> set[str]:
    # channel__is_active=True để read-scope khớp đúng edit-scope (assigned_channel_codes):
    # kênh inactive-nhưng-chưa-xóa không hiện ở list cũng không sửa được → "edit mirror read".
    return set(
        UserChannel.objects.filter(
            user=user,
            channel__deleted_at__isnull=True,
            channel__is_active=True,
            is_active=True,
        ).values_list("channel__code", flat=True)
    )


def department_channel_codes(user) -> set[str]:
    """Channel code của các kênh thuộc phòng ban user (qua UserDepartment, fallback User.department)."""
    dept_ids = set(
        UserDepartment.objects.filter(user=user).values_list("department_id", flat=True)
    )
    legacy_dept = getattr(user, "department_id", None)
    if legacy_dept:
        dept_ids.add(legacy_dept)
    if not dept_ids:
        return set()
    return set(
        Channel.objects.filter(
            departments__id__in=dept_ids, deleted_at__isnull=True
        ).values_list("code", flat=True)
    )


def resolved_channel_codes(user, section: str):
    eff = resolve_effective_permissions(user)
    if eff.is_superuser:
        return None
    scope = eff.scope_for(section)
    if scope == DataScope.ALL:
        return None
    if scope == DataScope.CHANNEL:
        return _user_channel_codes(user)
    if scope == DataScope.DEPARTMENT:
        return department_channel_codes(user)
    # own
    return OWN_SCOPE_SENTINEL
