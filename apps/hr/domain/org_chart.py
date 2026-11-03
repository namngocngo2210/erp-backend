from __future__ import annotations


class OrgChartCycleError(ValueError):
    """Gán manager tạo thành vòng trong cây tổ chức."""


def assert_no_cycle(
    *, employee_id: int, new_manager_id: int | None, parent_of: dict[int, int]
) -> None:
    """Raise nếu đặt ``new_manager_id`` làm quản lý của ``employee_id`` tạo vòng.

    ``parent_of`` map employee_id -> manager_id hiện tại (không gồm thay đổi mới).
    """
    if new_manager_id is None:
        return
    if new_manager_id == employee_id:
        raise OrgChartCycleError("Nhân viên không thể tự làm quản lý của mình.")
    cursor: int | None = new_manager_id
    seen: set[int] = set()
    while cursor is not None:
        if cursor == employee_id:
            raise OrgChartCycleError("Gán quản lý này tạo vòng trong sơ đồ tổ chức.")
        if cursor in seen:
            break
        seen.add(cursor)
        cursor = parent_of.get(cursor)
