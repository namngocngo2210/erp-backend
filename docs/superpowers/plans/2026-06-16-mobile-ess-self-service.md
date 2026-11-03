# Mobile Self-Service (ESS) — PWA `/m` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cung cấp một mobile PWA self-service tại `/m` cho nhân viên: chấm công, xem lịch sử chấm công, xin/xem nghỉ phép, xem hồ sơ + đổi mật khẩu — tách khỏi vỏ ERP desktop.

**Architecture:** Backend thêm nhóm endpoint "self" `/api/hr/me/*` (auth-only, employee resolve từ token, không nhận `employee_id`). Frontend thêm feature-slice `features/ess` với mobile shell riêng (bottom-tab nav), tách nhánh tại `main.tsx` cho path `/m` (code-split), dùng lại hạ tầng auth/http/query hiện có. Đóng gói PWA bằng `vite-plugin-pwa` scope `/m`.

**Tech Stack:** Django REST Framework + SimpleJWT (BE); React 18 + Vite + react-router + TanStack Query + shadcn/ui + Tailwind (FE); vitest + RTL (test); vite-plugin-pwa (PWA).

**Spec:** `docs/superpowers/specs/2026-06-16-mobile-ess-design.md`

**Cách chạy test BE** (theo memory `running-backend-tests`): image prod không có `tests/`/pytest; chạy ephemeral container trên network `desi-order_default`. Lệnh cụ thể ở Task 0.

---

## File Structure

**Backend (mới/sửa):**
- Create `backend/apps/hr/transport/serializers/self_service_serializers.py` — `MeProfileSerializer`, `MeLeaveCreateSerializer`.
- Create `backend/apps/hr/transport/views/self_service_views.py` — `me_profile`, `me_attendance`, `me_leave_requests`.
- Modify `backend/apps/hr/urls.py` — đăng ký 3 route `me/*`.
- Create `backend/apps/hr/tests/test_self_service.py` — unit + IDOR test.

**Frontend (mới/sửa):**
- Create `frontend/src/features/ess/` — `MobileApp.tsx`, `MobileLayout.tsx`, `BottomTabNav.tsx`, `MobileLoginView.tsx`, `mobile-router.tsx`, `ess-types.ts`, `queries.ts`, `lib/api.ts`, `pages/{CheckInPage,MyAttendancePage,MyLeavePage,ProfilePage}.tsx`, `__tests__/`.
- Create `frontend/src/features/hr/components/CheckInCore.tsx` — lõi check-in tách ra để dùng chung.
- Modify `frontend/src/features/hr/pages/AttendanceCheckInPage.tsx` — render `CheckInCore`.
- Modify `frontend/src/main.tsx` — nhánh `/m` → `MobileApp`.
- Modify `frontend/vite.config.ts` — thêm `VitePWA`.
- Create `frontend/public/icons/` — icon PWA 192/512 + maskable.

---

## Task 0: Chuẩn bị & xác minh môi trường test

**Files:** none (recon).

- [ ] **Step 1:** Xác minh container chạy & network test BE.

Run:
```bash
docker ps --format '{{.Names}}' | grep -i desi
docker network ls | grep desi-order
```
Expected: thấy container backend (vd `desi-order-backend-1` hoặc tương tự) và network `desi-order_default`.

- [ ] **Step 2:** Chạy thử test BE hiện có để biết lệnh chuẩn.

Run (điều chỉnh tên image/service cho khớp `docker compose config`):
```bash
docker compose -f /home/desi/desi-order/docker-compose.yml run --rm --no-deps \
  -e DJANGO_SETTINGS_MODULE=config.settings backend \
  python -m pytest apps/hr/tests -q
```
Nếu image prod thiếu pytest → dùng ephemeral image dev theo memory `running-backend-tests` (build/run container có `requirements-dev`). Ghi lại lệnh chạy được vào đầu file note để các task sau dùng lại.

- [ ] **Step 3:** Chạy thử FE test.

Run: `cd /home/desi/desi-order/frontend && npx vitest run src/features/hr/__tests__/queries.test.ts`
Expected: PASS (xác nhận toolchain ok). Lưu ý baseline có test FE fail ở mobile-viewport (memory `known-failing-baseline-tests`) — không nhầm là regression.

---

## SPRINT 1 — Backend self API

### Task 1: Serializers cho self endpoints

**Files:**
- Create: `backend/apps/hr/transport/serializers/self_service_serializers.py`

- [ ] **Step 1: Viết serializer**

```python
from __future__ import annotations

from rest_framework import serializers

from apps.hr.models import Employee, LeaveType


class MeProfileSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True, default="")
    manager_name = serializers.CharField(source="manager.full_name", read_only=True, default="")
    job_title_display = serializers.CharField(source="get_job_title_display", read_only=True)
    employment_status_display = serializers.CharField(
        source="get_employment_status_display", read_only=True
    )
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Employee
        # KHÔNG lộ lương/id_number/address ở ESS — chỉ thông tin định danh cơ bản.
        fields = (
            "id", "employee_code", "full_name", "department_name",
            "manager_name", "job_title", "job_title_display",
            "employment_status", "employment_status_display",
            "hire_date", "phone", "personal_email", "username",
        )


class MeLeaveCreateSerializer(serializers.Serializer):
    """Như LeaveCreateSerializer nhưng KHÔNG nhận employee_id (server tự gán)."""

    leave_type = serializers.ChoiceField(choices=LeaveType.choices)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError("Ngày kết thúc không được trước ngày bắt đầu.")
        return attrs
```

- [ ] **Step 2: Commit**

```bash
git add backend/apps/hr/transport/serializers/self_service_serializers.py
git commit -m "feat(hr): self-service serializers for ESS"
```

### Task 2: Views cho self endpoints (TDD)

**Files:**
- Create: `backend/apps/hr/tests/test_self_service.py`
- Create: `backend/apps/hr/transport/views/self_service_views.py`
- Modify: `backend/apps/hr/urls.py`

- [ ] **Step 1: Viết test thất bại**

Tham khảo các test HR sẵn có để dùng đúng factory/fixture (đọc `backend/apps/hr/tests/` để biết cách tạo `User`+`Employee`+token). Viết `test_self_service.py`:

```python
from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.hr.models import Employee, LeaveRequest
from apps.users.models import User


def _auth_client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def emp_user(db):
    user = User.objects.create_user(username="nv01", password="x")
    emp = Employee.objects.create(user=user, full_name="Nguyen Van A", employee_code="NV01")
    return user, emp


@pytest.mark.django_db
def test_me_profile_returns_own_profile(emp_user):
    user, emp = emp_user
    res = _auth_client(user).get("/api/hr/me/profile")
    assert res.status_code == 200
    assert res.data["employee_code"] == "NV01"
    assert "id_number" not in res.data  # không lộ trường nhạy cảm


@pytest.mark.django_db
def test_me_profile_404_when_no_employee():
    user = User.objects.create_user(username="ghost", password="x")
    res = _auth_client(user).get("/api/hr/me/profile")
    assert res.status_code == 404


@pytest.mark.django_db
def test_me_leave_create_and_list_self_only(emp_user):
    user, emp = emp_user
    other_user = User.objects.create_user(username="nv02", password="x")
    other_emp = Employee.objects.create(user=other_user, full_name="B", employee_code="NV02")
    LeaveRequest.objects.create(
        employee_id=other_emp.id, leave_type="annual",
        start_date=dt.date(2026, 6, 1), end_date=dt.date(2026, 6, 2), days=2, status="pending",
    )
    client = _auth_client(user)
    create = client.post(
        "/api/hr/me/leave-requests",
        {"leave_type": "annual", "start_date": "2026-06-10",
         "end_date": "2026-06-11", "reason": "viec rieng"},
        format="json",
    )
    assert create.status_code == 201
    listed = client.get("/api/hr/me/leave-requests")
    assert listed.status_code == 200
    emp_ids = {item["employee"] for item in listed.data["items"]}
    assert emp_ids == {emp.id}  # KHÔNG thấy đơn của nv02


@pytest.mark.django_db
def test_me_leave_create_ignores_injected_employee_id(emp_user):
    user, emp = emp_user
    other_user = User.objects.create_user(username="nv03", password="x")
    other_emp = Employee.objects.create(user=other_user, full_name="C", employee_code="NV03")
    res = _auth_client(user).post(
        "/api/hr/me/leave-requests",
        {"employee_id": other_emp.id, "leave_type": "annual",
         "start_date": "2026-06-10", "end_date": "2026-06-11"},
        format="json",
    )
    assert res.status_code == 201
    assert LeaveRequest.objects.get(pk=res.data["id"]).employee_id == emp.id  # gán cho A, không phải C


@pytest.mark.django_db
def test_me_attendance_returns_items(emp_user):
    user, emp = emp_user
    res = _auth_client(user).get("/api/hr/me/attendance?month=2026-06")
    assert res.status_code == 200
    assert "items" in res.data
```

> Lưu ý: nếu dự án dùng factory/fixture khác (vd `pytest-django` settings, helper tạo user khác), điều chỉnh `emp_user`/`User.objects.create_user` cho khớp pattern trong `apps/hr/tests/` hiện có. Giữ nguyên các assert hành vi.

- [ ] **Step 2: Chạy test — kỳ vọng FAIL**

Run (lệnh chuẩn từ Task 0):
```bash
docker compose -f /home/desi/desi-order/docker-compose.yml run --rm --no-deps backend \
  python -m pytest apps/hr/tests/test_self_service.py -q
```
Expected: FAIL — 404 (route chưa tồn tại).

- [ ] **Step 3: Viết view**

`backend/apps/hr/transport/views/self_service_views.py`:

```python
from __future__ import annotations

from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.hr.application.services.leave_service import LeaveService
from apps.hr.repositories import AttendanceRepository, LeaveRepository
from apps.hr.transport.serializers.leave_attendance_serializers import (
    AttendanceSerializer,
    LeaveRequestSerializer,
)
from apps.hr.transport.serializers.self_service_serializers import (
    MeLeaveCreateSerializer,
    MeProfileSerializer,
)


def _employee(request):
    return getattr(request.user, "employee", None)


def _no_employee():
    return Response(
        {"detail": "Tài khoản chưa gắn hồ sơ nhân viên."},
        status=status.HTTP_404_NOT_FOUND,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def me_profile(request):
    emp = _employee(request)
    if not emp:
        return _no_employee()
    return Response(MeProfileSerializer(emp).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@never_cache
def me_attendance(request):
    emp = _employee(request)
    if not emp:
        return _no_employee()
    month = request.query_params.get("month")  # "YYYY-MM"
    if month:
        year_s, month_s = month.split("-")
        qs = AttendanceRepository.for_month(emp.id, int(year_s), int(month_s))
    else:
        now = timezone.localdate()
        qs = AttendanceRepository.for_month(emp.id, now.year, now.month)
    return Response({"items": AttendanceSerializer(qs, many=True).data})


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@never_cache
def me_leave_requests(request):
    emp = _employee(request)
    if not emp:
        return _no_employee()

    if request.method == "GET":
        qs = LeaveRepository.active().filter(employee_id=emp.id).order_by("-start_date")
        return Response({"items": LeaveRequestSerializer(qs, many=True).data})

    ser = MeLeaveCreateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data
    lr = LeaveService.request(
        emp.id,  # luôn từ token, bỏ qua mọi employee_id client gửi
        leave_type=data["leave_type"],
        start_date=data["start_date"],
        end_date=data["end_date"],
        reason=data["reason"],
    )
    return Response(LeaveRequestSerializer(lr).data, status=status.HTTP_201_CREATED)
```

- [ ] **Step 4: Đăng ký route** trong `backend/apps/hr/urls.py`

Thêm import:
```python
from apps.hr.transport.views.self_service_views import (
    me_attendance,
    me_leave_requests,
    me_profile,
)
```
Thêm vào `urlpatterns` (đặt TRƯỚC các route `employees/<int:...>` để tránh nhầm, nhưng vì prefix khác `me/` nên thứ tự an toàn):
```python
    path("me/profile", me_profile, name="hr-me-profile"),
    path("me/attendance", me_attendance, name="hr-me-attendance"),
    path("me/leave-requests", me_leave_requests, name="hr-me-leave-requests"),
```

- [ ] **Step 5: Chạy test — kỳ vọng PASS**

Run: `... python -m pytest apps/hr/tests/test_self_service.py -q`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/hr/transport/views/self_service_views.py backend/apps/hr/urls.py backend/apps/hr/tests/test_self_service.py
git commit -m "feat(hr): self-service endpoints /api/hr/me/* with IDOR-safe scoping"
```

---

## SPRINT 2 — Mobile shell

### Task 3: Tách lõi check-in dùng chung (`CheckInCore`)

**Files:**
- Read first: `frontend/src/features/hr/pages/AttendanceCheckInPage.tsx`
- Create: `frontend/src/features/hr/components/CheckInCore.tsx`
- Modify: `frontend/src/features/hr/pages/AttendanceCheckInPage.tsx`

- [ ] **Step 1:** Đọc `AttendanceCheckInPage.tsx` đầy đủ. Xác định phần lõi: state camera (`useCamera`), GPS (`useGeolocation`), tính khoảng cách Haversine, `useAttendanceConfig`, `useCheckIn`, UI nút chụp + submit. **Loại trừ** mọi thứ thuộc app-shell ERP (breadcrumb, page header dùng layout desktop nếu có).

- [ ] **Step 2:** Tạo `CheckInCore.tsx` chứa toàn bộ logic + UI lõi đó, export `export function CheckInCore() { ... }`. Di chuyển (cut) code từ page sang, giữ nguyên hành vi. Component không phụ thuộc context ERP nào ngoài các hook/query đã import.

- [ ] **Step 3:** Sửa `AttendanceCheckInPage.tsx` để chỉ render wrapper + `<CheckInCore />`:
```tsx
import { CheckInCore } from "@/features/hr/components/CheckInCore";

export default function AttendanceCheckInPage() {
  return <CheckInCore />;
}
```
(Giữ lại default export đúng tên đang được `routed-pages.tsx` import — kiểm tra cách nó được export/import và bảo toàn.)

- [ ] **Step 4: Chạy test hồi quy** check-in cũ:

Run: `cd frontend && npx vitest run src/features/hr/__tests__/AttendanceCheckInPage.test.tsx`
Expected: PASS như trước (nếu test reference cấu trúc DOM cũ mà đổi, cập nhật test cho khớp `CheckInCore`).

- [ ] **Step 5: Commit**
```bash
git add frontend/src/features/hr/components/CheckInCore.tsx frontend/src/features/hr/pages/AttendanceCheckInPage.tsx frontend/src/features/hr/__tests__/AttendanceCheckInPage.test.tsx
git commit -m "refactor(hr): extract reusable CheckInCore from AttendanceCheckInPage"
```

### Task 4: ESS API client + types + queries

**Files:**
- Create: `frontend/src/features/ess/ess-types.ts`
- Create: `frontend/src/features/ess/lib/api.ts`
- Create: `frontend/src/features/ess/queries.ts`

- [ ] **Step 1:** `ess-types.ts`:
```ts
export type MeProfile = {
  id: number;
  employee_code: string;
  full_name: string;
  department_name: string;
  manager_name: string;
  job_title: string;
  job_title_display: string;
  employment_status: string;
  employment_status_display: string;
  hire_date: string | null;
  phone: string;
  personal_email: string;
  username: string;
};

export type MeAttendanceItem = {
  id: number;
  work_date: string;
  check_in: string | null;
  check_out: string | null;
  note: string;
  source: string;
  created_at: string;
};

export type LeaveType = "annual" | "sick" | "unpaid" | "other";
export type LeaveStatus = "pending" | "approved" | "rejected" | "cancelled";

export type MeLeaveItem = {
  id: number;
  employee: number;
  leave_type: LeaveType;
  start_date: string;
  end_date: string;
  days: string;
  reason: string;
  status: LeaveStatus;
  decided_at: string | null;
  created_at: string;
};

export type CreateLeavePayload = {
  leave_type: LeaveType;
  start_date: string;
  end_date: string;
  reason?: string;
};
```

- [ ] **Step 2:** `lib/api.ts`:
```ts
import { fetchJson } from "@/services/http";
import type {
  CreateLeavePayload,
  MeAttendanceItem,
  MeLeaveItem,
  MeProfile,
} from "@/features/ess/ess-types";

export function getMyProfile(): Promise<MeProfile> {
  return fetchJson("/api/hr/me/profile");
}

export async function getMyAttendance(month: string): Promise<MeAttendanceItem[]> {
  const res: { items: MeAttendanceItem[] } = await fetchJson(
    `/api/hr/me/attendance?month=${month}`,
  );
  return res.items;
}

export async function getMyLeave(): Promise<MeLeaveItem[]> {
  const res: { items: MeLeaveItem[] } = await fetchJson("/api/hr/me/leave-requests");
  return res.items;
}

export function createMyLeave(payload: CreateLeavePayload): Promise<MeLeaveItem> {
  return fetchJson("/api/hr/me/leave-requests", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
```

- [ ] **Step 3:** `queries.ts`:
```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createMyLeave,
  getMyAttendance,
  getMyLeave,
  getMyProfile,
} from "@/features/ess/lib/api";

export function useMyProfile() {
  return useQuery({ queryKey: ["ess", "profile"], queryFn: getMyProfile });
}

export function useMyAttendance(month: string) {
  return useQuery({
    queryKey: ["ess", "attendance", month],
    queryFn: () => getMyAttendance(month),
  });
}

export function useMyLeave() {
  return useQuery({ queryKey: ["ess", "leave"], queryFn: getMyLeave });
}

export function useCreateMyLeave() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createMyLeave,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ess", "leave"] }),
  });
}
```

- [ ] **Step 4: Commit**
```bash
git add frontend/src/features/ess/ess-types.ts frontend/src/features/ess/lib/api.ts frontend/src/features/ess/queries.ts
git commit -m "feat(ess): api client, types and react-query hooks"
```

### Task 5: Mobile shell (Layout + BottomTabNav + Login + Router + App)

**Files:**
- Create: `frontend/src/features/ess/BottomTabNav.tsx`
- Create: `frontend/src/features/ess/MobileLayout.tsx`
- Create: `frontend/src/features/ess/MobileLoginView.tsx`
- Create: `frontend/src/features/ess/mobile-router.tsx`
- Create: `frontend/src/features/ess/MobileApp.tsx`

- [ ] **Step 1:** `BottomTabNav.tsx` — nav cố định đáy, 4 tab, dùng `NavLink`:
```tsx
import { Clock, CalendarDays, FileText, User } from "lucide-react";
import { NavLink } from "react-router-dom";

const TABS = [
  { to: "/m", end: true, icon: Clock, label: "Chấm công" },
  { to: "/m/lich-su", end: false, icon: CalendarDays, label: "Lịch sử" },
  { to: "/m/nghi-phep", end: false, icon: FileText, label: "Nghỉ phép" },
  { to: "/m/ca-nhan", end: false, icon: User, label: "Cá nhân" },
];

export function BottomTabNav() {
  return (
    <nav className="fixed inset-x-0 bottom-0 z-20 grid grid-cols-4 border-t border-border bg-card">
      {TABS.map(({ to, end, icon: Icon, label }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            `flex flex-col items-center gap-1 py-2 text-xs ${
              isActive ? "text-primary" : "text-muted-foreground"
            }`
          }
        >
          <Icon className="h-5 w-5" />
          <span>{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}
```

- [ ] **Step 2:** `MobileLayout.tsx`:
```tsx
import { Outlet } from "react-router-dom";

import { BottomTabNav } from "@/features/ess/BottomTabNav";

export function MobileLayout() {
  return (
    <div className="flex min-h-[100dvh] flex-col bg-background">
      <main className="flex-1 overflow-y-auto px-4 pb-20 pt-4">
        <Outlet />
      </main>
      <BottomTabNav />
    </div>
  );
}
```

- [ ] **Step 3:** `MobileLoginView.tsx` — login full-screen tự quản state, gọi `/api/users/login` rồi `setUser`:
```tsx
import { FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { AuthUser } from "@/hooks/use-auth-bootstrap";
import { fetchJson } from "@/services/http";

type Props = { onLoggedIn: (user: AuthUser) => void };

export function MobileLoginView({ onLoggedIn }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const data = await fetchJson("/api/users/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      onLoggedIn((data.user ?? data) as AuthUser);
    } catch {
      setError("Sai tài khoản hoặc mật khẩu.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid min-h-[100dvh] place-items-center bg-background p-6">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4">
        <div className="flex flex-col items-center gap-2">
          <img src="/logo.jpg" alt="DESI" className="h-14 w-14 rounded-xl object-contain" />
          <p className="text-lg font-semibold">DESI Nhân viên</p>
        </div>
        <Input placeholder="Tài khoản" value={username} onChange={(e) => setUsername(e.target.value)} />
        <Input
          type="password"
          placeholder="Mật khẩu"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button type="submit" className="w-full" disabled={loading}>
          {loading ? "Đang đăng nhập..." : "Đăng nhập"}
        </Button>
      </form>
    </div>
  );
}
```
> Lưu ý: kiểm tra response thực của `/api/users/login` (trong `App.tsx` login handler dòng ~615) để map đúng `data.user` vs `data`. Bảo toàn cùng cách App đang làm (`const userPayload = ...`).

- [ ] **Step 4:** `mobile-router.tsx` — router con cho `/m`, lazy pages:
```tsx
import { lazy } from "react";
import { createBrowserRouter } from "react-router-dom";

import { MobileLayout } from "@/features/ess/MobileLayout";

const CheckInPage = lazy(() => import("@/features/ess/pages/CheckInPage"));
const MyAttendancePage = lazy(() => import("@/features/ess/pages/MyAttendancePage"));
const MyLeavePage = lazy(() => import("@/features/ess/pages/MyLeavePage"));
const ProfilePage = lazy(() => import("@/features/ess/pages/ProfilePage"));

export const mobileRouter = createBrowserRouter([
  {
    path: "/m",
    element: <MobileLayout />,
    children: [
      { index: true, element: <CheckInPage /> },
      { path: "lich-su", element: <MyAttendancePage /> },
      { path: "nghi-phep", element: <MyLeavePage /> },
      { path: "ca-nhan", element: <ProfilePage /> },
    ],
  },
]);
```

- [ ] **Step 5:** `MobileApp.tsx` — bootstrap auth, hiển thị login khi chưa auth:
```tsx
import { Suspense } from "react";
import { RouterProvider } from "react-router-dom";

import { MobileLoginView } from "@/features/ess/MobileLoginView";
import { mobileRouter } from "@/features/ess/mobile-router";
import { useAuthBootstrap } from "@/hooks/use-auth-bootstrap";

export default function MobileApp() {
  const { user, setUser, authChecking } = useAuthBootstrap();

  if (authChecking) {
    return <div className="grid min-h-[100dvh] place-items-center text-muted-foreground">Đang tải…</div>;
  }
  if (!user) {
    return <MobileLoginView onLoggedIn={(u) => setUser(u)} />;
  }
  return (
    <Suspense fallback={<div className="grid min-h-[100dvh] place-items-center">Đang tải…</div>}>
      <RouterProvider router={mobileRouter} />
    </Suspense>
  );
}
```

- [ ] **Step 6: Commit**
```bash
git add frontend/src/features/ess/BottomTabNav.tsx frontend/src/features/ess/MobileLayout.tsx frontend/src/features/ess/MobileLoginView.tsx frontend/src/features/ess/mobile-router.tsx frontend/src/features/ess/MobileApp.tsx
git commit -m "feat(ess): mobile shell — layout, bottom nav, login, router, app"
```

### Task 6: Nhánh `/m` trong `main.tsx`

**Files:**
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1:** Thêm lazy import + nhánh path. Sửa `main.tsx`:
```tsx
const WarrantyPage = lazy(() => import("./pages/WarrantyPage"));
const MobileApp = lazy(() => import("./features/ess/MobileApp"));

const path = window.location.pathname.toLowerCase();
const isWarrantyRoute = path.startsWith("/warranty");
const isMobileRoute = path.startsWith("/m");
```
Trong cây render, thay block điều kiện:
```tsx
{isWarrantyRoute ? (
  <Suspense fallback={null}>
    <WarrantyPage />
  </Suspense>
) : isMobileRoute ? (
  <Suspense fallback={null}>
    <MobileApp />
  </Suspense>
) : (
  <App />
)}
```
> Cẩn thận: `startsWith("/m")` cũng khớp `/marketing`,... nhưng ERP dùng react-router nội bộ và URL gốc khi tải trang ERP không bắt đầu bằng `/m` trừ các path `/m/...`. Để an toàn dùng điều kiện chặt hơn: `path === "/m" || path.startsWith("/m/")`. Áp dụng bản chặt này.

```tsx
const isMobileRoute = path === "/m" || path.startsWith("/m/");
```

- [ ] **Step 2: Verify build/typecheck**

Run: `cd frontend && npx tsc -p tsconfig.json --noEmit`
Expected: không lỗi type ở các file ESS.

- [ ] **Step 3: Commit**
```bash
git add frontend/src/main.tsx
git commit -m "feat(ess): mount MobileApp on /m route branch"
```

---

## SPRINT 3 — 4 màn hình + test

### Task 7: CheckInPage (ESS)

**Files:**
- Create: `frontend/src/features/ess/pages/CheckInPage.tsx`

- [ ] **Step 1:** Render lõi dùng chung:
```tsx
import { CheckInCore } from "@/features/hr/components/CheckInCore";

export default function CheckInPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Chấm công</h1>
      <CheckInCore />
    </div>
  );
}
```
- [ ] **Step 2: Commit** `git add ... && git commit -m "feat(ess): check-in page"`

### Task 8: MyAttendancePage (TDD)

**Files:**
- Create: `frontend/src/features/ess/__tests__/MyAttendancePage.test.tsx`
- Create: `frontend/src/features/ess/pages/MyAttendancePage.tsx`

- [ ] **Step 1: Test thất bại** — đọc một test FE hr sẵn có (vd `AttendanceHistoryPage.test.tsx`) để copy cách wrap `QueryClientProvider` + mock `fetchJson`/queries. Viết test render danh sách:
```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/features/ess/lib/api", () => ({
  getMyAttendance: vi.fn().mockResolvedValue([
    { id: 1, work_date: "2026-06-10", check_in: "08:00", check_out: "17:30",
      note: "", source: "self", created_at: "" },
  ]),
}));

import MyAttendancePage from "@/features/ess/pages/MyAttendancePage";

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("MyAttendancePage", () => {
  it("hiển thị bản ghi chấm công", async () => {
    wrap(<MyAttendancePage />);
    expect(await screen.findByText("08:00")).toBeInTheDocument();
    expect(screen.getByText("17:30")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — FAIL** `cd frontend && npx vitest run src/features/ess/__tests__/MyAttendancePage.test.tsx` (module chưa tồn tại).

- [ ] **Step 3: Implement** `MyAttendancePage.tsx`:
```tsx
import { useState } from "react";

import { useMyAttendance } from "@/features/ess/queries";

function currentMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export default function MyAttendancePage() {
  const [month, setMonth] = useState(currentMonth());
  const { data, isLoading } = useMyAttendance(month);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Lịch sử chấm công</h1>
        <input
          type="month"
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          className="rounded-md border border-border bg-card px-2 py-1 text-sm"
        />
      </div>
      {isLoading && <p className="text-sm text-muted-foreground">Đang tải…</p>}
      {data && data.length === 0 && (
        <p className="text-sm text-muted-foreground">Chưa có dữ liệu trong tháng.</p>
      )}
      <ul className="space-y-2">
        {data?.map((item) => (
          <li
            key={item.id}
            className="flex items-center justify-between rounded-lg border border-border bg-card px-3 py-2"
          >
            <span className="text-sm font-medium">{item.work_date}</span>
            <span className="text-sm text-muted-foreground">
              {item.check_in ?? "--"} → {item.check_out ?? "--"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `git add ... && git commit -m "feat(ess): my attendance page"`

### Task 9: MyLeavePage + form tạo đơn (TDD)

**Files:**
- Create: `frontend/src/features/ess/__tests__/MyLeavePage.test.tsx`
- Create: `frontend/src/features/ess/pages/MyLeavePage.tsx`

- [ ] **Step 1: Test thất bại** — render list đơn từ mock + có nút tạo:
```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/features/ess/lib/api", () => ({
  getMyLeave: vi.fn().mockResolvedValue([
    { id: 1, employee: 1, leave_type: "annual", start_date: "2026-06-10",
      end_date: "2026-06-11", days: "2", reason: "viec rieng", status: "pending",
      decided_at: null, created_at: "" },
  ]),
  createMyLeave: vi.fn(),
}));

import MyLeavePage from "@/features/ess/pages/MyLeavePage";

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("MyLeavePage", () => {
  it("hiển thị đơn nghỉ và nút tạo", async () => {
    wrap(<MyLeavePage />);
    expect(await screen.findByText(/viec rieng/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /tạo đơn|xin nghỉ/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** `MyLeavePage.tsx` (list + form inline có thể đóng/mở; dùng `useCreateMyLeave`):
```tsx
import { FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { LeaveType } from "@/features/ess/ess-types";
import { useCreateMyLeave, useMyLeave } from "@/features/ess/queries";

const STATUS_LABEL: Record<string, string> = {
  pending: "Chờ duyệt",
  approved: "Đã duyệt",
  rejected: "Từ chối",
  cancelled: "Đã huỷ",
};
const TYPE_LABEL: Record<string, string> = {
  annual: "Phép năm",
  sick: "Nghỉ ốm",
  unpaid: "Không lương",
  other: "Khác",
};

export default function MyLeavePage() {
  const { data, isLoading } = useMyLeave();
  const create = useCreateMyLeave();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    leave_type: "annual" as LeaveType,
    start_date: "",
    end_date: "",
    reason: "",
  });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    await create.mutateAsync(form);
    setOpen(false);
    setForm({ leave_type: "annual", start_date: "", end_date: "", reason: "" });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Nghỉ phép</h1>
        <Button size="sm" onClick={() => setOpen((v) => !v)}>
          {open ? "Đóng" : "Tạo đơn"}
        </Button>
      </div>

      {open && (
        <form onSubmit={submit} className="space-y-2 rounded-lg border border-border bg-card p-3">
          <select
            value={form.leave_type}
            onChange={(e) => setForm({ ...form, leave_type: e.target.value as LeaveType })}
            className="w-full rounded-md border border-border bg-background px-2 py-2 text-sm"
          >
            <option value="annual">Phép năm</option>
            <option value="sick">Nghỉ ốm</option>
            <option value="unpaid">Không lương</option>
            <option value="other">Khác</option>
          </select>
          <Input
            type="date"
            value={form.start_date}
            onChange={(e) => setForm({ ...form, start_date: e.target.value })}
            required
          />
          <Input
            type="date"
            value={form.end_date}
            onChange={(e) => setForm({ ...form, end_date: e.target.value })}
            required
          />
          <Input
            placeholder="Lý do"
            value={form.reason}
            onChange={(e) => setForm({ ...form, reason: e.target.value })}
          />
          <Button type="submit" className="w-full" disabled={create.isPending}>
            {create.isPending ? "Đang gửi…" : "Gửi đơn"}
          </Button>
        </form>
      )}

      {isLoading && <p className="text-sm text-muted-foreground">Đang tải…</p>}
      <ul className="space-y-2">
        {data?.map((item) => (
          <li key={item.id} className="rounded-lg border border-border bg-card px-3 py-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">{TYPE_LABEL[item.leave_type]}</span>
              <span className="text-xs text-muted-foreground">{STATUS_LABEL[item.status]}</span>
            </div>
            <p className="text-xs text-muted-foreground">
              {item.start_date} → {item.end_date} ({item.days} ngày)
            </p>
            {item.reason && <p className="text-xs">{item.reason}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `git add ... && git commit -m "feat(ess): my leave page with create form"`

### Task 10: ProfilePage (hồ sơ + đổi mật khẩu + logout) (TDD)

**Files:**
- Create: `frontend/src/features/ess/__tests__/ProfilePage.test.tsx`
- Create: `frontend/src/features/ess/pages/ProfilePage.tsx`

- [ ] **Step 1: Test thất bại** — render hồ sơ từ mock:
```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/features/ess/lib/api", () => ({
  getMyProfile: vi.fn().mockResolvedValue({
    id: 1, employee_code: "NV01", full_name: "Nguyen Van A",
    department_name: "Sales", manager_name: "Sep", job_title: "staff",
    job_title_display: "Nhân viên", employment_status: "active",
    employment_status_display: "Đang làm", hire_date: "2024-01-01",
    phone: "0900", personal_email: "a@x.vn", username: "nv01",
  }),
}));

import ProfilePage from "@/features/ess/pages/ProfilePage";

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("ProfilePage", () => {
  it("hiển thị hồ sơ nhân viên", async () => {
    wrap(<ProfilePage />);
    expect(await screen.findByText("Nguyen Van A")).toBeInTheDocument();
    expect(screen.getByText("NV01")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** `ProfilePage.tsx` — hồ sơ + form đổi mật khẩu (`/api/users/change-password`) + đăng xuất (`/api/users/logout` rồi `clearAuthTokens()` + chuyển `/m`):
```tsx
import { FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useMyProfile } from "@/features/ess/queries";
import { clearAuthTokens, fetchJson, setAccessToken } from "@/services/http";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between py-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value || "--"}</span>
    </div>
  );
}

export default function ProfilePage() {
  const { data: p } = useMyProfile();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState("");

  const changePassword = async (e: FormEvent) => {
    e.preventDefault();
    setMsg("");
    try {
      const res = await fetchJson("/api/users/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: cur, new_password: next }),
      });
      if (res?.access) setAccessToken(res.access);
      setMsg("Đổi mật khẩu thành công.");
      setCur("");
      setNext("");
    } catch {
      setMsg("Đổi mật khẩu thất bại. Kiểm tra lại mật khẩu hiện tại.");
    }
  };

  const logout = async () => {
    try {
      await fetchJson("/api/users/logout", { method: "POST" });
    } catch {
      // ignore
    }
    clearAuthTokens();
    window.location.assign("/m");
  };

  return (
    <div className="space-y-5">
      <h1 className="text-lg font-semibold">Thông tin cá nhân</h1>
      <div className="rounded-lg border border-border bg-card px-3 py-2">
        <Row label="Họ tên" value={p?.full_name ?? ""} />
        <Row label="Mã NV" value={p?.employee_code ?? ""} />
        <Row label="Phòng ban" value={p?.department_name ?? ""} />
        <Row label="Chức danh" value={p?.job_title_display ?? ""} />
        <Row label="Quản lý" value={p?.manager_name ?? ""} />
        <Row label="Trạng thái" value={p?.employment_status_display ?? ""} />
        <Row label="Ngày vào làm" value={p?.hire_date ?? ""} />
        <Row label="SĐT" value={p?.phone ?? ""} />
      </div>

      <form onSubmit={changePassword} className="space-y-2 rounded-lg border border-border bg-card p-3">
        <p className="text-sm font-medium">Đổi mật khẩu</p>
        <Input type="password" placeholder="Mật khẩu hiện tại" value={cur} onChange={(e) => setCur(e.target.value)} />
        <Input type="password" placeholder="Mật khẩu mới" value={next} onChange={(e) => setNext(e.target.value)} />
        {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
        <Button type="submit" className="w-full" disabled={!cur || !next}>
          Cập nhật
        </Button>
      </form>

      <Button variant="outline" className="w-full" onClick={logout}>
        Đăng xuất
      </Button>
    </div>
  );
}
```
> Kiểm tra `setAccessToken` được export từ `@/services/http` (đã xác nhận có). `change-password` trả `{access}` mới — set lại để phiên hiện tại không bị văng.

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Chạy toàn bộ test ESS:** `cd frontend && npx vitest run src/features/ess`
Expected: tất cả PASS.
- [ ] **Step 6: Commit** `git add ... && git commit -m "feat(ess): profile page with change-password and logout"`

---

## SPRINT 4 — PWA + hoàn thiện

### Task 11: vite-plugin-pwa + manifest

**Files:**
- Modify: `frontend/package.json` (devDependency)
- Modify: `frontend/vite.config.ts`
- Create: `frontend/public/icons/icon-192.png`, `icon-512.png`, `maskable-512.png`

- [ ] **Step 1: Cài plugin**

Run: `cd frontend && npm install -D vite-plugin-pwa`
Expected: thêm vào `devDependencies`.

- [ ] **Step 2: Thêm icon** — tạo 3 icon PNG (192, 512, maskable 512) đặt vào `frontend/public/icons/`. Nếu chưa có asset, tạo tạm từ `public/logo.jpg` (resize) — chấp nhận placeholder vuông, ghi chú thay asset chính thức sau.

- [ ] **Step 3: Cấu hình** `vite.config.ts` — thêm `VitePWA`:
```ts
import { VitePWA } from "vite-plugin-pwa";

// trong plugins: [...]
VitePWA({
  registerType: "autoUpdate",
  scope: "/m",
  includeAssets: ["icons/icon-192.png", "icons/icon-512.png"],
  manifest: {
    name: "DESI Nhân viên",
    short_name: "DESI NV",
    start_url: "/m",
    scope: "/m",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#2563eb",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  },
  workbox: {
    navigateFallbackDenylist: [/^\/api/, /^\/warranty/],
    runtimeCaching: [],  // KHÔNG cache API chấm công/nghỉ phép (cần realtime)
  },
})
```
> Đọc `vite.config.ts` hiện tại trước, chèn `VitePWA(...)` vào mảng `plugins` đã có, giữ nguyên `react()` và alias.

- [ ] **Step 4: Build kiểm tra**

Run: `cd frontend && npm run build`
Expected: build thành công, có `dist/manifest.webmanifest` + `dist/sw.js`.

- [ ] **Step 5: Commit**
```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/public/icons
git commit -m "feat(ess): PWA manifest + service worker scoped to /m"
```

### Task 12: Kiểm thử thủ công + cập nhật tài liệu

**Files:**
- Modify: `docs/superpowers/specs/2026-06-16-mobile-ess-design.md` (mục 7 & 8)

- [ ] **Step 1: Smoke test luồng** (mô tả lại trong note, chạy được trên môi trường dev):
  - Mở `/m` khi chưa login → thấy `MobileLoginView`. Đăng nhập → vào tab Chấm công.
  - Bottom nav chuyển 4 tab OK.
  - Tạo đơn nghỉ → xuất hiện trong list với trạng thái "Chờ duyệt".
  - Tab Cá nhân hiển thị hồ sơ; đổi mật khẩu; đăng xuất → về login.
  - Mở DevTools > Application: manifest hợp lệ, có thể "Install".
  - Vào ERP (vd `/sales/orders`) vẫn bình thường, SW không chiếm route ERP.

- [ ] **Step 2: Chạy full test suite** liên quan:
```bash
cd frontend && npx vitest run src/features/ess src/features/hr
```
Expected: PASS (trừ các test baseline đã biết fail — memory `known-failing-baseline-tests`).

- [ ] **Step 3:** Cập nhật bảng tiến độ (mục 7) sang ✅ và viết "Nhật ký thực hiện" (mục 8) trong spec: liệt kê đã làm / chưa làm / vướng mắc.

- [ ] **Step 4: Commit**
```bash
git add docs/superpowers/specs/2026-06-16-mobile-ess-design.md
git commit -m "docs(ess): mark sprints done + implementation log"
```

- [ ] **Step 5: Push & tạo MR**
```bash
git push -u origin feature/mobile-ess-self-service
```
Gửi link MR cho người dùng.

---

## Self-Review Notes (đã rà)

- **Spec coverage:** Chấm công→Task7/CheckInCore; Lịch sử→Task8 + `/me/attendance`(Task2); Nghỉ phép→Task9 + `/me/leave-requests`(Task2); Hồ sơ+đổi MK+logout→Task10 + `/me/profile`(Task2); PWA→Task11; tách shell `/m`→Task5-6; bảo mật IDOR→Task2 test.
- **Type consistency:** `MeProfile/MeAttendanceItem/MeLeaveItem/CreateLeavePayload` định nghĩa ở Task4, dùng nhất quán ở Task8-10. API fn `getMyProfile/getMyAttendance/getMyLeave/createMyLeave` khớp giữa `lib/api.ts`, `queries.ts`, và mock test.
- **Placeholder:** không còn TODO; icon PWA cho phép placeholder tạm có ghi chú thay asset chính thức.
- **Rủi ro cần chú ý lúc chạy:** (a) tên service/lệnh test BE phải khớp `docker-compose` thật (Task0 chốt); (b) response `/api/users/login` shape (`data.user` vs `data`) — bám `App.tsx`; (c) `main.tsx` điều kiện `/m` dùng bản chặt `=== "/m" || startsWith("/m/")`.
