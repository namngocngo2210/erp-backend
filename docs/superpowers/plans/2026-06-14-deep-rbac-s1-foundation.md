# Deep RBAC — Sprint 1: Foundation (models + catalog + data migration + resolver) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dựng nền tảng dữ liệu RBAC+ABAC (Permission/Role/RolePermission/RoleSectionScope/RoleFieldRule/UserRoleAssignment/UserDepartment), seed catalog permission, data migration ánh xạ trạng thái cũ (ADMIN→Administrator, STAFF→role auto-gen theo phòng ban), và `PermissionResolver` + cache Redis. **KHÔNG đổi hành vi runtime** — chưa có endpoint nào enforce theo hệ mới.

**Architecture:** Toàn bộ model + logic RBAC sống trong `apps/users` (chủ quyền model, theo luật ranh giới module `docs/architecture-standard.md`). Catalog permission là dữ liệu tĩnh trong code (`rbac_catalog.py`), sync xuống DB bằng hàm idempotent dùng cả trong data migration lẫn management command. Resolver gom mọi role của user → union theo quy tắc mục 4 của spec (permission = union; scope = rộng nhất; field rule = ít hạn chế nhất), cache theo `user_id` kèm cache-version (mirror `bump_channels_cache_version`).

**Tech Stack:** Django 5 / DRF (Python 3.12), PostgreSQL, Redis cache (`django.core.cache.backends.redis.RedisCache`, KEY_PREFIX `desi`).

**Spec:** `docs/superpowers/specs/2026-06-14-deep-rbac-permissions-design.md` (mục 3, 4, 5.1, 8).

---

## Quy ước đặt tên (đọc trước khi code)

- `apps/users/models.py` **đã có** `class UserRole(models.TextChoices)` (ADMIN/STAFF) dùng rộng rãi. Model M2M "user↔role" trong spec gọi là `UserRole` sẽ **đặt tên Python là `UserRoleAssignment`** (db_table `rbac_user_roles`) để tránh đụng tên enum cũ. Enum `UserRole` giữ nguyên cho tương thích ngược (xóa ở S5).
- Tên bảng RBAC dùng prefix `rbac_` để dễ nhận diện và tách khỏi bảng nghiệp vụ.

## File Structure

**Backend (`backend/`):**
- `apps/users/models.py` — MODIFY: thêm `from django.conf import settings`; thêm các enum `PermissionAction`, `DataScope`, `FieldRuleType`; thêm 7 model RBAC (append cuối file).
- `apps/users/rbac_catalog.py` — CREATE: `PERMISSION_CATALOG` (dữ liệu tĩnh section→actions) + `iter_catalog_codes()` + `sync_permissions()`.
- `apps/users/rbac.py` — CREATE: `EffectivePermissions` dataclass + `resolve_effective_permissions(user)` + cache (`bump_rbac_cache_version`, `get_rbac_cache_version`), constant `SCOPE_RANK`, `FIELD_RANK`.
- `apps/users/migrations/0013_rbac_models.py` — CREATE: schema migration cho 7 model (số tiếp theo sau `0012`).
- `apps/users/migrations/0014_seed_rbac_catalog.py` — CREATE: data migration gọi `sync_permissions` (RunPython).
- `apps/users/migrations/0015_backfill_roles_from_legacy.py` — CREATE: data migration ánh xạ ADMIN/STAFF/Department sang Role + UserRoleAssignment.
- `apps/users/management/__init__.py` — CREATE (nếu chưa có).
- `apps/users/management/commands/__init__.py` — CREATE (nếu chưa có).
- `apps/users/management/commands/sync_rbac_permissions.py` — CREATE: command chạy lại `sync_permissions`.
- `apps/users/tests/__init__.py` — CREATE (nếu chưa có).
- `apps/users/tests/test_rbac_catalog.py` — CREATE.
- `apps/users/tests/test_rbac_resolver.py` — CREATE.
- `apps/users/tests/test_rbac_backfill_migration.py` — CREATE.

> Không động tới frontend trong S1.

## Cách chạy test (theo memory project)

Backend (container ephemeral, KHÔNG dùng container prod `desi-web`):
```bash
cd /home/desi/desi-order
docker inspect desi-web -f '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^[A-Z_]+=' | grep -vE '^PATH=|^LANG=|^GPG|^PYTHON' > /tmp/web.env
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && rm -f /tmp/prom_multiproc/* && python -m pytest <PATHS> --reuse-db -q'
```
Ruff (host): `uv run --no-project --with ruff ruff check <files>`

> Lưu ý: test resolver dùng cache Redis. Chạy test trong container có Redis (network `desi-order_default`). Mỗi test bust cache hoặc dùng `user_id` riêng để tránh nhiễm chéo.

---

## Task 1: RBAC enums + models

**Files:**
- Modify: `backend/apps/users/models.py`

- [ ] **Step 1: Thêm import settings**

Tại đầu file `backend/apps/users/models.py`, ngay dưới 2 import hiện có, thêm:

```python
from django.conf import settings
```

Kết quả phần đầu file:
```python
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
```

- [ ] **Step 2: Thêm enums + 7 model RBAC vào cuối file**

Append vào cuối `backend/apps/users/models.py` (sau `UserKiotvietMapping`):

```python
class PermissionAction(models.TextChoices):
    VIEW = "view", "Xem"
    CREATE = "create", "Tạo"
    EDIT = "edit", "Sửa"
    DELETE = "delete", "Xóa"
    APPROVE = "approve", "Duyệt"
    EXPORT = "export", "Xuất"


class DataScope(models.TextChoices):
    ALL = "all", "Toàn bộ"
    DEPARTMENT = "department", "Theo phòng ban"
    CHANNEL = "channel", "Theo kênh"
    OWN = "own", "Của tôi"


class FieldRuleType(models.TextChoices):
    HIDDEN = "hidden", "Ẩn"
    READONLY = "readonly", "Chỉ đọc"


class Permission(models.Model):
    """Catalog quyền tĩnh, seed từ rbac_catalog. Không cho user tự tạo."""

    code = models.CharField(max_length=100, unique=True)  # dạng "orders:edit"
    section = models.CharField(max_length=50)
    action = models.CharField(max_length=20, choices=PermissionAction.choices)
    label = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rbac_permissions"
        unique_together = ("section", "action")
        ordering = ["section", "action"]
        indexes = [
            models.Index(fields=["section"], name="rbacperm_section_idx"),
        ]

    def __str__(self) -> str:
        return self.code


class Role(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True, default="")
    # is_system=True → role hệ thống (Administrator), short-circuit full quyền, khóa sửa/xóa.
    is_system = models.BooleanField(default=False)
    scope_default = models.CharField(
        max_length=20, choices=DataScope.choices, default=DataScope.OWN
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rbac_roles"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="role_links"
    )

    class Meta:
        db_table = "rbac_role_permissions"
        unique_together = ("role", "permission")
        indexes = [
            models.Index(fields=["role", "permission"], name="rbac_rp_role_perm_idx"),
        ]


class RoleSectionScope(models.Model):
    """ABAC override data-scope theo section cho 1 role (đè scope_default của role)."""

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="section_scopes")
    section = models.CharField(max_length=50)
    scope = models.CharField(max_length=20, choices=DataScope.choices)

    class Meta:
        db_table = "rbac_role_section_scopes"
        unique_together = ("role", "section")
        indexes = [
            models.Index(fields=["role", "section"], name="rbac_rss_role_sec_idx"),
        ]


class RoleFieldRule(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="field_rules")
    section = models.CharField(max_length=50)
    field = models.CharField(max_length=100)
    rule = models.CharField(max_length=20, choices=FieldRuleType.choices)

    class Meta:
        db_table = "rbac_role_field_rules"
        unique_together = ("role", "section", "field")


class UserRoleAssignment(models.Model):
    """Gán role cho user (spec gọi là UserRole; đổi tên Python để khỏi đụng enum UserRole)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="role_assignments"
    )
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="user_assignments")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rbac_user_roles"
        unique_together = ("user", "role")
        indexes = [
            models.Index(fields=["user"], name="rbac_ur_user_idx"),
        ]


class UserDepartment(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="department_links"
    )
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="user_links"
    )
    is_primary = models.BooleanField(default=False)

    class Meta:
        db_table = "rbac_user_departments"
        unique_together = ("user", "department")
        indexes = [
            models.Index(fields=["user"], name="rbac_ud_user_idx"),
        ]
```

- [ ] **Step 3: Sinh migration schema**

Run:
```bash
python manage.py makemigrations users --name rbac_models
```
Expected: tạo `apps/users/migrations/0013_rbac_models.py` chứa CreateModel cho 7 model. Mở file kiểm tra không có thao tác AlterField ngoài ý muốn trên `User`/`Department`.

- [ ] **Step 4: Chạy migrate để xác nhận schema hợp lệ**

Run:
```bash
python manage.py migrate users
```
Expected: applying `users.0013_rbac_models... OK`, không lỗi.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/users/models.py backend/apps/users/migrations/0013_rbac_models.py
git commit -m "feat(rbac): add Permission/Role/scope/field/user-role/user-dept models"
```

---

## Task 2: Permission catalog + sync (idempotent)

**Files:**
- Create: `backend/apps/users/rbac_catalog.py`
- Test: `backend/apps/users/tests/test_rbac_catalog.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/users/tests/__init__.py` (nếu chưa có, để trống). Create `backend/apps/users/tests/test_rbac_catalog.py`:

```python
from django.test import TestCase

from apps.users.models import Permission, VALID_SECTIONS
from apps.users.rbac_catalog import PERMISSION_CATALOG, iter_catalog_codes, sync_permissions


class CatalogShapeTests(TestCase):
    def test_every_catalog_section_is_valid(self):
        for section in PERMISSION_CATALOG:
            self.assertIn(section, VALID_SECTIONS, f"section {section} không có trong VALID_SECTIONS")

    def test_every_section_has_view(self):
        for section, actions in PERMISSION_CATALOG.items():
            self.assertIn("view", actions, f"section {section} thiếu action view")

    def test_codes_are_section_colon_action(self):
        for code in iter_catalog_codes():
            self.assertRegex(code, r"^[a-z0-9-]+:[a-z]+$")


class SyncPermissionsTests(TestCase):
    def test_sync_creates_all_catalog_codes(self):
        created, deleted = sync_permissions()
        self.assertEqual(created, len(list(iter_catalog_codes())))
        self.assertEqual(deleted, 0)
        self.assertEqual(
            set(Permission.objects.values_list("code", flat=True)),
            set(iter_catalog_codes()),
        )

    def test_sync_is_idempotent(self):
        sync_permissions()
        count_before = Permission.objects.count()
        created, deleted = sync_permissions()
        self.assertEqual(created, 0)
        self.assertEqual(deleted, 0)
        self.assertEqual(Permission.objects.count(), count_before)

    def test_sync_prunes_codes_not_in_catalog(self):
        sync_permissions()
        Permission.objects.create(code="orders:obsolete", section="orders", action="obsolete")
        created, deleted = sync_permissions()
        self.assertEqual(created, 0)
        self.assertEqual(deleted, 1)
        self.assertFalse(Permission.objects.filter(code="orders:obsolete").exists())
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_rbac_catalog.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.users.rbac_catalog'`.

- [ ] **Step 3: Tạo catalog + sync**

Create `backend/apps/users/rbac_catalog.py`:

```python
"""Catalog quyền tĩnh (nguồn sự thật ở code). Sync xuống bảng Permission.

Map từ section → danh sách action. Mọi section đều có "view". Section nghiệp vụ
thêm create/edit/delete; section có duyệt/xuất thêm approve/export. Danh sách bám
theo VALID_SECTIONS và endpoint thực tế (rà lại khi mở rộng).
"""

from collections.abc import Iterator

from django.db import transaction

from .models import Permission

# section -> tuple[action, ...]. Thứ tự action không quan trọng.
PERMISSION_CATALOG: dict[str, tuple[str, ...]] = {
    # Sales / đơn hàng — full CRUD + duyệt + xuất Excel
    "orders": ("view", "create", "edit", "delete", "approve", "export"),
    "landing-orders": ("view", "create", "edit", "delete", "export"),
    "koc-orders": ("view", "create", "edit", "delete", "export"),
    # CRM
    "customers": ("view", "create", "edit", "delete", "export"),
    "loyal-customers": ("view", "export"),
    "resale-tracking": ("view", "edit", "export"),
    # Báo cáo (chỉ xem + xuất)
    "dashboard": ("view", "export"),
    "ads-report": ("view", "export"),
    # Kênh & tích hợp
    "channels": ("view", "create", "edit", "delete"),
    "kiotviet": ("view", "edit"),
    "facebook-ads": ("view", "edit"),
    "google-ads": ("view", "edit"),
    # Catalog
    "products": ("view", "create", "edit", "delete", "export"),
    "product-categories": ("view", "create", "edit", "delete"),
    # Kho
    "warehouses": ("view", "create", "edit", "delete"),
    "stock": ("view", "edit", "export"),
    "stock-documents": ("view", "create", "edit", "delete", "approve", "export"),
    "returns": ("view", "create", "edit", "approve"),
    # Quản trị
    "users": ("view", "create", "edit", "delete"),
    "lead-assignment": ("view", "edit"),
    "audit": ("view", "export"),
    "cron-jobs": ("view", "edit"),
    "sync": ("view", "edit"),
}


def iter_catalog_codes() -> Iterator[str]:
    for section, actions in PERMISSION_CATALOG.items():
        for action in actions:
            yield f"{section}:{action}"


@transaction.atomic
def sync_permissions() -> tuple[int, int]:
    """Đồng bộ bảng Permission khớp catalog. Trả (số tạo mới, số xóa).

    Idempotent: tạo code thiếu, xóa code không còn trong catalog, giữ nguyên code khớp.
    """
    desired: dict[str, tuple[str, str]] = {}
    for section, actions in PERMISSION_CATALOG.items():
        for action in actions:
            desired[f"{section}:{action}"] = (section, action)

    existing = set(Permission.objects.values_list("code", flat=True))
    to_create = [
        Permission(code=code, section=section, action=action, label=f"{section} · {action}")
        for code, (section, action) in desired.items()
        if code not in existing
    ]
    if to_create:
        Permission.objects.bulk_create(to_create)

    obsolete = existing - set(desired)
    deleted = 0
    if obsolete:
        deleted, _ = Permission.objects.filter(code__in=obsolete).delete()

    return len(to_create), deleted
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_rbac_catalog.py -q`
Expected: PASS (6 test).

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/rbac_catalog.py backend/apps/users/tests/test_rbac_catalog.py
git add backend/apps/users/rbac_catalog.py backend/apps/users/tests/__init__.py backend/apps/users/tests/test_rbac_catalog.py
git commit -m "feat(rbac): permission catalog + idempotent sync_permissions"
```

---

## Task 3: Seed catalog qua data migration + management command

**Files:**
- Create: `backend/apps/users/migrations/0014_seed_rbac_catalog.py`
- Create: `backend/apps/users/management/__init__.py`
- Create: `backend/apps/users/management/commands/__init__.py`
- Create: `backend/apps/users/management/commands/sync_rbac_permissions.py`

- [ ] **Step 1: Tạo data migration seed catalog**

Create `backend/apps/users/migrations/0014_seed_rbac_catalog.py`:

```python
from django.db import migrations


def seed_permissions(apps, schema_editor):
    Permission = apps.get_model("users", "Permission")
    # Không import sync_permissions trực tiếp (model lịch sử ≠ model runtime). Nhân
    # bản logic catalog tối thiểu tại đây để migration ổn định theo thời gian.
    from apps.users.rbac_catalog import PERMISSION_CATALOG

    desired = {
        f"{section}:{action}": (section, action)
        for section, actions in PERMISSION_CATALOG.items()
        for action in actions
    }
    existing = set(Permission.objects.values_list("code", flat=True))
    to_create = [
        Permission(code=code, section=section, action=action, label=f"{section} · {action}")
        for code, (section, action) in desired.items()
        if code not in existing
    ]
    if to_create:
        Permission.objects.bulk_create(to_create)


def noop_reverse(apps, schema_editor):
    # Không xóa permission khi rollback để tránh mất FK của Role; an toàn hơn.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0013_rbac_models"),
    ]

    operations = [
        migrations.RunPython(seed_permissions, noop_reverse),
    ]
```

- [ ] **Step 2: Tạo management command**

Create `backend/apps/users/management/__init__.py` (rỗng), `backend/apps/users/management/commands/__init__.py` (rỗng), và `backend/apps/users/management/commands/sync_rbac_permissions.py`:

```python
from django.core.management.base import BaseCommand

from apps.users.rbac_catalog import sync_permissions


class Command(BaseCommand):
    help = "Đồng bộ bảng Permission khớp PERMISSION_CATALOG (idempotent)."

    def handle(self, *args, **options):
        created, deleted = sync_permissions()
        self.stdout.write(
            self.style.SUCCESS(f"RBAC permissions synced: +{created} created, -{deleted} deleted")
        )
```

- [ ] **Step 3: Chạy migrate + verify**

Run:
```bash
python manage.py migrate users
python manage.py shell -c "from apps.users.models import Permission; print(Permission.objects.count())"
```
Expected: migrate `0014_seed_rbac_catalog... OK`; số permission > 0 và khớp tổng catalog.

- [ ] **Step 4: Verify command idempotent**

Run:
```bash
python manage.py sync_rbac_permissions
```
Expected: `RBAC permissions synced: +0 created, -0 deleted` (vì migration đã seed).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/users/migrations/0014_seed_rbac_catalog.py backend/apps/users/management/
git commit -m "feat(rbac): seed permission catalog via migration + sync command"
```

---

## Task 4: PermissionResolver + cache

**Files:**
- Create: `backend/apps/users/rbac.py`
- Test: `backend/apps/users/tests/test_rbac_resolver.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/users/tests/test_rbac_resolver.py`:

```python
from django.test import TestCase

from apps.users.models import (
    DataScope,
    FieldRuleType,
    Permission,
    Role,
    RoleFieldRule,
    RolePermission,
    RoleSectionScope,
    User,
    UserRole,
    UserRoleAssignment,
)
from apps.users.rbac import bump_rbac_cache_version, resolve_effective_permissions


def _perm(code):
    section, action = code.split(":", 1)
    return Permission.objects.get_or_create(
        code=code, defaults={"section": section, "action": action}
    )[0]


def _grant(role, *codes):
    for code in codes:
        RolePermission.objects.create(role=role, permission=_perm(code))


class ResolverUnionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u1", password="x", role=UserRole.STAFF)

    def test_permission_codes_are_union_across_roles(self):
        r1 = Role.objects.create(name="r1", scope_default=DataScope.OWN)
        r2 = Role.objects.create(name="r2", scope_default=DataScope.OWN)
        _grant(r1, "orders:view")
        _grant(r2, "orders:edit", "customers:view")
        UserRoleAssignment.objects.create(user=self.user, role=r1)
        UserRoleAssignment.objects.create(user=self.user, role=r2)

        eff = resolve_effective_permissions(self.user)
        self.assertTrue(eff.can("orders:view"))
        self.assertTrue(eff.can("orders:edit"))
        self.assertTrue(eff.can("customers:view"))
        self.assertFalse(eff.can("orders:delete"))

    def test_scope_is_widest_across_roles(self):
        r_own = Role.objects.create(name="own", scope_default=DataScope.OWN)
        r_all = Role.objects.create(name="all", scope_default=DataScope.ALL)
        _grant(r_own, "orders:view")
        _grant(r_all, "orders:view")
        UserRoleAssignment.objects.create(user=self.user, role=r_own)
        UserRoleAssignment.objects.create(user=self.user, role=r_all)

        eff = resolve_effective_permissions(self.user)
        self.assertEqual(eff.scope_for("orders"), DataScope.ALL)

    def test_section_scope_override_beats_role_default(self):
        role = Role.objects.create(name="r", scope_default=DataScope.OWN)
        _grant(role, "orders:view")
        RoleSectionScope.objects.create(role=role, section="orders", scope=DataScope.CHANNEL)
        UserRoleAssignment.objects.create(user=self.user, role=role)

        eff = resolve_effective_permissions(self.user)
        self.assertEqual(eff.scope_for("orders"), DataScope.CHANNEL)

    def test_field_rule_least_restrictive_wins(self):
        r_hidden = Role.objects.create(name="h", scope_default=DataScope.OWN)
        r_readonly = Role.objects.create(name="ro", scope_default=DataScope.OWN)
        _grant(r_hidden, "orders:view")
        _grant(r_readonly, "orders:view")
        RoleFieldRule.objects.create(
            role=r_hidden, section="orders", field="cost_price", rule=FieldRuleType.HIDDEN
        )
        RoleFieldRule.objects.create(
            role=r_readonly, section="orders", field="cost_price", rule=FieldRuleType.READONLY
        )
        UserRoleAssignment.objects.create(user=self.user, role=r_hidden)
        UserRoleAssignment.objects.create(user=self.user, role=r_readonly)

        eff = resolve_effective_permissions(self.user)
        # readonly ít hạn chế hơn hidden ⇒ field hiệu dụng = readonly
        self.assertEqual(eff.field_policy.get("orders", {}).get("cost_price"), FieldRuleType.READONLY)

    def test_system_role_short_circuits_to_full(self):
        admin_role = Role.objects.create(name="Administrator", is_system=True, scope_default=DataScope.ALL)
        UserRoleAssignment.objects.create(user=self.user, role=admin_role)

        eff = resolve_effective_permissions(self.user)
        self.assertTrue(eff.is_superuser)
        self.assertTrue(eff.can("anything:at-all"))
        self.assertEqual(eff.scope_for("orders"), DataScope.ALL)

    def test_legacy_admin_flag_short_circuits(self):
        admin = User.objects.create_user(username="legacy-admin", password="x", role=UserRole.ADMIN)
        eff = resolve_effective_permissions(admin)
        self.assertTrue(eff.is_superuser)


class ResolverCacheTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cacheu", password="x", role=UserRole.STAFF)
        self.role = Role.objects.create(name="cr", scope_default=DataScope.OWN)
        _grant(self.role, "orders:view")
        UserRoleAssignment.objects.create(user=self.user, role=self.role)

    def test_result_is_cached_until_version_bump(self):
        eff1 = resolve_effective_permissions(self.user)
        self.assertTrue(eff1.can("orders:view"))
        # Thêm quyền nhưng KHÔNG bust → vẫn đọc cache cũ
        _grant(self.role, "orders:edit")
        eff_cached = resolve_effective_permissions(self.user)
        self.assertFalse(eff_cached.can("orders:edit"))
        # Bust → tính lại
        bump_rbac_cache_version()
        eff2 = resolve_effective_permissions(self.user)
        self.assertTrue(eff2.can("orders:edit"))
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_rbac_resolver.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.users.rbac'`.

- [ ] **Step 3: Implement resolver + cache**

Create `backend/apps/users/rbac.py`:

```python
"""Resolver quyền hiệu dụng (RBAC + ABAC) + cache Redis theo user.

Quy tắc union (spec mục 4):
- permission: union tập hợp.
- scope theo section: rộng nhất (all > department > channel > own).
- field rule: ít hạn chế nhất (hidden > readonly > full).

Cache theo user_id, kèm cache-version (mirror bump_channels_cache_version). Bất kỳ
thay đổi role/gán role/permission/scope/field/UserChannel/UserDepartment → bump version.
"""

from dataclasses import dataclass, field

from django.core.cache import cache

from .models import (
    DataScope,
    FieldRuleType,
    Role,
    RoleFieldRule,
    RolePermission,
    RoleSectionScope,
    UserRole,
)

RBAC_CACHE_VERSION_KEY = "rbac:cache:version"

# Hạng scope: số lớn = rộng hơn. Dùng để chọn "rộng nhất".
SCOPE_RANK = {
    DataScope.OWN: 0,
    DataScope.CHANNEL: 1,
    DataScope.DEPARTMENT: 2,
    DataScope.ALL: 3,
}
# Hạng field rule: số lớn = hạn chế hơn. Chọn "ít hạn chế nhất" = rank nhỏ nhất.
FIELD_RANK = {
    FieldRuleType.READONLY: 1,
    FieldRuleType.HIDDEN: 2,
}


def get_rbac_cache_version() -> int:
    version = cache.get(RBAC_CACHE_VERSION_KEY)
    if version is None:
        cache.set(RBAC_CACHE_VERSION_KEY, 1, timeout=None)
        return 1
    return int(version)


def bump_rbac_cache_version() -> int:
    try:
        return int(cache.incr(RBAC_CACHE_VERSION_KEY))
    except ValueError:
        cache.set(RBAC_CACHE_VERSION_KEY, 2, timeout=None)
        return 2


@dataclass(frozen=True)
class EffectivePermissions:
    permission_codes: frozenset = field(default_factory=frozenset)
    scope_by_section: dict = field(default_factory=dict)
    field_policy: dict = field(default_factory=dict)
    is_superuser: bool = False

    def can(self, code: str) -> bool:
        return self.is_superuser or code in self.permission_codes

    def scope_for(self, section: str) -> str:
        if self.is_superuser:
            return DataScope.ALL
        return self.scope_by_section.get(section, DataScope.OWN)

    def to_cache(self) -> dict:
        return {
            "permission_codes": sorted(self.permission_codes),
            "scope_by_section": self.scope_by_section,
            "field_policy": self.field_policy,
            "is_superuser": self.is_superuser,
        }

    @classmethod
    def from_cache(cls, data: dict) -> "EffectivePermissions":
        return cls(
            permission_codes=frozenset(data["permission_codes"]),
            scope_by_section=data["scope_by_section"],
            field_policy=data["field_policy"],
            is_superuser=data["is_superuser"],
        )


_SUPERUSER = EffectivePermissions(is_superuser=True)


def _compute(user) -> EffectivePermissions:
    # Short-circuit: cờ legacy admin hoặc bất kỳ role is_system nào.
    if getattr(user, "role", None) == UserRole.ADMIN:
        return _SUPERUSER

    roles = list(Role.objects.filter(user_assignments__user=user, is_active=True).distinct())
    if any(r.is_system for r in roles):
        return _SUPERUSER
    if not roles:
        return EffectivePermissions()

    role_ids = [r.id for r in roles]

    # 1) permission codes = union
    codes = set(
        RolePermission.objects.filter(role_id__in=role_ids).values_list(
            "permission__code", flat=True
        )
    )

    # 2) scope theo section = rộng nhất.
    #    Mỗi role áp scope cho mọi section mà nó cấp quyền: override RoleSectionScope nếu có,
    #    ngược lại scope_default của role.
    overrides: dict[int, dict[str, str]] = {}
    for rss in RoleSectionScope.objects.filter(role_id__in=role_ids):
        overrides.setdefault(rss.role_id, {})[rss.section] = rss.scope

    # section mà mỗi role cấp quyền
    sections_by_role: dict[int, set[str]] = {}
    for code in RolePermission.objects.filter(role_id__in=role_ids).values_list(
        "role_id", "permission__section"
    ):
        rid, section = code
        sections_by_role.setdefault(rid, set()).add(section)

    scope_default = {r.id: r.scope_default for r in roles}
    scope_by_section: dict[str, str] = {}
    for rid in role_ids:
        for section in sections_by_role.get(rid, set()):
            scope = overrides.get(rid, {}).get(section, scope_default[rid])
            current = scope_by_section.get(section)
            if current is None or SCOPE_RANK[scope] > SCOPE_RANK[current]:
                scope_by_section[section] = scope

    # 3) field policy = ít hạn chế nhất theo (section, field)
    field_policy: dict[str, dict[str, str]] = {}
    for fr in RoleFieldRule.objects.filter(role_id__in=role_ids):
        section_rules = field_policy.setdefault(fr.section, {})
        current = section_rules.get(fr.field)
        if current is None or FIELD_RANK[fr.rule] < FIELD_RANK[current]:
            section_rules[fr.field] = fr.rule

    return EffectivePermissions(
        permission_codes=frozenset(codes),
        scope_by_section=scope_by_section,
        field_policy=field_policy,
        is_superuser=False,
    )


def resolve_effective_permissions(user) -> EffectivePermissions:
    if not user or not getattr(user, "is_authenticated", False):
        return EffectivePermissions()

    version = get_rbac_cache_version()
    cache_key = f"rbac:eff:{version}:{user.id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return EffectivePermissions.from_cache(cached)

    eff = _compute(user)
    cache.set(cache_key, eff.to_cache(), timeout=60 * 30)
    return eff
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_rbac_resolver.py -q`
Expected: PASS (8 test).

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/rbac.py backend/apps/users/tests/test_rbac_resolver.py
git add backend/apps/users/rbac.py backend/apps/users/tests/test_rbac_resolver.py
git commit -m "feat(rbac): EffectivePermissions resolver with union semantics + redis cache"
```

---

## Task 5: Data migration ánh xạ trạng thái cũ → Role/UserRoleAssignment

**Files:**
- Create: `backend/apps/users/migrations/0015_backfill_roles_from_legacy.py`
- Test: `backend/apps/users/tests/test_rbac_backfill_migration.py`

**Ánh xạ (zero-regression):**
- Tạo role `Administrator` (`is_system=True`, `scope_default=all`), gán **mọi** permission. Mọi user `role=ADMIN` → gán `Administrator`.
- Với mỗi `Department`: tạo role `Phòng: {name}` (`is_system=False`). `scope_default=all`. Gán permission = **mọi action** của từng section trong `department.allowed_sections` (giữ nguyên khả năng thao tác hiện tại — page access = thao tác được). Riêng phòng ban `id == DepartmentID.ADS`: thêm `RoleSectionScope(section="orders", scope=channel)` (tổng quát hóa hardcode ADS, chuẩn bị cho S3). Mọi user `role=STAFF` thuộc phòng này → gán role tương ứng.
- Tạo role `Nhân viên mặc định` cho STAFF **không có phòng ban**: gán mọi action của các section `orders, landing-orders, koc-orders, customers, resale-tracking` (mirror `UserSerializer.get_allowed_sections` cho user không phòng ban), `scope_default=all`.

- [ ] **Step 1: Viết test thất bại (kiểm tra resolver sau backfill)**

Create `backend/apps/users/tests/test_rbac_backfill_migration.py`:

```python
from django.test import TestCase

from apps.users.constants import DepartmentID
from apps.users.models import (
    DataScope,
    Department,
    Role,
    User,
    UserRole,
    UserRoleAssignment,
)
from apps.users.rbac import bump_rbac_cache_version, resolve_effective_permissions
from apps.users.rbac_backfill import backfill_roles_from_legacy


class BackfillRolesTests(TestCase):
    def setUp(self):
        self.ads_dept = Department.objects.create(
            id=DepartmentID.ADS, name="Ads", allowed_sections=["orders"]
        )
        self.sales_dept = Department.objects.create(
            id=DepartmentID.SALES, name="Sales", allowed_sections=["orders", "customers"]
        )
        self.admin = User.objects.create_user(username="adm", password="x", role=UserRole.ADMIN)
        self.ads_staff = User.objects.create_user(
            username="ads1", password="x", role=UserRole.STAFF, department=self.ads_dept
        )
        self.sales_staff = User.objects.create_user(
            username="sales1", password="x", role=UserRole.STAFF, department=self.sales_dept
        )
        self.deptless_staff = User.objects.create_user(
            username="free1", password="x", role=UserRole.STAFF
        )
        # Catalog cần tồn tại để gán permission theo section.
        from apps.users.rbac_catalog import sync_permissions

        sync_permissions()
        backfill_roles_from_legacy()
        bump_rbac_cache_version()

    def test_admin_role_is_system_and_assigned(self):
        admin_role = Role.objects.get(name="Administrator")
        self.assertTrue(admin_role.is_system)
        self.assertTrue(
            UserRoleAssignment.objects.filter(user=self.admin, role=admin_role).exists()
        )
        self.assertTrue(resolve_effective_permissions(self.admin).is_superuser)

    def test_ads_staff_keeps_orders_view_with_channel_scope(self):
        eff = resolve_effective_permissions(self.ads_staff)
        self.assertTrue(eff.can("orders:view"))
        self.assertEqual(eff.scope_for("orders"), DataScope.CHANNEL)

    def test_sales_staff_unrestricted_scope(self):
        eff = resolve_effective_permissions(self.sales_staff)
        self.assertTrue(eff.can("orders:view"))
        self.assertTrue(eff.can("customers:view"))
        self.assertEqual(eff.scope_for("orders"), DataScope.ALL)

    def test_deptless_staff_gets_default_role(self):
        eff = resolve_effective_permissions(self.deptless_staff)
        self.assertTrue(eff.can("orders:view"))
        self.assertTrue(eff.can("customers:view"))
        self.assertFalse(eff.can("users:view"))

    def test_backfill_is_idempotent(self):
        roles_before = Role.objects.count()
        assignments_before = UserRoleAssignment.objects.count()
        backfill_roles_from_legacy()
        self.assertEqual(Role.objects.count(), roles_before)
        self.assertEqual(UserRoleAssignment.objects.count(), assignments_before)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_rbac_backfill_migration.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.users.rbac_backfill'`.

- [ ] **Step 3: Implement backfill (hàm thuần, dùng được cả ở test lẫn migration)**

Create `backend/apps/users/rbac_backfill.py`:

```python
"""Ánh xạ trạng thái phân quyền cũ (User.role + Department.allowed_sections) sang
Role/RolePermission/RoleSectionScope/UserRoleAssignment. Idempotent.

Dùng model runtime — gọi trực tiếp trong test; migration gọi qua wrapper riêng (xem
0015) để chạy được trên model lịch sử.
"""

from django.db import transaction

from .constants import DepartmentID
from .models import (
    DataScope,
    Department,
    Permission,
    Role,
    RolePermission,
    RoleSectionScope,
    User,
    UserRole,
    UserRoleAssignment,
)

DEPTLESS_STAFF_SECTIONS = ("orders", "landing-orders", "koc-orders", "customers", "resale-tracking")
DEPTLESS_ROLE_NAME = "Nhân viên mặc định"
ADMIN_ROLE_NAME = "Administrator"


def _grant_sections(role: Role, sections) -> None:
    """Gán cho role MỌI permission của các section cho trước (theo catalog đã seed)."""
    perms = Permission.objects.filter(section__in=list(sections))
    existing = set(
        RolePermission.objects.filter(role=role).values_list("permission_id", flat=True)
    )
    RolePermission.objects.bulk_create(
        [RolePermission(role=role, permission=p) for p in perms if p.id not in existing]
    )


def _assign(user, role) -> None:
    UserRoleAssignment.objects.get_or_create(user=user, role=role)


@transaction.atomic
def backfill_roles_from_legacy() -> None:
    # 1) Administrator
    admin_role, _ = Role.objects.get_or_create(
        name=ADMIN_ROLE_NAME,
        defaults={"is_system": True, "scope_default": DataScope.ALL, "description": "Toàn quyền hệ thống"},
    )
    for admin in User.objects.filter(role=UserRole.ADMIN, deleted_at__isnull=True):
        _assign(admin, admin_role)

    # 2) Role theo từng phòng ban
    dept_role_by_id: dict[int, Role] = {}
    for dept in Department.objects.all():
        role, _ = Role.objects.get_or_create(
            name=f"Phòng: {dept.name}",
            defaults={"scope_default": DataScope.ALL, "description": f"Auto từ phòng ban {dept.name}"},
        )
        _grant_sections(role, dept.allowed_sections or [])
        if dept.id == DepartmentID.ADS:
            RoleSectionScope.objects.get_or_create(
                role=role, section="orders", defaults={"scope": DataScope.CHANNEL}
            )
        dept_role_by_id[dept.id] = role

    for staff in User.objects.filter(
        role=UserRole.STAFF, department__isnull=False, deleted_at__isnull=True
    ):
        role = dept_role_by_id.get(staff.department_id)
        if role is not None:
            _assign(staff, role)

    # 3) Role mặc định cho STAFF không phòng ban
    deptless_role, _ = Role.objects.get_or_create(
        name=DEPTLESS_ROLE_NAME,
        defaults={"scope_default": DataScope.ALL, "description": "STAFF chưa gán phòng ban"},
    )
    _grant_sections(deptless_role, DEPTLESS_STAFF_SECTIONS)
    for staff in User.objects.filter(
        role=UserRole.STAFF, department__isnull=True, deleted_at__isnull=True
    ):
        _assign(staff, deptless_role)
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_rbac_backfill_migration.py -q`
Expected: PASS (5 test).

- [ ] **Step 5: Tạo data migration gọi backfill**

Create `backend/apps/users/migrations/0015_backfill_roles_from_legacy.py`:

```python
from django.db import migrations


def run_backfill(apps, schema_editor):
    # backfill_roles_from_legacy dùng model runtime; an toàn ở đây vì schema RBAC đã
    # có từ 0013 và không đổi field nào nó dùng. Bọc trong on_commit-free atomic.
    from apps.users.rbac_backfill import backfill_roles_from_legacy

    backfill_roles_from_legacy()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0014_seed_rbac_catalog"),
    ]

    operations = [
        migrations.RunPython(run_backfill, noop_reverse),
    ]
```

- [ ] **Step 6: Migrate + đối chiếu số liệu (dry-run an toàn)**

Run:
```bash
python manage.py migrate users
python manage.py shell -c "
from apps.users.models import User, UserRoleAssignment, UserRole
total_admin = User.objects.filter(role=UserRole.ADMIN, deleted_at__isnull=True).count()
total_staff = User.objects.filter(role=UserRole.STAFF, deleted_at__isnull=True).count()
assigned = UserRoleAssignment.objects.values('user').distinct().count()
print('admin', total_admin, 'staff', total_staff, 'users_with_role', assigned)
"
```
Expected: `users_with_role` == `admin + staff` (mọi user active đều có ít nhất 1 role). Nếu lệch → kiểm tra user STAFF có `department` trỏ tới phòng ban đã xóa.

- [ ] **Step 7: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/rbac_backfill.py backend/apps/users/migrations/0015_backfill_roles_from_legacy.py backend/apps/users/tests/test_rbac_backfill_migration.py
git add backend/apps/users/rbac_backfill.py backend/apps/users/migrations/0015_backfill_roles_from_legacy.py backend/apps/users/tests/test_rbac_backfill_migration.py
git commit -m "feat(rbac): backfill roles from legacy User.role + Department.allowed_sections"
```

---

## Task 6: Bust cache khi RBAC data đổi (signals) + smoke test toàn bộ S1

**Files:**
- Modify: `backend/apps/users/rbac.py` (thêm signal handlers ở cuối file)
- Modify: `backend/apps/users/apps.py` (import signals trong `ready()`)
- Test: `backend/apps/users/tests/test_rbac_cache_invalidation.py`

- [ ] **Step 1: Viết test thất bại**

Create `backend/apps/users/tests/test_rbac_cache_invalidation.py`:

```python
from django.test import TestCase

from apps.users.models import (
    DataScope,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
    UserRoleAssignment,
)
from apps.users.rbac import resolve_effective_permissions


def _perm(code):
    section, action = code.split(":", 1)
    return Permission.objects.get_or_create(
        code=code, defaults={"section": section, "action": action}
    )[0]


class CacheInvalidationTests(TestCase):
    def test_assigning_role_busts_cache_automatically(self):
        user = User.objects.create_user(username="sig1", password="x", role=UserRole.STAFF)
        role = Role.objects.create(name="sigr", scope_default=DataScope.OWN)
        RolePermission.objects.create(role=role, permission=_perm("orders:view"))

        # Chưa gán role → không có quyền (và kết quả này được cache)
        self.assertFalse(resolve_effective_permissions(user).can("orders:view"))

        # Gán role → signal bust version → lần resolve sau thấy quyền mới
        UserRoleAssignment.objects.create(user=user, role=role)
        self.assertTrue(resolve_effective_permissions(user).can("orders:view"))

    def test_adding_role_permission_busts_cache(self):
        user = User.objects.create_user(username="sig2", password="x", role=UserRole.STAFF)
        role = Role.objects.create(name="sigr2", scope_default=DataScope.OWN)
        UserRoleAssignment.objects.create(user=user, role=role)
        self.assertFalse(resolve_effective_permissions(user).can("orders:edit"))

        RolePermission.objects.create(role=role, permission=_perm("orders:edit"))
        self.assertTrue(resolve_effective_permissions(user).can("orders:edit"))
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest apps/users/tests/test_rbac_cache_invalidation.py -q`
Expected: FAIL (lần resolve thứ 2 vẫn đọc cache cũ → assertion sai).

- [ ] **Step 3: Thêm signals vào cuối `apps/users/rbac.py`**

Append vào cuối `backend/apps/users/rbac.py`:

```python
from django.db.models.signals import m2m_changed, post_delete, post_save  # noqa: E402
from django.dispatch import receiver  # noqa: E402

from .models import (  # noqa: E402
    RoleFieldRule as _RoleFieldRule,
    RolePermission as _RolePermission,
    RoleSectionScope as _RoleSectionScope,
    UserRoleAssignment as _UserRoleAssignment,
)


def _bust(*args, **kwargs):
    bump_rbac_cache_version()


for _model in (_UserRoleAssignment, _RolePermission, _RoleSectionScope, _RoleFieldRule, Role):
    post_save.connect(_bust, sender=_model, dispatch_uid=f"rbac_bust_save_{_model.__name__}")
    post_delete.connect(_bust, sender=_model, dispatch_uid=f"rbac_bust_del_{_model.__name__}")
```

> Lưu ý: `UserChannel`/`UserDepartment` cũng cần bust nhưng `UserChannel` ở app khác (`channels_core`). S3 sẽ nối thêm signal cho `UserChannel` khi scope `channel` bắt đầu enforce. Trong S1 chưa cần.

- [ ] **Step 4: Đảm bảo signals được nạp khi app ready**

Đọc `backend/apps/users/apps.py`. Thêm method `ready()` (nếu chưa có) import `rbac` để đăng ký signal:

```python
class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.users"

    def ready(self):
        from . import rbac  # noqa: F401  đăng ký signal bust cache
```

> Nếu `apps.py` đã có `ready()`, chỉ thêm dòng import `from . import rbac`.

- [ ] **Step 5: Chạy test để xác nhận PASS**

Run: `python -m pytest apps/users/tests/test_rbac_cache_invalidation.py -q`
Expected: PASS (2 test).

- [ ] **Step 6: Chạy toàn bộ test users + smoke regression**

Run:
```bash
python -m pytest apps/users apps/orders/tests/test_channel_scope_mutation_authz.py -q
```
Expected: PASS toàn bộ — đặc biệt `test_channel_scope_mutation_authz.py` vẫn xanh (chứng minh S1 không đổi hành vi runtime).

- [ ] **Step 7: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/rbac.py backend/apps/users/apps.py backend/apps/users/tests/test_rbac_cache_invalidation.py
git add backend/apps/users/rbac.py backend/apps/users/apps.py backend/apps/users/tests/test_rbac_cache_invalidation.py
git commit -m "feat(rbac): auto-bust resolver cache on role/permission changes"
```

---

## Self-review checklist (chạy sau khi xong S1)

- [ ] Mọi section trong `PERMISSION_CATALOG` ⊆ `VALID_SECTIONS` (test `test_every_catalog_section_is_valid`).
- [ ] `resolve_effective_permissions` không gây N+1 nghiêm trọng (số query cố định ~4 truy vấn/role-set; chấp nhận được vì có cache).
- [ ] `test_channel_scope_mutation_authz.py` vẫn pass → zero-regression runtime.
- [ ] Migration `0013/0014/0015` chạy tuần tự sạch trên DB rỗng (`migrate users` từ đầu) và idempotent khi chạy lại backfill.
- [ ] Tên model/field nhất quán với các sprint sau: `UserRoleAssignment`, `EffectivePermissions.can()`, `scope_for()`, `field_policy`, `bump_rbac_cache_version()`, `resolve_effective_permissions()`.

## Bàn giao S1 → S2

S1 chỉ tạo dữ liệu + resolver. **Chưa endpoint nào enforce.** S2 sẽ: viết DRF permission class `require_permission()` (đọc `resolve_effective_permissions`), giữ `require_section` thành shim `:view`, mở rộng `/api/users/me`, dựng trang quản trị Role + ma trận action.
