from __future__ import annotations

from django.db import transaction

from apps.hr.models import DepartmentHead, Employee, Team, TeamMember
from apps.hr.repositories import EmployeeRepository, TeamRepository


class OrgValidationError(ValueError):
    """Vi phạm ràng buộc tổ chức (nhân sự không thuộc phòng ban, tên team trùng...)."""


def _assert_in_department(employee_id: int, department_id: int) -> Employee:
    """Đảm bảo nhân sự tồn tại (chưa xoá) và thuộc đúng phòng ban."""
    emp = EmployeeRepository.get_or_none(employee_id)
    if emp is None:
        raise OrgValidationError("Không tìm thấy nhân sự.")
    if emp.department_id != department_id:
        raise OrgValidationError("Nhân sự không thuộc phòng ban này.")
    return emp


class DepartmentOrgService:
    """Quản lý cấu trúc tổ chức của phòng ban: trưởng phòng + team."""

    # ── Trưởng phòng ────────────────────────────────────────────────────────
    @staticmethod
    @transaction.atomic
    def set_head(department_id: int, employee_id: int | None) -> DepartmentHead | None:
        """Gán (hoặc gỡ nếu ``employee_id`` là None) trưởng phòng."""
        if employee_id is None:
            DepartmentHead.objects.filter(department_id=department_id).delete()
            return None
        _assert_in_department(employee_id, department_id)
        head, _ = DepartmentHead.objects.update_or_create(
            department_id=department_id, defaults={"employee_id": employee_id}
        )
        return head

    # ── Team ────────────────────────────────────────────────────────────────
    @staticmethod
    @transaction.atomic
    def create_team(
        department_id: int,
        *,
        name: str,
        lead_employee_id: int | None = None,
        member_employee_ids: list[int] | None = None,
    ) -> Team:
        name = (name or "").strip()
        if not name:
            raise OrgValidationError("Tên team không được để trống.")
        if TeamRepository.active().filter(department_id=department_id, name=name).exists():
            raise OrgValidationError("Tên team đã tồn tại trong phòng ban.")
        if lead_employee_id is not None:
            _assert_in_department(lead_employee_id, department_id)
        team = Team.objects.create(
            department_id=department_id, name=name, lead_id=lead_employee_id
        )
        DepartmentOrgService._set_members(
            team, member_employee_ids or [], lead_employee_id
        )
        return team

    @staticmethod
    @transaction.atomic
    def update_team(team_id: int, data: dict) -> Team:
        team = TeamRepository.active().select_for_update().filter(pk=team_id).first()
        if team is None:
            raise OrgValidationError("Không tìm thấy team.")
        dept_id = team.department_id

        if "name" in data:
            name = (data["name"] or "").strip()
            if not name:
                raise OrgValidationError("Tên team không được để trống.")
            if (
                TeamRepository.active()
                .filter(department_id=dept_id, name=name)
                .exclude(pk=team_id)
                .exists()
            ):
                raise OrgValidationError("Tên team đã tồn tại trong phòng ban.")
            team.name = name

        lead_id = team.lead_id
        if "lead_employee_id" in data:
            lead_id = data["lead_employee_id"]
            if lead_id is not None:
                _assert_in_department(lead_id, dept_id)
            team.lead_id = lead_id

        team.save()

        if "member_employee_ids" in data:
            DepartmentOrgService._set_members(team, data["member_employee_ids"] or [], lead_id)
        elif "lead_employee_id" in data and lead_id is not None:
            # Lead mới phải nằm trong danh sách thành viên.
            TeamMember.objects.get_or_create(team=team, employee_id=lead_id)
        return team

    @staticmethod
    @transaction.atomic
    def delete_team(team_id: int) -> None:
        team = TeamRepository.active().filter(pk=team_id).first()
        if team is None:
            raise OrgValidationError("Không tìm thấy team.")
        TeamRepository.soft_delete(team)

    @staticmethod
    def _set_members(
        team: Team, member_ids: list[int], lead_id: int | None
    ) -> None:
        """Thay toàn bộ thành viên (replace-set); lead luôn được coi là thành viên."""
        ids = {int(m) for m in member_ids}
        if lead_id is not None:
            ids.add(int(lead_id))
        for emp_id in ids:
            _assert_in_department(emp_id, team.department_id)
        team.members.exclude(employee_id__in=ids).delete()
        existing = set(team.members.values_list("employee_id", flat=True))
        TeamMember.objects.bulk_create(
            [TeamMember(team=team, employee_id=eid) for eid in ids - existing]
        )
