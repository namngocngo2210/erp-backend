# Lịch sử xuất kho — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm trang "Lịch sử xuất kho" trong phần Kho: tổng hợp SL đã xuất theo sản phẩm + nhật ký chi tiết từng lần xuất, lọc theo ngày/kho/sản phẩm/mã đơn.

**Architecture:** Module backend riêng (2 endpoint function-based GET) trên `StockMovement` lọc `sale`/`sale_reversal` với `reference_type="order"`, tách service query. Frontend là 1 trang React (react-query) tái dùng `DateFilter`, bảng thuần và pattern `*-queries.ts` hiện có. Tái dùng quyền RBAC `stock`.

**Tech Stack:** Django REST (function view + drf-spectacular), Postgres; React + Vite + TypeScript + @tanstack/react-query + react-router v6; vitest.

## Global Constraints

- Backend tests chạy trên test container: `POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_DB=desi_test POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= APP_ENV=development DJANGO_DEBUG=0 /home/desi/desi-order/.venv/bin/python manage.py test <path> -v2` (chạy trong `backend/`). KHÔNG dùng `.env` prod.
- Frontend không có node trên host → chạy qua container: `cd frontend && docker run --rm -v "$PWD":/app -w /app public.ecr.aws/docker/library/node:22-alpine <cmd>`. Test: `node node_modules/vitest/vitest.mjs run <path>`; typecheck: `node node_modules/typescript/bin/tsc --noEmit`; lint: `node node_modules/eslint/bin/eslint.js <files> --max-warnings=0`.
- Phạm vi cố định mọi query: `movement_type__in=["sale","sale_reversal"]`, `reference_type="order"`.
- Quy ước: `dispatched` (Đã xuất) và `reversed` (Hoàn lại) là số DƯƠNG; `net = dispatched - reversed`.
- Endpoint đều `@permission_classes([IsAuthenticated, require_section("stock")])` + `@never_cache`.
- Field ngày lọc theo `created_at__date`. Date sai định dạng (không phải `YYYY-MM-DD`) → HTTP 400.
- Commit message kết thúc bằng: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Branch làm việc: `feat/dispatch-history` (đã tạo, off master).

---

### Task 1: Backend — service tổng hợp + endpoint summary

**Files:**
- Create: `backend/apps/inventory/application/services/dispatch_history_service.py`
- Create: `backend/apps/inventory/transport/views/dispatch_history_views.py`
- Modify: `backend/apps/inventory/urls.py` (thêm import + 2 path — path summary ở task này, path detail ở Task 2)
- Test: `backend/apps/inventory/tests/test_dispatch_history_summary.py`

**Interfaces:**
- Produces:
  - `DispatchHistoryService.base_queryset(*, date_from: str, date_to: str, warehouse_id: int | None, product: str, order: str) -> QuerySet[StockMovement]`
  - `DispatchHistoryService.summary(*, date_from, date_to, warehouse_id, product, order) -> dict` trả `{"items": [...], "totals": {...}}`
  - `GET /api/inventory/dispatch-history/summary`
- Consumes: `StockMovement`, `Order` (models), `require_section`, `parse_required_pagination` (Task 2).

- [ ] **Step 1: Viết test thất bại cho summary**

Tạo `backend/apps/inventory/tests/test_dispatch_history_summary.py`:

```python
"""Endpoint tổng hợp lịch sử xuất kho: gross/reversed/net theo sản phẩm."""
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType
from apps.orders.models import Order, Product
from apps.users.test_helpers import make_admin
from apps.warehouses.models import Warehouse

URL = "/api/inventory/dispatch-history/summary"


class DispatchHistorySummaryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=make_admin("admin-dh-sum"))
        self.channel = Channel.objects.create(code="lp-dh", name="DH")
        self.customer = Customer.objects.create(
            full_name="K", phone_raw="0900000000", phone_normalized="0900000000"
        )
        self.wh = Warehouse.objects.create(code="WH-DH", name="Kho DH")
        self.wh2 = Warehouse.objects.create(code="WH-DH2", name="Kho DH2")
        self.p1 = Product.objects.create(name="SP Mot", sku="SKU-1")
        self.p2 = Product.objects.create(name="SP Hai", sku="SKU-2")

    def _order(self, code="ORD-1", shipping="VD-1"):
        return Order.objects.create(
            channel=self.channel, customer=self.customer, name="K", phone="0900000000",
            order_code=code, shipping_code=shipping, total_amount="1000",
        )

    def _move(self, product, qty_delta, mtype, order, wh=None):
        StockCommandService.record_movement(
            warehouse_id=(wh or self.wh).id, product_id=product.id, qty_delta=qty_delta,
            movement_type=mtype, reference_type="order", reference_id=order.id,
        )

    def test_gross_reversed_net_per_product(self):
        o = self._order()
        self._move(self.p1, -10, MovementType.SALE, o)
        self._move(self.p1, 3, MovementType.SALE_REVERSAL, o)
        self._move(self.p2, -5, MovementType.SALE, o)
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        rows = {r["sku"]: r for r in resp.json()["items"]}
        self.assertEqual(rows["SKU-1"]["dispatched"], 10)
        self.assertEqual(rows["SKU-1"]["reversed"], 3)
        self.assertEqual(rows["SKU-1"]["net"], 7)
        self.assertEqual(rows["SKU-2"]["dispatched"], 5)
        self.assertEqual(rows["SKU-2"]["reversed"], 0)

    def test_totals_and_product_count(self):
        o = self._order()
        self._move(self.p1, -10, MovementType.SALE, o)
        self._move(self.p2, -5, MovementType.SALE, o)
        totals = self.client.get(URL).json()["totals"]
        self.assertEqual(totals["dispatched"], 15)
        self.assertEqual(totals["reversed"], 0)
        self.assertEqual(totals["net"], 15)
        self.assertEqual(totals["product_count"], 2)

    def test_ignores_non_sale_movements(self):
        o = self._order()
        self._move(self.p1, -10, MovementType.SALE, o)
        # nhập kho + xuất thủ công không được tính
        StockCommandService.record_movement(
            warehouse_id=self.wh.id, product_id=self.p1.id, qty_delta=50,
            movement_type=MovementType.RECEIPT,
        )
        StockCommandService.record_movement(
            warehouse_id=self.wh.id, product_id=self.p1.id, qty_delta=-4,
            movement_type=MovementType.ISSUE, reference_type="document", reference_id=1,
        )
        rows = {r["sku"]: r for r in self.client.get(URL).json()["items"]}
        self.assertEqual(rows["SKU-1"]["dispatched"], 10)
        self.assertEqual(len(rows), 1)

    def test_filter_by_warehouse(self):
        o = self._order()
        self._move(self.p1, -10, MovementType.SALE, o, wh=self.wh)
        self._move(self.p1, -7, MovementType.SALE, o, wh=self.wh2)
        rows = self.client.get(f"{URL}?warehouse_id={self.wh2.id}").json()["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dispatched"], 7)

    def test_search_product_by_sku_and_name(self):
        o = self._order()
        self._move(self.p1, -10, MovementType.SALE, o)
        self._move(self.p2, -5, MovementType.SALE, o)
        by_sku = self.client.get(f"{URL}?product=SKU-2").json()["items"]
        self.assertEqual([r["sku"] for r in by_sku], ["SKU-2"])
        by_name = self.client.get(f"{URL}?product=Mot").json()["items"]
        self.assertEqual([r["sku"] for r in by_name], ["SKU-1"])

    def test_search_by_order_code_and_shipping_code(self):
        o1 = self._order(code="ORD-AAA", shipping="VD-AAA")
        o2 = self._order(code="ORD-BBB", shipping="VD-BBB")
        self._move(self.p1, -10, MovementType.SALE, o1)
        self._move(self.p2, -5, MovementType.SALE, o2)
        by_order = self.client.get(f"{URL}?order=ORD-AAA").json()["items"]
        self.assertEqual([r["sku"] for r in by_order], ["SKU-1"])
        by_ship = self.client.get(f"{URL}?order=VD-BBB").json()["items"]
        self.assertEqual([r["sku"] for r in by_ship], ["SKU-2"])

    def test_bad_date_returns_400(self):
        self.assertEqual(self.client.get(f"{URL}?date_from=2026/01/01").status_code, 400)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run (trong `backend/`):
```
POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_DB=desi_test POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= APP_ENV=development DJANGO_DEBUG=0 /home/desi/desi-order/.venv/bin/python manage.py test apps.inventory.tests.test_dispatch_history_summary -v2
```
Expected: FAIL — 404 (URL chưa tồn tại) hoặc lỗi import service.

- [ ] **Step 3: Viết service**

Tạo `backend/apps/inventory/application/services/dispatch_history_service.py`:

```python
from __future__ import annotations

from django.db.models import Q, QuerySet, Sum
from django.db.models.functions import Coalesce

from apps.inventory.models import StockMovement
from apps.orders.models import Order

_SALE = "sale"
_SALE_REVERSAL = "sale_reversal"


class DispatchHistoryService:
    @staticmethod
    def base_queryset(
        *,
        date_from: str = "",
        date_to: str = "",
        warehouse_id: int | None = None,
        product: str = "",
        order: str = "",
    ) -> QuerySet[StockMovement]:
        qs = StockMovement.objects.filter(
            movement_type__in=[_SALE, _SALE_REVERSAL], reference_type="order"
        )
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        product = (product or "").strip()
        if product:
            qs = qs.filter(
                Q(product__sku__icontains=product) | Q(product__name__icontains=product)
            )
        order = (order or "").strip()
        if order:
            order_ids = list(
                Order.objects.filter(
                    Q(order_code__icontains=order) | Q(shipping_code__icontains=order)
                ).values_list("id", flat=True)
            )
            qs = qs.filter(reference_id__in=order_ids or [-1])
        return qs

    @staticmethod
    def summary(**filters) -> dict:
        qs = DispatchHistoryService.base_queryset(**filters)
        rows = (
            qs.values("product_id", "product__sku", "product__name")
            .annotate(
                sale_sum=Coalesce(Sum("qty_delta", filter=Q(movement_type=_SALE)), 0),
                rev_sum=Coalesce(Sum("qty_delta", filter=Q(movement_type=_SALE_REVERSAL)), 0),
            )
        )
        items = []
        for r in rows:
            dispatched = -r["sale_sum"]
            reversed_qty = r["rev_sum"]
            items.append(
                {
                    "product_id": r["product_id"],
                    "sku": r["product__sku"],
                    "name": r["product__name"],
                    "dispatched": dispatched,
                    "reversed": reversed_qty,
                    "net": dispatched - reversed_qty,
                }
            )
        items.sort(key=lambda x: x["dispatched"], reverse=True)
        totals = {
            "dispatched": sum(i["dispatched"] for i in items),
            "reversed": sum(i["reversed"] for i in items),
            "net": sum(i["net"] for i in items),
            "product_count": len(items),
        }
        return {"items": items, "totals": totals}
```

- [ ] **Step 4: Viết view summary + helper parse filter/date**

Tạo `backend/apps/inventory/transport/views/dispatch_history_views.py`:

```python
from __future__ import annotations

from datetime import datetime

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.inventory.application.services.dispatch_history_service import DispatchHistoryService
from apps.users.permissions import require_section


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _valid_date(value: str) -> bool:
    if not value:
        return True
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _parse_filters(request):
    """Trả (filters_dict, error_response|None)."""
    date_from = request.query_params.get("date_from") or ""
    date_to = request.query_params.get("date_to") or ""
    if not _valid_date(date_from) or not _valid_date(date_to):
        return None, Response(
            {"detail": "date_from/date_to phải dạng YYYY-MM-DD."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return {
        "date_from": date_from,
        "date_to": date_to,
        "warehouse_id": _int_or_none(request.query_params.get("warehouse_id")),
        "product": request.query_params.get("product") or "",
        "order": request.query_params.get("order") or "",
    }, None


@extend_schema(
    responses={
        200: inline_serializer(
            "DispatchHistorySummary",
            fields={
                "items": inline_serializer(
                    "DispatchHistorySummaryItem",
                    many=True,
                    fields={
                        "product_id": drf.IntegerField(),
                        "sku": drf.CharField(allow_null=True),
                        "name": drf.CharField(allow_null=True),
                        "dispatched": drf.IntegerField(),
                        "reversed": drf.IntegerField(),
                        "net": drf.IntegerField(),
                    },
                ),
                "totals": inline_serializer(
                    "DispatchHistorySummaryTotals",
                    fields={
                        "dispatched": drf.IntegerField(),
                        "reversed": drf.IntegerField(),
                        "net": drf.IntegerField(),
                        "product_count": drf.IntegerField(),
                    },
                ),
            },
        ),
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def dispatch_history_summary(request):
    filters, error = _parse_filters(request)
    if error is not None:
        return error
    return Response(DispatchHistoryService.summary(**filters), status=status.HTTP_200_OK)
```

- [ ] **Step 5: Đăng ký URL summary**

Trong `backend/apps/inventory/urls.py`, thêm import sau khối import view hiện có:
```python
from apps.inventory.transport.views.dispatch_history_views import (
    dispatch_history_summary,
)
```
Và thêm vào `urlpatterns` (trước dòng `path("documents", ...)`):
```python
    path(
        "dispatch-history/summary",
        dispatch_history_summary,
        name="inventory-dispatch-history-summary",
    ),
```

- [ ] **Step 6: Chạy test để xác nhận PASS**

Run lệnh ở Step 2. Expected: `OK` (7 tests pass).

- [ ] **Step 7: Commit**

```bash
git add backend/apps/inventory/application/services/dispatch_history_service.py backend/apps/inventory/transport/views/dispatch_history_views.py backend/apps/inventory/urls.py backend/apps/inventory/tests/test_dispatch_history_summary.py
git commit -m "$(printf 'feat(inventory): endpoint tong hop lich su xuat kho\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: Backend — endpoint chi tiết (nhật ký) có phân trang

**Files:**
- Modify: `backend/apps/inventory/application/services/dispatch_history_service.py` (thêm `detail_queryset`)
- Modify: `backend/apps/inventory/transport/views/dispatch_history_views.py` (thêm view `dispatch_history_detail`)
- Modify: `backend/apps/inventory/urls.py` (thêm path detail)
- Test: `backend/apps/inventory/tests/test_dispatch_history_detail.py`

**Interfaces:**
- Consumes: `DispatchHistoryService.base_queryset`, `_parse_filters`, `parse_required_pagination`, `StockQueryService.resolve_reference_labels`.
- Produces:
  - `DispatchHistoryService.detail_queryset(**filters) -> QuerySet[StockMovement]` (đã `select_related` + order `-created_at,-id`)
  - `GET /api/inventory/dispatch-history` trả `{items, total, limit, offset}`; mỗi item: `id, created_at, movement_type, movement_type_label, order_id, order_code, shipping_code, warehouse_name, product_sku, product_name, quantity, balance_after`.

- [ ] **Step 1: Viết test thất bại cho detail**

Tạo `backend/apps/inventory/tests/test_dispatch_history_detail.py`:

```python
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType
from apps.orders.models import Order, Product
from apps.users.test_helpers import make_admin
from apps.warehouses.models import Warehouse

URL = "/api/inventory/dispatch-history"


class DispatchHistoryDetailTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=make_admin("admin-dh-det"))
        self.channel = Channel.objects.create(code="lp-dhd", name="DHD")
        self.customer = Customer.objects.create(
            full_name="K", phone_raw="0900000001", phone_normalized="0900000001"
        )
        self.wh = Warehouse.objects.create(code="WH-DHD", name="Kho DHD")
        self.p1 = Product.objects.create(name="SP Mot", sku="SKU-1")

    def _order(self, code="ORD-1", shipping="VD-1"):
        return Order.objects.create(
            channel=self.channel, customer=self.customer, name="K", phone="0900000001",
            order_code=code, shipping_code=shipping, total_amount="1000",
        )

    def _move(self, qty_delta, mtype, order):
        StockCommandService.record_movement(
            warehouse_id=self.wh.id, product_id=self.p1.id, qty_delta=qty_delta,
            movement_type=mtype, reference_type="order", reference_id=order.id,
        )

    def test_detail_rows_shape_and_labels(self):
        o = self._order(code="ORD-XYZ", shipping="VD-XYZ")
        self._move(-3, MovementType.SALE, o)
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 1)
        row = body["items"][0]
        self.assertEqual(row["quantity"], 3)
        self.assertEqual(row["movement_type"], "sale")
        self.assertEqual(row["order_code"], "ORD-XYZ")
        self.assertEqual(row["shipping_code"], "VD-XYZ")
        self.assertEqual(row["warehouse_name"], "Kho DHD")
        self.assertEqual(row["product_sku"], "SKU-1")

    def test_reversal_row_has_positive_quantity(self):
        o = self._order()
        self._move(4, MovementType.SALE_REVERSAL, o)
        row = self.client.get(URL).json()["items"][0]
        self.assertEqual(row["movement_type"], "sale_reversal")
        self.assertEqual(row["quantity"], 4)

    def test_pagination(self):
        o = self._order()
        for _ in range(3):
            self._move(-1, MovementType.SALE, o)
        page = self.client.get(f"{URL}?limit=2&offset=0").json()
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["items"]), 2)
        self.assertEqual(page["limit"], 2)

    def test_excludes_non_sale(self):
        StockCommandService.record_movement(
            warehouse_id=self.wh.id, product_id=self.p1.id, qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )
        self.assertEqual(self.client.get(URL).json()["total"], 0)
```

- [ ] **Step 2: Chạy test xác nhận FAIL**

Run:
```
POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_DB=desi_test POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= APP_ENV=development DJANGO_DEBUG=0 /home/desi/desi-order/.venv/bin/python manage.py test apps.inventory.tests.test_dispatch_history_detail -v2
```
Expected: FAIL — 404.

- [ ] **Step 3: Thêm `detail_queryset` vào service**

Trong `dispatch_history_service.py`, thêm method:
```python
    @staticmethod
    def detail_queryset(**filters) -> QuerySet[StockMovement]:
        return (
            DispatchHistoryService.base_queryset(**filters)
            .select_related("warehouse", "product")
            .order_by("-created_at", "-id")
        )
```

- [ ] **Step 4: Thêm view detail**

Trong `dispatch_history_views.py`: thêm import ở đầu file:
```python
from apps.common.pagination import parse_required_pagination
from apps.inventory.application.services.stock_query_service import StockQueryService
from apps.inventory.models import MovementType
```
Và thêm view (sau `dispatch_history_summary`):
```python
_MOVEMENT_LABELS = dict(MovementType.choices)


@extend_schema(
    responses={
        200: inline_serializer(
            "DispatchHistoryDetail",
            fields={
                "items": inline_serializer(
                    "DispatchHistoryDetailItem",
                    many=True,
                    fields={
                        "id": drf.IntegerField(),
                        "created_at": drf.DateTimeField(),
                        "movement_type": drf.CharField(),
                        "movement_type_label": drf.CharField(),
                        "order_id": drf.IntegerField(allow_null=True),
                        "order_code": drf.CharField(allow_null=True),
                        "shipping_code": drf.CharField(allow_null=True),
                        "warehouse_name": drf.CharField(allow_null=True),
                        "product_sku": drf.CharField(allow_null=True),
                        "product_name": drf.CharField(allow_null=True),
                        "quantity": drf.IntegerField(),
                        "balance_after": drf.IntegerField(),
                    },
                ),
                "total": drf.IntegerField(),
                "limit": drf.IntegerField(),
                "offset": drf.IntegerField(),
            },
        ),
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def dispatch_history_detail(request):
    filters, error = _parse_filters(request)
    if error is not None:
        return error
    pagination, page_error = parse_required_pagination(
        request, max_limit=200, default_limit=50
    )
    if page_error is not None:
        return page_error
    qs = DispatchHistoryService.detail_queryset(**filters)
    total = qs.count()
    items = list(qs[pagination["offset"] : pagination["offset"] + pagination["limit"]])
    labels = StockQueryService.resolve_reference_labels(items)
    order_ids = [m.reference_id for m in items if m.reference_type == "order"]
    shipping_by_id = {}
    if order_ids:
        from apps.orders.models import Order

        shipping_by_id = dict(
            Order.objects.filter(id__in=order_ids).values_list("id", "shipping_code")
        )
    rows = [
        {
            "id": m.id,
            "created_at": m.created_at,
            "movement_type": m.movement_type,
            "movement_type_label": _MOVEMENT_LABELS.get(m.movement_type, m.movement_type),
            "order_id": m.reference_id,
            "order_code": labels.get(("order", m.reference_id)),
            "shipping_code": shipping_by_id.get(m.reference_id),
            "warehouse_name": m.warehouse.name,
            "product_sku": m.product.sku,
            "product_name": m.product.name,
            "quantity": abs(m.qty_delta),
            "balance_after": m.balance_after,
        }
        for m in items
    ]
    return Response(
        {"items": rows, "total": total, "limit": pagination["limit"], "offset": pagination["offset"]},
        status=status.HTTP_200_OK,
    )
```

- [ ] **Step 5: Đăng ký URL detail**

Trong `backend/apps/inventory/urls.py`: mở rộng import thành:
```python
from apps.inventory.transport.views.dispatch_history_views import (
    dispatch_history_detail,
    dispatch_history_summary,
)
```
Thêm path (ngay dưới path summary; đặt path summary TRƯỚC path detail để không bị nuốt route):
```python
    path("dispatch-history", dispatch_history_detail, name="inventory-dispatch-history"),
```

- [ ] **Step 6: Chạy test xác nhận PASS** — lệnh ở Step 2. Expected: `OK` (4 tests).

- [ ] **Step 7: Chạy lại cả 2 file test backend cho chắc**

```
POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_DB=desi_test POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= APP_ENV=development DJANGO_DEBUG=0 /home/desi/desi-order/.venv/bin/python manage.py test apps.inventory.tests.test_dispatch_history_summary apps.inventory.tests.test_dispatch_history_detail -v2
```
Expected: `OK` (11 tests).

- [ ] **Step 8: Commit**

```bash
git add backend/apps/inventory/
git commit -m "$(printf 'feat(inventory): endpoint chi tiet lich su xuat kho co phan trang\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 3: Regenerate OpenAPI schema

**Files:**
- Modify: `backend/schema.yaml`

- [ ] **Step 1: Sinh schema ra file tạm & kiểm tra 0 errors**

Run (trong `backend/`):
```
POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_DB=desi_test POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= APP_ENV=development DJANGO_DEBUG=0 /home/desi/desi-order/.venv/bin/python manage.py spectacular --file schema.yaml
```
Expected: in ra "Errors: 0" (các Warning về operationId collisions là có sẵn, bỏ qua).

- [ ] **Step 2: Xác nhận có path mới**

Run: `grep -n "dispatch-history" backend/schema.yaml`
Expected: thấy `/api/inventory/dispatch-history` và `/api/inventory/dispatch-history/summary`.

- [ ] **Step 3: Commit**

```bash
git add backend/schema.yaml
git commit -m "$(printf 'chore(inventory): cap nhat schema cho dispatch-history\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

> Lưu ý: KHÔNG regenerate/commit `frontend/src/api/schema.d.ts` — type frontend là hand-written (Task 4).

---

### Task 4: Frontend — types + queries + helper `buildDispatchParams`

**Files:**
- Create: `frontend/src/features/inventory/dispatch-history-queries.ts`
- Test: `frontend/src/features/inventory/__tests__/dispatch-history-queries.test.ts`

**Interfaces:**
- Produces:
  - Types `DispatchHistoryFilters`, `DispatchSummaryRow`, `DispatchSummaryResponse`, `DispatchDetailRow`, `DispatchDetailResponse`.
  - `buildDispatchParams(filters: DispatchHistoryFilters): URLSearchParams`
  - Hooks `useDispatchSummary(filters)`, `useDispatchDetail(filters, page)` (react-query).
- Consumes: `fetchJson` từ `@/services/http`.

- [ ] **Step 1: Viết test thất bại cho `buildDispatchParams`**

Tạo `frontend/src/features/inventory/__tests__/dispatch-history-queries.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import { buildDispatchParams } from "../dispatch-history-queries";

describe("buildDispatchParams", () => {
  it("bỏ qua field rỗng/null", () => {
    const p = buildDispatchParams({
      dateFrom: "",
      dateTo: "",
      warehouseId: null,
      product: "",
      order: "",
    });
    expect(p.toString()).toBe("");
  });

  it("map đúng tên param snake_case", () => {
    const p = buildDispatchParams({
      dateFrom: "2026-07-01",
      dateTo: "2026-07-31",
      warehouseId: 5,
      product: "SKU-1",
      order: "ORD-9",
    });
    expect(p.get("date_from")).toBe("2026-07-01");
    expect(p.get("date_to")).toBe("2026-07-31");
    expect(p.get("warehouse_id")).toBe("5");
    expect(p.get("product")).toBe("SKU-1");
    expect(p.get("order")).toBe("ORD-9");
  });

  it("trim chuỗi tìm kiếm", () => {
    const p = buildDispatchParams({
      dateFrom: "",
      dateTo: "",
      warehouseId: null,
      product: "  áo  ",
      order: "",
    });
    expect(p.get("product")).toBe("áo");
  });
});
```

- [ ] **Step 2: Chạy test xác nhận FAIL**

Run (trong `frontend/`):
```
docker run --rm -v "$PWD":/app -w /app public.ecr.aws/docker/library/node:22-alpine node node_modules/vitest/vitest.mjs run src/features/inventory/__tests__/dispatch-history-queries.test.ts
```
Expected: FAIL — không resolve được import `../dispatch-history-queries`.

- [ ] **Step 3: Viết module queries + types**

Tạo `frontend/src/features/inventory/dispatch-history-queries.ts`:

```typescript
import { useQuery } from "@tanstack/react-query";

import { fetchJson } from "@/services/http";

export type DispatchHistoryFilters = {
  dateFrom: string;
  dateTo: string;
  warehouseId: number | null;
  product: string;
  order: string;
};

export type DispatchSummaryRow = {
  product_id: number;
  sku: string | null;
  name: string | null;
  dispatched: number;
  reversed: number;
  net: number;
};

export type DispatchSummaryResponse = {
  items: DispatchSummaryRow[];
  totals: { dispatched: number; reversed: number; net: number; product_count: number };
};

export type DispatchDetailRow = {
  id: number;
  created_at: string;
  movement_type: string;
  movement_type_label: string;
  order_id: number | null;
  order_code: string | null;
  shipping_code: string | null;
  warehouse_name: string | null;
  product_sku: string | null;
  product_name: string | null;
  quantity: number;
  balance_after: number;
};

export type DispatchDetailResponse = {
  items: DispatchDetailRow[];
  total: number;
  limit: number;
  offset: number;
};

export function buildDispatchParams(filters: DispatchHistoryFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (filters.dateFrom) p.set("date_from", filters.dateFrom);
  if (filters.dateTo) p.set("date_to", filters.dateTo);
  if (filters.warehouseId) p.set("warehouse_id", String(filters.warehouseId));
  if (filters.product.trim()) p.set("product", filters.product.trim());
  if (filters.order.trim()) p.set("order", filters.order.trim());
  return p;
}

export function useDispatchSummary(filters: DispatchHistoryFilters) {
  const params = buildDispatchParams(filters);
  return useQuery({
    queryKey: ["inventory", "dispatch-history", "summary", params.toString()],
    queryFn: () =>
      fetchJson(
        `/api/inventory/dispatch-history/summary?${params.toString()}`,
      ) as Promise<DispatchSummaryResponse>,
    staleTime: 10_000,
  });
}

export function useDispatchDetail(
  filters: DispatchHistoryFilters,
  page: { limit: number; offset: number },
) {
  const params = buildDispatchParams(filters);
  params.set("limit", String(page.limit));
  params.set("offset", String(page.offset));
  return useQuery({
    queryKey: ["inventory", "dispatch-history", "detail", params.toString()],
    queryFn: () =>
      fetchJson(
        `/api/inventory/dispatch-history?${params.toString()}`,
      ) as Promise<DispatchDetailResponse>,
    staleTime: 10_000,
  });
}
```

- [ ] **Step 4: Chạy test xác nhận PASS** — lệnh ở Step 2. Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/dispatch-history-queries.ts frontend/src/features/inventory/__tests__/dispatch-history-queries.test.ts
git commit -m "$(printf 'feat(inventory): queries + types trang lich su xuat kho\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 5: Frontend — trang `DispatchHistoryPage`

**Files:**
- Create: `frontend/src/features/inventory/pages/DispatchHistoryPage.tsx`

**Interfaces:**
- Consumes: `useDispatchSummary`, `useDispatchDetail`, `DispatchHistoryFilters` (Task 4); `DateFilter`, `getDateRange`, `DateFilterValue` từ `@/components/ui/date-filter`; `PageHeader` từ `@/components/ui/page-header`; `Select*` từ `@/components/ui/select`; `Input` từ `@/components/ui/input`; `fetchJson`; `WarehouseListResponse` từ `@/lib/api/warehouses`.
- Produces: `export default function DispatchHistoryPage()`.

- [ ] **Step 1: Viết component**

Tạo `frontend/src/features/inventory/pages/DispatchHistoryPage.tsx`:

```tsx
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { PageHeader } from "@/components/ui/page-header";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DateFilter, getDateRange, type DateFilterValue } from "@/components/ui/date-filter";
import { fetchJson } from "@/services/http";
import type { WarehouseListResponse } from "@/lib/api/warehouses";

import {
  useDispatchSummary,
  useDispatchDetail,
  type DispatchHistoryFilters,
} from "../dispatch-history-queries";

const PAGE_SIZE = 50;
const ALL_WH = "all";

export default function DispatchHistoryPage() {
  const initialRange = getDateRange("this_month");
  const [date, setDate] = useState<DateFilterValue>({
    option: "this_month",
    from: initialRange.from,
    to: initialRange.to,
  });
  const [warehouse, setWarehouse] = useState<string>(ALL_WH);
  const [product, setProduct] = useState("");
  const [order, setOrder] = useState("");
  const [offset, setOffset] = useState(0);

  const filters: DispatchHistoryFilters = useMemo(
    () => ({
      dateFrom: date.from ?? "",
      dateTo: date.to ?? "",
      warehouseId: warehouse === ALL_WH ? null : Number(warehouse),
      product,
      order,
    }),
    [date.from, date.to, warehouse, product, order],
  );

  const { data: whData } = useQuery({
    queryKey: ["warehouses", "list"],
    queryFn: () =>
      fetchJson("/api/warehouses?limit=200&offset=0") as Promise<WarehouseListResponse>,
    staleTime: 60_000,
  });
  const warehouses = whData?.items ?? [];

  const summary = useDispatchSummary(filters);
  const detail = useDispatchDetail(filters, { limit: PAGE_SIZE, offset });

  const totals = summary.data?.totals;
  const detailData = detail.data;

  return (
    <div className="space-y-4 p-4">
      <PageHeader title="Lịch sử xuất kho" />

      <div className="flex flex-wrap items-center gap-2">
        <DateFilter
          value={date}
          onChange={(v) => {
            setDate(v);
            setOffset(0);
          }}
          inline
        />
        <Select
          value={warehouse}
          onValueChange={(v) => {
            setWarehouse(v);
            setOffset(0);
          }}
        >
          <SelectTrigger className="w-48">
            <SelectValue placeholder="Tất cả kho" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_WH}>Tất cả kho</SelectItem>
            {warehouses.map((w) => (
              <SelectItem key={w.id} value={String(w.id)}>
                {w.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          className="w-48"
          placeholder="Tìm sản phẩm (SKU/tên)"
          value={product}
          onChange={(e) => {
            setProduct(e.target.value);
            setOffset(0);
          }}
        />
        <Input
          className="w-48"
          placeholder="Tìm mã đơn / mã vận đơn"
          value={order}
          onChange={(e) => {
            setOrder(e.target.value);
            setOffset(0);
          }}
        />
      </div>

      {/* Bảng tổng hợp */}
      <div className="rounded-lg border border-border">
        <div className="border-b border-border px-3 py-2 text-sm font-medium">
          Tổng hợp theo sản phẩm
        </div>
        <table data-slot="table" className="w-full text-sm">
          <thead className="text-left text-muted-foreground">
            <tr>
              <th className="px-3 py-2">SKU</th>
              <th className="px-3 py-2">Tên</th>
              <th className="px-3 py-2 text-right">Đã xuất</th>
              <th className="px-3 py-2 text-right">Hoàn lại</th>
              <th className="px-3 py-2 text-right">Còn lại ròng</th>
            </tr>
          </thead>
          <tbody>
            {(summary.data?.items.length ?? 0) === 0 && (
              <tr>
                <td className="px-3 py-3 text-muted-foreground" colSpan={5}>
                  {summary.isLoading ? "Đang tải…" : "Không có dữ liệu."}
                </td>
              </tr>
            )}
            {summary.data?.items.map((r) => (
              <tr key={r.product_id} className="border-t border-border">
                <td className="px-3 py-2 font-medium">{r.sku}</td>
                <td className="px-3 py-2">{r.name}</td>
                <td className="px-3 py-2 text-right">{r.dispatched}</td>
                <td className="px-3 py-2 text-right">{r.reversed}</td>
                <td className="px-3 py-2 text-right">{r.net}</td>
              </tr>
            ))}
            {totals && (summary.data?.items.length ?? 0) > 0 && (
              <tr className="border-t border-border font-medium">
                <td className="px-3 py-2">Tổng ({totals.product_count} SP)</td>
                <td className="px-3 py-2"></td>
                <td className="px-3 py-2 text-right">{totals.dispatched}</td>
                <td className="px-3 py-2 text-right">{totals.reversed}</td>
                <td className="px-3 py-2 text-right">{totals.net}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Bảng chi tiết */}
      <div className="rounded-lg border border-border">
        <div className="border-b border-border px-3 py-2 text-sm font-medium">
          Chi tiết từng lần xuất
        </div>
        <table data-slot="table" className="w-full text-sm">
          <thead className="text-left text-muted-foreground">
            <tr>
              <th className="px-3 py-2">Thời gian</th>
              <th className="px-3 py-2">Mã đơn</th>
              <th className="px-3 py-2">Mã vận đơn</th>
              <th className="px-3 py-2">Sản phẩm</th>
              <th className="px-3 py-2">Loại</th>
              <th className="px-3 py-2 text-right">SL</th>
              <th className="px-3 py-2 text-right">Tồn sau</th>
            </tr>
          </thead>
          <tbody>
            {(detailData?.items.length ?? 0) === 0 && (
              <tr>
                <td className="px-3 py-3 text-muted-foreground" colSpan={7}>
                  {detail.isLoading ? "Đang tải…" : "Không có dữ liệu."}
                </td>
              </tr>
            )}
            {detailData?.items.map((r) => (
              <tr key={r.id} className="border-t border-border">
                <td className="px-3 py-2">{new Date(r.created_at).toLocaleString("vi-VN")}</td>
                <td className="px-3 py-2">{r.order_code ?? "—"}</td>
                <td className="px-3 py-2">{r.shipping_code ?? "—"}</td>
                <td className="px-3 py-2">
                  {r.product_sku} <span className="text-muted-foreground">{r.product_name}</span>
                </td>
                <td className="px-3 py-2">
                  <span
                    className={
                      r.movement_type === "sale_reversal" ? "text-amber-600" : "text-green-600"
                    }
                  >
                    {r.movement_type === "sale_reversal" ? "Hoàn" : "Xuất"}
                  </span>
                </td>
                <td className="px-3 py-2 text-right">{r.quantity}</td>
                <td className="px-3 py-2 text-right">{r.balance_after}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {detailData && detailData.total > PAGE_SIZE && (
          <div className="flex items-center justify-between border-t border-border px-3 py-2 text-sm">
            <span className="text-muted-foreground">
              {offset + 1}–{Math.min(offset + PAGE_SIZE, detailData.total)} / {detailData.total}
            </span>
            <div className="flex gap-2">
              <button
                className="rounded border border-border px-2 py-1 disabled:opacity-50"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                Trước
              </button>
              <button
                className="rounded border border-border px-2 py-1 disabled:opacity-50"
                disabled={offset + PAGE_SIZE >= detailData.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Sau
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run (trong `frontend/`):
```
docker run --rm -v "$PWD":/app -w /app public.ecr.aws/docker/library/node:22-alpine node node_modules/typescript/bin/tsc --noEmit
```
Expected: exit 0 (không lỗi). Nếu `PageHeader`/`WarehouseListResponse` khác signature thực tế, sửa import theo lỗi tsc trước khi tiếp.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/inventory/pages/DispatchHistoryPage.tsx
git commit -m "$(printf 'feat(inventory): trang Lich su xuat kho (tong hop + chi tiet)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 6: Frontend — routing, menu, i18n

**Files:**
- Modify: `frontend/src/lib/routes.ts` (AppSection union + SECTION_TO_PATH + SECTION_PERMISSION)
- Modify: `frontend/src/app/modules.ts` (thêm section vào module inventory; đảm bảo icon `History` được import)
- Modify: `frontend/src/app/routed-pages.tsx` (lazy import + `DispatchHistoryRoute`)
- Modify: `frontend/src/app/routes.tsx` (thêm route dưới nhánh inventory)
- Modify: `frontend/src/i18n/messages/topnav.ts` (thêm `topnav.tabs.dispatchHistory`)

**Interfaces:**
- Consumes: `Guard`, `requiredPermissionFor` (đã có). Section mới `"dispatch-history"` map quyền về `"stock"`.

- [ ] **Step 1: `lib/routes.ts` — thêm section**

Thêm `| "dispatch-history"` vào union `AppSection` (sau `"stock"`).
Trong `SECTION_TO_PATH`, thêm sau dòng `stock: "/inventory/stock",`:
```typescript
  "dispatch-history": "/inventory/dispatch-history",
```
Trong `SECTION_PERMISSION`, thêm:
```typescript
  // Trang Lịch sử xuất kho dùng chung quyền với section Tồn kho (stock).
  "dispatch-history": "stock",
```

- [ ] **Step 2: `modules.ts` — thêm mục menu**

Đảm bảo `History` có trong import lucide-react ở đầu file (đã dùng cho `attendance-history`, nên thường đã import — nếu chưa thì thêm vào danh sách import).
Trong module `key: "inventory"`, mảng `sections`, thêm sau mục `stock`:
```typescript
      {
        key: "dispatch-history",
        labelKey: "topnav.tabs.dispatchHistory",
        icon: History,
        requires: "stock",
      },
```

- [ ] **Step 3: `routed-pages.tsx` — lazy import + route wrapper**

Thêm cạnh các lazy inventory (khoảng dòng 42-44):
```typescript
const DispatchHistoryPage = lazy(
  () => import("@/features/inventory/pages/DispatchHistoryPage"),
);
```
Thêm hàm route (cạnh `StockRoute`):
```typescript
export function DispatchHistoryRoute() {
  return <DispatchHistoryPage />;
}
```

- [ ] **Step 4: `routes.tsx` — thêm route**

Đảm bảo `DispatchHistoryRoute` nằm trong danh sách import từ `./routed-pages` (cùng chỗ `StockRoute`).
Thêm entry trong children, ngay sau block `inventory/stock`:
```tsx
      {
        path: "inventory/dispatch-history",
        element: (
          <Guard section="dispatch-history">
            <DispatchHistoryRoute />
          </Guard>
        ),
      },
```

- [ ] **Step 5: `i18n/messages/topnav.ts` — nhãn**

Thêm dòng vào object messages (cạnh các `topnav.tabs.*` khác):
```typescript
    "topnav.tabs.dispatchHistory": "Lịch sử xuất kho",
```

- [ ] **Step 6: Typecheck + lint**

Run (trong `frontend/`):
```
docker run --rm -v "$PWD":/app -w /app public.ecr.aws/docker/library/node:22-alpine sh -c 'node node_modules/typescript/bin/tsc --noEmit && node node_modules/eslint/bin/eslint.js src/features/inventory/pages/DispatchHistoryPage.tsx src/features/inventory/dispatch-history-queries.ts src/lib/routes.ts src/app/modules.ts src/app/routed-pages.tsx src/app/routes.tsx --max-warnings=0'
```
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/routes.ts frontend/src/app/modules.ts frontend/src/app/routed-pages.tsx frontend/src/app/routes.tsx frontend/src/i18n/messages/topnav.ts
git commit -m "$(printf 'feat(inventory): dinh tuyen + menu Lich su xuat kho\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 7: Kiểm chứng tổng thể

**Files:** (không sửa code, chỉ verify)

- [ ] **Step 1: Chạy toàn bộ test frontend inventory + typecheck**

Run (trong `frontend/`):
```
docker run --rm -v "$PWD":/app -w /app public.ecr.aws/docker/library/node:22-alpine sh -c 'node node_modules/vitest/vitest.mjs run src/features/inventory && node node_modules/typescript/bin/tsc --noEmit'
```
Expected: tất cả test pass, tsc exit 0.

- [ ] **Step 2: Chạy lại test backend inventory liên quan**

Run (trong `backend/`):
```
POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_DB=desi_test POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= APP_ENV=development DJANGO_DEBUG=0 /home/desi/desi-order/.venv/bin/python manage.py test apps.inventory.tests.test_dispatch_history_summary apps.inventory.tests.test_dispatch_history_detail -v2
```
Expected: `OK`.

- [ ] **Step 3: Verify thủ công qua skill `verify` (chạy app, mở `/inventory/dispatch-history`, đổi bộ lọc ngày/kho, kiểm tra bảng tổng hợp + chi tiết + phân trang khớp dữ liệu).** Ghi lại quan sát.

- [ ] **Step 4: (Tùy chọn) push branch + mở MR**

```bash
git push -u origin feat/dispatch-history -o merge_request.create -o merge_request.target=master -o merge_request.remove_source_branch -o merge_request.title="feat(inventory): trang Lịch sử xuất kho"
```

---

## Self-Review Notes

- **Spec coverage:** phạm vi SALE (Task 1/2 base_queryset cố định `sale`/`sale_reversal`), tổng hợp + chi tiết (Task 1/2), lọc ngày/kho/sản phẩm/mã đơn (base_queryset + _parse_filters), 2 cột gross/reversed + net (Task 1 summary), quyền stock (require_section + SECTION_PERMISSION), route/menu/i18n (Task 6), TDD backend+frontend, không Export Excel (đúng YAGNI). ✅
- **Type consistency:** tên field response backend (`dispatched/reversed/net/product_count`, `movement_type/quantity/order_code/shipping_code/...`) khớp type frontend trong Task 4 và render Task 5. ✅
- **Placeholder scan:** không có TODO/TBD; mọi step có code hoặc lệnh cụ thể. ✅
- **Rủi ro cần chú ý khi thực thi:** signature thực tế của `PageHeader` và `WarehouseListResponse` cần khớp — nếu tsc báo lỗi ở Task 5 Step 2, điều chỉnh import cho đúng (đây là lý do bước typecheck nằm ngay sau khi tạo component).
