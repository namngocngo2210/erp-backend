# HR Sprint 3 — Chấm công & Nghỉ phép — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.
> **Tiền đề:** Sprint 1 & 2 đã merge.

**Goal:** Quản lý đơn nghỉ phép (workflow tạo → duyệt/từ chối) và bản ghi chấm công cơ bản trong ERP.

> ⚠️ **Quyết định build-vs-Lark cần xác nhận trước khi bắt đầu sprint này.** Theo `erp-master-plan.md`, Lark đang làm chấm công tốt. Plan này giả định **build native phần Nghỉ phép** (vì gắn workflow duyệt nội bộ + ảnh hưởng lương) và **Chấm công ở mức ghi nhận tối thiểu** (có thể thay bằng connector đồng bộ từ Lark sau). Nếu chọn tích hợp Lark hoàn toàn, thay Task 1–5 bằng 1 task connector đồng bộ qua Lark API.

**Architecture:** Thêm `LeaveRequest` (workflow duyệt) và `AttendanceRecord` (ghi nhận công) trong `apps/hr`. Nghỉ phép được duyệt phát event `LeaveApproved`.

**Tech Stack:** như Sprint 1–2.

Spec gốc: `docs/erp-hr.md` §3 (Sprint 3).

---

## File structure
- `backend/apps/hr/models.py` — `LeaveRequest`, `AttendanceRecord`, enums `LeaveType`, `LeaveStatus`
- `backend/apps/hr/domain/leave.py` — quy tắc duyệt (chỉ pending mới duyệt/từ chối được; ngày kết thúc ≥ bắt đầu; tính số ngày)
- `backend/apps/hr/contracts/hr_events.py` — `LeaveApproved`
- `backend/apps/hr/application/services/leave_service.py`, `attendance_service.py`
- `backend/apps/hr/repositories.py` — `LeaveRepository`, `AttendanceRepository`
- `backend/apps/hr/transport/` serializers + views; `urls.py`
- `backend/apps/hr/migrations/0004_leave_attendance.py`
- FE: `frontend/src/features/hr/pages/LeaveRequestsPage.tsx`, components, mở rộng module section `leave`

---

## Task 0 (gate): Xác nhận build-vs-Lark

- [ ] **Step 1:** Hỏi/khẳng định với chủ dự án: build native (Task 1+) HAY connector Lark. Ghi quyết định vào `docs/erp-hr.md`. Nếu Lark → bỏ qua phần dưới, viết lại task connector.

---

## Task 1: Models `LeaveRequest` + `AttendanceRecord`

**Files:** Modify `models.py`; Test `test_leave_attendance_models.py`

- [ ] **Step 1: Test thất bại**
```python
from datetime import date
from django.test import TestCase
from apps.hr.models import Employee, LeaveRequest, LeaveStatus, LeaveType
from apps.users.models import User


class LeaveModelTests(TestCase):
    def test_leave_defaults_pending(self):
        u = User.objects.create_user(username="nv", password="x")
        emp = Employee.objects.create(user=u, employee_code="NV0001", full_name="A")
        lr = LeaveRequest.objects.create(
            employee=emp, leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 7, 1), end_date=date(2026, 7, 3), days=3,
        )
        self.assertEqual(lr.status, LeaveStatus.PENDING)
```

- [ ] **Step 2: Chạy — fail.**
- [ ] **Step 3: Implement**
```python
class LeaveType(models.TextChoices):
    ANNUAL = "annual", "Phép năm"
    SICK = "sick", "Nghỉ ốm"
    UNPAID = "unpaid", "Không lương"
    OTHER = "other", "Khác"


class LeaveStatus(models.TextChoices):
    PENDING = "pending", "Chờ duyệt"
    APPROVED = "approved", "Đã duyệt"
    REJECTED = "rejected", "Từ chối"
    CANCELLED = "cancelled", "Đã huỷ"


class LeaveRequest(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leaves")
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    reason = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=LeaveStatus.choices,
                              default=LeaveStatus.PENDING)
    approver_id = models.BigIntegerField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_leave_requests"
        indexes = [models.Index(fields=["employee", "status"], name="hr_leave_emp_idx")]


class AttendanceRecord(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="attendance")
    work_date = models.DateField()
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=20, default="manual")  # manual | lark
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "hr_attendance"
        constraints = [
            models.UniqueConstraint(fields=["employee", "work_date"],
                                    name="hr_attendance_unique_day"),
        ]
```
- [ ] **Step 4: makemigrations + chạy — pass.** **Step 5: Commit** `feat(hr): leave & attendance models`

---

## Task 2: Domain — quy tắc nghỉ phép

**Files:** Create `domain/leave.py`; Test `test_leave_domain.py`

- [ ] **Step 1: Test thất bại**
```python
from datetime import date
import pytest
from apps.hr.domain.leave import count_leave_days, assert_can_decide, LeaveRuleError
from apps.hr.models import LeaveStatus


def test_count_days_inclusive():
    assert count_leave_days(date(2026, 7, 1), date(2026, 7, 3)) == 3


def test_end_before_start_rejected():
    with pytest.raises(LeaveRuleError):
        count_leave_days(date(2026, 7, 3), date(2026, 7, 1))


def test_only_pending_can_be_decided():
    with pytest.raises(LeaveRuleError):
        assert_can_decide(LeaveStatus.APPROVED)
    assert_can_decide(LeaveStatus.PENDING)  # no raise
```
- [ ] **Step 2: Chạy — fail.**
- [ ] **Step 3: Implement**
```python
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
```
- [ ] **Step 4: Chạy — pass.** **Step 5: Commit** `feat(hr): leave domain rules`

---

## Task 3: Event + repositories

**Files:** Modify `contracts/hr_events.py`, `repositories.py`; Test `test_leave_repo.py`

- [ ] **Step 1: Test thất bại** — `LeaveRepository.pending_for_approver`/`for_employee`; `AttendanceRepository.for_month`.
- [ ] **Step 2: Chạy — fail.**
- [ ] **Step 3: Implement**
```python
# hr_events.py
@dataclass(frozen=True)
class LeaveApproved(DomainEvent):
    event_type: ClassVar[str] = "hr.leave.approved"
    leave_id: int = 0
    employee_id: int = 0
    days: float = 0.0
```
```python
# repositories.py
class LeaveRepository(BaseRepository[LeaveRequest]):
    model = LeaveRequest
    @classmethod
    def for_employee(cls, employee_id: int):
        return cls.active().filter(employee_id=employee_id).order_by("-start_date")

class AttendanceRepository(BaseRepository[AttendanceRecord]):
    model = AttendanceRecord
    @classmethod
    def for_month(cls, employee_id: int, year: int, month: int):
        return cls.active().filter(employee_id=employee_id, work_date__year=year,
                                   work_date__month=month).order_by("work_date")
```
- [ ] **Step 4: Chạy — pass.** **Step 5: Commit** `feat(hr): leave events & repositories`

---

## Task 4: Service — workflow nghỉ phép + chấm công

**Files:** Create `application/services/leave_service.py`, `attendance_service.py`; Test `test_leave_service.py`

- [ ] **Step 1: Test thất bại**
```python
from datetime import date
from django.test import TestCase
from apps.hr.application.services.leave_service import LeaveService
from apps.hr.domain.leave import LeaveRuleError
from apps.hr.models import Employee, LeaveStatus, LeaveType
from apps.users.models import User


class LeaveServiceTests(TestCase):
    def _emp(self):
        u = User.objects.create_user(username="nv", password="x")
        return Employee.objects.create(user=u, employee_code="NV0001", full_name="A")

    def test_request_then_approve(self):
        emp = self._emp()
        lr = LeaveService.request(emp.id, leave_type=LeaveType.ANNUAL,
                                  start_date=date(2026, 7, 1), end_date=date(2026, 7, 3))
        self.assertEqual(int(lr.days), 3)
        with self.captureOnCommitCallbacks(execute=True):
            LeaveService.approve(lr.id, approver_id=99)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveStatus.APPROVED)
        self.assertEqual(lr.approver_id, 99)

    def test_double_approve_rejected(self):
        emp = self._emp()
        lr = LeaveService.request(emp.id, leave_type=LeaveType.SICK,
                                  start_date=date(2026, 7, 1), end_date=date(2026, 7, 1))
        LeaveService.approve(lr.id, approver_id=99)
        with self.assertRaises(LeaveRuleError):
            LeaveService.approve(lr.id, approver_id=99)
```
- [ ] **Step 2: Chạy — fail.**
- [ ] **Step 3: Implement** `leave_service.py`
```python
from __future__ import annotations
from datetime import date
from django.db import transaction
from django.utils import timezone
from apps.common.events import event_bus
from apps.hr.contracts.hr_events import LeaveApproved
from apps.hr.domain.leave import assert_can_decide, count_leave_days
from apps.hr.models import LeaveRequest, LeaveStatus
from apps.hr.repositories import LeaveRepository


class LeaveService:
    @staticmethod
    @transaction.atomic
    def request(employee_id: int, *, leave_type: str, start_date: date,
                end_date: date, reason: str = "") -> LeaveRequest:
        days = count_leave_days(start_date, end_date)
        return LeaveRequest.objects.create(
            employee_id=employee_id, leave_type=leave_type, start_date=start_date,
            end_date=end_date, days=days, reason=reason, status=LeaveStatus.PENDING)

    @staticmethod
    @transaction.atomic
    def approve(leave_id: int, *, approver_id: int) -> LeaveRequest:
        lr = LeaveRepository.active().select_for_update().get(pk=leave_id)
        assert_can_decide(lr.status)
        lr.status = LeaveStatus.APPROVED
        lr.approver_id = approver_id
        lr.decided_at = timezone.now()
        lr.save(update_fields=["status", "approver_id", "decided_at"])
        event_bus.publish_on_commit(LeaveApproved(
            leave_id=lr.id, employee_id=lr.employee_id, days=float(lr.days)))
        return lr

    @staticmethod
    @transaction.atomic
    def reject(leave_id: int, *, approver_id: int) -> LeaveRequest:
        lr = LeaveRepository.active().select_for_update().get(pk=leave_id)
        assert_can_decide(lr.status)
        lr.status = LeaveStatus.REJECTED
        lr.approver_id = approver_id
        lr.decided_at = timezone.now()
        lr.save(update_fields=["status", "approver_id", "decided_at"])
        return lr
```
`attendance_service.py`: `record(employee_id, work_date, check_in, check_out)` — upsert theo (employee, work_date).
- [ ] **Step 4: Chạy — pass.** **Step 5: Commit** `feat(hr): leave workflow & attendance services`

---

## Task 5: API + URLs

**Files:** serializers + views; `urls.py`; Test `test_leave_api.py`

- [ ] **Step 1: Test thất bại**
  - `GET/POST /api/hr/leave-requests` (NV tạo đơn; list theo quyền)
  - `POST /api/hr/leave-requests/{id}/approve` & `/reject` (cần quyền `employees:approve` — thêm action `approve` vào section employees nếu chưa có; bổ sung seed migration users)
  - `GET/POST /api/hr/employees/{id}/attendance`
- [ ] **Step 2: Chạy — fail.**
- [ ] **Step 3: Implement** views (bám pattern Sprint 1). Nếu cần `employees:approve`: thêm `"approve"` vào tuple `employees` trong `rbac_catalog.py` + migration seed `users/00xx_seed_hr_approve.py`.
- [ ] **Step 4: Chạy — pass.** `pytest apps/hr/ -v && ruff check apps/hr`. **Step 5: Commit** `feat(hr): leave & attendance API`

---

## Task 6: Frontend — trang Nghỉ phép + section

**Files:** Modify `modules.ts` (thêm section `leave`), `routes.tsx`, `routed-pages.tsx`, i18n; Create `LeaveRequestsPage.tsx` + components + queries; Test render danh sách + nút duyệt.

- [ ] **Step 1: Test thất bại** (mock api leave, render rows + nút Duyệt cho quyền approve).
- [ ] **Step 2: Chạy — fail.**
- [ ] **Step 3: Implement** types (`LeaveRequest`, `AttendanceRecord`), api, queries (`useLeaveRequests`, `useApproveLeave`, `useRejectLeave`), trang list + dialog tạo đơn (`FormDialogShell`), nút Duyệt/Từ chối hiển thị theo quyền. Thêm section `leave` vào module `hr` trong `modules.ts` + route `/hr/leave`.
- [ ] **Step 4: Chạy — pass.** `npm run build`. **Step 5: Commit** `feat(hr): leave requests UI`

---

## Task 7: Tổng kiểm tra + ghi chú + đóng module HR P1

- [ ] `pytest apps/hr/ -v && ruff check apps/hr && npm run test -- src/features/hr` → PASS.
- [ ] Ghi chú "Sprint 3 — đã làm/chưa làm" + tổng kết HR Phase 1 vào `docs/erp-hr.md`.
- [ ] Commit + push + gửi MR.

---

## Self-review
- **Spec coverage (§3 Sprint 3):** nghỉ phép + workflow duyệt (T1,T2,T4,T5), chấm công cơ bản (T1,T4,T5), cờ build-vs-Lark (T0) — đủ.
- **Type consistency:** `LeaveService.request/approve/reject`, `count_leave_days(start,end)`, `assert_can_decide(status)`, `LeaveApproved(leave_id, employee_id, days)` nhất quán.
- **Mở rộng RBAC:** nếu dùng `employees:approve`, phải seed permission qua migration users — đã ghi rõ ở T5.
