# Sales Warranty Claims Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an internal "Bảo hành" (warranty claim) page under the Sales module where staff create one warranty claim per order, picking the order via a phone / tracking-code search combobox.

**Architecture:** New Django model `WarrantyClaim` (OneToOne → `Order`) in the existing `warranty` app, with thin repository + service + transport layers mirroring the `customers` app. A new authenticated order-search endpoint feeds an async combobox. Frontend adds a self-contained React page (list + filter + create/edit dialog) wired through a `warranty-claims` feature folder, react-query hooks, and a typed API layer.

**Tech Stack:** Backend — Django 5 + DRF, PostgreSQL, pytest, ruff, drf-spectacular. Frontend — React 19 SPA (Vite) + TypeScript, react-router, @tanstack/react-query, shadcn/ui, lucide-react, sonner.

## Global Constraints

- Backend Python 3.12+, lint with `ruff` (`uv run --no-project --with ruff ruff check <files>`).
- Clean-architecture layering: transport → service → repository → model. Views call services/repositories, never write business logic inline.
- Order info on a claim is **never duplicated** — read live from `Order` via the FK.
- One warranty claim per order (DB-level OneToOne).
- Soft delete via `deleted_at` (never hard-delete).
- RBAC: every claim endpoint gated by `require_section("warranty")`; section key is `warranty`.
- Frontend: shadcn/ui components, light theme (blue), `lucide-react` icons, Vietnamese UI copy via `useI18n` `t(key, fallback)`.
- Commit messages in English, short. Branch `feat/sales-warranty-claims` (already created).
- Backend tests run via the ephemeral docker pattern in the `running-backend-tests` memory (image `desi-order-web`, network `desi-order_default`, `--reuse-db`, `mkdir -p /tmp/prom_multiproc`). Use `--user $(id -u):$(id -g)` for `makemigrations`.

---

## File Structure

**Backend (create):**
- `backend/apps/warranty/claim_repository.py` — `WarrantyClaimRepository` (queries only)
- `backend/apps/warranty/claim_service.py` — `WarrantyClaimService` (create/update/delete + validation)
- `backend/apps/warranty/claim_views.py` — transport (list/create + detail/update/delete)
- `backend/apps/orders/transport/views/order_search_view.py` — `search_orders`
- `backend/apps/warranty/tests/__init__.py`
- `backend/apps/warranty/tests/test_warranty_claim_api.py`
- `backend/apps/warranty/tests/test_order_search_api.py`

**Backend (modify):**
- `backend/apps/warranty/models.py` — add `WarrantyClaimStatus`, `WarrantyClaim`
- `backend/apps/warranty/serializers.py` — add claim serializers
- `backend/apps/warranty/urls.py` — wire claim routes
- `backend/apps/users/models.py` — add `"warranty"` to `VALID_SECTIONS`
- `backend/apps/users/rbac_catalog.py` — add `"warranty"` catalog entry
- `backend/apps/orders/urls.py` — wire `search` route
- `backend/apps/warranty/migrations/0002_warrantyclaim.py` — generated

**Frontend (create):**
- `frontend/src/lib/api/warrantyClaims.ts` — typed API calls
- `frontend/src/features/warranty-claims/warranty-claim-types.ts` — TS types + status constants
- `frontend/src/features/warranty-claims/queries.ts` — react-query hooks
- `frontend/src/features/warranty-claims/components/OrderPicker.tsx` — async search combobox
- `frontend/src/features/warranty-claims/components/WarrantyClaimFormDialog.tsx` — create/edit dialog
- `frontend/src/pages/WarrantyClaimsPage.tsx` — list page

**Frontend (modify):**
- `frontend/src/lib/api/orders.ts` — add `searchOrders`
- `frontend/src/lib/routes.ts` — add `warranty` section + path
- `frontend/src/app/modules.ts` — add section to Sales group
- `frontend/src/app/routes.tsx` — add `/sales/warranty` route
- `frontend/src/app/routed-pages.tsx` — add `WarrantyClaimsRoute`
- `frontend/src/i18n/messages/topnav.ts` — add `topnav.tabs.warranty` (vi + en)

---

# SPRINT 1 — Backend

### Task 1: WarrantyClaim model + migration

**Files:**
- Modify: `backend/apps/warranty/models.py`
- Create: `backend/apps/warranty/tests/__init__.py`
- Test: `backend/apps/warranty/tests/test_warranty_claim_api.py` (model section)
- Create (generated): `backend/apps/warranty/migrations/0002_warrantyclaim.py`

**Interfaces:**
- Produces: `WarrantyClaim` model with fields `order` (OneToOne→`apps.orders.models.Order`, related_name `warranty_claim`, `on_delete=PROTECT`), `status` (int, default `WarrantyClaimStatus.NEW`), text fields `contact_channel/social_nickname/send_method/classification/recall_code/return_status/accounting_check` (CharField), `send_reason/accounting_note` (TextField), `require_carrier_call` (bool), `created_by` (FK User SET_NULL null), `created_at/updated_at/deleted_at`. `WarrantyClaimStatus` IntegerChoices NEW=1/PROCESSING=2/DONE=3/CANCELLED=4.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/warranty/tests/__init__.py` (empty), then create `backend/apps/warranty/tests/test_warranty_claim_api.py`:

```python
from django.db import IntegrityError
from django.test import TestCase

from apps.orders.models import Order
from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus


def _make_order(**kwargs):
    defaults = dict(
        name="Nguyen Van A",
        phone="0901234567",
        address="123 Đường ABC",
        product="Áo thun",
        sku="SKU-001",
        quantity=1,
        shipping_code="VD123",
        order_code="OD123",
    )
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


class WarrantyClaimModelTests(TestCase):
    def test_create_claim_defaults_to_new_status(self):
        order = _make_order()
        claim = WarrantyClaim.objects.create(order=order)
        self.assertEqual(claim.status, WarrantyClaimStatus.NEW)
        self.assertFalse(claim.require_carrier_call)
        self.assertEqual(claim.contact_channel, "")
        self.assertIsNone(claim.deleted_at)

    def test_one_claim_per_order_is_enforced(self):
        order = _make_order()
        WarrantyClaim.objects.create(order=order)
        with self.assertRaises(IntegrityError):
            WarrantyClaim.objects.create(order=order)
```

- [ ] **Step 2: Run test to verify it fails**

Run (host, Linux pattern from memory):
```bash
docker run --rm --network desi-order_default --user $(id -u):$(id -g) \
  --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
  -e DJANGO_SETTINGS_MODULE=config.settings \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/warranty/tests/test_warranty_claim_api.py -q --reuse-db'
```
Expected: FAIL — `ImportError: cannot import name 'WarrantyClaim'`.

- [ ] **Step 3: Add the model**

Append to `backend/apps/warranty/models.py` (the file currently imports only `from django.db import models` — keep that import):

```python
class WarrantyClaimStatus(models.IntegerChoices):
    NEW = 1, "Mới"
    PROCESSING = 2, "Đang xử lý"
    DONE = 3, "Hoàn tất"
    CANCELLED = 4, "Hủy"


class WarrantyClaim(models.Model):
    """Phiếu bảo hành nội bộ gắn 1-1 với một đơn hàng.

    Thông tin đơn (tên, sđt, địa chỉ, SKU, số tiền...) KHÔNG lưu ở đây mà đọc
    live qua FK ``order`` để tránh trùng lặp dữ liệu.
    """

    order = models.OneToOneField(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="warranty_claim",
    )
    status = models.PositiveSmallIntegerField(
        choices=WarrantyClaimStatus.choices, default=WarrantyClaimStatus.NEW
    )
    contact_channel = models.CharField(max_length=100, blank=True, default="")
    require_carrier_call = models.BooleanField(default=False)
    social_nickname = models.CharField(max_length=255, blank=True, default="")
    send_reason = models.TextField(blank=True, default="")
    send_method = models.CharField(max_length=100, blank=True, default="")
    classification = models.CharField(max_length=100, blank=True, default="")
    recall_code = models.CharField(max_length=100, blank=True, default="")
    return_status = models.CharField(max_length=100, blank=True, default="")
    accounting_check = models.CharField(max_length=100, blank=True, default="")
    accounting_note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "warranty_claims"
        indexes = [
            models.Index(fields=["status"], name="warranty_claim_status_idx"),
            models.Index(fields=["created_at"], name="warranty_claim_created_idx"),
            models.Index(fields=["deleted_at"], name="warranty_claim_deleted_idx"),
        ]

    def __str__(self):
        return f"WarrantyClaim #{self.pk} (order={self.order_id})"
```

- [ ] **Step 4: Generate the migration**

Run:
```bash
docker run --rm --network desi-order_default --user $(id -u):$(id -g) \
  --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
  -e DJANGO_SETTINGS_MODULE=config.settings \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  python manage.py makemigrations warranty
```
Expected: creates `apps/warranty/migrations/0002_warrantyclaim.py`.

- [ ] **Step 5: Run test to verify it passes**

Run the Step 2 command again. Expected: PASS (2 passed).

- [ ] **Step 6: Lint + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/warranty/models.py backend/apps/warranty/tests/
git add backend/apps/warranty/models.py backend/apps/warranty/migrations/0002_warrantyclaim.py backend/apps/warranty/tests/
git commit -m "feat(warranty): add WarrantyClaim model and migration"
```

---

### Task 2: Register `warranty` RBAC section

**Files:**
- Modify: `backend/apps/users/models.py:6-32` (`VALID_SECTIONS`)
- Modify: `backend/apps/users/rbac_catalog.py:15-49` (`PERMISSION_CATALOG`)
- Test: `backend/apps/warranty/tests/test_warranty_claim_api.py` (add a catalog test)

**Interfaces:**
- Produces: permission codes `warranty:view|create|edit|delete|export` available after `sync_permissions()`; section `warranty` accepted by `require_section`.

- [ ] **Step 1: Write the failing test**

Append to `backend/apps/warranty/tests/test_warranty_claim_api.py`:

```python
from apps.users.models import Permission
from apps.users.rbac_catalog import sync_permissions


class WarrantyRbacCatalogTests(TestCase):
    def test_sync_creates_warranty_section_codes(self):
        sync_permissions()
        codes = set(
            Permission.objects.filter(section="warranty").values_list("code", flat=True)
        )
        self.assertEqual(
            codes,
            {
                "warranty:view",
                "warranty:create",
                "warranty:edit",
                "warranty:delete",
                "warranty:export",
            },
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
docker run --rm --network desi-order_default --user $(id -u):$(id -g) \
  --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
  -e DJANGO_SETTINGS_MODULE=config.settings \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/warranty/tests/test_warranty_claim_api.py::WarrantyRbacCatalogTests -q --reuse-db'
```
Expected: FAIL (empty set != expected set).

- [ ] **Step 3: Add the section**

In `backend/apps/users/models.py`, add `"warranty",` to the `VALID_SECTIONS` list (after `"resale-tracking",` to keep Sales sections together):

```python
    "resale-tracking",
    "warranty",
```

In `backend/apps/users/rbac_catalog.py`, add to `PERMISSION_CATALOG` under the Sales block (after the `koc-orders` line):

```python
    "koc-orders": ("view", "create", "edit", "delete", "export"),
    "warranty": ("view", "create", "edit", "delete", "export"),
```

- [ ] **Step 4: Run test to verify it passes**

Re-run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/users/models.py backend/apps/users/rbac_catalog.py
git add backend/apps/users/models.py backend/apps/users/rbac_catalog.py backend/apps/warranty/tests/
git commit -m "feat(rbac): add warranty section to permission catalog"
```

---

### Task 3: Claim serializers

**Files:**
- Modify: `backend/apps/warranty/serializers.py`
- Test: covered indirectly by Task 4 API tests (no standalone step)

**Interfaces:**
- Produces:
  - `WarrantyClaimOrderSerializer` (read-only) — fields `id, name, phone, address, product, sku, quantity, total_after_discount, shipping_code, order_code`.
  - `WarrantyClaimSerializer` (read) — fields `id, order (nested), status, status_label, contact_channel, require_carrier_call, social_nickname, send_reason, send_method, classification, recall_code, return_status, accounting_check, accounting_note, created_by_name, created_at, updated_at`.
  - `WarrantyClaimWriteSerializer` — writable: `order_id (IntegerField, write-only, required on create), status, contact_channel, require_carrier_call, social_nickname, send_reason, send_method, classification, recall_code, return_status, accounting_check, accounting_note`.

- [ ] **Step 1: Add the serializers**

`backend/apps/warranty/serializers.py` currently defines `WarrantyRegistrationSerializer`. Append (keep existing imports; add `WarrantyClaim` import):

```python
from apps.warranty.models import WarrantyClaim, WarrantyClaimStatus


class WarrantyClaimOrderSerializer(serializers.ModelSerializer):
    """Thông tin đơn hiển thị trên phiếu (read-only, đọc live từ Order)."""

    class Meta:
        from apps.orders.models import Order

        model = Order
        fields = (
            "id",
            "name",
            "phone",
            "address",
            "product",
            "sku",
            "quantity",
            "total_after_discount",
            "shipping_code",
            "order_code",
        )
        read_only_fields = fields


class WarrantyClaimSerializer(serializers.ModelSerializer):
    order = WarrantyClaimOrderSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = WarrantyClaim
        fields = (
            "id",
            "order",
            "status",
            "status_label",
            "contact_channel",
            "require_carrier_call",
            "social_nickname",
            "send_reason",
            "send_method",
            "classification",
            "recall_code",
            "return_status",
            "accounting_check",
            "accounting_note",
            "created_by_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_created_by_name(self, obj) -> str:
        user = obj.created_by
        if not user:
            return ""
        return user.display_name or user.username


class WarrantyClaimWriteSerializer(serializers.ModelSerializer):
    order_id = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = WarrantyClaim
        fields = (
            "order_id",
            "status",
            "contact_channel",
            "require_carrier_call",
            "social_nickname",
            "send_reason",
            "send_method",
            "classification",
            "recall_code",
            "return_status",
            "accounting_check",
            "accounting_note",
        )

    def validate_status(self, value):
        if value not in WarrantyClaimStatus.values:
            raise serializers.ValidationError("Trạng thái không hợp lệ.")
        return value
```

> Note: `WarrantyRegistrationSerializer` already imports `from rest_framework import serializers`. If not present at top of file, add it.

- [ ] **Step 2: Lint + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/warranty/serializers.py
git add backend/apps/warranty/serializers.py
git commit -m "feat(warranty): add warranty claim serializers"
```

---

### Task 4: Claim repository, service, views, URLs (CRUD)

**Files:**
- Create: `backend/apps/warranty/claim_repository.py`
- Create: `backend/apps/warranty/claim_service.py`
- Create: `backend/apps/warranty/claim_views.py`
- Modify: `backend/apps/warranty/urls.py`
- Test: `backend/apps/warranty/tests/test_warranty_claim_api.py` (API section)

**Interfaces:**
- Consumes: `WarrantyClaim`, `WarrantyClaimStatus` (Task 1); serializers (Task 3); `parse_required_pagination` (`apps.common.pagination`); `require_section` (`apps.users.permissions`).
- Produces:
  - `WarrantyClaimRepository.list_claims(q: str, status: int | None, limit: int, offset: int) -> tuple[list[WarrantyClaim], int]` — excludes soft-deleted, `select_related("order", "created_by")`, ordered `-created_at`. `q` matches order phone/shipping_code/order_code/name (icontains).
  - `WarrantyClaimRepository.get_claim(claim_id: int) -> WarrantyClaim | None` — non-deleted only.
  - `WarrantyClaimService.create_claim(order_id: int, data: dict, created_by) -> WarrantyClaim` — raises `ValueError` if order missing/deleted, or `ValueError` if a non-deleted claim already exists for the order.
  - `WarrantyClaimService.update_claim(claim, data: dict) -> WarrantyClaim`.
  - `WarrantyClaimService.soft_delete(claim) -> None`.
  - Endpoints: `GET/POST /api/warranty/claims`, `GET/PATCH/DELETE /api/warranty/claims/{claim_id}`.

- [ ] **Step 1: Write the failing API tests**

Append to `backend/apps/warranty/tests/test_warranty_claim_api.py`:

```python
from rest_framework.test import APIClient

from apps.users.test_helpers import make_user_with_sections
from apps.users.models import User


class WarrantyClaimApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = make_user_with_sections("warranty-staff", ["warranty"])
        self.outsider = User.objects.create_user(username="no-warranty", password="x")
        self.order = _make_order(phone="0907778889", shipping_code="VDX9", order_code="ODX9")

    def _payload(self, **overrides):
        data = dict(
            order_id=self.order.id,
            status=WarrantyClaimStatus.NEW,
            contact_channel="Shopee chat",
            require_carrier_call=True,
            social_nickname="shop_abc",
            send_reason="Lỗi đường may",
            send_method="GHTK",
            classification="Đổi mới",
            recall_code="RC-1",
            return_status="Chờ về kho",
            accounting_check="OK",
            accounting_note="Đã đối soát",
        )
        data.update(overrides)
        return data

    def test_create_claim(self):
        self.client.force_authenticate(self.staff)
        res = self.client.post("/api/warranty/claims", self._payload(), format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["order"]["id"], self.order.id)
        self.assertEqual(res.data["order"]["phone"], "0907778889")
        self.assertEqual(res.data["status"], int(WarrantyClaimStatus.NEW))
        self.assertTrue(res.data["require_carrier_call"])
        self.assertEqual(WarrantyClaim.objects.count(), 1)

    def test_create_duplicate_claim_rejected(self):
        self.client.force_authenticate(self.staff)
        WarrantyClaim.objects.create(order=self.order)
        res = self.client.post("/api/warranty/claims", self._payload(), format="json")
        self.assertEqual(res.status_code, 400)

    def test_create_for_missing_order_rejected(self):
        self.client.force_authenticate(self.staff)
        res = self.client.post(
            "/api/warranty/claims", self._payload(order_id=999999), format="json"
        )
        self.assertEqual(res.status_code, 400)

    def test_list_filter_and_search(self):
        self.client.force_authenticate(self.staff)
        WarrantyClaim.objects.create(order=self.order, status=WarrantyClaimStatus.DONE)
        other = _make_order(phone="0911112222", shipping_code="VDZZ", order_code="ODZZ")
        WarrantyClaim.objects.create(order=other, status=WarrantyClaimStatus.NEW)

        res = self.client.get("/api/warranty/claims?limit=50&offset=0")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["total"], 2)

        res = self.client.get("/api/warranty/claims?status=3&limit=50&offset=0")
        self.assertEqual(res.data["total"], 1)
        self.assertEqual(res.data["results"][0]["order"]["id"], self.order.id)

        res = self.client.get("/api/warranty/claims?q=VDZZ&limit=50&offset=0")
        self.assertEqual(res.data["total"], 1)
        self.assertEqual(res.data["results"][0]["order"]["id"], other.id)

    def test_patch_claim(self):
        self.client.force_authenticate(self.staff)
        claim = WarrantyClaim.objects.create(order=self.order)
        res = self.client.patch(
            f"/api/warranty/claims/{claim.id}",
            {"status": int(WarrantyClaimStatus.PROCESSING), "accounting_note": "Đang xử"},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        claim.refresh_from_db()
        self.assertEqual(claim.status, WarrantyClaimStatus.PROCESSING)
        self.assertEqual(claim.accounting_note, "Đang xử")

    def test_soft_delete_claim(self):
        self.client.force_authenticate(self.staff)
        claim = WarrantyClaim.objects.create(order=self.order)
        res = self.client.delete(f"/api/warranty/claims/{claim.id}")
        self.assertEqual(res.status_code, 204)
        claim.refresh_from_db()
        self.assertIsNotNone(claim.deleted_at)
        # excluded from list after delete
        res = self.client.get("/api/warranty/claims?limit=50&offset=0")
        self.assertEqual(res.data["total"], 0)

    def test_outsider_forbidden(self):
        self.client.force_authenticate(self.outsider)
        res = self.client.get("/api/warranty/claims?limit=50&offset=0")
        self.assertEqual(res.status_code, 403)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
docker run --rm --network desi-order_default --user $(id -u):$(id -g) \
  --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
  -e DJANGO_SETTINGS_MODULE=config.settings \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/warranty/tests/test_warranty_claim_api.py::WarrantyClaimApiTests -q --reuse-db'
```
Expected: FAIL — 404 (routes not wired) on every test.

- [ ] **Step 3: Create the repository**

Create `backend/apps/warranty/claim_repository.py`:

```python
from __future__ import annotations

from django.db.models import Q

from apps.warranty.models import WarrantyClaim


class WarrantyClaimRepository:
    """Truy vấn phiếu bảo hành (chỉ tầng đọc/ghi DB)."""

    @staticmethod
    def base_qs():
        return WarrantyClaim.objects.filter(deleted_at__isnull=True).select_related(
            "order", "created_by"
        )

    @classmethod
    def list_claims(
        cls, q: str, status: int | None, limit: int, offset: int
    ) -> tuple[list[WarrantyClaim], int]:
        qs = cls.base_qs()
        if status is not None:
            qs = qs.filter(status=status)
        q = (q or "").strip()
        if q:
            qs = qs.filter(
                Q(order__phone__icontains=q)
                | Q(order__phone_normalized__icontains=q)
                | Q(order__shipping_code__icontains=q)
                | Q(order__order_code__icontains=q)
                | Q(order__name__icontains=q)
            )
        total = qs.count()
        rows = list(qs.order_by("-created_at")[offset : offset + limit])
        return rows, total

    @classmethod
    def get_claim(cls, claim_id: int) -> WarrantyClaim | None:
        return cls.base_qs().filter(id=claim_id).first()
```

- [ ] **Step 4: Create the service**

Create `backend/apps/warranty/claim_service.py`:

```python
from __future__ import annotations

from django.utils import timezone

from apps.orders.models import Order
from apps.warranty.models import WarrantyClaim

_EDITABLE_FIELDS = (
    "status",
    "contact_channel",
    "require_carrier_call",
    "social_nickname",
    "send_reason",
    "send_method",
    "classification",
    "recall_code",
    "return_status",
    "accounting_check",
    "accounting_note",
)


class WarrantyClaimService:
    """Nghiệp vụ phiếu bảo hành: tạo/sửa/xóa mềm + ràng buộc 1 phiếu / đơn."""

    @staticmethod
    def create_claim(order_id: int, data: dict, created_by) -> WarrantyClaim:
        order = Order.objects.filter(id=order_id, deleted_at__isnull=True).first()
        if order is None:
            raise ValueError("Không tìm thấy đơn hàng.")
        existing = WarrantyClaim.objects.filter(
            order_id=order_id, deleted_at__isnull=True
        ).exists()
        if existing:
            raise ValueError("Đơn hàng này đã có phiếu bảo hành.")
        fields = {k: data[k] for k in _EDITABLE_FIELDS if k in data}
        return WarrantyClaim.objects.create(order=order, created_by=created_by, **fields)

    @staticmethod
    def update_claim(claim: WarrantyClaim, data: dict) -> WarrantyClaim:
        changed = []
        for key in _EDITABLE_FIELDS:
            if key in data:
                setattr(claim, key, data[key])
                changed.append(key)
        if changed:
            claim.save(update_fields=[*changed, "updated_at"])
        return claim

    @staticmethod
    def soft_delete(claim: WarrantyClaim) -> None:
        claim.deleted_at = timezone.now()
        claim.save(update_fields=["deleted_at", "updated_at"])
```

- [ ] **Step 5: Create the views**

Create `backend/apps/warranty/claim_views.py`:

```python
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.users.permissions import require_section

from .claim_repository import WarrantyClaimRepository
from .claim_service import WarrantyClaimService
from .serializers import WarrantyClaimSerializer, WarrantyClaimWriteSerializer


def _query_int(request, key):
    try:
        return int(request.query_params.get(key))
    except (TypeError, ValueError):
        return None


@extend_schema(
    request=WarrantyClaimWriteSerializer,
    responses={
        200: inline_serializer(
            "WarrantyClaimListResponse",
            fields={
                "results": WarrantyClaimSerializer(many=True),
                "total": drf_serializers.IntegerField(),
            },
        ),
        201: WarrantyClaimSerializer,
        400: OpenApiResponse(description="Dữ liệu không hợp lệ / đơn đã có phiếu"),
    },
)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_section("warranty")])
def list_create_warranty_claims(request):
    if request.method == "POST":
        write = WarrantyClaimWriteSerializer(data=request.data)
        write.is_valid(raise_exception=True)
        order_id = write.validated_data.get("order_id")
        if not order_id:
            return Response({"detail": "Thiếu order_id."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            claim = WarrantyClaimService.create_claim(
                order_id=order_id,
                data=write.validated_data,
                created_by=request.user if request.user.is_authenticated else None,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(WarrantyClaimSerializer(claim).data, status=status.HTTP_201_CREATED)

    pagination, error = parse_required_pagination(request)
    if error is not None:
        return error
    rows, total = WarrantyClaimRepository.list_claims(
        q=request.query_params.get("q", ""),
        status=_query_int(request, "status"),
        limit=pagination["limit"],
        offset=pagination["offset"],
    )
    return Response({"results": WarrantyClaimSerializer(rows, many=True).data, "total": total})


@extend_schema(
    request=WarrantyClaimWriteSerializer,
    responses={
        200: WarrantyClaimSerializer,
        204: OpenApiResponse(description="Đã xóa phiếu"),
        404: OpenApiResponse(description="Không tìm thấy phiếu"),
    },
)
@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_section("warranty")])
def detail_update_delete_warranty_claim(request, claim_id: int):
    claim = WarrantyClaimRepository.get_claim(claim_id)
    if claim is None:
        return Response({"detail": "Không tìm thấy phiếu bảo hành."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(WarrantyClaimSerializer(claim).data)

    if request.method == "DELETE":
        WarrantyClaimService.soft_delete(claim)
        return Response(status=status.HTTP_204_NO_CONTENT)

    write = WarrantyClaimWriteSerializer(data=request.data, partial=True)
    write.is_valid(raise_exception=True)
    data = dict(write.validated_data)
    data.pop("order_id", None)  # order không đổi sau khi tạo
    claim = WarrantyClaimService.update_claim(claim, data)
    return Response(WarrantyClaimSerializer(claim).data, status=status.HTTP_200_OK)
```

- [ ] **Step 6: Wire the URLs**

Replace `backend/apps/warranty/urls.py` with:

```python
from django.urls import path

from .claim_views import (
    detail_update_delete_warranty_claim,
    list_create_warranty_claims,
)
from .views import create_warranty_registration

urlpatterns = [
    path("", create_warranty_registration, name="warranty-create"),
    path("claims", list_create_warranty_claims, name="warranty-claims"),
    path(
        "claims/<int:claim_id>",
        detail_update_delete_warranty_claim,
        name="warranty-claim-detail",
    ),
]
```

- [ ] **Step 7: Run tests to verify they pass**

Re-run the Step 2 command. Expected: PASS (all `WarrantyClaimApiTests`).

- [ ] **Step 8: Lint + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/warranty/
git add backend/apps/warranty/claim_repository.py backend/apps/warranty/claim_service.py backend/apps/warranty/claim_views.py backend/apps/warranty/urls.py backend/apps/warranty/tests/
git commit -m "feat(warranty): add warranty claim CRUD endpoints"
```

---

### Task 5: Order search endpoint

**Files:**
- Create: `backend/apps/orders/transport/views/order_search_view.py`
- Modify: `backend/apps/orders/urls.py:3,65-130`
- Test: `backend/apps/warranty/tests/test_order_search_api.py`

**Interfaces:**
- Produces: `GET /api/orders/search?q=<str>&limit=<int>` → JSON array of `{id, name, phone, address, product, sku, quantity, total_after_discount, shipping_code, order_code}`. Gated by `require_section("warranty", *ORDER_SECTIONS)`. Matches phone/phone_normalized/shipping_code/order_code/name (icontains), non-deleted, `-created_at`, default limit 20 (max 20). Empty/short `q` (<2 chars) returns `[]`.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/orders/transport/views/__init__.py` if missing (it already exists). Create `backend/apps/warranty/tests/test_order_search_api.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient

from apps.orders.models import Order
from apps.users.models import User
from apps.users.test_helpers import make_user_with_sections


def _make_order(**kwargs):
    defaults = dict(
        name="Tran Thi B",
        phone="0905550001",
        address="456 Đường XYZ",
        product="Quần jean",
        sku="SKU-9",
        quantity=2,
        shipping_code="SHIP777",
        order_code="ORD777",
    )
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


class OrderSearchApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = make_user_with_sections("warranty-search", ["warranty"])
        self.outsider = User.objects.create_user(username="nobody-search", password="x")
        self.order = _make_order()

    def test_search_by_phone(self):
        self.client.force_authenticate(self.staff)
        res = self.client.get("/api/orders/search?q=0905550001")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["id"], self.order.id)
        self.assertEqual(res.data[0]["shipping_code"], "SHIP777")

    def test_search_by_shipping_code(self):
        self.client.force_authenticate(self.staff)
        res = self.client.get("/api/orders/search?q=SHIP777")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)

    def test_short_query_returns_empty(self):
        self.client.force_authenticate(self.staff)
        res = self.client.get("/api/orders/search?q=0")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, [])

    def test_excludes_deleted(self):
        from django.utils import timezone

        self.order.deleted_at = timezone.now()
        self.order.save(update_fields=["deleted_at"])
        self.client.force_authenticate(self.staff)
        res = self.client.get("/api/orders/search?q=SHIP777")
        self.assertEqual(res.data, [])

    def test_outsider_forbidden(self):
        self.client.force_authenticate(self.outsider)
        res = self.client.get("/api/orders/search?q=SHIP777")
        self.assertEqual(res.status_code, 403)
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
docker run --rm --network desi-order_default --user $(id -u):$(id -g) \
  --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
  -e DJANGO_SETTINGS_MODULE=config.settings \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/warranty/tests/test_order_search_api.py -q --reuse-db'
```
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Create the view**

Create `backend/apps/orders/transport/views/order_search_view.py`:

```python
from __future__ import annotations

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.models import Order
from apps.users.permissions import ORDER_SECTIONS, require_section

_MAX_LIMIT = 20

_SearchResultSerializer = inline_serializer(
    "OrderSearchResult",
    many=True,
    fields={
        "id": drf_serializers.IntegerField(),
        "name": drf_serializers.CharField(),
        "phone": drf_serializers.CharField(),
        "address": drf_serializers.CharField(),
        "product": drf_serializers.CharField(),
        "sku": drf_serializers.CharField(),
        "quantity": drf_serializers.IntegerField(),
        "total_after_discount": drf_serializers.CharField(),
        "shipping_code": drf_serializers.CharField(),
        "order_code": drf_serializers.CharField(),
    },
)


@extend_schema(
    parameters=[
        OpenApiParameter(name="q", type=str, required=True, description="SĐT / mã vận đơn / mã đơn / tên"),
        OpenApiParameter(name="limit", type=int, required=False),
    ],
    responses={200: _SearchResultSerializer},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("warranty", *ORDER_SECTIONS)])
def search_orders(request):
    q = (request.query_params.get("q") or "").strip()
    if len(q) < 2:
        return Response([])
    try:
        limit = min(int(request.query_params.get("limit", _MAX_LIMIT)), _MAX_LIMIT)
    except (TypeError, ValueError):
        limit = _MAX_LIMIT
    limit = max(1, limit)
    rows = (
        Order.objects.filter(deleted_at__isnull=True)
        .filter(
            Q(phone__icontains=q)
            | Q(phone_normalized__icontains=q)
            | Q(shipping_code__icontains=q)
            | Q(order_code__icontains=q)
            | Q(name__icontains=q)
        )
        .order_by("-created_at")[:limit]
    )
    return Response(
        [
            {
                "id": o.id,
                "name": o.name,
                "phone": o.phone,
                "address": o.address,
                "product": o.product,
                "sku": o.sku,
                "quantity": o.quantity,
                "total_after_discount": str(o.total_after_discount),
                "shipping_code": o.shipping_code,
                "order_code": o.order_code,
            }
            for o in rows
        ]
    )
```

- [ ] **Step 4: Wire the URL**

In `backend/apps/orders/urls.py`, add the import near the other transport-view imports (line ~3):

```python
from apps.orders.transport.views.order_search_view import search_orders
```

And add the route in `urlpatterns` immediately before the `lookup-by-shipping-code` line (must be above the `<int:order_id>` catch-all at the bottom):

```python
    path("search", search_orders, name="orders-search"),
    path("lookup-by-shipping-code", lookup_by_shipping_code, name="orders-lookup-shipping-code"),
```

- [ ] **Step 5: Run test to verify it passes**

Re-run the Step 2 command. Expected: PASS (5 passed).

- [ ] **Step 6: Lint + run the whole warranty test suite + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/orders/transport/views/order_search_view.py backend/apps/orders/urls.py
# full suite for the feature:
docker run --rm --network desi-order_default --user $(id -u):$(id -g) \
  --env-file backend/.env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
  -e DJANGO_SETTINGS_MODULE=config.settings \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/warranty/tests/ -q --reuse-db'
git add backend/apps/orders/transport/views/order_search_view.py backend/apps/orders/urls.py backend/apps/warranty/tests/
git commit -m "feat(orders): add authenticated order search endpoint"
```
Expected: all tests pass.

---

# SPRINT 2 — Frontend

### Task 6: API layer + types

**Files:**
- Create: `frontend/src/features/warranty-claims/warranty-claim-types.ts`
- Create: `frontend/src/lib/api/warrantyClaims.ts`
- Modify: `frontend/src/lib/api/orders.ts`

**Interfaces:**
- Produces TS types `WarrantyClaim`, `WarrantyClaimOrder`, `WarrantyClaimPayload`, `WarrantyClaimListParams`, `WarrantyClaimListResponse`, `OrderSearchResult`, `WarrantyClaimStatus`, `WARRANTY_STATUS_OPTIONS`.
- Produces API fns `listWarrantyClaims`, `createWarrantyClaim`, `updateWarrantyClaim`, `deleteWarrantyClaim`, `searchOrders`.

- [ ] **Step 1: Create the types**

Create `frontend/src/features/warranty-claims/warranty-claim-types.ts`:

```ts
export type WarrantyClaimStatus = 1 | 2 | 3 | 4;

export const WARRANTY_STATUS_OPTIONS: { value: WarrantyClaimStatus; label: string }[] = [
  { value: 1, label: "Mới" },
  { value: 2, label: "Đang xử lý" },
  { value: 3, label: "Hoàn tất" },
  { value: 4, label: "Hủy" },
];

export interface OrderSearchResult {
  id: number;
  name: string;
  phone: string;
  address: string;
  product: string;
  sku: string;
  quantity: number;
  total_after_discount: string;
  shipping_code: string;
  order_code: string;
}

export type WarrantyClaimOrder = OrderSearchResult;

export interface WarrantyClaim {
  id: number;
  order: WarrantyClaimOrder;
  status: WarrantyClaimStatus;
  status_label: string;
  contact_channel: string;
  require_carrier_call: boolean;
  social_nickname: string;
  send_reason: string;
  send_method: string;
  classification: string;
  recall_code: string;
  return_status: string;
  accounting_check: string;
  accounting_note: string;
  created_by_name: string;
  created_at: string;
  updated_at: string;
}

export interface WarrantyClaimPayload {
  order_id?: number;
  status: WarrantyClaimStatus;
  contact_channel: string;
  require_carrier_call: boolean;
  social_nickname: string;
  send_reason: string;
  send_method: string;
  classification: string;
  recall_code: string;
  return_status: string;
  accounting_check: string;
  accounting_note: string;
}

export interface WarrantyClaimListParams {
  q?: string;
  status?: WarrantyClaimStatus | null;
  limit: number;
  offset: number;
}

export interface WarrantyClaimListResponse {
  results: WarrantyClaim[];
  total: number;
}
```

- [ ] **Step 2: Create the claim API module**

Create `frontend/src/lib/api/warrantyClaims.ts`:

```ts
import { fetchJson } from "@/services/http";
import type {
  WarrantyClaim,
  WarrantyClaimListParams,
  WarrantyClaimListResponse,
  WarrantyClaimPayload,
} from "@/features/warranty-claims/warranty-claim-types";

const BASE = "/api/warranty/claims";

export async function listWarrantyClaims(
  params: WarrantyClaimListParams,
): Promise<WarrantyClaimListResponse> {
  const q = new URLSearchParams({
    limit: String(params.limit),
    offset: String(params.offset),
  });
  if (params.q) q.set("q", params.q);
  if (params.status != null) q.set("status", String(params.status));
  return fetchJson(`${BASE}?${q.toString()}`);
}

export async function createWarrantyClaim(
  payload: WarrantyClaimPayload,
): Promise<WarrantyClaim> {
  return fetchJson(BASE, { method: "POST", body: JSON.stringify(payload) });
}

export async function updateWarrantyClaim(
  id: number,
  payload: Partial<WarrantyClaimPayload>,
): Promise<WarrantyClaim> {
  return fetchJson(`${BASE}/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export async function deleteWarrantyClaim(id: number): Promise<void> {
  await fetchJson(`${BASE}/${id}`, { method: "DELETE" });
}
```

- [ ] **Step 3: Add `searchOrders` to the orders API module**

In `frontend/src/lib/api/orders.ts`, add the import of the type at the top and append the function at the end of the file:

```ts
import type { OrderSearchResult } from "@/features/warranty-claims/warranty-claim-types";

export async function searchOrders(q: string): Promise<OrderSearchResult[]> {
  const params = new URLSearchParams({ q });
  return fetchJson(`/api/orders/search?${params.toString()}`);
}
```

> If `fetchJson` is not already imported in `orders.ts`, add `import { fetchJson } from "@/services/http";` (the file already imports from `@/api/typed` and `@/services/http` per existing patterns — verify and reuse the existing import).

- [ ] **Step 4: Type-check + commit**

```bash
cd frontend && npx tsc --noEmit
cd .. && git add frontend/src/features/warranty-claims/warranty-claim-types.ts frontend/src/lib/api/warrantyClaims.ts frontend/src/lib/api/orders.ts
git commit -m "feat(fe): add warranty claim + order search API layer"
```
Expected: tsc reports no new errors.

---

### Task 7: react-query hooks

**Files:**
- Create: `frontend/src/features/warranty-claims/queries.ts`

**Interfaces:**
- Consumes: API fns (Task 6).
- Produces hooks: `useWarrantyClaims(params)`, `useCreateWarrantyClaim()`, `useUpdateWarrantyClaim()`, `useDeleteWarrantyClaim()`, `useOrderSearch(q)`. Query key root `["warranty-claims"]`.

- [ ] **Step 1: Create the hooks**

Create `frontend/src/features/warranty-claims/queries.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createWarrantyClaim,
  deleteWarrantyClaim,
  listWarrantyClaims,
  updateWarrantyClaim,
} from "@/lib/api/warrantyClaims";
import { searchOrders } from "@/lib/api/orders";
import type {
  WarrantyClaimListParams,
  WarrantyClaimPayload,
} from "@/features/warranty-claims/warranty-claim-types";

export const warrantyClaimKeys = {
  all: ["warranty-claims"] as const,
  list: (params: WarrantyClaimListParams) => ["warranty-claims", "list", params] as const,
  search: (q: string) => ["warranty-claims", "order-search", q] as const,
};

export function useWarrantyClaims(params: WarrantyClaimListParams) {
  return useQuery({
    queryKey: warrantyClaimKeys.list(params),
    queryFn: () => listWarrantyClaims(params),
  });
}

export function useCreateWarrantyClaim() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: WarrantyClaimPayload) => createWarrantyClaim(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: warrantyClaimKeys.all }),
  });
}

export function useUpdateWarrantyClaim() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<WarrantyClaimPayload> }) =>
      updateWarrantyClaim(id, payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: warrantyClaimKeys.all }),
  });
}

export function useDeleteWarrantyClaim() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteWarrantyClaim(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: warrantyClaimKeys.all }),
  });
}

export function useOrderSearch(q: string) {
  return useQuery({
    queryKey: warrantyClaimKeys.search(q),
    queryFn: () => searchOrders(q),
    enabled: q.trim().length >= 2,
    staleTime: 30_000,
  });
}
```

- [ ] **Step 2: Type-check + commit**

```bash
cd frontend && npx tsc --noEmit
cd .. && git add frontend/src/features/warranty-claims/queries.ts
git commit -m "feat(fe): add warranty claim react-query hooks"
```

---

### Task 8: Route + section + menu + i18n wiring

**Files:**
- Modify: `frontend/src/lib/routes.ts:3-34` (AppSection) and `:39-71` (SECTION_TO_PATH)
- Modify: `frontend/src/app/modules.ts:62-67` (Sales sections)
- Modify: `frontend/src/app/routes.tsx` (imports + route)
- Modify: `frontend/src/app/routed-pages.tsx` (lazy import + route fn)
- Modify: `frontend/src/i18n/messages/topnav.ts` (vi + en label)

**Interfaces:**
- Produces: section key `"warranty"` typed in `AppSection`, path `/sales/warranty`, sidebar tab under Sales, route guarded by `Guard section="warranty"`, lazy `WarrantyClaimsRoute`.

- [ ] **Step 1: Add the section to the AppSection union + path map**

In `frontend/src/lib/routes.ts`, add `| "warranty"` to the `AppSection` union (after `"resale-tracking"`), and add to `SECTION_TO_PATH` (after the `"resale-tracking"` entry):

```ts
  "resale-tracking": "/sales/resale-tracking",
  warranty: "/sales/warranty",
```

- [ ] **Step 2: Add the sidebar tab**

In `frontend/src/app/modules.ts`, add to the Sales module `sections` array (after the `resale-tracking` entry). `ShieldCheck` is already imported:

```ts
      { key: "resale-tracking", labelKey: "topnav.tabs.resaleTracking", icon: PhoneCall },
      { key: "warranty", labelKey: "topnav.tabs.warranty", icon: ShieldCheck },
```

- [ ] **Step 3: Add the i18n labels**

In `frontend/src/i18n/messages/topnav.ts`, add to BOTH the vi block and the en block (next to the `resaleTracking` keys):

```ts
// vi block:
    "topnav.tabs.warranty": "Bảo hành",
// en block:
    "topnav.tabs.warranty": "Warranty",
```

- [ ] **Step 4: Add the lazy route component**

In `frontend/src/app/routed-pages.tsx`, add a lazy import near the other `lazy(() => import("@/pages/..."))` lines:

```ts
const WarrantyClaimsPage = lazy(() => import("@/pages/WarrantyClaimsPage"));
```

And add the exported route function near `ResaleTrackingRoute`:

```ts
export function WarrantyClaimsRoute() {
  return <WarrantyClaimsPage />;
}
```

- [ ] **Step 5: Register the route**

In `frontend/src/app/routes.tsx`, add `WarrantyClaimsRoute` to the import list from `@/app/routed-pages`, then add the route after the `sales/resale-tracking` block:

```tsx
      {
        path: "sales/warranty",
        element: (
          <Guard section="warranty">
            <WarrantyClaimsRoute />
          </Guard>
        ),
      },
```

- [ ] **Step 6: Create a placeholder page so the import resolves**

Create `frontend/src/pages/WarrantyClaimsPage.tsx` (temporary stub, replaced in Task 11):

```tsx
export default function WarrantyClaimsPage() {
  return <div className="p-4">Bảo hành</div>;
}
```

- [ ] **Step 7: Type-check + commit**

```bash
cd frontend && npx tsc --noEmit
cd .. && git add frontend/src/lib/routes.ts frontend/src/app/modules.ts frontend/src/app/routes.tsx frontend/src/app/routed-pages.tsx frontend/src/i18n/messages/topnav.ts frontend/src/pages/WarrantyClaimsPage.tsx
git commit -m "feat(fe): wire warranty section route and sidebar tab"
```

---

### Task 9: Order picker combobox

**Files:**
- Create: `frontend/src/features/warranty-claims/components/OrderPicker.tsx`

**Interfaces:**
- Consumes: `useOrderSearch` (Task 7), `OrderSearchResult` type.
- Produces: `OrderPicker` component with props `{ value: OrderSearchResult | null; onChange: (o: OrderSearchResult) => void; disabled?: boolean }`. Debounced (300ms) server search by phone/tracking code; outside-click closes; renders option label `"<name> · <phone> · VĐ <shipping_code>"`.

- [ ] **Step 1: Create the component**

Create `frontend/src/features/warranty-claims/components/OrderPicker.tsx`:

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { useOrderSearch } from "@/features/warranty-claims/queries";
import type { OrderSearchResult } from "@/features/warranty-claims/warranty-claim-types";

interface Props {
  value: OrderSearchResult | null;
  onChange: (order: OrderSearchResult) => void;
  disabled?: boolean;
}

function optionLabel(o: OrderSearchResult): string {
  const parts = [o.name || "(không tên)", o.phone].filter(Boolean);
  if (o.shipping_code) parts.push(`VĐ ${o.shipping_code}`);
  return parts.join(" · ");
}

export function OrderPicker({ value, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const id = setTimeout(() => setDebounced(text.trim()), 300);
    return () => clearTimeout(id);
  }, [text]);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const { data, isFetching } = useOrderSearch(debounced);
  const results = useMemo(() => data ?? [], [data]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="flex h-9 w-full items-center justify-between rounded-lg border border-input bg-background px-3 text-left text-sm focus:outline-hidden focus:ring-2 focus:ring-ring/50 disabled:opacity-60"
      >
        <span className={value ? "" : "text-muted-foreground"}>
          {value ? optionLabel(value) : "Tìm đơn theo SĐT / mã vận đơn..."}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full rounded-md border border-input bg-popover shadow-lg">
          <div className="flex items-center gap-2 border-b p-2">
            <input
              type="text"
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Nhập SĐT hoặc mã vận đơn..."
              className="h-8 w-full rounded border px-2 text-sm focus:outline-hidden"
            />
            {isFetching && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
          </div>
          <div className="max-h-64 overflow-auto">
            {debounced.length < 2 ? (
              <div className="px-3 py-2 text-sm text-muted-foreground">
                Nhập tối thiểu 2 ký tự để tìm.
              </div>
            ) : results.length === 0 && !isFetching ? (
              <div className="px-3 py-2 text-sm text-muted-foreground">Không tìm thấy đơn.</div>
            ) : (
              results.map((o) => (
                <button
                  key={o.id}
                  type="button"
                  className={cn(
                    "block w-full cursor-pointer px-3 py-2 text-left text-sm hover:bg-accent",
                    value?.id === o.id && "bg-accent",
                  )}
                  onClick={() => {
                    onChange(o);
                    setOpen(false);
                    setText("");
                  }}
                >
                  <div className="font-medium text-foreground">{optionLabel(o)}</div>
                  <div className="text-xs text-muted-foreground">
                    {o.product} · {o.sku}
                  </div>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Type-check + commit**

```bash
cd frontend && npx tsc --noEmit
cd .. && git add frontend/src/features/warranty-claims/components/OrderPicker.tsx
git commit -m "feat(fe): add order search combobox for warranty claims"
```

---

### Task 10: Warranty claim form dialog

**Files:**
- Create: `frontend/src/features/warranty-claims/components/WarrantyClaimFormDialog.tsx`

**Interfaces:**
- Consumes: `FormDialogShell`, `FormGrid`, `Dialog`, `Input`, `Switch`, `Button`, native `select`, `OrderPicker`, types + `WARRANTY_STATUS_OPTIONS`.
- Produces: `WarrantyClaimFormDialog` with props `{ open, onOpenChange, claim?: WarrantyClaim | null, isPending, onSubmit: (payload: WarrantyClaimPayload) => void }`. Create mode shows `OrderPicker`; edit mode shows the locked order info card (order not changeable). Renders an order info card once an order is selected/loaded.

- [ ] **Step 1: Create the dialog**

Create `frontend/src/features/warranty-claims/components/WarrantyClaimFormDialog.tsx`:

```tsx
import { useEffect, useState } from "react";

import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { FormDialogShell, FormGrid } from "@/components/ui/form-dialog-content";

import { OrderPicker } from "./OrderPicker";
import {
  WARRANTY_STATUS_OPTIONS,
  type OrderSearchResult,
  type WarrantyClaim,
  type WarrantyClaimPayload,
  type WarrantyClaimStatus,
} from "@/features/warranty-claims/warranty-claim-types";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  claim?: WarrantyClaim | null;
  isPending: boolean;
  onSubmit: (payload: WarrantyClaimPayload) => void;
}

const labelClass = "mb-1 block text-sm font-medium text-muted-foreground";

function emptyForm(): Omit<WarrantyClaimPayload, "order_id"> {
  return {
    status: 1,
    contact_channel: "",
    require_carrier_call: false,
    social_nickname: "",
    send_reason: "",
    send_method: "",
    classification: "",
    recall_code: "",
    return_status: "",
    accounting_check: "",
    accounting_note: "",
  };
}

export function WarrantyClaimFormDialog({
  open,
  onOpenChange,
  claim,
  isPending,
  onSubmit,
}: Props) {
  const isEdit = Boolean(claim);
  const [order, setOrder] = useState<OrderSearchResult | null>(null);
  const [form, setForm] = useState(emptyForm());
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setError("");
    if (claim) {
      setOrder(claim.order);
      setForm({
        status: claim.status,
        contact_channel: claim.contact_channel,
        require_carrier_call: claim.require_carrier_call,
        social_nickname: claim.social_nickname,
        send_reason: claim.send_reason,
        send_method: claim.send_method,
        classification: claim.classification,
        recall_code: claim.recall_code,
        return_status: claim.return_status,
        accounting_check: claim.accounting_check,
        accounting_note: claim.accounting_note,
      });
    } else {
      setOrder(null);
      setForm(emptyForm());
    }
  }, [open, claim]);

  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const handleSubmit = () => {
    if (isPending) return;
    if (!order) {
      setError("Vui lòng chọn đơn hàng.");
      return;
    }
    onSubmit({ ...form, order_id: order.id });
  };

  const money = (v: string) => {
    const n = Number(v);
    return Number.isFinite(n) ? n.toLocaleString("vi-VN") + " ₫" : v;
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <FormDialogShell
        title={isEdit ? "Sửa phiếu bảo hành" : "Thêm bảo hành"}
        footer={
          <>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Hủy
            </Button>
            <Button type="button" disabled={isPending} onClick={handleSubmit}>
              {isPending ? "Đang lưu..." : isEdit ? "Lưu" : "Tạo"}
            </Button>
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSubmit();
          }}
        >
          {/* Chọn đơn (chỉ khi tạo) */}
          {!isEdit && (
            <div className="mb-4">
              <label className={labelClass}>Đơn hàng</label>
              <OrderPicker value={order} onChange={setOrder} />
            </div>
          )}

          {/* Thông tin đơn (read-only, live) */}
          {order && (
            <div className="mb-4 rounded-lg border border-border bg-muted/30 p-3 text-sm">
              <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                <div><span className="text-muted-foreground">Khách: </span>{order.name}</div>
                <div><span className="text-muted-foreground">SĐT: </span>{order.phone}</div>
                <div className="sm:col-span-2">
                  <span className="text-muted-foreground">Địa chỉ: </span>{order.address}
                </div>
                <div><span className="text-muted-foreground">SP/SKU: </span>{order.product} ({order.sku})</div>
                <div><span className="text-muted-foreground">Số tiền: </span>{money(order.total_after_discount)}</div>
                <div><span className="text-muted-foreground">Mã vận đơn: </span>{order.shipping_code || "—"}</div>
                <div><span className="text-muted-foreground">Mã đơn: </span>{order.order_code || "—"}</div>
              </div>
            </div>
          )}

          <FormGrid>
            <div>
              <label className={labelClass}>Trạng thái</label>
              <select
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm"
                value={form.status}
                onChange={(e) => set("status", Number(e.target.value) as WarrantyClaimStatus)}
              >
                {WARRANTY_STATUS_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelClass}>Kênh liên hệ</label>
              <Input value={form.contact_channel} onChange={(e) => set("contact_channel", e.target.value)} />
            </div>
            <div>
              <label className={labelClass}>Nick Shopee/TikTok/FB</label>
              <Input value={form.social_nickname} onChange={(e) => set("social_nickname", e.target.value)} />
            </div>
            <div>
              <label className={labelClass}>Hình thức gửi</label>
              <Input value={form.send_method} onChange={(e) => set("send_method", e.target.value)} />
            </div>
            <div>
              <label className={labelClass}>Phân loại</label>
              <Input value={form.classification} onChange={(e) => set("classification", e.target.value)} />
            </div>
            <div>
              <label className={labelClass}>Mã thu hồi</label>
              <Input value={form.recall_code} onChange={(e) => set("recall_code", e.target.value)} />
            </div>
            <div>
              <label className={labelClass}>Tình trạng đơn hoàn</label>
              <Input value={form.return_status} onChange={(e) => set("return_status", e.target.value)} />
            </div>
            <div>
              <label className={labelClass}>Kế toán check</label>
              <Input value={form.accounting_check} onChange={(e) => set("accounting_check", e.target.value)} />
            </div>
            <div className="flex items-center gap-2 pt-6">
              <Switch
                id="require-carrier-call"
                checked={form.require_carrier_call}
                onCheckedChange={(v) => set("require_carrier_call", v)}
              />
              <label htmlFor="require-carrier-call" className="text-sm font-medium text-foreground">
                YC vận đơn gọi xác nhận
              </label>
            </div>
            <div className="lg:col-span-2">
              <label className={labelClass}>Lí do gửi</label>
              <textarea
                className="min-h-[72px] w-full rounded-lg border border-input bg-background px-3 py-2 text-sm"
                value={form.send_reason}
                onChange={(e) => set("send_reason", e.target.value)}
              />
            </div>
            <div className="lg:col-span-2">
              <label className={labelClass}>Kế toán note</label>
              <textarea
                className="min-h-[72px] w-full rounded-lg border border-input bg-background px-3 py-2 text-sm"
                value={form.accounting_note}
                onChange={(e) => set("accounting_note", e.target.value)}
              />
            </div>
          </FormGrid>
          {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
          <button type="submit" className="hidden" aria-hidden tabIndex={-1} />
        </form>
      </FormDialogShell>
    </Dialog>
  );
}
```

- [ ] **Step 2: Type-check + commit**

```bash
cd frontend && npx tsc --noEmit
cd .. && git add frontend/src/features/warranty-claims/components/WarrantyClaimFormDialog.tsx
git commit -m "feat(fe): add warranty claim form dialog"
```

---

### Task 11: Warranty claims list page

**Files:**
- Modify (replace stub): `frontend/src/pages/WarrantyClaimsPage.tsx`

**Interfaces:**
- Consumes: `useWarrantyClaims`, `useCreateWarrantyClaim`, `useUpdateWarrantyClaim`, `useDeleteWarrantyClaim` (Task 7); `WarrantyClaimFormDialog` (Task 10); `DataTableShell`, `Table*`, `Badge`, `Button`, `Input`, `ResponsiveList`, `sonner`.
- Produces: default-exported page with status filter + search box + "Thêm bảo hành" button + table/card list + edit/delete actions.

- [ ] **Step 1: Replace the page**

Replace `frontend/src/pages/WarrantyClaimsPage.tsx` with:

```tsx
import { useMemo, useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataTableShell } from "@/components/ui/data-table-shell";
import { Input } from "@/components/ui/input";
import { ResponsiveList } from "@/components/ui/responsive-list";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { WarrantyClaimFormDialog } from "@/features/warranty-claims/components/WarrantyClaimFormDialog";
import {
  useCreateWarrantyClaim,
  useDeleteWarrantyClaim,
  useUpdateWarrantyClaim,
  useWarrantyClaims,
} from "@/features/warranty-claims/queries";
import {
  WARRANTY_STATUS_OPTIONS,
  type WarrantyClaim,
  type WarrantyClaimPayload,
  type WarrantyClaimStatus,
} from "@/features/warranty-claims/warranty-claim-types";

const PAGE_SIZE = 50;
const COLUMN_COUNT = 7;

const STATUS_BADGE: Record<WarrantyClaimStatus, string> = {
  1: "bg-blue-100 text-blue-700",
  2: "bg-amber-100 text-amber-700",
  3: "bg-green-100 text-green-700",
  4: "bg-gray-200 text-gray-600",
};

export default function WarrantyClaimsPage() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<WarrantyClaimStatus | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<WarrantyClaim | null>(null);

  const params = useMemo(
    () => ({ q: q.trim() || undefined, status, limit: PAGE_SIZE, offset: 0 }),
    [q, status],
  );
  const { data, isLoading } = useWarrantyClaims(params);
  const items = data?.results ?? [];

  const createMutation = useCreateWarrantyClaim();
  const updateMutation = useUpdateWarrantyClaim();
  const deleteMutation = useDeleteWarrantyClaim();

  const headerClass =
    "h-11 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground";

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };
  const openEdit = (row: WarrantyClaim) => {
    setEditing(row);
    setDialogOpen(true);
  };

  const handleSubmit = (payload: WarrantyClaimPayload) => {
    const onError = (err: unknown) =>
      toast.error(err instanceof Error ? err.message : "Thao tác thất bại.");
    if (editing) {
      updateMutation.mutate(
        { id: editing.id, payload },
        {
          onSuccess: () => {
            toast.success("Đã cập nhật phiếu bảo hành.");
            setDialogOpen(false);
          },
          onError,
        },
      );
    } else {
      createMutation.mutate(payload, {
        onSuccess: () => {
          toast.success("Đã tạo phiếu bảo hành.");
          setDialogOpen(false);
        },
        onError,
      });
    }
  };

  const handleDelete = (row: WarrantyClaim) => {
    if (!window.confirm("Xóa phiếu bảo hành này?")) return;
    deleteMutation.mutate(row.id, {
      onSuccess: () => toast.success("Đã xóa phiếu bảo hành."),
      onError: (err) => toast.error(err instanceof Error ? err.message : "Thao tác thất bại."),
    });
  };

  const statusBadge = (row: WarrantyClaim) => (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[row.status]}`}
    >
      {row.status_label}
    </span>
  );

  const toolbar = (
    <div className="flex w-full flex-wrap items-center gap-2">
      <Input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Tìm theo SĐT / mã vận đơn / tên..."
        className="h-9 w-full max-w-xs"
      />
      <select
        className="h-9 rounded-lg border border-input bg-background px-3 text-sm"
        value={status ?? ""}
        onChange={(e) => setStatus(e.target.value ? (Number(e.target.value) as WarrantyClaimStatus) : null)}
      >
        <option value="">Tất cả trạng thái</option>
        {WARRANTY_STATUS_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      <Button
        className="ml-auto bg-primary text-primary-foreground hover:bg-primary/90"
        onClick={openCreate}
      >
        <Plus className="mr-2 h-4 w-4" />
        Thêm bảo hành
      </Button>
    </div>
  );

  const actions = (row: WarrantyClaim) => (
    <>
      <Button
        title="Sửa"
        aria-label="Sửa"
        size="icon-sm"
        variant="outline"
        className="border-border bg-background text-primary hover:bg-accent"
        onClick={() => openEdit(row)}
      >
        <Pencil className="h-4 w-4" />
      </Button>
      <Button
        title="Xóa"
        aria-label="Xóa"
        size="icon-sm"
        variant="outline"
        className="ml-1 border-border bg-background text-destructive hover:bg-accent"
        disabled={deleteMutation.isPending}
        onClick={() => handleDelete(row)}
      >
        <Trash2 className="h-4 w-4" />
      </Button>
    </>
  );

  const table = (
    <Table className="min-w-[860px] [&_td]:px-3 [&_th]:px-3">
      <TableHeader>
        <TableRow className="border-b border-border bg-muted/40 hover:bg-muted/40">
          <TableHead className={headerClass}>Khách hàng</TableHead>
          <TableHead className={headerClass}>SĐT</TableHead>
          <TableHead className={headerClass}>Mã vận đơn</TableHead>
          <TableHead className={headerClass}>Phân loại</TableHead>
          <TableHead className={headerClass}>Lí do gửi</TableHead>
          <TableHead className={headerClass}>Trạng thái</TableHead>
          <TableHead className={`${headerClass} text-right`}>Hành động</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {isLoading && items.length === 0 ? (
          <TableRow>
            <TableCell colSpan={COLUMN_COUNT} className="py-8 text-center text-muted-foreground">
              Đang tải...
            </TableCell>
          </TableRow>
        ) : items.length === 0 ? (
          <TableRow>
            <TableCell colSpan={COLUMN_COUNT} className="py-8 text-center text-muted-foreground">
              Chưa có phiếu bảo hành nào.
            </TableCell>
          </TableRow>
        ) : (
          items.map((row) => (
            <TableRow key={row.id} className="border-b border-border/70 hover:bg-muted/30">
              <TableCell className="font-medium text-foreground">{row.order.name}</TableCell>
              <TableCell className="text-muted-foreground">{row.order.phone}</TableCell>
              <TableCell className="text-muted-foreground">{row.order.shipping_code || "—"}</TableCell>
              <TableCell className="text-muted-foreground">{row.classification || "—"}</TableCell>
              <TableCell className="max-w-[220px] truncate text-muted-foreground">{row.send_reason || "—"}</TableCell>
              <TableCell>{statusBadge(row)}</TableCell>
              <TableCell className="whitespace-nowrap text-right">{actions(row)}</TableCell>
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  );

  return (
    <>
      <DataTableShell toolbar={toolbar}>
        <ResponsiveList
          items={items}
          table={table}
          getKey={(it) => it.id}
          emptyText="Chưa có phiếu bảo hành nào."
          renderCard={(row) => (
            <div className="rounded-lg border border-border bg-card p-3 text-sm shadow-sm">
              <div className="flex items-center justify-between">
                <span className="font-medium text-foreground">{row.order.name}</span>
                {statusBadge(row)}
              </div>
              <div className="mt-1 text-muted-foreground">{row.order.phone}</div>
              <div className="text-muted-foreground">VĐ: {row.order.shipping_code || "—"}</div>
              {row.send_reason && <div className="mt-1 text-muted-foreground">Lí do: {row.send_reason}</div>}
              <div className="mt-2 flex justify-end">{actions(row)}</div>
            </div>
          )}
        />
      </DataTableShell>

      <WarrantyClaimFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        claim={editing}
        isPending={createMutation.isPending || updateMutation.isPending}
        onSubmit={handleSubmit}
      />
    </>
  );
}
```

- [ ] **Step 2: Type-check + build + commit**

```bash
cd frontend && npx tsc --noEmit && npm run build
cd .. && git add frontend/src/pages/WarrantyClaimsPage.tsx
git commit -m "feat(fe): build warranty claims list page"
```
Expected: tsc + build succeed.

---

# SPRINT 3 — Finishing

### Task 12: FE smoke test + QA + doc update

**Files:**
- Create: `frontend/src/features/warranty-claims/__tests__/WarrantyClaimsPage.test.tsx`
- Modify: `docs/sales-warranty-claims.md` (progress notes)

**Interfaces:** none (verification + docs).

- [ ] **Step 1: Write a smoke test**

Check the existing FE test setup first (look at `frontend/src/components/ui/__tests__/responsive-list.test.tsx` for the test runner + render helpers — it uses vitest + @testing-library). Create `frontend/src/features/warranty-claims/__tests__/WarrantyClaimsPage.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import WarrantyClaimsPage from "@/pages/WarrantyClaimsPage";

vi.mock("@/lib/api/warrantyClaims", () => ({
  listWarrantyClaims: vi.fn().mockResolvedValue({ results: [], total: 0 }),
  createWarrantyClaim: vi.fn(),
  updateWarrantyClaim: vi.fn(),
  deleteWarrantyClaim: vi.fn(),
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <WarrantyClaimsPage />
    </QueryClientProvider>,
  );
}

describe("WarrantyClaimsPage", () => {
  it("renders the add button and empty state", async () => {
    renderPage();
    expect(screen.getByText("Thêm bảo hành")).toBeInTheDocument();
    expect(await screen.findByText("Chưa có phiếu bảo hành nào.")).toBeInTheDocument();
  });
});
```

> Adjust the mock/render helper to match the repo's actual vitest config and any global providers (e.g. an i18n provider) discovered in the reference test. If the page needs an i18n provider, wrap it accordingly.

- [ ] **Step 2: Run the FE test**

```bash
cd frontend && npx vitest run src/features/warranty-claims
```
Expected: PASS.

- [ ] **Step 3: Manual QA checklist (light theme, blue)**

Run the app, log in as a user with the `warranty` section, then verify:
- Sidebar shows "Bảo hành" under Sales; clicking opens `/sales/warranty`.
- "Thêm bảo hành" opens the dialog; the order picker returns results when typing a phone/tracking code; selecting fills the read-only order card.
- Saving creates a row; status badge renders; edit pre-fills; delete removes the row.
- A user without `warranty` permission cannot see the tab and is blocked from `/sales/warranty`.

- [ ] **Step 4: Update progress notes + commit**

In `docs/sales-warranty-claims.md` section 8, record what shipped (model/migration, RBAC, CRUD + search endpoints, FE page/dialog/picker, tests) and anything deferred.

```bash
git add frontend/src/features/warranty-claims/__tests__/ docs/sales-warranty-claims.md
git commit -m "test(fe): add warranty claims page smoke test; update docs"
```

- [ ] **Step 5: Push + open MR**

```bash
git push -u origin feat/sales-warranty-claims
```
Then create the merge request and share the link.

---

## Self-Review notes

- **Spec coverage:** model (Task 1) ✓; RBAC section (Task 2) ✓; FK + live order info via nested serializer (Task 3) ✓; OneToOne one-claim-per-order (Tasks 1,4) ✓; CRUD + soft delete (Task 4) ✓; status enum + filter (Tasks 1,4,11) ✓; order search endpoint + combobox (Tasks 5,9) ✓; route/menu/i18n (Task 8) ✓; dialog with all listed fields (Task 10) ✓; list page (Task 11) ✓; tests BE+FE (Tasks 1–5,12) ✓; migration (Task 1) ✓.
- All listed warranty fields (kênh liên hệ, YC gọi xác nhận, nick MXH, lí do gửi, hình thức gửi, phân loại, mã thu hồi, tình trạng đơn hoàn, kế toán check, kế toán note) appear in model, serializer, dialog.
- Public `POST /api/warranty/` is untouched; claims live at `/api/warranty/claims`.
```
