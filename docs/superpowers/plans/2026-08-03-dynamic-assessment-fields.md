# Dynamic Assessment Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trường đánh giá đơn hàng động (định nghĩa trong DB, không cần migration/deploy khi thêm) + giỏ mở cho AI ghi nhận thông tin tự do từ transcript.

**Architecture:** Bảng registry `assessment_field_defs` + 2 cột JSONB trên `order_assessments` (`extra_fields`, `ai_open_insights`). Sale ghi qua đúng cửa `_upsert_order_assessment`; AI extract build prompt/schema từ registry; 3 luật cứng AI fill giữ nguyên cho trường động; giỏ mở append-only có dedupe + cap. Spec: `docs/superpowers/specs/2026-08-03-dynamic-assessment-fields-design.md`.

**Tech Stack:** Django 5 + DRF + PostgreSQL JSONB (backend), google-genai structured output (AI), React + shadcn/react-select (frontend).

## Global Constraints

- KHÔNG đụng 25 cột cứng hiện có của `OrderAssessment`, không đổi shape `ai_filled_fields`.
- Đúng 2 schema migration mới cho app `orders`: `0112` (bảng + 2 cột JSONB) và `0113` (seed 3 field text AI).
- Field def inactive / code không có trong registry → payload sale bị reject `AssessmentValidationError`; luồng AI thì skip im lặng (không crash tick).
- Giới hạn: text ≤ `TEXT_MAX_LEN` (4000), array ≤ `ARRAY_MAX_ITEMS` (32), open insights ≤ 100 item/đơn, topic ≤ 100 ký tự.
- Registry cache 60s, invalidate khi save/delete field def. KHÔNG BAO GIỜ `cache.clear()` trong test (FLUSHDB đè Redis thật) — dùng `cache.delete(REGISTRY_CACHE_KEY)`.
- Lint bắt buộc: `ruff check .` (backend), `npm run lint:strict` + `npx tsc --noEmit` (frontend).

### Lệnh chạy test (worktree này)

Backend (image `desi-backend-test:latest`, KHÔNG phải tên trong docs; đặt `POSTGRES_DB`/`REDIS_CACHE_DB` riêng cho từng LẦN chạy):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=test_dyn_assessment_$RANDOM \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Ruff: `docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .`

Frontend: worktree chưa có `node_modules` — tạo junction 1 lần (PowerShell):

```powershell
New-Item -ItemType Junction -Path "<worktree>\frontend\node_modules" -Target "C:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```

Rồi `npx vitest run <file>`. Lưu ý: `src/app/__tests__/RequireAuth.test.tsx` fail 2 test sẵn trên master — không phải lỗi của thay đổi này.

---

### Task 1: Model `AssessmentFieldDef` + 2 cột JSONB + migration + Django admin

**Files:**
- Modify: `backend/apps/orders/models.py` (thêm model sau `OrderNeed`, ~dòng 620; thêm 2 field vào `OrderAssessment` ngay trên `ai_filled_fields`)
- Modify: `backend/apps/orders/admin.py`
- Create: `backend/apps/orders/migrations/0112_dynamic_assessment_fields.py` (makemigrations)
- Test: `backend/apps/orders/tests/test_dynamic_assessment_registry.py`

**Interfaces:**
- Produces: model `AssessmentFieldDef` (fields: `code, label, kind, options, ai_enabled, ai_hint, is_active, sort_order, created_at, updated_at`), `FieldKind(models.TextChoices)` với values `single_choice|multi_choice|text|number|bool`, hằng `RESERVED_FIELD_CODES: frozenset[str]`, `OrderAssessment.extra_fields: dict`, `OrderAssessment.ai_open_insights: list`.
- Consumes: `ASSESSMENT_FIELD_NAMES` từ `apps.orders.assessment_fields`.

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_dynamic_assessment_registry.py
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.orders.models import AssessmentFieldDef, FieldKind


def make_def(**kwargs) -> AssessmentFieldDef:
    defaults = {
        "code": "family_context",
        "label": "Hoàn cảnh gia đình",
        "kind": FieldKind.TEXT,
        "options": [],
    }
    defaults.update(kwargs)
    return AssessmentFieldDef(**defaults)


class AssessmentFieldDefCleanTests(TestCase):
    def test_valid_text_def_passes(self):
        make_def().full_clean()  # không raise

    def test_reserved_code_rejected(self):
        for code in ("sales_status_code", "needs", "extra_fields", "ai_filled_fields", "ai_open_insights"):
            with self.assertRaises(ValidationError, msg=code):
                make_def(code=code).full_clean()

    def test_choice_kind_requires_options(self):
        with self.assertRaises(ValidationError):
            make_def(code="housing_type", kind=FieldKind.SINGLE_CHOICE, options=[]).full_clean()

    def test_choice_options_must_have_code_and_label(self):
        with self.assertRaises(ValidationError):
            make_def(
                code="housing_type", kind=FieldKind.SINGLE_CHOICE, options=[{"code": "apartment"}]
            ).full_clean()

    def test_choice_option_codes_must_be_unique(self):
        with self.assertRaises(ValidationError):
            make_def(
                code="housing_type",
                kind=FieldKind.SINGLE_CHOICE,
                options=[
                    {"code": "apartment", "label": "Chung cư"},
                    {"code": "apartment", "label": "Trùng"},
                ],
            ).full_clean()

    def test_non_choice_kind_must_not_have_options(self):
        with self.assertRaises(ValidationError):
            make_def(options=[{"code": "x", "label": "X"}]).full_clean()

    def test_kind_change_blocked_when_data_exists(self):
        from apps.orders.models import Order, OrderAssessment

        spec = make_def()
        spec.full_clean()
        spec.save()
        order = Order.objects.create(phone="0900000001", name="KH")
        OrderAssessment.objects.create(order=order, extra_fields={"family_context": "sống cùng con"})
        spec.kind = FieldKind.NUMBER
        with self.assertRaises(ValidationError):
            spec.full_clean()

    def test_kind_change_allowed_without_data(self):
        spec = make_def()
        spec.full_clean()
        spec.save()
        spec.kind = FieldKind.NUMBER
        spec.full_clean()  # không raise
```

Lưu ý: nếu `Order.objects.create` cần field bắt buộc khác, xem cách các test hiện có tạo Order (vd `backend/apps/orders/tests/test_restore_cancelled_orders.py`) và dùng đúng factory/pattern đó.

- [ ] **Step 2: Chạy test, xác nhận fail**

Chạy lệnh Docker ở đầu plan với path `apps/orders/tests/test_dynamic_assessment_registry.py`.
Expected: FAIL `ImportError: cannot import name 'AssessmentFieldDef'`.

- [ ] **Step 3: Thêm model + 2 cột JSONB**

Trong `backend/apps/orders/models.py`, thêm vào `OrderAssessment` ngay TRÊN `ai_filled_fields` (giữ nhóm JSONB gần nhau):

```python
    # Giá trị các trường động định nghĩa trong AssessmentFieldDef.
    # Shape: {"<field_code>": str | list[str] | int | float | bool}
    extra_fields = models.JSONField(default=dict, blank=True)
    # Giỏ mở: AI ghi nhận thông tin đáng chú ý chưa thuộc trường nào. Append-only,
    # dedupe theo (topic, content), tối đa 100 item (xem assessment_ai_fill_service).
    # Shape: [{"topic": str, "content": str, "quote": str, "call_record_id": int, "at": iso8601}]
    ai_open_insights = models.JSONField(default=list, blank=True)
```

Sau class `OrderNeed`, thêm:

```python
class FieldKind(models.TextChoices):
    SINGLE_CHOICE = "single_choice", "Chọn một"
    MULTI_CHOICE = "multi_choice", "Chọn nhiều"
    TEXT = "text", "Text tự do"
    NUMBER = "number", "Số"
    BOOL = "bool", "Có/Không"


# Code trường động không được trùng cột thật / key hệ thống của payload CRM.
RESERVED_FIELD_CODES = frozenset(
    {"needs", "extra_fields", "ai_filled_fields", "ai_open_insights", "events"}
)


class AssessmentFieldDef(models.Model):
    """Registry trường đánh giá động — thêm trường CRM mới là thao tác dữ liệu
    trong Django admin, không cần migration. Giá trị lưu ở OrderAssessment.extra_fields."""

    code = models.SlugField(max_length=64, unique=True)
    label = models.CharField(max_length=255)
    kind = models.CharField(max_length=32, choices=FieldKind.choices)
    # [{"code": str, "label": str}] — bắt buộc với single/multi_choice, rỗng với kind khác.
    options = models.JSONField(default=list, blank=True)
    ai_enabled = models.BooleanField(default=False)
    ai_hint = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assessment_field_defs"
        ordering = ["sort_order", "id"]

    def __str__(self) -> str:
        return f"{self.code} ({self.get_kind_display()})"

    def clean(self) -> None:
        from django.core.exceptions import ValidationError

        from apps.orders.assessment_fields import ASSESSMENT_FIELD_NAMES

        if self.code in RESERVED_FIELD_CODES or self.code in ASSESSMENT_FIELD_NAMES:
            raise ValidationError({"code": f"'{self.code}' trùng cột có sẵn hoặc key hệ thống."})

        is_choice = self.kind in (FieldKind.SINGLE_CHOICE, FieldKind.MULTI_CHOICE)
        options = self.options or []
        if is_choice:
            if not options:
                raise ValidationError({"options": "Kind choice bắt buộc có options."})
            codes = []
            for item in options:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("code"), str)
                    or not item.get("code")
                    or not isinstance(item.get("label"), str)
                    or not item.get("label")
                ):
                    raise ValidationError({"options": 'Mỗi option phải là {"code": str, "label": str}.'})
                codes.append(item["code"])
            if len(codes) != len(set(codes)):
                raise ValidationError({"options": "Option code bị trùng."})
        elif options:
            raise ValidationError({"options": f"Kind '{self.kind}' không dùng options."})

        # Đổi kind khi đã có dữ liệu = đọc sai dữ liệu cũ. Muốn đổi thì tạo field mới.
        if self.pk:
            current = AssessmentFieldDef.objects.filter(pk=self.pk).values_list("kind", flat=True).first()
            if current and current != self.kind:
                if OrderAssessment.objects.filter(extra_fields__has_key=self.code).exists():
                    raise ValidationError({"kind": "Đã có dữ liệu — không đổi kind, hãy tạo field mới."})

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from apps.orders.dynamic_assessment import invalidate_registry_cache

        invalidate_registry_cache()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        from apps.orders.dynamic_assessment import invalidate_registry_cache

        invalidate_registry_cache()
        return result
```

Ghi chú: import `invalidate_registry_cache` để trong hàm (module `dynamic_assessment` import ngược `models` — Task 2). Ở Task 1, tạo trước file `backend/apps/orders/dynamic_assessment.py` tối thiểu:

```python
"""Registry trường đánh giá động. Hàm đọc/validate hoàn thiện ở task sau."""
from django.core.cache import cache

REGISTRY_CACHE_KEY = "assessment_field_defs:active"
REGISTRY_CACHE_TTL = 60


def invalidate_registry_cache() -> None:
    cache.delete(REGISTRY_CACHE_KEY)
```

- [ ] **Step 4: Tạo migration**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=makemig_dyn \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py makemigrations orders --name dynamic_assessment_fields
```

Kiểm tra file sinh ra là `0112_...` và chỉ chứa: CreateModel `AssessmentFieldDef` + AddField `extra_fields`, `ai_open_insights`.

- [ ] **Step 5: Đăng ký Django admin**

Trong `backend/apps/orders/admin.py`:

```python
from .models import AssessmentFieldDef, LandingIngestTrace, Order


@admin.register(AssessmentFieldDef)
class AssessmentFieldDefAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "kind", "ai_enabled", "is_active", "sort_order", "updated_at")
    list_editable = ("ai_enabled", "is_active", "sort_order")
    list_filter = ("kind", "ai_enabled", "is_active")
    search_fields = ("code", "label")
    ordering = ("sort_order", "id")

    def has_delete_permission(self, request, obj=None):
        # Spec §2.1: đã có dữ liệu thì không hard-delete — chỉ tắt is_active.
        if obj is None:
            return super().has_delete_permission(request, obj)
        from apps.orders.models import OrderAssessment

        return not OrderAssessment.objects.filter(extra_fields__has_key=obj.code).exists()
```

- [ ] **Step 6: Chạy test, xác nhận pass** — cùng lệnh Step 2. Expected: PASS toàn bộ.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/admin.py backend/apps/orders/dynamic_assessment.py backend/apps/orders/migrations/0112_dynamic_assessment_fields.py backend/apps/orders/tests/test_dynamic_assessment_registry.py
git commit -m "feat(orders): add assessment field def registry and jsonb columns"
```

---

### Task 2: `dynamic_assessment.py` — đọc registry có cache + validate giá trị

**Files:**
- Modify: `backend/apps/orders/dynamic_assessment.py`
- Test: `backend/apps/orders/tests/test_dynamic_assessment_registry.py` (thêm class)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) DynamicFieldDef(code: str, label: str, kind: str, options: tuple, ai_enabled: bool, ai_hint: str, sort_order: int)`
  - `get_active_defs() -> tuple[DynamicFieldDef, ...]` (cache 60s)
  - `get_active_defs_by_code() -> dict[str, DynamicFieldDef]`
  - `get_ai_enabled_defs() -> tuple[DynamicFieldDef, ...]`
  - `option_codes(spec: DynamicFieldDef) -> tuple[str, ...]`
  - `coerce_and_validate_dynamic(spec: DynamicFieldDef, value) -> Any` — raise `AssessmentValidationError`
- Consumes: `AssessmentFieldDef`, `TEXT_MAX_LEN`, `ARRAY_MAX_ITEMS`, `AssessmentValidationError`.

- [ ] **Step 1: Viết test fail** (thêm vào file test Task 1)

```python
from django.core.cache import cache

from apps.orders.assessment_fields import AssessmentValidationError
from apps.orders.dynamic_assessment import (
    REGISTRY_CACHE_KEY,
    coerce_and_validate_dynamic,
    get_active_defs,
    get_active_defs_by_code,
    get_ai_enabled_defs,
)


class DynamicRegistryReadTests(TestCase):
    def setUp(self):
        cache.delete(REGISTRY_CACHE_KEY)
        self.addCleanup(cache.delete, REGISTRY_CACHE_KEY)

    def _seed(self):
        AssessmentFieldDef.objects.create(
            code="housing_type", label="Loại nhà ở", kind=FieldKind.SINGLE_CHOICE,
            options=[{"code": "apartment", "label": "Chung cư"}, {"code": "house", "label": "Nhà đất"}],
            ai_enabled=True, sort_order=2,
        )
        AssessmentFieldDef.objects.create(
            code="family_context", label="Hoàn cảnh gia đình", kind=FieldKind.TEXT, sort_order=1,
        )
        AssessmentFieldDef.objects.create(
            code="old_field", label="Đã tắt", kind=FieldKind.TEXT, is_active=False,
        )

    def test_active_defs_sorted_and_exclude_inactive(self):
        self._seed()
        self.assertEqual([d.code for d in get_active_defs()], ["family_context", "housing_type"])

    def test_ai_enabled_subset(self):
        self._seed()
        self.assertEqual([d.code for d in get_ai_enabled_defs()], ["housing_type"])

    def test_cache_invalidated_on_save(self):
        self._seed()
        get_active_defs()  # nạp cache
        AssessmentFieldDef.objects.create(code="budget", label="Ngân sách", kind=FieldKind.NUMBER)
        self.assertIn("budget", get_active_defs_by_code())


class CoerceDynamicTests(TestCase):
    def _choice(self, kind=FieldKind.SINGLE_CHOICE):
        spec = AssessmentFieldDef.objects.create(
            code="housing_type", label="Loại nhà ở", kind=kind,
            options=[{"code": "apartment", "label": "Chung cư"}, {"code": "house", "label": "Nhà đất"}],
        )
        return get_active_defs_by_code()[spec.code]

    def setUp(self):
        cache.delete(REGISTRY_CACHE_KEY)
        self.addCleanup(cache.delete, REGISTRY_CACHE_KEY)

    def test_single_choice_accepts_listed_code(self):
        self.assertEqual(coerce_and_validate_dynamic(self._choice(), "apartment"), "apartment")

    def test_single_choice_rejects_unknown_code(self):
        with self.assertRaises(AssessmentValidationError):
            coerce_and_validate_dynamic(self._choice(), "tent")

    def test_multi_choice_dedupes_and_validates(self):
        spec = self._choice(kind=FieldKind.MULTI_CHOICE)
        self.assertEqual(
            coerce_and_validate_dynamic(spec, ["house", "apartment", "house"]), ["house", "apartment"]
        )
        with self.assertRaises(AssessmentValidationError):
            coerce_and_validate_dynamic(spec, ["house", "tent"])
        with self.assertRaises(AssessmentValidationError):
            coerce_and_validate_dynamic(spec, "house")  # phải là list

    def test_text_length_capped(self):
        spec_row = AssessmentFieldDef.objects.create(code="note_x", label="X", kind=FieldKind.TEXT)
        spec = get_active_defs_by_code()[spec_row.code]
        self.assertEqual(coerce_and_validate_dynamic(spec, " ok "), " ok ")
        with self.assertRaises(AssessmentValidationError):
            coerce_and_validate_dynamic(spec, "a" * 4001)
        with self.assertRaises(AssessmentValidationError):
            coerce_and_validate_dynamic(spec, 123)

    def test_number_rejects_bool_and_strings(self):
        spec_row = AssessmentFieldDef.objects.create(code="budget", label="Ngân sách", kind=FieldKind.NUMBER)
        spec = get_active_defs_by_code()[spec_row.code]
        self.assertEqual(coerce_and_validate_dynamic(spec, 25), 25)
        self.assertEqual(coerce_and_validate_dynamic(spec, 25.5), 25.5)
        for bad in (True, "25"):
            with self.assertRaises(AssessmentValidationError):
                coerce_and_validate_dynamic(spec, bad)

    def test_bool_kind(self):
        spec_row = AssessmentFieldDef.objects.create(code="has_elderly", label="Có người già", kind=FieldKind.BOOL)
        spec = get_active_defs_by_code()[spec_row.code]
        self.assertIs(coerce_and_validate_dynamic(spec, True), True)
        with self.assertRaises(AssessmentValidationError):
            coerce_and_validate_dynamic(spec, "yes")

    def test_none_passes_through(self):
        spec_row = AssessmentFieldDef.objects.create(code="note_y", label="Y", kind=FieldKind.TEXT)
        spec = get_active_defs_by_code()[spec_row.code]
        self.assertIsNone(coerce_and_validate_dynamic(spec, None))
```

- [ ] **Step 2: Chạy test, xác nhận fail** — `ImportError` các hàm mới.

- [ ] **Step 3: Hoàn thiện module**

```python
"""Registry trường đánh giá động: đọc/cache field defs + validate giá trị.

Cache plain dataclass (không cache model instance) để pickle gọn và không rò
queryset. TTL ngắn + invalidate chủ động khi save/delete nên admin sửa xong
tối đa 60s là mọi worker thấy — đủ cho reference data đổi vài lần/tháng.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.cache import cache

from apps.orders.assessment_fields import (
    ARRAY_MAX_ITEMS,
    TEXT_MAX_LEN,
    AssessmentValidationError,
)

REGISTRY_CACHE_KEY = "assessment_field_defs:active"
REGISTRY_CACHE_TTL = 60


@dataclass(frozen=True)
class DynamicFieldDef:
    code: str
    label: str
    kind: str
    options: tuple  # tuple[dict[str, str], ...] — ({"code", "label"}, ...)
    ai_enabled: bool
    ai_hint: str
    sort_order: int


def invalidate_registry_cache() -> None:
    cache.delete(REGISTRY_CACHE_KEY)


def get_active_defs() -> tuple[DynamicFieldDef, ...]:
    cached = cache.get(REGISTRY_CACHE_KEY)
    if cached is not None:
        return cached
    from apps.orders.models import AssessmentFieldDef

    defs = tuple(
        DynamicFieldDef(
            code=row.code,
            label=row.label,
            kind=row.kind,
            options=tuple(row.options or []),
            ai_enabled=row.ai_enabled,
            ai_hint=row.ai_hint,
            sort_order=row.sort_order,
        )
        for row in AssessmentFieldDef.objects.filter(is_active=True).order_by("sort_order", "id")
    )
    cache.set(REGISTRY_CACHE_KEY, defs, REGISTRY_CACHE_TTL)
    return defs


def get_active_defs_by_code() -> dict[str, DynamicFieldDef]:
    return {spec.code: spec for spec in get_active_defs()}


def get_ai_enabled_defs() -> tuple[DynamicFieldDef, ...]:
    return tuple(spec for spec in get_active_defs() if spec.ai_enabled)


def option_codes(spec: DynamicFieldDef) -> tuple[str, ...]:
    return tuple(item["code"] for item in spec.options)


def coerce_and_validate_dynamic(spec: DynamicFieldDef, value: Any) -> Any:
    """Validate giá trị trường động theo kind. None đi qua (nghĩa là xoá key)."""
    if value is None:
        return None

    if spec.kind == "single_choice":
        if not isinstance(value, str):
            raise AssessmentValidationError(f"{spec.code} must be a string code")
        if value and value not in option_codes(spec):
            raise AssessmentValidationError(f"{spec.code}={value!r} is not a valid code")
        return value

    if spec.kind == "multi_choice":
        if not isinstance(value, (list, tuple)):
            raise AssessmentValidationError(f"{spec.code} must be a list of codes")
        if len(value) > ARRAY_MAX_ITEMS:
            raise AssessmentValidationError(f"{spec.code} exceeds max items {ARRAY_MAX_ITEMS}")
        allowed = option_codes(spec)
        seen: list[str] = []
        for code in value:
            if not isinstance(code, str) or code not in allowed:
                raise AssessmentValidationError(f"{spec.code} contains invalid code {code!r}")
            if code not in seen:
                seen.append(code)
        return seen

    if spec.kind == "text":
        if not isinstance(value, str):
            raise AssessmentValidationError(f"{spec.code} must be a string")
        if len(value) > TEXT_MAX_LEN:
            raise AssessmentValidationError(f"{spec.code} exceeds max length {TEXT_MAX_LEN}")
        return value

    if spec.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AssessmentValidationError(f"{spec.code} must be a number")
        return value

    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise AssessmentValidationError(f"{spec.code} must be a boolean")
        return value

    raise AssessmentValidationError(f"{spec.code} has unknown kind {spec.kind!r}")
```

- [ ] **Step 4: Chạy test, xác nhận pass.**

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/dynamic_assessment.py backend/apps/orders/tests/test_dynamic_assessment_registry.py
git commit -m "feat(orders): dynamic field registry read path and validation"
```

---

### Task 3: Ghi trường động qua `_upsert_order_assessment`

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_helpers.py` (quanh dòng 550–616)
- Test: `backend/apps/orders/tests/test_dynamic_assessment_upsert.py`

**Interfaces:**
- Produces: `OrderServiceHelpers._apply_dynamic_assessment_payload(assessment, crm_payload) -> list[str]` (list code đã đổi); `_upsert_order_assessment` chấp nhận key động trong `crm_payload`.
- Consumes: `get_active_defs_by_code`, `coerce_and_validate_dynamic` (Task 2).

**Hành vi chốt:**
1. Key không phải cột cứng, không phải `"needs"`, không có trong registry active → raise `AssessmentValidationError` (sale thấy 400 như code sai hiện tại).
2. Giá trị hợp lệ → merge vào `extra_fields`; `None`/`""`/`[]` = xoá key.
3. Có thay đổi động → `"extra_fields"` vào `changed_fields` (để `_save_assessment` ghi đúng `update_fields`) và code động nào bị sale đè thì key đó bị xoá khỏi `ai_filled_fields` (cùng luật field cứng).

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_dynamic_assessment_upsert.py
from django.core.cache import cache
from django.test import TestCase

from apps.orders.application.services.order_service_helpers import OrderServiceHelpers
from apps.orders.assessment_fields import AssessmentValidationError
from apps.orders.dynamic_assessment import REGISTRY_CACHE_KEY
from apps.orders.models import AssessmentFieldDef, FieldKind, Order, OrderAssessment


class DynamicUpsertTests(TestCase):
    def setUp(self):
        cache.delete(REGISTRY_CACHE_KEY)
        self.addCleanup(cache.delete, REGISTRY_CACHE_KEY)
        self.order = Order.objects.create(phone="0900000002", name="KH")
        AssessmentFieldDef.objects.create(
            code="housing_type", label="Loại nhà ở", kind=FieldKind.SINGLE_CHOICE,
            options=[{"code": "apartment", "label": "Chung cư"}, {"code": "house", "label": "Nhà đất"}],
        )
        AssessmentFieldDef.objects.create(
            code="family_context", label="Hoàn cảnh gia đình", kind=FieldKind.TEXT,
        )
        AssessmentFieldDef.objects.create(
            code="inactive_field", label="Đã tắt", kind=FieldKind.TEXT, is_active=False,
        )

    def _upsert(self, payload):
        assessment, changed = OrderServiceHelpers._upsert_order_assessment(self.order, payload, None)
        assessment.refresh_from_db()
        return assessment, changed

    def test_writes_dynamic_value_into_extra_fields(self):
        assessment, changed = self._upsert({"housing_type": "apartment", "internal_note": "ghi chú"})
        self.assertEqual(assessment.extra_fields, {"housing_type": "apartment"})
        self.assertEqual(assessment.internal_note, "ghi chú")
        self.assertIn("extra_fields", changed)

    def test_unknown_key_rejected(self):
        with self.assertRaises(AssessmentValidationError):
            self._upsert({"not_a_field": "x"})

    def test_inactive_def_rejected(self):
        with self.assertRaises(AssessmentValidationError):
            self._upsert({"inactive_field": "x"})

    def test_invalid_code_rejected(self):
        with self.assertRaises(AssessmentValidationError):
            self._upsert({"housing_type": "tent"})

    def test_empty_value_removes_key(self):
        self._upsert({"housing_type": "apartment"})
        assessment, _ = self._upsert({"housing_type": None})
        self.assertEqual(assessment.extra_fields, {})

    def test_manual_edit_clears_ai_provenance_for_dynamic_code(self):
        assessment, _ = self._upsert({"family_context": "AI ghi"})
        OrderAssessment.objects.filter(pk=assessment.pk).update(
            ai_filled_fields={"family_context": {"call_record_id": 1, "quote": "q", "at": "t"}}
        )
        assessment, _ = self._upsert({"family_context": "sale sửa lại"})
        self.assertNotIn("family_context", assessment.ai_filled_fields)
        self.assertEqual(assessment.extra_fields["family_context"], "sale sửa lại")

    def test_no_dynamic_change_no_extra_fields_in_changed(self):
        self._upsert({"housing_type": "apartment"})
        _, changed = self._upsert({"housing_type": "apartment"})
        self.assertNotIn("extra_fields", changed)
```

- [ ] **Step 2: Chạy test, xác nhận fail** — unknown key hiện bị bỏ qua im lặng nên `test_unknown_key_rejected`, `test_writes_dynamic_value_into_extra_fields` fail.

- [ ] **Step 3: Implement**

Thêm static method vào `OrderServiceHelpers` (cạnh `_apply_assessment_payload`):

```python
    @staticmethod
    def _apply_dynamic_assessment_payload(assessment: OrderAssessment, crm_payload: dict) -> list[str]:
        """Validate + merge trường động vào extra_fields. Trả list code đã đổi.

        Key lạ (không phải cột cứng, không phải needs, không có trong registry
        active) bị reject — không ghi mù vào JSONB để dữ liệu không bẩn dần.
        """
        from apps.orders.dynamic_assessment import coerce_and_validate_dynamic, get_active_defs_by_code

        dynamic_keys = [
            key for key in crm_payload if key not in ASSESSMENT_FIELDS_BY_NAME and key != "needs"
        ]
        if not dynamic_keys:
            return []

        defs_by_code = get_active_defs_by_code()
        extra = dict(assessment.extra_fields or {})
        changed: list[str] = []
        for key in dynamic_keys:
            spec = defs_by_code.get(key)
            if spec is None:
                raise AssessmentValidationError(f"{key} is not a known assessment field")
            next_value = coerce_and_validate_dynamic(spec, crm_payload[key])
            cleared = next_value is None or next_value == "" or next_value == []
            if cleared:
                if key not in extra:
                    continue
                extra.pop(key)
            else:
                if extra.get(key) == next_value:
                    continue
                extra[key] = next_value
            changed.append(key)
        if changed:
            assessment.extra_fields = extra
        return changed
```

Import bổ sung ở đầu file: `ASSESSMENT_FIELDS_BY_NAME` từ `apps.orders.assessment_fields` (file đã import `ASSESSMENT_FIELDS`, `coerce_and_validate` — thêm tên mới vào cùng import).

Trong `_upsert_order_assessment`, sau dòng gọi `_apply_assessment_payload` (hiện ở ~dòng 598):

```python
        changed_dynamic = OrderServiceHelpers._apply_dynamic_assessment_payload(assessment, crm_payload)
        if changed_dynamic:
            changed_fields.append("extra_fields")
```

Và sửa dòng tính `stale_keys` (hiện ~dòng 607) thành:

```python
        stale_keys = [
            name for name in changed_fields + changed_dynamic if name in (assessment.ai_filled_fields or {})
        ]
```

- [ ] **Step 4: Chạy test mới + test hồi quy liền kề, xác nhận pass**

Paths: `apps/orders/tests/test_dynamic_assessment_upsert.py apps/orders/tests/test_age_group_validation.py apps/orders/tests/test_restore_cancelled_orders.py`

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/application/services/order_service_helpers.py backend/apps/orders/tests/test_dynamic_assessment_upsert.py
git commit -m "feat(orders): accept dynamic assessment fields in crm upsert"
```

---

### Task 4: AI contract — schema/prompt/normalize từ registry + open insights

**Files:**
- Modify: `backend/apps/orders/ai_extract_contract.py`
- Test: `backend/apps/orders/tests/test_dynamic_ai_extract.py`

**Interfaces:**
- Produces: `OPEN_INSIGHTS_KEY = "open_insights"`, `OPEN_INSIGHT_TOPIC_MAX = 100`, `ai_evidence_keys() -> tuple[str, ...]` (thay hằng `AI_EVIDENCE_KEYS` ở chỗ dùng động); `build_extract_response_schema()`/`build_prompt_catalog_text()`/`normalize_extracted()` gồm cả trường động và `open_insights`. `clean["open_insights"]` = `[{"topic","content","quote"}]`.
- Consumes: `get_ai_enabled_defs`, `option_codes` (Task 2).
- Lưu ý: các hàm này giờ chạm DB (đọc registry) — test dùng `TestCase`, không `SimpleTestCase`. `AI_EVIDENCE_KEYS` (hằng tĩnh) giữ nguyên cho phần field cứng; hàm `ai_evidence_keys()` = hằng đó + code động.

- [ ] **Step 1: Viết test fail**

```python
# backend/apps/orders/tests/test_dynamic_ai_extract.py
from django.core.cache import cache
from django.test import TestCase

from apps.orders.ai_extract_contract import (
    OPEN_INSIGHTS_KEY,
    ai_evidence_keys,
    build_extract_response_schema,
    build_prompt_catalog_text,
    normalize_extracted,
)
from apps.orders.dynamic_assessment import REGISTRY_CACHE_KEY
from apps.orders.models import AssessmentFieldDef, FieldKind


class DynamicContractTests(TestCase):
    def setUp(self):
        cache.delete(REGISTRY_CACHE_KEY)
        self.addCleanup(cache.delete, REGISTRY_CACHE_KEY)
        AssessmentFieldDef.objects.create(
            code="housing_type", label="Loại nhà ở", kind=FieldKind.SINGLE_CHOICE,
            options=[{"code": "apartment", "label": "Chung cư"}, {"code": "house", "label": "Nhà đất"}],
            ai_enabled=True,
        )
        AssessmentFieldDef.objects.create(
            code="family_context", label="Hoàn cảnh gia đình", kind=FieldKind.TEXT,
            ai_enabled=True, ai_hint="ai dùng máy, ai quyết định chi tiền",
        )
        AssessmentFieldDef.objects.create(
            code="manual_only", label="Chỉ sale nhập", kind=FieldKind.TEXT, ai_enabled=False,
        )

    def test_schema_includes_dynamic_defs_and_open_insights(self):
        props = build_extract_response_schema()["properties"]
        self.assertEqual(props["housing_type"]["enum"], ["apartment", "house"])
        self.assertEqual(props["family_context"]["type"], "string")
        self.assertNotIn("manual_only", props)
        item_props = props[OPEN_INSIGHTS_KEY]["items"]["properties"]
        self.assertEqual(set(item_props), {"topic", "content", "quote"})

    def test_evidence_enum_covers_dynamic_codes(self):
        enum = build_extract_response_schema()["properties"]["evidence"]["items"]["properties"]["field"]["enum"]
        self.assertIn("housing_type", enum)
        self.assertIn("family_context", enum)
        self.assertEqual(sorted(ai_evidence_keys()), enum)

    def test_prompt_lists_dynamic_fields_with_hint(self):
        text = build_prompt_catalog_text()
        self.assertIn("housing_type", text)
        self.assertIn("ai dùng máy, ai quyết định chi tiền", text)
        self.assertIn(OPEN_INSIGHTS_KEY, text)

    def test_normalize_keeps_valid_dynamic_and_rejects_unknown_code(self):
        clean, rejected = normalize_extracted(
            {
                "housing_type": "apartment",
                "family_context": "  sống cùng con trai  ",
                "manual_only": "AI không được điền",
                "evidence": [{"field": "housing_type", "quote": "nhà tôi chung cư"}],
            }
        )
        self.assertEqual(clean["housing_type"], "apartment")
        self.assertEqual(clean["family_context"], "sống cùng con trai")
        self.assertNotIn("manual_only", clean)
        self.assertEqual(clean["evidence"], {"housing_type": "nhà tôi chung cư"})
        clean2, rejected2 = normalize_extracted({"housing_type": "tent"})
        self.assertNotIn("housing_type", clean2)
        self.assertEqual(rejected2["housing_type"], "tent")

    def test_normalize_open_insights_requires_quote_and_caps_topic(self):
        clean, _ = normalize_extracted(
            {
                OPEN_INSIGHTS_KEY: [
                    {"topic": "Bảo hành", "content": "khách hỏi bảo hành 5 năm", "quote": "bảo hành mấy năm"},
                    {"topic": "Thiếu quote", "content": "bị loại", "quote": ""},
                    {"topic": "t" * 200, "content": "topic bị cắt", "quote": "q"},
                    "not-a-dict",
                ]
            }
        )
        items = clean[OPEN_INSIGHTS_KEY]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["topic"], "Bảo hành")
        self.assertEqual(len(items[1]["topic"]), 100)
```

- [ ] **Step 2: Chạy test, xác nhận fail** — `ImportError: OPEN_INSIGHTS_KEY`.

- [ ] **Step 3: Implement trong `ai_extract_contract.py`**

Thêm sau khai báo `AI_EVIDENCE_KEYS`:

```python
OPEN_INSIGHTS_KEY = "open_insights"
OPEN_INSIGHT_TOPIC_MAX = 100
# Text trường động + content/quote giỏ mở dùng chung trần với text field cứng.
from apps.orders.assessment_fields import TEXT_MAX_LEN  # noqa: E402 — cạnh các import đầu file


def _dynamic_ai_defs():
    """Import trong hàm: tránh vòng crm_catalog ← assessment_fields ← dynamic_assessment."""
    from apps.orders.dynamic_assessment import get_ai_enabled_defs

    return get_ai_enabled_defs()


def ai_evidence_keys() -> tuple[str, ...]:
    return AI_EVIDENCE_KEYS + tuple(spec.code for spec in _dynamic_ai_defs())
```

(Đặt `from apps.orders.assessment_fields import TEXT_MAX_LEN` lên khối import đầu file cùng các import khác — không giữa file; ghi chú trên chỉ nói rõ nó thuộc về đâu.)

Trong `build_extract_response_schema()`, trước phần `evidence`:

```python
    from apps.orders.dynamic_assessment import option_codes

    for spec in _dynamic_ai_defs():
        if spec.kind == "single_choice":
            properties[spec.code] = {"type": "string", "enum": sorted(option_codes(spec)), "nullable": True}
        elif spec.kind == "multi_choice":
            properties[spec.code] = {
                "type": "array",
                "items": {"type": "string", "enum": sorted(option_codes(spec))},
            }
        elif spec.kind == "number":
            properties[spec.code] = {"type": "number", "nullable": True}
        elif spec.kind == "bool":
            properties[spec.code] = {"type": "boolean", "nullable": True}
        else:  # text
            properties[spec.code] = {"type": "string", "nullable": True}

    properties[OPEN_INSIGHTS_KEY] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "content": {"type": "string"},
                "quote": {"type": "string"},
            },
            "required": ["topic", "content", "quote"],
        },
    }
```

Và đổi enum của `evidence.field` từ `sorted(AI_EVIDENCE_KEYS)` thành `sorted(ai_evidence_keys())`.

Trong `build_prompt_catalog_text()`, trước `return`:

```python
    for spec in _dynamic_ai_defs():
        if spec.kind in ("single_choice", "multi_choice"):
            options = "; ".join(f"{item['code']} = {item['label']}" for item in spec.options)
            header = f"{spec.code} ({spec.ai_hint})" if spec.ai_hint else spec.code
            blocks.append(f"- {header}: {options}")
        else:
            hint = spec.ai_hint or spec.label
            blocks.append(f"- {spec.code}: text tự do — {hint}")
    blocks.append(
        f"- {OPEN_INSIGHTS_KEY}: MỌI thông tin đáng chú ý khác về khách/thương vụ chưa thuộc "
        "field nào ở trên; mỗi item gồm topic (chủ đề ngắn), content (nội dung), "
        "quote (trích dẫn NGUYÊN VĂN câu của khách — thiếu quote thì item bị loại)."
    )
```

`_clean_evidence` nhận thêm tham số: `def _clean_evidence(raw_evidence, allowed_keys) -> dict[str, str]:` và check `field in allowed_keys`; call site truyền `ai_evidence_keys()`.

Cuối `normalize_extracted`, trước dòng gán `clean["evidence"]`:

```python
    from apps.orders.dynamic_assessment import option_codes

    for spec in _dynamic_ai_defs():
        value = raw.get(spec.code)
        if value is None:
            continue
        if spec.kind == "single_choice":
            if isinstance(value, str) and value in option_codes(spec):
                clean[spec.code] = value
            else:
                rejected[spec.code] = value
        elif spec.kind == "multi_choice":
            if not isinstance(value, list):
                rejected[spec.code] = value
                continue
            allowed = option_codes(spec)
            kept = [c for i, c in enumerate(value) if isinstance(c, str) and c in allowed and c not in value[:i]]
            dropped = [c for c in value if not (isinstance(c, str) and c in allowed)]
            if kept:
                clean[spec.code] = kept
            if dropped:
                rejected[spec.code] = dropped
        elif spec.kind == "number":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                clean[spec.code] = value
            else:
                rejected[spec.code] = value
        elif spec.kind == "bool":
            if isinstance(value, bool):
                clean[spec.code] = value
            else:
                rejected[spec.code] = value
        else:  # text
            if isinstance(value, str) and value.strip():
                clean[spec.code] = value.strip()[:TEXT_MAX_LEN]
            else:
                rejected[spec.code] = value

    clean[OPEN_INSIGHTS_KEY] = _clean_open_insights(raw.get(OPEN_INSIGHTS_KEY))
```

Và hàm mới:

```python
def _clean_open_insights(raw_items) -> list[dict]:
    if not isinstance(raw_items, list):
        return []
    items: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        topic = item.get("topic")
        content = item.get("content")
        quote = item.get("quote")
        if not all(isinstance(v, str) and v.strip() for v in (topic, content, quote)):
            continue
        items.append(
            {
                "topic": topic.strip()[:OPEN_INSIGHT_TOPIC_MAX],
                "content": content.strip()[:TEXT_MAX_LEN],
                "quote": quote.strip()[:TEXT_MAX_LEN],
            }
        )
    return items
```

- [ ] **Step 4: Chạy test, xác nhận pass** — kèm hồi quy: `apps/orders/tests/test_dynamic_ai_extract.py apps/orders/tests/test_age_group_validation.py apps/orders/tests/test_age_group_catalog.py`. Lưu ý test cũ dạng `SimpleTestCase` gọi các hàm contract giờ chạm DB sẽ lỗi `DatabaseError... not allowed` — nếu gặp, đổi các class đó sang `TestCase` (giữ nguyên assertions).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/ai_extract_contract.py backend/apps/orders/tests/test_dynamic_ai_extract.py backend/apps/orders/tests/test_age_group_validation.py
git commit -m "feat(orders): build ai extract contract from dynamic registry with open insights"
```

---

### Task 5: `apply_ai_insights` — điền trường động + append giỏ mở

**Files:**
- Modify: `backend/apps/orders/application/services/assessment_ai_fill_service.py`
- Test: `backend/apps/orders/tests/test_dynamic_ai_extract.py` (thêm class)

**Interfaces:**
- Produces: `apply_ai_insights` xử lý thêm code động trong `clean` (điền-khi-trống qua đúng upsert cũ, provenance vào `ai_filled_fields`) và `clean["open_insights"]` (append vào `ai_open_insights`, dedupe theo `(topic, content)`, cap `OPEN_INSIGHT_MAX_ITEMS = 100`, KHÔNG cần trống). Giá trị trả về: thêm `"open_insights"` vào list khi có append.
- Consumes: `get_ai_enabled_defs`, `coerce_and_validate_dynamic` (Task 2), `OPEN_INSIGHTS_KEY` (Task 4).

- [ ] **Step 1: Viết test fail** (thêm vào `test_dynamic_ai_extract.py`)

```python
from apps.orders.application.services.assessment_ai_fill_service import (
    OPEN_INSIGHT_MAX_ITEMS,
    apply_ai_insights,
)
from apps.orders.models import Order, OrderAssessment


class DynamicAiFillTests(TestCase):
    def setUp(self):
        cache.delete(REGISTRY_CACHE_KEY)
        self.addCleanup(cache.delete, REGISTRY_CACHE_KEY)
        self.order = Order.objects.create(phone="0900000003", name="KH")
        AssessmentFieldDef.objects.create(
            code="family_context", label="Hoàn cảnh gia đình", kind=FieldKind.TEXT, ai_enabled=True,
        )

    def test_fills_empty_dynamic_field_with_evidence(self):
        filled = apply_ai_insights(
            order=self.order,
            clean={"family_context": "sống cùng con trai", "evidence": {"family_context": "tôi ở với con trai"}},
            call_record_id=7,
        )
        self.assertEqual(filled, ["family_context"])
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.extra_fields["family_context"], "sống cùng con trai")
        self.assertEqual(assessment.ai_filled_fields["family_context"]["call_record_id"], 7)

    def test_does_not_overwrite_existing_dynamic_value(self):
        OrderAssessment.objects.create(order=self.order, extra_fields={"family_context": "sale đã ghi"})
        filled = apply_ai_insights(
            order=self.order,
            clean={"family_context": "AI ghi khác", "evidence": {"family_context": "q"}},
            call_record_id=7,
        )
        self.assertEqual(filled, [])
        self.assertEqual(
            OrderAssessment.objects.get(order=self.order).extra_fields["family_context"], "sale đã ghi"
        )

    def test_no_evidence_no_write(self):
        filled = apply_ai_insights(
            order=self.order, clean={"family_context": "không căn cứ", "evidence": {}}, call_record_id=7
        )
        self.assertEqual(filled, [])

    def test_open_insights_appended_and_deduped(self):
        clean = {
            "evidence": {},
            "open_insights": [
                {"topic": "Bảo hành", "content": "khách quan tâm bảo hành", "quote": "bảo hành mấy năm"},
            ],
        }
        self.assertEqual(apply_ai_insights(order=self.order, clean=clean, call_record_id=7), ["open_insights"])
        # Gọi lại cùng nội dung (cuộc gọi khác) → dedupe, không append thêm.
        self.assertEqual(apply_ai_insights(order=self.order, clean=clean, call_record_id=8), [])
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(len(assessment.ai_open_insights), 1)
        item = assessment.ai_open_insights[0]
        self.assertEqual(item["call_record_id"], 7)
        self.assertEqual(item["topic"], "Bảo hành")

    def test_open_insights_capped(self):
        OrderAssessment.objects.create(
            order=self.order,
            ai_open_insights=[
                {"topic": f"t{i}", "content": f"c{i}", "quote": "q", "call_record_id": 1, "at": "x"}
                for i in range(OPEN_INSIGHT_MAX_ITEMS)
            ],
        )
        filled = apply_ai_insights(
            order=self.order,
            clean={"evidence": {}, "open_insights": [{"topic": "mới", "content": "bị bỏ", "quote": "q"}]},
            call_record_id=9,
        )
        self.assertEqual(filled, [])
        self.assertEqual(
            len(OrderAssessment.objects.get(order=self.order).ai_open_insights), OPEN_INSIGHT_MAX_ITEMS
        )
```

- [ ] **Step 2: Chạy test, xác nhận fail** — `ImportError: OPEN_INSIGHT_MAX_ITEMS`.

- [ ] **Step 3: Implement trong `assessment_ai_fill_service.py`**

Thêm hằng + import:

```python
from apps.orders.ai_extract_contract import AI_ARRAY_FIELDS, AI_SCALAR_FIELDS, OPEN_INSIGHTS_KEY

OPEN_INSIGHT_MAX_ITEMS = 100
```

Trong `apply_ai_insights`, sau vòng `for name in AI_SCALAR_FIELDS + AI_ARRAY_FIELDS` hiện có, thêm vòng trường động (giá trị đã qua `normalize_extracted` nhưng validate lại phòng registry đổi giữa chừng — lỗi thì SKIP, không raise, để không crash tick extract):

```python
    from apps.orders.assessment_fields import AssessmentValidationError
    from apps.orders.dynamic_assessment import coerce_and_validate_dynamic, get_ai_enabled_defs

    extra_existing = (assessment.extra_fields or {}) if assessment is not None else {}
    for spec in get_ai_enabled_defs():
        value = clean.get(spec.code)
        if _is_empty(value) or spec.code not in evidence:
            continue
        if not _is_empty(extra_existing.get(spec.code)):
            continue
        try:
            coerce_and_validate_dynamic(spec, value)
        except AssessmentValidationError:
            continue
        crm_payload[spec.code] = value
        filled[spec.code] = {"call_record_id": call_record_id, "quote": evidence[spec.code], "at": now_iso}
```

Phần open insights: hiện hàm `return []` sớm khi `crm_payload` rỗng — đổi điều kiện đó thành:

```python
    open_items = clean.get(OPEN_INSIGHTS_KEY) or []
    if not crm_payload and not open_items:
        return []
```

(`_upsert_order_assessment` với payload `{}` vẫn get_or_create assessment — cần cho ca chỉ có open insights.)

Sau khi gọi helper upsert + set `ai_filled_fields` (giữ nguyên đoạn hiện có), thêm:

```python
    appended = 0
    if open_items:
        existing = list(assessment.ai_open_insights or [])
        seen = {(item.get("topic"), item.get("content")) for item in existing}
        for item in open_items:
            if len(existing) >= OPEN_INSIGHT_MAX_ITEMS:
                break
            key = (item["topic"], item["content"])
            if key in seen:
                continue
            existing.append({**item, "call_record_id": call_record_id, "at": now_iso})
            seen.add(key)
            appended += 1
        if appended:
            assessment.ai_open_insights = existing
            assessment.save(update_fields=["ai_open_insights", "updated_at"])
```

Journey event: chỉ tạo khi `filled` hoặc `appended` (bọc đoạn `_create_journey_event` hiện có trong `if filled or appended:`), và thêm `"open_insights_appended": appended` vào `payload_json`. Return cuối:

```python
    return sorted(filled) + (["open_insights"] if appended else [])
```

Lưu ý thứ tự: đoạn set `ai_filled_fields` + save hiện tại chỉ chạy khi `crm_payload` truthy — giữ nguyên bằng cách bọc `if crm_payload:` quanh đoạn upsert + provenance, còn khi `crm_payload` rỗng mà có `open_items` thì gọi `OrderServiceHelpers._upsert_order_assessment(order, {}, actor)` để có assessment rồi mới append. Cách gọn nhất: luôn gọi upsert (payload có thể rỗng), rồi `if filled:` mới save `ai_filled_fields`.

- [ ] **Step 4: Chạy test, xác nhận pass** — kèm hồi quy autofill hiện có: `apps/orders/tests/test_dynamic_ai_extract.py` + file test autofill hiện có (tìm bằng `grep -rl apply_ai_insights backend/apps/*/tests`).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/application/services/assessment_ai_fill_service.py backend/apps/orders/tests/test_dynamic_ai_extract.py
git commit -m "feat(orders): ai fill dynamic fields and append open insights"
```

---

### Task 6: Catalog payload + serializer + seed 3 field text AI

**Files:**
- Modify: `backend/apps/orders/crm_catalog.py` (`build_crm_catalog_payload`, ~dòng 559)
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/product_item_crm_serializers.py` (`_build_meta_fields` ~dòng 399, `OrderCRMSerializer` ~dòng 421)
- Create: `backend/apps/orders/migrations/0113_seed_dynamic_field_defs.py`
- Test: `backend/apps/orders/tests/test_dynamic_assessment_registry.py` (thêm class)

**Interfaces:**
- Produces: key `"dynamic_fields"` trong catalog payload (`[{code, label, kind, options, sort_order}]`); `OrderCRMSerializer` trả `extra_fields`, `ai_open_insights` (read-only); 3 field def seed: `call_summary`, `competitors_mentioned`, `family_context` (đều `kind="text"`, `ai_enabled=True`).

- [ ] **Step 1: Viết test fail**

```python
from apps.orders.crm_catalog import build_crm_catalog_payload


class DynamicCatalogPayloadTests(TestCase):
    def setUp(self):
        cache.delete(REGISTRY_CACHE_KEY)
        self.addCleanup(cache.delete, REGISTRY_CACHE_KEY)

    def test_payload_contains_active_dynamic_fields(self):
        AssessmentFieldDef.objects.create(
            code="housing_type", label="Loại nhà ở", kind=FieldKind.SINGLE_CHOICE,
            options=[{"code": "apartment", "label": "Chung cư"}], sort_order=5,
        )
        AssessmentFieldDef.objects.create(code="off", label="Tắt", kind=FieldKind.TEXT, is_active=False)
        payload = build_crm_catalog_payload()
        self.assertEqual(
            payload["dynamic_fields"],
            [{
                "code": "housing_type", "label": "Loại nhà ở", "kind": "single_choice",
                "options": [{"code": "apartment", "label": "Chung cư"}], "sort_order": 5,
            }],
        )


class SeedDynamicDefsTests(TestCase):
    def test_seed_migration_created_ai_text_fields(self):
        codes = set(
            AssessmentFieldDef.objects.filter(ai_enabled=True, kind=FieldKind.TEXT).values_list(
                "code", flat=True
            )
        )
        self.assertLessEqual({"call_summary", "competitors_mentioned", "family_context"}, codes)
```

Và test serializer (cùng file):

```python
class CrmSerializerDynamicTests(TestCase):
    def test_serializer_exposes_extra_fields_and_open_insights(self):
        from apps.orders.transport.serializers.order_serializers_impl.product_item_crm_serializers import (
            OrderCRMSerializer,
        )

        order = Order.objects.create(phone="0900000004", name="KH")
        assessment = OrderAssessment.objects.create(
            order=order,
            extra_fields={"family_context": "sống cùng con"},
            ai_open_insights=[{"topic": "t", "content": "c", "quote": "q", "call_record_id": 1, "at": "x"}],
        )
        data = OrderCRMSerializer(assessment, context={"journey_events": []}).data
        self.assertEqual(data["extra_fields"], {"family_context": "sống cùng con"})
        self.assertEqual(len(data["ai_open_insights"]), 1)
```

(Import `Order`, `OrderAssessment` đã có ở đầu file test từ task trước.)

- [ ] **Step 2: Chạy test, xác nhận fail.**

- [ ] **Step 3: Implement**

`crm_catalog.py` — cuối dict trong `build_crm_catalog_payload()` (import trong hàm để tránh vòng import qua `assessment_fields`):

```python
def build_crm_catalog_payload() -> dict:
    from apps.orders.dynamic_assessment import get_active_defs

    return {
        ...  # các key hiện có giữ nguyên
        "dynamic_fields": [
            {
                "code": spec.code,
                "label": spec.label,
                "kind": spec.kind,
                "options": list(spec.options),
                "sort_order": spec.sort_order,
            }
            for spec in get_active_defs()
        ],
    }
```

Serializer — thêm 2 declared field cạnh `ai_filled_fields` và 2 tên vào list custom trong `_build_meta_fields`:

```python
    # Client không ghi trực tiếp — mọi ghi đi qua crm payload → _upsert_order_assessment.
    extra_fields = serializers.JSONField(read_only=True)
    ai_open_insights = serializers.JSONField(read_only=True)
```

```python
        "ai_filled_fields",
        "extra_fields",
        "ai_open_insights",
```

Migration seed `0113_seed_dynamic_field_defs.py`:

```python
from django.db import migrations

SEEDS = [
    {
        "code": "call_summary",
        "label": "Tóm tắt cuộc gọi",
        "ai_hint": "tóm tắt ngắn gọn tình trạng, nhu cầu và ý định mua của khách qua cuộc gọi",
        "sort_order": 10,
    },
    {
        "code": "competitors_mentioned",
        "label": "Đối thủ được nhắc tới",
        "ai_hint": "tên thương hiệu/hãng ghế hoặc máy massage khác mà khách nhắc tới hoặc đang so sánh",
        "sort_order": 20,
    },
    {
        "code": "family_context",
        "label": "Hoàn cảnh gia đình",
        "ai_hint": "ai trong nhà sẽ dùng máy, khách sống cùng ai, ai là người quyết định chi tiền",
        "sort_order": 30,
    },
]


def seed(apps, schema_editor):
    AssessmentFieldDef = apps.get_model("orders", "AssessmentFieldDef")
    for item in SEEDS:
        AssessmentFieldDef.objects.update_or_create(
            code=item["code"],
            defaults={**item, "kind": "text", "options": [], "ai_enabled": True, "is_active": True},
        )


def unseed(apps, schema_editor):
    AssessmentFieldDef = apps.get_model("orders", "AssessmentFieldDef")
    AssessmentFieldDef.objects.filter(code__in=[item["code"] for item in SEEDS]).delete()


class Migration(migrations.Migration):
    dependencies = [("orders", "0112_dynamic_assessment_fields")]
    operations = [migrations.RunPython(seed, unseed)]
```

- [ ] **Step 4: Chạy test, xác nhận pass** — cả file `test_dynamic_assessment_registry.py` + hồi quy `apps/orders/tests/test_age_group_catalog.py`.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/crm_catalog.py backend/apps/orders/transport/serializers/order_serializers_impl/product_item_crm_serializers.py backend/apps/orders/migrations/0113_seed_dynamic_field_defs.py backend/apps/orders/tests/test_dynamic_assessment_registry.py
git commit -m "feat(orders): expose dynamic fields in catalog and crm serializer, seed ai text fields"
```

---

### Task 7: Frontend — types + section trường động + khối "AI ghi nhận thêm"

**Files:**
- Modify: `frontend/src/features/orders/order-types.ts` (OrderCRMRow, cạnh `ai_filled_fields` ~dòng 84)
- Modify: `frontend/src/features/orders/BlockCrmRating.tsx` (type `CrmCatalogPayload` ~dòng 89; render section)
- Create: `frontend/src/features/orders/DynamicAssessmentFields.tsx`
- Create: `frontend/src/features/orders/AiOpenInsights.tsx`
- Test: `frontend/src/features/orders/__tests__/DynamicAssessmentFields.test.tsx`

**Interfaces:**
- Consumes API mới: `crmCatalog.dynamic_fields`, `crm.extra_fields`, `crm.ai_open_insights` (Task 6); `patchCrm({[code]: value})` gửi key động phẳng — backend Task 3 nhận.
- Produces:
  - `export type DynamicFieldDef = { code: string; label: string; kind: "single_choice" | "multi_choice" | "text" | "number" | "bool"; options: Array<{ code: string; label: string }>; sort_order: number }`
  - `<DynamicAssessmentFields defs values aiFilled onPatch />` với `onPatch: (code: string, value: string | string[] | number | boolean | null) => void`
  - `<AiOpenInsights items />` read-only.

- [ ] **Step 1: Thêm types**

`order-types.ts`, dưới `ai_filled_fields`:

```ts
  // Giá trị các trường đánh giá động (định nghĩa trong assessment_field_defs).
  extra_fields?: Record<string, string | string[] | number | boolean | null> | null;
  // Giỏ mở: AI ghi nhận thông tin đáng chú ý ngoài các trường đã định nghĩa. Read-only.
  ai_open_insights?: Array<{
    topic: string;
    content: string;
    quote: string;
    call_record_id: number;
    at: string;
  }> | null;
```

`BlockCrmRating.tsx`, thêm vào `CrmCatalogPayload`:

```ts
  dynamic_fields?: Array<{
    code: string;
    label: string;
    kind: "single_choice" | "multi_choice" | "text" | "number" | "bool";
    options: CrmCatalogOption[];
    sort_order: number;
  }>;
```

- [ ] **Step 2: Viết test fail**

```tsx
// frontend/src/features/orders/__tests__/DynamicAssessmentFields.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DynamicAssessmentFields } from "@/features/orders/DynamicAssessmentFields";

const defs = [
  {
    code: "housing_type",
    label: "Loại nhà ở",
    kind: "single_choice" as const,
    options: [{ code: "apartment", label: "Chung cư" }],
    sort_order: 1,
  },
  { code: "family_context", label: "Hoàn cảnh gia đình", kind: "text" as const, options: [], sort_order: 2 },
];

describe("DynamicAssessmentFields", () => {
  it("renders one row per active def with current values", () => {
    render(
      <DynamicAssessmentFields
        defs={defs}
        values={{ housing_type: "apartment", family_context: "sống cùng con" }}
        onPatch={vi.fn()}
      />,
    );
    expect(screen.getByText("Loại nhà ở")).toBeInTheDocument();
    expect(screen.getByText("Chung cư")).toBeInTheDocument();
    expect(screen.getByText("Hoàn cảnh gia đình")).toBeInTheDocument();
    expect(screen.getByText("sống cùng con")).toBeInTheDocument();
  });

  it("shows raw code when option was removed from catalog", () => {
    render(<DynamicAssessmentFields defs={defs} values={{ housing_type: "villa" }} onPatch={vi.fn()} />);
    expect(screen.getByText("villa")).toBeInTheDocument();
  });

  it("renders nothing when there is no def", () => {
    const { container } = render(<DynamicAssessmentFields defs={[]} values={{}} onPatch={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

Chạy: `npx vitest run src/features/orders/__tests__/DynamicAssessmentFields.test.tsx` → FAIL (module chưa tồn tại).

- [ ] **Step 3: Implement `DynamicAssessmentFields.tsx`**

Dùng đúng pattern `ERow` của BlockCrmRating (xem `rowAge`/`rowUsedMassage` trong file đó làm mẫu render/onSave):

```tsx
import { memo } from "react";
import ReactSelect from "react-select";
import { ERow } from "@/features/orders/order-detail-shared";
import { AiFilledBadge, type AiFilledMap } from "@/components/orders/AiFilledBadge";
import { orderGridSelectStylesFor } from "@/features/orders/react-select-styles";

export type DynamicFieldDef = {
  code: string;
  label: string;
  kind: "single_choice" | "multi_choice" | "text" | "number" | "bool";
  options: Array<{ code: string; label: string }>;
  sort_order: number;
};

export type DynamicFieldValue = string | string[] | number | boolean | null;

const BOOL_OPTIONS = [
  { value: "yes", label: "Có" },
  { value: "no", label: "Không" },
];

function DynamicAssessmentFieldsImpl({
  defs,
  values,
  aiFilled,
  onPatch,
}: {
  defs: DynamicFieldDef[];
  values: Record<string, DynamicFieldValue>;
  aiFilled?: AiFilledMap;
  onPatch: (code: string, value: DynamicFieldValue) => void;
}) {
  if (defs.length === 0) return null;
  const sorted = [...defs].sort((a, b) => a.sort_order - b.sort_order);
  return (
    <>
      {sorted.map((def) => {
        const value = values[def.code];
        const badge = <AiFilledBadge info={aiFilled?.[def.code]} />;
        if (def.kind === "single_choice") {
          const opts = def.options.map((o) => ({ value: o.code, label: o.label }));
          const current = typeof value === "string" ? value : "";
          return (
            <ERow
              key={def.code}
              label={def.label}
              labelAdornment={badge}
              rawValue={current}
              display={
                <span className="text-sm font-medium">
                  {opts.find((o) => o.value === current)?.label || current || "—"}
                </span>
              }
              type="select"
              options={opts}
              onSave={(v) => onPatch(def.code, v ? String(v) : null)}
            />
          );
        }
        if (def.kind === "multi_choice") {
          const opts = def.options.map((o) => ({ value: o.code, label: o.label }));
          const codes = Array.isArray(value) ? value : [];
          return (
            <div key={def.code} className="py-2 border-b border-border/40">
              <span className="mb-2 block text-sm text-muted-foreground">
                {def.label}
                {badge}
              </span>
              <ReactSelect
                isMulti
                styles={orderGridSelectStylesFor()}
                menuPortalTarget={document.body}
                menuPosition="fixed"
                options={opts}
                value={codes.map((c) => opts.find((o) => o.value === c) ?? { value: c, label: c })}
                onChange={(selected) => onPatch(def.code, selected.map((o) => o.value))}
                placeholder="Chọn một hoặc nhiều"
              />
            </div>
          );
        }
        if (def.kind === "bool") {
          const current = value == null ? "" : value ? "yes" : "no";
          return (
            <ERow
              key={def.code}
              label={def.label}
              labelAdornment={badge}
              rawValue={current}
              display={
                <span className="text-sm font-medium">{value == null ? "—" : value ? "Có" : "Không"}</span>
              }
              type="select"
              options={BOOL_OPTIONS}
              onSave={(v) => onPatch(def.code, v === "yes" ? true : v === "no" ? false : null)}
            />
          );
        }
        if (def.kind === "number") {
          const current = value == null ? "" : String(value);
          return (
            <ERow
              key={def.code}
              label={def.label}
              labelAdornment={badge}
              rawValue={current}
              onSave={(v) => {
                const parsed = Number(String(v).trim());
                onPatch(def.code, String(v).trim() === "" || Number.isNaN(parsed) ? null : parsed);
              }}
            />
          );
        }
        // text
        return (
          <ERow
            key={def.code}
            label={def.label}
            labelAdornment={badge}
            rawValue={typeof value === "string" ? value : ""}
            onSave={(v) => onPatch(def.code, String(v) || null)}
          />
        );
      })}
    </>
  );
}

export const DynamicAssessmentFields = memo(DynamicAssessmentFieldsImpl);
```

Nếu chữ ký props thực tế của `ERow`/`orderGridSelectStylesFor` khác (đọc `order-detail-shared.tsx` khi implement), chỉnh theo đúng component thật — hành vi phải giữ: hiển thị label + giá trị, sửa được, code mồ côi hiển thị raw.

- [ ] **Step 4: Implement `AiOpenInsights.tsx`**

```tsx
import { memo } from "react";
import { Sparkles } from "lucide-react";
import { SectionCard } from "@/features/orders/order-detail-shared";

export type AiOpenInsightItem = {
  topic: string;
  content: string;
  quote: string;
  call_record_id: number;
  at: string;
};

function AiOpenInsightsImpl({ items }: { items: AiOpenInsightItem[] }) {
  if (items.length === 0) return null;
  return (
    <SectionCard
      title={
        <span className="flex items-center gap-1.5">
          <Sparkles className="h-4 w-4 text-sky-500" /> AI ghi nhận thêm
        </span>
      }
    >
      <ul className="space-y-2">
        {items.map((item, index) => (
          <li key={`${item.call_record_id}-${index}`} className="text-sm" title={`“${item.quote}”`}>
            <span className="font-medium">{item.topic}:</span>{" "}
            <span className="text-muted-foreground">{item.content}</span>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

export const AiOpenInsights = memo(AiOpenInsightsImpl);
```

(Nếu `SectionCard` không nhận ReactNode cho `title`, dùng chuỗi "AI ghi nhận thêm" và bỏ icon — đọc `order-detail-shared.tsx` khi implement.)

- [ ] **Step 5: Nối vào `BlockCrmRating.tsx`**

Trong component, sau các row hiện có: dựng section từ catalog + render 2 component mới ở cuối danh sách row (cùng SectionCard chứa các row CRM hoặc ngay dưới — theo layout hiện có của file):

```tsx
  const dynamicDefs = crmCatalog?.dynamic_fields ?? [];
  const dynamicRows = (
    <DynamicAssessmentFields
      defs={dynamicDefs}
      values={(crm?.extra_fields ?? {}) as Record<string, DynamicFieldValue>}
      aiFilled={aiFilled}
      onPatch={(code, value) => patchCrm({ [code]: value } as Partial<OrderCRMRow>)}
    />
  );
  const openInsights = <AiOpenInsights items={crm?.ai_open_insights ?? []} />;
```

- [ ] **Step 6: Chạy test + type-check, xác nhận pass**

```bash
npx vitest run src/features/orders/__tests__/DynamicAssessmentFields.test.tsx
npx tsc --noEmit
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/orders/order-types.ts frontend/src/features/orders/BlockCrmRating.tsx frontend/src/features/orders/DynamicAssessmentFields.tsx frontend/src/features/orders/AiOpenInsights.tsx frontend/src/features/orders/__tests__/DynamicAssessmentFields.test.tsx
git commit -m "feat(frontend): render dynamic assessment fields and ai open insights"
```

---

### Task 8: Verification toàn cục + cập nhật tiến độ

- [ ] **Step 1: Backend — chạy toàn bộ test app orders + callcenter** (2 app bị chạm):

Path: `apps/orders/tests apps/callcenter/tests`. Lưu ý suite dài — nếu chạy nền phải redirect log ra file (bài học 2026-07: shell nền bị kill nhưng container chạy tiếp).

- [ ] **Step 2: Backend — ruff** (lệnh Docker ruff ở đầu plan). Expected: 0 lỗi mới.

- [ ] **Step 3: Frontend — `npm run test:ci` + `npm run lint:strict` + `npx tsc --noEmit`**

Expected: chỉ còn 2 fail sẵn có của `RequireAuth.test.tsx` (kiểm chứng trên master nếu nghi ngờ).

- [ ] **Step 4: Cập nhật spec** — thêm mục "Tiến độ" cuối file spec: đã làm gì, chưa làm gì (theo yêu cầu CLAUDE.md).

- [ ] **Step 5: Commit + push + gửi link MR**

```bash
git add -A && git commit -m "docs: mark dynamic assessment fields progress" && git push -u origin feat/dynamic-assessment-fields
```
