# Deep RBAC — Sprint 5: Multi-department + dọn legacy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Phụ thuộc:** S1–S4 hoàn tất (resolver + enforcement action/scope/field đã chạy production).
> **⚠️ Rủi ro cao** (xóa field DB). Làm cuối cùng, từng bước nhỏ, mỗi bước có test + commit, có thể rollback từng commit.

**Goal:** Bật **multi-department** thực sự (`UserDepartment` là nguồn chính, UI gán nhiều phòng ban), và **gỡ nợ kỹ thuật**: thay mọi short-circuit `user.role == ADMIN` / `_is_admin` bằng resolver `is_superuser`; xóa `User.role`, `Department.allowed_sections`, `User.has_section_access`, enum `UserRole`, `DepartmentID` (đã hết dùng từ S3) — sau khi mọi test/đường dẫn đã chuyển sang hệ RBAC mới.

**Architecture:** Backfill `UserDepartment` từ `User.department` (1 dòng, `is_primary=True`). API user create/update nhận `department_ids`. Resolver bỏ nhánh legacy `user.role == ADMIN` (superuser = chỉ còn dựa role `is_system`). Mọi nơi cần "admin?" gọi `resolve_effective_permissions(user).is_superuser`. Xóa cột chỉ thực hiện ở 2 migration cuối, sau khi toàn bộ test xanh không còn `role=`/`allowed_sections`.

**Tech Stack:** Django 5 / DRF, PostgreSQL, React SPA + TS.

**Spec:** mục 2 (D4 strangler — xóa ở sprint cuối), 3 (UserDepartment), 6 (gán nhiều phòng ban).

---

## Bản đồ legacy cần gỡ (rà trước khi bắt đầu)

Production code còn tham chiếu legacy (ngoài test):
- `apps/users/models.py` — định nghĩa `UserRole`, `User.role`, `User.has_section_access`, `Department.allowed_sections`.
- `apps/users/views.py` — `_is_admin`, `UserRole`, gán `user.role`.
- `apps/users/serializers.py` — `UserRole.choices`, `get_allowed_sections`, field `role` ở Admin*Serializer.
- `apps/users/services.py` — import `UserRole` (đã ít dùng sau S3).
- `apps/users/permissions.py` — kiểm tra (S2 đã chuyển sang resolver; xác nhận không còn dùng role).
- `apps/orders/_repositories_reports.py` — `user.role`/`UserRole` (4).
- `apps/orders/transport/views/order_views_impl/order_crud_view.py` (1), `order_action_serializers.py` (1), `kiotviet_landing_serializers.py` (1).
- `apps/users/constants.py` — `DepartmentID` (S3 đã bỏ trong scope; xác nhận chỉ còn test dùng).

Chạy lệnh rà trước mỗi task:
```bash
grep -rn "UserRole\|user\.role\|has_section_access\|allowed_sections\|_is_admin\|DepartmentID" backend/apps --include="*.py" | grep -v "/tests/" | grep -v "/migrations/"
```

## File Structure (tổng hợp)
- `apps/users/migrations/0016_backfill_user_departments.py` — CREATE.
- `apps/users/migrations/0017_drop_legacy_role_sections.py` — CREATE (migration cuối, xóa cột).
- `apps/users/models.py`, `views.py`, `serializers.py`, `services.py` — MODIFY (gỡ legacy).
- `apps/users/rbac.py` — MODIFY (bỏ nhánh `user.role == ADMIN`).
- `apps/users/constants.py` — MODIFY/DELETE `DepartmentID` (nếu hết dùng).
- `apps/orders/_repositories_reports.py`, `order_crud_view.py`, `order_action_serializers.py`, `kiotviet_landing_serializers.py` — MODIFY.
- Frontend: `App.tsx`, `features/users/*`, `UsersPage.tsx`, `lib/api` user — MODIFY (department_ids, bỏ `role`).
- Tests: migrate fixtures off `role=`/`allowed_sections`.

## Cách chạy test
Như S1/S2. Sau mỗi task: chạy full suite `python -m pytest apps -q` ở các task gỡ field.

---

## Task 1: Backfill UserDepartment + department scope dùng UserDepartment làm chính

**Files:**
- Create: `backend/apps/users/migrations/0016_backfill_user_departments.py`
- Create: `backend/apps/users/tests/test_user_department_backfill.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/users/tests/test_user_department_backfill.py`:

```python
from django.test import TestCase

from apps.users.models import Department, User, UserDepartment, UserRole
from apps.users.rbac_userdept_backfill import backfill_user_departments


class UserDepartmentBackfillTests(TestCase):
    def test_creates_primary_row_from_legacy_fk(self):
        dept = Department.objects.create(name="Sales")
        user = User.objects.create_user(
            username="u", password="x", role=UserRole.STAFF, department=dept
        )
        backfill_user_departments()
        link = UserDepartment.objects.get(user=user, department=dept)
        self.assertTrue(link.is_primary)

    def test_idempotent(self):
        dept = Department.objects.create(name="Sales")
        User.objects.create_user(username="u", password="x", department=dept)
        backfill_user_departments()
        before = UserDepartment.objects.count()
        backfill_user_departments()
        self.assertEqual(UserDepartment.objects.count(), before)

    def test_user_without_department_skipped(self):
        User.objects.create_user(username="free", password="x")
        backfill_user_departments()
        self.assertEqual(UserDepartment.objects.count(), 0)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_user_department_backfill.py -q`
Expected: FAIL — module `rbac_userdept_backfill` chưa tồn tại.

- [ ] **Step 3: Implement helper**

Create `backend/apps/users/rbac_userdept_backfill.py`:

```python
"""Backfill UserDepartment (1 dòng is_primary) từ User.department FK. Idempotent."""

from django.db import transaction

from .models import User, UserDepartment


@transaction.atomic
def backfill_user_departments() -> int:
    created = 0
    for user in User.objects.filter(department__isnull=False):
        _, made = UserDepartment.objects.get_or_create(
            user=user, department_id=user.department_id, defaults={"is_primary": True}
        )
        created += int(made)
    return created
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_user_department_backfill.py -q`
Expected: PASS (3 test).

- [ ] **Step 5: Data migration**

Create `backend/apps/users/migrations/0016_backfill_user_departments.py`:

```python
from django.db import migrations


def run(apps, schema_editor):
    from apps.users.rbac_userdept_backfill import backfill_user_departments

    backfill_user_departments()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0015_backfill_roles_from_legacy"),
    ]

    operations = [migrations.RunPython(run, noop)]
```

- [ ] **Step 6: Migrate + commit**

```bash
python manage.py migrate users
uv run --no-project --with ruff ruff check backend/apps/users/rbac_userdept_backfill.py backend/apps/users/tests/test_user_department_backfill.py
git add backend/apps/users/rbac_userdept_backfill.py backend/apps/users/migrations/0016_backfill_user_departments.py backend/apps/users/tests/test_user_department_backfill.py
git commit -m "feat(rbac): backfill UserDepartment from legacy User.department"
```

---

## Task 2: API + UI gán nhiều phòng ban (department_ids)

**Files:**
- Modify: `backend/apps/users/serializers.py`, `backend/apps/users/services.py`, `backend/apps/users/views.py`
- Modify: `frontend/src/features/users/types.ts`, `frontend/src/pages/UsersPage.tsx` (form user)
- Test: `backend/apps/users/tests/test_user_departments_api.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/users/tests/test_user_departments_api.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import Department, User, UserDepartment, UserRole
from apps.users.test_helpers import make_admin


class UserDepartmentsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=make_admin("adm"))
        self.d1 = Department.objects.create(name="Sales")
        self.d2 = Department.objects.create(name="Ads")

    def test_assign_multiple_departments_on_update(self):
        target = User.objects.create_user(username="t", password="x", role=UserRole.STAFF)
        resp = self.client.patch(
            f"/api/users/admin/{target.id}",
            {"department_ids": [self.d1.id, self.d2.id]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            set(UserDepartment.objects.filter(user=target).values_list("department_id", flat=True)),
            {self.d1.id, self.d2.id},
        )

    def test_user_serializer_returns_department_ids(self):
        target = User.objects.create_user(username="t2", password="x", role=UserRole.STAFF)
        UserDepartment.objects.create(user=target, department=self.d1, is_primary=True)
        resp = self.client.get("/api/users/admin")
        row = next(r for r in resp.json() if r["id"] == target.id)
        self.assertEqual(row["department_ids"], [self.d1.id])
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_user_departments_api.py -q`
Expected: FAIL — `department_ids` chưa hỗ trợ.

- [ ] **Step 3: Service + serializer + view**

`RoleService`/hoặc service mới trong `services.py`:
```python
    @staticmethod
    @transaction.atomic
    def set_user_departments(user, department_ids: list[int]) -> None:
        from .models import UserDepartment

        target = set(department_ids)
        existing = set(
            UserDepartment.objects.filter(user=user).values_list("department_id", flat=True)
        )
        UserDepartment.objects.filter(user=user, department_id__in=existing - target).delete()
        UserDepartment.objects.bulk_create(
            [UserDepartment(user=user, department_id=did) for did in target - existing]
        )
        # Đảm bảo có 1 primary nếu danh sách không rỗng.
        if target and not UserDepartment.objects.filter(user=user, is_primary=True).exists():
            first = UserDepartment.objects.filter(user=user).order_by("department_id").first()
            if first:
                first.is_primary = True
                first.save(update_fields=["is_primary"])
        bump_rbac_cache_version()
```

`UserSerializer` thêm:
```python
    department_ids = serializers.SerializerMethodField()
    # thêm "department_ids" vào Meta.fields
    def get_department_ids(self, obj):
        return list(
            obj.department_links.order_by("-is_primary", "department_id").values_list(
                "department_id", flat=True
            )
        )
```

`AdminUserCreateSerializer` + `AdminUserUpdateSerializer` thêm:
```python
    department_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
```

`admin_users_collection` (create) + `admin_user_detail` (update): sau `user.save()`/role assign, thêm:
```python
    if "department_ids" in serializer.validated_data:
        RoleService.set_user_departments(user, serializer.validated_data["department_ids"])
```

- [ ] **Step 4: Chạy test BE để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_user_departments_api.py -q`
Expected: PASS (2 test).

- [ ] **Step 5: FE — multi-select phòng ban + nhiều role**

Trong `features/users/types.ts`, thêm `department_ids?: number[]` và `role_ids?: number[]` vào `AdminUserRow`.
Trong form tạo/sửa user (`UsersPage.tsx`), thay select 1 phòng ban bằng multi-select `department_ids`, và multi-select role `role_ids` (đọc `listRoles()`); gửi kèm khi submit. (Giữ tương thích: BE vẫn nhận `department_id` đơn cho tới khi cột bị xóa ở Task 6 — nhưng FE chuyển hẳn sang `department_ids`.)

- [ ] **Step 6: Build + commit**

```bash
cd frontend && npm run build && cd ..
uv run --no-project --with ruff ruff check backend/apps/users/serializers.py backend/apps/users/services.py backend/apps/users/views.py backend/apps/users/tests/test_user_departments_api.py
git add backend/apps/users/serializers.py backend/apps/users/services.py backend/apps/users/views.py backend/apps/users/tests/test_user_departments_api.py frontend/src/features/users/types.ts frontend/src/pages/UsersPage.tsx
git commit -m "feat(rbac): assign multiple departments + roles to user (API + UI)"
```

---

## Task 3: Thay short-circuit `_is_admin`/`user.role == ADMIN` bằng resolver is_superuser (production)

**Files:** (production, ngoài migration/test)
- Modify: `apps/users/views.py`, `apps/users/services.py`, `apps/orders/_repositories_reports.py`, `apps/orders/transport/views/order_views_impl/order_crud_view.py`, `apps/orders/transport/serializers/order_serializers_impl/order_action_serializers.py`, `kiotviet_landing_serializers.py`
- Test: `backend/apps/users/tests/test_admin_via_role.py`

- [ ] **Step 1: Viết test thất bại — admin-bằng-role (không cần cờ legacy)**

Create `backend/apps/users/tests/test_admin_via_role.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import DataScope, Role, User, UserRole, UserRoleAssignment
from apps.users.rbac import bump_rbac_cache_version


class AdminViaRoleTests(TestCase):
    def test_user_management_allowed_for_system_role_without_legacy_admin_flag(self):
        # User có role is_system nhưng cờ legacy role=STAFF → vẫn phải là admin hiệu dụng.
        user = User.objects.create_user(username="sysadm", password="x", role=UserRole.STAFF)
        sys_role = Role.objects.create(name="Administrator", is_system=True, scope_default=DataScope.ALL)
        UserRoleAssignment.objects.create(user=user, role=sys_role)
        bump_rbac_cache_version()

        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.get("/api/users/admin")
        self.assertEqual(resp.status_code, 200)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_admin_via_role.py -q`
Expected: FAIL — `_is_admin` hiện kiểm `user.role == ADMIN` → 403 cho STAFF dù có role is_system.

- [ ] **Step 3: Thay `_is_admin` trong `apps/users/views.py`**

Đổi định nghĩa:
```python
def _is_admin(user) -> bool:
    from .rbac import resolve_effective_permissions

    return resolve_effective_permissions(user).is_superuser
```
Trong các view gán `user.role = ...` (create/update user), **giữ tạm** việc set `role` (cột còn tồn tại tới Task 6) nhưng KHÔNG dùng nó để phân quyền. Bỏ field `role` khỏi `Admin*Serializer` được thực hiện ở Task 5.

- [ ] **Step 4: Thay các short-circuit khác**

Với mỗi nơi `user.role == UserRole.ADMIN` / `user.role == "admin"` trong production (rà bằng grep ở đầu plan), thay bằng:
```python
from apps.users.rbac import resolve_effective_permissions
... resolve_effective_permissions(user).is_superuser ...
```
Cụ thể:
- `_repositories_reports.py` (4 chỗ): nếu là check "admin xem toàn bộ" → dùng `is_superuser`.
- `order_crud_view.py`, `order_action_serializers.py`, `kiotviet_landing_serializers.py`: thay tương tự.
- `OrderServiceHelpers.assigned_channel_codes` (S3 đã đổi sang is_superuser — xác nhận).

- [ ] **Step 5: Chạy test để xác nhận PASS + full suite**

Run:
```bash
python -m pytest apps/users/tests/test_admin_via_role.py apps/users apps/orders -q
```
Expected: PASS. Sửa test fixtures vỡ bằng helper S2 (`make_admin`, `make_user_with_sections`).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(rbac): replace legacy admin checks with resolver is_superuser"
```

---

## Task 4: Migrate toàn bộ test fixtures off `role=`/`allowed_sections`/`DepartmentID`

**Files:** mọi test còn dùng (rà bằng grep)

> Bắt buộc trước khi xóa cột (Task 6). Mục tiêu: không file test nào còn truyền `role=`, `allowed_sections=`, hay import `DepartmentID`/`UserRole` để phân quyền.

- [ ] **Step 1: Liệt kê test cần sửa**

Run:
```bash
grep -rln "role=UserRole\|role=\"admin\"\|role='admin'\|allowed_sections=\|DepartmentID\|UserRole" backend/apps --include="*.py" | grep "/tests/"
```

- [ ] **Step 2: Sửa từng file dùng helper S2**

Pattern thay thế:
- `User.objects.create_user(..., role=UserRole.ADMIN)` → `make_admin("name")`.
- `User.objects.create_user(..., role=UserRole.STAFF, department=dept_with_sections)` → `make_user_with_sections("name", [<sections>])`.
- Test cố ý kiểm scope channel → tạo Role + RoleSectionScope channel + UserChannel (xem mẫu S3 `test_channel_scope_mutation_authz.py`).
- Thêm `bump_rbac_cache_version()` sau khi dựng role.

- [ ] **Step 3: Chạy full suite tới khi xanh**

Run: `python -m pytest apps -q`
Expected: PASS toàn bộ; grep ở Step 1 trả rỗng (trừ chính các test RBAC kiểm tra ánh xạ legacy nếu còn giữ).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test(rbac): migrate all fixtures to role-based setup (drop legacy kwargs)"
```

---

## Task 5: Gỡ legacy khỏi resolver + serializer + permissions

**Files:**
- Modify: `apps/users/rbac.py` (bỏ nhánh `user.role == ADMIN`)
- Modify: `apps/users/serializers.py` (bỏ `role` field, `get_allowed_sections`)
- Modify: `apps/users/permissions.py` (xác nhận không còn dùng role)
- Test: dùng test hiện có

- [ ] **Step 1: Bỏ nhánh legacy trong resolver**

Trong `apps/users/rbac.py` `_compute`, xóa:
```python
    if getattr(user, "role", None) == UserRole.ADMIN:
        return _SUPERUSER
```
và bỏ import `UserRole`. Superuser giờ chỉ còn dựa `any(r.is_system for r in roles)`.

> Điều kiện an toàn: backfill S1 (Task 5) đã gán Administrator (is_system) cho mọi admin; xác nhận lại bằng truy vấn trước khi xóa (mọi user từng là ADMIN đều có role is_system).

- [ ] **Step 2: Bỏ `role`/allowed_sections khỏi serializer**

Trong `apps/users/serializers.py`:
- `UserSerializer`: xóa `"role"` khỏi `Meta.fields`; xóa method `get_allowed_sections` + `allowed_sections = SerializerMethodField()` (allowed_sections giờ do `/me` derive ở view `_me_payload`, S2). Với danh sách user admin (`/api/users/admin`) không cần allowed_sections.
- `AdminUserCreateSerializer`/`AdminUserUpdateSerializer`: xóa field `role` (và `role` khỏi xử lý ở views).
- Bỏ import `UserRole`, `VALID_SECTIONS` nếu hết dùng.

> ⚠️ FE: `/api/users/me` vẫn trả `allowed_sections` (derived ở `_me_payload`) → menu không vỡ. Nhưng `UserSerializer` (dùng cho login response + list admin) sẽ không còn `allowed_sections`. Kiểm tra login flow FE đọc `allowed_sections` từ đâu: nếu từ login response (UserSerializer) thì giữ `allowed_sections` derived trong UserSerializer bằng cách gọi resolver thay vì department. Xem Step 3.

- [ ] **Step 3: Giữ allowed_sections derived trong UserSerializer qua resolver (không qua department)**

Thay `get_allowed_sections` bằng bản derived từ resolver (an toàn cho login response):
```python
    allowed_sections = serializers.SerializerMethodField()

    def get_allowed_sections(self, obj):
        from .models import VALID_SECTIONS
        from .rbac import resolve_effective_permissions

        eff = resolve_effective_permissions(obj)
        if eff.is_superuser:
            return list(VALID_SECTIONS)
        return sorted({code.split(":", 1)[0] for code in eff.permission_codes})
```
(Như vậy login response + /me nhất quán, không phụ thuộc `Department.allowed_sections`.)

- [ ] **Step 4: Full suite**

Run: `python -m pytest apps -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/rbac.py backend/apps/users/serializers.py backend/apps/users/permissions.py
git add -A
git commit -m "refactor(rbac): derive allowed_sections from resolver; drop role from serializers"
```

---

## Task 6: Migration xóa cột legacy + xóa code định nghĩa

**Files:**
- Modify: `apps/users/models.py` (xóa `UserRole`, `User.role`, `User.has_section_access`, `Department.allowed_sections`)
- Modify: `apps/users/constants.py` (xóa `DepartmentID` nếu hết dùng)
- Create: `backend/apps/users/migrations/0017_drop_legacy_role_sections.py`
- Test: full suite

- [ ] **Step 1: Rà lần cuối — không còn tham chiếu production/test**

Run:
```bash
grep -rn "UserRole\|user\.role\|has_section_access\|\.allowed_sections\|DepartmentID" backend/apps --include="*.py" | grep -v "/migrations/"
```
Expected: rỗng (hoặc chỉ còn trong test ánh xạ legacy đã xác định giữ lại — nếu vậy, sửa nốt). Nếu còn → quay lại Task 3/4/5.

- [ ] **Step 2: Xóa định nghĩa trong models.py**

Trong `apps/users/models.py`:
- Xóa class `UserRole(models.TextChoices)`.
- Xóa field `role = models.CharField(...)` khỏi `User`.
- Xóa method `has_section_access`.
- Xóa field `allowed_sections` khỏi `Department`.
- Giữ `VALID_SECTIONS` (vẫn dùng cho catalog/derive).

Trong `apps/users/constants.py`: nếu `DepartmentID` không còn ai dùng → xóa file/biến (giữ file rỗng nếu có import khác).

- [ ] **Step 3: Sinh migration xóa cột**

Run:
```bash
python manage.py makemigrations users --name drop_legacy_role_sections
```
Expected: tạo `0017_drop_legacy_role_sections.py` với `RemoveField(User, "role")`, `RemoveField(Department, "allowed_sections")`. Mở file xác nhận chỉ có RemoveField (không AlterField lạ).

- [ ] **Step 4: Migrate + full suite + ruff**

Run:
```bash
python manage.py migrate users
python -m pytest apps -q
uv run --no-project --with ruff ruff check backend/apps/users/models.py backend/apps/users/constants.py
```
Expected: migrate OK; toàn bộ test PASS.

- [ ] **Step 5: FE — bỏ `role` khỏi type/UI**

Trong `frontend/src/App.tsx` type `User`: xóa `role: string;` (thay bằng `is_superuser`). Rà FE còn dùng `user.role === "admin"`:
```bash
grep -rn "\.role" frontend/src --include="*.ts" --include="*.tsx" | grep -i "admin\|user"
```
Thay bằng `user.is_superuser` hoặc `usePermission()`. Trong `App.tsx` các chỗ `currentUser.role === "admin"` (vd dòng ~697, ~944) → `currentUser.is_superuser`. Trong `features/users/types.ts` bỏ `role` khỏi `AdminUserRow` (hoặc giữ optional nếu UI hiển thị nhãn role-name thay vì admin/staff).

- [ ] **Step 6: Build + commit**

```bash
cd frontend && npm run build && cd ..
git add -A
git commit -m "feat(rbac): drop legacy User.role/Department.allowed_sections + UserRole enum"
```

---

## Self-review checklist (S5)

- [ ] `grep` legacy (Task 6 Step 1) trả rỗng ngoài migration.
- [ ] Mọi admin cũ vẫn full quyền (qua role Administrator is_system), kiểm bằng `test_admin_via_role`.
- [ ] Multi-department: UserDepartment là nguồn; department scope (S3) đọc UserDepartment.
- [ ] Login response + `/me` đều trả `allowed_sections` derived từ resolver (không phụ thuộc cột đã xóa).
- [ ] Full suite `python -m pytest apps -q` xanh; `npm run build` xanh.
- [ ] Migration `0016`/`0017` chạy tuần tự sạch; `0017` chỉ RemoveField.

## Hoàn tất chương trình RBAC

Sau S5: hệ phân quyền là RBAC+ABAC đầy đủ 4 chiều (action/field/scope/custom-role+multi-dept), không còn role cứng trong code, không còn hardcode ADS, không còn `allowed_sections` JSON. Cập nhật `docs/superpowers/specs/2026-06-14-deep-rbac-permissions-design.md` trạng thái → "Đã triển khai (S1–S5)". Cân nhắc backlog ngoài phạm vi (spec mục 10): deny-override, time-based permission, per-record ACL, đồng bộ Lark.
