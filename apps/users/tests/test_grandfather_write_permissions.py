"""Test migration users/0039 — grandfather quyền GHI (sweep N2 giai đoạn 2).

Gọi thẳng hàm forward của migration với app registry THẬT (migration dùng
apps.get_model nên nhận registry nào cũng chạy) — không cần MigratorTestCase.
Điểm khoá của contract:
- Role có `{section}:view` nhận đủ action ghi của section đó.
- Role KHÔNG có view của section thì không nhận gì của section đó.
- Role is_system không bị đụng tới.
- Idempotent: chạy 2 lần không nhân đôi.
- `returns:create` KHÔNG được cấp (đợt 1 đã siết không grandfather — cấp bây
  giờ là NỚI hành vi prod).
- `call-quality:edit` (action mới) được tạo Permission row ngay trong migration.
"""

from __future__ import annotations

from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase

from apps.users.models import Permission, Role, RolePermission
from apps.users.rbac_catalog import sync_permissions

_migration = import_module("apps.users.migrations.0039_grandfather_write_permissions")


def _codes(role: Role) -> set[str]:
    return set(
        RolePermission.objects.filter(role=role).values_list(
            "permission__code", flat=True
        )
    )


def _grant(role: Role, *codes: str) -> None:
    for code in codes:
        RolePermission.objects.create(
            role=role, permission=Permission.objects.get(code=code)
        )


class GrandfatherWritePermissionsMigrationTests(TestCase):
    def setUp(self):
        sync_permissions()

    def run_forward(self):
        _migration.grandfather(django_apps, None)

    def test_role_with_view_gets_all_grandfathered_actions(self):
        role = Role.objects.create(name="gf-sale")
        _grant(role, "orders:view", "contacts:view", "floating-pool:view")

        self.run_forward()

        codes = _codes(role)
        assert {"orders:create", "orders:edit", "orders:delete"} <= codes
        assert "contacts:edit" in codes
        assert "floating-pool:edit" in codes
        # Không lan sang section role KHÔNG có view.
        assert not any(c.startswith(("stock", "shipments", "koc-orders")) for c in codes)

    def test_kho_staff_role_gets_stock_shipments_documents(self):
        role = Role.objects.create(name="gf-kho")
        _grant(role, "stock:view", "shipments:view", "stock-documents:view", "returns:view")

        self.run_forward()

        codes = _codes(role)
        assert "stock:edit" in codes
        assert "shipments:edit" in codes
        assert {"stock-documents:create", "stock-documents:edit"} <= codes
        # returns:create/edit CỐ Ý không grandfather (đợt 1 đã siết returns:create).
        assert "returns:create" not in codes
        assert "returns:edit" not in codes

    def test_role_without_view_gets_nothing(self):
        role = Role.objects.create(name="gf-nothing")
        _grant(role, "dashboard:view")  # section ngoài danh sách grandfather

        self.run_forward()

        assert _codes(role) == {"dashboard:view"}

    def test_is_system_role_untouched(self):
        admin = Role.objects.create(name="gf-admin", is_system=True)
        _grant(admin, "orders:view")

        self.run_forward()

        assert _codes(admin) == {"orders:view"}

    def test_idempotent_second_run_adds_nothing(self):
        role = Role.objects.create(name="gf-idem")
        _grant(role, "orders:view")

        self.run_forward()
        first = RolePermission.objects.filter(role=role).count()
        self.run_forward()

        assert RolePermission.objects.filter(role=role).count() == first

    def test_call_quality_edit_permission_row_created(self):
        # Mô phỏng prod trước sync catalog: chưa có call-quality:edit trong bảng.
        Permission.objects.filter(code="call-quality:edit").delete()
        role = Role.objects.create(name="gf-cq-lead")
        _grant(role, "call-quality:view")

        self.run_forward()

        assert Permission.objects.filter(
            code="call-quality:edit", section="call-quality", action="edit"
        ).exists()
        assert "call-quality:edit" in _codes(role)

    def test_pairs_match_catalog_actions(self):
        """Mọi cặp grandfather phải là action CÓ THẬT trong PERMISSION_CATALOG —
        cặp gõ nhầm sẽ seed Permission mồ côi mà không gate endpoint nào."""
        from apps.users.rbac_catalog import PERMISSION_CATALOG

        for section, action in _migration.GRANDFATHER_PAIRS:
            assert action in PERMISSION_CATALOG.get(section, ()), (section, action)
