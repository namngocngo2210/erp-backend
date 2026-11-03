from __future__ import annotations

import datetime as dt
from datetime import date

import pytest
from rest_framework.test import APIClient

from apps.hr.application.services.leave_service import LeaveService
from apps.hr.domain.leave import LeaveRuleError
from apps.hr.models import Employee, LeaveRequest, LeaveStatus, LeaveType
from apps.hr.tests.factories import create_employee
from apps.users.models import User

pytestmark = pytest.mark.django_db


def _auth_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def emp_user(db):
    user = User.objects.create_user(username="nv01", password="x")
    emp = create_employee(user=user, full_name="Nguyen Van A", employee_code="NV01")
    return user, emp


def test_me_profile_returns_own_profile(emp_user):
    user, emp = emp_user
    res = _auth_client(user).get("/api/hr/me/profile")
    assert res.status_code == 200
    assert res.data["employee_code"] == "NV01"
    assert "id_number" not in res.data


def test_me_profile_404_when_no_employee():
    user = User.objects.create_user(username="ghost", password="x")
    res = _auth_client(user).get("/api/hr/me/profile")
    assert res.status_code == 404


def test_me_leave_create_and_list_self_only(emp_user):
    user, emp = emp_user
    other_user = User.objects.create_user(username="nv02", password="x")
    other_emp = create_employee(user=other_user, full_name="B", employee_code="NV02")
    LeaveRequest.objects.create(
        employee_id=other_emp.id, leave_type="annual",
        start_date=dt.date(2026, 6, 1), end_date=dt.date(2026, 6, 2), days=2, status="pending",
    )
    client = _auth_client(user)
    create = client.post(
        "/api/hr/me/leave-requests",
        {"leave_type": "annual", "start_date": "2026-06-10",
         "end_date": "2026-06-11", "reason": "viec rieng"},
        format="json",
    )
    assert create.status_code == 201
    listed = client.get("/api/hr/me/leave-requests")
    assert listed.status_code == 200
    emp_ids = {item["employee"] for item in listed.data["items"]}
    assert emp_ids == {emp.id}


def test_me_leave_create_ignores_injected_employee_id(emp_user):
    user, emp = emp_user
    other_user = User.objects.create_user(username="nv03", password="x")
    other_emp = create_employee(user=other_user, full_name="C", employee_code="NV03")
    res = _auth_client(user).post(
        "/api/hr/me/leave-requests",
        {"employee_id": other_emp.id, "leave_type": "annual",
         "start_date": "2026-06-10", "end_date": "2026-06-11"},
        format="json",
    )
    assert res.status_code == 201
    assert LeaveRequest.objects.get(pk=res.data["id"]).employee_id == emp.id


def test_me_leave_create_rejects_end_before_start(emp_user):
    user, emp = emp_user
    res = _auth_client(user).post(
        "/api/hr/me/leave-requests",
        {"leave_type": "annual", "start_date": "2026-06-11",
         "end_date": "2026-06-10", "reason": "viec rieng"},
        format="json",
    )
    assert res.status_code == 400


def test_me_attendance_returns_items(emp_user):
    user, emp = emp_user
    res = _auth_client(user).get("/api/hr/me/attendance?month=2026-06")
    assert res.status_code == 200
    assert "items" in res.data


def _leave_request(employee):
    return LeaveService.request(
        employee.id,
        leave_type=LeaveType.ANNUAL,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 2),
    )


class TestLeaveSelfApprove:
    """Chặn nhân sự tự duyệt/từ chối đơn nghỉ của chính mình (audit followup 2.2)."""

    def test_approve_own_request_raises(self):
        user = User.objects.create_user(username="self-approver", password="x")
        employee = create_employee(user=user, employee_code="NV-SA1", full_name="A")
        lr = _leave_request(employee)

        with pytest.raises(LeaveRuleError, match="tự duyệt"):
            LeaveService.approve(lr.id, approver_id=user.id)

        lr.refresh_from_db()
        assert lr.status == LeaveStatus.PENDING

    def test_reject_own_request_raises(self):
        user = User.objects.create_user(username="self-rejecter", password="x")
        employee = create_employee(user=user, employee_code="NV-SA2", full_name="B")
        lr = _leave_request(employee)

        with pytest.raises(LeaveRuleError, match="tự duyệt"):
            LeaveService.reject(lr.id, approver_id=user.id)

        lr.refresh_from_db()
        assert lr.status == LeaveStatus.PENDING

    def test_manager_can_approve_other_employee_request(self):
        requester = create_employee(employee_code="NV-SA3", full_name="C")
        manager_user = User.objects.create_user(username="manager-sa", password="x")
        create_employee(user=manager_user, employee_code="NV-SA4", full_name="D")
        lr = _leave_request(requester)

        approved = LeaveService.approve(lr.id, approver_id=manager_user.id)

        assert approved.status == LeaveStatus.APPROVED
        assert approved.approver_id == manager_user.id

    def test_manager_can_reject_other_employee_request(self):
        requester = create_employee(employee_code="NV-SA5", full_name="E")
        manager_user = User.objects.create_user(username="manager-sa2", password="x")
        create_employee(user=manager_user, employee_code="NV-SA6", full_name="F")
        lr = _leave_request(requester)

        rejected = LeaveService.reject(lr.id, approver_id=manager_user.id)

        assert rejected.status == LeaveStatus.REJECTED

    def test_approver_without_employee_profile_allowed(self):
        """Admin thuần (chưa gắn hồ sơ NV) vẫn duyệt được — không có gì để tự-so trùng."""
        requester = create_employee(employee_code="NV-SA7", full_name="G")
        admin_user = User.objects.create_user(username="admin-sa", password="x")
        lr = _leave_request(requester)

        approved = LeaveService.approve(lr.id, approver_id=admin_user.id)

        assert approved.status == LeaveStatus.APPROVED
