"""Helper dùng chung cho test HR sau khi Employee↔User chuyển sang 1-N.

Trước đây ``Employee.objects.create(user=...)`` gắn trực tiếp 1-1; nay tài khoản
gắn qua ``EmployeeAccount``. Helper giữ test ngắn gọn và đúng quan hệ mới.
"""

from __future__ import annotations

from apps.hr.models import Employee, EmployeeAccount


def create_employee(*, user=None, **kwargs) -> Employee:
    """Tạo Employee, và nếu có ``user`` thì gắn làm tài khoản chính."""
    emp = Employee.objects.create(**kwargs)
    if user is not None:
        EmployeeAccount.objects.create(employee=emp, user=user, is_primary=True)
    return emp
