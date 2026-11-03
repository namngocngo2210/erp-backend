"""Resolver quyền hiệu dụng (RBAC + ABAC) + cache Redis theo user.

Quy tắc union (spec mục 4):
- permission: union tập hợp.
- scope theo section: rộng nhất (all > department > channel > own).
- field rule: ít hạn chế nhất (hidden > readonly > full).
- editable field: hợp các grant (allow-list).

Cache theo user_id, kèm cache-version (mirror bump_channels_cache_version). Bất kỳ
thay đổi role/gán role/permission/scope/field/UserChannel/UserDepartment → bump version.
"""

from dataclasses import dataclass, field

from django.core.cache import cache

from .models import (
    DataScope,
    FieldRuleType,
    Role,
    RoleFieldGrant,
    RoleFieldRule,
    RolePermission,
    RoleSectionScope,
)

RBAC_CACHE_VERSION_KEY = "rbac:cache:version"

# Version SHAPE của payload cache — khác hẳn cache-version ở trên (cái đó bump khi DỮ
# LIỆU quyền đổi). Tăng hằng này MỖI KHI shape `EffectivePermissions.to_cache()` đổi
# (thêm/bớt/đổi nghĩa một key).
#
# Vì sao phải có: runbook deploy là migrate TRƯỚC, roll code SAU. Migration bump
# cache-version rồi, nhưng trong 1-3 phút pull image + recreate container thì CODE CŨ
# vẫn phục vụ request và vẫn GHI entry ở version MỚI — entry đó thiếu key mà code mới
# cần, TTL 30 phút. Code mới lên đọc trúng, `editable_fields` rỗng, fail-closed → cả
# công ty 403 tới nửa tiếng. Prefix shape đứng trong key nên hai thế hệ code không bao
# giờ đọc trúng entry của nhau, kể cả khi cache-version trùng nhau.
RBAC_CACHE_SHAPE_VERSION = "v2"

# Hạng scope: số lớn = rộng hơn. Dùng để chọn "rộng nhất".
SCOPE_RANK = {
    DataScope.OWN: 0,
    DataScope.CHANNEL: 1,
    DataScope.DEPARTMENT: 2,
    DataScope.ALL: 3,
}
# Hạng field rule: số lớn = hạn chế hơn. Chọn "ít hạn chế nhất" = rank nhỏ nhất.
FIELD_RANK = {
    FieldRuleType.READONLY: 1,
    FieldRuleType.HIDDEN: 2,
}


def get_rbac_cache_version() -> int:
    version = cache.get(RBAC_CACHE_VERSION_KEY)
    if version is None:
        cache.set(RBAC_CACHE_VERSION_KEY, 1, timeout=None)
        return 1
    return int(version)


def bump_rbac_cache_version() -> int:
    try:
        return int(cache.incr(RBAC_CACHE_VERSION_KEY))
    except ValueError:
        cache.set(RBAC_CACHE_VERSION_KEY, 2, timeout=None)
        return 2


@dataclass(frozen=True)
class EffectivePermissions:
    permission_codes: frozenset = field(default_factory=frozenset)
    scope_by_section: dict = field(default_factory=dict)
    field_policy: dict = field(default_factory=dict)
    # Allow-list field được SỬA, theo section. Vắng mặt = không được sửa.
    editable_fields: dict = field(default_factory=dict)
    is_superuser: bool = False

    def can(self, code: str) -> bool:
        return self.is_superuser or code in self.permission_codes

    def scope_for(self, section: str) -> str:
        if self.is_superuser:
            return DataScope.ALL
        return self.scope_by_section.get(section, DataScope.OWN)

    def can_edit_field(self, section: str, field_name: str) -> bool:
        if self.is_superuser:
            return True
        return field_name in self.editable_fields.get(section, frozenset())

    def to_cache(self) -> dict:
        return {
            "permission_codes": sorted(self.permission_codes),
            "scope_by_section": self.scope_by_section,
            "field_policy": self.field_policy,
            "editable_fields": {s: sorted(f) for s, f in self.editable_fields.items()},
            "is_superuser": self.is_superuser,
        }

    @classmethod
    def from_cache(cls, data: dict) -> "EffectivePermissions":
        return cls(
            permission_codes=frozenset(data["permission_codes"]),
            scope_by_section=data["scope_by_section"],
            field_policy=data["field_policy"],
            # .get() chứ KHÔNG data[...]: lớp phòng thủ thứ hai sau
            # RBAC_CACHE_SHAPE_VERSION — entry thiếu key phải suy biến về fail-closed,
            # không phải KeyError hàng loạt.
            editable_fields={
                s: frozenset(f) for s, f in (data.get("editable_fields") or {}).items()
            },
            is_superuser=data["is_superuser"],
        )


_SUPERUSER = EffectivePermissions(is_superuser=True)


@dataclass
class _RoleRows:
    """Dữ liệu 4 bảng rule của một tập role, nạp MỘT lần rồi hợp theo từng user.

    ``_compute`` (1 user) và ``resolve_effective_permissions_bulk`` (N user) cùng
    đi qua ``_build_effective`` với cùng bộ dữ liệu này — hai đường không thể lệch
    luật union/rank vì chỉ có một chỗ tính (audit 2026-09-23 T1 §2.2).
    """

    # role_id → (role.is_system, role.scope_default)
    roles: dict
    # (role_id, code, section) — RolePermission
    perm_rows: list
    # role_id → {section: scope} — RoleSectionScope
    overrides: dict
    # (role_id, section, field, rule) — RoleFieldRule
    field_rules: list
    # (role_id, section, field) — RoleFieldGrant
    grants: list


def _load_role_rows(role_ids: list[int]) -> _RoleRows:
    """4 query `role_id__in` cho toàn bộ tập role — không phụ thuộc số user."""
    if not role_ids:
        return _RoleRows(roles={}, perm_rows=[], overrides={}, field_rules=[], grants=[])
    roles = {
        rid: (is_system, scope_default)
        for rid, is_system, scope_default in Role.objects.filter(
            id__in=role_ids, is_active=True
        ).values_list("id", "is_system", "scope_default")
    }
    active_ids = list(roles)
    if not active_ids:
        return _RoleRows(roles={}, perm_rows=[], overrides={}, field_rules=[], grants=[])
    # 1) permission codes: một values_list lấy CẢ code lẫn (role, section) cho bước
    # scope — trước đây cùng queryset này bị chạy hai lần (audit 2026-08-19 P3).
    perm_rows = list(
        RolePermission.objects.filter(role_id__in=active_ids).values_list(
            "role_id", "permission__code", "permission__section"
        )
    )
    overrides: dict[int, dict[str, str]] = {}
    for rid, section, scope in RoleSectionScope.objects.filter(
        role_id__in=active_ids
    ).values_list("role_id", "section", "scope"):
        overrides.setdefault(rid, {})[section] = scope
    field_rules = list(
        RoleFieldRule.objects.filter(role_id__in=active_ids).values_list(
            "role_id", "section", "field", "rule"
        )
    )
    grants = list(
        RoleFieldGrant.objects.filter(role_id__in=active_ids).values_list(
            "role_id", "section", "field"
        )
    )
    return _RoleRows(
        roles=roles, perm_rows=perm_rows, overrides=overrides, field_rules=field_rules, grants=grants
    )


def _build_effective(role_ids: list[int], rows: _RoleRows) -> EffectivePermissions:
    """Hợp quyền của MỘT user từ tập role của họ (chỉ tính trên dữ liệu đã nạp)."""
    role_ids = [rid for rid in role_ids if rid in rows.roles]
    # Short-circuit: bất kỳ role is_system nào (Administrator) → full quyền.
    if any(rows.roles[rid][0] for rid in role_ids):
        return _SUPERUSER
    if not role_ids:
        return EffectivePermissions()
    wanted = set(role_ids)

    # 1) permission codes = union.
    codes = {code for rid, code, _section in rows.perm_rows if rid in wanted}

    # 2) scope theo section = rộng nhất.
    #    Mỗi role áp scope cho mọi section mà nó cấp quyền: override RoleSectionScope nếu có,
    #    ngược lại scope_default của role.
    sections_by_role: dict[int, set[str]] = {}
    for rid, _code, section in rows.perm_rows:
        if rid in wanted:
            sections_by_role.setdefault(rid, set()).add(section)
    scope_by_section: dict[str, str] = {}
    for rid in role_ids:
        scope_default = rows.roles[rid][1]
        for section in sections_by_role.get(rid, set()):
            scope = rows.overrides.get(rid, {}).get(section, scope_default)
            current = scope_by_section.get(section)
            if current is None or SCOPE_RANK[scope] > SCOPE_RANK[current]:
                scope_by_section[section] = scope

    # 3) field policy = ít hạn chế nhất theo (section, field)
    field_policy: dict[str, dict[str, str]] = {}
    for rid, section, fname, rule in rows.field_rules:
        if rid not in wanted:
            continue
        section_rules = field_policy.setdefault(section, {})
        current = section_rules.get(fname)
        if current is None or FIELD_RANK[rule] < FIELD_RANK[current]:
            section_rules[fname] = rule

    # 4) editable fields = HỢP các grant (allow-list, nới lỏng nhất thắng)
    editable_fields: dict[str, set] = {}
    for rid, section, fname in rows.grants:
        if rid in wanted:
            editable_fields.setdefault(section, set()).add(fname)

    return EffectivePermissions(
        permission_codes=frozenset(codes),
        scope_by_section=scope_by_section,
        field_policy=field_policy,
        editable_fields={s: frozenset(f) for s, f in editable_fields.items()},
        is_superuser=False,
    )


def _compute(user) -> EffectivePermissions:
    role_ids = list(
        Role.objects.filter(user_assignments__user=user, is_active=True)
        .distinct()
        .values_list("id", flat=True)
    )
    if not role_ids:
        return EffectivePermissions()
    return _build_effective(role_ids, _load_role_rows(role_ids))


def _cache_key(version: int, user_id: int) -> str:
    return f"rbac:eff:{RBAC_CACHE_SHAPE_VERSION}:{version}:{user_id}"


def resolve_effective_permissions(user) -> EffectivePermissions:
    if not user or not getattr(user, "is_authenticated", False):
        return EffectivePermissions()

    version = get_rbac_cache_version()
    cache_key = _cache_key(version, user.id)
    cached = cache.get(cache_key)
    if cached is not None:
        return EffectivePermissions.from_cache(cached)

    eff = _compute(user)
    cache.set(cache_key, eff.to_cache(), timeout=60 * 30)
    return eff


def resolve_effective_permissions_bulk(users) -> dict:
    """Quyền hiệu dụng của NHIỀU user: ``{user_id: EffectivePermissions}``.

    Số query KHÔNG phụ thuộc số user (audit 2026-09-23 T1 §2.2 — trước: 5 SQL +
    2 Redis GET mỗi user khi cache lạnh, xảy ra cho MỌI user ngay sau bất kỳ
    post_save role/permission nào — đúng lúc admin ở trang Phân quyền):
    1 ``cache.get_many`` cho toàn bộ user → phần miss: 1 query gán role
    (``user_id__in``) + 4 query rule theo ``role_id__in`` → hợp từng user bằng
    Python → 1 ``cache.set_many``. Cùng khoá/TTL với ``resolve_effective_permissions``
    nên hai đường dùng chung entry.
    """
    users = [u for u in users if u and getattr(u, "is_authenticated", False)]
    if not users:
        return {}
    version = get_rbac_cache_version()
    key_by_user_id = {u.id: _cache_key(version, u.id) for u in users}
    cached = cache.get_many(list(key_by_user_id.values()))
    result: dict = {}
    missing_ids: list[int] = []
    for user_id, key in key_by_user_id.items():
        data = cached.get(key)
        if data is not None:
            result[user_id] = EffectivePermissions.from_cache(data)
        else:
            missing_ids.append(user_id)
    if not missing_ids:
        return result

    from .models import UserRoleAssignment

    role_ids_by_user: dict[int, list[int]] = {uid: [] for uid in missing_ids}
    for user_id, role_id in UserRoleAssignment.objects.filter(
        user_id__in=missing_ids
    ).values_list("user_id", "role_id"):
        role_ids_by_user[user_id].append(role_id)
    all_role_ids = sorted({rid for ids in role_ids_by_user.values() for rid in ids})
    rows = _load_role_rows(all_role_ids)

    to_cache: dict = {}
    for user_id in missing_ids:
        eff = _build_effective(role_ids_by_user[user_id], rows)
        result[user_id] = eff
        to_cache[key_by_user_id[user_id]] = eff.to_cache()
    cache.set_many(to_cache, timeout=60 * 30)
    return result


from django.db.models.signals import post_delete, post_save  # noqa: E402

from .models import (  # noqa: E402
    RoleFieldGrant as _RoleFieldGrant,
    RoleFieldRule as _RoleFieldRule,
    RolePermission as _RolePermission,
    RoleSectionScope as _RoleSectionScope,
    UserDepartment as _UserDepartment,
    UserRoleAssignment as _UserRoleAssignment,
)


def _bust(*args, **kwargs):
    bump_rbac_cache_version()


for _model in (
    _UserRoleAssignment,
    _RolePermission,
    _RoleSectionScope,
    _RoleFieldRule,
    _RoleFieldGrant,
    Role,
    _UserDepartment,
):
    post_save.connect(_bust, sender=_model, dispatch_uid=f"rbac_bust_save_{_model.__name__}")
    post_delete.connect(_bust, sender=_model, dispatch_uid=f"rbac_bust_del_{_model.__name__}")


def _connect_user_channel_signals():
    # UserChannel thuộc apps.channels_core — import trong hàm để tránh vòng import lúc app load.
    from apps.channels_core.models import UserChannel

    post_save.connect(_bust, sender=UserChannel, dispatch_uid="rbac_bust_save_UserChannel")
    post_delete.connect(_bust, sender=UserChannel, dispatch_uid="rbac_bust_del_UserChannel")
