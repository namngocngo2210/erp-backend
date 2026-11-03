# Cấu hình hệ thống trong DB (sysconfig) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chuyển 77 khóa credential/tham số vận hành từ `.env` sang bảng `system_settings`, sửa được trên trang "Cấu hình hệ thống" trong app, có hiệu lực ≤2s không restart; DB là nguồn duy nhất.

**Architecture:** App mới `apps.sysconfig` = registry trong code (whitelist + kiểu + default + cờ secret/staging) + bảng key-value (cột plaintext + cột mã hoá Fernet) + store in-process có version trên Redis để mọi web thread/worker nạp lại sau khi ghi. `apps.common` (tầng đáy) đọc qua hook `runtime_config` do sysconfig đăng ký lúc app ready. API superuser-only + trang FE tab theo nhóm. Lệnh `sysconfig_import_env` chuyển giá trị từ env một lần; guard boot fail-fast khi `.env` còn khóa đã chuyển.

**Tech Stack:** Django 5 + DRF, pytest-django, Redis qua `django.core.cache`, `apps.common.fields.EncryptedCharField`; React + TanStack Query + vitest.

**Spec:** `docs/superpowers/specs/2026-09-19-system-settings-db-design.md`

## Global Constraints

- **Worktree riêng, là thư mục anh em của repo** (KHÔNG nằm dưới `/home/desi/desi-order`, vì `load_dotenv()` đi ngược thư mục cha sẽ nạp `.env` prod): `git worktree add /home/desi/desi-order-sysconfig -b feat/system-settings-db master`. Mọi lệnh dưới đây chạy trong worktree này. Frontend: `ln -s /home/desi/desi-order/frontend/node_modules /home/desi/desi-order-sysconfig/frontend/node_modules` (symlink hiện `??` — không bao giờ `git add -A`).
- **Backend test** chạy bằng container test (KHÔNG chạm prod DB). Trước khi chạy: `docker start desi-test-pg desi-test-redis`. Đặt biến một lần mỗi shell:
  ```bash
  export TEST_ENV="POSTGRES_DB=desi_test_sysconfig POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass POSTGRES_HOST=localhost POSTGRES_PORT=5433 REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= APP_ENV=development DJANGO_DEBUG=0 LOG_FORMAT=text ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= ZENIFY_DOMAIN= ZENIFY_API_KEY= ZENIFY_API_SECRET="
  export PYTEST="/home/desi/desi-order/.venv/bin/pytest"
  ```
  Chạy: `cd /home/desi/desi-order-sysconfig/backend && env $TEST_ENV $PYTEST <path> -q --reuse-db`. Lần đầu (hoặc sau khi thêm migration) dùng `--create-db` thay `--reuse-db`. **Không chạy 2 lượt pytest song song** (máy là prod VPS, OOM-kill).
- **Frontend** không có node trên PATH: `docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order-sysconfig/frontend node:22-alpine npx vitest run <path>`; typecheck `... node:22-alpine npm run typecheck`; lint `... npm run lint`. Chạy tuần tự, không song song.
- Mọi khóa registry giữ **đúng tên env cũ**, viết hoa. Kiểu: `bool | int | float | str | url`.
- Secret: API/serializer **không bao giờ** trả giá trị; chỉ `is_set` + `hint` 4 ký tự cuối.
- Section FE/RBAC: `system-settings`, superuser-only, KHÔNG vào `PERMISSION_CATALOG`.
- Route FE: `/settings/system-settings`. API: `/api/system-settings/`.
- Commit message tiếng Việt, prefix `feat(sysconfig):`/`refactor(...)`/`docs:`; kết thúc bằng `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- Tầng import (`.importlinter`): `sysconfig` nằm giữa `users | channels_core` và `audit | dispatch`. `apps.common` KHÔNG import `apps.sysconfig` — dùng `apps.common.runtime_config`.

---

### Task 1: Scaffold app `apps.sysconfig` + registry core

**Files:**
- Create: `backend/apps/sysconfig/__init__.py`, `backend/apps/sysconfig/apps.py`, `backend/apps/sysconfig/registry.py`, `backend/apps/sysconfig/tests/__init__.py`, `backend/apps/sysconfig/tests/test_registry.py`
- Modify: `backend/config/settings_parts/base.py` (INSTALLED_APPS, sau `"apps.users"`), `backend/.importlinter` (layers)

**Interfaces:**
- Produces: `Setting` dataclass (`key, type, default, group, label, help="", secret=False, staging_forbidden=False`), `GROUPS: tuple[tuple[str, str], ...]`, `SETTINGS: tuple[Setting, ...]`, `BY_KEY: dict[str, Setting]`, `validate_registry(settings: Iterable[Setting]) -> None` (raise `ImproperlyConfigured`), `coerce(spec: Setting, raw: str) -> bool | int | float | str` (raise `ValueError`).

- [ ] **Step 1: Tạo worktree + scaffold app**

```bash
cd /home/desi/desi-order && git worktree add /home/desi/desi-order-sysconfig -b feat/system-settings-db master
cd /home/desi/desi-order-sysconfig && ln -s /home/desi/desi-order/frontend/node_modules frontend/node_modules
mkdir -p backend/apps/sysconfig/tests backend/apps/sysconfig/migrations backend/apps/sysconfig/management/commands backend/apps/sysconfig/transport
touch backend/apps/sysconfig/tests/__init__.py backend/apps/sysconfig/migrations/__init__.py backend/apps/sysconfig/management/__init__.py backend/apps/sysconfig/management/commands/__init__.py backend/apps/sysconfig/transport/__init__.py
```

`backend/apps/sysconfig/apps.py`:
```python
from django.apps import AppConfig


class SysconfigConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sysconfig"
    verbose_name = "Cấu hình hệ thống"

    def ready(self) -> None:
        # Kiểm tra registry lúc boot: key trùng / group lạ / default sai kiểu → chết sớm.
        from apps.sysconfig.registry import SETTINGS, validate_registry

        validate_registry(SETTINGS)
```

`backend/apps/sysconfig/__init__.py` để trống ở task này (facade `sysconfig` thêm ở Task 3).

- [ ] **Step 2: Viết test registry (fail)**

`backend/apps/sysconfig/tests/test_registry.py`:
```python
import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.sysconfig.registry import GROUPS, Setting, coerce, validate_registry


def _s(**kw) -> Setting:
    base = dict(key="X_KEY", type="str", default="", group=GROUPS[0][0], label="x")
    base.update(kw)
    return Setting(**base)


def test_validate_rejects_duplicate_key():
    with pytest.raises(ImproperlyConfigured, match="trùng"):
        validate_registry([_s(), _s()])


def test_validate_rejects_unknown_group():
    with pytest.raises(ImproperlyConfigured, match="group"):
        validate_registry([_s(group="nope")])


def test_validate_rejects_default_wrong_type():
    with pytest.raises(ImproperlyConfigured, match="default"):
        validate_registry([_s(type="int", default="80")])


def test_validate_rejects_non_str_secret():
    with pytest.raises(ImproperlyConfigured, match="secret"):
        validate_registry([_s(type="bool", default=False, secret=True)])


@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("true", True), ("YES", True), ("0", False), ("false", False), ("", False)],
)
def test_coerce_bool(raw, expected):
    assert coerce(_s(type="bool", default=False), raw) is expected


def test_coerce_bool_rejects_garbage():
    with pytest.raises(ValueError):
        coerce(_s(type="bool", default=False), "maybe")


def test_coerce_int_and_float():
    assert coerce(_s(type="int", default=0), " 80 ") == 80
    assert coerce(_s(type="float", default=0.0), "5") == 5.0
    with pytest.raises(ValueError):
        coerce(_s(type="int", default=0), "8.5")


def test_coerce_url_requires_scheme_or_empty():
    assert coerce(_s(type="url"), " https://a.b/c ") == "https://a.b/c"
    assert coerce(_s(type="url"), "") == ""
    with pytest.raises(ValueError):
        coerce(_s(type="url"), "a.b/c")


def test_coerce_str_strips():
    assert coerce(_s(), "  abc ") == "abc"
```

- [ ] **Step 3: Chạy test, xác nhận fail**

Run: `cd /home/desi/desi-order-sysconfig/backend && env $TEST_ENV $PYTEST apps/sysconfig/tests/test_registry.py -q --create-db`
Expected: FAIL `ModuleNotFoundError: apps.sysconfig.registry` (hoặc app chưa trong INSTALLED_APPS).

- [ ] **Step 4: Viết registry.py (core, chưa có danh sách khóa)**

`backend/apps/sysconfig/registry.py`:
```python
"""Registry cài đặt hệ thống — nguồn chân lý về KHÓA NÀO tồn tại.

Thêm một cài đặt mới = thêm một `Setting` vào `SETTINGS` (không migration). Giá trị
nằm ở bảng `system_settings` (apps.sysconfig.models); không có dòng → `default`.
Spec: docs/superpowers/specs/2026-09-19-system-settings-db-design.md §5.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from django.core.exceptions import ImproperlyConfigured

SettingType = Literal["bool", "int", "float", "str", "url"]

GROUPS: tuple[tuple[str, str], ...] = (
    ("lark_bots", "Lark bot"),
    ("lark_open", "Lark OpenAPI"),
    ("ai", "Gemini / AI"),
    ("call_qa", "Chấm chất lượng cuộc gọi"),
    ("zenify", "Zenify"),
    ("integrations", "Foxia / Pancake / CNV"),
    ("inbound_tokens", "Token API vào"),
    ("orders_portal", "Đơn hàng / Portal"),
)
GROUP_KEYS = frozenset(k for k, _ in GROUPS)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}
_PY_TYPE = {"bool": bool, "int": int, "float": float, "str": str, "url": str}


@dataclass(frozen=True)
class Setting:
    key: str
    type: SettingType
    default: bool | int | float | str
    group: str
    label: str
    help: str = ""
    secret: bool = False
    staging_forbidden: bool = False


def coerce(spec: Setting, raw: str) -> bool | int | float | str:
    """Ép chuỗi thô (DB/env/form) về kiểu của khóa. Sai → ValueError."""
    text = (raw or "").strip()
    if spec.type == "bool":
        low = text.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f"{spec.key}: giá trị bool không hợp lệ: {raw!r}")
    if spec.type == "int":
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{spec.key}: phải là số nguyên, nhận {raw!r}") from exc
    if spec.type == "float":
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"{spec.key}: phải là số, nhận {raw!r}") from exc
    if spec.type == "url":
        if text and not (text.startswith("http://") or text.startswith("https://")):
            raise ValueError(f"{spec.key}: URL phải bắt đầu bằng http:// hoặc https://")
        return text
    return text


def validate_registry(settings: Iterable[Setting]) -> None:
    seen: set[str] = set()
    for spec in settings:
        if spec.key in seen:
            raise ImproperlyConfigured(f"sysconfig registry: khóa trùng {spec.key}")
        seen.add(spec.key)
        if spec.group not in GROUP_KEYS:
            raise ImproperlyConfigured(f"sysconfig registry: {spec.key} có group lạ {spec.group!r}")
        expected = _PY_TYPE[spec.type]
        # bool là subclass của int — kiểm tra chặt để default=True không lọt vào khóa int.
        if type(spec.default) is not expected:
            raise ImproperlyConfigured(
                f"sysconfig registry: {spec.key} default {spec.default!r} không đúng kiểu {spec.type}"
            )
        if spec.secret and spec.type != "str":
            raise ImproperlyConfigured(f"sysconfig registry: {spec.key} secret phải là kiểu str")


# Danh sách khóa điền ở Task 5. Giữ tuple rỗng để app boot được ngay từ task này.
SETTINGS: tuple[Setting, ...] = ()
BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}
```

- [ ] **Step 5: Đăng ký app + tầng import**

`backend/config/settings_parts/base.py` — trong `INSTALLED_APPS`, ngay sau dòng `"apps.users",` thêm:
```python
    "apps.sysconfig",
```

`backend/.importlinter` — trong `layers =`, chèn dòng `    sysconfig` ngay TRÊN dòng `    users | channels_core`:
```
    audit | dispatch
    sysconfig
    users | channels_core
    common
```

- [ ] **Step 6: Chạy test, xác nhận pass**

Run: `cd /home/desi/desi-order-sysconfig/backend && env $TEST_ENV $PYTEST apps/sysconfig/tests/test_registry.py -q --reuse-db`
Expected: 10 passed.

- [ ] **Step 7: Commit**

```bash
cd /home/desi/desi-order-sysconfig && git add backend/apps/sysconfig backend/config/settings_parts/base.py backend/.importlinter
git commit -m "feat(sysconfig): scaffold app + registry core (Setting, coerce, validate)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Model `SystemSetting` + `SystemSettingChange` + migration

**Files:**
- Create: `backend/apps/sysconfig/models.py`, `backend/apps/sysconfig/migrations/0001_initial.py` (sinh bằng makemigrations), `backend/apps/sysconfig/admin.py`, `backend/apps/sysconfig/tests/test_models.py`

**Interfaces:**
- Produces: `SystemSetting(key, value, secret_value, updated_by, updated_at)` bảng `system_settings`; `SystemSettingChange(key, old_value, new_value, changed_by, changed_at)` bảng `system_setting_changes`.

- [ ] **Step 1: Test model (fail)**

`backend/apps/sysconfig/tests/test_models.py`:
```python
import pytest
from django.db import connection

from apps.sysconfig.models import SystemSetting, SystemSettingChange

pytestmark = pytest.mark.django_db


def test_secret_value_is_encrypted_at_rest():
    SystemSetting.objects.create(key="GEMINI_API_KEY", secret_value="plain-secret")
    with connection.cursor() as cur:
        cur.execute("SELECT secret_value FROM system_settings WHERE key = %s", ["GEMINI_API_KEY"])
        (stored,) = cur.fetchone()
    assert stored != "plain-secret"
    assert SystemSetting.objects.get(key="GEMINI_API_KEY").secret_value == "plain-secret"


def test_key_unique():
    SystemSetting.objects.create(key="QA_PASS_SCORE", value="80")
    with pytest.raises(Exception):
        SystemSetting.objects.create(key="QA_PASS_SCORE", value="90")


def test_change_log_orders_newest_first():
    a = SystemSettingChange.objects.create(key="K", old_value="", new_value="1")
    b = SystemSettingChange.objects.create(key="K", old_value="1", new_value="2")
    assert list(SystemSettingChange.objects.filter(key="K")) == [b, a]
```

- [ ] **Step 2: Chạy, xác nhận fail**

Run: `env $TEST_ENV $PYTEST apps/sysconfig/tests/test_models.py -q --reuse-db`
Expected: FAIL `ImportError ... models`.

- [ ] **Step 3: Viết models.py + admin.py**

`backend/apps/sysconfig/models.py`:
```python
"""Bảng key-value cài đặt hệ thống. Khóa hợp lệ do apps.sysconfig.registry quyết định.

Một khóa dùng ĐÚNG MỘT trong hai cột: `value` (plaintext, còn psql xem được) cho
khóa thường, `secret_value` (Fernet, cùng ENCRYPTION_KEY với các bảng *Connection)
cho khóa `secret=True`. Service đảm bảo cột kia luôn rỗng — caller không tự chọn.
"""

from django.conf import settings
from django.db import models

from apps.common.fields import EncryptedCharField


class SystemSetting(models.Model):
    key = models.CharField(max_length=64, unique=True)
    value = models.TextField(blank=True, default="")
    secret_value = EncryptedCharField(max_length=4096, blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "system_settings"
        verbose_name = "Cài đặt hệ thống"
        verbose_name_plural = "Cài đặt hệ thống"

    def __str__(self) -> str:
        return self.key


class SystemSettingChange(models.Model):
    """Lịch sử đổi giá trị. Secret chỉ ghi "***" (hoặc "" khi chưa đặt/xoá)."""

    key = models.CharField(max_length=64, db_index=True)
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "system_setting_changes"
        ordering = ("-changed_at", "-id")

    def __str__(self) -> str:
        return f"{self.key} @ {self.changed_at:%Y-%m-%d %H:%M}"
```

`backend/apps/sysconfig/admin.py` (chỉ tra cứu, không sửa — sửa qua trang app):
```python
from django.contrib import admin

from apps.sysconfig.models import SystemSetting, SystemSettingChange


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "has_secret", "updated_by", "updated_at")
    search_fields = ("key",)
    readonly_fields = ("key", "value", "has_secret", "updated_by", "updated_at")
    exclude = ("secret_value",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(boolean=True, description="Có secret")
    def has_secret(self, obj):
        return bool(obj.secret_value)


@admin.register(SystemSettingChange)
class SystemSettingChangeAdmin(admin.ModelAdmin):
    list_display = ("key", "old_value", "new_value", "changed_by", "changed_at")
    search_fields = ("key",)
    list_filter = ("key",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
```

- [ ] **Step 4: Sinh migration**

Run: `cd /home/desi/desi-order-sysconfig/backend && env $TEST_ENV /home/desi/desi-order/.venv/bin/python manage.py makemigrations sysconfig -n initial`
Expected: tạo `apps/sysconfig/migrations/0001_initial.py` với 2 model. Mở file kiểm tra `db_table` đúng.

- [ ] **Step 5: Chạy test, pass**

Run: `env $TEST_ENV $PYTEST apps/sysconfig/tests/test_models.py -q --create-db`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/sysconfig && git commit -m "feat(sysconfig): bảng system_settings (value + secret_value mã hoá) và lịch sử thay đổi

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Store đọc (`sysconfig.get`) + test mode + override + hook cho `apps.common`

**Files:**
- Create: `backend/apps/sysconfig/service.py`, `backend/apps/common/runtime_config.py`, `backend/apps/sysconfig/tests/test_store.py`
- Modify: `backend/apps/sysconfig/__init__.py`, `backend/apps/sysconfig/apps.py`, `backend/conftest.py`

**Interfaces:**
- Produces: `sysconfig.get(key) -> bool|int|float|str`, `sysconfig.get_many(keys) -> dict`, `sysconfig.reset()` (xoá snapshot, dùng trong test), `override_sysconfig(**values)` (context manager + decorator), `apps.common.runtime_config.get(key)` / `register_provider(fn)`.
- Ngữ nghĩa test mode: `settings.SYSCONFIG_TEST_MODE=True` (conftest bật cho mọi test) → thứ tự: `override_sysconfig` → `getattr(django.conf.settings, key)` nếu có → `default`; KHÔNG chạm DB/Redis. Test cần DB thật đặt `settings.SYSCONFIG_TEST_MODE = False` (fixture `sysconfig_db` bên dưới).

- [ ] **Step 1: Test store (fail)**

`backend/apps/sysconfig/tests/test_store.py`:
```python
import pytest
from django.core.cache import cache
from django.test import override_settings

from apps.common import runtime_config
from apps.sysconfig import registry, service
from apps.sysconfig.models import SystemSetting
from apps.sysconfig.registry import Setting
from apps.sysconfig.service import override_sysconfig, sysconfig

FAKE = (
    Setting("T_BOOL", "bool", False, "ai", "b"),
    Setting("T_INT", "int", 80, "ai", "i"),
    Setting("T_SECRET", "str", "", "ai", "s", secret=True, staging_forbidden=True),
    Setting("T_STR", "str", "dflt", "ai", "t"),
)


@pytest.fixture(autouse=True)
def _fake_registry(monkeypatch):
    monkeypatch.setattr(registry, "SETTINGS", FAKE)
    monkeypatch.setattr(registry, "BY_KEY", {s.key: s for s in FAKE})
    sysconfig.reset()
    cache.delete(service.VERSION_KEY)
    yield
    sysconfig.reset()


@pytest.fixture
def sysconfig_db(settings):
    settings.SYSCONFIG_TEST_MODE = False
    sysconfig.reset()


# ── test mode (mặc định cho toàn suite) ────────────────────────────────────────

def test_test_mode_returns_default_without_db():
    assert sysconfig.get("T_INT") == 80
    assert sysconfig.get("T_BOOL") is False


def test_test_mode_honours_override_settings():
    with override_settings(T_INT=5, T_BOOL="1"):
        assert sysconfig.get("T_INT") == 5
        assert sysconfig.get("T_BOOL") is True


def test_override_sysconfig_context_and_decorator():
    with override_sysconfig(T_STR="x"):
        assert sysconfig.get("T_STR") == "x"
    assert sysconfig.get("T_STR") == "dflt"

    @override_sysconfig(T_INT=1)
    def inner():
        return sysconfig.get("T_INT")

    assert inner() == 1


def test_unknown_key_raises():
    with pytest.raises(KeyError):
        sysconfig.get("NOPE")


# ── DB thật ────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_db_value_wins_over_default(sysconfig_db):
    SystemSetting.objects.create(key="T_INT", value="90")
    assert sysconfig.get("T_INT") == 90


@pytest.mark.django_db
def test_secret_read_from_secret_column(sysconfig_db):
    SystemSetting.objects.create(key="T_SECRET", secret_value="shh")
    assert sysconfig.get("T_SECRET") == "shh"


@pytest.mark.django_db
def test_bad_db_value_falls_back_to_default(sysconfig_db, caplog):
    SystemSetting.objects.create(key="T_INT", value="abc")
    assert sysconfig.get("T_INT") == 80
    assert "T_INT" in caplog.text


@pytest.mark.django_db
def test_snapshot_reloads_when_version_bumps(sysconfig_db, monkeypatch):
    SystemSetting.objects.create(key="T_INT", value="1")
    assert sysconfig.get("T_INT") == 1
    SystemSetting.objects.filter(key="T_INT").update(value="2")
    # Trong cửa sổ 2s không so version → vẫn 1
    assert sysconfig.get("T_INT") == 1
    # Hết cửa sổ nhưng version chưa đổi → vẫn 1
    monkeypatch.setattr(service, "_monotonic", lambda: 10_000.0)
    assert sysconfig.get("T_INT") == 1
    # Version đổi → nạp lại
    service.bump_version()
    monkeypatch.setattr(service, "_monotonic", lambda: 20_000.0)
    assert sysconfig.get("T_INT") == 2


@pytest.mark.django_db
def test_redis_failure_keeps_snapshot(sysconfig_db, monkeypatch):
    SystemSetting.objects.create(key="T_INT", value="7")
    assert sysconfig.get("T_INT") == 7

    def boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(service.cache, "get", boom)
    monkeypatch.setattr(service, "_monotonic", lambda: 10_000.0)
    assert sysconfig.get("T_INT") == 7


@pytest.mark.django_db
def test_staging_forbidden_returns_default(sysconfig_db, settings):
    SystemSetting.objects.create(key="T_SECRET", secret_value="prod-key")
    settings.IS_STAGING = True
    sysconfig.reset()
    assert sysconfig.get("T_SECRET") == ""


def test_runtime_config_hook_delegates():
    with override_sysconfig(T_STR="via-hook"):
        assert runtime_config.get("T_STR") == "via-hook"
```

- [ ] **Step 2: Chạy, fail**

Run: `env $TEST_ENV $PYTEST apps/sysconfig/tests/test_store.py -q --reuse-db`
Expected: FAIL `ImportError: cannot import name 'service'`.

- [ ] **Step 3: Viết `apps/common/runtime_config.py`**

```python
"""Cổng đọc cài đặt hệ thống cho tầng `apps.common`.

`apps.common` là tầng đáy (.importlinter) nên không được import `apps.sysconfig`.
`apps.sysconfig.apps.SysconfigConfig.ready()` đăng ký provider vào đây; các module
common (lark_open_api, ops_alerts) gọi `runtime_config.get(KEY)`.
"""

from __future__ import annotations

from typing import Any, Callable

from django.core.exceptions import ImproperlyConfigured

_provider: Callable[[str], Any] | None = None


def register_provider(fn: Callable[[str], Any]) -> None:
    global _provider
    _provider = fn


def get(key: str) -> Any:
    if _provider is None:
        raise ImproperlyConfigured("runtime_config: chưa có provider (apps.sysconfig chưa ready?)")
    return _provider(key)
```

- [ ] **Step 4: Viết `apps/sysconfig/service.py` (phần đọc)**

```python
"""Store đọc/ghi cài đặt hệ thống. Spec §5.3–5.4.

Đọc: snapshot toàn bảng trong process; mỗi ≥2s so `sysconfig:version` trên Redis,
lệch thì nạp lại. Redis lỗi → giữ snapshot cũ (fail-soft). Test mode (conftest):
không chạm DB/Redis — override_sysconfig → django settings → default.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import ContextDecorator
from typing import Any, Iterable

from django.conf import settings as django_settings
from django.core.cache import cache

from apps.sysconfig import registry
from apps.sysconfig.registry import coerce

logger = logging.getLogger(__name__)

VERSION_KEY = "sysconfig:version"
CHECK_INTERVAL_SECONDS = 2.0
_monotonic = time.monotonic  # patch được trong test


def _is_test_mode() -> bool:
    return bool(getattr(django_settings, "SYSCONFIG_TEST_MODE", False))


def _is_staging() -> bool:
    return bool(getattr(django_settings, "IS_STAGING", False))


def _read_version() -> int | None:
    """None = Redis không trả lời (lỗi/timeout) — caller giữ snapshot cũ."""
    try:
        raw = cache.get(VERSION_KEY)
    except Exception as exc:  # noqa: BLE001 — fail-soft có chủ đích
        logger.warning("sysconfig: không đọc được version từ Redis (%s)", exc)
        return None
    return int(raw or 0)


def bump_version() -> None:
    try:
        try:
            cache.incr(VERSION_KEY)
        except ValueError:  # key chưa tồn tại
            cache.set(VERSION_KEY, 1, None)
    except Exception as exc:  # noqa: BLE001
        logger.error("sysconfig: không tăng được version trên Redis (%s) — process khác sẽ lệch", exc)


def _load_from_db() -> dict[str, str]:
    from apps.sysconfig.models import SystemSetting  # tránh import model lúc nạp module

    rows: dict[str, str] = {}
    for key, value, secret_value in SystemSetting.objects.values_list("key", "value", "secret_value"):
        spec = registry.BY_KEY.get(key)
        if spec is None:
            continue
        rows[key] = secret_value if spec.secret else value
    return rows


class _Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._overrides: list[dict[str, Any]] = []
        self.reset()

    def reset(self) -> None:
        self._snapshot: dict[str, str] | None = None
        self._version = -1
        self._checked_at = -1e9

    # ── override (test + drain command) ──────────────────────────────────────
    def push_override(self, values: dict[str, Any]) -> None:
        self._overrides.append(values)

    def pop_override(self) -> None:
        self._overrides.pop()

    def _override_value(self, key: str):
        for layer in reversed(self._overrides):
            if key in layer:
                return True, layer[key]
        return False, None

    # ── đọc ──────────────────────────────────────────────────────────────────
    def _maybe_refresh(self) -> None:
        now = _monotonic()
        if self._snapshot is not None and now - self._checked_at < CHECK_INTERVAL_SECONDS:
            return
        with self._lock:
            now = _monotonic()
            if self._snapshot is not None and now - self._checked_at < CHECK_INTERVAL_SECONDS:
                return
            self._checked_at = now
            version = _read_version()
            if self._snapshot is None:
                self._snapshot = _load_from_db()
                self._version = version if version is not None else 0
                return
            if version is None or version == self._version:
                return
            self._snapshot = _load_from_db()
            self._version = version

    def get(self, key: str) -> Any:
        spec = registry.BY_KEY[key]  # KeyError = lỗi lập trình
        found, value = self._override_value(key)
        if found:
            return value if not isinstance(value, str) else coerce(spec, value)
        if _is_test_mode():
            if hasattr(django_settings, key):
                raw = getattr(django_settings, key)
                return raw if not isinstance(raw, str) else coerce(spec, raw)
            return spec.default
        if spec.staging_forbidden and _is_staging():
            return spec.default
        self._maybe_refresh()
        raw = (self._snapshot or {}).get(key)
        if raw is None:
            return spec.default
        try:
            return coerce(spec, raw)
        except ValueError as exc:
            logger.warning("sysconfig: giá trị DB của %s không hợp lệ (%s) — dùng default", key, exc)
            return spec.default

    def get_many(self, keys: Iterable[str]) -> dict[str, Any]:
        return {k: self.get(k) for k in keys}


sysconfig = _Store()


class override_sysconfig(ContextDecorator):  # noqa: N801 — dùng như override_settings
    """Ghi đè giá trị trong process hiện tại (test, lệnh drain). Không chạm DB/Redis."""

    def __init__(self, **values: Any) -> None:
        unknown = [k for k in values if k not in registry.BY_KEY]
        if unknown:
            raise KeyError(f"override_sysconfig: khóa không có trong registry: {unknown}")
        self._values = values

    def __enter__(self):
        sysconfig.push_override(self._values)
        return self

    def __exit__(self, *exc):
        sysconfig.pop_override()
        return False
```

`backend/apps/sysconfig/__init__.py`:
```python
from apps.sysconfig.service import override_sysconfig, sysconfig  # noqa: F401 — facade
```

`backend/apps/sysconfig/apps.py` — trong `ready()` thêm sau `validate_registry(SETTINGS)`:
```python
        from apps.common import runtime_config
        from apps.sysconfig.service import sysconfig

        runtime_config.register_provider(sysconfig.get)
```

`backend/conftest.py` — thêm fixture autouse ở cuối file:
```python
@pytest.fixture(autouse=True)
def _sysconfig_test_mode(settings):
    """Toàn suite đọc cài đặt hệ thống ở TEST MODE: override_sysconfig → override_settings
    → default; không chạm DB/Redis. 155 chỗ `override_settings(GEMINI_API_KEY=…)` cũ
    nhờ vậy vẫn chạy. Test cần bảng thật: đặt `settings.SYSCONFIG_TEST_MODE = False`
    rồi `sysconfig.reset()` (xem apps/sysconfig/tests/test_store.py::sysconfig_db)."""
    settings.SYSCONFIG_TEST_MODE = True
    from apps.sysconfig.service import sysconfig

    sysconfig.reset()
    yield
    sysconfig.reset()
```

- [ ] **Step 5: Chạy, pass**

Run: `env $TEST_ENV $PYTEST apps/sysconfig/tests/test_store.py -q --reuse-db`
Expected: 11 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/sysconfig backend/apps/common/runtime_config.py backend/conftest.py
git commit -m "feat(sysconfig): store đọc snapshot + version Redis, test mode, override_sysconfig, hook runtime_config cho apps.common

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Ghi (`set_many`) + lịch sử + bump version

**Files:**
- Modify: `backend/apps/sysconfig/service.py`
- Create: `backend/apps/sysconfig/tests/test_set_many.py`

**Interfaces:**
- Produces: `set_many(values: dict[str, str | None], *, user) -> None` (raise `SysconfigValidationError(errors: dict[str, str])`), `mask_for_log(spec, raw) -> str`, `hint_for(raw: str) -> str` (4 ký tự cuối).

- [ ] **Step 1: Test (fail)**

`backend/apps/sysconfig/tests/test_set_many.py`:
```python
import pytest
from django.core.cache import cache

from apps.sysconfig import registry, service
from apps.sysconfig.models import SystemSetting, SystemSettingChange
from apps.sysconfig.registry import Setting
from apps.sysconfig.service import SysconfigValidationError, set_many, sysconfig
from apps.users.models import User

FAKE = (
    Setting("T_INT", "int", 80, "ai", "i"),
    Setting("T_SECRET", "str", "", "ai", "s", secret=True),
    Setting("T_URL", "url", "", "ai", "u"),
)
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fake_registry(monkeypatch, settings):
    monkeypatch.setattr(registry, "SETTINGS", FAKE)
    monkeypatch.setattr(registry, "BY_KEY", {s.key: s for s in FAKE})
    settings.SYSCONFIG_TEST_MODE = False
    sysconfig.reset()
    cache.delete(service.VERSION_KEY)


@pytest.fixture
def admin():
    return User.objects.create_superuser("root", "root@example.com", "pw")


def test_set_plain_and_secret_use_right_columns(admin):
    set_many({"T_INT": "90", "T_SECRET": "abc123"}, user=admin)
    plain = SystemSetting.objects.get(key="T_INT")
    secret = SystemSetting.objects.get(key="T_SECRET")
    assert (plain.value, plain.secret_value) == ("90", "")
    assert (secret.value, secret.secret_value) == ("", "abc123")
    assert plain.updated_by == admin


def test_history_masks_secret(admin):
    set_many({"T_INT": "90", "T_SECRET": "abc123"}, user=admin)
    set_many({"T_INT": "95", "T_SECRET": "xyz"}, user=admin)
    plain = SystemSettingChange.objects.filter(key="T_INT").first()
    secret = SystemSettingChange.objects.filter(key="T_SECRET").first()
    assert (plain.old_value, plain.new_value) == ("90", "95")
    assert (secret.old_value, secret.new_value) == ("***", "***")
    first_secret = SystemSettingChange.objects.filter(key="T_SECRET").last()
    assert (first_secret.old_value, first_secret.new_value) == ("", "***")


def test_none_deletes_row_and_logs(admin):
    set_many({"T_SECRET": "abc"}, user=admin)
    set_many({"T_SECRET": None}, user=admin)
    assert not SystemSetting.objects.filter(key="T_SECRET").exists()
    assert SystemSettingChange.objects.filter(key="T_SECRET").first().new_value == ""


def test_validation_error_writes_nothing(admin):
    with pytest.raises(SysconfigValidationError) as exc:
        set_many({"T_INT": "90", "T_URL": "not-a-url", "NOPE": "1"}, user=admin)
    assert set(exc.value.errors) == {"T_URL", "NOPE"}
    assert not SystemSetting.objects.exists()


@pytest.mark.django_db(transaction=True)  # on_commit chỉ chạy khi có commit thật
def test_bumps_version_after_commit(admin):
    assert cache.get(service.VERSION_KEY) is None
    set_many({"T_INT": "1"}, user=admin)
    assert int(cache.get(service.VERSION_KEY)) >= 1


def test_unchanged_value_no_history(admin):
    set_many({"T_INT": "1"}, user=admin)
    set_many({"T_INT": "1"}, user=admin)
    assert SystemSettingChange.objects.filter(key="T_INT").count() == 1
```

(`transaction.on_commit` không chạy dưới `django_db` mặc định — vì thế riêng test bump version dùng `transaction=True`.)

- [ ] **Step 2: Chạy, fail**

Run: `env $TEST_ENV $PYTEST apps/sysconfig/tests/test_set_many.py -q --reuse-db`
Expected: FAIL `ImportError: set_many`.

- [ ] **Step 3: Thêm phần ghi vào service.py**

Thêm vào cuối `backend/apps/sysconfig/service.py`:
```python
from django.db import transaction  # noqa: E402 — nhóm ghi, đặt dưới cho dễ đọc


class SysconfigValidationError(Exception):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def hint_for(raw: str) -> str:
    """4 ký tự cuối để nhận diện secret đã đặt (không đủ để dùng lại)."""
    raw = raw or ""
    return f"…{raw[-4:]}" if len(raw) >= 4 else "…"


def mask_for_log(spec: registry.Setting, raw: str) -> str:
    if not raw:
        return ""
    return "***" if spec.secret else raw


def set_many(values: dict[str, str | None], *, user) -> None:
    """Ghi nhiều khóa trong MỘT transaction; lỗi bất kỳ → không ghi khóa nào.

    `None` = xoá dòng (secret: xoá; khóa thường: về default). Chuỗi = ép kiểu thử
    trước rồi lưu chuỗi đã strip.
    """
    from apps.sysconfig.models import SystemSetting, SystemSettingChange

    errors: dict[str, str] = {}
    cleaned: dict[str, str | None] = {}
    for key, raw in values.items():
        spec = registry.BY_KEY.get(key)
        if spec is None:
            errors[key] = "Khóa không có trong registry."
            continue
        if raw is None:
            cleaned[key] = None
            continue
        try:
            coerce(spec, str(raw))
        except ValueError as exc:
            errors[key] = str(exc)
            continue
        cleaned[key] = str(raw).strip()
    if errors:
        raise SysconfigValidationError(errors)

    with transaction.atomic():
        existing = {r.key: r for r in SystemSetting.objects.select_for_update().filter(key__in=cleaned)}
        changed = False
        for key, raw in cleaned.items():
            spec = registry.BY_KEY[key]
            row = existing.get(key)
            old_raw = (row.secret_value if spec.secret else row.value) if row else ""
            if raw is None:
                if row is None:
                    continue
                row.delete()
                new_raw = ""
            else:
                if row is not None and old_raw == raw:
                    continue
                if row is None:
                    row = SystemSetting(key=key)
                if spec.secret:
                    row.value, row.secret_value = "", raw
                else:
                    row.value, row.secret_value = raw, ""
                row.updated_by = user
                row.save()
                new_raw = raw
            SystemSettingChange.objects.create(
                key=key,
                old_value=mask_for_log(spec, old_raw),
                new_value=mask_for_log(spec, new_raw),
                changed_by=user,
            )
            changed = True
        if changed:
            transaction.on_commit(bump_version)
```

- [ ] **Step 4: Chạy, pass**

Run: `env $TEST_ENV $PYTEST apps/sysconfig/tests/test_set_many.py -q --reuse-db`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/sysconfig && git commit -m "feat(sysconfig): set_many ghi transaction + lịch sử (secret che ***) + bump version sau commit

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Điền registry đầy đủ (77 khóa) + test an toàn audit

**Files:**
- Modify: `backend/apps/sysconfig/registry.py` (thay `SETTINGS = ()`)
- Create: `backend/apps/sysconfig/tests/test_registry_contents.py`

**Interfaces:**
- Produces: `SETTINGS` đầy đủ theo spec §3 + 8 khóa Lark bổ sung (có consumer trong code nhưng chưa có ở `.env`): `LARK_INVENTORY_BOT_WEBHOOK_URL/SECRET/TIMEOUT_SECONDS`, `LARK_SALE_REMINDER_TIMEOUT_SECONDS`, `LARK_VAT_BOT_TIMEOUT_SECONDS`, `LARK_CHECKIN_REMINDER_CHAT_ID`, `LARK_CHECKIN_REMINDER_LINK`, `LARK_OPEN_API_TIMEOUT_SECONDS`.

- [ ] **Step 1: Test nội dung registry (fail)**

`backend/apps/sysconfig/tests/test_registry_contents.py`:
```python
from apps.audit.middleware_impl import AuditLogMiddleware
from apps.sysconfig.registry import BY_KEY, GROUPS, SETTINGS

EXPECTED_SECRET = {
    "LARK_ORDER_BOT_SECRET", "LARK_SALE_REMINDER_SECRET", "LARK_VAT_BOT_SECRET",
    "LARK_CANCEL_DISPATCH_BOT_SECRET", "LARK_OPS_ALERT_SECRET", "LARK_INVENTORY_BOT_SECRET",
    "LARK_APP_SECRET", "LARK_CHECKIN_REMINDER_APP_SECRET", "LARK_BITABLE_APP_TOKEN",
    "GEMINI_API_KEY", "CHAT_CRAWLER_DB_PASSWORD", "ZENIFY_API_KEY", "ZENIFY_API_SECRET",
    "HERMES_API_TOKEN", "DAILY_REPORT_API_TOKEN", "ORDERS_INGEST_API_TOKEN",
    "KIOTVIET_WEBHOOK_SECRET", "BEST_EXPRESS_WEBHOOK_TOKEN", "WARRANTY_PORTAL_ZALO_TOKEN",
}


def test_registry_has_expected_size_and_groups():
    # 18 lark_bots + 12 lark_open + 8 ai + 5 call_qa + 4 zenify + 11 integrations
    # + 6 inbound_tokens + 13 orders_portal
    assert len(SETTINGS) == 77
    assert {s.group for s in SETTINGS} == {k for k, _ in GROUPS}


def test_secret_flags_match_spec():
    assert {s.key for s in SETTINGS if s.secret} == EXPECTED_SECRET


def test_every_secret_key_is_redacted_by_audit_middleware():
    """Body PATCH đi qua audit middleware; tên khóa phải bị nhận diện là nhạy cảm
    để không lưu plaintext vào audit_logs.curl_command."""
    mw = AuditLogMiddleware(lambda r: None)
    for key in EXPECTED_SECRET:
        assert mw._is_sensitive_key(key), key


def test_staging_forbidden_covers_outbound_credentials():
    forbidden = {s.key for s in SETTINGS if s.staging_forbidden}
    for key in ("GEMINI_API_KEY", "ZENIFY_API_KEY", "LARK_APP_SECRET", "LARK_ORDER_BOT_WEBHOOK_URL"):
        assert key in forbidden
    # Token vào không cấm (staging cần token riêng), nhưng neutralize vẫn xoá — Task 7.
    assert "HERMES_API_TOKEN" not in forbidden


def test_defaults_match_previous_env_defaults():
    assert BY_KEY["QA_PASS_SCORE"].default == 80
    assert BY_KEY["GEMINI_MAX_RPM"].default == 8
    assert BY_KEY["WARRANTY_PORTAL_MONTHS"].default == 24
    assert BY_KEY["LARK_OPEN_BASE_URL"].default == "https://open.larksuite.com/open-apis"
    assert BY_KEY["CNV_SSO_SERVER_URI"].default == "https://id.cnv.vn"
    assert BY_KEY["FOXIA_PUSH_BASE_URL"].default == "https://desiglobal.foxia.vn/duongdankhachhang"
    assert BY_KEY["WARRANTY_QR_BASE_URL"].default == "https://delax.com.vn/sp/"
```

(Kiểm tra tên class middleware: `grep -n "^class" backend/apps/audit/middleware_impl.py` — dùng đúng tên class có `_is_sensitive_key`.)

- [ ] **Step 2: Chạy, fail**

Run: `env $TEST_ENV $PYTEST apps/sysconfig/tests/test_registry_contents.py -q --reuse-db`
Expected: FAIL `len(SETTINGS) == 77` (đang 0).

- [ ] **Step 3: Điền SETTINGS**

Thay khối cuối `registry.py` (`SETTINGS: tuple[Setting, ...] = ()` và `BY_KEY`) bằng:
```python
_S = Setting

SETTINGS: tuple[Setting, ...] = (
    # ── lark_bots ─────────────────────────────────────────────────────────────
    _S("LARK_ORDER_BOT_WEBHOOK_URL", "url", "", "lark_bots", "Bot đơn mới — Webhook URL",
       "Custom bot Lark trong group báo đơn. Trống = không gửi.", staging_forbidden=True),
    _S("LARK_ORDER_BOT_SECRET", "str", "", "lark_bots", "Bot đơn mới — Secret",
       "Sign verification secret của bot (tuỳ chọn).", secret=True, staging_forbidden=True),
    _S("LARK_ORDER_BOT_TIMEOUT_SECONDS", "float", 5.0, "lark_bots", "Bot đơn mới — Timeout (giây)"),
    _S("LARK_ORDER_BOT_MESSAGE", "str", "", "lark_bots", "Bot đơn mới — Mẫu tin",
       "Trống = mẫu mặc định trong code. Giữ đủ {channel_name} và {staff_name}."),
    _S("LARK_SALE_REMINDER_WEBHOOK_URL", "url", "", "lark_bots", "Bot nhắc CSKH — Webhook URL",
       staging_forbidden=True),
    _S("LARK_SALE_REMINDER_SECRET", "str", "", "lark_bots", "Bot nhắc CSKH — Secret",
       secret=True, staging_forbidden=True),
    _S("LARK_SALE_REMINDER_TIMEOUT_SECONDS", "float", 5.0, "lark_bots", "Bot nhắc CSKH — Timeout (giây)"),
    _S("LARK_VAT_BOT_WEBHOOK_URL", "url", "", "lark_bots", "Bot VAT — Webhook URL", staging_forbidden=True),
    _S("LARK_VAT_BOT_SECRET", "str", "", "lark_bots", "Bot VAT — Secret", secret=True, staging_forbidden=True),
    _S("LARK_VAT_BOT_TIMEOUT_SECONDS", "float", 5.0, "lark_bots", "Bot VAT — Timeout (giây)"),
    _S("LARK_CANCEL_DISPATCH_BOT_WEBHOOK_URL", "url", "", "lark_bots", "Bot huỷ xuất kho — Webhook URL",
       staging_forbidden=True),
    _S("LARK_CANCEL_DISPATCH_BOT_SECRET", "str", "", "lark_bots", "Bot huỷ xuất kho — Secret",
       secret=True, staging_forbidden=True),
    _S("LARK_CANCEL_DISPATCH_BOT_TIMEOUT_SECONDS", "float", 5.0, "lark_bots",
       "Bot huỷ xuất kho — Timeout (giây)"),
    _S("LARK_INVENTORY_BOT_WEBHOOK_URL", "url", "", "lark_bots", "Bot kho — Webhook URL", staging_forbidden=True),
    _S("LARK_INVENTORY_BOT_SECRET", "str", "", "lark_bots", "Bot kho — Secret", secret=True, staging_forbidden=True),
    _S("LARK_INVENTORY_BOT_TIMEOUT_SECONDS", "float", 5.0, "lark_bots", "Bot kho — Timeout (giây)"),
    _S("LARK_OPS_ALERT_WEBHOOK_URL", "url", "", "lark_bots", "Bot cảnh báo vận hành — Webhook URL",
       "Nhận cảnh báo token Facebook hỏng, báo cáo ads bị chặn… Trống = chỉ ghi log.",
       staging_forbidden=True),
    _S("LARK_OPS_ALERT_SECRET", "str", "", "lark_bots", "Bot cảnh báo vận hành — Secret",
       secret=True, staging_forbidden=True),
    # ── lark_open ─────────────────────────────────────────────────────────────
    _S("LARK_OPEN_BASE_URL", "url", "https://open.larksuite.com/open-apis", "lark_open", "Open API base URL"),
    _S("LARK_OPEN_API_TIMEOUT_SECONDS", "float", 15.0, "lark_open", "Open API timeout (giây)"),
    _S("LARK_APP_ID", "str", "", "lark_open", "App ID (Bitable)", staging_forbidden=True),
    _S("LARK_APP_SECRET", "str", "", "lark_open", "App Secret (Bitable)", secret=True, staging_forbidden=True),
    _S("LARK_BITABLE_APP_TOKEN", "str", "", "lark_open", "Bitable app token", secret=True, staging_forbidden=True),
    _S("LARK_BITABLE_REVENUE_TABLE_ID", "str", "", "lark_open", "Bitable — bảng doanh thu KV", staging_forbidden=True),
    _S("LARK_BITABLE_ADS_CHANNEL_TABLE_ID", "str", "", "lark_open", "Bitable — bảng ads theo kênh",
       staging_forbidden=True),
    _S("LARK_BITABLE_ADS_PRODUCT_TABLE_ID", "str", "", "lark_open", "Bitable — bảng ads theo sản phẩm",
       staging_forbidden=True),
    _S("LARK_CHECKIN_REMINDER_APP_ID", "str", "", "lark_open", "App nhắc chấm công — App ID", staging_forbidden=True),
    _S("LARK_CHECKIN_REMINDER_APP_SECRET", "str", "", "lark_open", "App nhắc chấm công — App Secret",
       secret=True, staging_forbidden=True),
    _S("LARK_CHECKIN_REMINDER_CHAT_ID", "str", "", "lark_open", "App nhắc chấm công — chat_id nhóm",
       "Lấy bằng manage.py lark_list_bot_chats.", staging_forbidden=True),
    _S("LARK_CHECKIN_REMINDER_LINK", "url", "", "lark_open", "App nhắc chấm công — link chấm công"),
    # ── ai ────────────────────────────────────────────────────────────────────
    _S("GEMINI_API_KEY", "str", "", "ai", "Gemini API key", secret=True, staging_forbidden=True),
    _S("GEMINI_TRANSCRIBE_MODEL", "str", "gemini-flash-latest", "ai", "Model transcribe"),
    _S("GEMINI_MAX_RPM", "int", 8, "ai", "Trần request/phút",
       "Trần phía mình (Google chỉ trả 429). Đã bật billing thì nâng."),
    _S("GEMINI_MAX_RPD", "int", 200, "ai", "Trần request/ngày", "Chốt chặn chi phí."),
    _S("GEMINI_EXTRACT_ENABLED", "bool", False, "ai", "Bật trích xuất từ transcript cuộc gọi"),
    _S("GEMINI_EXTRACT_MODEL", "str", "gemini-flash-latest", "ai", "Model trích xuất"),
    _S("AI_CHAT_EXTRACT_ENABLED", "bool", False, "ai", "Bật trích xuất hội thoại chat (ai_mart)"),
    _S("CHAT_CRAWLER_DB_PASSWORD", "str", "", "ai", "Mật khẩu DB crawler chat (chat_ro)",
       secret=True, staging_forbidden=True),
    # ── call_qa ───────────────────────────────────────────────────────────────
    _S("QA_SCORING_ENABLED", "bool", False, "call_qa", "Bật chấm chất lượng cuộc gọi"),
    _S("QA_SCORING_MODEL", "str", "gemini-flash-latest", "call_qa", "Model chấm"),
    _S("QA_MIN_DURATION_SECONDS", "int", 60, "call_qa", "Thời lượng tối thiểu (giây)"),
    _S("QA_MIN_TRANSCRIPT_CHARS", "int", 400, "call_qa", "Transcript tối thiểu (ký tự)"),
    _S("QA_PASS_SCORE", "int", 80, "call_qa", "Điểm đạt"),
    # ── zenify ────────────────────────────────────────────────────────────────
    _S("ZENIFY_DOMAIN", "str", "", "zenify", "Domain Zenify"),
    _S("ZENIFY_API_KEY", "str", "", "zenify", "API key", secret=True, staging_forbidden=True),
    _S("ZENIFY_API_SECRET", "str", "", "zenify", "API secret", secret=True, staging_forbidden=True),
    _S("ZENIFY_CALL_SYNC_ENABLED", "bool", False, "zenify", "Bật đồng bộ lịch sử cuộc gọi"),
    # ── integrations ──────────────────────────────────────────────────────────
    _S("FOXIA_PUSH_ENABLED", "bool", False, "integrations", "Bật đẩy đơn/cơ hội sang Foxia"),
    _S("FOXIA_PUSH_BASE_URL", "url", "https://desiglobal.foxia.vn/duongdankhachhang", "integrations",
       "Foxia push base URL"),
    _S("FOXIA_PUSH_TIMEOUT", "float", 10.0, "integrations", "Foxia push timeout (giây)"),
    _S("PANCAKE_POLL_ENABLED", "bool", False, "integrations", "Bật poll hội thoại Pancake"),
    _S("PANCAKE_HTTP_TIMEOUT", "float", 15.0, "integrations", "Pancake HTTP timeout (giây)"),
    _S("CNV_SYNC_ENABLED", "bool", False, "integrations", "Bật đồng bộ CNV Loyalty"),
    _S("CNV_SSO_SERVER_URI", "url", "https://id.cnv.vn", "integrations", "CNV SSO server"),
    _S("CNV_API_BASE_URL", "url", "https://apis.cnvloyalty.com", "integrations", "CNV API base"),
    _S("CNV_SSO_REDIRECT_URI", "url", "", "integrations", "CNV SSO redirect URI",
       "Callback công khai đã đăng ký với CNV: https://<domain>/api/cnv/callback"),
    _S("CNV_ACCOUNT_NAME", "str", "", "integrations", "CNV account name (đối soát)"),
    _S("CNV_ACCOUNT_ID", "str", "", "integrations", "CNV account id (đối soát)"),
    # ── inbound_tokens ────────────────────────────────────────────────────────
    _S("HERMES_API_TOKEN", "str", "", "inbound_tokens", "Token Hermes",
       "Trống = endpoint trả 503 (fail-closed).", secret=True),
    _S("DAILY_REPORT_API_TOKEN", "str", "", "inbound_tokens", "Token Daily Report",
       "Trống = endpoint trả 503 (fail-closed).", secret=True),
    _S("ORDERS_INGEST_API_TOKEN", "str", "", "inbound_tokens", "Token ingest đơn landing", secret=True),
    _S("KIOTVIET_WEBHOOK_SECRET", "str", "", "inbound_tokens", "Secret webhook KiotViet",
       "Bản base64 tự suy ra khi đăng ký/xác thực — không cần nhập riêng.", secret=True),
    _S("BEST_EXPRESS_WEBHOOK_TOKEN", "str", "", "inbound_tokens", "Bearer token webhook Best Express",
       secret=True),
    _S("WARRANTY_PORTAL_ZALO_TOKEN", "str", "", "inbound_tokens", "Token kênh Zalo OA kích hoạt BH",
       "Trống = chặn kênh zalo_oa.", secret=True),
    # ── orders_portal ─────────────────────────────────────────────────────────
    _S("ORDERS_LANDING_INGEST_DRY_RUN", "bool", False, "orders_portal", "Ingest landing chạy thử (không tạo đơn)"),
    _S("RECONCILE_DELIVERY_EXACT_MIRROR", "bool", False, "orders_portal", "Reconcile giao hàng: mirror chính xác"),
    _S("SALE_REMINDER_FOLLOWUP_HOUR", "int", 9, "orders_portal", "Giờ nhắc follow-up D+2/D+4/D+7"),
    _S("FB_TOKEN_EXPIRY_WARN_DAYS", "int", 7, "orders_portal", "Cảnh báo token Facebook trước (ngày)"),
    _S("WARRANTY_PORTAL_MONTHS", "int", 24, "orders_portal", "Thời hạn bảo hành (tháng)"),
    _S("WARRANTY_PORTAL_SCAN_LOG_RETENTION_DAYS", "int", 180, "orders_portal", "Giữ log quét portal (ngày)"),
    _S("WARRANTY_QR_BASE_URL", "url", "https://delax.com.vn/sp/", "orders_portal", "URL in lên tem QR",
       "Sai giá trị hỏng cả lô tem đã in."),
    _S("PORTAL_COMPANY_NAME", "str", "", "orders_portal", "Footer portal — Tên công ty"),
    _S("PORTAL_COMPANY_ADDRESS", "str", "", "orders_portal", "Footer portal — Địa chỉ"),
    _S("PORTAL_COMPANY_TAX_CODE", "str", "", "orders_portal", "Footer portal — Mã số thuế"),
    _S("PORTAL_COMPANY_HOTLINE", "str", "", "orders_portal", "Footer portal — Hotline"),
    _S("PORTAL_COMPANY_ZALO_URL", "url", "", "orders_portal", "Footer portal — Link Zalo"),
    _S("PORTAL_COMPANY_FACEBOOK_URL", "url", "", "orders_portal", "Footer portal — Link Facebook"),
)
BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}
```

Đếm: lark_bots 18, lark_open 12, ai 8, call_qa 5, zenify 4, integrations 11, inbound_tokens 6, orders_portal 13 → **77** (spec nói ~63 vì chưa tính timeout từng bot + 8 khóa Lark bổ sung). Nếu test đếm lệch, đếm lại danh sách chứ không sửa con số.

Lưu ý `WARRANTY_QR_BASE_URL`: code cũ ép trailing slash (`_with_trailing_slash`) — giữ hành vi đó tại chỗ dùng (Task 10).

- [ ] **Step 4: Chạy, pass**

Run: `env $TEST_ENV $PYTEST apps/sysconfig -q --reuse-db`
Expected: tất cả pass (registry_contents 5 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/sysconfig && git commit -m "feat(sysconfig): registry đầy đủ 77 khóa theo 8 nhóm + test secret bị audit che

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Lệnh `sysconfig_import_env`

**Files:**
- Create: `backend/apps/sysconfig/management/commands/sysconfig_import_env.py`, `backend/apps/sysconfig/tests/test_import_env_command.py`

- [ ] **Step 1: Test (fail)**

```python
import pytest
from django.core.management import call_command

from apps.sysconfig import registry
from apps.sysconfig.models import SystemSetting
from apps.sysconfig.registry import Setting
from apps.sysconfig.service import sysconfig

FAKE = (
    Setting("T_INT", "int", 80, "ai", "i"),
    Setting("T_SECRET", "str", "", "ai", "s", secret=True),
)
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fake(monkeypatch, settings):
    monkeypatch.setattr(registry, "SETTINGS", FAKE)
    monkeypatch.setattr(registry, "BY_KEY", {s.key: s for s in FAKE})
    settings.SYSCONFIG_TEST_MODE = False
    sysconfig.reset()


def test_dry_run_prints_diff_and_writes_nothing(monkeypatch, capsys):
    monkeypatch.setenv("T_INT", "90")
    monkeypatch.setenv("T_SECRET", "abcdef")
    call_command("sysconfig_import_env")
    out = capsys.readouterr().out
    assert "T_INT" in out and "90" in out
    assert "abcdef" not in out and "***" in out
    assert not SystemSetting.objects.exists()


def test_apply_creates_rows(monkeypatch):
    monkeypatch.setenv("T_INT", "90")
    monkeypatch.setenv("T_SECRET", "abcdef")
    call_command("sysconfig_import_env", "--apply")
    assert SystemSetting.objects.get(key="T_INT").value == "90"
    assert SystemSetting.objects.get(key="T_SECRET").secret_value == "abcdef"


def test_apply_skips_empty_env_and_existing_diff_without_force(monkeypatch):
    SystemSetting.objects.create(key="T_INT", value="1")
    monkeypatch.setenv("T_INT", "90")
    monkeypatch.delenv("T_SECRET", raising=False)
    call_command("sysconfig_import_env", "--apply")
    assert SystemSetting.objects.get(key="T_INT").value == "1"
    assert not SystemSetting.objects.filter(key="T_SECRET").exists()
    call_command("sysconfig_import_env", "--apply", "--force")
    assert SystemSetting.objects.get(key="T_INT").value == "90"
```

- [ ] **Step 2: Chạy, fail**

Run: `env $TEST_ENV $PYTEST apps/sysconfig/tests/test_import_env_command.py -q --reuse-db`
Expected: FAIL `CommandError: Unknown command`.

- [ ] **Step 3: Viết lệnh**

```python
"""Chuyển giá trị từ biến môi trường vào bảng system_settings (chạy MỘT lần khi deploy).

    manage.py sysconfig_import_env            # chỉ in diff
    manage.py sysconfig_import_env --apply    # ghi khóa DB chưa có
    manage.py sysconfig_import_env --apply --force   # ghi đè cả khóa DB đã có

Quy trình deploy: docs/system-settings.md. Lệnh chỉ đụng khóa có trong registry.
"""

import os

from django.core.management.base import BaseCommand

from apps.sysconfig import registry
from apps.sysconfig.models import SystemSetting
from apps.sysconfig.service import set_many


class Command(BaseCommand):
    help = "Import giá trị cài đặt hệ thống từ env vào DB (dry-run mặc định)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Ghi vào DB (mặc định chỉ in diff).")
        parser.add_argument("--force", action="store_true", help="Ghi đè khóa DB đã có giá trị khác.")

    def handle(self, *args, **options):
        existing = {
            r.key: (r.secret_value if registry.BY_KEY[r.key].secret else r.value)
            for r in SystemSetting.objects.filter(key__in=registry.BY_KEY)
        }
        to_write: dict[str, str] = {}
        self.stdout.write(f"{'KEY':45} {'ENV':>8} {'DB':>8}  HÀNH ĐỘNG")
        for spec in registry.SETTINGS:
            env_raw = (os.environ.get(spec.key) or "").strip()
            db_raw = existing.get(spec.key)
            shown_env = ("***" if spec.secret else env_raw) if env_raw else "-"
            shown_db = ("***" if spec.secret else db_raw) if db_raw else "-"
            if not env_raw:
                action = "bỏ qua (env trống)"
            elif db_raw is None:
                action = "TẠO"
                to_write[spec.key] = env_raw
            elif db_raw == env_raw:
                action = "giống nhau"
            elif options["force"]:
                action = "GHI ĐÈ (--force)"
                to_write[spec.key] = env_raw
            else:
                action = "KHÁC — giữ DB (thêm --force để ghi đè)"
            self.stdout.write(f"{spec.key:45} {shown_env[:8]:>8} {str(shown_db)[:8]:>8}  {action}")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING(f"\nDry-run: {len(to_write)} khóa sẽ ghi. Thêm --apply để ghi."))
            return
        if to_write:
            set_many(to_write, user=None)
        self.stdout.write(self.style.SUCCESS(f"\nĐã ghi {len(to_write)} khóa."))
```

- [ ] **Step 4: Chạy, pass**

Run: `env $TEST_ENV $PYTEST apps/sysconfig/tests/test_import_env_command.py -q --reuse-db`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/sysconfig && git commit -m "feat(sysconfig): lệnh sysconfig_import_env (dry-run/--apply/--force)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Guard env còn khóa registry + `staging_neutralize` dọn bảng

**Files:**
- Modify: `backend/config/settings_parts/base.py` (sau khối staging guard, ~dòng 345), `backend/apps/users/management/commands/staging_neutralize.py`
- Create: `backend/apps/sysconfig/tests/test_env_guard.py`
- Modify: `backend/apps/users/tests/test_staging_neutralize.py` (nếu có; `ls backend/apps/users/tests | grep neutral`) — nếu không có, tạo `backend/apps/sysconfig/tests/test_staging_neutralize.py`

**Interfaces:**
- Produces: `config.settings_parts.base.find_registry_env_leftovers(environ) -> list[str]` (thuần, không import Django app), `assert_no_registry_env_leftovers()`.

Guard **không** được import `apps.sysconfig.registry` lúc nạp settings (app chưa ready, và import app trong settings là cấm kỵ). Giải pháp: registry là module Python thuần (chỉ import `django.core.exceptions`) — import được lúc nạp settings. Nhưng để chắc chắn không kéo Django app registry, guard đọc **danh sách tên khóa** qua `from apps.sysconfig.registry import SETTINGS` bên trong hàm (lazy). `apps.sysconfig.registry` không import models — OK.

- [ ] **Step 1: Test (fail)**

`backend/apps/sysconfig/tests/test_env_guard.py`:
```python
import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings_parts.base import assert_no_registry_env_leftovers, find_registry_env_leftovers


def test_finds_registry_keys_present_in_env():
    env = {"GEMINI_API_KEY": "x", "QA_PASS_SCORE": "80", "POSTGRES_HOST": "db", "LARK_APP_ID": ""}
    assert find_registry_env_leftovers(env) == ["GEMINI_API_KEY", "QA_PASS_SCORE"]


def test_raises_unless_allowed():
    env = {"GEMINI_API_KEY": "x"}
    with pytest.raises(ImproperlyConfigured, match="GEMINI_API_KEY"):
        assert_no_registry_env_leftovers(env)
    # Cửa thoát trong cửa sổ chuyển đổi: chỉ warning
    assert_no_registry_env_leftovers({**env, "SYSCONFIG_ALLOW_ENV_KEYS": "1"})
```

`backend/apps/sysconfig/tests/test_staging_neutralize.py`:
```python
import pytest
from django.core.management import call_command

from apps.sysconfig.models import SystemSetting, SystemSettingChange

pytestmark = pytest.mark.django_db


def test_neutralize_deletes_forbidden_and_inbound_tokens(settings):
    settings.APP_ENV = "staging"
    settings.IS_STAGING = True
    settings.IS_PRODUCTION = False
    SystemSetting.objects.create(key="GEMINI_API_KEY", secret_value="k")
    SystemSetting.objects.create(key="HERMES_API_TOKEN", secret_value="t")
    SystemSetting.objects.create(key="QA_PASS_SCORE", value="80")
    call_command("staging_neutralize", "--yes")
    assert set(SystemSetting.objects.values_list("key", flat=True)) == {"QA_PASS_SCORE"}
    assert SystemSettingChange.objects.filter(key="GEMINI_API_KEY", new_value="").exists()
```

- [ ] **Step 2: Chạy, fail**

Run: `env $TEST_ENV $PYTEST apps/sysconfig/tests/test_env_guard.py apps/sysconfig/tests/test_staging_neutralize.py -q --reuse-db`
Expected: FAIL ImportError / còn dòng GEMINI trong DB.

- [ ] **Step 3: Thêm guard vào base.py**

Ngay sau khối `if IS_STAGING: assert_staging_outbound_disabled()`:
```python
# ── Guard registry: khóa đã chuyển vào DB (apps.sysconfig) KHÔNG được còn trong env ──
# DB là nguồn duy nhất (spec 2026-09-19 D2). Env còn mang khóa = ai đó tưởng .env
# còn tác dụng → fail-fast mọi môi trường. Cửa thoát SYSCONFIG_ALLOW_ENV_KEYS=1 chỉ
# dành cho cửa sổ deploy chuyển đổi (docs/system-settings.md §deploy), phải gỡ sau.
def find_registry_env_leftovers(environ=None) -> list[str]:
    from apps.sysconfig.registry import SETTINGS  # module thuần, không import model

    env_map = os.environ if environ is None else environ
    return [s.key for s in SETTINGS if str(env_map.get(s.key, "") or "").strip()]


def assert_no_registry_env_leftovers(environ=None) -> None:
    env_map = os.environ if environ is None else environ
    leftovers = find_registry_env_leftovers(env_map)
    if not leftovers:
        return
    message = (
        "Các khóa sau đã chuyển vào DB (Cài đặt → Cấu hình hệ thống) và phải xoá khỏi .env: "
        + ", ".join(leftovers)
    )
    if str(env_map.get("SYSCONFIG_ALLOW_ENV_KEYS", "")).strip() == "1":
        import warnings

        warnings.warn(message + " (SYSCONFIG_ALLOW_ENV_KEYS=1 — chỉ cảnh báo)", stacklevel=2)
        return
    raise ImproperlyConfigured(message)


assert_no_registry_env_leftovers()
```

Kiểm tra `ImproperlyConfigured` đã import ở đầu base.py (`grep -n "ImproperlyConfigured" config/settings_parts/base.py`).

**Lưu ý cho `$TEST_ENV`:** khối env test đặt `ZENIFY_DOMAIN=` rỗng nên không kích guard. Nếu chạy test ở máy có env prod, thêm `SYSCONFIG_ALLOW_ENV_KEYS=1` vào `$TEST_ENV`.

- [ ] **Step 4: Mở rộng staging_neutralize**

Trong `staging_neutralize.py`: thêm import
```python
from apps.sysconfig.models import SystemSetting, SystemSettingChange
from apps.sysconfig.registry import SETTINGS
```
Trong `with transaction.atomic():`, sau dòng `cron_count = ...` thêm:
```python
            wipe_keys = [
                s.key for s in SETTINGS if s.staging_forbidden or s.group == "inbound_tokens"
            ]
            wiped_rows = list(SystemSetting.objects.filter(key__in=wipe_keys))
            SystemSettingChange.objects.bulk_create(
                SystemSettingChange(key=r.key, old_value="***", new_value="", changed_by=None)
                for r in wiped_rows
            )
            sysconfig_count = len(wiped_rows)
            SystemSetting.objects.filter(key__in=wipe_keys).delete()
```
Và thêm `f"system_settings_deleted={sysconfig_count}, "` vào chuỗi báo cáo. Cập nhật docstring đầu file thêm gạch đầu dòng:
```
- SystemSetting (apps.sysconfig): xoá mọi dòng `staging_forbidden` + nhóm inbound_tokens
  (staging phải đặt token riêng); ghi SystemSettingChange changed_by=None.
```
Sau `transaction.atomic()` gọi `from apps.sysconfig.service import bump_version; bump_version()` để process staging đang chạy nạp lại.

- [ ] **Step 5: Chạy, pass**

Run: `env $TEST_ENV $PYTEST apps/sysconfig -q --reuse-db && env $TEST_ENV $PYTEST apps/common/tests/test_settings_guard.py -q --reuse-db`
Expected: pass (test_settings_guard là test guard staging cũ — vẫn phải xanh).

- [ ] **Step 6: Commit**

```bash
git add backend/config/settings_parts/base.py backend/apps/users/management/commands/staging_neutralize.py backend/apps/sysconfig
git commit -m "feat(sysconfig): guard env còn khóa registry (SYSCONFIG_ALLOW_ENV_KEYS cửa thoát) + staging_neutralize dọn system_settings

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Thay chỗ gọi — callcenter / ai_mart / daily_report (Gemini, QA, Zenify, Hermes, chat crawler)

**Files:**
- Modify: `backend/apps/callcenter/{call_history.py,exports_definitions.py,extract.py,quality_reports.py,scoring.py,services.py,tasks.py,transcribe.py}`, `backend/apps/callcenter/management/commands/{drain_transcribe.py,export_call_transcript.py,score_call_quality.py}`, `backend/apps/callcenter/transport/views/{hermes_views_impl.py,quality_views_impl.py}`, `backend/apps/ai_mart/infrastructure/{chat_extract_runner.py,tasks.py}`, `backend/apps/ai_mart/management/commands/ai_mart_chat_fdw.py`, `backend/apps/ai_mart/transport/hermes_chat_views.py`, `backend/apps/daily_report/transport/auth.py`
- Modify: `backend/config/settings_parts/base.py` — XOÁ dòng gán: `ZENIFY_DOMAIN`, `ZENIFY_API_KEY`, `ZENIFY_API_SECRET`, `ZENIFY_CALL_SYNC_ENABLED`, `GEMINI_API_KEY`, `GEMINI_TRANSCRIBE_MODEL`, `GEMINI_MAX_RPM`, `GEMINI_MAX_RPD`, `GEMINI_EXTRACT_ENABLED`, `GEMINI_EXTRACT_MODEL`, `CHAT_CRAWLER_DB_PASSWORD`, `AI_CHAT_EXTRACT_ENABLED`, `QA_SCORING_ENABLED`, `QA_SCORING_MODEL`, `QA_MIN_DURATION_SECONDS`, `QA_MIN_TRANSCRIPT_CHARS`, `QA_PASS_SCORE`, `HERMES_API_TOKEN`, `DAILY_REPORT_API_TOKEN` (giữ comment hữu ích → chuyển sang `help` registry nếu chưa có).
- Create: `backend/apps/callcenter/tests/test_sysconfig_wiring.py`

**Quy tắc thay thế (áp dụng cho Task 8–10):**
- `settings.X` / `getattr(settings, "X", default)` → `sysconfig.get("X")`; import `from apps.sysconfig import sysconfig`. Bỏ `or default` thừa vì `get()` đã trả default đúng kiểu.
- Đọc **tại thời điểm dùng**. Trước khi sửa mỗi file chạy `grep -nE "^[A-Z_]+ = (settings|getattr\(settings)" <file>` — hằng module-level đọc settings phải chuyển thành hàm.
- Nếu file chỉ dùng `settings` cho các khóa này thì xoá import `from django.conf import settings`.

- [ ] **Step 1: Test wiring (fail)**

`backend/apps/callcenter/tests/test_sysconfig_wiring.py`:
```python
"""Chứng minh các gate đọc sysconfig TẠI LÚC GỌI (đổi trên UI có tác dụng ngay)."""

from apps.sysconfig import override_sysconfig


def test_zenify_configured_follows_sysconfig():
    from apps.callcenter import services

    with override_sysconfig(ZENIFY_DOMAIN="d", ZENIFY_API_KEY="k", ZENIFY_API_SECRET="s"):
        assert services.zenify_configured() is True
    with override_sysconfig(ZENIFY_DOMAIN="", ZENIFY_API_KEY="", ZENIFY_API_SECRET=""):
        assert services.zenify_configured() is False


def test_pass_score_from_sysconfig():
    from apps.callcenter.quality_reports import current_pass_score

    with override_sysconfig(QA_PASS_SCORE="75"):
        assert current_pass_score() == 75
```

(Tên hàm `zenify_configured` / `current_pass_score`: mở `services.py:37` và `quality_reports.py:155` để xem tên hàm/biểu thức hiện có; nếu là biểu thức inline, tách thành hàm nhỏ đúng tên trên rồi dùng ở chỗ cũ.)

- [ ] **Step 2: Chạy, fail**

Run: `env $TEST_ENV $PYTEST apps/callcenter/tests/test_sysconfig_wiring.py -q --reuse-db`
Expected: FAIL (ImportError hoặc assert).

- [ ] **Step 3: Thay từng chỗ gọi**

Danh sách chính xác (từ grep 2026-09-19):
```
apps/ai_mart/infrastructure/chat_extract_runner.py:204,207,269,322
apps/ai_mart/infrastructure/tasks.py:56
apps/ai_mart/management/commands/ai_mart_chat_fdw.py:31
apps/ai_mart/transport/hermes_chat_views.py:77
apps/callcenter/call_history.py:40-41
apps/callcenter/exports_definitions.py:127
apps/callcenter/extract.py:92,96,208
apps/callcenter/management/commands/drain_transcribe.py:67,75,77
apps/callcenter/management/commands/export_call_transcript.py:219
apps/callcenter/management/commands/score_call_quality.py:101,104,112-113,124
apps/callcenter/quality_reports.py:138,155
apps/callcenter/scoring.py:122,126,251,277-278,299
apps/callcenter/services.py:37,118-120
apps/callcenter/tasks.py:35,59,61,82,84,109,111
apps/callcenter/transcribe.py:136-139,204,209,345
apps/callcenter/transport/views/hermes_views_impl.py:181
apps/callcenter/transport/views/quality_views_impl.py:566
apps/daily_report/transport/auth.py:42
```
Ví dụ mẫu — `apps/callcenter/tasks.py`:
```python
from apps.sysconfig import sysconfig
...
    if not sysconfig.get("ZENIFY_CALL_SYNC_ENABLED"):
```
`drain_transcribe.py` dòng 73–77 (override trong process command) thay bằng:
```python
        overrides = {}
        if options["rpm"]:
            overrides["GEMINI_MAX_RPM"] = int(options["rpm"])
        if options["rpd"]:
            overrides["GEMINI_MAX_RPD"] = int(options["rpd"])
        with override_sysconfig(**overrides):
            self._run(concurrency)   # phần thân còn lại của handle() bọc trong context này
```
(import `from apps.sysconfig import override_sysconfig, sysconfig`; tách phần thân sau đoạn override thành `_run(self, concurrency)` nếu chưa có.)

Xoá 19 dòng gán tương ứng trong `config/settings_parts/base.py` (và comment liền kề chỉ nói về chúng).

- [ ] **Step 4: Chạy test của các app này**

Run: `env $TEST_ENV $PYTEST apps/callcenter apps/ai_mart apps/daily_report apps/sysconfig -q --reuse-db -p no:cacheprovider`
Expected: pass. Test cũ dùng `override_settings(GEMINI_API_KEY=…)` vẫn pass nhờ test mode. Nếu một test fail vì `settings.X` không tồn tại (test đọc `settings.GEMINI_MAX_RPM` trực tiếp để assert), sửa test đó sang `sysconfig.get("GEMINI_MAX_RPM")`.

- [ ] **Step 5: Commit**

```bash
git add -A backend/apps/callcenter backend/apps/ai_mart backend/apps/daily_report backend/config/settings_parts/base.py
git commit -m "refactor(callcenter,ai_mart,daily_report): đọc Gemini/QA/Zenify/Hermes/Daily Report token từ sysconfig thay vì settings

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Thay chỗ gọi — Lark (bot, OpenAPI, Bitable, HR, ops alerts)

**Files:**
- Modify: `backend/apps/common/lark_open_api.py:50-60`, `backend/apps/common/ops_alerts.py:45-50` (dùng `runtime_config`), `backend/apps/orders/infrastructure/lark_notifier.py:56,125-195`, `backend/apps/orders/application/services/order_service_commands.py:1398,1429`, `backend/apps/inventory/subscribers.py:99`, `backend/apps/facebook_ads/infrastructure/lark_ads_report_sync.py:89-91`, `backend/apps/kiotviet/infrastructure/services/lark_revenue_sync.py:65-66`, `backend/apps/hr/infrastructure/lark_messenger.py:27-28`, `backend/apps/hr/application/services/attendance_reminder_service.py:41,74`
- Modify: `backend/config/settings_parts/base.py` — XOÁ mọi dòng gán `LARK_*` (dòng 656–683, 822–828, 836–839, 844–845 và `LARK_OPEN_API_TIMEOUT_SECONDS`, `LARK_SALE_REMINDER_TIMEOUT_SECONDS`, `LARK_VAT_BOT_TIMEOUT_SECONDS`, `LARK_INVENTORY_BOT_*`). Giữ `_STAGING_FORBIDDEN_PREFIXES = ("LARK_",)` (vẫn bắt env staging lỡ mang khóa).
- Create: `backend/apps/orders/tests/test_lark_notifier_sysconfig.py`

- [ ] **Step 1: Test (fail)**

```python
from unittest.mock import patch

from apps.orders.infrastructure import lark_notifier
from apps.sysconfig import override_sysconfig


def test_order_bot_reads_sysconfig_at_call_time():
    with override_sysconfig(
        LARK_ORDER_BOT_WEBHOOK_URL="https://lark.example/hook",
        LARK_ORDER_BOT_SECRET="s3",
        LARK_ORDER_BOT_TIMEOUT_SECONDS="7",
    ):
        cfg = lark_notifier.order_bot_config()
    assert cfg.webhook_url == "https://lark.example/hook"
    assert cfg.secret == "s3"
    assert cfg.timeout == 7.0


def test_ops_alert_uses_runtime_config():
    from apps.common import ops_alerts

    with override_sysconfig(LARK_OPS_ALERT_WEBHOOK_URL="https://lark.example/ops"):
        assert ops_alerts._webhook_url() == "https://lark.example/ops"
```

(Tên `order_bot_config` — mở `lark_notifier.py:120-130`: nếu hàm dựng config bot đơn có tên khác, dùng tên đó; nếu là inline trong `notify_new_order`, tách thành `order_bot_config() -> LarkBotConfig` trả NamedTuple có `webhook_url/secret/timeout` và dùng ở chỗ cũ.)

- [ ] **Step 2: Chạy, fail**

Run: `env $TEST_ENV $PYTEST apps/orders/tests/test_lark_notifier_sysconfig.py -q --reuse-db`

- [ ] **Step 3: Sửa `apps/common` qua runtime_config**

`apps/common/lark_open_api.py`:
```python
from apps.common import runtime_config
...
def _resolve_credentials(credentials: LarkCredentials | None) -> LarkCredentials:
    if credentials is not None:
        return credentials
    return LarkCredentials(
        app_id=str(runtime_config.get("LARK_APP_ID")),
        app_secret=str(runtime_config.get("LARK_APP_SECRET")),
    )


def base_url() -> str:
    return str(runtime_config.get("LARK_OPEN_BASE_URL")).rstrip("/")


def timeout_seconds() -> float:
    return float(runtime_config.get("LARK_OPEN_API_TIMEOUT_SECONDS") or 15)
```
`apps/common/ops_alerts.py`:
```python
from apps.common import runtime_config
...
def _webhook_url() -> str:
    return str(runtime_config.get("LARK_OPS_ALERT_WEBHOOK_URL"))


def _secret() -> str:
    return str(runtime_config.get("LARK_OPS_ALERT_SECRET"))
```
Xoá import `settings` nếu không còn dùng.

- [ ] **Step 4: Sửa các app còn lại**

Mẫu `lark_notifier.py` (5 bot: order, vat, inventory, cancel_dispatch, sale_reminder):
```python
from apps.sysconfig import sysconfig
...
        webhook_url=sysconfig.get("LARK_ORDER_BOT_WEBHOOK_URL"),
        secret=sysconfig.get("LARK_ORDER_BOT_SECRET"),
        timeout=sysconfig.get("LARK_ORDER_BOT_TIMEOUT_SECONDS"),
```
Dòng 56: `template = sysconfig.get("LARK_ORDER_BOT_MESSAGE") or DEFAULT_MESSAGE_TEMPLATE`.
`order_service_commands.py:1398,1429`, `inventory/subscribers.py:99`: `if not sysconfig.get("LARK_ORDER_BOT_WEBHOOK_URL"):` (tương tự VAT / CANCEL_DISPATCH).
`facebook_ads/lark_ads_report_sync.py:89-91`, `kiotviet/lark_revenue_sync.py:65-66`, `hr/lark_messenger.py:27-28`, `hr/attendance_reminder_service.py:41,74`: thay `str(getattr(settings, "K", "") or "").strip()` → `sysconfig.get("K")`.

Xoá các dòng `LARK_*` trong base.py.

- [ ] **Step 5: Chạy test liên quan**

Run: `env $TEST_ENV $PYTEST apps/orders/tests/test_lark_notifier_sysconfig.py apps/common apps/facebook_ads apps/hr apps/inventory/tests -q --reuse-db -k "lark or ops_alert or notifier or reminder or bitable"`
Expected: pass. Chạy thêm toàn bộ `apps/common` (test_settings_guard có case prefix LARK_ — vẫn phải pass vì guard env giữ nguyên).

- [ ] **Step 6: Commit**

```bash
git add -A backend/apps backend/config/settings_parts/base.py
git commit -m "refactor(lark): mọi bot/OpenAPI/Bitable đọc sysconfig; apps.common đi qua runtime_config

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Thay chỗ gọi còn lại + gỡ seed Best/CNV + xoá khóa chết

**Files:**
- Modify: `backend/apps/foxia/application/services/order_push_service.py:111,121,184`, `backend/apps/pancake/infrastructure/pancake_client.py:158`, `backend/apps/pancake/tasks.py:34`, `backend/apps/cnv_loyalty/{subscribers.py:39,tasks.py:48,111,157,models.py:46,49}`, `backend/apps/cnv_loyalty/infrastructure/sso_client.py:40,54,65`, `backend/apps/cnv_loyalty/transport/api_views.py:131`, `backend/apps/kiotviet/webhook_impl.py:115-135`, `backend/apps/kiotviet/management/commands/register_kiotviet_webhook.py:48,71`, `backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py:173`, `backend/apps/orders/application/services/{order_ingest_service.py:134,order_service_commands.py:1453,sale_reminder_service.py:69}`, `backend/apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py:180`, `backend/apps/orders/transport/views/order_views_impl/order_ingest_views.py:60,95`, `backend/apps/warranty/ingest_views.py:39`, `backend/apps/warranty_codes/{application/services/activation_service.py:76,application/services/product_page_service.py:79-84,147,exports_definitions.py:123,tasks.py:63,transport/portal_views.py:51,66}`, `backend/apps/facebook_ads/infrastructure/token_service.py:42`, `backend/apps/best_express/transport/webhook_views.py` (grep `BEST_EXPRESS_WEBHOOK_TOKEN`)
- Delete: `backend/apps/best_express/management/commands/best_sync_account.py`, `backend/apps/cnv_loyalty/management/commands/cnv_sync_connection.py` + test của chúng (`grep -rl "best_sync_account\|cnv_sync_connection" backend/apps`)
- Modify: `backend/config/settings_parts/base.py` — xoá gán: `KIOTVIET_WEBHOOK_SECRET`, `KIOTVIET_WEBHOOK_SECRET_B64`, `RECONCILE_DELIVERY_EXACT_MIRROR`, `ORDERS_INGEST_API_TOKEN`, `ORDERS_LANDING_INGEST_DRY_RUN`, `BEST_EXPRESS_WEBHOOK_TOKEN`, `BEST_EXPRESS_USERNAME/PASSWORD/EMS_KEY/ENVIRONMENT` (kiểm tra `grep -rn "BEST_EXPRESS_ENVIRONMENT\|BEST_EXPRESS_BASE_URL" apps` — nếu `BEST_EXPRESS_ENVIRONMENT` còn được `best_client` đọc để chọn base URL thì GIỮ nó và ghi rõ trong docs), `CNV_SYNC_ENABLED`, `CNV_SSO_SERVER_URI`, `CNV_API_BASE_URL`, `CNV_SSO_CLIENT_ID/SECRET`, `CNV_SSO_REDIRECT_URI`, `CNV_ACCOUNT_NAME/ID`, `FOXIA_PUSH_ENABLED/BASE_URL/TIMEOUT`, `PANCAKE_POLL_ENABLED`, `PANCAKE_HTTP_TIMEOUT`, `WARRANTY_QR_BASE_URL`, `WARRANTY_PORTAL_MONTHS`, `WARRANTY_PORTAL_SCAN_LOG_RETENTION_DAYS`, `WARRANTY_PORTAL_ZALO_TOKEN`, `PORTAL_COMPANY_*`, `SALE_REMINDER_FOLLOWUP_HOUR`, `FB_TOKEN_EXPIRY_WARN_DAYS`. Trong `STAGING_FORBIDDEN_OUTBOUND_ENVS` bỏ `BEST_EXPRESS_USERNAME`, `BEST_EXPRESS_PASSWORD`, `ZENIFY_API_KEY`, `ZENIFY_API_SECRET`, `GEMINI_API_KEY`, `CNV_SSO_CLIENT_SECRET`, `CHAT_CRAWLER_DB_PASSWORD` (không còn đọc env; guard registry + neutralize thay thế). Cập nhật test guard tương ứng trong `apps/common/tests/test_settings_guard*.py`.
- Create: `backend/apps/kiotviet/tests/test_webhook_secret_sysconfig.py`

- [ ] **Step 1: Test webhook secret (fail)**

```python
import base64

from apps.kiotviet import webhook_impl
from apps.sysconfig import override_sysconfig


def test_signing_secret_candidates_derive_b64_from_raw():
    with override_sysconfig(KIOTVIET_WEBHOOK_SECRET="@@@Desi-secret-01"):
        candidates, _ = webhook_impl._resolve_webhook_signing_secrets()
    assert candidates[0] == "@@@Desi-secret-01"
    assert base64.b64encode(b"@@@Desi-secret-01").decode() in candidates


def test_no_secret_means_no_candidates():
    with override_sysconfig(KIOTVIET_WEBHOOK_SECRET=""):
        candidates, _ = webhook_impl._resolve_webhook_signing_secrets()
    assert candidates == []
```

- [ ] **Step 2: Chạy, fail**

Run: `env $TEST_ENV $PYTEST apps/kiotviet/tests/test_webhook_secret_sysconfig.py -q --reuse-db`

- [ ] **Step 3: Sửa webhook_impl + register command**

`webhook_impl._resolve_webhook_signing_secrets` thay khối 115–133 bằng:
```python
    raw_secret = sysconfig.get("KIOTVIET_WEBHOOK_SECRET")
    candidates: list[str] = []
    if raw_secret:
        # Thứ tự: secret thô (một số tích hợp ký bằng bytes gốc) rồi bản base64 đã
        # đăng ký với KiotViet — giữ đúng hai ứng viên như trước khi bỏ khóa _B64.
        candidates.append(raw_secret)
        candidates.append(base64.b64encode(raw_secret.encode("utf-8")).decode("ascii"))
```
(giữ phần dedupe phía dưới; giá trị thứ hai của tuple trả về giữ như cũ — đọc phần còn lại của hàm để biết nó là gì). `register_kiotviet_webhook.py:48,71`: tính `secret_b64` cùng cách từ `sysconfig.get("KIOTVIET_WEBHOOK_SECRET")`.

- [ ] **Step 4: Thay các chỗ gọi còn lại**

Mẫu:
- `order_push_service.py:111`: `base = sysconfig.get("FOXIA_PUSH_BASE_URL").rstrip("/")`; `:121`: `timeout=sysconfig.get("FOXIA_PUSH_TIMEOUT")`; `:184`: `if not sysconfig.get("FOXIA_PUSH_ENABLED"):`.
- `cnv_loyalty/models.py:46`: `return (self.sso_server_uri or sysconfig.get("CNV_SSO_SERVER_URI")).rstrip("/")` (help_text sửa chữ "settings." → "cài đặt hệ thống").
- `warranty_codes/exports_definitions.py:123`: `base_url = sysconfig.get("WARRANTY_QR_BASE_URL"); base_url = base_url if base_url.endswith("/") else base_url + "/"` (giữ hành vi `_with_trailing_slash` cũ; xoá helper đó trong base.py nếu không còn ai dùng).
- `facebook_ads/token_service.py:42`: `return int(sysconfig.get("FB_TOKEN_EXPIRY_WARN_DAYS") or DEFAULT_WARN_DAYS)`.
- Token vào (`order_ingest_views.py`, `warranty/ingest_views.py`, `portal_views.py:66`, `best_express/transport/webhook_views.py`): `expected_token = sysconfig.get("ORDERS_INGEST_API_TOKEN")` v.v.
- `product_page_service.py:79-84,147`, `portal_views.py:51`, `activation_service.py:76`, `tasks.py:63`, `sale_reminder_service.py:69`, `order_ingest_service.py:134`, `ingest_job_tasks.py:180`, `sync_invoices_service.py:173`, `pancake_client.py:158`, `pancake/tasks.py:34`, `cnv_loyalty/*`: thay tương tự.

Xoá 2 lệnh seed + test của chúng; xoá các dòng gán trong base.py; cập nhật `STAGING_FORBIDDEN_OUTBOUND_ENVS` và test guard.

- [ ] **Step 5: Xác nhận không còn chỗ nào đọc settings cho khóa registry**

Run:
```bash
cd /home/desi/desi-order-sysconfig/backend && python3 - <<'EOF'
import re,subprocess
src=open("apps/sysconfig/registry.py").read()
keys=re.findall(r'_S\("([A-Z0-9_]+)"',src)+["KIOTVIET_WEBHOOK_SECRET_B64","BEST_EXPRESS_USERNAME","BEST_EXPRESS_PASSWORD","CNV_SSO_CLIENT_ID","CNV_SSO_CLIENT_SECRET"]
pat="|".join(keys)
out=subprocess.run(f"grep -rnE 'settings\\.({pat})\\b|getattr\\(settings, *\"({pat})\"|^({pat}) *=' --include=*.py apps config | grep -v '/tests/'",shell=True,capture_output=True,text=True).stdout
print(out or "SẠCH")
EOF
```
Expected: `SẠCH`.

- [ ] **Step 6: Chạy test các app đã sửa**

Run: `env $TEST_ENV $PYTEST apps/foxia apps/pancake apps/cnv_loyalty apps/kiotviet/tests/test_webhook_secret_sysconfig.py apps/warranty_codes apps/warranty apps/best_express apps/common apps/sysconfig -q --reuse-db`
Rồi: `env $TEST_ENV $PYTEST apps/orders -q --reuse-db` (nhớ: 21 test `test_price_book_service.py` fail sẵn dưới `--reuse-db` — so với master nếu nghi ngờ).
Expected: không có fail mới.

- [ ] **Step 7: Commit**

```bash
git add -A backend/apps backend/config/settings_parts/base.py
git commit -m "refactor: foxia/pancake/cnv/kiotviet webhook/ingest/warranty/portal đọc sysconfig; gỡ seed Best/CNV từ env; xoá khóa chết

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: API `/api/system-settings/` + section RBAC

**Files:**
- Create: `backend/apps/sysconfig/transport/views.py`, `backend/apps/sysconfig/transport/urls.py`, `backend/apps/sysconfig/tests/test_api.py`
- Modify: `backend/config/urls.py` (thêm `re_path(r"^api/system-settings/", include("apps.sysconfig.transport.urls"))` cạnh dòng pancake), `backend/apps/users/models.py` (`VALID_SECTIONS` thêm `"system-settings"` sau `"pancake-leads"`)

**Interfaces:**
- Produces: `GET /api/system-settings/` → `{groups:[{key,label,settings:[SettingOut]}]}`; `PATCH /api/system-settings/` body `{values:{KEY: string|null}}` → 200 payload GET | 400 `{errors:{KEY:msg}}`; `GET /api/system-settings/history/?key=&limit=` → list.
- `SettingOut = {key,label,help,type,is_secret,is_set,value,hint,default,updated_at,updated_by:{id,name}|null}`.

- [ ] **Step 1: Test API (fail)**

`backend/apps/sysconfig/tests/test_api.py`:
```python
import pytest
from rest_framework.test import APIClient

from apps.sysconfig.models import SystemSetting, SystemSettingChange
from apps.sysconfig.service import sysconfig
from apps.users.models import User

pytestmark = pytest.mark.django_db
URL = "/api/system-settings/"


@pytest.fixture(autouse=True)
def _db_mode(settings):
    settings.SYSCONFIG_TEST_MODE = False
    sysconfig.reset()


@pytest.fixture
def admin_client():
    user = User.objects.create_superuser("root", "root@example.com", "pw")
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def staff_client():
    user = User.objects.create_user("staff", "s@example.com", "pw")
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_non_superuser_forbidden(staff_client):
    assert staff_client.get(URL).status_code == 403
    assert staff_client.patch(URL, {"values": {}}, format="json").status_code == 403


def test_get_groups_and_masks_secret(admin_client):
    SystemSetting.objects.create(key="GEMINI_API_KEY", secret_value="abcdWXYZ")
    SystemSetting.objects.create(key="QA_PASS_SCORE", value="75")
    body = admin_client.get(URL).json()
    groups = {g["key"]: g for g in body["groups"]}
    assert list(groups) == ["lark_bots", "lark_open", "ai", "call_qa", "zenify", "integrations", "inbound_tokens", "orders_portal"]
    by_key = {s["key"]: s for g in body["groups"] for s in g["settings"]}
    gem = by_key["GEMINI_API_KEY"]
    assert gem["is_secret"] and gem["is_set"] and gem["value"] is None and gem["hint"] == "…WXYZ"
    assert "abcdWXYZ" not in str(body)
    assert by_key["QA_PASS_SCORE"]["value"] == "75"
    assert by_key["QA_MIN_DURATION_SECONDS"]["value"] is None
    assert by_key["QA_MIN_DURATION_SECONDS"]["default"] == 60


def test_patch_writes_many_and_returns_get_payload(admin_client):
    r = admin_client.patch(URL, {"values": {"QA_PASS_SCORE": "70", "GEMINI_API_KEY": "new-key-1234"}}, format="json")
    assert r.status_code == 200
    assert SystemSetting.objects.get(key="QA_PASS_SCORE").value == "70"
    assert SystemSetting.objects.get(key="GEMINI_API_KEY").secret_value == "new-key-1234"
    by_key = {s["key"]: s for g in r.json()["groups"] for s in g["settings"]}
    assert by_key["GEMINI_API_KEY"]["hint"] == "…1234"
    assert by_key["QA_PASS_SCORE"]["updated_by"]["name"]


def test_patch_null_deletes(admin_client):
    SystemSetting.objects.create(key="GEMINI_API_KEY", secret_value="x")
    assert admin_client.patch(URL, {"values": {"GEMINI_API_KEY": None}}, format="json").status_code == 200
    assert not SystemSetting.objects.filter(key="GEMINI_API_KEY").exists()


def test_patch_invalid_returns_400_and_writes_nothing(admin_client):
    r = admin_client.patch(URL, {"values": {"QA_PASS_SCORE": "abc", "NOPE": "1", "GEMINI_MAX_RPM": "9"}}, format="json")
    assert r.status_code == 400
    assert set(r.json()["errors"]) == {"QA_PASS_SCORE", "NOPE"}
    assert not SystemSetting.objects.exists()


def test_history(admin_client):
    admin_client.patch(URL, {"values": {"QA_PASS_SCORE": "70"}}, format="json")
    admin_client.patch(URL, {"values": {"QA_PASS_SCORE": "71"}}, format="json")
    r = admin_client.get(URL + "history/", {"key": "QA_PASS_SCORE"})
    assert r.status_code == 200
    rows = r.json()
    assert [x["new_value"] for x in rows] == ["71", "70"]
    assert rows[0]["changed_by"]["name"]
    assert SystemSettingChange.objects.count() == 2
```

- [ ] **Step 2: Chạy, fail**

Run: `env $TEST_ENV $PYTEST apps/sysconfig/tests/test_api.py -q --reuse-db`
Expected: 404.

- [ ] **Step 3: Viết views + urls**

`backend/apps/sysconfig/transport/views.py`:
```python
"""API Cấu hình hệ thống — CHỈ superuser (cầm API key Gemini/Zenify/Lark + token
mọi API vào). Không endpoint nào trả giá trị secret ra ngoài."""

from __future__ import annotations

from functools import wraps

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.sysconfig import registry
from apps.sysconfig.models import SystemSetting, SystemSettingChange
from apps.sysconfig.service import SysconfigValidationError, hint_for, set_many
from apps.users.rbac import resolve_effective_permissions

User = get_user_model()
HISTORY_MAX = 200


def superuser_only(view):
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not resolve_effective_permissions(request.user).is_superuser:
            return Response({"detail": "Chỉ quản trị viên."}, status=status.HTTP_403_FORBIDDEN)
        return view(request, *args, **kwargs)

    return _wrapped


def _user_out(user) -> dict | None:
    if user is None:
        return None
    name = (getattr(user, "display_name", "") or user.get_full_name() or user.get_username()).strip()
    return {"id": user.pk, "name": name}


def _payload() -> dict:
    rows = {r.key: r for r in SystemSetting.objects.select_related("updated_by").filter(key__in=registry.BY_KEY)}
    groups = []
    for gkey, glabel in registry.GROUPS:
        items = []
        for spec in registry.SETTINGS:
            if spec.group != gkey:
                continue
            row = rows.get(spec.key)
            raw = (row.secret_value if spec.secret else row.value) if row else None
            items.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "help": spec.help,
                    "type": spec.type,
                    "is_secret": spec.secret,
                    "is_set": bool(raw),
                    "value": None if (spec.secret or raw is None) else raw,
                    "hint": hint_for(raw) if (spec.secret and raw) else None,
                    "default": spec.default,
                    "updated_at": row.updated_at.isoformat() if row else None,
                    "updated_by": _user_out(row.updated_by) if row else None,
                }
            )
        groups.append({"key": gkey, "label": glabel, "settings": items})
    return {"groups": groups}


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
@superuser_only
def system_settings(request):
    if request.method == "GET":
        return Response(_payload())
    values = request.data.get("values")
    if not isinstance(values, dict):
        return Response({"errors": {"values": "Phải là object {KEY: value}."}}, status=status.HTTP_400_BAD_REQUEST)
    try:
        set_many({str(k): (None if v is None else str(v)) for k, v in values.items()}, user=request.user)
    except SysconfigValidationError as exc:
        return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)
    return Response(_payload())


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@superuser_only
def system_settings_history(request):
    key = (request.query_params.get("key") or "").strip()
    if key not in registry.BY_KEY:
        return Response({"detail": "key không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        limit = min(max(int(request.query_params.get("limit", 50)), 1), HISTORY_MAX)
    except ValueError:
        limit = 50
    qs = SystemSettingChange.objects.select_related("changed_by").filter(key=key)[:limit]
    return Response(
        [
            {
                "key": c.key,
                "old_value": c.old_value,
                "new_value": c.new_value,
                "changed_by": _user_out(c.changed_by),
                "changed_at": c.changed_at.isoformat(),
            }
            for c in qs
        ]
    )
```

`backend/apps/sysconfig/transport/urls.py`:
```python
from django.urls import path

from apps.sysconfig.transport.views import system_settings, system_settings_history

urlpatterns = [
    path("", system_settings, name="system-settings"),
    path("history/", system_settings_history, name="system-settings-history"),
]
```

`config/urls.py`: thêm `re_path(r"^api/system-settings/", include("apps.sysconfig.transport.urls")),` ngay sau dòng pancake.

`apps/users/models.py` `VALID_SECTIONS`: sau `"pancake-leads",` thêm:
```python
    # Cài đặt → Cấu hình hệ thống (apps.sysconfig) — superuser-only, CỐ Ý không có
    # trong PERMISSION_CATALOG (cùng khuôn pancake/foxia-link). Phải có ở đây để
    # superuser không bị FE ẩn menu.
    "system-settings",
```

Nếu có test liệt kê VALID_SECTIONS/`allowed_sections` phía backend (`grep -rn "pancake-leads" apps/users/tests`), thêm `"system-settings"` vào đó.

- [ ] **Step 4: Chạy, pass**

Run: `env $TEST_ENV $PYTEST apps/sysconfig apps/users -q --reuse-db`
Expected: pass (2 test users fail sẵn trên master: `test_system_role_short_circuits_to_full`, `test_cannot_edit_system_role` — bỏ qua nếu vẫn đúng 2 cái đó).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/sysconfig backend/config/urls.py backend/apps/users
git commit -m "feat(sysconfig): API /api/system-settings (GET/PATCH/history) superuser-only + section system-settings

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Frontend — trang Cấu hình hệ thống

**Files:**
- Create: `frontend/src/lib/api/systemSettings.ts`, `frontend/src/features/system-settings/queries.ts`, `frontend/src/features/system-settings/pages/SystemSettingsPage.tsx`, `frontend/src/features/system-settings/__tests__/SystemSettingsPage.test.tsx`, `frontend/src/i18n/messages/systemSettings.ts`
- Modify: `frontend/src/lib/routes.ts` (type `AppSection` + `SECTION_PATHS`), `frontend/src/app/modules.ts` (section trong module `settings`, sau `pancake`), `frontend/src/app/nav-layout.ts` (zone `settings` thêm `settings: ["system-settings"]`), `frontend/src/app/routed-pages.tsx`, `frontend/src/app/routes.tsx` (route `settings/system-settings` bọc `GuardSuperuser`), `frontend/src/i18n/messages.ts` (import + spread vi/en), `frontend/src/i18n/messages/topnav.ts` (`topnav.tabs.systemSettings` vi "Cấu hình hệ thống" / en "System settings"), `frontend/src/app/__tests__/modules.test.ts` (`ALL_SECTIONS` thêm `"system-settings"`)

- [ ] **Step 1: API client + queries**

`frontend/src/lib/api/systemSettings.ts`:
```ts
import { fetchJson } from "@/services/http";

/** Cấu hình hệ thống (apps.sysconfig). Secret không bao giờ có `value` — chỉ is_set + hint. */
export type SettingType = "bool" | "int" | "float" | "str" | "url";

export type SystemSettingOut = {
  key: string;
  label: string;
  help: string;
  type: SettingType;
  is_secret: boolean;
  is_set: boolean;
  value: string | null;
  hint: string | null;
  default: boolean | number | string;
  updated_at: string | null;
  updated_by: { id: number; name: string } | null;
};

export type SystemSettingsGroup = { key: string; label: string; settings: SystemSettingOut[] };
export type SystemSettingsResponse = { groups: SystemSettingsGroup[] };
export type SystemSettingChange = {
  key: string;
  old_value: string;
  new_value: string;
  changed_by: { id: number; name: string } | null;
  changed_at: string;
};

const BASE = "/api/system-settings/";

export async function getSystemSettings(): Promise<SystemSettingsResponse> {
  return (await fetchJson(BASE)) as SystemSettingsResponse;
}

/** `null` = xoá (secret) / về mặc định (khóa thường). Chỉ gửi khóa đã đổi. */
export async function updateSystemSettings(
  values: Record<string, string | null>,
): Promise<SystemSettingsResponse> {
  return (await fetchJson(BASE, { method: "PATCH", body: JSON.stringify({ values }) })) as SystemSettingsResponse;
}

export async function getSystemSettingHistory(key: string): Promise<SystemSettingChange[]> {
  return (await fetchJson(`${BASE}history/?key=${encodeURIComponent(key)}&limit=50`)) as SystemSettingChange[];
}
```

Kiểm tra `fetchJson` với 400: xem `src/services/http.ts` phần xử lý `!response.ok` — nếu nó throw `Error` với message từ `detail`, cần lấy `errors` từ body. Mở hàm và nếu lỗi ném là object có `body`/`data`, dùng nó; nếu chỉ có message, sửa `updateSystemSettings` thành gọi `fetch` qua `fetchJson` nhưng bắt lỗi và parse: đơn giản nhất — trong page, bắt `error` và đọc `(error as { errors?: Record<string,string> }).errors`. Xác định cách http.ts gắn body lỗi trước khi viết page (grep `throw` trong http.ts).

`frontend/src/features/system-settings/queries.ts`:
```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getSystemSettingHistory, getSystemSettings, updateSystemSettings } from "@/lib/api/systemSettings";

export const systemSettingsKeys = {
  all: ["system-settings"] as const,
  history: (key: string) => ["system-settings", "history", key] as const,
};

export function useSystemSettings() {
  return useQuery({ queryKey: systemSettingsKeys.all, queryFn: getSystemSettings });
}

export function useUpdateSystemSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: updateSystemSettings,
    onSuccess: (data) => {
      qc.setQueryData(systemSettingsKeys.all, data);
      void qc.invalidateQueries({ queryKey: ["system-settings", "history"] });
    },
  });
}

export function useSystemSettingHistory(key: string | null) {
  return useQuery({
    queryKey: systemSettingsKeys.history(key ?? ""),
    queryFn: () => getSystemSettingHistory(key as string),
    enabled: Boolean(key),
  });
}
```

- [ ] **Step 2: i18n + đăng ký section/route**

`frontend/src/i18n/messages/systemSettings.ts`:
```ts
export const systemSettings = {
  vi: {
    "systemSettings.title": "Cấu hình hệ thống",
    "systemSettings.description":
      "Credential đối tác và tham số vận hành đọc từ DB, có hiệu lực trong vài giây, không cần deploy. Secret chỉ hiện 4 ký tự cuối.",
    "systemSettings.save": "Lưu",
    "systemSettings.saved": "Đã lưu cấu hình",
    "systemSettings.saveFailed": "Lưu thất bại",
    "systemSettings.secret.set": "Đã đặt ({hint})",
    "systemSettings.secret.unset": "Chưa đặt",
    "systemSettings.secret.placeholder": "Nhập giá trị mới để thay",
    "systemSettings.secret.clear": "Xoá",
    "systemSettings.secret.willClear": "Sẽ xoá khi Lưu",
    "systemSettings.secret.undo": "Hoàn tác",
    "systemSettings.default": "Mặc định: {value}",
    "systemSettings.lastEdited": "Sửa lần cuối: {user}, {time}",
    "systemSettings.history": "Lịch sử",
    "systemSettings.history.empty": "Chưa có thay đổi",
    "systemSettings.unsavedPrompt": "Tab này có thay đổi chưa lưu. Bỏ qua?",
    "systemSettings.loadFailed": "Không tải được cấu hình",
  },
  en: {
    "systemSettings.title": "System settings",
    "systemSettings.description":
      "Partner credentials and runtime parameters stored in DB; changes apply within seconds, no deploy. Secrets show only their last 4 characters.",
    "systemSettings.save": "Save",
    "systemSettings.saved": "Settings saved",
    "systemSettings.saveFailed": "Save failed",
    "systemSettings.secret.set": "Set ({hint})",
    "systemSettings.secret.unset": "Not set",
    "systemSettings.secret.placeholder": "Enter a new value to replace",
    "systemSettings.secret.clear": "Clear",
    "systemSettings.secret.willClear": "Will be cleared on Save",
    "systemSettings.secret.undo": "Undo",
    "systemSettings.default": "Default: {value}",
    "systemSettings.lastEdited": "Last edited: {user}, {time}",
    "systemSettings.history": "History",
    "systemSettings.history.empty": "No changes yet",
    "systemSettings.unsavedPrompt": "This tab has unsaved changes. Discard?",
    "systemSettings.loadFailed": "Could not load settings",
  },
} as const;
```
`messages.ts`: `import { systemSettings } from "./messages/systemSettings";` + `...systemSettings.vi,` / `...systemSettings.en,` cạnh pancake. `topnav.ts`: thêm `"topnav.tabs.systemSettings"` vi/en.

`routes.ts`: thêm `| "system-settings"` vào union sau `"pancake-leads"`, và `"system-settings": "/settings/system-settings",` vào map paths.
`modules.ts` module `settings`, sau dòng pancake: `{ key: "system-settings", labelKey: "topnav.tabs.systemSettings", icon: SlidersHorizontal },` (import `SlidersHorizontal` từ lucide-react).
`nav-layout.ts` zone `settings`: thêm `settings: ["system-settings"],`.
`routed-pages.tsx`: `const SystemSettingsPage = lazy(() => import("@/features/system-settings/pages/SystemSettingsPage"));` + `export function SystemSettingsRoute() { return <SystemSettingsPage />; }`.
`routes.tsx`: sau block `settings/pancake`:
```tsx
      {
        // Cầm API key/secret đối tác + token API vào — superuser như pancake/foxia-link.
        path: "settings/system-settings",
        element: (
          <GuardSuperuser>
            <SystemSettingsRoute />
          </GuardSuperuser>
        ),
      },
```
`modules.test.ts` `ALL_SECTIONS`: thêm `"system-settings"` sau `"pancake"`.

- [ ] **Step 3: Test trang (fail)**

`frontend/src/features/system-settings/__tests__/SystemSettingsPage.test.tsx`:
```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/systemSettings", () => ({
  getSystemSettings: vi.fn(),
  updateSystemSettings: vi.fn(),
  getSystemSettingHistory: vi.fn(async () => []),
}));
vi.mock("sonner", async () => (await import("@/test/utils")).makeToastMock());

import * as api from "@/lib/api/systemSettings";
import SystemSettingsPage from "@/features/system-settings/pages/SystemSettingsPage";
import { I18nProvider } from "@/i18n";
import { renderWithClient } from "@/test/utils";

const RESPONSE = {
  groups: [
    {
      key: "ai",
      label: "Gemini / AI",
      settings: [
        { key: "GEMINI_API_KEY", label: "Gemini API key", help: "", type: "str", is_secret: true, is_set: true, value: null, hint: "…WXYZ", default: "", updated_at: "2026-09-19T01:00:00Z", updated_by: { id: 1, name: "Hung" } },
        { key: "GEMINI_MAX_RPM", label: "Trần request/phút", help: "Trần phía mình", type: "int", is_secret: false, is_set: true, value: "60", hint: null, default: 8, updated_at: null, updated_by: null },
        { key: "GEMINI_EXTRACT_ENABLED", label: "Bật trích xuất", help: "", type: "bool", is_secret: false, is_set: false, value: null, hint: null, default: false, updated_at: null, updated_by: null },
      ],
    },
    { key: "zenify", label: "Zenify", settings: [] },
  ],
};

function renderPage() {
  return renderWithClient(
    <I18nProvider>
      <SystemSettingsPage />
    </I18nProvider>,
  );
}

describe("SystemSettingsPage", () => {
  beforeEach(() => {
    vi.mocked(api.getSystemSettings).mockResolvedValue(RESPONSE as never);
    vi.mocked(api.updateSystemSettings).mockResolvedValue(RESPONSE as never);
  });

  it("hiện nhóm, secret chỉ hiện hint, nút Lưu disabled khi chưa đổi", async () => {
    renderPage();
    expect(await screen.findByText("Gemini / AI")).toBeInTheDocument();
    expect(screen.getByText("Đã đặt (…WXYZ)")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("abcdWXYZ")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lưu" })).toBeDisabled();
  });

  it("chỉ gửi khóa đã đổi; secret gõ mới thì gửi, bấm Xoá thì gửi null", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Gemini / AI");
    const rpm = screen.getByLabelText("Trần request/phút");
    await user.clear(rpm);
    await user.type(rpm, "90");
    await user.click(screen.getByRole("button", { name: "Xoá" }));
    await user.click(screen.getByRole("button", { name: "Lưu" }));
    await waitFor(() =>
      expect(api.updateSystemSettings).toHaveBeenCalledWith({ GEMINI_MAX_RPM: "90", GEMINI_API_KEY: null }),
    );
  });

  it("switch bool gửi '1'/'0'", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Gemini / AI");
    await user.click(screen.getByRole("switch", { name: "Bật trích xuất" }));
    await user.click(screen.getByRole("button", { name: "Lưu" }));
    await waitFor(() => expect(api.updateSystemSettings).toHaveBeenCalledWith({ GEMINI_EXTRACT_ENABLED: "1" }));
  });

  it("lỗi 400 gắn vào đúng ô", async () => {
    const user = userEvent.setup();
    vi.mocked(api.updateSystemSettings).mockRejectedValue(
      Object.assign(new Error("400"), { errors: { GEMINI_MAX_RPM: "phải là số nguyên" } }),
    );
    renderPage();
    await screen.findByText("Gemini / AI");
    const rpm = screen.getByLabelText("Trần request/phút");
    await user.clear(rpm);
    await user.type(rpm, "x");
    await user.click(screen.getByRole("button", { name: "Lưu" }));
    expect(await screen.findByText("phải là số nguyên")).toBeInTheDocument();
  });
});
```

(Kiểm tra `Switch` component có `role="switch"` và nhận `aria-label`/`id` để `getByRole(..., {name})` hoạt động — xem `src/components/ui/switch.tsx`. Kiểm tra cách `fetchJson` ném lỗi 400 để mock khớp: nếu http.ts ném `Error` với `.data`/`.body`, đổi `errors` thành field đúng trong cả mock và page.)

- [ ] **Step 4: Chạy, fail**

Run: `docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order-sysconfig/frontend node:22-alpine npx vitest run src/features/system-settings`
Expected: FAIL (module page chưa có).

- [ ] **Step 5: Viết page**

`frontend/src/features/system-settings/pages/SystemSettingsPage.tsx`:
```tsx
import { useEffect, useMemo, useState } from "react";
import { History } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import { useI18n } from "@/i18n";
import type { SystemSettingOut, SystemSettingsGroup } from "@/lib/api/systemSettings";
import { formatDateTime } from "@/lib/utils";
import {
  useSystemSettingHistory,
  useSystemSettings,
  useUpdateSystemSettings,
} from "@/features/system-settings/queries";

/**
 * Cài đặt → Cấu hình hệ thống (superuser-only). Tab dọc trái = nhóm registry; mỗi
 * tab một form, Lưu chỉ gửi khóa đã đổi. Secret: không bao giờ hiện giá trị —
 * gõ = thay, Xoá = gửi null. Bool gửi "1"/"0" (BE ép kiểu theo registry).
 */

/** Trạng thái chỉnh sửa: chuỗi = giá trị mới; null = xoá/về mặc định. */
type Draft = Record<string, string | null>;

function initialValue(s: SystemSettingOut): string {
  if (s.is_secret) return "";
  if (s.value !== null) return s.value;
  if (s.type === "bool") return s.default ? "1" : "0";
  return String(s.default);
}

function currentValue(s: SystemSettingOut, draft: Draft): string {
  const d = draft[s.key];
  return d === undefined || d === null ? initialValue(s) : d;
}

export default function SystemSettingsPage() {
  const { t } = useI18n();
  const query = useSystemSettings();
  const mutation = useUpdateSystemSettings();
  const groups = query.data?.groups ?? [];

  const [active, setActive] = useState<string>("");
  const [draft, setDraft] = useState<Draft>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [historyKey, setHistoryKey] = useState<string | null>(null);
  const historyQuery = useSystemSettingHistory(historyKey);

  useEffect(() => {
    if (!active && groups.length > 0) setActive(groups[0].key);
  }, [active, groups]);

  const group: SystemSettingsGroup | undefined = useMemo(
    () => groups.find((g) => g.key === active),
    [groups, active],
  );
  const dirty = Object.keys(draft).length > 0;

  const switchTab = (key: string) => {
    if (key === active) return;
    if (dirty && !window.confirm(t("systemSettings.unsavedPrompt"))) return;
    setDraft({});
    setErrors({});
    setActive(key);
  };

  const setValue = (s: SystemSettingOut, value: string | null) => {
    setDraft((prev) => {
      const next = { ...prev };
      // Trở về giá trị ban đầu → bỏ khỏi draft để nút Lưu tự disable.
      if (value !== null && !s.is_secret && value === initialValue(s)) delete next[s.key];
      else next[s.key] = value;
      return next;
    });
    setErrors((prev) => {
      const { [s.key]: _omit, ...rest } = prev;
      return rest;
    });
  };

  const handleSave = async () => {
    try {
      await mutation.mutateAsync(draft);
      setDraft({});
      setErrors({});
      toast.success(t("systemSettings.saved"));
    } catch (error) {
      const fieldErrors = (error as { errors?: Record<string, string> })?.errors;
      if (fieldErrors) setErrors(fieldErrors);
      else toast.error((error as Error)?.message || t("systemSettings.saveFailed"));
    }
  };

  const renderControl = (s: SystemSettingOut) => {
    const value = currentValue(s, draft);
    if (s.is_secret) {
      const clearing = draft[s.key] === null;
      return (
        <div className="flex flex-wrap items-center gap-2">
          <Input
            id={s.key}
            type="password"
            autoComplete="new-password"
            placeholder={t("systemSettings.secret.placeholder")}
            value={clearing ? "" : value}
            disabled={clearing}
            onChange={(e) => setValue(s, e.target.value === "" ? null : e.target.value)}
            className="max-w-md"
          />
          {clearing ? (
            <>
              <Badge variant="destructive">{t("systemSettings.secret.willClear")}</Badge>
              <Button type="button" variant="ghost" size="sm" onClick={() => setValue(s, "")}>
                {t("systemSettings.secret.undo")}
              </Button>
            </>
          ) : s.is_set ? (
            <>
              <Badge variant="secondary">
                {t("systemSettings.secret.set").replace("{hint}", s.hint ?? "")}
              </Badge>
              <Button type="button" variant="outline" size="sm" onClick={() => setValue(s, null)}>
                {t("systemSettings.secret.clear")}
              </Button>
            </>
          ) : (
            <Badge variant="outline">{t("systemSettings.secret.unset")}</Badge>
          )}
        </div>
      );
    }
    if (s.type === "bool") {
      return (
        <Switch
          id={s.key}
          aria-label={s.label}
          checked={value === "1"}
          onCheckedChange={(checked) => setValue(s, checked ? "1" : "0")}
        />
      );
    }
    return (
      <Input
        id={s.key}
        type={s.type === "int" || s.type === "float" ? "number" : "text"}
        step={s.type === "float" ? "any" : undefined}
        value={value}
        onChange={(e) => setValue(s, e.target.value)}
        className="max-w-md"
      />
    );
  };

  if (query.isError) {
    return <PageHeader title={t("systemSettings.title")} description={t("systemSettings.loadFailed")} />;
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <PageHeader title={t("systemSettings.title")} description={t("systemSettings.description")} />
      <div className="flex min-h-0 flex-1 gap-4">
        <nav className="w-56 shrink-0 space-y-1" aria-label="groups">
          {groups.map((g) => (
            <button
              key={g.key}
              type="button"
              onClick={() => switchTab(g.key)}
              className={[
                "w-full rounded-md px-3 py-2 text-left text-sm",
                g.key === active ? "bg-primary/10 font-medium text-primary" : "hover:bg-muted",
              ].join(" ")}
            >
              {g.label}
            </button>
          ))}
        </nav>
        <Card className="min-w-0 flex-1">
          <CardContent className="space-y-6 p-6">
            {group?.settings.map((s) => (
              <div key={s.key} className="grid gap-2 border-b pb-4 last:border-b-0">
                <label htmlFor={s.key} className="text-sm font-medium">
                  {s.label}
                </label>
                {renderControl(s)}
                {errors[s.key] && <p className="text-sm text-destructive">{errors[s.key]}</p>}
                <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                  {s.help && <span>{s.help}</span>}
                  {!s.is_secret && (
                    <span>{t("systemSettings.default").replace("{value}", String(s.default))}</span>
                  )}
                  {s.updated_at && (
                    <span>
                      {t("systemSettings.lastEdited")
                        .replace("{user}", s.updated_by?.name ?? "—")
                        .replace("{time}", formatDateTime(s.updated_at))}
                    </span>
                  )}
                  <Popover
                    open={historyKey === s.key}
                    onOpenChange={(open) => setHistoryKey(open ? s.key : null)}
                  >
                    <PopoverTrigger asChild>
                      <Button type="button" variant="ghost" size="sm" className="h-6 px-2">
                        <History className="mr-1 h-3 w-3" />
                        {t("systemSettings.history")}
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-96 text-xs">
                      {(historyQuery.data ?? []).length === 0 ? (
                        <p>{t("systemSettings.history.empty")}</p>
                      ) : (
                        <ul className="space-y-1">
                          {historyQuery.data!.map((h) => (
                            <li key={h.changed_at}>
                              {formatDateTime(h.changed_at)} · {h.changed_by?.name ?? "—"} ·{" "}
                              <code>{h.old_value || "∅"}</code> → <code>{h.new_value || "∅"}</code>
                            </li>
                          ))}
                        </ul>
                      )}
                    </PopoverContent>
                  </Popover>
                </div>
              </div>
            ))}
            <div className="flex justify-end">
              <Button type="button" onClick={handleSave} disabled={!dirty || mutation.isPending}>
                {t("systemSettings.save")}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
```
Điều chỉnh theo API thật của `Badge` (`variant` có `destructive/secondary/outline`?), `Switch` (`onCheckedChange`?), `Popover` — mở 3 file trong `src/components/ui` để khớp props.

- [ ] **Step 6: Chạy test FE + typecheck + lint (tuần tự)**

```bash
D="docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order-sysconfig/frontend node:22-alpine"
$D npx vitest run src/features/system-settings src/app/__tests__/modules.test.ts src/i18n
$D npm run typecheck
$D npm run lint
```
Expected: pass; typecheck 0 lỗi; lint không thêm warning mới.

- [ ] **Step 7: Commit**

```bash
git add frontend/src && git commit -m "feat(frontend): trang Cài đặt → Cấu hình hệ thống (tab theo nhóm, secret che, lưu theo khóa đã đổi, lịch sử)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Tài liệu, `.env.example`, staging, chạy toàn bộ suite, hoàn tất nhánh

**Files:**
- Create: `docs/system-settings.md`
- Modify: `.env.example`, `.env.staging.example`, `docs/staging-environment.md`, `backend/.env.example` (nếu có khóa trùng), `docs/superpowers/specs/2026-09-19-system-settings-db-design.md` (ghi chú số khóa 77 + 8 khóa Lark bổ sung)

- [ ] **Step 1: Viết `docs/system-settings.md`**

```markdown
# Cấu hình hệ thống trong DB (apps.sysconfig)

Spec: `docs/superpowers/specs/2026-09-19-system-settings-db-design.md`.

## Đọc trong code
```python
from apps.sysconfig import sysconfig
if not sysconfig.get("FOXIA_PUSH_ENABLED"): ...
```
- Đọc **tại lúc dùng**, không cache ở module-level (mất tác dụng "đổi là có hiệu lực").
- `apps.common` không import sysconfig — dùng `apps.common.runtime_config.get(KEY)`.
- Cơ chế: snapshot toàn bảng trong process; mỗi ≥2s so `sysconfig:version` trên Redis,
  lệch thì nạp lại. Redis lỗi → giữ snapshot cũ. Ghi qua `set_many()` tăng version sau commit.

## Thêm cài đặt mới
Thêm một `Setting(...)` vào `SETTINGS` trong `apps/sysconfig/registry.py` (key viết hoa,
kiểu, default đúng kiểu, group, label, help, `secret=True` nếu là credential,
`staging_forbidden=True` nếu cầm nó là ghi được lên hệ thống thật). Không migration.
Registry được kiểm tra lúc boot. KHÔNG thêm `os.getenv` cho khóa vận hành nữa.

## Test
Toàn suite chạy ở test mode (conftest): `override_sysconfig(KEY=...)` (ưu tiên) hoặc
`override_settings(KEY=...)` (tương thích cũ); không có → default. Test cần bảng thật:
`settings.SYSCONFIG_TEST_MODE = False; sysconfig.reset()`.

## Staging
`sysconfig.get()` trả default cho khóa `staging_forbidden` khi `APP_ENV=staging` bất kể DB.
`manage.py staging_neutralize --yes` xoá các dòng đó + nhóm `inbound_tokens`.

## Deploy prod lần đầu (chuyển từ .env)
0. Thêm `SYSCONFIG_ALLOW_ENV_KEYS=1` vào `.env` (hạ guard thành warning trong cửa sổ chuyển).
1. Pull image, `migrate` (sysconfig.0001).
2. `docker compose run --rm web python manage.py sysconfig_import_env` → xem diff → `--apply`.
3. Recreate web / worker* / beat.
4. Kiểm tra trang Cài đặt → Cấu hình hệ thống; tạo thử đơn → Lark; gọi Hermes/Daily Report.
5. Backup `.env` → `.env.bak-<ngày>`; xoá mọi khóa registry + khóa chết + `BEST_EXPRESS_USERNAME/
   PASSWORD/EMS_KEY`, `CNV_SSO_CLIENT_ID/SECRET`, `KIOTVIET_WEBHOOK_SECRET_B64`; gỡ
   `SYSCONFIG_ALLOW_ENV_KEYS`.
6. Recreate lần nữa. Boot fail-fast nếu còn sót khóa (cố ý).
Rollback: image cũ + `.env.bak`.
```

- [ ] **Step 2: Sửa `.env.example` + `.env.staging.example`**

Xoá mọi dòng (kể cả dòng comment `#KEY=`) của khóa registry + 5 khóa chết + `BEST_EXPRESS_USERNAME/PASSWORD/EMS_KEY`, `CNV_SSO_CLIENT_ID/SECRET`, `KIOTVIET_WEBHOOK_SECRET_B64`. Thay bằng một khối:
```
# ===== Đã chuyển vào DB (2026-09-19) =====
# Credential đối tác (Lark, Gemini, Zenify, CNV, token API vào…) và tham số vận hành
# (cờ bật/tắt, ngưỡng QA, thông tin công ty portal…) KHÔNG còn đọc từ .env — sửa ở
# Cài đặt → Cấu hình hệ thống (superuser). Danh sách khóa: backend/apps/sysconfig/registry.py.
# Boot sẽ CHẾT nếu .env còn khóa nào trong danh sách đó. Xem docs/system-settings.md.
# SYSCONFIG_ALLOW_ENV_KEYS=1   # chỉ trong cửa sổ deploy chuyển đổi
```
Kiểm tra bằng script Step 5 Task 10 (mở rộng `grep` sang `.env.example` `.env.staging.example`): không còn tên khóa registry ở dạng `KEY=`.

`docs/staging-environment.md` bảng neutralize thêm dòng `system_settings` (staging_forbidden + inbound_tokens). Spec §3 thêm ghi chú 8 khóa Lark bổ sung và tổng 77.

- [ ] **Step 3: Chạy toàn bộ backend suite (một mình, không song song)**

Run: `cd /home/desi/desi-order-sysconfig/backend && env $TEST_ENV $PYTEST apps -q --reuse-db -x --maxfail=30 2>&1 | tee /home/desi/.cache/sysconfig-pytest.txt`
Expected: chỉ còn các fail đã biết trên master (`test_price_book_service.py` ×21 dưới reuse-db, 2 test users role Administrator, `StatementTimeoutTests`). Với mỗi fail khác: chạy đúng file đó trên master (`git stash`/worktree master, cùng DB test) để so.

Chạy import-linter: `cd backend && /home/desi/desi-order/.venv/bin/lint-imports` → phải PASS (tầng `sysconfig` mới).

- [ ] **Step 4: Chạy toàn bộ FE suite (một mình)**

Run: `docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order-sysconfig/frontend node:22-alpine npx vitest run 2>&1 | tee /home/desi/.cache/sysconfig-vitest.txt`
Expected: chỉ `flow-graph.test.ts` fail sẵn (thiếu @xyflow). Test UI nặng timeout → chạy lại đơn lẻ trước khi kết luận.

- [ ] **Step 5: Commit docs + hoàn tất**

```bash
git add docs .env.example .env.staging.example backend/.env.example
git commit -m "docs: hướng dẫn cấu hình hệ thống trong DB, dọn .env.example, quy trình deploy chuyển đổi

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```
Sau đó dùng skill `superpowers:finishing-a-development-branch` (push nhánh `feat/system-settings-db`, tạo MR bằng `git push -o merge_request.create -o merge_request.target=master -o merge_request.title="..."` theo memory `gitlab-mr-via-push-options`; mô tả MR kết thúc bằng `🤖 Generated with [Claude Code](https://claude.com/claude-code)`). Ghi chú deploy trong MR: migration `sysconfig.0001`, thứ tự 0→6 ở `docs/system-settings.md`, cần `SYSCONFIG_ALLOW_ENV_KEYS=1` trước khi recreate.
