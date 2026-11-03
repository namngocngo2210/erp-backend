from __future__ import annotations
from datetime import date
from django.db import transaction
from django.utils import timezone
from apps.common.events import event_bus
from apps.hr.contracts.hr_events import LeaveApproved
from apps.hr.domain.leave import assert_can_decide, assert_not_self_approval, count_leave_days
from apps.hr.models import EmployeeAccount, LeaveRequest, LeaveStatus
from apps.hr.repositories import LeaveRepository


def _employee_id_for_user(user_id: int) -> int | None:
    """Nhân sự gắn với tài khoản `user_id` (quan hệ 1-N qua EmployeeAccount)."""
    return (
        EmployeeAccount.objects.filter(user_id=user_id)
        .values_list("employee_id", flat=True)
        .first()
    )


class LeaveService:
    @staticmethod
    @transaction.atomic
    def request(employee_id: int, *, leave_type: str, start_date: date,
                end_date: date, reason: str = "") -> LeaveRequest:
        days = count_leave_days(start_date, end_date)
        return LeaveRequest.objects.create(
            employee_id=employee_id, leave_type=leave_type, start_date=start_date,
            end_date=end_date, days=days, reason=reason, status=LeaveStatus.PENDING)

    @staticmethod
    @transaction.atomic
    def approve(leave_id: int, *, approver_id: int) -> LeaveRequest:
        lr = LeaveRepository.active().select_for_update().get(pk=leave_id)
        assert_can_decide(lr.status)
        assert_not_self_approval(lr.employee_id, _employee_id_for_user(approver_id))
        lr.status = LeaveStatus.APPROVED
        lr.approver_id = approver_id
        lr.decided_at = timezone.now()
        lr.save(update_fields=["status", "approver_id", "decided_at"])
        event_bus.publish_on_commit(LeaveApproved(
            leave_id=lr.id, employee_id=lr.employee_id, days=float(lr.days)))
        return lr

    @staticmethod
    @transaction.atomic
    def reject(leave_id: int, *, approver_id: int) -> LeaveRequest:
        lr = LeaveRepository.active().select_for_update().get(pk=leave_id)
        assert_can_decide(lr.status)
        assert_not_self_approval(lr.employee_id, _employee_id_for_user(approver_id))
        lr.status = LeaveStatus.REJECTED
        lr.approver_id = approver_id
        lr.decided_at = timezone.now()
        lr.save(update_fields=["status", "approver_id", "decided_at"])
        return lr
