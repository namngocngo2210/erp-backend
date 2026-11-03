# HR Sprint 2 — Hợp đồng & Lương cơ bản — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.
> **Tiền đề:** Sprint 1 đã merge (`apps/hr` + `Employee` tồn tại).

**Goal:** Quản lý hợp đồng lao động và mức lương của nhân viên với **lịch sử immutable** (append-only) theo kỷ luật dữ liệu tài chính.

**Architecture:** Thêm 2 bảng con của `Employee` trong `apps/hr`: `EmploymentContract` và `SalaryRecord`. Mọi thay đổi lương = tạo bản ghi mới (không UPDATE/DELETE). Phát event `SalaryChanged` để module Kế toán (Phase 2) tiêu thụ sau.

**Tech Stack:** như Sprint 1.

Spec gốc: `docs/erp-hr.md` §3 (Sprint 2). Pattern bám `apps/hr` đã tạo ở Sprint 1.

---

## File structure
- `backend/apps/hr/models.py` — thêm `EmploymentContract`, `SalaryRecord`, enum `ContractType`
- `backend/apps/hr/domain/salary.py` — quy tắc: chỉ 1 lương "current" tại 1 thời điểm, validate khoảng hiệu lực
- `backend/apps/hr/contracts/hr_events.py` — thêm `SalaryChanged`, `ContractSigned`
- `backend/apps/hr/application/services/contract_service.py`, `salary_service.py`
- `backend/apps/hr/repositories.py` — thêm `ContractRepository`, `SalaryRepository`
- `backend/apps/hr/transport/serializers/` + `views/` — endpoint contract & salary
- `backend/apps/hr/urls.py` — thêm route
- `backend/apps/hr/migrations/0003_contract_salary.py`
- FE: `frontend/src/features/hr/components/EmployeeContractsTab.tsx`, `EmployeeSalaryTab.tsx`, mở rộng `hr-types.ts`, `lib/api/hr.ts`, `queries.ts`

---

## Task 1: Models `EmploymentContract` + `SalaryRecord`

**Files:** Modify `backend/apps/hr/models.py`; Test `backend/apps/hr/tests/test_contract_salary_models.py`

- [ ] **Step 1: Viết test thất bại**
```python
from datetime import date
from django.test import TestCase
from apps.hr.models import Employee, EmploymentContract, SalaryRecord, ContractType
from apps.users.models import User


class ContractSalaryModelTests(TestCase):
    def _emp(self):
        u = User.objects.create_user(username="nv", password="x")
        return Employee.objects.create(user=u, employee_code="NV0001", full_name="A")

    def test_contract_belongs_to_employee(self):
        emp = self._emp()
        c = EmploymentContract.objects.create(
            employee=emp, contract_type=ContractType.OFFICIAL,
            start_date=date(2026, 1, 1), base_salary=15000000,
        )
        self.assertEqual(list(emp.contracts.all()), [c])

    def test_salary_record_append_only_fields(self):
        emp = self._emp()
        s = SalaryRecord.objects.create(
            employee=emp, amount=15000000, effective_from=date(2026, 1, 1),
        )
        self.assertTrue(s.is_current)
```

- [ ] **Step 2: Chạy — fail.** `pytest apps/hr/tests/test_contract_salary_models.py -v`

- [ ] **Step 3: Implement** (thêm vào `models.py`)
```python
class ContractType(models.TextChoices):
    PROBATION = "probation", "Thử việc"
    OFFICIAL = "official", "Chính thức"
    SEASONAL = "seasonal", "Thời vụ"
    COLLABORATOR = "collaborator", "Cộng tác viên"


class EmploymentContract(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="contracts")
    contract_no = models.CharField(max_length=50, blank=True, default="")
    contract_type = models.CharField(max_length=20, choices=ContractType.choices)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    base_salary = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    file_url = models.CharField(max_length=500, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_contracts"
        indexes = [models.Index(fields=["employee"], name="hr_contract_emp_idx")]


class SalaryRecord(models.Model):
    """Append-only: thay lương = tạo bản ghi mới, không sửa/xoá bản cũ."""
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="salaries")
    amount = models.DecimalField(max_digits=12, decimal_places=0)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=True)
    reason = models.CharField(max_length=255, blank=True, default="")
    created_by_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_salary_records"
        indexes = [
            models.Index(fields=["employee", "is_current"], name="hr_salary_cur_idx"),
        ]
```

- [ ] **Step 4: makemigrations + chạy test — pass.**
```bash
python manage.py makemigrations hr   # -> 0003
pytest apps/hr/tests/test_contract_salary_models.py -v
```

- [ ] **Step 5: Commit** `feat(hr): contract & salary models`

---

## Task 2: Domain — quy tắc lương

**Files:** Create `backend/apps/hr/domain/salary.py`; Test `test_salary_domain.py`

- [ ] **Step 1: Test thất bại** — `effective_from` mới phải > bản current; trả về cập nhật cần áp lên bản cũ (set `is_current=False`, `effective_to`).
```python
from datetime import date
import pytest
from apps.hr.domain.salary import close_previous, SalaryRuleError


def test_new_must_be_after_current():
    with pytest.raises(SalaryRuleError):
        close_previous(current_from=date(2026, 1, 1), new_from=date(2025, 12, 1))


def test_close_previous_sets_effective_to_day_before():
    assert close_previous(current_from=date(2026, 1, 1), new_from=date(2026, 6, 1)) == date(2026, 5, 31)
```

- [ ] **Step 2: Chạy — fail.**

- [ ] **Step 3: Implement**
```python
from __future__ import annotations
from datetime import date, timedelta


class SalaryRuleError(ValueError):
    pass


def close_previous(*, current_from: date, new_from: date) -> date:
    """Trả về effective_to của bản lương cũ khi thêm bản mới hiệu lực ``new_from``."""
    if new_from <= current_from:
        raise SalaryRuleError("Mốc hiệu lực mới phải sau bản lương hiện tại.")
    return new_from - timedelta(days=1)
```

- [ ] **Step 4: Chạy — pass.** **Step 5: Commit** `feat(hr): salary domain rules`

---

## Task 3: Events + repositories

**Files:** Modify `contracts/hr_events.py`, `repositories.py`; Test `test_contract_salary_repo.py`

- [ ] **Step 1: Test thất bại** — `SalaryRepository.current(employee_id)` trả bản `is_current=True`; `ContractRepository.for_employee` trả theo `-start_date`.
- [ ] **Step 2: Chạy — fail.**
- [ ] **Step 3: Implement**
```python
# contracts/hr_events.py — thêm
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
```
```python
# repositories.py — thêm
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
```
- [ ] **Step 4: Chạy — pass.** **Step 5: Commit** `feat(hr): contract/salary events & repositories`

---

## Task 4: Services — contract & salary (append-only)

**Files:** Create `application/services/contract_service.py`, `salary_service.py`; Test `test_contract_salary_service.py`

- [ ] **Step 1: Test thất bại**
```python
from datetime import date
from django.test import TestCase
from apps.hr.application.services.salary_service import SalaryService
from apps.hr.models import Employee, SalaryRecord
from apps.users.models import User


class SalaryServiceTests(TestCase):
    def _emp(self):
        u = User.objects.create_user(username="nv", password="x")
        return Employee.objects.create(user=u, employee_code="NV0001", full_name="A")

    def test_set_salary_closes_previous_and_keeps_one_current(self):
        emp = self._emp()
        with self.captureOnCommitCallbacks(execute=True):
            SalaryService.set_salary(emp.id, amount=15000000, effective_from=date(2026, 1, 1))
            SalaryService.set_salary(emp.id, amount=18000000, effective_from=date(2026, 6, 1))
        self.assertEqual(SalaryRecord.objects.filter(employee=emp, is_current=True).count(), 1)
        cur = SalaryRecord.objects.get(employee=emp, is_current=True)
        self.assertEqual(int(cur.amount), 18000000)
        old = SalaryRecord.objects.get(employee=emp, is_current=False)
        self.assertEqual(old.effective_to, date(2026, 5, 31))
```

- [ ] **Step 2: Chạy — fail.**

- [ ] **Step 3: Implement** `salary_service.py`
```python
from __future__ import annotations
from datetime import date
from django.db import transaction
from apps.common.events import event_bus
from apps.hr.contracts.hr_events import SalaryChanged
from apps.hr.domain.salary import close_previous
from apps.hr.models import SalaryRecord
from apps.hr.repositories import SalaryRepository


class SalaryService:
    @staticmethod
    @transaction.atomic
    def set_salary(employee_id: int, *, amount: int, effective_from: date,
                   reason: str = "", created_by_id: int | None = None) -> SalaryRecord:
        current = SalaryRepository.current(employee_id)
        if current is not None:
            current.effective_to = close_previous(
                current_from=current.effective_from, new_from=effective_from)
            current.is_current = False
            current.save(update_fields=["effective_to", "is_current"])
        record = SalaryRecord.objects.create(
            employee_id=employee_id, amount=amount, effective_from=effective_from,
            is_current=True, reason=reason, created_by_id=created_by_id,
        )
        event_bus.publish_on_commit(SalaryChanged(
            employee_id=employee_id, amount=int(amount),
            effective_from=effective_from.isoformat()))
        return record
```
`contract_service.py`: `sign(employee_id, ...)` tạo `EmploymentContract` + publish `ContractSigned` (tương tự pattern trên).

- [ ] **Step 4: Chạy — pass.** **Step 5: Commit** `feat(hr): contract & salary services (append-only)`

---

## Task 5: API + URLs

**Files:** Create serializers + views cho contract/salary; Modify `urls.py`; Test `test_contract_salary_api.py`

- [ ] **Step 1: Test thất bại** — endpoint cần quyền `employees:edit` để set lương; `employees:view` để xem lịch sử.
  - `GET /api/hr/employees/{id}/salaries` → history
  - `POST /api/hr/employees/{id}/salaries` → tạo bản lương mới
  - `GET/POST /api/hr/employees/{id}/contracts`
- [ ] **Step 2: Chạy — fail.**
- [ ] **Step 3: Implement** (bám pattern view Sprint 1 Task 11; check quyền theo method bằng `resolve_effective_permissions(...).can(...)`).
- [ ] **Step 4: Chạy — pass.** **Step 5:** `pytest apps/hr/ -v && ruff check apps/hr`. **Commit** `feat(hr): contract & salary API`

---

## Task 6: Frontend — tab Hợp đồng & Lương trong chi tiết NV

**Files:** Mở rộng `hr-types.ts`, `lib/api/hr.ts`, `queries.ts`; Create `EmployeeContractsTab.tsx`, `EmployeeSalaryTab.tsx`; Test tab lương render history + form thêm.

- [ ] **Step 1: Test thất bại** (mock api salaries, render history rows).
- [ ] **Step 2: Chạy — fail.**
- [ ] **Step 3: Implement** types (`SalaryRecord`, `Contract`), api (`getSalaries/createSalary/getContracts/createContract`), queries hooks, 2 component tab dùng `FormDialogShell` cho form thêm; hiển thị lương dùng `formatCurrency` từ `@/lib/format`.
- [ ] **Step 4: Chạy — pass.** **Step 5: Commit** `feat(hr): contract & salary tabs UI`

---

## Task 7: Tổng kiểm tra + ghi chú sprint

- [ ] `pytest apps/hr/ -v && ruff check apps/hr && npm run test -- src/features/hr` → PASS.
- [ ] Ghi chú "Sprint 2 — đã làm/chưa làm" vào `docs/erp-hr.md`.
- [ ] Commit + push + gửi MR.

---

## Self-review
- **Spec coverage (§3 Sprint 2):** hợp đồng (T1,T4,T5), lương immutable (T1,T2,T4), event cho kế toán (T3,T4) — đủ.
- **Type consistency:** `SalaryService.set_salary(amount, effective_from)`, `close_previous(current_from, new_from)`, `SalaryChanged(employee_id, amount, effective_from)` nhất quán.
- **Immutability:** lương chỉ INSERT + đóng bản cũ (set flag/effective_to); không expose endpoint sửa/xoá amount của bản đã tạo.
