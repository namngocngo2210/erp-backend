# HR Sprint 1 — Hồ sơ NV + Phòng ban + Org chart + Mapping User — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tạo app `apps/hr` với model `Employee` nối 1-1 tới `User`, CRUD API, org-chart, đồng bộ phòng ban về RBAC qua event, backfill dữ liệu hiện có, và feature-slice FE `src/features/hr`.

**Architecture:** Modular monolith, clean architecture (domain/application/contracts/transport/repositories). HR sở hữu bảng `hr_employees`; chỉ tham chiếu `users.User` (AUTH_USER_MODEL) và `users.Department` (master data dùng chung). Đổi phòng ban phát `EmployeeDepartmentChanged` → handler trong `apps/users` sync `User.department`/`UserDepartment` + bust cache RBAC.

**Tech Stack:** Django 5 + DRF, PostgreSQL, pytest (Django TestCase + APIClient). FE: React + Vite + TanStack Query + shadcn/ui, vitest.

Spec gốc: `docs/erp-hr.md` §2.

---

## File structure

**Backend (`backend/apps/hr/`):**
- `__init__.py`, `apps.py` — AppConfig + `ready()` (đăng ký subscriber phía hr nếu cần)
- `models.py` — `Employee`, enum `EmploymentStatus`, `Gender`
- `domain/employee_code.py` — sinh mã NV
- `domain/org_chart.py` — validate chống cycle manager
- `contracts/hr_events.py` — `EmployeeCreated`, `EmployeeDepartmentChanged`, `EmployeeResigned`
- `contracts/employee_service.py` — Protocol (interface công bố cho module khác)
- `application/services/employee_service.py` — orchestration create/update/resign + tạo User + publish event
- `repositories.py` — `EmployeeRepository(BaseRepository[Employee])`
- `transport/serializers/employee_serializers.py`
- `transport/views/employee_views.py`
- `urls.py`
- `migrations/0001_initial.py`, `migrations/0002_backfill_employees.py`
- `tests/test_*.py`

**Modify:**
- `backend/config/settings_parts/base.py` — thêm `"apps.hr"` vào INSTALLED_APPS
- `backend/config/urls.py` — thêm route `^api/hr/?`
- `backend/apps/users/rbac_catalog.py` — thêm section `employees`
- `backend/apps/users/migrations/0018_seed_hr_permissions.py` — seed permission `employees:*`
- `backend/apps/users/subscribers.py` (tạo mới) + `backend/apps/users/apps.py` — subscribe `hr.employee.department_changed`

**Frontend (`frontend/src/features/hr/`):** `hr-types.ts`, `queries.ts`, `components/EmployeesTable.tsx`, `components/EmployeeFormDialog.tsx`, `pages/EmployeesPage.tsx`; modify `src/app/modules.ts`, `src/app/routes.tsx`, `src/app/routed-pages.tsx`, `src/i18n/messages/hr.ts`, `src/i18n/messages.ts`.

---

## Task 1: Tạo app skeleton `apps/hr`

**Files:**
- Create: `backend/apps/hr/__init__.py`, `backend/apps/hr/apps.py`
- Modify: `backend/config/settings_parts/base.py`

- [ ] **Step 1: Tạo package + AppConfig**

`backend/apps/hr/__init__.py`: rỗng.

`backend/apps/hr/apps.py`:
```python
from django.apps import AppConfig


class HrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.hr"
```

- [ ] **Step 2: Đăng ký app**

Trong `backend/config/settings_parts/base.py`, thêm dòng `"apps.hr",` ngay sau `"apps.inventory",` trong `INSTALLED_APPS`.

- [ ] **Step 3: Verify Django nhận app**

Run (xem memory "Running backend tests" để biết cách chạy trong docker):
```bash
docker run --rm --network desi-order_default -e DJANGO_SETTINGS_MODULE=config.settings -w /app <backend-test-image> python manage.py check
```
Expected: `System check identified no issues`.

- [ ] **Step 4: Commit**
```bash
git add backend/apps/hr/__init__.py backend/apps/hr/apps.py backend/config/settings_parts/base.py
git commit -m "feat(hr): scaffold hr app"
```

---

## Task 2: Model `Employee`

**Files:**
- Create: `backend/apps/hr/models.py`
- Test: `backend/apps/hr/tests/__init__.py`, `backend/apps/hr/tests/test_models.py`

- [ ] **Step 1: Viết test thất bại**

`backend/apps/hr/tests/test_models.py`:
```python
from django.test import TestCase

from apps.hr.models import Employee, EmploymentStatus
from apps.users.models import Department, User


class EmployeeModelTests(TestCase):
    def test_create_employee_links_user_one_to_one(self):
        user = User.objects.create_user(username="nv1", password="x")
        dept = Department.objects.create(name="Sales")
        emp = Employee.objects.create(
            user=user, employee_code="NV0001", full_name="Nguyen Van A",
            department=dept, employment_status=EmploymentStatus.ACTIVE,
        )
        self.assertEqual(user.employee, emp)
        self.assertEqual(emp.user_id, user.id)

    def test_soft_delete_field_present(self):
        self.assertTrue(any(f.name == "deleted_at" for f in Employee._meta.get_fields()))
```

- [ ] **Step 2: Chạy test — fail**

Run: `pytest apps/hr/tests/test_models.py -v`
Expected: FAIL (ModuleNotFoundError: apps.hr.models).

- [ ] **Step 3: Viết model**

`backend/apps/hr/models.py`:
```python
from __future__ import annotations

from django.conf import settings
from django.db import models


class EmploymentStatus(models.TextChoices):
    PROBATION = "probation", "Thử việc"
    ACTIVE = "active", "Chính thức"
    ON_LEAVE = "on_leave", "Tạm nghỉ"
    RESIGNED = "resigned", "Đã nghỉ"


class Gender(models.TextChoices):
    MALE = "male", "Nam"
    FEMALE = "female", "Nữ"
    OTHER = "other", "Khác"


class Employee(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="employee"
    )
    employee_code = models.CharField(max_length=20, unique=True)
    full_name = models.CharField(max_length=150)
    department = models.ForeignKey(
        "users.Department", on_delete=models.PROTECT, related_name="employees",
        null=True, blank=True,
    )
    manager = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reports"
    )
    job_title = models.CharField(max_length=150, blank=True, default="")
    employment_status = models.CharField(
        max_length=20, choices=EmploymentStatus.choices, default=EmploymentStatus.PROBATION
    )
    hire_date = models.DateField(null=True, blank=True)
    resign_date = models.DateField(null=True, blank=True)
    personal_email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    dob = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True, default="")
    id_number = models.CharField(max_length=20, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "hr_employees"
        indexes = [
            models.Index(fields=["department"], name="hr_emp_dept_idx"),
            models.Index(fields=["manager"], name="hr_emp_mgr_idx"),
            models.Index(fields=["employment_status"], name="hr_emp_status_idx"),
            models.Index(fields=["deleted_at"], name="hr_emp_deleted_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.employee_code} · {self.full_name}"
```

`backend/apps/hr/tests/__init__.py`: rỗng.

- [ ] **Step 4: Tạo migration + chạy test**

Run:
```bash
python manage.py makemigrations hr
pytest apps/hr/tests/test_models.py -v
```
Expected: tạo `0001_initial.py`; test PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/apps/hr/models.py backend/apps/hr/tests/ backend/apps/hr/migrations/
git commit -m "feat(hr): add Employee model + migration"
```

---

## Task 3: Domain — sinh mã NV

**Files:**
- Create: `backend/apps/hr/domain/__init__.py`, `backend/apps/hr/domain/employee_code.py`
- Test: `backend/apps/hr/tests/test_employee_code.py`

- [ ] **Step 1: Viết test thất bại**
```python
from apps.hr.domain.employee_code import next_employee_code


def test_next_code_from_empty():
    assert next_employee_code(None) == "NV0001"


def test_next_code_increments():
    assert next_employee_code("NV0007") == "NV0008"


def test_next_code_pads_to_four():
    assert next_employee_code("NV0099") == "NV0100"
```

- [ ] **Step 2: Chạy — fail**

Run: `pytest apps/hr/tests/test_employee_code.py -v` → FAIL (no module).

- [ ] **Step 3: Implement**

`backend/apps/hr/domain/employee_code.py`:
```python
from __future__ import annotations

PREFIX = "NV"


def next_employee_code(latest_code: str | None) -> str:
    """Sinh mã NV kế tiếp dạng NV0001. ``latest_code`` là mã lớn nhất hiện có."""
    if not latest_code or not latest_code.startswith(PREFIX):
        return f"{PREFIX}0001"
    try:
        number = int(latest_code[len(PREFIX):])
    except ValueError:
        return f"{PREFIX}0001"
    return f"{PREFIX}{number + 1:04d}"
```
`backend/apps/hr/domain/__init__.py`: rỗng.

- [ ] **Step 4: Chạy — pass.** Run: `pytest apps/hr/tests/test_employee_code.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/apps/hr/domain/ backend/apps/hr/tests/test_employee_code.py
git commit -m "feat(hr): employee code generator"
```

---

## Task 4: Domain — chống cycle org chart

**Files:**
- Create: `backend/apps/hr/domain/org_chart.py`
- Test: `backend/apps/hr/tests/test_org_chart.py`

- [ ] **Step 1: Viết test thất bại**
```python
import pytest

from apps.hr.domain.org_chart import assert_no_cycle, OrgChartCycleError


def test_self_manager_rejected():
    with pytest.raises(OrgChartCycleError):
        assert_no_cycle(employee_id=1, new_manager_id=1, parent_of={})


def test_indirect_cycle_rejected():
    # 1 -> 2 -> 3 ; gán manager của 3 = 1 tạo vòng
    parent_of = {2: 1, 3: 2}
    with pytest.raises(OrgChartCycleError):
        assert_no_cycle(employee_id=1, new_manager_id=3, parent_of=parent_of)


def test_valid_chain_ok():
    parent_of = {2: 1}
    assert_no_cycle(employee_id=3, new_manager_id=2, parent_of=parent_of)  # no raise
```

- [ ] **Step 2: Chạy — fail.** Run: `pytest apps/hr/tests/test_org_chart.py -v` → FAIL.

- [ ] **Step 3: Implement**

`backend/apps/hr/domain/org_chart.py`:
```python
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
```

- [ ] **Step 4: Chạy — pass.** Run: `pytest apps/hr/tests/test_org_chart.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/apps/hr/domain/org_chart.py backend/apps/hr/tests/test_org_chart.py
git commit -m "feat(hr): org chart cycle guard"
```

---

## Task 5: Event contracts

**Files:**
- Create: `backend/apps/hr/contracts/__init__.py`, `backend/apps/hr/contracts/hr_events.py`
- Test: `backend/apps/hr/tests/test_events_contract.py`

- [ ] **Step 1: Viết test thất bại**
```python
from apps.hr.contracts.hr_events import (
    EmployeeCreated, EmployeeDepartmentChanged, EmployeeResigned,
)


def test_event_types():
    assert EmployeeCreated.event_type == "hr.employee.created"
    assert EmployeeDepartmentChanged.event_type == "hr.employee.department_changed"
    assert EmployeeResigned.event_type == "hr.employee.resigned"


def test_department_changed_carries_user_and_dept():
    e = EmployeeDepartmentChanged(user_id=5, department_id=9, employee_id=3)
    assert e.user_id == 5 and e.department_id == 9 and e.employee_id == 3
```

- [ ] **Step 2: Chạy — fail.** Run: `pytest apps/hr/tests/test_events_contract.py -v` → FAIL.

- [ ] **Step 3: Implement**

`backend/apps/hr/contracts/hr_events.py`:
```python
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
```
`backend/apps/hr/contracts/__init__.py`: rỗng.

- [ ] **Step 4: Chạy — pass.** Run: `pytest apps/hr/tests/test_events_contract.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/apps/hr/contracts/ backend/apps/hr/tests/test_events_contract.py
git commit -m "feat(hr): domain event contracts"
```

---

## Task 6: Repository

**Files:**
- Create: `backend/apps/hr/repositories.py`
- Test: `backend/apps/hr/tests/test_repository.py`

- [ ] **Step 1: Viết test thất bại**
```python
from django.test import TestCase

from apps.hr.models import Employee
from apps.hr.repositories import EmployeeRepository
from apps.users.models import User


class EmployeeRepositoryTests(TestCase):
    def _emp(self, code):
        u = User.objects.create_user(username=f"u-{code}", password="x")
        return Employee.objects.create(user=u, employee_code=code, full_name=code)

    def test_latest_code_returns_max(self):
        self._emp("NV0001")
        self._emp("NV0005")
        self._emp("NV0003")
        self.assertEqual(EmployeeRepository.latest_code(), "NV0005")

    def test_latest_code_none_when_empty(self):
        self.assertIsNone(EmployeeRepository.latest_code())

    def test_active_excludes_soft_deleted(self):
        e = self._emp("NV0001")
        EmployeeRepository.soft_delete(e)
        self.assertEqual(EmployeeRepository.active().count(), 0)
```

- [ ] **Step 2: Chạy — fail.** Run: `pytest apps/hr/tests/test_repository.py -v` → FAIL.

- [ ] **Step 3: Implement**

`backend/apps/hr/repositories.py`:
```python
from __future__ import annotations

from apps.common.repository import BaseRepository
from apps.hr.models import Employee


class EmployeeRepository(BaseRepository[Employee]):
    model = Employee

    @classmethod
    def latest_code(cls) -> str | None:
        row = cls.all_with_deleted().order_by("-employee_code").values_list(
            "employee_code", flat=True
        ).first()
        return row

    @classmethod
    def parent_map(cls) -> dict[int, int]:
        """Map employee_id -> manager_id cho toàn bộ NV (validate org chart)."""
        return {
            eid: mid
            for eid, mid in cls.all_with_deleted()
            .exclude(manager__isnull=True)
            .values_list("id", "manager_id")
        }

    @classmethod
    def list_with_relations(cls):
        return cls.active().select_related("user", "department", "manager")
```

- [ ] **Step 4: Chạy — pass.** Run: `pytest apps/hr/tests/test_repository.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/apps/hr/repositories.py backend/apps/hr/tests/test_repository.py
git commit -m "feat(hr): employee repository"
```

---

## Task 7: Application service — create/update/resign + publish event

**Files:**
- Create: `backend/apps/hr/application/__init__.py`, `backend/apps/hr/application/services/__init__.py`, `backend/apps/hr/application/services/employee_service.py`
- Test: `backend/apps/hr/tests/test_employee_service.py`

- [ ] **Step 1: Viết test thất bại**
```python
from django.test import TestCase

from apps.common.events import event_bus
from apps.hr.application.services.employee_service import EmployeeService
from apps.hr.contracts.hr_events import EmployeeDepartmentChanged
from apps.hr.domain.org_chart import OrgChartCycleError
from apps.hr.models import Employee, EmploymentStatus
from apps.users.models import Department, User


class EmployeeServiceTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name="Sales")
        self.events = []
        event_bus.subscribe(
            EmployeeDepartmentChanged.event_type, lambda e: self.events.append(e)
        )

    def tearDown(self):
        event_bus.clear()

    def test_create_makes_user_and_employee(self):
        emp = EmployeeService.create(
            full_name="A", department_id=self.dept.id, create_login=False,
            username="nva", job_title="Sale",
        )
        self.assertTrue(emp.employee_code.startswith("NV"))
        user = User.objects.get(pk=emp.user_id)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_active)

    def test_create_login_user_active(self):
        emp = EmployeeService.create(
            full_name="B", department_id=self.dept.id, create_login=True,
            username="nvb", password="secret123",
        )
        user = User.objects.get(pk=emp.user_id)
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("secret123"))

    def test_change_department_publishes_event(self):
        emp = EmployeeService.create(
            full_name="C", department_id=self.dept.id, create_login=False, username="nvc"
        )
        other = Department.objects.create(name="Booking")
        EmployeeService.update(emp.id, {"department_id": other.id})
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0].department_id, other.id)
        self.assertEqual(self.events[0].user_id, emp.user_id)

    def test_self_manager_rejected(self):
        emp = EmployeeService.create(
            full_name="D", department_id=self.dept.id, create_login=False, username="nvd"
        )
        with self.assertRaises(OrgChartCycleError):
            EmployeeService.update(emp.id, {"manager_id": emp.id})

    def test_resign_deactivates_user(self):
        emp = EmployeeService.create(
            full_name="E", department_id=self.dept.id, create_login=True,
            username="nve", password="x123",
        )
        EmployeeService.resign(emp.id)
        emp.refresh_from_db()
        self.assertEqual(emp.employment_status, EmploymentStatus.RESIGNED)
        self.assertFalse(User.objects.get(pk=emp.user_id).is_active)
```

> Lưu ý: event handler đăng ký trong test bằng `event_bus.subscribe` trực tiếp; service dùng `publish_on_commit`, nên test phải bọc trong transaction commit. Dùng `from django.test import TransactionTestCase` HOẶC publish ngay (`publish`) khi `transaction.get_connection().in_atomic_block` là False. Để đơn giản và chạy được dưới `TestCase`, service publish bằng `event_bus.publish_on_commit`; trong test dùng `self.captureOnCommitCallbacks(execute=True)` bao quanh lời gọi update. Cập nhật test `test_change_department_publishes_event` và `test_resign...` để bọc:
> ```python
> with self.captureOnCommitCallbacks(execute=True):
>     EmployeeService.update(emp.id, {"department_id": other.id})
> ```

- [ ] **Step 2: Chạy — fail.** Run: `pytest apps/hr/tests/test_employee_service.py -v` → FAIL.

- [ ] **Step 3: Implement service**

`backend/apps/hr/application/services/employee_service.py`:
```python
from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.common.events import event_bus
from apps.hr.contracts.hr_events import (
    EmployeeCreated, EmployeeDepartmentChanged, EmployeeResigned,
)
from apps.hr.domain.employee_code import next_employee_code
from apps.hr.domain.org_chart import assert_no_cycle
from apps.hr.models import Employee, EmploymentStatus
from apps.hr.repositories import EmployeeRepository


def _create_user(*, username: str, create_login: bool, password: str | None,
                 display_name: str):
    from apps.users.models import User  # local import: tránh phụ thuộc cứng lúc import

    user = User(username=username, display_name=display_name)
    if create_login and password:
        user.is_active = True
        user.set_password(password)
    else:
        user.is_active = False
        user.set_unusable_password()
    user.save()
    return user


class EmployeeService:
    @staticmethod
    @transaction.atomic
    def create(*, full_name: str, department_id: int | None, username: str,
               create_login: bool = False, password: str | None = None,
               job_title: str = "", manager_id: int | None = None,
               employment_status: str = EmploymentStatus.PROBATION,
               **extra: Any) -> Employee:
        user = _create_user(
            username=username, create_login=create_login, password=password,
            display_name=full_name,
        )
        code = next_employee_code(EmployeeRepository.latest_code())
        if manager_id is not None:
            assert_no_cycle(employee_id=-1, new_manager_id=manager_id,
                            parent_of=EmployeeRepository.parent_map())
        emp = Employee.objects.create(
            user=user, employee_code=code, full_name=full_name,
            department_id=department_id, manager_id=manager_id, job_title=job_title,
            employment_status=employment_status,
            personal_email=extra.get("personal_email", ""),
            phone=extra.get("phone", ""), id_number=extra.get("id_number", ""),
            address=extra.get("address", ""), gender=extra.get("gender", ""),
            dob=extra.get("dob"), hire_date=extra.get("hire_date"),
        )
        event_bus.publish_on_commit(
            EmployeeCreated(employee_id=emp.id, user_id=user.id,
                            department_id=department_id)
        )
        return emp

    @staticmethod
    @transaction.atomic
    def update(employee_id: int, data: dict[str, Any]) -> Employee:
        emp = EmployeeRepository.active().select_for_update().get(pk=employee_id)
        dept_changed = "department_id" in data and data["department_id"] != emp.department_id
        if "manager_id" in data:
            assert_no_cycle(employee_id=emp.id, new_manager_id=data["manager_id"],
                            parent_of=EmployeeRepository.parent_map())
        allowed = {
            "full_name", "department_id", "manager_id", "job_title",
            "employment_status", "hire_date", "resign_date", "personal_email",
            "phone", "dob", "gender", "id_number", "address",
        }
        for key, value in data.items():
            if key in allowed:
                setattr(emp, key, value)
        emp.save()
        if dept_changed:
            event_bus.publish_on_commit(
                EmployeeDepartmentChanged(employee_id=emp.id, user_id=emp.user_id,
                                          department_id=emp.department_id)
            )
        return emp

    @staticmethod
    @transaction.atomic
    def resign(employee_id: int) -> Employee:
        from apps.users.models import User

        emp = EmployeeRepository.active().select_for_update().get(pk=employee_id)
        emp.employment_status = EmploymentStatus.RESIGNED
        emp.save(update_fields=["employment_status", "updated_at"])
        User.objects.filter(pk=emp.user_id).update(is_active=False)
        event_bus.publish_on_commit(
            EmployeeResigned(employee_id=emp.id, user_id=emp.user_id)
        )
        return emp
```
`application/__init__.py`, `application/services/__init__.py`: rỗng.

- [ ] **Step 4: Chạy — pass.** Run: `pytest apps/hr/tests/test_employee_service.py -v` → PASS (đã bọc captureOnCommitCallbacks ở các test cần event).

- [ ] **Step 5: Commit**
```bash
git add backend/apps/hr/application/ backend/apps/hr/tests/test_employee_service.py
git commit -m "feat(hr): employee application service"
```

---

## Task 8: RBAC — section `employees` + seed migration

**Files:**
- Modify: `backend/apps/users/rbac_catalog.py`
- Create: `backend/apps/users/migrations/0018_seed_hr_permissions.py`
- Test: `backend/apps/hr/tests/test_rbac_section.py`

- [ ] **Step 1: Viết test thất bại**
```python
from django.test import TestCase

from apps.users.rbac_catalog import PERMISSION_CATALOG, sync_permissions
from apps.users.models import Permission


class HrRbacSectionTests(TestCase):
    def test_employees_section_in_catalog(self):
        self.assertIn("employees", PERMISSION_CATALOG)
        self.assertIn("view", PERMISSION_CATALOG["employees"])

    def test_sync_creates_employees_permissions(self):
        sync_permissions()
        self.assertTrue(Permission.objects.filter(code="employees:view").exists())
        self.assertTrue(Permission.objects.filter(code="employees:create").exists())
```

- [ ] **Step 2: Chạy — fail.** Run: `pytest apps/hr/tests/test_rbac_section.py -v` → FAIL.

- [ ] **Step 3: Thêm section vào catalog**

Trong `backend/apps/users/rbac_catalog.py`, thêm vào `PERMISSION_CATALOG` (nhóm gần `users`):
```python
    # HR / nhân sự
    "employees": ("view", "create", "edit", "delete", "export"),
```

- [ ] **Step 4: Tạo seed migration**

`backend/apps/users/migrations/0018_seed_hr_permissions.py` (copy pattern từ `0014_seed_rbac_catalog.py`, chỉ seed section employees):
```python
from django.db import migrations

HR_SECTION = "employees"
HR_ACTIONS = ("view", "create", "edit", "delete", "export")


def seed(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    existing = set(Permission.objects.values_list("code", flat=True))
    to_create = [
        Permission(code=f"{HR_SECTION}:{a}", section=HR_SECTION, action=a,
                   label=f"{HR_SECTION} · {a}")
        for a in HR_ACTIONS
        if f"{HR_SECTION}:{a}" not in existing
    ]
    if to_create:
        Permission.objects.bulk_create(to_create)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("users", "0017_drop_legacy_role_sections")]
    operations = [migrations.RunPython(seed, noop)]
```
> Kiểm tra số migration mới nhất của app users trước khi đặt tên (hiện tại là 0017).

- [ ] **Step 5: Chạy — pass.** Run: `pytest apps/hr/tests/test_rbac_section.py -v` → PASS.

- [ ] **Step 6: Commit**
```bash
git add backend/apps/users/rbac_catalog.py backend/apps/users/migrations/0018_seed_hr_permissions.py backend/apps/hr/tests/test_rbac_section.py
git commit -m "feat(hr): add employees RBAC section + seed"
```

---

## Task 9: Handler sync department về RBAC (phía apps/users)

**Files:**
- Create: `backend/apps/users/subscribers.py`
- Modify: `backend/apps/users/apps.py`
- Test: `backend/apps/hr/tests/test_department_sync.py`

- [ ] **Step 1: Viết test thất bại**
```python
from django.test import TestCase

from apps.hr.application.services.employee_service import EmployeeService
from apps.users.models import Department, User, UserDepartment


class DepartmentSyncTests(TestCase):
    def test_change_department_updates_user(self):
        d1 = Department.objects.create(name="Sales")
        d2 = Department.objects.create(name="Booking")
        emp = EmployeeService.create(
            full_name="A", department_id=d1.id, create_login=False, username="nva"
        )
        with self.captureOnCommitCallbacks(execute=True):
            EmployeeService.update(emp.id, {"department_id": d2.id})
        user = User.objects.get(pk=emp.user_id)
        self.assertEqual(user.department_id, d2.id)
        self.assertTrue(
            UserDepartment.objects.filter(user=user, department=d2, is_primary=True).exists()
        )
```

- [ ] **Step 2: Chạy — fail.** Run: `pytest apps/hr/tests/test_department_sync.py -v` → FAIL.

- [ ] **Step 3: Implement subscriber**

`backend/apps/users/subscribers.py`:
```python
from __future__ import annotations

import logging

from apps.common.events import event_bus
from apps.hr.contracts.hr_events import EmployeeDepartmentChanged

logger = logging.getLogger(__name__)
_registered = False


def _on_department_changed(event: EmployeeDepartmentChanged) -> None:
    from apps.users.models import User, UserDepartment
    from apps.users.rbac import bump_rbac_cache_version

    user = User.objects.filter(pk=event.user_id).first()
    if not user:
        return
    user.department_id = event.department_id
    user.save(update_fields=["department"])
    if event.department_id is not None:
        UserDepartment.objects.update_or_create(
            user=user, department_id=event.department_id,
            defaults={"is_primary": True},
        )
        UserDepartment.objects.filter(user=user).exclude(
            department_id=event.department_id
        ).update(is_primary=False)
    bump_rbac_cache_version()


def register() -> None:
    global _registered
    if _registered:
        return
    event_bus.subscribe(EmployeeDepartmentChanged.event_type, _on_department_changed)
    _registered = True
```
> Xác minh chữ ký `UserDepartment` (`is_primary`) và `bump_rbac_cache_version` import path khi triển khai (xem `apps/users/models.py`, `apps/users/rbac.py`, `apps/users/test_helpers.py`). Điều chỉnh tên field nếu khác.

- [ ] **Step 4: Đăng ký trong ready()**

Trong `backend/apps/users/apps.py`, thêm vào cuối `ready()`:
```python
        from apps.users import subscribers
        subscribers.register()
```

- [ ] **Step 5: Chạy — pass.** Run: `pytest apps/hr/tests/test_department_sync.py -v` → PASS.

- [ ] **Step 6: Commit**
```bash
git add backend/apps/users/subscribers.py backend/apps/users/apps.py backend/apps/hr/tests/test_department_sync.py
git commit -m "feat(hr): sync employee department to RBAC via event"
```

---

## Task 10: Serializers

**Files:**
- Create: `backend/apps/hr/transport/__init__.py`, `backend/apps/hr/transport/serializers/__init__.py`, `backend/apps/hr/transport/serializers/employee_serializers.py`
- Test: `backend/apps/hr/tests/test_serializers.py`

- [ ] **Step 1: Viết test thất bại**
```python
from django.test import TestCase

from apps.hr.models import Employee
from apps.hr.transport.serializers.employee_serializers import EmployeeSerializer
from apps.users.models import Department, User


class EmployeeSerializerTests(TestCase):
    def test_serializes_relations(self):
        u = User.objects.create_user(username="nva", password="x")
        d = Department.objects.create(name="Sales")
        e = Employee.objects.create(user=u, employee_code="NV0001", full_name="A", department=d)
        data = EmployeeSerializer(e).data
        self.assertEqual(data["employee_code"], "NV0001")
        self.assertEqual(data["department_name"], "Sales")
        self.assertEqual(data["username"], "nva")
```

- [ ] **Step 2: Chạy — fail.** Run: `pytest apps/hr/tests/test_serializers.py -v` → FAIL.

- [ ] **Step 3: Implement**

`backend/apps/hr/transport/serializers/employee_serializers.py`:
```python
from __future__ import annotations

from rest_framework import serializers

from apps.hr.models import Employee


class EmployeeSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True, default="")
    manager_name = serializers.CharField(source="manager.full_name", read_only=True, default="")
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Employee
        fields = (
            "id", "employee_code", "full_name", "department", "department_name",
            "manager", "manager_name", "job_title", "employment_status",
            "hire_date", "resign_date", "personal_email", "phone", "dob",
            "gender", "id_number", "address", "username", "created_at", "updated_at",
        )
        read_only_fields = ("id", "employee_code", "created_at", "updated_at")


class EmployeeCreateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150)
    department_id = serializers.IntegerField(required=False, allow_null=True)
    username = serializers.CharField(max_length=150)
    create_login = serializers.BooleanField(default=False)
    password = serializers.CharField(required=False, allow_blank=True, default="")
    job_title = serializers.CharField(required=False, allow_blank=True, default="")
    manager_id = serializers.IntegerField(required=False, allow_null=True)
    phone = serializers.CharField(required=False, allow_blank=True, default="")
    personal_email = serializers.EmailField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs.get("create_login") and not attrs.get("password"):
            raise serializers.ValidationError("Cần mật khẩu khi tạo tài khoản đăng nhập.")
        return attrs
```
`transport/__init__.py`, `transport/serializers/__init__.py`: rỗng.

- [ ] **Step 4: Chạy — pass.** Run: `pytest apps/hr/tests/test_serializers.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/apps/hr/transport/ backend/apps/hr/tests/test_serializers.py
git commit -m "feat(hr): employee serializers"
```

---

## Task 11: Views + URLs (CRUD + org-chart)

**Files:**
- Create: `backend/apps/hr/transport/views/__init__.py`, `backend/apps/hr/transport/views/employee_views.py`, `backend/apps/hr/urls.py`
- Modify: `backend/config/urls.py`
- Test: `backend/apps/hr/tests/test_employee_api.py`

- [ ] **Step 1: Viết test thất bại**
```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.hr.models import Employee
from apps.users.models import Department
from apps.users.test_helpers import make_admin, make_user_with_sections


class EmployeeApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("hr-admin")
        self.dept = Department.objects.create(name="Sales")

    def test_create_requires_permission(self):
        nobody = make_user_with_sections("nobody", ["orders"])
        self.client.force_authenticate(user=nobody)
        r = self.client.post("/api/hr/employees",
                             {"full_name": "A", "username": "nva", "department_id": self.dept.id},
                             format="json")
        self.assertEqual(r.status_code, 403)

    def test_admin_create_and_list(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.post("/api/hr/employees",
                             {"full_name": "A", "username": "nva", "department_id": self.dept.id},
                             format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.data["employee_code"].startswith("NV"))
        r2 = self.client.get("/api/hr/employees?limit=20&offset=0")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data["total"], 1)

    def test_org_chart(self):
        self.client.force_authenticate(user=self.admin)
        u = self.client.post("/api/hr/employees",
                             {"full_name": "Mgr", "username": "mgr", "department_id": self.dept.id},
                             format="json").data
        self.client.post("/api/hr/employees",
                         {"full_name": "Report", "username": "rep",
                          "department_id": self.dept.id, "manager_id": u["id"]},
                         format="json")
        r = self.client.get(f"/api/hr/employees/{u['id']}/org-chart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["reports"]), 1)
```

- [ ] **Step 2: Chạy — fail.** Run: `pytest apps/hr/tests/test_employee_api.py -v` → FAIL.

- [ ] **Step 3: Implement views**

`backend/apps/hr/transport/views/employee_views.py`:
```python
from __future__ import annotations

from django.db import IntegrityError
from django.db.models import Q
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.hr.application.services.employee_service import EmployeeService
from apps.hr.domain.org_chart import OrgChartCycleError
from apps.hr.repositories import EmployeeRepository
from apps.hr.transport.serializers.employee_serializers import (
    EmployeeCreateSerializer, EmployeeSerializer,
)
from apps.users.permissions import require_permission

_MAX_LIMIT = 200


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@never_cache
def list_create_employees(request):
    if request.method == "GET":
        if not _can(request, "employees:view"):
            return _forbidden()
        pagination, err = parse_required_pagination(request, max_limit=_MAX_LIMIT,
                                                    default_limit=50)
        if err is not None:
            return err
        qs = EmployeeRepository.list_with_relations()
        dept = request.query_params.get("department")
        if dept:
            qs = qs.filter(department_id=dept)
        status_f = request.query_params.get("status")
        if status_f:
            qs = qs.filter(employment_status=status_f)
        q = request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(Q(full_name__icontains=q) | Q(employee_code__icontains=q)
                           | Q(phone__icontains=q))
        total = qs.count()
        items = EmployeeRepository.paginate(qs.order_by("employee_code"),
                                            limit=pagination["limit"],
                                            offset=pagination["offset"])
        return Response({"items": EmployeeSerializer(items, many=True).data, "total": total,
                         "limit": pagination["limit"], "offset": pagination["offset"]})

    if not _can(request, "employees:create"):
        return _forbidden()
    ser = EmployeeCreateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    try:
        emp = EmployeeService.create(**ser.validated_data)
    except IntegrityError:
        return Response({"detail": "Username đã tồn tại."}, status=status.HTTP_400_BAD_REQUEST)
    return Response(EmployeeSerializer(emp).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
@never_cache
def employee_detail(request, employee_id: int):
    emp = EmployeeRepository.get_or_none(employee_id)
    if not emp:
        return Response({"detail": "Không tìm thấy nhân viên."}, status=404)
    if request.method == "GET":
        if not _can(request, "employees:view"):
            return _forbidden()
        return Response(EmployeeSerializer(emp).data)
    if request.method == "DELETE":
        if not _can(request, "employees:delete"):
            return _forbidden()
        EmployeeRepository.soft_delete(emp)
        return Response(status=status.HTTP_204_NO_CONTENT)
    if not _can(request, "employees:edit"):
        return _forbidden()
    try:
        emp = EmployeeService.update(employee_id, request.data)
    except OrgChartCycleError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(EmployeeSerializer(emp).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def employee_org_chart(request, employee_id: int):
    if not _can(request, "employees:view"):
        return _forbidden()
    emp = EmployeeRepository.get_or_none(employee_id)
    if not emp:
        return Response({"detail": "Không tìm thấy nhân viên."}, status=404)
    reports = EmployeeRepository.active().filter(manager_id=emp.id).select_related("user")
    return Response({"employee": EmployeeSerializer(emp).data,
                     "reports": EmployeeSerializer(reports, many=True).data})


def _can(request, code: str) -> bool:
    from apps.users.rbac import resolve_effective_permissions
    return resolve_effective_permissions(request.user).can(code)


def _forbidden():
    return Response({"detail": "Bạn không có quyền thực hiện hành động này."}, status=403)
```
> Có thể thay `_can`/`@permission_classes` bằng `require_permission("employees:...")` như `apps/warehouses` nếu mỗi method dùng 1 quyền cố định. Vì view gộp nhiều method nên kiểm tra quyền theo method bên trong cho rõ ràng.

`backend/apps/hr/urls.py`:
```python
from django.urls import path

from apps.hr.transport.views.employee_views import (
    employee_detail, employee_org_chart, list_create_employees,
)

urlpatterns = [
    path("employees", list_create_employees, name="hr-employees"),
    path("employees/<int:employee_id>", employee_detail, name="hr-employee-detail"),
    path("employees/<int:employee_id>/org-chart", employee_org_chart, name="hr-employee-org"),
]
```
`transport/views/__init__.py`: rỗng.

- [ ] **Step 4: Wire URL gốc**

Trong `backend/config/urls.py`, thêm:
```python
    re_path(r"^api/hr/?", include("apps.hr.urls")),
```

- [ ] **Step 5: Chạy — pass.** Run: `pytest apps/hr/tests/test_employee_api.py -v` → PASS.

- [ ] **Step 6: Chạy toàn bộ test hr + ruff**
```bash
pytest apps/hr/ -v
ruff check apps/hr
```
Expected: tất cả PASS, ruff sạch.

- [ ] **Step 7: Commit**
```bash
git add backend/apps/hr/transport/views/ backend/apps/hr/urls.py backend/config/urls.py backend/apps/hr/tests/test_employee_api.py
git commit -m "feat(hr): employee CRUD + org-chart API"
```

---

## Task 12: Data migration — backfill Employee cho User hiện có

**Files:**
- Create: `backend/apps/hr/migrations/0002_backfill_employees.py`
- Test: `backend/apps/hr/tests/test_backfill.py`

- [ ] **Step 1: Viết test thất bại** (test logic backfill bằng cách gọi hàm thuần)
```python
from django.test import TestCase

from apps.hr.migrations_logic import backfill_employees  # hàm thuần, test được
from apps.hr.models import Employee
from apps.users.models import Department, User


class BackfillTests(TestCase):
    def test_creates_employee_per_active_user_skipping_bots(self):
        d = Department.objects.create(name="Sales")
        User.objects.create_user(username="staff1", password="x", department=d, is_active=True)
        User.objects.create_user(username="lark", password="x", is_active=True)  # bot, bỏ qua
        User.objects.create_user(username="old", password="x", is_active=False)  # inactive, bỏ
        backfill_employees(User, Department, Employee, bot_usernames={"lark"})
        self.assertEqual(Employee.objects.count(), 1)
        emp = Employee.objects.get()
        self.assertEqual(emp.user.username, "staff1")
        self.assertEqual(emp.department_id, d.id)
        self.assertTrue(emp.employee_code.startswith("NV"))

    def test_idempotent(self):
        User.objects.create_user(username="staff1", password="x", is_active=True)
        backfill_employees(User, Department, Employee, bot_usernames=set())
        backfill_employees(User, Department, Employee, bot_usernames=set())
        self.assertEqual(Employee.objects.count(), 1)
```

- [ ] **Step 2: Chạy — fail.** Run: `pytest apps/hr/tests/test_backfill.py -v` → FAIL.

- [ ] **Step 3: Implement logic thuần + migration**

`backend/apps/hr/migrations_logic.py`:
```python
from __future__ import annotations

from apps.hr.domain.employee_code import next_employee_code

DEFAULT_BOT_USERNAMES = {"lark"}


def backfill_employees(User, Department, Employee, *, bot_usernames=None) -> int:
    bot_usernames = bot_usernames if bot_usernames is not None else DEFAULT_BOT_USERNAMES
    existing_user_ids = set(Employee.objects.values_list("user_id", flat=True))
    latest = (
        Employee.objects.order_by("-employee_code")
        .values_list("employee_code", flat=True)
        .first()
    )
    created = 0
    qs = User.objects.filter(is_active=True, deleted_at__isnull=True).order_by("id")
    for user in qs:
        if user.id in existing_user_ids or user.username in bot_usernames:
            continue
        if getattr(user, "is_superuser", False) and user.username in bot_usernames:
            continue
        latest = next_employee_code(latest)
        full_name = (user.display_name or "").strip() or \
            (f"{user.first_name} {user.last_name}".strip()) or user.username
        Employee.objects.create(
            user=user, employee_code=latest, full_name=full_name,
            department_id=user.department_id, employment_status="active",
        )
        created += 1
    return created
```

`backend/apps/hr/migrations/0002_backfill_employees.py`:
```python
from django.db import migrations

from apps.hr.migrations_logic import DEFAULT_BOT_USERNAMES


def run(apps, schema_editor):
    from apps.hr.migrations_logic import backfill_employees
    User = apps.get_model("users", "User")
    Department = apps.get_model("users", "Department")
    Employee = apps.get_model("hr", "Employee")
    backfill_employees(User, Department, Employee, bot_usernames=set(DEFAULT_BOT_USERNAMES))


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("hr", "0001_initial"), ("users", "0018_seed_hr_permissions")]
    operations = [migrations.RunPython(run, noop)]
```
> ⚠️ Lưu ý dùng `apps.get_model` historical models trong migration; `backfill_employees` không gọi method runtime nào ngoài ORM cơ bản nên an toàn. `next_employee_code` là hàm thuần — import an toàn.

- [ ] **Step 4: Chạy — pass.** Run: `pytest apps/hr/tests/test_backfill.py -v` → PASS.

- [ ] **Step 5: Verify migrate sạch trên DB test**

Run: `python manage.py migrate hr` (trên DB ephemeral) → không lỗi.

- [ ] **Step 6: Commit**
```bash
git add backend/apps/hr/migrations_logic.py backend/apps/hr/migrations/0002_backfill_employees.py backend/apps/hr/tests/test_backfill.py
git commit -m "feat(hr): backfill employees from existing users"
```

---

## Task 13: Frontend — types + queries + API client

**Files:**
- Create: `frontend/src/features/hr/hr-types.ts`, `frontend/src/lib/api/hr.ts`, `frontend/src/features/hr/queries.ts`
- Test: `frontend/src/features/hr/__tests__/queries.test.ts` (smoke test key factory)

- [ ] **Step 1: Viết test thất bại**
```typescript
import { describe, expect, it } from "vitest";
import { employeeKeys } from "@/features/hr/queries";

describe("employeeKeys", () => {
  it("profiles key includes params", () => {
    expect(employeeKeys.list({ limit: 20, offset: 0 })).toEqual([
      "hr", "employees", { limit: 20, offset: 0 },
    ]);
  });
});
```

- [ ] **Step 2: Chạy — fail.** Run: `npm run test -- src/features/hr` → FAIL.

- [ ] **Step 3: Implement types + api + queries**

`frontend/src/features/hr/hr-types.ts`:
```typescript
export type EmploymentStatus = "probation" | "active" | "on_leave" | "resigned";

export interface Employee {
  id: number;
  employee_code: string;
  full_name: string;
  department: number | null;
  department_name: string;
  manager: number | null;
  manager_name: string;
  job_title: string;
  employment_status: EmploymentStatus;
  hire_date: string | null;
  phone: string;
  personal_email: string;
  username: string;
}

export interface EmployeeListResponse {
  items: Employee[];
  total: number;
  limit: number;
  offset: number;
}

export interface EmployeeListParams {
  limit: number;
  offset: number;
  q?: string;
  department?: number;
  status?: EmploymentStatus;
}

export interface EmployeeCreatePayload {
  full_name: string;
  username: string;
  department_id?: number | null;
  create_login?: boolean;
  password?: string;
  job_title?: string;
  manager_id?: number | null;
  phone?: string;
  personal_email?: string;
}
```

`frontend/src/lib/api/hr.ts`:
```typescript
import { fetchJson } from "@/services/http";
import type {
  Employee, EmployeeCreatePayload, EmployeeListParams, EmployeeListResponse,
} from "@/features/hr/hr-types";

const API_BASE = "/api/hr";

export async function getEmployees(params: EmployeeListParams): Promise<EmployeeListResponse> {
  const q = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
  if (params.q) q.set("q", params.q);
  if (params.department) q.set("department", String(params.department));
  if (params.status) q.set("status", params.status);
  return fetchJson(`${API_BASE}/employees?${q.toString()}`);
}

export async function createEmployee(payload: EmployeeCreatePayload): Promise<Employee> {
  return fetchJson(`${API_BASE}/employees`, { method: "POST", body: JSON.stringify(payload) });
}

export async function updateEmployee(id: number, payload: Partial<EmployeeCreatePayload>): Promise<Employee> {
  return fetchJson(`${API_BASE}/employees/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export async function deleteEmployee(id: number): Promise<void> {
  await fetchJson(`${API_BASE}/employees/${id}`, { method: "DELETE" });
}
```

`frontend/src/features/hr/queries.ts`:
```typescript
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createEmployee, deleteEmployee, getEmployees, updateEmployee,
} from "@/lib/api/hr";
import type { EmployeeCreatePayload, EmployeeListParams } from "@/features/hr/hr-types";

export const employeeKeys = {
  all: ["hr", "employees"] as const,
  list: (params: EmployeeListParams) => ["hr", "employees", params] as const,
};

export function useEmployees(params: EmployeeListParams) {
  return useQuery({ queryKey: employeeKeys.list(params), queryFn: () => getEmployees(params) });
}

export function useCreateEmployee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: EmployeeCreatePayload) => createEmployee(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: employeeKeys.all }),
  });
}

export function useUpdateEmployee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<EmployeeCreatePayload> }) =>
      updateEmployee(id, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: employeeKeys.all }),
  });
}

export function useDeleteEmployee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteEmployee(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: employeeKeys.all }),
  });
}
```

- [ ] **Step 4: Chạy — pass.** Run: `npm run test -- src/features/hr` → PASS.

- [ ] **Step 5: Commit**
```bash
git add frontend/src/features/hr/hr-types.ts frontend/src/lib/api/hr.ts frontend/src/features/hr/queries.ts frontend/src/features/hr/__tests__/queries.test.ts
git commit -m "feat(hr): frontend types, api client, queries"
```

---

## Task 14: Frontend — i18n + module registry + routes

**Files:**
- Create: `frontend/src/i18n/messages/hr.ts`
- Modify: `frontend/src/i18n/messages.ts`, `frontend/src/app/modules.ts`, `frontend/src/app/routes.tsx`, `frontend/src/app/routed-pages.tsx`

- [ ] **Step 1: Tạo i18n**

`frontend/src/i18n/messages/hr.ts`:
```typescript
export const hr = {
  vi: {
    "sidebar.group.hr": "Nhân sự",
    "module.hr": "Nhân sự",
    "topnav.tabs.employees": "Nhân viên",
    "hr.create.title": "Thêm nhân viên",
    "hr.edit.title": "Sửa nhân viên",
    "hr.field.fullName": "Họ tên",
    "hr.field.code": "Mã NV",
    "hr.field.department": "Phòng ban",
    "hr.field.jobTitle": "Chức danh",
    "hr.field.status": "Trạng thái",
    "hr.field.phone": "Điện thoại",
  },
  en: {
    "sidebar.group.hr": "HR",
    "module.hr": "HR",
    "topnav.tabs.employees": "Employees",
    "hr.create.title": "Add employee",
    "hr.edit.title": "Edit employee",
    "hr.field.fullName": "Full name",
    "hr.field.code": "Code",
    "hr.field.department": "Department",
    "hr.field.jobTitle": "Job title",
    "hr.field.status": "Status",
    "hr.field.phone": "Phone",
  },
} as const;
```

Trong `frontend/src/i18n/messages.ts`: import `{ hr }` và spread `...hr.vi` / `...hr.en`.

- [ ] **Step 2: Đăng ký module**

Trong `frontend/src/app/modules.ts`, thêm vào mảng module (sau `sales`/trước admin):
```typescript
{
  key: "hr",
  labelKey: "sidebar.group.hr",
  shortLabelKey: "module.hr",
  icon: Users,
  basePath: "/hr",
  sections: [
    { key: "employees", labelKey: "topnav.tabs.employees", icon: Users },
  ],
},
```
> Import `Users` từ `lucide-react` nếu chưa có. `AppSection` type cần thêm `"employees"` — tìm khai báo `AppSection` (cùng file hoặc nơi định nghĩa section keys) và thêm `"employees"`.

- [ ] **Step 3: Route + lazy page**

Trong `frontend/src/app/routed-pages.tsx`:
```typescript
const EmployeesPage = lazy(() => import("@/features/hr/pages/EmployeesPage"));
export function EmployeesRoute() {
  return <EmployeesPage />;
}
```
Trong `frontend/src/app/routes.tsx`, thêm:
```typescript
{
  path: "hr/employees",
  element: (
    <Guard section="employees">
      <EmployeesRoute />
    </Guard>
  ),
},
```
> Import `EmployeesRoute` trong routes.tsx theo pattern hiện có.

- [ ] **Step 4: Verify build typecheck**

Run: `npm run build` (hoặc `tsc --noEmit`) → không lỗi type. (Sẽ fail cho tới khi Task 15 tạo `EmployeesPage` — nếu chạy tuần tự, để verify ở Task 15.)

- [ ] **Step 5: Commit**
```bash
git add frontend/src/i18n/messages/hr.ts frontend/src/i18n/messages.ts frontend/src/app/modules.ts frontend/src/app/routes.tsx frontend/src/app/routed-pages.tsx
git commit -m "feat(hr): register hr module, routes, i18n"
```

---

## Task 15: Frontend — EmployeesPage + Table + FormDialog

**Files:**
- Create: `frontend/src/features/hr/pages/EmployeesPage.tsx`, `frontend/src/features/hr/components/EmployeesTable.tsx`, `frontend/src/features/hr/components/EmployeeFormDialog.tsx`
- Test: `frontend/src/features/hr/__tests__/EmployeesPage.test.tsx`

- [ ] **Step 1: Viết test thất bại** (mock api, render, assert hiển thị + mở form)
```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import { renderWithI18n } from "@/test/render-with-i18n";

vi.mock("@/lib/api/hr", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/hr")>("@/lib/api/hr");
  return { ...actual, getEmployees: vi.fn() };
});
import { getEmployees } from "@/lib/api/hr";
import EmployeesPage from "@/features/hr/pages/EmployeesPage";

beforeEach(() => {
  vi.mocked(getEmployees).mockResolvedValue({
    items: [{
      id: 1, employee_code: "NV0001", full_name: "Nguyen Van A", department: 1,
      department_name: "Sales", manager: null, manager_name: "", job_title: "Sale",
      employment_status: "active", hire_date: null, phone: "", personal_email: "",
      username: "nva",
    }],
    total: 1, limit: 50, offset: 0,
  });
});
afterEach(cleanup);

describe("EmployeesPage", () => {
  it("renders employee rows", async () => {
    renderWithI18n(<EmployeesPage />);
    expect(await screen.findByText("Nguyen Van A")).toBeInTheDocument();
    expect(screen.getByText("NV0001")).toBeInTheDocument();
  });
});
```
> Cần bọc trong QueryClientProvider — kiểm tra `renderWithI18n` đã gồm chưa; nếu chưa, dùng helper render kèm QueryClient có sẵn trong dự án hoặc tạo wrapper cục bộ trong test.

- [ ] **Step 2: Chạy — fail.** Run: `npm run test -- src/features/hr` → FAIL.

- [ ] **Step 3: Implement** (table + form dùng `FormDialogShell` + `FormGrid`; theo pattern `customers`/`CustomerFormDialog`). Page: state phân trang + search + nút "Thêm NV" mở dialog; table render cột Mã NV, Họ tên, Phòng ban, Chức danh, Trạng thái, hành động Sửa/Xóa. Form gọi `useCreateEmployee`/`useUpdateEmployee`.

> Triển khai cụ thể bám sát `frontend/src/features/customers/components/CustomersTable.tsx` và `CustomerFormDialog.tsx`. Giữ mỗi file < 400 dòng; tách presentational nếu cần.

- [ ] **Step 4: Chạy — pass.** Run: `npm run test -- src/features/hr` → PASS.

- [ ] **Step 5: Typecheck + build**

Run: `npm run build` → không lỗi.

- [ ] **Step 6: Commit**
```bash
git add frontend/src/features/hr/pages frontend/src/features/hr/components frontend/src/features/hr/__tests__/EmployeesPage.test.tsx
git commit -m "feat(hr): employees page, table, form dialog"
```

---

## Task 16: Tổng kiểm tra + ghi chú sprint

**Files:**
- Modify: `docs/erp-hr.md` (thêm mục "Sprint 1 — đã làm/chưa làm")

- [ ] **Step 1: Chạy full test BE + FE**
```bash
pytest apps/hr/ -v          # backend
ruff check apps/hr
npm run test -- src/features/hr   # frontend
```
Expected: tất cả PASS.

- [ ] **Step 2: Ghi chú tiến độ** vào cuối `docs/erp-hr.md`: liệt kê đã làm (model, service, API, RBAC, event sync, backfill, FE) và TODO chuyển Sprint 2.

- [ ] **Step 3: Commit + push + tạo MR**
```bash
git add docs/erp-hr.md
git commit -m "docs(hr): sprint 1 progress notes"
git push -u origin feature/hr-employee-module
```
Gửi link MR cho người dùng.

---

## Self-review (đã rà)
- **Spec coverage:** model/mapping (T2,T7), org-chart (T4,T11), department→RBAC event (T5,T9), RBAC section (T8), API (T10,T11), backfill (T12), FE (T13–15) — phủ đủ §2 spec.
- **Type consistency:** `EmployeeService.create/update/resign`, `EmployeeDepartmentChanged(user_id, department_id, employee_id)`, `next_employee_code`, `assert_no_cycle` dùng nhất quán across tasks.
- **Điểm cần xác minh khi code (đã chú thích inline):** chữ ký `UserDepartment.is_primary`, `bump_rbac_cache_version` import path, `AppSection` union FE, QueryClientProvider trong test helper, số migration users mới nhất.
