from django.test import TestCase

from apps.common.events import event_bus
from apps.hr.application.services.employee_service import (
    DuplicateEmployeeCodeError,
    EmployeeService,
)
from apps.hr.contracts.hr_events import EmployeeDepartmentChanged, EmployeeResigned
from apps.hr.repositories import EmployeeRepository
from apps.hr.domain.org_chart import OrgChartCycleError
from apps.hr.models import EmployeeAccount, EmploymentStatus
from apps.users.models import Department, User


class EmployeeServiceTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Sales")
        self.events = []
        event_bus.subscribe(
            EmployeeDepartmentChanged.event_type, lambda e: self.events.append(e)
        )

    def tearDown(self):
        event_bus.clear()

    def test_create_makes_user_and_employee(self):
        emp = EmployeeService.create(
            full_name="A", department_id=self.dept.id, create_login=False,
            username="nva", job_title="Sale",
        )
        self.assertTrue(emp.employee_code.startswith("NV"))
        user = emp.primary_user
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_active)

    def test_create_login_user_active(self):
        emp = EmployeeService.create(
            full_name="B", department_id=self.dept.id, create_login=True,
            username="nvb", password="secret123",
        )
        user = emp.primary_user
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("secret123"))

    def test_change_department_publishes_event(self):
        emp = EmployeeService.create(
            full_name="C", department_id=self.dept.id, create_login=False, username="nvc"
        )
        other = Department.objects.create(name="Booking")
        with self.captureOnCommitCallbacks(execute=True):
            EmployeeService.update(emp.id, {"department_id": other.id})
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0].department_id, other.id)
        self.assertEqual(self.events[0].user_id, emp.primary_user.id)

    def test_self_manager_rejected(self):
        emp = EmployeeService.create(
            full_name="D", department_id=self.dept.id, create_login=False, username="nvd"
        )
        with self.assertRaises(OrgChartCycleError):
            EmployeeService.update(emp.id, {"manager_id": emp.id})

    def test_delete_soft_deletes_and_deactivates_user(self):
        resigned = []
        event_bus.subscribe(
            EmployeeResigned.event_type, lambda e: resigned.append(e)
        )
        emp = EmployeeService.create(
            full_name="F", department_id=self.dept.id, create_login=True,
            username="nvf", password="x123",
        )
        with self.captureOnCommitCallbacks(execute=True):
            EmployeeService.delete(emp.id)
        self.assertIsNone(EmployeeRepository.active().filter(pk=emp.id).first())
        emp.refresh_from_db()
        self.assertIsNotNone(emp.deleted_at)
        self.assertFalse(emp.primary_user.is_active)
        self.assertEqual(len(resigned), 1)
        self.assertEqual(resigned[0].employee_id, emp.id)

    def test_create_with_custom_code(self):
        emp = EmployeeService.create(
            full_name="A", department_id=None, username="nva", employee_code="EMP123"
        )
        self.assertEqual(emp.employee_code, "EMP123")

    def test_create_blank_code_autogenerates(self):
        emp = EmployeeService.create(
            full_name="A", department_id=None, username="nva", employee_code=""
        )
        self.assertTrue(emp.employee_code.startswith("NV"))

    def test_create_duplicate_code_rejected(self):
        EmployeeService.create(
            full_name="A", department_id=None, username="nva", employee_code="DUP1"
        )
        with self.assertRaises(DuplicateEmployeeCodeError):
            EmployeeService.create(
                full_name="B", department_id=None, username="nvb", employee_code="DUP1"
            )

    def test_update_changes_code(self):
        emp = EmployeeService.create(
            full_name="A", department_id=None, username="nva"
        )
        EmployeeService.update(emp.id, {"employee_code": "NEW9"})
        emp.refresh_from_db()
        self.assertEqual(emp.employee_code, "NEW9")
        other = EmployeeService.create(
            full_name="B", department_id=None, username="nvb"
        )
        with self.assertRaises(DuplicateEmployeeCodeError):
            EmployeeService.update(other.id, {"employee_code": "NEW9"})

    def test_autogen_ignores_custom_codes(self):
        # A custom code that is lexicographically larger than any NV code
        # must not corrupt the auto-gen sequence nor cause a collision.
        EmployeeService.create(
            full_name="Custom", department_id=None, username="nvz",
            employee_code="Z001",
        )
        emp = EmployeeService.create(
            full_name="Auto", department_id=None, username="nvauto",
            employee_code="",
        )
        self.assertTrue(emp.employee_code.startswith("NV"))

    def test_autogen_continues_from_nv_max_ignoring_custom(self):
        EmployeeService.create(
            full_name="Seed", department_id=None, username="nvseed",
            employee_code="NV0003",
        )
        EmployeeService.create(
            full_name="Custom", department_id=None, username="nvz",
            employee_code="Z001",
        )
        emp = EmployeeService.create(
            full_name="Auto", department_id=None, username="nvauto",
            employee_code="",
        )
        self.assertEqual(emp.employee_code, "NV0004")

    def test_resign_deactivates_user(self):
        emp = EmployeeService.create(
            full_name="E", department_id=self.dept.id, create_login=True,
            username="nve", password="x123",
        )
        with self.captureOnCommitCallbacks(execute=True):
            EmployeeService.resign(emp.id)
        emp.refresh_from_db()
        self.assertEqual(emp.employment_status, EmploymentStatus.RESIGNED)
        self.assertFalse(emp.primary_user.is_active)


class AddUnlinkAccountServiceTests(TestCase):
    def _make_emp(self):
        return EmployeeService.create(
            full_name="Holder", department_id=None, username="primary1",
            create_login=True, password="secret123",
        )

    def test_add_account_links_non_primary_user(self):
        emp = self._make_emp()
        acc = EmployeeService.add_account(
            emp.id,
            {
                "username": "second1", "display_name": "Second", "is_active": True,
                "password": "secret123", "department_id": None, "kiotviet_mappings": [],
            },
        )
        self.assertFalse(acc.is_primary)
        self.assertTrue(User.objects.filter(username="second1").exists())
        self.assertEqual(emp.accounts.count(), 2)

    def test_unlink_keeps_user(self):
        emp = self._make_emp()
        acc = EmployeeService.add_account(
            emp.id,
            {"username": "second1", "display_name": "", "is_active": True,
             "password": "secret123", "department_id": None, "kiotviet_mappings": []},
        )
        EmployeeService.unlink_account(emp.id, acc.id)
        self.assertFalse(EmployeeAccount.objects.filter(id=acc.id).exists())
        self.assertTrue(User.objects.filter(username="second1").exists())  # User kept

    def test_unlink_primary_promotes_oldest_remaining(self):
        emp = self._make_emp()
        primary = emp.accounts.get(is_primary=True)
        acc2 = EmployeeService.add_account(
            emp.id,
            {"username": "second1", "display_name": "", "is_active": True,
             "password": "secret123", "department_id": None, "kiotviet_mappings": []},
        )
        EmployeeService.unlink_account(emp.id, primary.id)
        acc2.refresh_from_db()
        self.assertTrue(acc2.is_primary)

    def test_unlink_unknown_account_raises(self):
        emp = self._make_emp()
        with self.assertRaises(EmployeeAccount.DoesNotExist):
            EmployeeService.unlink_account(emp.id, 999999)

    def test_add_account_is_primary_when_no_existing_accounts(self):
        emp = self._make_emp()
        # Remove the auto-created primary so employee has zero accounts
        emp.accounts.all().delete()
        self.assertEqual(emp.accounts.count(), 0)
        acc = EmployeeService.add_account(
            emp.id,
            {
                "username": "firstnew1", "display_name": "First New", "is_active": True,
                "password": "secret123", "department_id": None, "kiotviet_mappings": [],
            },
        )
        self.assertTrue(acc.is_primary, "First account added to an employee with no accounts must be primary")
