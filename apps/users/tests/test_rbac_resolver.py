from django.test import TestCase

from apps.channels_core.models import Channel, UserChannel
from apps.users.models import (
    DataScope,
    Department,
    FieldRuleType,
    Permission,
    Role,
    RoleFieldGrant,
    RoleFieldRule,
    RolePermission,
    RoleSectionScope,
    User,
    UserDepartment,
    UserRoleAssignment,
)
from apps.users.rbac import (
    RBAC_CACHE_SHAPE_VERSION,
    EffectivePermissions,
    bump_rbac_cache_version,
    get_rbac_cache_version,
    resolve_effective_permissions,
)
from apps.users.scoping import resolved_channel_codes
from apps.users.tests.helpers import make_admin


def _perm(code):
    section, action = code.split(":", 1)
    return Permission.objects.get_or_create(
        code=code, defaults={"section": section, "action": action}
    )[0]


def _grant(role, *codes):
    for code in codes:
        RolePermission.objects.create(role=role, permission=_perm(code))


class ResolverUnionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u1", password="x")

    def test_permission_codes_are_union_across_roles(self):
        r1 = Role.objects.create(name="r1", scope_default=DataScope.OWN)
        r2 = Role.objects.create(name="r2", scope_default=DataScope.OWN)
        _grant(r1, "orders:view")
        _grant(r2, "orders:edit", "customers:view")
        UserRoleAssignment.objects.create(user=self.user, role=r1)
        UserRoleAssignment.objects.create(user=self.user, role=r2)

        eff = resolve_effective_permissions(self.user)
        self.assertTrue(eff.can("orders:view"))
        self.assertTrue(eff.can("orders:edit"))
        self.assertTrue(eff.can("customers:view"))
        self.assertFalse(eff.can("orders:delete"))

    def test_scope_is_widest_across_roles(self):
        r_own = Role.objects.create(name="own", scope_default=DataScope.OWN)
        r_all = Role.objects.create(name="all", scope_default=DataScope.ALL)
        _grant(r_own, "orders:view")
        _grant(r_all, "orders:view")
        UserRoleAssignment.objects.create(user=self.user, role=r_own)
        UserRoleAssignment.objects.create(user=self.user, role=r_all)

        eff = resolve_effective_permissions(self.user)
        self.assertEqual(eff.scope_for("orders"), DataScope.ALL)

    def test_section_scope_override_beats_role_default(self):
        role = Role.objects.create(name="r", scope_default=DataScope.OWN)
        _grant(role, "orders:view")
        RoleSectionScope.objects.create(role=role, section="orders", scope=DataScope.CHANNEL)
        UserRoleAssignment.objects.create(user=self.user, role=role)

        eff = resolve_effective_permissions(self.user)
        self.assertEqual(eff.scope_for("orders"), DataScope.CHANNEL)

    def test_field_rule_least_restrictive_wins(self):
        r_hidden = Role.objects.create(name="h", scope_default=DataScope.OWN)
        r_readonly = Role.objects.create(name="ro", scope_default=DataScope.OWN)
        _grant(r_hidden, "orders:view")
        _grant(r_readonly, "orders:view")
        RoleFieldRule.objects.create(
            role=r_hidden, section="orders", field="cost_price", rule=FieldRuleType.HIDDEN
        )
        RoleFieldRule.objects.create(
            role=r_readonly, section="orders", field="cost_price", rule=FieldRuleType.READONLY
        )
        UserRoleAssignment.objects.create(user=self.user, role=r_hidden)
        UserRoleAssignment.objects.create(user=self.user, role=r_readonly)

        eff = resolve_effective_permissions(self.user)
        # readonly ít hạn chế hơn hidden ⇒ field hiệu dụng = readonly
        self.assertEqual(eff.field_policy.get("orders", {}).get("cost_price"), FieldRuleType.READONLY)

    def test_system_role_short_circuits_to_full(self):
        # Tên phải KHÁC "Administrator": migration 0015 đã seed sẵn role đó vào test DB
        # → create() trùng `rbac_roles_name_key`. Short-circuit trong rbac._compute xét
        # cờ is_system chứ không xét tên, nên đổi tên không làm mất ý nghĩa test.
        admin_role = Role.objects.create(
            name="System Role (resolver test)", is_system=True, scope_default=DataScope.ALL
        )
        UserRoleAssignment.objects.create(user=self.user, role=admin_role)

        eff = resolve_effective_permissions(self.user)
        self.assertTrue(eff.is_superuser)
        self.assertTrue(eff.can("anything:at-all"))
        self.assertEqual(eff.scope_for("orders"), DataScope.ALL)

    def test_system_role_admin_short_circuits(self):
        admin = make_admin("legacy-admin")
        eff = resolve_effective_permissions(admin)
        self.assertTrue(eff.is_superuser)


class ResolverEditableFieldsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u-fields", password="x")

    def test_editable_fields_unions_grants_across_roles(self):
        role_a = Role.objects.create(name="Chỉ tiền", scope_default=DataScope.OWN)
        role_b = Role.objects.create(name="Chỉ khách", scope_default=DataScope.OWN)
        RoleFieldGrant.objects.create(role=role_a, section="orders", field="total_amount")
        RoleFieldGrant.objects.create(role=role_b, section="orders", field="phone")
        UserRoleAssignment.objects.create(user=self.user, role=role_a)
        UserRoleAssignment.objects.create(user=self.user, role=role_b)

        eff = resolve_effective_permissions(self.user)

        self.assertEqual(eff.editable_fields["orders"], frozenset({"total_amount", "phone"}))
        self.assertTrue(eff.can_edit_field("orders", "total_amount"))
        self.assertTrue(eff.can_edit_field("orders", "phone"))
        self.assertFalse(eff.can_edit_field("orders", "status"))

    def test_no_grant_means_no_editable_field(self):
        role = Role.objects.create(name="Không grant", scope_default=DataScope.OWN)
        UserRoleAssignment.objects.create(user=self.user, role=role)

        eff = resolve_effective_permissions(self.user)

        self.assertEqual(eff.editable_fields, {})
        self.assertFalse(eff.can_edit_field("orders", "phone"))

    def test_superuser_can_edit_every_field(self):
        admin = make_admin("u-super-field")

        eff = resolve_effective_permissions(admin)

        self.assertTrue(eff.can_edit_field("orders", "total_amount"))
        self.assertTrue(eff.can_edit_field("orders", "bat_ky_field_nao"))


class ResolverCacheTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cacheu", password="x")
        self.role = Role.objects.create(name="cr", scope_default=DataScope.OWN)
        _grant(self.role, "orders:view")
        UserRoleAssignment.objects.create(user=self.user, role=self.role)

    def test_result_is_cached_until_version_bump(self):
        eff1 = resolve_effective_permissions(self.user)
        self.assertTrue(eff1.can("orders:view"))
        # Thêm quyền qua bulk_create (KHÔNG bắn post_save signal → không tự bust),
        # mô phỏng một thay đổi chưa được bust → resolver vẫn đọc cache cũ.
        RolePermission.objects.bulk_create([RolePermission(role=self.role, permission=_perm("orders:edit"))])
        eff_cached = resolve_effective_permissions(self.user)
        self.assertFalse(eff_cached.can("orders:edit"))
        # Bust thủ công → tính lại
        bump_rbac_cache_version()
        eff2 = resolve_effective_permissions(self.user)
        self.assertTrue(eff2.can("orders:edit"))

    def test_cache_key_carries_shape_version(self):
        from django.core.cache import cache

        resolve_effective_permissions(self.user)

        version = get_rbac_cache_version()
        expected = f"rbac:eff:{RBAC_CACHE_SHAPE_VERSION}:{version}:{self.user.id}"
        self.assertIsNotNone(cache.get(expected))

    def test_entry_written_in_legacy_shape_is_never_read(self):
        """Cửa sổ giữa migrate và roll code: code CŨ vẫn phục vụ request và ghi entry
        ở cache-version MỚI nhưng thiếu `editable_fields`, TTL 30'. Code mới đọc trúng
        entry đó = editable_fields rỗng = fail-closed = cả công ty 403 tới nửa tiếng.
        Prefix shape trong key phải làm entry của thế hệ code cũ vô hình."""
        from django.core.cache import cache

        RoleFieldGrant.objects.create(role=self.role, section="orders", field="phone")
        bump_rbac_cache_version()
        version = get_rbac_cache_version()
        # Entry y như code CŨ ghi: key không có prefix shape, payload thiếu key mới.
        cache.set(
            f"rbac:eff:{version}:{self.user.id}",
            {
                "permission_codes": ["orders:view"],
                "scope_by_section": {},
                "field_policy": {},
                "is_superuser": False,
            },
            timeout=60 * 30,
        )

        eff = resolve_effective_permissions(self.user)

        self.assertTrue(eff.can_edit_field("orders", "phone"))


def _role_with_orders(scope_default, section_scope=None):
    role = Role.objects.create(name=f"r-{scope_default}-{section_scope}", scope_default=scope_default)
    perm = Permission.objects.get_or_create(
        code="orders:view", defaults={"section": "orders", "action": "view"}
    )[0]
    RolePermission.objects.create(role=role, permission=perm)
    if section_scope:
        RoleSectionScope.objects.create(role=role, section="orders", scope=section_scope)
    return role


class ResolvedChannelCodesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="x")
        self.ch_a = Channel.objects.create(code="a", name="A")
        self.ch_b = Channel.objects.create(code="b", name="B")

    def test_all_scope_returns_none(self):
        UserRoleAssignment.objects.create(user=self.user, role=_role_with_orders(DataScope.ALL))
        bump_rbac_cache_version()
        self.assertIsNone(resolved_channel_codes(self.user, "orders"))

    def test_channel_scope_returns_assigned_codes(self):
        UserChannel.objects.create(user=self.user, channel=self.ch_a, is_active=True)
        UserRoleAssignment.objects.create(
            user=self.user, role=_role_with_orders(DataScope.OWN, section_scope=DataScope.CHANNEL)
        )
        bump_rbac_cache_version()
        self.assertEqual(resolved_channel_codes(self.user, "orders"), {"a"})

    def test_channel_scope_no_assignment_returns_empty_set(self):
        UserRoleAssignment.objects.create(
            user=self.user, role=_role_with_orders(DataScope.OWN, section_scope=DataScope.CHANNEL)
        )
        bump_rbac_cache_version()
        self.assertEqual(resolved_channel_codes(self.user, "orders"), set())

    def test_department_scope_returns_dept_channel_codes(self):
        dept = Department.objects.create(name="Sales")
        self.ch_a.departments.add(dept)
        UserDepartment.objects.create(user=self.user, department=dept, is_primary=True)
        UserRoleAssignment.objects.create(
            user=self.user, role=_role_with_orders(DataScope.DEPARTMENT)
        )
        bump_rbac_cache_version()
        self.assertEqual(resolved_channel_codes(self.user, "orders"), {"a"})

    def test_own_scope_returns_sentinel(self):
        from apps.users.scoping import OWN_SCOPE_SENTINEL

        UserRoleAssignment.objects.create(user=self.user, role=_role_with_orders(DataScope.OWN))
        bump_rbac_cache_version()
        self.assertIs(resolved_channel_codes(self.user, "orders"), OWN_SCOPE_SENTINEL)

    def test_admin_returns_none(self):
        admin = make_admin("a")
        bump_rbac_cache_version()
        self.assertIsNone(resolved_channel_codes(admin, "orders"))


def test_from_cache_tolerates_payload_without_editable_fields():
    """Entry cache CŨ (TTL 30') không có key này. Đọc bằng data[...] là KeyError
    hàng loạt ngay giây deploy — case này khoá lại đúng cú đó."""
    legacy = {
        "permission_codes": ["orders:view"],
        "scope_by_section": {},
        "field_policy": {},
        "is_superuser": False,
    }

    eff = EffectivePermissions.from_cache(legacy)

    assert eff.editable_fields == {}
    assert eff.can("orders:view")


def test_to_cache_round_trip_keeps_editable_fields():
    eff = EffectivePermissions(editable_fields={"orders": frozenset({"phone"})})

    restored = EffectivePermissions.from_cache(eff.to_cache())

    assert restored.editable_fields == {"orders": frozenset({"phone"})}
