from __future__ import annotations
from datetime import date
from apps.hr.models import LeaveStatus


class LeaveRuleError(ValueError):
    pass


def count_leave_days(start: date, end: date) -> int:
    if end < start:
        raise LeaveRuleError("Ngày kết thúc phải >= ngày bắt đầu.")
    return (end - start).days + 1


def assert_can_decide(status: str) -> None:
    if status != LeaveStatus.PENDING:
        raise LeaveRuleError("Chỉ đơn đang chờ duyệt mới được duyệt/từ chối.")


def assert_not_self_approval(leave_employee_id: int, approver_employee_id: int | None) -> None:
    """Chặn tự duyệt đơn nghỉ của chính mình.

    `approver_employee_id` là None khi tài khoản duyệt chưa gắn hồ sơ nhân sự (vd
    admin không phải nhân viên) — không có gì để tự-so trùng nên cho qua.
    """
    if approver_employee_id is not None and approver_employee_id == leave_employee_id:
        raise LeaveRuleError("Không thể tự duyệt đơn của chính mình.")
