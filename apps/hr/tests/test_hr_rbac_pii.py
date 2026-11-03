from __future__ import annotations

import pytest
from django.test import TestCase
from rest_framework.test import APIClient

from apps.hr.models import Employee, EmployeeAccount
from apps.hr.tests.factories import create_employee
from apps.hr.transport.serializers.employee_serializers import SENSITIVE_EMPLOYEE_FIELDS
from apps.users.models import Department
from apps.users.tests.helpers import make_admin, make_user_with_permissions

pytestmark = pytest.mark.django_db


def _client_for(user) -> APIClient:
    c = APIClient()
    c.force_authenticate(user)
    return c


class TestSalaryEndpointPermissions:
    """RBAC lương/hợp đồng — section `salaries` tách hẳn khỏi `employees`.

    `employees:view` không đủ để XEM lương/hợp đồng (cần `salaries:view`), và từ audit
    2026-07-14 thì `employees:edit` cũng không đủ để GHI lương/ký hợp đồng (cần
    `salaries:edit`): quản lý hồ sơ nhân sự và quyết định lương là hai việc khác nhau.
    """

    @pytest.mark.parametrize(
        ("code", "perms", "status"),
        [
            # employees:view (nhưng KHÔNG có salaries:view) — trước fix sẽ pass qua được.
            ("NV-SAL1", ["employees:view"], 403),
            ("NV-SAL2", ["salaries:view"], 200),
            ("NV-SAL3", "admin", 200),
        ],
        ids=["employees_view_only_403", "salaries_view_200", "admin_200"],
    )
    def test_get_salaries(self, code, perms, status):
        employee = create_employee(employee_code=code, full_name="A")
        user = make_admin("hr-admin-salaries") if perms == "admin" else make_user_with_permissions(
            f"hr-get-salaries-{code}", perms
        )
        resp = _client_for(user).get(f"/api/hr/employees/{employee.id}/salaries")
        assert resp.status_code == status
        if status == 200:
            assert resp.data["items"] == []

    @pytest.mark.parametrize(
        ("code", "perms", "status"),
        [
            # Xem được lương không có nghĩa là đặt được lương.
            ("NV-SAL4", ["salaries:view"], 403),
            # employees:edit = quản lý hồ sơ, KHÔNG được đặt lương (audit 2026-07-14).
            ("NV-SAL5", ["employees:edit"], 403),
            ("NV-SAL6", ["salaries:edit"], 201),
        ],
        ids=["salaries_view_only_403", "employees_edit_403", "salaries_edit_201"],
    )
    def test_post_salaries(self, code, perms, status):
        employee = create_employee(employee_code=code, full_name="D")
        user = make_user_with_permissions(f"hr-post-salaries-{code}", perms)
        resp = _client_for(user).post(
            f"/api/hr/employees/{employee.id}/salaries",
            {"amount": 1000, "effective_from": "2026-08-01", "reason": "raise"},
            format="json",
        )
        assert resp.status_code == status


class TestContractEndpointPermissions:
    @pytest.mark.parametrize(
        ("code", "perms", "status"),
        [("NV-CON1", ["employees:view"], 403), ("NV-CON2", ["salaries:view"], 200)],
        ids=["employees_view_only_403", "salaries_view_200"],
    )
    def test_get_contracts(self, code, perms, status):
        employee = create_employee(employee_code=code, full_name="F")
        user = make_user_with_permissions(f"hr-get-contracts-{code}", perms)
        resp = _client_for(user).get(f"/api/hr/employees/{employee.id}/contracts")
        assert resp.status_code == status
        if status == 200:
            assert resp.data["items"] == []


class EmployeePiiVisibilityTests(TestCase):
    """PII hồ sơ nhân sự chỉ lộ cho người QUẢN LÝ hồ sơ hoặc chính chủ.

    `employees:view` được cấp rộng (sơ đồ tổ chức, danh bạ, chọn nhân sự trong form). Trả
    nguyên CCCD / ngày sinh / địa chỉ thường trú / STK ngân hàng / MST / sổ BHXH cho ngần ấy
    người là rò rỉ PII — và làm gate `salaries` (chặn đường lương) chỉ đúng một nửa, vì số
    tài khoản vẫn lấy được qua /api/hr/employees (audit 2026-07-14 vòng 3).
    """

    def setUp(self):
        self.client = APIClient()
        self.dept = Department.objects.create(name="Kho")
        self.employee = Employee.objects.create(
            employee_code="NV999",
            full_name="Nguyen Van PII",
            department=self.dept,
            id_number="001199001234",
            permanent_address="123 Duong X",
            tax_code="8123456789",
            social_insurance_no="SI-123",
            bank_account_no="0123456789",
            bank_name="VCB",
        )

    def _get_detail(self, user):
        self.client.force_authenticate(user=user)
        resp = self.client.get(f"/api/hr/employees/{self.employee.id}")
        self.assertEqual(resp.status_code, 200)
        return resp.data

    def test_viewer_without_edit_cannot_read_sensitive_fields(self):
        viewer = make_user_with_permissions("hr_viewer_only", ["employees:view"])

        data = self._get_detail(viewer)

        self.assertEqual(data["full_name"], "Nguyen Van PII")
        for field in SENSITIVE_EMPLOYEE_FIELDS:
            self.assertIsNone(data[field], f"{field} không được lộ cho employees:view")

    def test_hr_with_edit_reads_sensitive_fields(self):
        hr = make_user_with_permissions(
            "hr_manager", ["employees:view", "employees:edit"]
        )

        data = self._get_detail(hr)

        self.assertEqual(data["bank_account_no"], "0123456789")
        self.assertEqual(data["id_number"], "001199001234")

    def test_admin_reads_sensitive_fields(self):
        data = self._get_detail(make_admin("hr_pii_admin"))

        self.assertEqual(data["bank_account_no"], "0123456789")

    def test_owner_reads_own_sensitive_fields(self):
        me = make_user_with_permissions("hr_self", ["employees:view"])
        EmployeeAccount.objects.create(
            employee=self.employee, user=me, is_primary=True
        )

        data = self._get_detail(me)

        self.assertEqual(data["bank_account_no"], "0123456789")
        self.assertEqual(data["tax_code"], "8123456789")

    def test_list_endpoint_also_masks(self):
        viewer = make_user_with_permissions("hr_viewer_list", ["employees:view"])
        self.client.force_authenticate(user=viewer)

        resp = self.client.get("/api/hr/employees?limit=20&offset=0")

        self.assertEqual(resp.status_code, 200)
        row = resp.data["items"][0]
        self.assertIsNone(row["bank_account_no"])
        self.assertIsNone(row["id_number"])
