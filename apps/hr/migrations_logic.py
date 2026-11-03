from __future__ import annotations

from apps.hr.domain.employee_code import next_employee_code

DEFAULT_BOT_USERNAMES = {"lark"}


def backfill_employees(
    User, Department, Employee, EmployeeAccount, *, bot_usernames=None
) -> int:
    """Tạo hồ sơ ``Employee`` cho mỗi ``User`` đang hoạt động chưa có hồ sơ.

    Quan hệ 1-N: mỗi User được gắn vào Employee qua ``EmployeeAccount`` (tài
    khoản chính). Bỏ qua user đã gắn tài khoản và các bot.
    """
    bot_usernames = bot_usernames if bot_usernames is not None else DEFAULT_BOT_USERNAMES
    linked_user_ids = set(EmployeeAccount.objects.values_list("user_id", flat=True))
    latest = _latest_nv_code(Employee)
    created = 0
    qs = User.objects.filter(is_active=True, deleted_at__isnull=True).order_by("id")
    for user in qs:
        if user.id in linked_user_ids or user.username in bot_usernames:
            continue
        latest = next_employee_code(latest)
        full_name = (user.display_name or "").strip() or (
            f"{user.first_name} {user.last_name}".strip()
        ) or user.username
        emp = Employee.objects.create(
            employee_code=latest,
            full_name=full_name,
            department_id=user.department_id,
            employment_status="active",
        )
        EmployeeAccount.objects.create(employee=emp, user=user, is_primary=True)
        created += 1
    return created


def _latest_nv_code(Employee) -> str | None:
    """Mã NV lớn nhất theo prefix NV (giữ chuỗi auto-gen NV độc lập với mã D)."""
    return (
        Employee.objects.filter(employee_code__startswith="NV")
        .order_by("-employee_code")
        .values_list("employee_code", flat=True)
        .first()
    )
