from __future__ import annotations

from apps.common.repository import BaseRepository
from apps.hr.models import (
    AttendanceEvent,
    AttendanceRecord,
    CompanyLocation,
    DepartmentHead,
    Employee,
    EmploymentContract,
    LeaveRequest,
    SalaryRecord,
    Team,
)


class EmployeeRepository(BaseRepository[Employee]):
    model = Employee

    @classmethod
    def latest_code(cls) -> str | None:
        return (
            cls.all_with_deleted()
            .filter(employee_code__startswith="NV")
            .order_by("-employee_code")
            .values_list("employee_code", flat=True)
            .first()
        )

    @classmethod
    def parent_map(cls) -> dict[int, int]:
        """Map employee_id -> manager_id for all employees (org chart validation)."""
        return dict(
            cls.all_with_deleted()
            .exclude(manager__isnull=True)
            .values_list("id", "manager_id")
        )

    @classmethod
    def list_with_relations(cls):
        return (
            cls.active()
            .select_related("department", "manager")
            # EmployeeSerializer lồng `children` (EmployeeChildSerializer) — thiếu
            # prefetch là +1 query cho MỖI nhân sự khi list (audit 2026-07-28 DB-6).
            .prefetch_related("accounts__user", "children")
        )


class ContractRepository(BaseRepository[EmploymentContract]):
    model = EmploymentContract

    @classmethod
    def for_employee(cls, employee_id: int):
        return cls.active().filter(employee_id=employee_id).order_by("-start_date")


class SalaryRepository(BaseRepository[SalaryRecord]):
    model = SalaryRecord

    @classmethod
    def current(cls, employee_id: int):
        return cls.active().filter(employee_id=employee_id, is_current=True).first()

    @classmethod
    def history(cls, employee_id: int):
        return cls.active().filter(employee_id=employee_id).order_by("-effective_from")


class LeaveRepository(BaseRepository[LeaveRequest]):
    model = LeaveRequest

    @classmethod
    def for_employee(cls, employee_id: int):
        return cls.active().filter(employee_id=employee_id).order_by("-start_date")


class AttendanceRepository(BaseRepository[AttendanceRecord]):
    model = AttendanceRecord

    @classmethod
    def for_month(cls, employee_id: int, year: int, month: int):
        return cls.active().filter(
            employee_id=employee_id, work_date__year=year, work_date__month=month
        ).order_by("work_date")


class CompanyLocationRepository(BaseRepository[CompanyLocation]):
    model = CompanyLocation

    @classmethod
    def active(cls):
        # CompanyLocation has no deleted_at; active means is_active=True
        return cls.model._default_manager.filter(is_active=True).order_by("name")


class TeamRepository(BaseRepository[Team]):
    model = Team

    @classmethod
    def for_department(cls, department_id: int):
        return (
            cls.active()
            .filter(department_id=department_id)
            .select_related("lead")
            .prefetch_related("members__employee")
            .order_by("name")
        )


class DepartmentHeadRepository(BaseRepository[DepartmentHead]):
    model = DepartmentHead


class AttendanceEventRepository(BaseRepository[AttendanceEvent]):
    model = AttendanceEvent

    @classmethod
    def for_day(cls, employee_id: int, work_date):
        # AttendanceEvent has no deleted_at; filter directly
        return cls.model._default_manager.filter(
            employee_id=employee_id, work_date=work_date
        ).order_by("occurred_at")
