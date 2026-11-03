# Inventory Sprint 1.1 Implementation Plan — Stock Ledger Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Dựng nền tảng tồn kho: app `apps/inventory` với sổ kho immutable (`StockMovement`), số dư projection (`StockItem`), primitive ghi movement atomic, API xem tồn/lịch sử, và trang FE "Tồn kho" gắn vào app-shell.

**Architecture:** App Django mới theo clean-architecture (như `apps/orders`). Sổ kho là nguồn chân lý; `StockItem` là cache số dư cập nhật atomic cùng transaction (`select_for_update`). FK tới `warehouses.Warehouse` (cùng module) và `orders.Product` (thoả hiệp ranh giới có chủ đích — spec §2.2). Đọc qua query service; ghi qua command primitive (document/sale ở sprint sau).

**Tech Stack:** Django 6 + DRF + PostgreSQL; FE React 18 + Vite + TanStack Query + ag-grid.

**Spec:** `docs/erp-inventory.md` (§3, §4.1, §6, §7). Chuẩn: `docs/architecture-standard.md`.

**Chạy test BE** (memory `running-backend-tests`): ephemeral docker —
```bash
docker inspect desi-web -f '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^[A-Z_]+=' | grep -vE '^PATH=|^LANG=|^GPG|^PYTHON' > /tmp/web.env
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd)/backend:/app" -w /app desi-order-web \
  sh -c 'mkdir -p /tmp/prom_multiproc && rm -f /tmp/prom_multiproc/* && python -m pytest <paths> --reuse-db -q'
```
Ruff (host): `uv run --no-project --with ruff ruff check <files>`. FE từ `frontend/`: `npx tsc --noEmit`, `npx vitest run <file>`.

---

## File Structure
| File | Trách nhiệm |
|---|---|
| `backend/apps/inventory/__init__.py`, `apps.py` | App config. |
| `backend/config/settings_parts/base.py` | (Sửa) thêm `"apps.inventory"` vào INSTALLED_APPS. |
| `backend/config/urls.py` | (Sửa) include `apps.inventory.urls` tại `^api/inventory/`. |
| `backend/apps/users/models.py` | (Sửa) thêm `"stock"` vào `VALID_SECTIONS`. |
| `backend/apps/inventory/models.py` | `MovementType`, `StockMovement`, `StockItem`. |
| `backend/apps/inventory/migrations/0001_initial.py` | Migration (sinh tự động). |
| `backend/apps/inventory/repositories.py` | `StockItemRepository`, `StockMovementRepository`. |
| `backend/apps/inventory/application/services/stock_command_service.py` | `record_movement` primitive. |
| `backend/apps/inventory/application/services/stock_query_service.py` | `list_stock`, `list_movements`. |
| `backend/apps/inventory/transport/serializers/stock_serializers.py` | Output serializers. |
| `backend/apps/inventory/transport/views/stock_views.py` | GET stock / movements. |
| `backend/apps/inventory/urls.py` | Routes. |
| `backend/apps/inventory/management/commands/rebuild_stock_items.py` | Đối soát số dư từ sổ. |
| `backend/apps/inventory/tests/` | Unit tests. |
| `frontend/src/lib/routes.ts` | (Sửa) thêm section `stock`. |
| `frontend/src/app/modules.ts` | (Sửa) thêm section `stock` vào module inventory. |
| `frontend/src/app/routes.tsx` + `routed-pages.tsx` | (Sửa) route `/inventory/stock`. |
| `frontend/src/features/inventory/` | `StockPage`, `queries.ts`, `types.ts`. |
| `frontend/src/i18n/messages/*` | Nhãn `topnav.tabs.stock`. |

Mọi lệnh BE từ repo root; FE từ `frontend/`. Commit message tiếng Anh.

---

## Task 1: Scaffold app `apps/inventory` + đăng ký

**Files:** Create `backend/apps/inventory/__init__.py`, `backend/apps/inventory/apps.py`, `backend/apps/inventory/urls.py` (tạm rỗng), `backend/apps/inventory/management/__init__.py`, `backend/apps/inventory/management/commands/__init__.py`, `backend/apps/inventory/tests/__init__.py`. Modify settings + config/urls + users/models.

- [ ] **Step 1: Tạo app config**

`backend/apps/inventory/__init__.py`: file rỗng.

`backend/apps/inventory/apps.py`:
```python
from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventory"
```

`backend/apps/inventory/urls.py` (sẽ điền ở Task 6):
```python
from django.urls import path

urlpatterns: list[path] = []
```
Tạo các `__init__.py` rỗng cho `management/`, `management/commands/`, `tests/`.

- [ ] **Step 2: Đăng ký INSTALLED_APPS**

Trong `backend/config/settings_parts/base.py`, sau dòng `"apps.warehouses",` thêm:
```python
    "apps.inventory",
```

- [ ] **Step 3: Include URL**

Trong `backend/config/urls.py`, sau dòng warehouses include, thêm:
```python
    re_path(r"^api/inventory/?", include("apps.inventory.urls")),
```

- [ ] **Step 4: Đăng ký section quyền**

Trong `backend/apps/users/models.py`, trong list `VALID_SECTIONS`, sau `"warehouses",` thêm:
```python
    "stock",
```

- [ ] **Step 5: Kiểm tra Django nhận app**

Run (host hoặc docker): kiểm Django load không lỗi —
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env -v "$(pwd)/backend:/app" -w /app desi-order-web python -c "import django; django.setup(); from django.apps import apps; print('inventory' in [a.label for a in apps.get_app_configs()])"
```
(Nếu `/tmp/web.env` chưa có, tạo bằng lệnh ở đầu plan.) Expected: in ra `True`.

- [ ] **Step 6: Commit**
```bash
git add backend/apps/inventory backend/config/settings_parts/base.py backend/config/urls.py backend/apps/users/models.py
git commit -m "feat(inventory): scaffold app and register stock section"
```

---

## Task 2: Models `StockMovement` + `StockItem` + migration

**Files:** Create `backend/apps/inventory/models.py`; generate migration.

- [ ] **Step 1: Viết models**

`backend/apps/inventory/models.py`:
```python
from __future__ import annotations

from django.conf import settings
from django.db import models


class MovementType(models.TextChoices):
    RECEIPT = "receipt", "Nhập kho"
    ISSUE = "issue", "Xuất kho"
    ADJUSTMENT = "adjustment", "Điều chỉnh"
    TRANSFER_IN = "transfer_in", "Chuyển đến"
    TRANSFER_OUT = "transfer_out", "Chuyển đi"
    SALE = "sale", "Bán hàng"
    SALE_REVERSAL = "sale_reversal", "Hoàn bán"


class StockMovement(models.Model):
    """Immutable stock ledger — the source of truth. Append-only: never updated
    or deleted; corrections are recorded as reversing movements."""

    warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT, related_name="stock_movements"
    )
    product = models.ForeignKey(
        "orders.Product", on_delete=models.PROTECT, related_name="stock_movements"
    )
    qty_delta = models.IntegerField()
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    balance_after = models.IntegerField()
    reference_type = models.CharField(max_length=20, null=True, blank=True)
    reference_id = models.BigIntegerField(null=True, blank=True)
    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stock_movements"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["warehouse", "product", "created_at"], name="stockmove_wh_prod_idx"
            ),
            models.Index(fields=["reference_type", "reference_id"], name="stockmove_ref_idx"),
        ]

    def __str__(self):
        return f"{self.movement_type} {self.qty_delta:+d} w{self.warehouse_id} p{self.product_id}"


class StockItem(models.Model):
    """Current on-hand balance per (warehouse, product). A projection of the
    ledger, updated atomically with each movement. May go negative (overselling
    is allowed with a warning — see spec §4.2)."""

    warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT, related_name="stock_items"
    )
    product = models.ForeignKey(
        "orders.Product", on_delete=models.PROTECT, related_name="stock_items"
    )
    quantity = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "stock_items"
        constraints = [
            models.UniqueConstraint(
                fields=["warehouse", "product"], name="uniq_stock_item_wh_product"
            )
        ]
        indexes = [
            models.Index(fields=["warehouse"], name="stockitem_wh_idx"),
            models.Index(fields=["product"], name="stockitem_prod_idx"),
        ]

    def __str__(self):
        return f"w{self.warehouse_id} p{self.product_id} = {self.quantity}"
```

- [ ] **Step 2: Sinh migration**
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env -v "$(pwd)/backend:/app" -w /app desi-order-web python manage.py makemigrations inventory
```
Expected: tạo `backend/apps/inventory/migrations/0001_initial.py` (+ `migrations/__init__.py`). Đọc file sinh ra để chắc đúng (2 model, index, unique constraint).

- [ ] **Step 3: Kiểm migration áp được (test DB)**
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" -v "$(pwd)/backend:/app" -w /app desi-order-web sh -c 'mkdir -p /tmp/prom_multiproc && python manage.py migrate inventory --plan'
```
Expected: liệt kê `inventory.0001_initial` không lỗi.

- [ ] **Step 4: Ruff + commit**
```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/models.py
git add backend/apps/inventory/models.py backend/apps/inventory/migrations
git commit -m "feat(inventory): add StockMovement and StockItem models"
```

---

## Task 3: Repositories

**Files:** Create `backend/apps/inventory/repositories.py`.

- [ ] **Step 1: Viết repositories**
```python
from __future__ import annotations

from django.db.models import QuerySet

from apps.inventory.models import StockItem, StockMovement


class StockItemRepository:
    @staticmethod
    def base() -> QuerySet[StockItem]:
        return StockItem.objects.select_related("warehouse", "product")

    @staticmethod
    def for_warehouse_product(warehouse_id: int, product_id: int) -> StockItem | None:
        return StockItem.objects.filter(
            warehouse_id=warehouse_id, product_id=product_id
        ).first()


class StockMovementRepository:
    @staticmethod
    def base() -> QuerySet[StockMovement]:
        return StockMovement.objects.select_related("warehouse", "product", "created_by")
```
(Không kế thừa `BaseRepository` vì stock không soft-delete; query thuần.)

- [ ] **Step 2: Ruff + commit**
```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/repositories.py
git add backend/apps/inventory/repositories.py
git commit -m "feat(inventory): add stock repositories"
```

---

## Task 4: `StockCommandService.record_movement` primitive (TDD)

**Files:** Create `backend/apps/inventory/application/__init__.py`, `application/services/__init__.py`, `application/services/stock_command_service.py`; test `backend/apps/inventory/tests/test_stock_command_service.py`.

- [ ] **Step 1: Viết test thất bại**

`backend/apps/inventory/tests/test_stock_command_service.py`:
```python
from __future__ import annotations

import pytest
from django.test import TestCase

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType, StockItem, StockMovement
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


class RecordMovementTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(code="W1", name="Kho 1")
        self.product = Product.objects.create(name="SP A", sku="A-1")

    def _record(self, qty_delta, mtype=MovementType.RECEIPT):
        return StockCommandService.record_movement(
            warehouse_id=self.wh.id,
            product_id=self.product.id,
            qty_delta=qty_delta,
            movement_type=mtype,
        )

    def test_first_movement_creates_item_and_sets_balance(self):
        result = self._record(10)
        item = StockItem.objects.get(warehouse=self.wh, product=self.product)
        assert item.quantity == 10
        assert result.balance_after == 10
        assert result.went_negative is False
        mv = StockMovement.objects.get(id=result.movement_id)
        assert mv.qty_delta == 10
        assert mv.balance_after == 10

    def test_movements_accumulate(self):
        self._record(10)
        result = self._record(-3, MovementType.ISSUE)
        item = StockItem.objects.get(warehouse=self.wh, product=self.product)
        assert item.quantity == 7
        assert result.balance_after == 7

    def test_balance_can_go_negative_and_flags_warning(self):
        result = self._record(-5, MovementType.ISSUE)
        item = StockItem.objects.get(warehouse=self.wh, product=self.product)
        assert item.quantity == -5
        assert result.balance_after == -5
        assert result.went_negative is True

    def test_zero_delta_rejected(self):
        with pytest.raises(ValueError):
            self._record(0)

    def test_balance_after_is_snapshot_history(self):
        self._record(10)
        self._record(5)
        balances = list(
            StockMovement.objects.filter(warehouse=self.wh, product=self.product)
            .order_by("id")
            .values_list("balance_after", flat=True)
        )
        assert balances == [10, 15]
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" -v "$(pwd)/backend:/app" -w /app desi-order-web sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_stock_command_service.py --reuse-db -q'
```
Expected: FAIL (service chưa tồn tại).

- [ ] **Step 3: Viết service**

`backend/apps/inventory/application/services/stock_command_service.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from apps.inventory.models import StockItem, StockMovement


@dataclass(frozen=True)
class MovementResult:
    movement_id: int
    balance_after: int
    went_negative: bool


class StockCommandService:
    """Low-level ledger primitive. All stock changes (documents, sales) go
    through `record_movement`, which appends an immutable movement and updates
    the `StockItem` balance atomically."""

    @staticmethod
    @transaction.atomic
    def record_movement(
        *,
        warehouse_id: int,
        product_id: int,
        qty_delta: int,
        movement_type: str,
        reference_type: str | None = None,
        reference_id: int | None = None,
        note: str = "",
        user_id: int | None = None,
    ) -> MovementResult:
        if qty_delta == 0:
            raise ValueError("qty_delta must be non-zero.")

        StockItem.objects.get_or_create(
            warehouse_id=warehouse_id, product_id=product_id, defaults={"quantity": 0}
        )
        item = StockItem.objects.select_for_update().get(
            warehouse_id=warehouse_id, product_id=product_id
        )
        new_balance = item.quantity + qty_delta
        movement = StockMovement.objects.create(
            warehouse_id=warehouse_id,
            product_id=product_id,
            qty_delta=qty_delta,
            movement_type=movement_type,
            balance_after=new_balance,
            reference_type=reference_type,
            reference_id=reference_id,
            note=note,
            created_by_id=user_id,
        )
        item.quantity = new_balance
        item.save(update_fields=["quantity", "updated_at"])
        return MovementResult(
            movement_id=movement.id, balance_after=new_balance, went_negative=new_balance < 0
        )
```
Tạo `application/__init__.py` và `application/services/__init__.py` rỗng.

- [ ] **Step 4: Chạy test, xác nhận PASS** (lệnh như Step 2). Expected: 5 passed.

- [ ] **Step 5: Ruff + commit**
```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/application backend/apps/inventory/tests/test_stock_command_service.py
git add backend/apps/inventory/application backend/apps/inventory/tests/test_stock_command_service.py
git commit -m "feat(inventory): add record_movement ledger primitive"
```

---

## Task 5: `StockQueryService` (TDD)

**Files:** Create `application/services/stock_query_service.py`; test `tests/test_stock_query_service.py`.

- [ ] **Step 1: Viết test thất bại**

`backend/apps/inventory/tests/test_stock_query_service.py`:
```python
from __future__ import annotations

from django.test import TestCase

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_query_service import StockQueryService
from apps.inventory.models import MovementType
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


class StockQueryTests(TestCase):
    def setUp(self):
        self.w1 = Warehouse.objects.create(code="W1", name="Kho 1")
        self.w2 = Warehouse.objects.create(code="W2", name="Kho 2")
        self.p1 = Product.objects.create(name="SP A", sku="A-1")
        self.p2 = Product.objects.create(name="SP B", sku="B-1")
        StockCommandService.record_movement(
            warehouse_id=self.w1.id, product_id=self.p1.id, qty_delta=10,
            movement_type=MovementType.RECEIPT)
        StockCommandService.record_movement(
            warehouse_id=self.w2.id, product_id=self.p2.id, qty_delta=4,
            movement_type=MovementType.RECEIPT)

    def test_list_stock_returns_all_items(self):
        qs = StockQueryService.list_stock()
        assert qs.count() == 2

    def test_list_stock_filtered_by_warehouse(self):
        qs = StockQueryService.list_stock(warehouse_id=self.w1.id)
        assert qs.count() == 1
        assert qs.first().product_id == self.p1.id

    def test_list_stock_filtered_by_sku_search(self):
        qs = StockQueryService.list_stock(search="A-1")
        assert qs.count() == 1
        assert qs.first().product_id == self.p1.id

    def test_list_movements_filtered_by_warehouse_product(self):
        qs = StockQueryService.list_movements(warehouse_id=self.w1.id, product_id=self.p1.id)
        assert qs.count() == 1
        assert qs.first().qty_delta == 10
```

- [ ] **Step 2: Chạy test → FAIL** (đổi path test cho phù hợp).

- [ ] **Step 3: Viết service**

`backend/apps/inventory/application/services/stock_query_service.py`:
```python
from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.inventory.models import StockItem, StockMovement
from apps.inventory.repositories import StockItemRepository, StockMovementRepository


class StockQueryService:
    @staticmethod
    def list_stock(
        *, warehouse_id: int | None = None, search: str = ""
    ) -> QuerySet[StockItem]:
        qs = StockItemRepository.base()
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        search = (search or "").strip()
        if search:
            qs = qs.filter(Q(product__sku__icontains=search) | Q(product__name__icontains=search))
        return qs.order_by("product__sku", "warehouse__name", "id")

    @staticmethod
    def list_movements(
        *, warehouse_id: int | None = None, product_id: int | None = None
    ) -> QuerySet[StockMovement]:
        qs = StockMovementRepository.base()
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        if product_id:
            qs = qs.filter(product_id=product_id)
        return qs.order_by("-created_at", "-id")
```

- [ ] **Step 4: Chạy test → PASS** (4 passed).

- [ ] **Step 5: Ruff + commit**
```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/application/services/stock_query_service.py backend/apps/inventory/tests/test_stock_query_service.py
git add backend/apps/inventory/application/services/stock_query_service.py backend/apps/inventory/tests/test_stock_query_service.py
git commit -m "feat(inventory): add stock query service"
```

---

## Task 6: API — views, serializers, urls (TDD)

**Files:** Create `transport/__init__.py`, `transport/serializers/__init__.py`, `transport/serializers/stock_serializers.py`, `transport/views/__init__.py`, `transport/views/stock_views.py`; edit `urls.py`; test `tests/test_stock_api.py`.

- [ ] **Step 1: Serializers**

`backend/apps/inventory/transport/serializers/stock_serializers.py`:
```python
from __future__ import annotations

from rest_framework import serializers

from apps.inventory.models import StockItem, StockMovement


class StockItemSerializer(serializers.ModelSerializer):
    warehouse_code = serializers.CharField(source="warehouse.code", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockItem
        fields = [
            "id", "warehouse", "warehouse_code", "warehouse_name",
            "product", "product_sku", "product_name", "quantity", "updated_at",
        ]


class StockMovementSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            "id", "warehouse", "warehouse_name", "product", "product_sku",
            "qty_delta", "movement_type", "balance_after", "reference_type",
            "reference_id", "note", "created_at",
        ]
```

- [ ] **Step 2: Views**

`backend/apps/inventory/transport/views/stock_views.py`:
```python
from __future__ import annotations

from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.inventory.application.services.stock_query_service import StockQueryService
from apps.inventory.transport.serializers.stock_serializers import (
    StockItemSerializer,
    StockMovementSerializer,
)
from apps.users.permissions import require_section

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def list_stock(request):
    pagination, error_response = parse_required_pagination(
        request, max_limit=_MAX_LIMIT, default_limit=_DEFAULT_LIMIT
    )
    if error_response is not None:
        return error_response
    qs = StockQueryService.list_stock(
        warehouse_id=_int_or_none(request.query_params.get("warehouse_id")),
        search=request.query_params.get("search", ""),
    )
    total = qs.count()
    items = qs[pagination["offset"] : pagination["offset"] + pagination["limit"]]
    return Response(
        {
            "items": StockItemSerializer(items, many=True).data,
            "total": total,
            "limit": pagination["limit"],
            "offset": pagination["offset"],
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def list_movements(request):
    pagination, error_response = parse_required_pagination(
        request, max_limit=_MAX_LIMIT, default_limit=_DEFAULT_LIMIT
    )
    if error_response is not None:
        return error_response
    qs = StockQueryService.list_movements(
        warehouse_id=_int_or_none(request.query_params.get("warehouse_id")),
        product_id=_int_or_none(request.query_params.get("product_id")),
    )
    total = qs.count()
    items = qs[pagination["offset"] : pagination["offset"] + pagination["limit"]]
    return Response(
        {
            "items": StockMovementSerializer(items, many=True).data,
            "total": total,
            "limit": pagination["limit"],
            "offset": pagination["offset"],
        },
        status=status.HTTP_200_OK,
    )
```

- [ ] **Step 3: URLs**

`backend/apps/inventory/urls.py`:
```python
from django.urls import path

from apps.inventory.transport.views.stock_views import list_movements, list_stock

urlpatterns = [
    path("stock", list_stock, name="inventory-stock-list"),
    path("stock/movements", list_movements, name="inventory-stock-movements"),
]
```
Tạo các `__init__.py` rỗng cho `transport/`, `transport/serializers/`, `transport/views/`.

- [ ] **Step 4: Test API**

`backend/apps/inventory/tests/test_stock_api.py`:
```python
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType
from apps.orders.models import Product
from apps.users.models import User
from apps.warehouses.models import Warehouse


class StockApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="admin", password="x", role="admin")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="W1", name="Kho 1")
        self.product = Product.objects.create(name="SP A", sku="A-1")
        StockCommandService.record_movement(
            warehouse_id=self.wh.id, product_id=self.product.id, qty_delta=10,
            movement_type=MovementType.RECEIPT)

    def test_list_stock_returns_balance(self):
        resp = self.client.get("/api/inventory/stock", {"limit": 50, "offset": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["quantity"] == 10
        assert body["items"][0]["product_sku"] == "A-1"

    def test_list_movements_returns_history(self):
        resp = self.client.get(
            "/api/inventory/stock/movements",
            {"limit": 50, "offset": 0, "warehouse_id": self.wh.id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["balance_after"] == 10
```
> Lưu ý: kiểm chữ ký `User.objects.create_user` thực tế (đọc `apps/users/models.py`); nếu khác (vd cần `display_name` hoặc không nhận `role`), chỉnh cho khớp. Mục tiêu: tạo user admin có quyền section "stock".

- [ ] **Step 5: Chạy toàn bộ test inventory → PASS**
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" -v "$(pwd)/backend:/app" -w /app desi-order-web sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory --reuse-db -q'
```
Expected: tất cả pass.

- [ ] **Step 6: Ruff + commit**
```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/transport backend/apps/inventory/urls.py backend/apps/inventory/tests/test_stock_api.py
git add backend/apps/inventory/transport backend/apps/inventory/urls.py backend/apps/inventory/tests/test_stock_api.py
git commit -m "feat(inventory): add stock read API"
```

---

## Task 7: Management command `rebuild_stock_items` (đối soát — TDD)

**Files:** Create `management/commands/rebuild_stock_items.py`; test `tests/test_rebuild_stock_items.py`.

- [ ] **Step 1: Test thất bại**

`backend/apps/inventory/tests/test_rebuild_stock_items.py`:
```python
from __future__ import annotations

from django.core.management import call_command
from django.test import TestCase

from apps.inventory.models import StockItem, StockMovement
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


class RebuildStockItemsTests(TestCase):
    def test_rebuilds_balances_from_ledger(self):
        wh = Warehouse.objects.create(code="W1", name="Kho 1")
        product = Product.objects.create(name="SP A", sku="A-1")
        # Sổ kho có 2 movement tổng +7, nhưng StockItem bị lệch (cố tình = 999)
        StockMovement.objects.create(
            warehouse=wh, product=product, qty_delta=10,
            movement_type="receipt", balance_after=10)
        StockMovement.objects.create(
            warehouse=wh, product=product, qty_delta=-3,
            movement_type="issue", balance_after=7)
        StockItem.objects.create(warehouse=wh, product=product, quantity=999)

        call_command("rebuild_stock_items")

        item = StockItem.objects.get(warehouse=wh, product=product)
        assert item.quantity == 7
```

- [ ] **Step 2: Chạy → FAIL.**

- [ ] **Step 3: Viết command**

`backend/apps/inventory/management/commands/rebuild_stock_items.py`:
```python
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from apps.inventory.models import StockItem, StockMovement


class Command(BaseCommand):
    help = "Rebuild StockItem balances from the immutable StockMovement ledger."

    @transaction.atomic
    def handle(self, *args, **options):
        sums = (
            StockMovement.objects.values("warehouse_id", "product_id")
            .annotate(total=Sum("qty_delta"))
        )
        updated = 0
        for row in sums:
            StockItem.objects.update_or_create(
                warehouse_id=row["warehouse_id"],
                product_id=row["product_id"],
                defaults={"quantity": row["total"] or 0},
            )
            updated += 1
        self.stdout.write(self.style.SUCCESS(f"Rebuilt {updated} stock item(s)."))
```

- [ ] **Step 4: Chạy → PASS.**

- [ ] **Step 5: Ruff + commit**
```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/management
git add backend/apps/inventory/management backend/apps/inventory/tests/test_rebuild_stock_items.py
git commit -m "feat(inventory): add rebuild_stock_items reconciliation command"
```

---

## Task 8: Frontend — section `stock` + trang Tồn kho

**Files:** Edit `frontend/src/lib/routes.ts`, `frontend/src/app/modules.ts`, `frontend/src/app/routes.tsx`, `frontend/src/app/routed-pages.tsx`, `frontend/src/i18n/messages/topnav.ts`; create `frontend/src/features/inventory/types.ts`, `queries.ts`, `pages/StockPage.tsx`.

- [ ] **Step 1: Thêm AppSection + path**

Trong `frontend/src/lib/routes.ts`: thêm `"stock"` vào union `AppSection`; thêm vào `SECTION_TO_PATH`:
```ts
  stock: "/inventory/stock",
```
Không thêm vào `LEGACY_PATH_REDIRECTS` (section mới, không có path cũ).

- [ ] **Step 2: Thêm vào registry module inventory**

Trong `frontend/src/app/modules.ts`, trong module `inventory`, thêm section ĐẦU danh sách (để là default section khi mở module — hoặc cuối nếu muốn giữ products mặc định; đặt đầu để "Tồn kho" là mặc định):
```ts
      { key: "stock", labelKey: "topnav.tabs.stock", icon: Boxes },
```
Thêm `Boxes` vào import lucide-react ở đầu `modules.ts`.

- [ ] **Step 3: i18n**

Trong `frontend/src/i18n/messages/topnav.ts`, trong namespace `topnav.tabs` (cả vi & en), thêm:
- vi: `stock: "Tồn kho"` (hoặc theo format file — nếu file dùng key phẳng `"topnav.tabs.stock"` thì theo đó)
- en: `stock: "Stock"`
Đọc file để theo đúng format hiện có.

- [ ] **Step 4: types + queries (feature-slice)**

`frontend/src/features/inventory/types.ts`:
```ts
export type StockRow = {
  id: number;
  warehouse: number;
  warehouse_code: string;
  warehouse_name: string;
  product: number;
  product_sku: string;
  product_name: string;
  quantity: number;
  updated_at: string;
};

export type StockListResponse = {
  items: StockRow[];
  total: number;
  limit: number;
  offset: number;
};
```

`frontend/src/features/inventory/queries.ts`:
```ts
import { useQuery } from "@tanstack/react-query";

import { fetchJson } from "@/services/http";
import { type StockListResponse } from "@/features/inventory/types";

export const inventoryKeys = {
  stock: (warehouseId: number | null, search: string) =>
    ["inventory", "stock", warehouseId, search] as const,
};

export function useStockList(warehouseId: number | null, search: string) {
  const params = new URLSearchParams({ limit: "200", offset: "0" });
  if (warehouseId) params.set("warehouse_id", String(warehouseId));
  if (search.trim()) params.set("search", search.trim());
  return useQuery({
    queryKey: inventoryKeys.stock(warehouseId, search),
    queryFn: () => fetchJson<StockListResponse>(`/api/inventory/stock?${params.toString()}`),
  });
}
```
> Kiểm tra signature thực tế của `fetchJson` trong `@/services/http` và sửa cho khớp (một số dự án `fetchJson(url)` trả data trực tiếp; nếu khác, chỉnh).

- [ ] **Step 5: StockPage**

`frontend/src/features/inventory/pages/StockPage.tsx`: bảng tồn (ag-grid hoặc bảng đơn giản) với ô tìm SKU/tên + cột Kho/SKU/Tên/Số lượng; số lượng < 0 tô đỏ. Dùng `useStockList`. Theo style các page khác (xem `features/orders` để bám pattern ag-grid + container). Giữ ≤400 dòng, không `any`.

Bản tối thiểu (bảng thường, không cần ag-grid cho 1.1):
```tsx
import { useState } from "react";

import { useStockList } from "@/features/inventory/queries";
import { useI18n } from "@/i18n";

export default function StockPage() {
  const { t } = useI18n();
  const [search, setSearch] = useState("");
  const { data, isLoading } = useStockList(null, search);
  const rows = data?.items ?? [];

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-foreground">{t("topnav.tabs.stock")}</h1>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("common.search", "Tìm SKU / tên")}
          className="rounded-lg border border-border bg-white px-3 py-2 text-sm outline-none focus:border-primary"
        />
      </div>
      <div className="overflow-x-auto rounded-xl border border-border bg-white">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-left text-muted-foreground">
            <tr>
              <th className="px-3 py-2">Kho</th>
              <th className="px-3 py-2">SKU</th>
              <th className="px-3 py-2">Sản phẩm</th>
              <th className="px-3 py-2 text-right">Tồn</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td className="px-3 py-3 text-muted-foreground" colSpan={4}>Đang tải…</td></tr>
            )}
            {!isLoading && rows.length === 0 && (
              <tr><td className="px-3 py-3 text-muted-foreground" colSpan={4}>Chưa có dữ liệu tồn.</td></tr>
            )}
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-border">
                <td className="px-3 py-2">{r.warehouse_name}</td>
                <td className="px-3 py-2 font-medium">{r.product_sku}</td>
                <td className="px-3 py-2">{r.product_name}</td>
                <td className={`px-3 py-2 text-right ${r.quantity < 0 ? "font-semibold text-destructive" : ""}`}>
                  {r.quantity}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Route**

Trong `frontend/src/app/routed-pages.tsx`: thêm lazy export `StockRoute` trỏ tới `@/features/inventory/pages/StockPage` (theo pattern các `*Route` khác trong file — đọc để khớp cách lazy/Suspense).
Trong `frontend/src/app/routes.tsx`: thêm route trong children RootLayout:
```tsx
      { path: "inventory/stock", element: <Guard section="stock"><StockRoute /></Guard> },
```
Import `StockRoute` từ `@/app/routed-pages`.

- [ ] **Step 7: Verify FE**
```bash
cd frontend && npx tsc --noEmit
```
Expected: clean. Chạy lại bộ test routes nếu có round-trip section: `npx vitest run src/lib/routes.test.ts` (nếu test đó assert độ dài/round-trip mọi section, cập nhật cho `stock`).

- [ ] **Step 8: Commit**
```bash
git add frontend/src/lib/routes.ts frontend/src/app/modules.ts frontend/src/app/routes.tsx frontend/src/app/routed-pages.tsx frontend/src/i18n/messages/topnav.ts frontend/src/features/inventory
git commit -m "feat(inventory): add stock section and stock page (FE)"
```

---

## Self-Review (đã thực hiện khi viết plan)
- **Spec coverage (1.1):** models StockMovement/StockItem (Task 2 ↔ spec §3.1-3.2), primitive ghi atomic + cho âm (Task 4 ↔ §4.1-4.2), API xem tồn/lịch sử (Task 6 ↔ §6), FE section stock (Task 8 ↔ §7), đối soát R1 (Task 7 ↔ §9 R1). Event `StockWentNegative` (§5.3) HOÃN sang 1.4 (1.4 mới có người tiêu thụ) — `record_movement` đã trả `went_negative` để 1.4 dùng.
- **Type consistency:** `record_movement` kwargs (warehouse_id/product_id/qty_delta/movement_type) dùng nhất quán Task 4/5/6/7; `StockQueryService.list_stock/list_movements` chữ ký khớp giữa Task 5 và views Task 6.
- **Điểm cần kiểm khi code (đã ghi inline):** chữ ký `User.objects.create_user` (Task 6 Step 4), `fetchJson` (Task 8 Step 4), format i18n file (Task 8 Step 3), pattern `*Route` lazy (Task 8 Step 6).
- **Migration:** Task 2 tạo + kiểm `migrate --plan`; chưa chạy migrate prod (theo thói quen defer prod migration).
