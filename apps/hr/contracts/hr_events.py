from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from apps.common.events import DomainEvent


@dataclass(frozen=True)
class EmployeeCreated(DomainEvent):
    event_type: ClassVar[str] = "hr.employee.created"
    employee_id: int = 0
    user_id: int = 0
    department_id: int | None = None


@dataclass(frozen=True)
class EmployeeDepartmentChanged(DomainEvent):
    event_type: ClassVar[str] = "hr.employee.department_changed"
    employee_id: int = 0
    user_id: int = 0
    department_id: int | None = None


@dataclass(frozen=True)
class EmployeeResigned(DomainEvent):
    event_type: ClassVar[str] = "hr.employee.resigned"
    employee_id: int = 0
    user_id: int = 0


@dataclass(frozen=True)
class SalaryChanged(DomainEvent):
    event_type: ClassVar[str] = "hr.salary.changed"
    employee_id: int = 0
    amount: int = 0
    effective_from: str = ""


@dataclass(frozen=True)
class ContractSigned(DomainEvent):
    event_type: ClassVar[str] = "hr.contract.signed"
    employee_id: int = 0
    contract_id: int = 0


@dataclass(frozen=True)
class LeaveApproved(DomainEvent):
    event_type: ClassVar[str] = "hr.leave.approved"
    leave_id: int = 0
    employee_id: int = 0
    days: float = 0.0
