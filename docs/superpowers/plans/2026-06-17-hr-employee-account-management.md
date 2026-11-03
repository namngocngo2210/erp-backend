# HR Employee Account Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho phép thêm tài khoản đăng nhập mới (gắn vào nhân sự) và gỡ (unlink) tài khoản ngay trong trang chi tiết nhân sự.

**Architecture:** Backend thêm 2 endpoint trong `apps/hr` (gate `employees:edit`), tái dụng `AdminUserCreateSerializer` + logic tạo User được tách thành `UserService.create_admin_user`. `EmployeeService.add_account`/`unlink_account` thao tác `EmployeeAccount`. Serializer thêm field `accounts` có cấu trúc. Frontend bọc lại `UserCreateDialog` (đã controlled, có sẵn `onSubmit`) trong wrapper HR tự giữ state, thêm danh sách account + nút Thêm/Gỡ trong `EmployeeProfileForm`.

**Tech Stack:** Django REST Framework, pytest (TestCase + APIClient); React + TypeScript, @tanstack/react-query, vitest, shadcn/ui.

## Global Constraints

- Backend Python ≥ 3.12, clean architecture (view mỏng → service → model), ruff sạch.
- Endpoint mới gate bằng `employees:edit` (KHÔNG phải superuser) — chủ đích mở quyền tạo User cho HR manager.
- Gỡ tài khoản = chỉ xóa bản ghi `EmployeeAccount`, GIỮ `User` (không xóa/không vô hiệu hóa).
- Khi gỡ account đang là `is_primary` mà còn account khác → promote account cũ nhất còn lại (`order_by("created_at").first()`) làm primary.
- Giữ nguyên field `usernames` của `EmployeeSerializer` (consumer cũ: org-chart, list, mobile ESS phụ thuộc).
- Không sửa logic bên trong `UserCreateDialog` — chỉ tái dụng qua props.
- Commit message tiếng Anh, ngắn gọn, KHÔNG kèm dòng Co-Authored-By.
- **Chạy test backend** bằng ephemeral docker container (prod image `desi-web` không có thư mục tests/ruff):

```bash
docker inspect desi-web -f '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^[A-Z_]+=' | grep -vE '^PATH=|^LANG=|^GPG|^PYTHON' > /tmp/web.env
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && rm -f /tmp/prom_multiproc/* && python -m pytest <paths> --reuse-db -q'
```

- **Ruff** trên host: `uv run --no-project --with ruff ruff check <files>`
- **Frontend test/lint**: `cd frontend && npx vitest run <path>` và `npx tsc --noEmit`.

---

## Backend

### Task 1: Tách logic tạo admin user thành `UserService.create_admin_user`

**Files:**
- Modify: `backend/apps/users/services.py` (thêm method vào class `UserService`, dùng lại helper `_replace_kiotviet_mappings` — chuyển nó thành staticmethod hoặc import)
- Modify: `backend/apps/users/views.py:241-270` (hàm `admin_users_collection` POST gọi service mới)
- Test: `backend/apps/users/tests/test_user_service.py` (tạo mới nếu chưa có)

**Interfaces:**
- Produces: `UserService.create_admin_user(validated: dict) -> User`
  — nhận dict đã qua `AdminUserCreateSerializer.validated_data`; tạo `User`, `set_password`,
  gán `department_id`, kiotviet mappings, `role_ids`, `department_ids`; trả về `User` đã lưu.
  Phải chạy trong `@transaction.atomic`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/users/tests/test_user_service.py`:

```python
from django.test import TestCase

from apps.users.models import Department, User, UserRoleAssignment
from apps.users.services import UserService
from apps.users.test_helpers import make_admin  # sync_permissions side-effect not needed here


class CreateAdminUserServiceTests(TestCase):
    def test_creates_active_user_with_department_and_roles(self):
        dept = Department.objects.create(name="Sales")
        from apps.users.models import Role
        role = Role.objects.create(name="r1")
        user = UserService.create_admin_user(
            {
                "username": "svc1",
                "display_name": "Service One",
                "is_active": True,
                "password": "secret123",
                "department_id": dept.id,
                "kiotviet_mappings": [],
                "role_ids": [role.id],
                "department_ids": [dept.id],
            }
        )
        self.assertTrue(User.objects.filter(username="svc1").exists())
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("secret123"))
        self.assertEqual(user.department_id, dept.id)
        self.assertTrue(UserRoleAssignment.objects.filter(user=user, role=role).exists())
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `... python -m pytest apps/users/tests/test_user_service.py --reuse-db -q`
Expected: FAIL — `AttributeError: type object 'UserService' has no attribute 'create_admin_user'`

- [ ] **Step 3: Cài đặt service**

Trong `backend/apps/users/services.py`, thêm vào class `UserService` (giữ method `can_edit_order` hiện có):

```python
    @staticmethod
    @transaction.atomic
    def create_admin_user(validated: dict):
        """Tạo User quản trị từ dữ liệu đã validate bởi AdminUserCreateSerializer.

        Dùng chung cho endpoint admin Users và endpoint thêm tài khoản nhân sự.
        """
        from apps.users.models import User, UserKiotvietMapping

        data = dict(validated)
        password = data.pop("password")
        department_id = data.pop("department_id", None)
        kiotviet_mappings = data.pop("kiotviet_mappings", [])
        role_ids = data.pop("role_ids", None)
        department_ids = data.pop("department_ids", None)
        user = User(**data)
        if department_id:
            user.department_id = department_id
        user.set_password(password)
        user.save()

        desired_kv_ids = {e["kiotviet_channel_id"] for e in kiotviet_mappings}
        UserKiotvietMapping.objects.filter(user=user).exclude(
            kiotviet_channel_id__in=desired_kv_ids
        ).delete()
        for entry in kiotviet_mappings:
            UserKiotvietMapping.objects.update_or_create(
                user=user,
                kiotviet_channel_id=entry["kiotviet_channel_id"],
                defaults={"external_kiot_user_id": entry["external_kiot_user_id"]},
            )
        if role_ids is not None:
            RoleService.assign_roles_to_user(user, role_ids)
        if department_ids is not None:
            RoleService.set_user_departments(user, department_ids)
        return user
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `... python -m pytest apps/users/tests/test_user_service.py --reuse-db -q`
Expected: PASS

- [ ] **Step 5: Refactor `admin_users_collection` dùng service (giữ hành vi)**

Trong `backend/apps/users/views.py`, thay thân nhánh POST của `admin_users_collection` (đoạn từ `validated = dict(serializer.validated_data)` đến trước `return Response(...)`) bằng:

```python
    user = UserService.create_admin_user(serializer.validated_data)
    return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)
```

Thêm import: `from apps.users.services import UserService` (cạnh import `RoleService` hiện có). Nếu `_replace_kiotviet_mappings` không còn nơi nào dùng, xóa hàm đó để tránh code chết.

- [ ] **Step 6: Chạy test admin users hiện có để xác nhận không vỡ**

Run: `... python -m pytest apps/users/tests/test_user_service.py apps/users -k "admin or user" --reuse-db -q`
Expected: PASS (toàn bộ test admin user cũ vẫn xanh)

- [ ] **Step 7: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/services.py backend/apps/users/views.py backend/apps/users/tests/test_user_service.py
git add backend/apps/users/services.py backend/apps/users/views.py backend/apps/users/tests/test_user_service.py
git commit -m "refactor(users): extract UserService.create_admin_user"
```

---

### Task 2: Thêm field `accounts` có cấu trúc vào `EmployeeSerializer`

**Files:**
- Modify: `backend/apps/hr/transport/serializers/employee_serializers.py:22-53`
- Test: `backend/apps/hr/tests/test_serializers.py` (thêm test case)

**Interfaces:**
- Produces: `EmployeeSerializer(...).data["accounts"]` = list dict với keys
  `account_id:int, user_id:int, username:str, display_name:str, is_primary:bool, is_active:bool`.
  Đọc từ `obj.accounts.all()` (đã được repo prefetch — không N+1). Field `usernames` giữ nguyên.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `backend/apps/hr/tests/test_serializers.py` (theo style file hiện có; nếu cần factory xem `apps/hr/tests/factories.py`):

```python
from apps.hr.models import EmployeeAccount
from apps.hr.repositories import EmployeeRepository
from apps.hr.transport.serializers.employee_serializers import EmployeeSerializer
from apps.users.models import User


def test_employee_serializer_exposes_structured_accounts(db):
    from apps.hr.application.services.employee_service import EmployeeService

    emp = EmployeeService.create(
        full_name="Acc Holder", department_id=None, username="primary1",
        create_login=True, password="secret123",
    )
    extra = User.objects.create_user(username="second1", password="secret123")
    EmployeeAccount.objects.create(employee=emp, user=extra, is_primary=False)

    fresh = EmployeeRepository.get_or_none(emp.id)
    data = EmployeeSerializer(fresh).data
    accounts = {a["username"]: a for a in data["accounts"]}
    assert set(accounts) == {"primary1", "second1"}
    assert accounts["primary1"]["is_primary"] is True
    assert accounts["second1"]["is_primary"] is False
    assert accounts["primary1"]["account_id"] > 0
    # backward-compat field still present
    assert set(data["usernames"]) == {"primary1", "second1"}
```

> Lưu ý: nếu `test_serializers.py` dùng `unittest.TestCase` (không phải pytest fixtures), gói test trên vào một `TestCase` method và bỏ tham số `db`/dùng `setUp`. Kiểm tra header file trước khi viết.

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `... python -m pytest apps/hr/tests/test_serializers.py -k structured_accounts --reuse-db -q`
Expected: FAIL — `KeyError: 'accounts'`

- [ ] **Step 3: Cài đặt field**

Trong `EmployeeSerializer`, thêm khai báo + method và thêm `"accounts"` vào `Meta.fields`:

```python
    accounts = serializers.SerializerMethodField()

    def get_accounts(self, obj) -> list[dict]:
        return [
            {
                "account_id": a.id,
                "user_id": a.user_id,
                "username": a.user.username,
                "display_name": a.user.display_name,
                "is_primary": a.is_primary,
                "is_active": a.user.is_active,
            }
            for a in obj.accounts.all()
        ]
```

Trong `Meta.fields`, thêm `"accounts"` ngay sau `"usernames"`.

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `... python -m pytest apps/hr/tests/test_serializers.py -k structured_accounts --reuse-db -q`
Expected: PASS

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/hr/transport/serializers/employee_serializers.py backend/apps/hr/tests/test_serializers.py
git add backend/apps/hr/transport/serializers/employee_serializers.py backend/apps/hr/tests/test_serializers.py
git commit -m "feat(hr): expose structured accounts on EmployeeSerializer"
```

---

### Task 3: `EmployeeService.add_account` + `unlink_account`

**Files:**
- Modify: `backend/apps/hr/application/services/employee_service.py` (thêm 2 staticmethod vào class `EmployeeService`)
- Test: `backend/apps/hr/tests/test_employee_service.py` (thêm test cases)

**Interfaces:**
- Consumes: `UserService.create_admin_user(validated)` (Task 1) → `User`.
- Produces:
  - `EmployeeService.add_account(employee_id: int, validated: dict) -> EmployeeAccount`
    — tạo User qua `UserService.create_admin_user`, link `EmployeeAccount(is_primary=False)`.
  - `EmployeeService.unlink_account(employee_id: int, account_id: int) -> None`
    — load `EmployeeAccount` theo `(id=account_id, employee_id=employee_id)`; nếu không có →
    raise `EmployeeAccount.DoesNotExist`; xóa bản ghi (giữ User); nếu bản ghi đã xóa là primary
    và còn account khác → promote account cũ nhất còn lại làm primary.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `backend/apps/hr/tests/test_employee_service.py`:

```python
from apps.hr.models import EmployeeAccount
from apps.hr.application.services.employee_service import EmployeeService
from apps.users.models import User


class AddUnlinkAccountServiceTests(TestCase):
    def _make_emp(self):
        return EmployeeService.create(
            full_name="Holder", department_id=None, username="primary1",
            create_login=True, password="secret123",
        )

    def test_add_account_links_non_primary_user(self):
        emp = self._make_emp()
        acc = EmployeeService.add_account(
            emp.id,
            {
                "username": "second1", "display_name": "Second", "is_active": True,
                "password": "secret123", "department_id": None, "kiotviet_mappings": [],
            },
        )
        self.assertFalse(acc.is_primary)
        self.assertTrue(User.objects.filter(username="second1").exists())
        self.assertEqual(emp.accounts.count(), 2)

    def test_unlink_keeps_user(self):
        emp = self._make_emp()
        acc = EmployeeService.add_account(
            emp.id,
            {"username": "second1", "display_name": "", "is_active": True,
             "password": "secret123", "department_id": None, "kiotviet_mappings": []},
        )
        EmployeeService.unlink_account(emp.id, acc.id)
        self.assertFalse(EmployeeAccount.objects.filter(id=acc.id).exists())
        self.assertTrue(User.objects.filter(username="second1").exists())  # User kept

    def test_unlink_primary_promotes_oldest_remaining(self):
        emp = self._make_emp()
        primary = emp.accounts.get(is_primary=True)
        acc2 = EmployeeService.add_account(
            emp.id,
            {"username": "second1", "display_name": "", "is_active": True,
             "password": "secret123", "department_id": None, "kiotviet_mappings": []},
        )
        EmployeeService.unlink_account(emp.id, primary.id)
        acc2.refresh_from_db()
        self.assertTrue(acc2.is_primary)

    def test_unlink_unknown_account_raises(self):
        emp = self._make_emp()
        with self.assertRaises(EmployeeAccount.DoesNotExist):
            EmployeeService.unlink_account(emp.id, 999999)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `... python -m pytest apps/hr/tests/test_employee_service.py -k AddUnlinkAccount --reuse-db -q`
Expected: FAIL — `AttributeError: ... has no attribute 'add_account'`

- [ ] **Step 3: Cài đặt service**

Trong `backend/apps/hr/application/services/employee_service.py`, thêm vào class `EmployeeService`:

```python
    @staticmethod
    @transaction.atomic
    def add_account(employee_id: int, validated: dict) -> EmployeeAccount:
        from apps.users.services import UserService  # local import: tránh phụ thuộc cứng

        user = UserService.create_admin_user(validated)
        return EmployeeAccount.objects.create(
            employee_id=employee_id, user=user, is_primary=False
        )

    @staticmethod
    @transaction.atomic
    def unlink_account(employee_id: int, account_id: int) -> None:
        acc = EmployeeAccount.objects.get(id=account_id, employee_id=employee_id)
        was_primary = acc.is_primary
        acc.delete()  # chỉ gỡ liên kết; User được giữ nguyên
        if was_primary:
            nxt = (
                EmployeeAccount.objects.filter(employee_id=employee_id)
                .order_by("created_at")
                .first()
            )
            if nxt:
                nxt.is_primary = True
                nxt.save(update_fields=["is_primary"])
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `... python -m pytest apps/hr/tests/test_employee_service.py -k AddUnlinkAccount --reuse-db -q`
Expected: PASS

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/hr/application/services/employee_service.py backend/apps/hr/tests/test_employee_service.py
git add backend/apps/hr/application/services/employee_service.py backend/apps/hr/tests/test_employee_service.py
git commit -m "feat(hr): add/unlink employee account services"
```

---

### Task 4: HR account endpoints (view + urls) gate `employees:edit`

**Files:**
- Create: `backend/apps/hr/transport/views/account_views.py`
- Modify: `backend/apps/hr/urls.py` (import + 2 path)
- Test: `backend/apps/hr/tests/test_employee_account_api.py`

**Interfaces:**
- Consumes: `EmployeeService.add_account`, `EmployeeService.unlink_account` (Task 3);
  `AdminUserCreateSerializer` (apps.users.serializers); `_can`, `_forbidden` (hr views `_helpers`).
- Produces HTTP:
  - `POST /api/hr/employees/<id>/accounts` → 201 `EmployeeSerializer(emp).data` (gate `employees:edit`)
  - `DELETE /api/hr/employees/<id>/accounts/<account_id>` → 204 (gate `employees:edit`)

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/hr/tests/test_employee_account_api.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.hr.application.services.employee_service import EmployeeService
from apps.users.models import User
from apps.users.test_helpers import make_user_with_permissions, make_user_with_sections


class EmployeeAccountApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.editor = make_user_with_permissions("hr-editor", ["employees:edit", "employees:view"])
        self.emp = EmployeeService.create(
            full_name="Holder", department_id=None, username="primary1",
            create_login=True, password="secret123",
        )

    def test_add_account_requires_edit_permission(self):
        viewer = make_user_with_permissions("viewer", ["employees:view"])
        self.client.force_authenticate(user=viewer)
        r = self.client.post(
            f"/api/hr/employees/{self.emp.id}/accounts",
            {"username": "x1", "password": "secret123", "is_active": True},
            format="json",
        )
        self.assertEqual(r.status_code, 403)

    def test_add_account_creates_and_links(self):
        self.client.force_authenticate(user=self.editor)
        r = self.client.post(
            f"/api/hr/employees/{self.emp.id}/accounts",
            {"username": "second1", "password": "secret123", "is_active": True},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        self.assertTrue(User.objects.filter(username="second1").exists())
        usernames = {a["username"] for a in r.data["accounts"]}
        self.assertEqual(usernames, {"primary1", "second1"})

    def test_add_account_duplicate_username_400(self):
        self.client.force_authenticate(user=self.editor)
        r = self.client.post(
            f"/api/hr/employees/{self.emp.id}/accounts",
            {"username": "primary1", "password": "secret123", "is_active": True},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_unlink_account(self):
        self.client.force_authenticate(user=self.editor)
        acc = EmployeeService.add_account(
            self.emp.id,
            {"username": "second1", "display_name": "", "is_active": True,
             "password": "secret123", "department_id": None, "kiotviet_mappings": []},
        )
        r = self.client.delete(f"/api/hr/employees/{self.emp.id}/accounts/{acc.id}")
        self.assertEqual(r.status_code, 204)
        self.assertTrue(User.objects.filter(username="second1").exists())

    def test_unlink_unknown_account_404(self):
        self.client.force_authenticate(user=self.editor)
        r = self.client.delete(f"/api/hr/employees/{self.emp.id}/accounts/999999")
        self.assertEqual(r.status_code, 404)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `... python -m pytest apps/hr/tests/test_employee_account_api.py --reuse-db -q`
Expected: FAIL — 404 (URL chưa tồn tại) trên các test.

- [ ] **Step 3: Cài đặt view**

Tạo `backend/apps/hr/transport/views/account_views.py`:

```python
from __future__ import annotations

from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.hr.application.services.employee_service import EmployeeService
from apps.hr.models import EmployeeAccount
from apps.hr.repositories import EmployeeRepository
from apps.hr.transport.serializers.employee_serializers import EmployeeSerializer
from apps.hr.transport.views._helpers import can as _can
from apps.hr.transport.views._helpers import forbidden as _forbidden
from apps.users.serializers import AdminUserCreateSerializer


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@never_cache
def employee_accounts(request, employee_id: int):
    if not _can(request, "employees:edit"):
        return _forbidden()
    emp = EmployeeRepository.get_or_none(employee_id)
    if not emp:
        return Response({"detail": "Không tìm thấy nhân viên."}, status=404)
    ser = AdminUserCreateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    EmployeeService.add_account(employee_id, ser.validated_data)
    fresh = EmployeeRepository.get_or_none(employee_id)
    return Response(EmployeeSerializer(fresh).data, status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
@never_cache
def employee_account_detail(request, employee_id: int, account_id: int):
    if not _can(request, "employees:edit"):
        return _forbidden()
    try:
        EmployeeService.unlink_account(employee_id, account_id)
    except EmployeeAccount.DoesNotExist:
        return Response({"detail": "Không tìm thấy tài khoản."}, status=404)
    return Response(status=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 4: Đăng ký URL**

Trong `backend/apps/hr/urls.py`, thêm import:

```python
from apps.hr.transport.views.account_views import (
    employee_account_detail,
    employee_accounts,
)
```

và thêm 2 path (đặt cạnh nhóm `employees/<int:employee_id>/...`):

```python
    path("employees/<int:employee_id>/accounts", employee_accounts, name="hr-employee-accounts"),
    path("employees/<int:employee_id>/accounts/<int:account_id>", employee_account_detail, name="hr-employee-account-detail"),
```

- [ ] **Step 5: Chạy test để xác nhận PASS**

Run: `... python -m pytest apps/hr/tests/test_employee_account_api.py --reuse-db -q`
Expected: PASS (5 test)

- [ ] **Step 6: Chạy regression nhanh HR + ruff**

Run: `... python -m pytest apps/hr/tests/test_employee_api.py apps/hr/tests/test_serializers.py apps/hr/tests/test_employee_service.py --reuse-db -q`
Expected: PASS

```bash
uv run --no-project --with ruff ruff check backend/apps/hr/transport/views/account_views.py backend/apps/hr/urls.py backend/apps/hr/tests/test_employee_account_api.py
```

- [ ] **Step 7: Commit**

```bash
git add backend/apps/hr/transport/views/account_views.py backend/apps/hr/urls.py backend/apps/hr/tests/test_employee_account_api.py
git commit -m "feat(hr): employee account add/unlink endpoints"
```

---

## Frontend

### Task 5: Types + API + react-query hooks

**Files:**
- Modify: `frontend/src/features/hr/hr-types.ts` (interface `EmployeeAccount` + field `accounts` trên `Employee`)
- Modify: `frontend/src/lib/api/hr.ts` (2 hàm + import `CreateAdminUserPayload`)
- Modify: `frontend/src/features/hr/queries.ts` (2 mutation hook)

**Interfaces:**
- Produces (types): `EmployeeAccount { account_id, user_id, username, display_name, is_primary, is_active }`;
  `Employee.accounts: EmployeeAccount[]`.
- Produces (api): `addEmployeeAccount(empId: number, payload: CreateAdminUserPayload): Promise<Employee>`,
  `unlinkEmployeeAccount(empId: number, accountId: number): Promise<void>`.
- Produces (hooks): `useAddEmployeeAccount(empId)`, `useUnlinkEmployeeAccount(empId)` — invalidate
  `employeeKeys.all` + `employeeKeys.detail(empId)`.

- [ ] **Step 1: Thêm types**

Trong `frontend/src/features/hr/hr-types.ts`, thêm interface và field. Tìm `interface Employee` và thêm `accounts`:

```ts
export interface EmployeeAccount {
  account_id: number;
  user_id: number;
  username: string;
  display_name: string;
  is_primary: boolean;
  is_active: boolean;
}
```

Trong `interface Employee`, thêm dòng (cạnh `usernames`):

```ts
  accounts: EmployeeAccount[];
```

- [ ] **Step 2: Thêm hàm API**

Trong `frontend/src/lib/api/hr.ts`, thêm import kiểu và 2 hàm:

```ts
import type { CreateAdminUserPayload } from "@/lib/api/users";
```

```ts
export async function addEmployeeAccount(
  empId: number,
  payload: CreateAdminUserPayload,
): Promise<Employee> {
  return fetchJson(`${API_BASE}/employees/${empId}/accounts`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function unlinkEmployeeAccount(empId: number, accountId: number): Promise<void> {
  await fetchJson(`${API_BASE}/employees/${empId}/accounts/${accountId}`, { method: "DELETE" });
}
```

- [ ] **Step 3: Thêm react-query hooks**

Trong `frontend/src/features/hr/queries.ts`, thêm import vào khối import từ `@/lib/api/hr`:
`addEmployeeAccount, unlinkEmployeeAccount`. Sau `useUpdateEmployee` thêm:

```ts
export function useAddEmployeeAccount(empId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: import("@/lib/api/users").CreateAdminUserPayload) =>
      addEmployeeAccount(empId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: employeeKeys.all });
      qc.invalidateQueries({ queryKey: employeeKeys.detail(empId) });
    },
  });
}

export function useUnlinkEmployeeAccount(empId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (accountId: number) => unlinkEmployeeAccount(empId, accountId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: employeeKeys.all });
      qc.invalidateQueries({ queryKey: employeeKeys.detail(empId) });
    },
  });
}
```

- [ ] **Step 4: Kiểm tra typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: không lỗi liên quan các file vừa sửa. (Nếu báo `Employee.accounts` thiếu ở mock test nào đó, ghi nhận để xử lý ở Task 7 hoặc cập nhật mock.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/hr/hr-types.ts frontend/src/lib/api/hr.ts frontend/src/features/hr/queries.ts
git commit -m "feat(hr): frontend api + hooks for employee accounts"
```

---

### Task 6: Wrapper `EmployeeAccountCreateDialog`

**Files:**
- Create: `frontend/src/features/hr/components/EmployeeAccountCreateDialog.tsx`
- Test: `frontend/src/features/hr/components/__tests__/EmployeeAccountCreateDialog.test.tsx`

**Interfaces:**
- Consumes: `UserCreateDialog` (controlled, props đầy đủ); `useAppShell()` cho `departments` +
  `kiotVietChannels`; `listRoles()` cho roles; `CreateAdminUserPayload`.
- Produces: component `EmployeeAccountCreateDialog({ open, onOpenChange, onSubmit })`
  với `onSubmit: (payload: CreateAdminUserPayload) => Promise<void>`. Tự giữ form state, build
  payload, gọi `onSubmit`, reset + đóng khi thành công.

> Lưu ý reuse: lấy state/handler/submit y như `UserManagement.tsx:70-146` (createForm, cleanMappings,
> submitCreate) nhưng thay `onCreate` bằng prop `onSubmit` và bỏ phần liên quan bảng/danh sách.

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/hr/components/__tests__/EmployeeAccountCreateDialog.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import { EmployeeAccountCreateDialog } from "../EmployeeAccountCreateDialog";

vi.mock("@/lib/api/roles", () => ({ listRoles: () => Promise.resolve([]) }));
vi.mock("@/app/AppShellContext", () => ({
  useAppShell: () => ({ departments: [], kiotVietChannels: [] }),
}));

describe("EmployeeAccountCreateDialog", () => {
  it("submits username + password payload", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <EmployeeAccountCreateDialog open onOpenChange={() => {}} onSubmit={onSubmit} />,
    );
    fireEvent.change(screen.getByLabelText(/username/i), {
      target: { value: "newacc" },
    });
    fireEvent.change(screen.getByLabelText(/password|mật khẩu/i), {
      target: { value: "secret123" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create|tạo/i }));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ username: "newacc", password: "secret123" }),
      ),
    );
  });
});
```

> Kiểm tra đúng tên export `useAppShell` và đường dẫn trong `frontend/src/app/AppShellContext.tsx`
> trước khi viết mock; chỉnh cho khớp. Nhãn input lấy từ i18n key `users.fields.username` /
> `users.fields.password` — dùng regex linh hoạt như trên.

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd frontend && npx vitest run src/features/hr/components/__tests__/EmployeeAccountCreateDialog.test.tsx`
Expected: FAIL — không import được `EmployeeAccountCreateDialog`.

- [ ] **Step 3: Cài đặt component**

Tạo `frontend/src/features/hr/components/EmployeeAccountCreateDialog.tsx`:

```tsx
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { useAppShell } from "@/app/AppShellContext";
import { UserCreateDialog } from "@/features/users/UserCreateDialog";
import { useI18n } from "@/i18n";
import { listRoles, type Role } from "@/lib/api/roles";
import type { CreateAdminUserPayload } from "@/lib/api/users";
import type { UserKiotvietMapping } from "@/features/users/types";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: CreateAdminUserPayload) => Promise<void>;
};

const EMPTY = {
  username: "",
  displayName: "",
  role: "staff",
  active: "true",
  kiotvietMappings: [] as UserKiotvietMapping[],
  password: "",
  departmentId: "" as string,
  departmentIds: [] as number[],
  roleIds: [] as number[],
};

export function EmployeeAccountCreateDialog({ open, onOpenChange, onSubmit }: Props) {
  const { t } = useI18n();
  const shell = useAppShell();
  const [roles, setRoles] = useState<Role[]>([]);
  const [form, setForm] = useState(EMPTY);

  useEffect(() => {
    listRoles().then(setRoles).catch(() => setRoles([]));
  }, []);

  const cleanMappings = (m: UserKiotvietMapping[]) =>
    m
      .map((x) => ({ ...x, external_kiot_user_id: x.external_kiot_user_id.trim() }))
      .filter((x) => x.external_kiot_user_id);

  const submit = async () => {
    if (!form.username.trim() || !form.password.trim()) {
      toast.error(t("users.toast.usernamePasswordRequired", "Cần tên đăng nhập và mật khẩu."));
      return;
    }
    await onSubmit({
      username: form.username.trim(),
      display_name: form.displayName.trim(),
      role: form.role,
      is_active: form.active === "true",
      kiotviet_mappings: cleanMappings(form.kiotvietMappings),
      password: form.password,
      department_id: form.departmentId ? Number(form.departmentId) : null,
      department_ids: form.departmentIds,
      role_ids: form.roleIds,
    });
    setForm(EMPTY);
    onOpenChange(false);
  };

  return (
    <UserCreateDialog
      open={open}
      username={form.username}
      displayName={form.displayName}
      role={form.role}
      active={form.active}
      kiotvietMappings={form.kiotvietMappings}
      password={form.password}
      departmentId={form.departmentId}
      departmentIds={form.departmentIds}
      roleIds={form.roleIds}
      departments={shell.departments}
      roles={roles}
      kiotvietChannels={shell.kiotVietChannels}
      onOpenChange={onOpenChange}
      onUsernameChange={(v) => setForm((p) => ({ ...p, username: v }))}
      onDisplayNameChange={(v) => setForm((p) => ({ ...p, displayName: v }))}
      onRoleChange={(v) => setForm((p) => ({ ...p, role: v }))}
      onActiveChange={(v) => setForm((p) => ({ ...p, active: v }))}
      onKiotvietMappingsChange={(v) => setForm((p) => ({ ...p, kiotvietMappings: v }))}
      onPasswordChange={(v) => setForm((p) => ({ ...p, password: v }))}
      onDepartmentIdChange={(v) => setForm((p) => ({ ...p, departmentId: v }))}
      onDepartmentIdsChange={(v) => setForm((p) => ({ ...p, departmentIds: v }))}
      onRoleIdsChange={(v) => setForm((p) => ({ ...p, roleIds: v }))}
      onSubmit={submit}
    />
  );
}
```

> Trước khi chạy: xác nhận `shell.departments` có kiểu `Department[]` và `shell.kiotVietChannels`
> có kiểu khớp prop `kiotvietChannels` của `UserCreateDialog` (`{id:number; name:string}[]`).
> Nếu lệch tên field, map lại (vd `shell.kiotVietChannels.map((c) => ({ id: c.id, name: c.name }))`).

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd frontend && npx vitest run src/features/hr/components/__tests__/EmployeeAccountCreateDialog.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/hr/components/EmployeeAccountCreateDialog.tsx frontend/src/features/hr/components/__tests__/EmployeeAccountCreateDialog.test.tsx
git commit -m "feat(hr): employee account create dialog wrapper"
```

---

### Task 7: UI danh sách account + nút Thêm/Gỡ trong `EmployeeProfileForm`

**Files:**
- Modify: `frontend/src/features/hr/components/EmployeeProfileForm.tsx:349-366` (khối Tài khoản)
- Test: `frontend/src/features/hr/components/__tests__/EmployeeProfileForm.accounts.test.tsx` (mới)

**Interfaces:**
- Consumes: `employee.accounts` (Task 5), `EmployeeAccountCreateDialog` (Task 6),
  `useAddEmployeeAccount`/`useUnlinkEmployeeAccount` (Task 5), `AlertDialog` (shadcn).

> Lưu ý gate quyền FE: shell chỉ phơi `allowedSections` (mức section), không có mã `employees:edit`.
> Form hiện tại KHÔNG gate nút Save theo quyền — backend là nơi enforce thật. Theo đúng pattern đó,
> nút Thêm/Gỡ luôn hiển thị trên FE (người không đủ quyền sẽ nhận 403 từ backend, hiện toast lỗi).
> KHÔNG tự thêm cơ chế gate FE mới.

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/hr/components/__tests__/EmployeeProfileForm.accounts.test.tsx`. Mock các
hook query để cô lập UI khối account (kiểm tra header file `EmployeeProfileForm.tsx` để biết đúng
danh sách hook cần mock — `useHrDepartments`, `useUpdateEmployee`, và 2 hook mới):

```tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import { EmployeeProfileForm } from "../EmployeeProfileForm";
import type { Employee } from "../../hr-types";

vi.mock("../../queries", () => ({
  useHrDepartments: () => ({ data: [] }),
  useUpdateEmployee: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAddEmployeeAccount: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUnlinkEmployeeAccount: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock("../EmployeeAccountCreateDialog", () => ({
  EmployeeAccountCreateDialog: () => null,
}));

const emp = {
  id: 1,
  full_name: "Holder",
  usernames: ["primary1", "second1"],
  accounts: [
    { account_id: 10, user_id: 1, username: "primary1", display_name: "", is_primary: true, is_active: true },
    { account_id: 11, user_id: 2, username: "second1", display_name: "", is_primary: false, is_active: true },
  ],
} as unknown as Employee;

describe("EmployeeProfileForm accounts", () => {
  it("renders accounts with an add button and a remove control per account", () => {
    render(<EmployeeProfileForm employee={emp} />);
    expect(screen.getByText("primary1")).toBeTruthy();
    expect(screen.getByText("second1")).toBeTruthy();
    expect(screen.getByRole("button", { name: /thêm tài khoản|add account/i })).toBeTruthy();
    // mỗi account có nút gỡ (aria-label chứa "Gỡ")
    expect(screen.getAllByRole("button", { name: /gỡ|remove|unlink/i }).length).toBe(2);
  });
});
```

> Nếu `EmployeeProfileForm` cần `QueryClientProvider`/i18n provider để render, bọc bằng helper
> render hiện có trong repo (tìm `renderWithProviders` hoặc xem test FE khác trong `features/hr`).

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd frontend && npx vitest run src/features/hr/components/__tests__/EmployeeProfileForm.accounts.test.tsx`
Expected: FAIL — không tìm thấy nút "Thêm tài khoản"/nút gỡ.

- [ ] **Step 3: Cài đặt UI**

Trong `frontend/src/features/hr/components/EmployeeProfileForm.tsx`:

a) Thêm import ở đầu file:

```tsx
import { UserPlus, X } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { EmployeeAccountCreateDialog } from "./EmployeeAccountCreateDialog";
import { useAddEmployeeAccount, useUnlinkEmployeeAccount } from "../queries";
import type { CreateAdminUserPayload } from "@/lib/api/users";
import type { EmployeeAccount } from "../hr-types";
```

b) Trong component, sau dòng lấy `useUpdateEmployee()` hiện có, thêm state + hook:

```tsx
  const addAccount = useAddEmployeeAccount(employee.id);
  const unlinkAccount = useUnlinkEmployeeAccount(employee.id);
  const [openAddAccount, setOpenAddAccount] = useState(false);
  const [accountToRemove, setAccountToRemove] = useState<EmployeeAccount | null>(null);

  const handleAddAccount = async (payload: CreateAdminUserPayload) => {
    try {
      await addAccount.mutateAsync(payload);
      toast.success(t("hr.account.added", "Đã thêm tài khoản."));
    } catch {
      toast.error(t("hr.account.addFailed", "Thêm tài khoản thất bại."));
    }
  };

  const handleConfirmRemove = async () => {
    if (!accountToRemove) return;
    try {
      await unlinkAccount.mutateAsync(accountToRemove.account_id);
      toast.success(t("hr.account.removed", "Đã gỡ tài khoản."));
    } catch {
      toast.error(t("hr.account.removeFailed", "Gỡ tài khoản thất bại."));
    } finally {
      setAccountToRemove(null);
    }
  };
```

c) Thay khối `Tài khoản đăng nhập` (dòng ~349-366) bằng danh sách account có nút:

```tsx
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-primary">
              {t("hr.section.accounts", "Tài khoản đăng nhập")}
            </h3>
            <Button variant="outline" size="sm" onClick={() => setOpenAddAccount(true)}>
              <UserPlus className="mr-1 h-4 w-4" />
              {t("hr.account.add", "Thêm tài khoản")}
            </Button>
          </div>
          {employee.accounts.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("hr.detail.noAccount", "Chưa gắn tài khoản")}
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {employee.accounts.map((acc) => (
                <div
                  key={acc.account_id}
                  className="flex items-center justify-between rounded-md border px-3 py-2"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{acc.username}</span>
                    {acc.is_primary && (
                      <Badge variant="secondary">{t("hr.account.primary", "Chính")}</Badge>
                    )}
                    {!acc.is_active && (
                      <Badge variant="outline">{t("common.inactive", "Ngừng")}</Badge>
                    )}
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t("hr.account.remove", "Gỡ tài khoản")}
                    onClick={() => setAccountToRemove(acc)}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>

        <EmployeeAccountCreateDialog
          open={openAddAccount}
          onOpenChange={setOpenAddAccount}
          onSubmit={handleAddAccount}
        />

        <AlertDialog
          open={accountToRemove !== null}
          onOpenChange={(o) => !o && setAccountToRemove(null)}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t("hr.account.removeTitle", "Gỡ tài khoản?")}</AlertDialogTitle>
              <AlertDialogDescription>
                {t(
                  "hr.account.removeDesc",
                  "Tài khoản sẽ được gỡ khỏi nhân sự này. Tài khoản đăng nhập vẫn được giữ lại.",
                )}
                {accountToRemove && (
                  <span className="mt-2 block font-medium">{accountToRemove.username}</span>
                )}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>{t("common.cancel", "Hủy")}</AlertDialogCancel>
              <AlertDialogAction onClick={handleConfirmRemove}>
                {t("common.confirm", "Xác nhận")}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
```

> Nếu `useState` chưa được import trong file (đã có ở dòng 1) thì bỏ qua. Xác nhận `AlertDialog`
> tồn tại tại `@/components/ui/alert-dialog` (tìm trong repo; nếu không có, dùng `Dialog` + footer
> như mẫu xóa trong `UserManagement.tsx:301-324`).

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd frontend && npx vitest run src/features/hr/components/__tests__/EmployeeProfileForm.accounts.test.tsx`
Expected: PASS

- [ ] **Step 5: Typecheck + regression FE HR**

Run: `cd frontend && npx tsc --noEmit && npx vitest run src/features/hr`
Expected: không lỗi type; test HR xanh. (Nếu test/mock HR cũ thiếu `accounts` trên `Employee`, cập nhật mock cho khớp.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/hr/components/EmployeeProfileForm.tsx frontend/src/features/hr/components/__tests__/EmployeeProfileForm.accounts.test.tsx
git commit -m "feat(hr): manage employee accounts in profile form"
```

---

## Final verification

- [ ] **Backend full HR + users suite:**
  `... python -m pytest apps/hr apps/users --reuse-db -q` → PASS
- [ ] **Frontend:** `cd frontend && npx tsc --noEmit && npx vitest run` → PASS
- [ ] **Ruff:** `uv run --no-project --with ruff ruff check backend/apps/hr backend/apps/users` → sạch
- [ ] Push branch + tạo MR.

## Self-review notes

- Spec D1–D6 đều có task: D1 (gate edit)=Task4; D2 (unlink giữ User)=Task3/4; D3+D4 (form đầy đủ, reuse)=Task6; D5 (accounts serializer)=Task2; D6 (promote primary)=Task3.
- Điều chỉnh so với spec: gate FE theo section-visibility thay vì mã `employees:edit` (shell không phơi mã action). Backend vẫn enforce `employees:edit` — đúng tinh thần spec. Ghi rõ ở Task 7.
- Không có migration mới (model `EmployeeAccount` đã tồn tại).
