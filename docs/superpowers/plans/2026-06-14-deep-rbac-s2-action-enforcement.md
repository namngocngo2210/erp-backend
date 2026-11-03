# Deep RBAC — Sprint 2: Action-level enforcement + Role admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Phụ thuộc:** Hoàn tất `2026-06-14-deep-rbac-s1-foundation.md` (models, catalog, backfill, resolver, cache) trước khi bắt đầu.

**Goal:** Bật enforcement **action-level**: DRF permission class `require_permission("orders:edit")`; biến `require_section` thành shim `:view` (không vỡ endpoint cũ); mở rộng `/api/users/me` trả `permissions/scopes/field_policy`; dựng API quản trị Role (CRUD + ma trận Role×permission) + gán nhiều role cho user; frontend hook `usePermission()` + component `<Can>` + trang quản trị Role.

**Architecture:** `require_permission` đọc `resolve_effective_permissions` (S1). `require_section` chuyển sang delegate `:view` → một nguồn sự thật duy nhất là resolver. API Role nằm trong `apps/users` (chủ quyền model). FE đọc `permissions` từ payload `/me` đã có sẵn trong context `useAppShell`, expose qua `usePermission()`; `<Can>` ẩn/khóa nút.

**Tech Stack:** Django 5 / DRF, React SPA (Vite) + TypeScript + shadcn/ui + react-router, react-query không bắt buộc (FE dùng `fetchJson`).

**Spec:** mục 5.2 (chốt 1 action-level), 6 (FE + trang Role), 4 (union).

---

## File Structure

**Backend (`backend/`):**
- `apps/users/permissions.py` — MODIFY: thêm `require_permission()`; đổi `require_section()` thành shim gọi `require_permission(f"{s}:view" ...)`.
- `apps/users/views.py` — MODIFY: `me()` trả thêm permissions/scopes/field_policy; thêm view Role CRUD + matrix + permissions catalog list; mở rộng user create/update nhận `role_ids`.
- `apps/users/serializers.py` — MODIFY: thêm `RoleSerializer`, `RoleWriteSerializer`, `PermissionSerializer`; `UserSerializer` thêm `role_ids`.
- `apps/users/urls.py` — MODIFY: routes roles/permissions.
- `apps/users/services.py` — MODIFY: thêm `RoleService.set_role_permissions()` + `assign_roles_to_user()` (bust cache).
- `apps/users/tests/test_require_permission.py` — CREATE.
- `apps/users/tests/test_me_payload.py` — CREATE.
- `apps/users/tests/test_role_admin_api.py` — CREATE.
- Endpoint pilot enforcement: `apps/orders/transport/views/order_views_impl/order_export_view.py` — MODIFY: dùng `require_permission("orders:export")` (chứng minh action-gating thật).
- `apps/orders/tests/test_orders_export_permission.py` — CREATE.

**Frontend (`frontend/`):**
- `src/App.tsx` — MODIFY: type `User` thêm `permissions/scopes/field_policy`.
- `src/lib/permissions.ts` — CREATE: type `EffectivePermissions`, helper `can()`.
- `src/app/AppShellContext.tsx` — MODIFY: expose `permissions` (nếu chưa).
- `src/hooks/usePermission.ts` — CREATE: hook `usePermission()`.
- `src/components/Can.tsx` — CREATE: `<Can permission="orders:edit">`.
- `src/lib/api/roles.ts` — CREATE: API client roles/permissions.
- `src/lib/routes.ts` — MODIFY: thêm AppSection `"roles"` + path `/settings/roles`.
- `src/app/modules.ts` — MODIFY: nav entry `roles` (requires `users`).
- `src/app/routed-pages.tsx` — MODIFY: lazy `RolesRoute`.
- `src/app/routes.tsx` — MODIFY: route `settings/roles` (Guard section `roles`).
- `src/pages/RolesPage.tsx` — CREATE: CRUD role + ma trận checkbox.
- `src/hooks/__tests__/usePermission.test.tsx` — CREATE.
- `src/lib/api/__tests__/roles.test.ts` — CREATE.

## Cách chạy test
Như S1 (backend qua container ephemeral; ruff host). Frontend: `cd frontend && npx vitest run <path>`; build: `npm run build`.

---

## Task 1: `require_permission` + `require_section` shim

**Files:**
- Modify: `backend/apps/users/permissions.py`
- Test: `backend/apps/users/tests/test_require_permission.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/users/tests/test_require_permission.py`:

```python
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from apps.users.models import (
    DataScope,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
    UserRoleAssignment,
)
from apps.users.permissions import require_permission, require_section
from apps.users.rbac import bump_rbac_cache_version


def _perm(code):
    section, action = code.split(":", 1)
    return Permission.objects.get_or_create(
        code=code, defaults={"section": section, "action": action}
    )[0]


class RequirePermissionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = User.objects.create_user(username="p1", password="x", role=UserRole.STAFF)
        self.role = Role.objects.create(name="r", scope_default=DataScope.OWN)
        RolePermission.objects.create(role=self.role, permission=_perm("orders:edit"))
        RolePermission.objects.create(role=self.role, permission=_perm("orders:view"))
        UserRoleAssignment.objects.create(user=self.user, role=self.role)
        bump_rbac_cache_version()

    def _check(self, perm_cls):
        request = self.factory.get("/")
        request.user = self.user
        return perm_cls().has_permission(request, view=None)

    def test_grants_when_code_present(self):
        self.assertTrue(self._check(require_permission("orders:edit")))

    def test_denies_when_code_absent(self):
        self.assertFalse(self._check(require_permission("orders:delete")))

    def test_variadic_any_match(self):
        self.assertTrue(self._check(require_permission("orders:delete", "orders:edit")))

    def test_require_section_is_view_shim(self):
        # role có orders:view → require_section("orders") pass
        self.assertTrue(self._check(require_section("orders")))
        # role KHÔNG có customers:view → require_section("customers") deny
        self.assertFalse(self._check(require_section("customers")))

    def test_admin_short_circuits(self):
        admin = User.objects.create_user(username="adm", password="x", role=UserRole.ADMIN)
        request = self.factory.get("/")
        request.user = admin
        self.assertTrue(require_permission("anything:at-all")().has_permission(request, None))

    def test_anonymous_denied(self):
        from django.contrib.auth.models import AnonymousUser

        request = self.factory.get("/")
        request.user = AnonymousUser()
        self.assertFalse(require_permission("orders:view")().has_permission(request, None))
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_require_permission.py -q`
Expected: FAIL — `ImportError: cannot import name 'require_permission'`.

- [ ] **Step 3: Implement**

Replace toàn bộ nội dung `backend/apps/users/permissions.py`:

```python
from rest_framework.permissions import BasePermission

# Nhóm section cho endpoint dùng chung giữa 3 trang đơn (orders / landing / koc).
ORDER_SECTIONS = ("orders", "landing-orders", "koc-orders")


def require_permission(*codes: str):
    """Permission pass nếu user có BẤT KỲ permission code nào trong danh sách.

    Nguồn sự thật: apps.users.rbac.resolve_effective_permissions (RBAC+ABAC, có cache).
    Admin / role is_system short-circuit = full quyền.
    """

    class _HasPermission(BasePermission):
        message = "Bạn không có quyền thực hiện hành động này."

        def has_permission(self, request, view):
            user = getattr(request, "user", None)
            if not user or not getattr(user, "is_authenticated", False):
                return False
            from .rbac import resolve_effective_permissions

            eff = resolve_effective_permissions(user)
            return any(eff.can(code) for code in codes)

    return _HasPermission


def require_section(*sections: str):
    """Shim tương thích ngược: 'có truy cập section' = có quyền `{section}:view`.

    Giữ chữ ký variadic của bản cũ; mọi call-site hiện tại không cần đổi. Endpoint
    muốn siết theo hành động cụ thể (create/edit/delete/approve/export) thì chuyển
    sang require_permission trực tiếp.
    """
    return require_permission(*[f"{section}:view" for section in sections])
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_require_permission.py -q`
Expected: PASS (6 test).

- [ ] **Step 5: Regression — toàn bộ test đang dùng require_section vẫn xanh**

Run:
```bash
python -m pytest apps/orders/tests/test_channel_scope_mutation_authz.py apps/users -q
```
Expected: PASS. Nếu fail: kiểm tra backfill (S1) đã seed view-permission cho mọi section dùng trong test fixtures (test fixtures phải `sync_permissions()` + `backfill_roles_from_legacy()` hoặc tạo role thủ công).

> ⚠️ Nếu các test cũ tạo user qua `Department(allowed_sections=[...])` mà KHÔNG chạy backfill, chúng sẽ fail vì resolver không thấy role. Xử lý ở Task 2 (thêm test helper) — tạm thời chấp nhận một số test cũ đỏ và sửa ở bước regression cuối Task 7.

- [ ] **Step 6: Commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/permissions.py backend/apps/users/tests/test_require_permission.py
git add backend/apps/users/permissions.py backend/apps/users/tests/test_require_permission.py
git commit -m "feat(rbac): require_permission permission class + require_section view-shim"
```

---

## Task 2: Test helper tạo user có role (dùng lại cho mọi test RBAC)

**Files:**
- Create: `backend/apps/users/test_helpers.py`

> Mục tiêu: chuẩn hóa cách dựng user + role trong test để regression không lặp code, và để các test app khác (orders, customers, ...) dùng được sau khi require_section đổi nguồn.

- [ ] **Step 1: Implement helper (kèm doctest-style usage trong docstring)**

Create `backend/apps/users/test_helpers.py`:

```python
"""Helpers dựng user + role cho test (sau khi require_section dùng resolver).

Dùng trong test app khác:
    from apps.users.test_helpers import make_user_with_sections
    staff = make_user_with_sections("sales1", ["orders", "customers"])  # full action mỗi section
"""

from apps.users.models import (
    DataScope,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
    UserRoleAssignment,
)
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import sync_permissions

_role_seq = 0


def make_admin(username: str = "admin") -> User:
    user = User.objects.create_user(username=username, password="x", role=UserRole.ADMIN)
    bump_rbac_cache_version()
    return user


def make_user_with_permissions(username: str, codes, scope=DataScope.ALL) -> User:
    sync_permissions()
    global _role_seq
    _role_seq += 1
    user = User.objects.create_user(username=username, password="x", role=UserRole.STAFF)
    role = Role.objects.create(name=f"test-role-{_role_seq}", scope_default=scope)
    perms = list(Permission.objects.filter(code__in=list(codes)))
    RolePermission.objects.bulk_create([RolePermission(role=role, permission=p) for p in perms])
    UserRoleAssignment.objects.create(user=user, role=role)
    bump_rbac_cache_version()
    return user


def make_user_with_sections(username: str, sections, scope=DataScope.ALL) -> User:
    """Gán mọi action của các section (mirror page-access cũ)."""
    sync_permissions()
    codes = list(Permission.objects.filter(section__in=list(sections)).values_list("code", flat=True))
    return make_user_with_permissions(username, codes, scope=scope)
```

- [ ] **Step 2: Smoke test nhanh helper**

Run:
```bash
python -m pytest -q -k "require_permission" \
  --co apps/users/tests/test_require_permission.py
```
(Chỉ collect; helper được kiểm gián tiếp ở Task tiếp theo.)

- [ ] **Step 3: Commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/test_helpers.py
git add backend/apps/users/test_helpers.py
git commit -m "test(rbac): shared helpers to build users with roles/permissions"
```

---

## Task 3: Mở rộng `/api/users/me` (permissions/scopes/field_policy + derived allowed_sections)

**Files:**
- Modify: `backend/apps/users/views.py` (hàm `me`)
- Test: `backend/apps/users/tests/test_me_payload.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/users/tests/test_me_payload.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import DataScope
from apps.users.test_helpers import make_admin, make_user_with_permissions


class MePayloadTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_staff_me_includes_permissions_scopes_fields(self):
        user = make_user_with_permissions(
            "s1", ["orders:view", "orders:edit", "customers:view"], scope=DataScope.OWN
        )
        self.client.force_authenticate(user=user)
        data = self.client.get("/api/users/me").json()

        self.assertIn("orders:view", data["permissions"])
        self.assertIn("orders:edit", data["permissions"])
        self.assertEqual(set(data["allowed_sections"]), {"orders", "customers"})
        self.assertEqual(data["scopes"].get("orders"), "own")
        self.assertIsInstance(data["field_policy"], dict)

    def test_admin_me_is_superuser_full(self):
        admin = make_admin("a1")
        self.client.force_authenticate(user=admin)
        data = self.client.get("/api/users/me").json()
        self.assertTrue(data["is_superuser"])
        # derived allowed_sections cho admin = mọi section
        from apps.users.models import VALID_SECTIONS

        self.assertEqual(set(data["allowed_sections"]), set(VALID_SECTIONS))
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_me_payload.py -q`
Expected: FAIL — `KeyError: 'permissions'`.

- [ ] **Step 3: Implement — viết hàm build payload + sửa `me`**

Trong `backend/apps/users/views.py`, thêm import ở đầu file (cạnh import `.models`):

```python
from .models import VALID_SECTIONS
from .rbac import resolve_effective_permissions
```

Thêm helper trên hàm `me`:

```python
def _me_payload(user) -> dict:
    eff = resolve_effective_permissions(user)
    data = dict(UserSerializer(user).data)
    if eff.is_superuser:
        permissions: list[str] = []  # superuser: FE dựa is_superuser, không cần liệt kê
        allowed_sections = list(VALID_SECTIONS)
        scopes = {section: "all" for section in VALID_SECTIONS}
        field_policy: dict = {}
    else:
        permissions = sorted(eff.permission_codes)
        allowed_sections = sorted({code.split(":", 1)[0] for code in eff.permission_codes})
        scopes = dict(eff.scope_by_section)
        field_policy = dict(eff.field_policy)
    data.update(
        {
            "permissions": permissions,
            "allowed_sections": allowed_sections,
            "scopes": scopes,
            "field_policy": field_policy,
            "is_superuser": eff.is_superuser,
        }
    )
    return data
```

Sửa hàm `me`:

```python
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    return Response(_me_payload(request.user))
```

> Lưu ý: `_me_payload` ghi đè `allowed_sections` mà `UserSerializer.get_allowed_sections` trả ra bằng phiên bản derived-từ-permission — đây là nguồn mới (FE menu dùng nó). Hành vi không vỡ vì backfill (S1) đã ánh xạ allowed_sections cũ → view permission.

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_me_payload.py -q`
Expected: PASS (2 test).

- [ ] **Step 5: Commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/views.py backend/apps/users/tests/test_me_payload.py
git add backend/apps/users/views.py backend/apps/users/tests/test_me_payload.py
git commit -m "feat(rbac): /me returns permissions/scopes/field_policy + derived allowed_sections"
```

---

## Task 4: Role admin API (CRUD + ma trận permission) + gán role cho user

**Files:**
- Modify: `backend/apps/users/serializers.py`, `backend/apps/users/services.py`, `backend/apps/users/views.py`, `backend/apps/users/urls.py`
- Test: `backend/apps/users/tests/test_role_admin_api.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/users/tests/test_role_admin_api.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import Role, RolePermission, User, UserRole, UserRoleAssignment
from apps.users.rbac_catalog import sync_permissions
from apps.users.test_helpers import make_admin, make_user_with_permissions


class RoleAdminApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        sync_permissions()
        self.admin = make_admin("adm")

    def test_non_admin_cannot_list_roles(self):
        staff = make_user_with_permissions("s", ["orders:view"])
        self.client.force_authenticate(user=staff)
        resp = self.client.get("/api/users/roles")
        self.assertEqual(resp.status_code, 403)

    def test_admin_lists_permissions_catalog(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get("/api/users/permissions")
        self.assertEqual(resp.status_code, 200)
        codes = {row["code"] for row in resp.json()}
        self.assertIn("orders:view", codes)

    def test_create_role_with_permissions(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            "/api/users/roles",
            {"name": "Sale Lead", "scope_default": "department",
             "permission_codes": ["orders:view", "orders:edit"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        role = Role.objects.get(name="Sale Lead")
        self.assertEqual(role.scope_default, "department")
        self.assertEqual(
            set(RolePermission.objects.filter(role=role).values_list("permission__code", flat=True)),
            {"orders:view", "orders:edit"},
        )

    def test_update_role_permissions_replaces_set(self):
        self.client.force_authenticate(user=self.admin)
        role = Role.objects.create(name="Editor")
        resp = self.client.patch(
            f"/api/users/roles/{role.id}",
            {"permission_codes": ["customers:view"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            set(RolePermission.objects.filter(role=role).values_list("permission__code", flat=True)),
            {"customers:view"},
        )

    def test_cannot_edit_system_role(self):
        self.client.force_authenticate(user=self.admin)
        sys_role = Role.objects.create(name="Administrator", is_system=True)
        resp = self.client.patch(
            f"/api/users/roles/{sys_role.id}", {"name": "Hacked"}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_assign_roles_to_user(self):
        self.client.force_authenticate(user=self.admin)
        target = User.objects.create_user(username="t", password="x", role=UserRole.STAFF)
        r1 = Role.objects.create(name="R1")
        r2 = Role.objects.create(name="R2")
        resp = self.client.patch(
            f"/api/users/admin/{target.id}", {"role_ids": [r1.id, r2.id]}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            set(UserRoleAssignment.objects.filter(user=target).values_list("role_id", flat=True)),
            {r1.id, r2.id},
        )
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_role_admin_api.py -q`
Expected: FAIL — 404 (route chưa tồn tại).

- [ ] **Step 3: Serializers**

Trong `backend/apps/users/serializers.py`, cập nhật import dòng `from .models import ...` để gồm `Permission, Role, RolePermission, DataScope`, và thêm các serializer (append cuối file):

```python
class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ("id", "code", "section", "action", "label")


class RoleSerializer(serializers.ModelSerializer):
    permission_codes = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = (
            "id", "name", "description", "is_system", "scope_default",
            "is_active", "created_at", "permission_codes",
        )
        read_only_fields = ("id", "is_system", "created_at")

    def get_permission_codes(self, obj: Role) -> list[str]:
        return list(
            RolePermission.objects.filter(role=obj)
            .order_by("permission__code")
            .values_list("permission__code", flat=True)
        )


class RoleWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    description = serializers.CharField(max_length=255, allow_blank=True, required=False)
    scope_default = serializers.ChoiceField(choices=DataScope.choices, required=False)
    is_active = serializers.BooleanField(required=False)
    permission_codes = serializers.ListField(child=serializers.CharField(), required=False)

    def validate_permission_codes(self, value):
        valid = set(Permission.objects.filter(code__in=value).values_list("code", flat=True))
        invalid = [c for c in value if c not in valid]
        if invalid:
            raise serializers.ValidationError(f"Permission code không hợp lệ: {invalid}")
        return list(dict.fromkeys(value))
```

Trong `UserSerializer.Meta.fields`, thêm `"role_ids"`; thêm method:

```python
    role_ids = serializers.SerializerMethodField()
```
và
```python
    def get_role_ids(self, obj):
        return list(obj.role_assignments.values_list("role_id", flat=True))
```

Trong `AdminUserUpdateSerializer` và `AdminUserCreateSerializer`, thêm field:
```python
    role_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
```

- [ ] **Step 4: Service (set permissions + assign roles, bust cache)**

Append vào `backend/apps/users/services.py`:

```python
from django.db import transaction  # đặt cùng nhóm import ở đầu file

from .models import Permission, Role, RolePermission, UserRoleAssignment
from .rbac import bump_rbac_cache_version


class RoleService:
    @staticmethod
    @transaction.atomic
    def set_role_permissions(role: Role, codes: list[str]) -> None:
        perm_ids = list(Permission.objects.filter(code__in=codes).values_list("id", flat=True))
        RolePermission.objects.filter(role=role).delete()
        RolePermission.objects.bulk_create(
            [RolePermission(role=role, permission_id=pid) for pid in perm_ids]
        )
        bump_rbac_cache_version()

    @staticmethod
    @transaction.atomic
    def assign_roles_to_user(user, role_ids: list[int]) -> None:
        target = set(role_ids)
        existing = set(
            UserRoleAssignment.objects.filter(user=user).values_list("role_id", flat=True)
        )
        UserRoleAssignment.objects.filter(user=user, role_id__in=existing - target).delete()
        UserRoleAssignment.objects.bulk_create(
            [UserRoleAssignment(user=user, role_id=rid) for rid in target - existing]
        )
        bump_rbac_cache_version()
```

> `import` đặt ở đầu file `services.py` cùng các import hiện có; ví dụ trên gộp cho gọn — khi implement, đưa các dòng `from ... import ...` lên đầu module.

- [ ] **Step 5: Views (roles CRUD + permissions list) + nối role_ids vào user update**

Append vào `backend/apps/users/views.py`:

```python
from .models import Permission, Role
from .serializers import PermissionSerializer, RoleSerializer, RoleWriteSerializer
from .services import RoleService


@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("users")])
def permissions_catalog(request):
    if not _is_admin(request.user):
        return Response({"detail": "Khong co quyen."}, status=status.HTTP_403_FORBIDDEN)
    perms = Permission.objects.all().order_by("section", "action")
    return Response(PermissionSerializer(perms, many=True).data)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_section("users")])
def role_collection(request):
    if not _is_admin(request.user):
        return Response({"detail": "Khong co quyen."}, status=status.HTTP_403_FORBIDDEN)
    if request.method == "GET":
        return Response(RoleSerializer(Role.objects.all(), many=True).data)

    serializer = RoleWriteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if not data.get("name"):
        return Response({"detail": "Thiếu tên role."}, status=status.HTTP_400_BAD_REQUEST)
    if Role.objects.filter(name=data["name"]).exists():
        return Response({"detail": "Tên role đã tồn tại."}, status=status.HTTP_400_BAD_REQUEST)
    role = Role.objects.create(
        name=data["name"],
        description=data.get("description", ""),
        scope_default=data.get("scope_default", "own"),
        is_active=data.get("is_active", True),
    )
    if "permission_codes" in data:
        RoleService.set_role_permissions(role, data["permission_codes"])
    return Response(RoleSerializer(role).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_section("users")])
def role_detail(request, role_id: int):
    if not _is_admin(request.user):
        return Response({"detail": "Khong co quyen."}, status=status.HTTP_403_FORBIDDEN)
    role = get_object_or_404(Role, id=role_id)
    if role.is_system:
        return Response(
            {"detail": "Không thể sửa/xóa role hệ thống."}, status=status.HTTP_400_BAD_REQUEST
        )

    if request.method == "DELETE":
        role.delete()
        from .rbac import bump_rbac_cache_version

        bump_rbac_cache_version()
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = RoleWriteSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if "name" in data and Role.objects.filter(name=data["name"]).exclude(id=role.id).exists():
        return Response({"detail": "Tên role đã tồn tại."}, status=status.HTTP_400_BAD_REQUEST)
    for field_name in ("name", "description", "scope_default", "is_active"):
        if field_name in data:
            setattr(role, field_name, data[field_name])
    role.save()
    if "permission_codes" in data:
        RoleService.set_role_permissions(role, data["permission_codes"])
    return Response(RoleSerializer(role).data)
```

Trong `admin_users_collection` (POST create) và `admin_user_detail` (PATCH), sau khi `user.save()`, thêm gán role:

```python
    if "role_ids" in serializer.validated_data:
        RoleService.assign_roles_to_user(user, serializer.validated_data["role_ids"])
```
(create: đặt sau `_replace_kiotviet_mappings(user, kiotviet_mappings)`; update: đặt cuối, trước `return`.)

- [ ] **Step 6: URLs**

Trong `backend/apps/users/urls.py`, thêm import các view mới và routes (trước dòng `]` cuối):

```python
    path("permissions", permissions_catalog, name="rbac-permissions"),
    path("roles", role_collection, name="rbac-roles-collection"),
    path("roles/<int:role_id>", role_detail, name="rbac-roles-detail"),
```
(Cập nhật khối `from .views import (...)` thêm `permissions_catalog, role_collection, role_detail`.)

- [ ] **Step 7: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_role_admin_api.py -q`
Expected: PASS (6 test).

- [ ] **Step 8: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/serializers.py backend/apps/users/services.py backend/apps/users/views.py backend/apps/users/urls.py backend/apps/users/tests/test_role_admin_api.py
git add backend/apps/users/serializers.py backend/apps/users/services.py backend/apps/users/views.py backend/apps/users/urls.py backend/apps/users/tests/test_role_admin_api.py
git commit -m "feat(rbac): role CRUD + permission matrix API + assign roles to user"
```

---

## Task 5: Pilot action-gating thật — orders export = `orders:export`

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_export_view.py`
- Test: `backend/apps/orders/tests/test_orders_export_permission.py`

> Mục đích: chứng minh action-level hoạt động trên endpoint thật, đồng thời tạo khuôn mẫu để migrate dần các endpoint khác. Endpoint pilot: `bulk_export_excel` (`POST /api/orders/bulk-export-excel`), hiện decorator là `@permission_classes([IsAuthenticated, require_section(*ORDER_SECTIONS)])` (xem `order_export_view.py:135-138`). Đổi sang `require_permission("orders:export")`.

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/orders/tests/test_orders_export_permission.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.test_helpers import make_user_with_permissions


class OrdersExportPermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_viewer_without_export_is_forbidden(self):
        user = make_user_with_permissions("viewer", ["orders:view"])
        self.client.force_authenticate(user=user)
        resp = self.client.post("/api/orders/bulk-export-excel", {}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_user_with_export_is_allowed(self):
        user = make_user_with_permissions("exporter", ["orders:view", "orders:export"])
        self.client.force_authenticate(user=user)
        resp = self.client.post("/api/orders/bulk-export-excel", {}, format="json")
        self.assertNotEqual(resp.status_code, 403)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/orders/tests/test_orders_export_permission.py -q`
Expected: FAIL — viewer hiện vẫn được export (200/khác 403) vì endpoint còn dùng `require_section`.

- [ ] **Step 3: Đổi permission class của view export**

Trong `order_export_view.py`, đổi decorator của (các) view export từ `require_section("orders", "landing-orders", "koc-orders")` sang `require_permission("orders:export")`, và cập nhật import:
```python
from apps.users.permissions import require_permission
```
(Giữ `require_section` nếu file còn dùng cho view khác.)

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/orders/tests/test_orders_export_permission.py -q`
Expected: PASS (2 test).

- [ ] **Step 5: Commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/transport/views/order_views_impl/order_export_view.py backend/apps/orders/tests/test_orders_export_permission.py
git add backend/apps/orders/transport/views/order_views_impl/order_export_view.py backend/apps/orders/tests/test_orders_export_permission.py
git commit -m "feat(rbac): gate orders export behind orders:export permission"
```

---

## Task 6: Frontend — types, usePermission, <Can>

**Files:**
- Modify: `frontend/src/App.tsx` (type `User`)
- Create: `frontend/src/lib/permissions.ts`, `frontend/src/hooks/usePermission.ts`, `frontend/src/components/Can.tsx`
- Test: `frontend/src/hooks/__tests__/usePermission.test.tsx`

- [ ] **Step 1: Mở rộng type User**

Trong `frontend/src/App.tsx`, thêm vào type `User` (sau `allowed_sections: string[];`):

```typescript
  permissions?: string[];
  scopes?: Record<string, string>;
  field_policy?: Record<string, Record<string, string>>;
  is_superuser?: boolean;
```

- [ ] **Step 2: Helper thuần `can()`**

Create `frontend/src/lib/permissions.ts`:

```typescript
export type EffectivePermissions = {
  permissions?: string[];
  is_superuser?: boolean;
};

/** True nếu user là superuser hoặc có permission code. */
export function can(user: EffectivePermissions | null | undefined, code: string): boolean {
  if (!user) return false;
  if (user.is_superuser) return true;
  return (user.permissions ?? []).includes(code);
}
```

- [ ] **Step 3: Viết test thất bại cho hook**

Create `frontend/src/hooks/__tests__/usePermission.test.tsx`:

```typescript
import { describe, expect, it } from "vitest";
import { can } from "@/lib/permissions";

describe("can()", () => {
  it("returns true for superuser regardless of list", () => {
    expect(can({ is_superuser: true, permissions: [] }, "orders:edit")).toBe(true);
  });
  it("returns true when code present", () => {
    expect(can({ permissions: ["orders:view"] }, "orders:view")).toBe(true);
  });
  it("returns false when code absent", () => {
    expect(can({ permissions: ["orders:view"] }, "orders:edit")).toBe(false);
  });
  it("returns false for null user", () => {
    expect(can(null, "orders:view")).toBe(false);
  });
});
```

- [ ] **Step 4: Chạy test để xác nhận FAIL**

Run: `cd frontend && npx vitest run src/hooks/__tests__/usePermission.test.tsx`
Expected: FAIL — `Cannot find module '@/lib/permissions'`.

- [ ] **Step 5: Implement hook + component**

Create `frontend/src/hooks/usePermission.ts`:

```typescript
import { useAppShell } from "@/app/AppShellContext";
import { can } from "@/lib/permissions";

/** Trả callback can("section:action") đọc quyền từ user trong AppShell. */
export function usePermission(): (code: string) => boolean {
  const { user } = useAppShell();
  return (code: string) => can(user, code);
}
```

Create `frontend/src/components/Can.tsx`:

```typescript
import type { ReactNode } from "react";
import { usePermission } from "@/hooks/usePermission";

/**
 * Ẩn children nếu user không có quyền. BE vẫn là nguồn enforcement thật — đây chỉ là UX.
 *   <Can permission="orders:export"><Button>Xuất Excel</Button></Can>
 */
export function Can({
  permission,
  fallback = null,
  children,
}: {
  permission: string;
  fallback?: ReactNode;
  children: ReactNode;
}) {
  const can = usePermission();
  return <>{can(permission) ? children : fallback}</>;
}
```

> Kiểm tra `useAppShell()` trả về `{ user }` (đã thấy trong `routes.tsx`). Nếu type `user` trong `AppShellContext.tsx` chưa có các field mới, dùng chung type `User` từ App.tsx hoặc nới type ở context.

- [ ] **Step 6: Chạy test để xác nhận PASS**

Run: `cd frontend && npx vitest run src/hooks/__tests__/usePermission.test.tsx`
Expected: PASS (4 test).

- [ ] **Step 7: Build + commit**

```bash
cd frontend && npm run build
cd .. && git add frontend/src/App.tsx frontend/src/lib/permissions.ts frontend/src/hooks/usePermission.ts frontend/src/components/Can.tsx frontend/src/hooks/__tests__/usePermission.test.tsx
git commit -m "feat(rbac-fe): usePermission hook + <Can> component + user permission types"
```

---

## Task 7: Frontend — API client + trang quản trị Role (CRUD + ma trận)

**Files:**
- Create: `frontend/src/lib/api/roles.ts`, `frontend/src/pages/RolesPage.tsx`
- Modify: `frontend/src/lib/routes.ts`, `frontend/src/app/modules.ts`, `frontend/src/app/routed-pages.tsx`, `frontend/src/app/routes.tsx`
- Test: `frontend/src/lib/api/__tests__/roles.test.ts`

- [ ] **Step 1: API client + test thất bại**

Create `frontend/src/lib/api/roles.ts`:

```typescript
import { fetchJson } from "@/services/http";

export type PermissionRow = {
  id: number;
  code: string;
  section: string;
  action: string;
  label: string;
};

export type Role = {
  id: number;
  name: string;
  description: string;
  is_system: boolean;
  scope_default: string;
  is_active: boolean;
  created_at: string;
  permission_codes: string[];
};

export type RoleWrite = {
  name?: string;
  description?: string;
  scope_default?: string;
  is_active?: boolean;
  permission_codes?: string[];
};

export function listPermissions(): Promise<PermissionRow[]> {
  return fetchJson("/api/users/permissions");
}
export function listRoles(): Promise<Role[]> {
  return fetchJson("/api/users/roles");
}
export function createRole(body: RoleWrite): Promise<Role> {
  return fetchJson("/api/users/roles", { method: "POST", body: JSON.stringify(body) });
}
export function updateRole(id: number, body: RoleWrite): Promise<Role> {
  return fetchJson(`/api/users/roles/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}
export function deleteRole(id: number): Promise<null> {
  return fetchJson(`/api/users/roles/${id}`, { method: "DELETE" });
}
```

Create `frontend/src/lib/api/__tests__/roles.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import * as http from "@/services/http";
import { createRole, listRoles, updateRole } from "@/lib/api/roles";

afterEach(() => vi.restoreAllMocks());

describe("roles api", () => {
  it("listRoles GET /api/users/roles", async () => {
    const spy = vi.spyOn(http, "fetchJson").mockResolvedValue([] as never);
    await listRoles();
    expect(spy).toHaveBeenCalledWith("/api/users/roles");
  });
  it("createRole POSTs body", async () => {
    const spy = vi.spyOn(http, "fetchJson").mockResolvedValue({} as never);
    await createRole({ name: "X", permission_codes: ["orders:view"] });
    expect(spy).toHaveBeenCalledWith(
      "/api/users/roles",
      expect.objectContaining({ method: "POST" }),
    );
  });
  it("updateRole PATCHes by id", async () => {
    const spy = vi.spyOn(http, "fetchJson").mockResolvedValue({} as never);
    await updateRole(7, { permission_codes: [] });
    expect(spy).toHaveBeenCalledWith(
      "/api/users/roles/7",
      expect.objectContaining({ method: "PATCH" }),
    );
  });
});
```

- [ ] **Step 2: Chạy test (FAIL) → không cần, client là pure; chạy để xác nhận PASS sau khi tạo**

Run: `cd frontend && npx vitest run src/lib/api/__tests__/roles.test.ts`
Expected: PASS (3 test).

- [ ] **Step 3: Routes + modules + lazy page**

Trong `frontend/src/lib/routes.ts`:
- Thêm `| "roles"` vào type `AppSection`.
- Thêm vào `SECTION_TO_PATH`: `roles: "/settings/roles",`.

Trong `frontend/src/app/modules.ts`, thêm vào sections của module `settings` (sau entry `users`/`departments`):
```typescript
      { key: "roles", labelKey: "topnav.tabs.roles", icon: ShieldCheck, requires: "users" },
```
(`ShieldCheck` đã import sẵn ở đầu file.)

Trong `frontend/src/app/routed-pages.tsx`, thêm lazy export `RolesRoute` theo đúng pattern các route khác (mở file xem mẫu `UsersRoute`).

Trong `frontend/src/app/routes.tsx`:
- import `RolesRoute` từ `@/app/routed-pages`.
- thêm route trong children của `/`:
```tsx
      {
        path: "settings/roles",
        element: (
          <Guard section="roles">
            <RolesPage />
          </Guard>
        ),
      },
```
(dùng `RolesRoute` nếu theo pattern lazy; đặt cạnh `settings/users`.)

- [ ] **Step 4: Trang RolesPage (CRUD + ma trận checkbox)**

Create `frontend/src/pages/RolesPage.tsx`:

```tsx
import { useEffect, useMemo, useState } from "react";
import {
  type PermissionRow,
  type Role,
  createRole,
  deleteRole,
  listPermissions,
  listRoles,
  updateRole,
} from "@/lib/api/roles";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";

const SCOPES = ["all", "department", "channel", "own"];

export default function RolesPage() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [perms, setPerms] = useState<PermissionRow[]>([]);
  const [selected, setSelected] = useState<Role | null>(null);
  const [draftCodes, setDraftCodes] = useState<Set<string>>(new Set());

  async function refresh() {
    const [r, p] = await Promise.all([listRoles(), listPermissions()]);
    setRoles(r);
    setPerms(p);
  }
  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    setDraftCodes(new Set(selected?.permission_codes ?? []));
  }, [selected]);

  // Nhóm permission theo section để render ma trận section × action.
  const sections = useMemo(() => {
    const map = new Map<string, PermissionRow[]>();
    for (const row of perms) {
      const list = map.get(row.section) ?? [];
      list.push(row);
      map.set(row.section, list);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [perms]);

  function toggle(code: string) {
    setDraftCodes((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }

  async function save() {
    if (!selected) return;
    await updateRole(selected.id, { permission_codes: [...draftCodes] });
    await refresh();
  }

  async function addRole() {
    const name = window.prompt("Tên role mới?");
    if (!name) return;
    const created = await createRole({ name, scope_default: "own" });
    await refresh();
    setSelected(created);
  }

  return (
    <div className="flex gap-4 p-4">
      <aside className="w-64 space-y-2">
        <Button onClick={addRole}>+ Role mới</Button>
        <ul className="space-y-1">
          {roles.map((r) => (
            <li key={r.id}>
              <button
                className={`w-full text-left px-2 py-1 rounded ${selected?.id === r.id ? "bg-sky-100" : ""}`}
                onClick={() => setSelected(r)}
              >
                {r.name} {r.is_system ? "🔒" : ""}
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="flex-1">
        {selected ? (
          <>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-lg font-semibold">{selected.name}</h2>
              <div className="space-x-2">
                <Button onClick={save} disabled={selected.is_system}>
                  Lưu quyền
                </Button>
                {!selected.is_system && (
                  <Button
                    variant="destructive"
                    onClick={async () => {
                      await deleteRole(selected.id);
                      setSelected(null);
                      await refresh();
                    }}
                  >
                    Xóa
                  </Button>
                )}
              </div>
            </div>
            <table className="w-full text-sm">
              <tbody>
                {sections.map(([section, rows]) => (
                  <tr key={section} className="border-b">
                    <td className="py-1 font-medium">{section}</td>
                    <td className="py-1">
                      <div className="flex flex-wrap gap-3">
                        {rows.map((row) => (
                          <label key={row.code} className="flex items-center gap-1">
                            <Checkbox
                              checked={draftCodes.has(row.code)}
                              onCheckedChange={() => toggle(row.code)}
                              disabled={selected.is_system}
                            />
                            {row.action}
                          </label>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : (
          <p className="text-muted-foreground">Chọn một role để xem/sửa quyền.</p>
        )}
      </section>
    </div>
  );
}
```

> Kiểm tra component shadcn `Checkbox` tồn tại (`frontend/src/components/ui/checkbox.tsx`). Nếu chưa, `npx shadcn@latest add checkbox`. Thêm i18n key `topnav.tabs.roles` vào file messages (theo pattern `topnav.tabs.leadAssignment`).

- [ ] **Step 5: Build + commit**

```bash
cd frontend && npx vitest run src/lib/api/__tests__/roles.test.ts && npm run build
cd .. && git add frontend/src/lib/api/roles.ts frontend/src/lib/api/__tests__/roles.test.ts frontend/src/pages/RolesPage.tsx frontend/src/lib/routes.ts frontend/src/app/modules.ts frontend/src/app/routed-pages.tsx frontend/src/app/routes.tsx
git commit -m "feat(rbac-fe): role admin page with permission matrix + nav/route"
```

---

## Task 8: Regression toàn cục + sửa test cũ vỡ do đổi nguồn require_section

**Files:** (tùy phát hiện)

- [ ] **Step 1: Chạy toàn bộ test backend liên quan section-gating**

Run:
```bash
python -m pytest apps/users apps/orders apps/customers apps/inventory -q
```
Expected: liệt kê test fail. Test fail điển hình: fixture tạo user STAFF + Department(allowed_sections) nhưng KHÔNG backfill → resolver thấy 0 quyền → 403.

- [ ] **Step 2: Sửa từng fixture vỡ dùng `make_user_with_sections`**

Với mỗi test fail, thay cách dựng user bằng helper:
```python
from apps.users.test_helpers import make_user_with_sections, make_admin
# user = User.objects.create_user(..., department=dept_with_sections)
user = make_user_with_sections("u", ["orders", "customers"])  # mọi action các section đó
```
hoặc gọi `sync_permissions(); backfill_roles_from_legacy(); bump_rbac_cache_version()` trong `setUp` nếu test cố ý kiểm tra ánh xạ phòng ban.

- [ ] **Step 3: Chạy lại đến khi xanh**

Run: `python -m pytest apps/users apps/orders apps/customers apps/inventory -q`
Expected: PASS toàn bộ.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test(rbac): migrate section-gated tests to role-based fixtures"
```

---

## Self-review checklist (S2)

- [ ] `require_section` shim trả đúng `:view` → mọi endpoint cũ giữ hành vi (đã có test regression).
- [ ] `/me` trả `permissions/scopes/field_policy/is_superuser`; admin → `is_superuser=true` + allowed_sections = VALID_SECTIONS.
- [ ] Role hệ thống (`is_system`) không sửa/xóa được qua API (400).
- [ ] Mọi mutation Role/RolePermission/UserRoleAssignment bust cache (`RoleService` + signals S1).
- [ ] FE: `<Can>` chỉ ẩn UI; BE vẫn enforce (pilot export có test 403).
- [ ] Tên nhất quán S1: `resolve_effective_permissions`, `EffectivePermissions.can`, `bump_rbac_cache_version`, `UserRoleAssignment`.

## Bàn giao S2 → S3

S2 mới gate **action**. Data-scope vẫn theo `order_channel_codes_scope` hardcode ADS. S3 sẽ viết `scope_queryset(user, section, qs)` tổng quát (own/department/channel/all) đọc `eff.scope_for(section)`, thay thế hardcode ADS bằng `RoleSectionScope` đã seed ở S1.
