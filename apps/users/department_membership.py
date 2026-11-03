"""Nhận diện "user thuộc phòng ban X" theo CẢ phòng chính lẫn phòng ban nhiều.

Một người có thể kiêm nhiều vai trò — vd NV Sale kiêm chạy ads cho 1 page: phòng
CHÍNH (`User.department` FK) = Sale để đơn của họ vào báo cáo sale, nhưng phòng ban
NHIỀU (`UserDepartment` M2M) có thêm Ads để họ được gán vào kênh + kênh đó vào báo
cáo. Mọi kiểm tra "là NV phòng X" nên dùng helper này thay cho so sánh cứng
``user.department_id == X`` (chỉ nhìn phòng chính).
"""
from __future__ import annotations

from django.db.models import Q

from .models import UserDepartment


def user_in_department(user, department_id: int) -> bool:
    """True nếu user thuộc phòng ``department_id`` (phòng chính FK HOẶC M2M)."""
    if getattr(user, "department_id", None) == department_id:
        return True
    return UserDepartment.objects.filter(user=user, department_id=department_id).exists()


def department_member_q(department_id: int, *, prefix: str = "") -> Q:
    """Q lọc "user thuộc phòng" (phòng chính hoặc M2M).

    ``prefix=""`` khi lọc trực tiếp trên ``User``; ``prefix="user__"`` khi lọc từ
    model tham chiếu tới User (vd ``UserChannel``). Nhớ ``.distinct()`` ở caller vì
    join M2M có thể nhân dòng.
    """
    return Q(**{f"{prefix}department_id": department_id}) | Q(
        **{f"{prefix}department_links__department_id": department_id}
    )
