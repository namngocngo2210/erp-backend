"""Test 2 migration grandfather của audit 2026-09-23 N1/M1.

- users/0051: role đang sửa được địa chỉ (grant `address` hoặc `ward`) nhận thêm
  `address_level2`; reverse xoá đúng grant đó.
- users/0052: role có `pancake-leads:view` nhận `pancake-leads:edit`; Permission row
  được tạo ngay trong migration; is_system không đụng; idempotent.

Gọi thẳng hàm forward/reverse với app registry THẬT (cùng khuôn
test_grandfather_write_permissions.py).
"""

from __future__ import annotations

from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase

from apps.users.models import Permission, Role, RoleFieldGrant, RolePermission
from apps.users.rbac_catalog import ALL_ORDER_FIELDS, PERMISSION_CATALOG, sync_permissions

_level2 = import_module("apps.users.migrations.0051_grandfather_address_level2_field")
_pancake = import_module("apps.users.migrations.0052_seed_pancake_leads_edit_permission")


def _fields(role: Role) -> set[str]:
    return set(
        RoleFieldGrant.objects.filter(role=role, section="orders").values_list("field", flat=True)
    )


def _codes(role: Role) -> set[str]:
    return set(
        RolePermission.objects.filter(role=role).values_list("permission__code", flat=True)
    )


class AddressLevel2GrandfatherTests(TestCase):
    def test_field_is_in_catalog(self):
        assert "address_level2" in ALL_ORDER_FIELDS

    def test_role_with_address_or_ward_gets_level2(self):
        by_address = Role.objects.create(name="gf-addr")
        RoleFieldGrant.objects.create(role=by_address, section="orders", field="address")
        by_ward = Role.objects.create(name="gf-ward")
        RoleFieldGrant.objects.create(role=by_ward, section="orders", field="ward")
        untouched = Role.objects.create(name="gf-money")
        RoleFieldGrant.objects.create(role=untouched, section="orders", field="total_amount")

        _level2.grandfather(django_apps, None)

        assert "address_level2" in _fields(by_address)
        assert "address_level2" in _fields(by_ward)
        assert _fields(untouched) == {"total_amount"}

    def test_idempotent_and_reverse_removes_only_level2(self):
        role = Role.objects.create(name="gf-addr-idem")
        RoleFieldGrant.objects.create(role=role, section="orders", field="address")

        _level2.grandfather(django_apps, None)
        _level2.grandfather(django_apps, None)
        assert RoleFieldGrant.objects.filter(role=role, field="address_level2").count() == 1

        _level2.revoke(django_apps, None)
        assert _fields(role) == {"address"}


class PancakeLeadsEditGrandfatherTests(TestCase):
    def setUp(self):
        sync_permissions()

    def test_action_exists_in_catalog(self):
        assert "edit" in PERMISSION_CATALOG["pancake-leads"]

    def test_role_with_view_gets_edit_and_row_created(self):
        # Mô phỏng prod trước sync catalog: chưa có row :edit.
        Permission.objects.filter(code="pancake-leads:edit").delete()
        role = Role.objects.create(name="gf-pancake")
        RolePermission.objects.create(
            role=role, permission=Permission.objects.get(code="pancake-leads:view")
        )
        other = Role.objects.create(name="gf-pancake-none")
        RolePermission.objects.create(
            role=other, permission=Permission.objects.get(code="orders:view")
        )
        admin = Role.objects.create(name="gf-pancake-admin", is_system=True)
        RolePermission.objects.create(
            role=admin, permission=Permission.objects.get(code="pancake-leads:view")
        )

        _pancake.seed_and_grandfather(django_apps, None)
        _pancake.seed_and_grandfather(django_apps, None)  # idempotent

        assert Permission.objects.filter(
            code="pancake-leads:edit", section="pancake-leads", action="edit"
        ).exists()
        assert _codes(role) == {"pancake-leads:view", "pancake-leads:edit"}
        assert RolePermission.objects.filter(role=role).count() == 2
        assert _codes(other) == {"orders:view"}
        assert _codes(admin) == {"pancake-leads:view"}
