"""Command rà data-scope RBAC (audit 2026-07-26).

Command này là công cụ người vận hành chạy trên prod để lấy ma trận role × section ×
scope — thứ không đọc được từ code vì role sinh động theo phòng ban. Test ở đây giữ
cho nó không hỏng âm thầm khi ai đó đổi model RBAC, và khoá hai kết luận nghiệp vụ
mà command dựa vào (`MUST_BE_ALL`, `SCOPE_AWARE_SECTIONS`).
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.users.management.commands.audit_rbac_scope import (
    MUST_BE_ALL,
    SCOPE_AWARE_SECTIONS,
)
from apps.users.models import (
    DataScope,
    Permission,
    Role,
    RolePermission,
    RoleSectionScope,
)


def _permission(section: str, action: str = "view") -> Permission:
    # Permission có unique (section, action) và migration đã seed bộ chuẩn — lấy lại
    # bản đã seed thay vì tạo mới (sẽ vỡ constraint).
    existing = Permission.objects.filter(section=section, action=action).first()
    if existing is not None:
        return existing
    return Permission.objects.create(
        code=f"{section}:{action}", section=section, action=action
    )


class AuditRbacScopeCommandTests(TestCase):
    def _run(self, **kwargs) -> str:
        out = StringIO()
        call_command("audit_rbac_scope", stdout=out, **kwargs)
        return out.getvalue()

    def test_flags_narrow_scope_on_section_that_would_break_business(self):
        """landing-orders/shipments/warranty với scope hẹp phải bị gắn cờ NGUY HIỂM."""
        role = Role.objects.create(name="rbac-audit-narrow", scope_default=DataScope.OWN)
        RolePermission.objects.create(role=role, permission=_permission("landing-orders"))

        output = self._run(role="rbac-audit-narrow")

        self.assertIn("landing-orders", output)
        self.assertIn("NGUY HIỂM", output)
        # Lý do phải được in ra, không chỉ nhãn — người vận hành cần biết vì sao.
        self.assertIn("POOL CHUNG", output)

    def test_flags_all_scope_on_section_that_should_be_narrowed(self):
        """orders=all nghĩa là record-scope không lọc gì — phải nói thẳng."""
        role = Role.objects.create(name="rbac-audit-wide", scope_default=DataScope.ALL)
        RolePermission.objects.create(role=role, permission=_permission("orders"))

        output = self._run(role="rbac-audit-wide")

        self.assertIn("orders", output)
        self.assertIn("không siết gì", output)

    def test_narrow_scope_on_normal_section_is_not_flagged(self):
        """orders=own là cấu hình MONG MUỐN → không được kêu."""
        role = Role.objects.create(name="rbac-audit-good", scope_default=DataScope.OWN)
        RolePermission.objects.create(role=role, permission=_permission("orders"))

        output = self._run(role="rbac-audit-good")

        self.assertNotIn("NGUY HIỂM", output)
        self.assertNotIn("không siết gì", output)

    def test_section_scope_override_beats_role_default(self):
        role = Role.objects.create(name="rbac-audit-override", scope_default=DataScope.OWN)
        RolePermission.objects.create(role=role, permission=_permission("landing-orders"))
        RoleSectionScope.objects.create(
            role=role, section="landing-orders", scope=DataScope.ALL
        )

        output = self._run(role="rbac-audit-override")

        self.assertIn("override", output)
        self.assertNotIn("NGUY HIỂM", output)

    def test_system_role_reported_as_bypassing_scope(self):
        """Role is_system short-circuit ở resolver → scope không được đọc tới."""
        role = Role.objects.filter(is_system=True).first()
        self.assertIsNotNone(role, "migration 0015 phải seed sẵn role is_system")

        output = self._run(role=role.name)

        self.assertIn("BỎ QUA MỌI SCOPE", output)

    def test_must_be_all_sections_are_all_scope_aware(self):
        """Không được cảnh báo về một section mà code không hề resolve scope."""
        for section in MUST_BE_ALL:
            self.assertIn(section, SCOPE_AWARE_SECTIONS, f"{section} không scope-aware")

    # Test đối chiếu SCOPE_AWARE_SECTIONS với _SECTION_ORDER_IDENTITY_Q nằm ở
    # apps/orders/tests/test_rbac_scope_aware_sections.py — KHÔNG đặt được ở đây vì
    # `apps.users` nằm DƯỚI `apps.orders` trong contract phân tầng (.importlinter),
    # import ngược lên là vi phạm.
