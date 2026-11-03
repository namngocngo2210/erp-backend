# Deep RBAC — Sprint 3: Data-scope (own/department/channel/all) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Phụ thuộc:** Hoàn tất S1 (resolver + RoleSectionScope + backfill đã seed ADS→channel) và S2 (require_permission, /me, role admin).

**Goal:** Tổng quát hóa record-level scope cho đơn hàng theo 4 mức `all/department/channel/own`, **xóa hardcode `DepartmentID.ADS`** trong `order_channel_codes_scope`/`can_edit_order`. Scope hiệu dụng đọc từ resolver (`eff.scope_for("orders")`), nguồn cấu hình là `RoleSectionScope` (admin chỉnh qua trang Role). Bổ sung UI cấu hình scope theo section trong trang Role.

**Architecture:** Hàm trung tâm `scope_resolved_channel_codes(user)` (apps/users) trả tập channel code được phép theo scope hiệu dụng (None = không giới hạn). Orders dùng nó thay cho logic ADS cũ. Scope `own` không biểu diễn bằng channel code → thêm seam lọc theo owner (`Order.staff`) trong `_apply_user_channel_scope`. `channel` đọc `UserChannel`; `department` đọc `UserDepartment`→`Channel.departments`. Bust cache khi `UserChannel`/`UserDepartment` đổi.

**Tech Stack:** Django 5 / DRF, PostgreSQL, React SPA + TS.

**Spec:** mục 5.2 (chốt 2 record-level), 4 (scope rộng nhất thắng), 3 (RoleSectionScope).

---

## File Structure

**Backend (`backend/`):**
- `apps/users/scoping.py` — CREATE: `resolved_channel_codes(user, section)`, `department_channel_codes(user)`, hằng số.
- `apps/users/rbac.py` — MODIFY: thêm signal bust cache cho `UserChannel`, `UserDepartment` (đăng ký ở `ready()` — nhưng `UserChannel` thuộc app khác → đăng ký an toàn bằng sender lazy).
- `apps/orders/application/services/order_service_helpers.py` — MODIFY: viết lại `order_channel_codes_scope` + `can_edit_channel`/`assigned_channel_codes` không còn hardcode ADS.
- `apps/users/services.py` — MODIFY: `UserService.can_edit_order` đọc scope resolver thay vì `DepartmentID.ADS`.
- `apps/orders/transport/views/order_views_impl/_view_helpers.py` — MODIFY: `_apply_user_channel_scope` thêm nhánh `own` (lọc theo staff).
- `apps/users/serializers.py` + `apps/users/views.py` — MODIFY: API set/get `RoleSectionScope` cho role.
- `apps/orders/tests/test_order_scope_resolution.py` — CREATE.
- `apps/orders/tests/test_channel_scope_mutation_authz.py` — MODIFY: chuyển fixtures sang role channel-scope (không còn phụ thuộc DepartmentID.ADS hardcode).
- `apps/users/tests/test_role_section_scope_api.py` — CREATE.

**Frontend (`frontend/`):**
- `src/lib/api/roles.ts` — MODIFY: type + API `section_scopes`.
- `src/pages/RolesPage.tsx` — MODIFY: cột chọn scope (dropdown all/department/channel/own) theo section.

## Cách chạy test
Như S1/S2.

---

## Task 1: Central scope helper (apps/users/scoping.py)

**Files:**
- Create: `backend/apps/users/scoping.py`
- Test: `backend/apps/users/tests/test_scoping.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/users/tests/test_scoping.py`:

```python
from django.test import TestCase

from apps.channels_core.models import Channel, UserChannel
from apps.users.models import DataScope, Department, Role, RoleSectionScope, RolePermission, Permission, UserRoleAssignment, User, UserRole, UserDepartment
from apps.users.rbac import bump_rbac_cache_version
from apps.users.scoping import resolved_channel_codes


def _role_with_orders(scope_default, section_scope=None):
    role = Role.objects.create(name=f"r-{scope_default}-{section_scope}", scope_default=scope_default)
    perm = Permission.objects.get_or_create(
        code="orders:view", defaults={"section": "orders", "action": "view"}
    )[0]
    RolePermission.objects.create(role=role, permission=perm)
    if section_scope:
        RoleSectionScope.objects.create(role=role, section="orders", scope=section_scope)
    return role


class ResolvedChannelCodesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="x", role=UserRole.STAFF)
        self.ch_a = Channel.objects.create(code="a", name="A")
        self.ch_b = Channel.objects.create(code="b", name="B")

    def test_all_scope_returns_none(self):
        UserRoleAssignment.objects.create(user=self.user, role=_role_with_orders(DataScope.ALL))
        bump_rbac_cache_version()
        self.assertIsNone(resolved_channel_codes(self.user, "orders"))

    def test_channel_scope_returns_assigned_codes(self):
        UserChannel.objects.create(user=self.user, channel=self.ch_a, is_active=True)
        UserRoleAssignment.objects.create(
            user=self.user, role=_role_with_orders(DataScope.OWN, section_scope=DataScope.CHANNEL)
        )
        bump_rbac_cache_version()
        self.assertEqual(resolved_channel_codes(self.user, "orders"), {"a"})

    def test_channel_scope_no_assignment_returns_empty_set(self):
        UserRoleAssignment.objects.create(
            user=self.user, role=_role_with_orders(DataScope.OWN, section_scope=DataScope.CHANNEL)
        )
        bump_rbac_cache_version()
        self.assertEqual(resolved_channel_codes(self.user, "orders"), set())

    def test_department_scope_returns_dept_channel_codes(self):
        dept = Department.objects.create(name="Sales")
        self.ch_a.departments.add(dept)
        UserDepartment.objects.create(user=self.user, department=dept, is_primary=True)
        UserRoleAssignment.objects.create(
            user=self.user, role=_role_with_orders(DataScope.DEPARTMENT)
        )
        bump_rbac_cache_version()
        self.assertEqual(resolved_channel_codes(self.user, "orders"), {"a"})

    def test_own_scope_returns_sentinel(self):
        from apps.users.scoping import OWN_SCOPE_SENTINEL

        UserRoleAssignment.objects.create(user=self.user, role=_role_with_orders(DataScope.OWN))
        bump_rbac_cache_version()
        self.assertIs(resolved_channel_codes(self.user, "orders"), OWN_SCOPE_SENTINEL)

    def test_admin_returns_none(self):
        admin = User.objects.create_user(username="a", password="x", role=UserRole.ADMIN)
        bump_rbac_cache_version()
        self.assertIsNone(resolved_channel_codes(admin, "orders"))
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_scoping.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.users.scoping'`.

- [ ] **Step 3: Implement**

Create `backend/apps/users/scoping.py`:

```python
"""Tổng quát hóa record-level scope. Trả tập channel code được phép theo scope
hiệu dụng của user trên 1 section.

Quy ước trả về của resolved_channel_codes:
- None              → không giới hạn (all, hoặc admin/superuser).
- set[str]          → chỉ các channel code này (channel / department scope).
- OWN_SCOPE_SENTINEL→ scope theo người tạo/sở hữu; caller phải tự lọc theo owner field
                      (channel code không biểu diễn được "của tôi").

Không hardcode phòng ban — mọi quyết định lấy từ resolver + UserChannel/UserDepartment.
"""

from apps.channels_core.models import Channel, UserChannel

from .models import DataScope, UserDepartment
from .rbac import resolve_effective_permissions


class _OwnScope:
    __slots__ = ()

    def __repr__(self) -> str:
        return "OWN_SCOPE_SENTINEL"


OWN_SCOPE_SENTINEL = _OwnScope()


def _user_channel_codes(user) -> set[str]:
    return set(
        UserChannel.objects.filter(
            user=user, channel__deleted_at__isnull=True, is_active=True
        ).values_list("channel__code", flat=True)
    )


def department_channel_codes(user) -> set[str]:
    """Channel code của các kênh thuộc phòng ban user (qua UserDepartment, fallback User.department)."""
    dept_ids = set(
        UserDepartment.objects.filter(user=user).values_list("department_id", flat=True)
    )
    legacy_dept = getattr(user, "department_id", None)
    if legacy_dept:
        dept_ids.add(legacy_dept)
    if not dept_ids:
        return set()
    return set(
        Channel.objects.filter(
            departments__id__in=dept_ids, deleted_at__isnull=True
        ).values_list("code", flat=True)
    )


def resolved_channel_codes(user, section: str):
    eff = resolve_effective_permissions(user)
    if eff.is_superuser:
        return None
    scope = eff.scope_for(section)
    if scope == DataScope.ALL:
        return None
    if scope == DataScope.CHANNEL:
        return _user_channel_codes(user)
    if scope == DataScope.DEPARTMENT:
        return department_channel_codes(user)
    # own
    return OWN_SCOPE_SENTINEL
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_scoping.py -q`
Expected: PASS (6 test).

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/scoping.py backend/apps/users/tests/test_scoping.py
git add backend/apps/users/scoping.py backend/apps/users/tests/test_scoping.py
git commit -m "feat(rbac): central scope helper resolved_channel_codes (own/dept/channel/all)"
```

---

## Task 2: Bust cache khi UserChannel/UserDepartment đổi

**Files:**
- Modify: `backend/apps/users/rbac.py` (đăng ký signal cho UserChannel + UserDepartment)
- Test: `backend/apps/users/tests/test_scope_cache_invalidation.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/users/tests/test_scope_cache_invalidation.py`:

```python
from django.test import TestCase

from apps.channels_core.models import Channel, UserChannel
from apps.users.models import (
    DataScope, Permission, Role, RolePermission, RoleSectionScope,
    User, UserRole, UserRoleAssignment,
)
from apps.users.scoping import resolved_channel_codes


class ScopeCacheInvalidationTests(TestCase):
    def test_adding_user_channel_busts_cache(self):
        user = User.objects.create_user(username="uc", password="x", role=UserRole.STAFF)
        role = Role.objects.create(name="r", scope_default=DataScope.OWN)
        perm = Permission.objects.get_or_create(
            code="orders:view", defaults={"section": "orders", "action": "view"}
        )[0]
        RolePermission.objects.create(role=role, permission=perm)
        RoleSectionScope.objects.create(role=role, section="orders", scope=DataScope.CHANNEL)
        UserRoleAssignment.objects.create(user=user, role=role)

        ch = Channel.objects.create(code="x", name="X")
        # Trước khi gán kênh: tập rỗng (và được cache)
        self.assertEqual(resolved_channel_codes(user, "orders"), set())
        # Gán kênh → signal bust → thấy mã mới
        UserChannel.objects.create(user=user, channel=ch, is_active=True)
        self.assertEqual(resolved_channel_codes(user, "orders"), {"x"})
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_scope_cache_invalidation.py -q`
Expected: FAIL (lần 2 vẫn đọc cache cũ → set() thay vì {"x"}).

- [ ] **Step 3: Thêm signal cho UserChannel + UserDepartment**

Trong `backend/apps/users/rbac.py`, ở khối signal (cuối file, do S1 tạo), thêm UserDepartment vào vòng for model nội bộ và đăng ký UserChannel (app khác) qua import:

```python
from .models import UserDepartment as _UserDepartment  # noqa: E402

for _model in (
    _UserRoleAssignment, _RolePermission, _RoleSectionScope, _RoleFieldRule, Role, _UserDepartment
):
    post_save.connect(_bust, sender=_model, dispatch_uid=f"rbac_bust_save_{_model.__name__}")
    post_delete.connect(_bust, sender=_model, dispatch_uid=f"rbac_bust_del_{_model.__name__}")


def _connect_user_channel_signals():
    # UserChannel thuộc apps.channels_core — import trong hàm để tránh vòng import lúc app load.
    from apps.channels_core.models import UserChannel

    post_save.connect(_bust, sender=UserChannel, dispatch_uid="rbac_bust_save_UserChannel")
    post_delete.connect(_bust, sender=UserChannel, dispatch_uid="rbac_bust_del_UserChannel")
```

Trong `backend/apps/users/apps.py` `ready()`, sau `from . import rbac`, thêm:
```python
        rbac._connect_user_channel_signals()
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_scope_cache_invalidation.py -q`
Expected: PASS (1 test).

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/rbac.py backend/apps/users/apps.py backend/apps/users/tests/test_scope_cache_invalidation.py
git add backend/apps/users/rbac.py backend/apps/users/apps.py backend/apps/users/tests/test_scope_cache_invalidation.py
git commit -m "feat(rbac): bust resolver cache on UserChannel/UserDepartment changes"
```

---

## Task 3: Viết lại order_channel_codes_scope + can_edit_order (bỏ hardcode ADS)

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_helpers.py`
- Modify: `backend/apps/users/services.py`
- Test: `backend/apps/orders/tests/test_order_scope_resolution.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/orders/tests/test_order_scope_resolution.py`:

```python
from django.test import TestCase

from apps.channels_core.models import Channel, UserChannel
from apps.orders.services import OrderService
from apps.users.models import (
    DataScope, Permission, Role, RolePermission, RoleSectionScope,
    User, UserRole, UserRoleAssignment,
)
from apps.users.rbac import bump_rbac_cache_version


def _orders_role(scope_default, section_scope=None, name="r"):
    role = Role.objects.create(name=name, scope_default=scope_default)
    perm = Permission.objects.get_or_create(
        code="orders:view", defaults={"section": "orders", "action": "view"}
    )[0]
    RolePermission.objects.create(role=role, permission=perm)
    if section_scope:
        RoleSectionScope.objects.create(role=role, section="orders", scope=section_scope)
    return role


class OrderScopeResolutionTests(TestCase):
    def test_channel_scoped_role_restricts_to_assigned(self):
        user = User.objects.create_user(username="ch", password="x", role=UserRole.STAFF)
        ch = Channel.objects.create(code="x", name="X")
        UserChannel.objects.create(user=user, channel=ch, is_active=True)
        UserRoleAssignment.objects.create(
            user=user, role=_orders_role(DataScope.OWN, section_scope=DataScope.CHANNEL)
        )
        bump_rbac_cache_version()
        self.assertEqual(OrderService.order_channel_codes_scope(user), {"x"})

    def test_all_scoped_role_unrestricted(self):
        user = User.objects.create_user(username="all", password="x", role=UserRole.STAFF)
        UserRoleAssignment.objects.create(user=user, role=_orders_role(DataScope.ALL, name="all"))
        bump_rbac_cache_version()
        self.assertIsNone(OrderService.order_channel_codes_scope(user))

    def test_admin_unrestricted(self):
        admin = User.objects.create_user(username="adm", password="x", role=UserRole.ADMIN)
        bump_rbac_cache_version()
        self.assertIsNone(OrderService.order_channel_codes_scope(admin))

    def test_can_edit_mirrors_channel_scope(self):
        user = User.objects.create_user(username="e", password="x", role=UserRole.STAFF)
        ch = Channel.objects.create(code="x", name="X")
        UserChannel.objects.create(user=user, channel=ch, is_active=True)
        UserRoleAssignment.objects.create(
            user=user, role=_orders_role(DataScope.OWN, section_scope=DataScope.CHANNEL, name="ce")
        )
        bump_rbac_cache_version()
        from apps.orders.application.services.order_service_helpers import OrderServiceHelpers

        assigned = OrderServiceHelpers.assigned_channel_codes(user)
        self.assertTrue(OrderService.can_edit_order(user, "x", assigned).can_edit)
        self.assertFalse(OrderService.can_edit_order(user, "other", assigned).can_edit)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/orders/tests/test_order_scope_resolution.py -q`
Expected: FAIL — scope hiện theo `DepartmentID.ADS`; staff không thuộc ADS → `order_channel_codes_scope` trả None (test channel mong đợi `{"x"}`).

- [ ] **Step 3: Viết lại `order_channel_codes_scope`**

Trong `backend/apps/orders/application/services/order_service_helpers.py`, thay thân hàm `order_channel_codes_scope` (giữ chữ ký):

```python
    @staticmethod
    def order_channel_codes_scope(user) -> set[str] | None:
        """Tập channel code user được xem ở trang Đơn hàng theo scope hiệu dụng.

        None  = không giới hạn (all / admin).
        set() = bị giới hạn nhưng chưa được gán → không thấy đơn nào.
        Với scope `own`, trả None ở đây (channel không biểu diễn được "của tôi"); việc
        lọc theo owner do `_apply_user_channel_scope` xử lý riêng.
        """
        from apps.users.scoping import OWN_SCOPE_SENTINEL, resolved_channel_codes

        result = resolved_channel_codes(user, "orders")
        if result is OWN_SCOPE_SENTINEL:
            return None
        return result
```

Và `assigned_channel_codes` — bỏ string `"admin"` hardcode, dùng resolver superuser:

```python
    @staticmethod
    def assigned_channel_codes(user) -> set[str]:
        from apps.users.rbac import resolve_effective_permissions

        if resolve_effective_permissions(user).is_superuser:
            return set(Channel.objects.filter(deleted_at__isnull=True).values_list("code", flat=True))
        return set(
            UserChannel.objects.filter(
                user=user,
                channel__is_active=True,
                channel__deleted_at__isnull=True,
                is_active=True,
            ).values_list("channel__code", flat=True)
        )
```

- [ ] **Step 4: Viết lại `UserService.can_edit_order`**

Thay thân `can_edit_order` trong `backend/apps/users/services.py` (giữ chữ ký + dataclass):

```python
    @staticmethod
    def can_edit_order(user, order_channel_code: str, assigned_channel_codes: set[str]):
        """Quyền GHI một đơn theo kênh — mirror đúng read-scope.

        - superuser/all scope → toàn quyền
        - channel/department scope → chỉ kênh nằm trong tập được phép
        - own scope → không chặn theo kênh tại đây (chặn theo owner ở tầng view)
        """
        from apps.users.models import DataScope
        from apps.users.rbac import resolve_effective_permissions

        eff = resolve_effective_permissions(user)
        if eff.is_superuser:
            return UserPermissionResult(can_edit=True, reason="superuser")

        scope = eff.scope_for("orders")
        if scope in (DataScope.ALL, DataScope.OWN):
            return UserPermissionResult(can_edit=True, reason=f"scope-{scope}")
        if order_channel_code in assigned_channel_codes:
            return UserPermissionResult(can_edit=True, reason="assigned-channel")
        return UserPermissionResult(can_edit=False, reason="channel-not-assigned")
```

> Xóa import không còn dùng (`from .constants import DepartmentID`, `UserRole`) nếu ruff báo unused. Giữ `UserPermissionResult`.

- [ ] **Step 5: Chạy test để xác nhận PASS + regression mutation authz**

Trước hết cập nhật `apps/orders/tests/test_channel_scope_mutation_authz.py` để fixtures dùng role channel-scope thay vì dựa hành vi ADS hardcode. Thay `setUp` phần tạo `ads_staff`:

```python
    def setUp(self):
        self.client = APIClient()
        from apps.users.models import (
            DataScope, Permission, Role, RolePermission, RoleSectionScope, UserRoleAssignment,
        )
        from apps.users.rbac import bump_rbac_cache_version

        self.admin = User.objects.create_user(username="cs-admin", password="x", role=UserRole.ADMIN)
        self.ads_staff = User.objects.create_user(username="cs-ads-staff", password="x", role=UserRole.STAFF)

        # Role channel-scope trên orders (thay cho hành vi hardcode ADS cũ).
        role = Role.objects.create(name="cs-channel", scope_default=DataScope.OWN)
        for code in ("orders:view", "orders:edit"):
            section, action = code.split(":", 1)
            perm = Permission.objects.get_or_create(
                code=code, defaults={"section": section, "action": action}
            )[0]
            RolePermission.objects.create(role=role, permission=perm)
        RoleSectionScope.objects.create(role=role, section="orders", scope=DataScope.CHANNEL)
        UserRoleAssignment.objects.create(user=self.ads_staff, role=role)

        self.assigned_channel = Channel.objects.create(code="cs-a", name="Assigned")
        self.other_channel = Channel.objects.create(code="cs-b", name="Other")
        UserChannel.objects.create(user=self.ads_staff, channel=self.assigned_channel, is_active=True)
        bump_rbac_cache_version()

        self.customer = Customer.objects.create(
            full_name="K", phone_raw="0900000099", phone_normalized="0900000099"
        )
```

Run:
```bash
python -m pytest apps/orders/tests/test_order_scope_resolution.py apps/orders/tests/test_channel_scope_mutation_authz.py -q
```
Expected: PASS toàn bộ.

- [ ] **Step 6: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/application/services/order_service_helpers.py backend/apps/users/services.py backend/apps/orders/tests/test_order_scope_resolution.py backend/apps/orders/tests/test_channel_scope_mutation_authz.py
git add backend/apps/orders/application/services/order_service_helpers.py backend/apps/users/services.py backend/apps/orders/tests/test_order_scope_resolution.py backend/apps/orders/tests/test_channel_scope_mutation_authz.py
git commit -m "feat(rbac): drive order channel scope from resolver, remove hardcoded ADS"
```

---

## Task 4: Hỗ trợ scope `own` ở trang Đơn hàng (lọc theo staff)

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/_view_helpers.py`
- Test: `backend/apps/orders/tests/test_order_own_scope_list.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/orders/tests/test_order_own_scope_list.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderSource
from apps.users.models import (
    DataScope, Permission, Role, RolePermission, User, UserRole, UserRoleAssignment,
)
from apps.users.rbac import bump_rbac_cache_version


class OrderOwnScopeListTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(code="c", name="C")
        self.customer = Customer.objects.create(
            full_name="K", phone_raw="0900000011", phone_normalized="0900000011"
        )
        self.owner = User.objects.create_user(username="own", password="x", role=UserRole.STAFF)
        self.other = User.objects.create_user(username="oth", password="x", role=UserRole.STAFF)

        role = Role.objects.create(name="own-role", scope_default=DataScope.OWN)
        perm = Permission.objects.get_or_create(
            code="orders:view", defaults={"section": "orders", "action": "view"}
        )[0]
        RolePermission.objects.create(role=role, permission=perm)
        UserRoleAssignment.objects.create(user=self.owner, role=role)
        bump_rbac_cache_version()

        self.mine = Order.objects.create(
            channel=self.channel, customer=self.customer, name="mine", phone="0900000011",
            product="P", total_amount="1.00", order_source=OrderSource.LANDING, staff=self.owner,
            show_orders=True,
        )
        self.theirs = Order.objects.create(
            channel=self.channel, customer=self.customer, name="theirs", phone="0900000011",
            product="P", total_amount="1.00", order_source=OrderSource.LANDING, staff=self.other,
            show_orders=True,
        )

    def test_own_scope_lists_only_own_orders(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post("/api/orders/", {}, format="json")
        self.assertEqual(resp.status_code, 200)
        ids = {row["id"] for row in resp.json()["results"]}
        self.assertIn(self.mine.id, ids)
        self.assertNotIn(self.theirs.id, ids)
```

> Kiểm tra cấu trúc response list (`results`) khớp `list_orders`. Điều chỉnh nếu khác.

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/orders/tests/test_order_own_scope_list.py -q`
Expected: FAIL — own scope chưa lọc theo staff → thấy cả 2 đơn.

- [ ] **Step 3: Implement nhánh own trong `_apply_user_channel_scope`**

Trong `_view_helpers.py`, sửa `_apply_user_channel_scope` để xử lý own. Vì hàm hiện chỉ thao tác `filters["channel"]`, ta thêm khóa `filters["owner_user_id"]` khi scope own, rồi đảm bảo repository tôn trọng nó.

Thay hàm:

```python
def _apply_user_channel_scope(filters: dict, user) -> tuple[dict, bool]:
    """Giao filters['channel'] với kênh user được xem; xử lý thêm scope 'own'.

    Returns (filters, should_return_empty).
    """
    from apps.users.scoping import OWN_SCOPE_SENTINEL, resolved_channel_codes

    scope_codes = resolved_channel_codes(user, "orders")
    if scope_codes is OWN_SCOPE_SENTINEL:
        return {**filters, "owner_user_id": user.id}, False
    if scope_codes is None:
        return filters, False
    if not scope_codes:
        return filters, True
    requested = filters.get("channel") or []
    if requested:
        intersection = [code for code in requested if code in scope_codes]
        if not intersection:
            return filters, True
        filters = {**filters, "channel": intersection}
    else:
        filters = {**filters, "channel": sorted(scope_codes)}
    return filters, False
```

- [ ] **Step 4: Repository tôn trọng `owner_user_id`**

Trong `backend/apps/orders/repositories.py`, hàm `_apply_list_filters` (tìm nơi áp các filter), thêm nhánh:

```python
        owner_user_id = filters.get("owner_user_id")
        if owner_user_id:
            queryset = queryset.filter(staff_id=owner_user_id)
```
(Đặt cạnh các filter khác trong `_apply_list_filters`; đọc hàm để chèn đúng chỗ trước khi return queryset.)

- [ ] **Step 5: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/orders/tests/test_order_own_scope_list.py -q`
Expected: PASS (1 test).

- [ ] **Step 6: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/transport/views/order_views_impl/_view_helpers.py backend/apps/orders/repositories.py backend/apps/orders/tests/test_order_own_scope_list.py
git add backend/apps/orders/transport/views/order_views_impl/_view_helpers.py backend/apps/orders/repositories.py backend/apps/orders/tests/test_order_own_scope_list.py
git commit -m "feat(rbac): support own data-scope on orders list (filter by staff)"
```

---

## Task 5: API + UI cấu hình RoleSectionScope

**Files:**
- Modify: `backend/apps/users/serializers.py`, `backend/apps/users/views.py`, `backend/apps/users/services.py`
- Modify: `frontend/src/lib/api/roles.ts`, `frontend/src/pages/RolesPage.tsx`
- Test: `backend/apps/users/tests/test_role_section_scope_api.py`

- [ ] **Step 1: Viết test thất bại (BE)**

Create `backend/apps/users/tests/test_role_section_scope_api.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import Role, RoleSectionScope
from apps.users.test_helpers import make_admin


class RoleSectionScopeApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("adm")
        self.client.force_authenticate(user=self.admin)

    def test_set_section_scopes_on_role(self):
        role = Role.objects.create(name="r")
        resp = self.client.patch(
            f"/api/users/roles/{role.id}",
            {"section_scopes": [{"section": "orders", "scope": "channel"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            RoleSectionScope.objects.get(role=role, section="orders").scope, "channel"
        )

    def test_role_serializer_returns_section_scopes(self):
        role = Role.objects.create(name="r2")
        RoleSectionScope.objects.create(role=role, section="orders", scope="own")
        resp = self.client.get("/api/users/roles")
        row = next(r for r in resp.json() if r["id"] == role.id)
        self.assertEqual(row["section_scopes"], {"orders": "own"})
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_role_section_scope_api.py -q`
Expected: FAIL — `section_scopes` chưa hỗ trợ.

- [ ] **Step 3: Serializer + service + view**

Trong `RoleSerializer` (S2), thêm field + method:
```python
    section_scopes = serializers.SerializerMethodField()
    # ... thêm "section_scopes" vào Meta.fields
    def get_section_scopes(self, obj):
        return dict(
            RoleSectionScope.objects.filter(role=obj).values_list("section", "scope")
        )
```
(thêm import `RoleSectionScope`, `VALID_SECTIONS`.)

Trong `RoleWriteSerializer`, thêm:
```python
    section_scopes = serializers.ListField(child=serializers.DictField(), required=False)

    def validate_section_scopes(self, value):
        from .models import DataScope, VALID_SECTIONS

        valid_scopes = {c for c, _ in DataScope.choices}
        for item in value:
            if item.get("section") not in VALID_SECTIONS:
                raise serializers.ValidationError(f"section không hợp lệ: {item.get('section')}")
            if item.get("scope") not in valid_scopes:
                raise serializers.ValidationError(f"scope không hợp lệ: {item.get('scope')}")
        return value
```

Trong `RoleService` (services.py), thêm:
```python
    @staticmethod
    @transaction.atomic
    def set_role_section_scopes(role, items: list[dict]) -> None:
        from .models import RoleSectionScope

        RoleSectionScope.objects.filter(role=role).delete()
        RoleSectionScope.objects.bulk_create(
            [RoleSectionScope(role=role, section=i["section"], scope=i["scope"]) for i in items]
        )
        bump_rbac_cache_version()
```

Trong `role_collection` (POST) và `role_detail` (PATCH), sau khi xử lý `permission_codes`, thêm:
```python
    if "section_scopes" in data:
        RoleService.set_role_section_scopes(role, data["section_scopes"])
```

- [ ] **Step 4: Chạy test BE để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_role_section_scope_api.py -q`
Expected: PASS (2 test).

- [ ] **Step 5: FE — type + API + dropdown scope**

Trong `frontend/src/lib/api/roles.ts`:
- Thêm vào `Role`: `section_scopes: Record<string, string>;`
- Thêm vào `RoleWrite`: `section_scopes?: { section: string; scope: string }[];`

Trong `RolesPage.tsx`, thêm state `draftScopes` (Record<section, scope>) khởi tạo từ `selected.section_scopes`, render dropdown 4 mức cho mỗi section, và gửi kèm khi `save()`:

```tsx
const SCOPE_OPTIONS = ["all", "department", "channel", "own"];
// trong save():
await updateRole(selected.id, {
  permission_codes: [...draftCodes],
  section_scopes: Object.entries(draftScopes).map(([section, scope]) => ({ section, scope })),
});
```
Thêm cột "Scope" vào bảng ma trận: mỗi hàng section có `<select>` (hoặc shadcn `Select`) value = `draftScopes[section] ?? ""` (rỗng = dùng scope_default của role).

- [ ] **Step 6: Build + commit**

```bash
cd frontend && npm run build && cd ..
uv run --no-project --with ruff ruff check backend/apps/users/serializers.py backend/apps/users/services.py backend/apps/users/views.py backend/apps/users/tests/test_role_section_scope_api.py
git add backend/apps/users/serializers.py backend/apps/users/services.py backend/apps/users/views.py backend/apps/users/tests/test_role_section_scope_api.py frontend/src/lib/api/roles.ts frontend/src/pages/RolesPage.tsx
git commit -m "feat(rbac): configure RoleSectionScope via API + role admin UI"
```

---

## Self-review checklist (S3)

- [ ] Không còn tham chiếu `DepartmentID.ADS` trong logic scope đơn (`grep -rn "DepartmentID.ADS" backend/apps/orders backend/apps/users` chỉ còn ở test ánh xạ/migration nếu có).
- [ ] `order_channel_codes_scope`: None=all, set()=restricted-empty, codes=channel/department; own→None ở đây + lọc staff ở view.
- [ ] Scope rộng nhất thắng (qua resolver S1) — role all + role channel ⇒ all.
- [ ] Cache bust khi UserChannel/UserDepartment/RoleSectionScope đổi.
- [ ] `test_channel_scope_mutation_authz.py` vẫn pass với fixtures mới (channel scope, không ADS hardcode).
- [ ] Tên nhất quán: `resolved_channel_codes`, `OWN_SCOPE_SENTINEL`, `set_role_section_scopes`.

## Bàn giao S3 → S4

Record-level xong. S4: field-level — `RoleFieldRule` (đã có model + resolver field_policy từ S1) áp vào serializer output/write cho field nhạy cảm (giá vốn, lợi nhuận, SĐT), + UI cấu hình field rule trong trang Role.
