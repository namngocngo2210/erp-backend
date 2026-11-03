"""Helpers dựng user + role cho test (sau khi require_section dùng resolver).

Dùng trong test app khác:
    from apps.users.tests.helpers import make_user_with_sections
    staff = make_user_with_sections("sales1", ["orders", "customers"])  # full action mỗi section
"""

from apps.users.models import (
    DataScope,
    Permission,
    Role,
    RoleFieldGrant,
    RolePermission,
    User,
    UserRoleAssignment,
)
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import ORDER_GRANT_SECTION, sync_permissions

_role_seq = 0


def make_admin(username: str = "admin") -> User:
    sync_permissions()
    user = User.objects.create_user(username=username, password="x")
    role, _ = Role.objects.get_or_create(
        name="Administrator", defaults={"is_system": True, "scope_default": DataScope.ALL}
    )
    UserRoleAssignment.objects.create(user=user, role=role)
    bump_rbac_cache_version()
    return user


def make_user_with_permissions(username: str, codes, scope=DataScope.ALL) -> User:
    sync_permissions()
    global _role_seq
    _role_seq += 1
    user = User.objects.create_user(username=username, password="x")
    role = Role.objects.create(name=f"test-role-{_role_seq}", scope_default=scope)
    perms = list(Permission.objects.filter(code__in=list(codes)))
    RolePermission.objects.bulk_create([RolePermission(role=role, permission=p) for p in perms])
    UserRoleAssignment.objects.create(user=user, role=role)
    bump_rbac_cache_version()
    return user


def make_user_with_sections(username: str, sections, scope=DataScope.ALL) -> User:
    """Gán mọi action của các section (mirror page-access cũ)."""
    sync_permissions()
    codes = list(
        Permission.objects.filter(section__in=list(sections)).values_list("code", flat=True)
    )
    return make_user_with_permissions(username, codes, scope=scope)


def grant_order_fields(user: User, *fields: str) -> None:
    """Cấp `RoleFieldGrant` (section "orders") cho MỌI role hiện có của `user`.

    Guard quyền sửa-theo-field trên PATCH đơn (Task 7, fail-closed) chặn field
    KHÔNG được cấp dù giá trị đổi thật. `make_admin`/`make_user_with_permissions`/
    `make_user_with_sections` (và `apps.orders.tests.helpers.make_scoped_user`) không
    giữ tham chiếu tới `Role` đã tạo, nên test cần PATCH thành công một field cụ thể
    phải tra lại role qua `user_assignments` rồi cấp field đó — KHÔNG cấp cả
    `ALL_ORDER_FIELDS`, để guard vẫn phát hiện được hồi quy trên field khác.
    """
    roles = Role.objects.filter(user_assignments__user=user, is_active=True)
    RoleFieldGrant.objects.bulk_create(
        RoleFieldGrant(role=role, section=ORDER_GRANT_SECTION, field=field)
        for role in roles
        for field in fields
    )
    bump_rbac_cache_version()
