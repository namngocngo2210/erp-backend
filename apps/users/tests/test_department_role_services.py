from django.test import TestCase

from apps.channels_core.models import Channel
from apps.users.models import Department, Role, RolePermission
from apps.users.rbac_catalog import sync_permissions
from apps.users.services import DepartmentService, RoleService


class DepartmentServiceTests(TestCase):
    def test_create_department_happy(self):
        dept = DepartmentService.create_department({"name": "Kho Test", "is_active": True})
        self.assertIsNotNone(dept.id)
        self.assertEqual(dept.name, "Kho Test")
        self.assertTrue(dept.is_active)

    def test_create_department_with_channels(self):
        ch1 = Channel.objects.create(code="dept-svc-c1", name="C1")
        ch2 = Channel.objects.create(code="dept-svc-c2", name="C2")
        dept = DepartmentService.create_department(
            {"name": "Ads Test", "channel_ids": [ch1.id, ch2.id]}
        )
        self.assertEqual(set(dept.channels.values_list("id", flat=True)), {ch1.id, ch2.id})

    def test_create_department_duplicate_name_raises(self):
        Department.objects.create(name="Trung Ten")
        with self.assertRaises(ValueError) as ctx:
            DepartmentService.create_department({"name": "Trung Ten"})
        self.assertEqual(str(ctx.exception), DepartmentService.DUPLICATE_NAME_MESSAGE)

    def test_update_department_rename_and_channels(self):
        dept = Department.objects.create(name="Cu")
        ch = Channel.objects.create(code="dept-svc-c3", name="C3")
        updated = DepartmentService.update_department(
            dept, {"name": "Moi", "is_active": False, "channel_ids": [ch.id]}
        )
        updated.refresh_from_db()
        self.assertEqual(updated.name, "Moi")
        self.assertFalse(updated.is_active)
        self.assertEqual(list(updated.channels.values_list("id", flat=True)), [ch.id])

    def test_update_department_duplicate_name_raises(self):
        Department.objects.create(name="Ton Tai")
        dept = Department.objects.create(name="Khac")
        with self.assertRaises(ValueError):
            DepartmentService.update_department(dept, {"name": "Ton Tai"})
        dept.refresh_from_db()
        self.assertEqual(dept.name, "Khac")

    def test_delete_department(self):
        dept = Department.objects.create(name="Xoa Toi")
        DepartmentService.delete_department(dept)
        self.assertFalse(Department.objects.filter(name="Xoa Toi").exists())


class RoleServiceCrudTests(TestCase):
    def setUp(self):
        sync_permissions()

    def test_create_role_happy_with_permissions(self):
        role = RoleService.create_role(
            {
                "name": "Sale Lead SVC",
                "scope_default": "department",
                "permission_codes": ["orders:view"],
            }
        )
        self.assertIsNotNone(role.id)
        self.assertEqual(role.scope_default, "department")
        self.assertEqual(
            list(
                RolePermission.objects.filter(role=role).values_list(
                    "permission__code", flat=True
                )
            ),
            ["orders:view"],
        )

    def test_create_role_duplicate_name_raises(self):
        Role.objects.create(name="Trung Role")
        with self.assertRaises(ValueError) as ctx:
            RoleService.create_role({"name": "Trung Role"})
        self.assertEqual(str(ctx.exception), RoleService.DUPLICATE_NAME_MESSAGE)

    def test_update_role_fields_and_permissions(self):
        role = Role.objects.create(name="Role Cu", scope_default="own")
        updated = RoleService.update_role(
            role,
            {
                "name": "Role Moi",
                "description": "mo ta",
                "is_active": False,
                "permission_codes": ["orders:view", "orders:edit"],
            },
        )
        updated.refresh_from_db()
        self.assertEqual(updated.name, "Role Moi")
        self.assertEqual(updated.description, "mo ta")
        self.assertFalse(updated.is_active)
        self.assertEqual(
            set(
                RolePermission.objects.filter(role=updated).values_list(
                    "permission__code", flat=True
                )
            ),
            {"orders:view", "orders:edit"},
        )

    def test_update_role_duplicate_name_raises(self):
        Role.objects.create(name="Da Co")
        role = Role.objects.create(name="Role Rieng")
        with self.assertRaises(ValueError):
            RoleService.update_role(role, {"name": "Da Co"})
        role.refresh_from_db()
        self.assertEqual(role.name, "Role Rieng")

    def test_delete_role(self):
        role = Role.objects.create(name="Role Xoa")
        RoleService.delete_role(role)
        self.assertFalse(Role.objects.filter(name="Role Xoa").exists())
