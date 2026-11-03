# Flow History SKU View — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm chế độ "Xem theo SKU" cho trang Lịch sử nhập xuất — toggle chung cho cả 2 tab, gộp số lượng theo SKU với drill-down ra đơn/phiếu.

**Architecture:** Đảo chiều gộp của view hiện tại (theo đơn → theo SKU) ở tầng service, thêm 4 endpoint GET (serializer riêng để FE typed), thêm `?group=sku` cho 2 endpoint export. FE tách `IssueTab`/`ReturnTab`/`flow-shared` khỏi `DispatchHistoryPage.tsx`, thêm toggle `viewMode` render `OrderView`/`SkuView`.

**Tech Stack:** Django + DRF + drf-spectacular, PostgreSQL, openpyxl (Excel); React + TypeScript + React Query + shadcn/ui; openapi-typescript (`gen:api`).

## Global Constraints

- **Branch:** làm trên `feat/flow-history-sku-view` (worktree hiện tại). Không tự tạo nhánh mới.
- **RBAC:** mọi endpoint mới `@permission_classes([IsAuthenticated, require_section("stock")])` + `@never_cache`.
- **FE typing:** type response mới lấy từ `src/api/schema.d.ts` qua `npm run gen:api` — KHÔNG khai type tay (convention CLAUDE.md).
- **FE UI:** dùng `components/ui`; CẤM `react-select`; không tự style component từ đầu; light theme xanh da trời.
- **Không đổi hành vi mặc định:** trang mở ở view "Theo đơn"; view Theo đơn giữ nguyên logic (chỉ tách file).
- **Mã chờ khớp (pending):** ẩn hoàn toàn ở SKU view.
- **Backend tests (Windows, Git Bash):** container test là **ephemeral `docker run`** (KHÔNG phải `docker exec`). Dùng đúng lệnh sau (thay `<path>` và `<unique>`); mọi dòng "Run: docker exec ..." trong task bên dưới ĐỀU thay bằng lệnh này:
  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
    --env-file "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/flow-history-sku-view/.env" \
    --env-file "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/flow-history-sku-view/backend/.env" \
    -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
    -e POSTGRES_DB=desi_<unique> \
    -v "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/flow-history-sku-view/backend:/app" -w /app \
    desi-order-web-test:latest pytest <path> -q --create-db
  ```
  `--create-db` an toàn (POSTGRES_DB unique → test DB mới tinh). `mkdir -p /tmp/prom_multiproc` không cần với image này (đã có entrypoint), nếu gặp lỗi prometheus thì bọc `sh -c 'mkdir -p /tmp/prom_multiproc && pytest ...'`.
- **Regen schema (Task 5):** chạy spectacular trong container ephemeral, ghi vào backend mount:
  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
    --env-file "c:/.../flow-history-sku-view/.env" --env-file "c:/.../flow-history-sku-view/backend/.env" \
    -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
    -v "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/flow-history-sku-view/backend:/app" -w /app \
    desi-order-web-test:latest python manage.py spectacular --file schema.yaml
  ```
  rồi `cd frontend && npm run gen:api` (đọc `../backend/schema.yaml`).
- **net (Xuất)** = `dispatched − reversed`, với `dispatched = -Sum(qty_delta | sale)`, `reversed = Sum(qty_delta | sale_reversal)`.

---

## File Structure

**Backend**
- Modify `apps/inventory/application/services/dispatch_history_service.py` — thêm `skus_page`, `sku_orders`.
- Modify `apps/inventory/application/services/return_history_service.py` — thêm `skus_page`, `sku_returns`.
- Modify `apps/inventory/transport/views/flow_history_views.py` — 4 view mới + `group` param cho 2 export view.
- Modify `apps/inventory/urls.py` — 4 route mới.
- Modify `apps/inventory/infrastructure/flow_history_excel.py` — `export_issue_skus`, `export_return_skus`.
- Modify `apps/inventory/tests/test_flow_history_views.py`, `test_flow_history_export.py`.

**Frontend**
- Modify `src/api/schema.d.ts` (regen), `src/lib/api/inventory-flow.ts`.
- Modify `src/features/inventory/issue-history-queries.ts`, `return-history-queries.ts`.
- Create `src/features/inventory/components/flow-shared.tsx` (primitive dùng chung).
- Create `src/features/inventory/components/IssueTab.tsx`, `components/ReturnTab.tsx`.
- Modify `src/features/inventory/pages/DispatchHistoryPage.tsx` (shell).
- Create `src/features/inventory/__tests__/flow-sku-view.test.tsx`.

---

## Task 1: BE — DispatchHistoryService SKU aggregation

**Files:**
- Modify: `backend/apps/inventory/application/services/dispatch_history_service.py`
- Test: `backend/apps/inventory/tests/test_dispatch_history_service.py` (create if absent; else append)

**Interfaces:**
- Consumes: `DispatchHistoryService.base_queryset(**filters)` (đã có, hỗ trợ `product_id`, `order_id`, date/warehouse/product/order).
- Produces:
  - `DispatchHistoryService.skus_page(*, pagination: dict, **filters) -> dict` → `{"items": [ {product_id:int, sku:str|None, name:str|None, order_count:int, dispatched:int, reversed:int, net:int, last_at:datetime|None} ], "total": int, "total_quantity": int, "limit": int, "offset": int}`.
  - `DispatchHistoryService.sku_orders(*, product_id: int, **filters) -> list[dict]` → `[ {order_id:int, order_code:str|None, invoice_code:str|None, shipping_code:str|None, warehouse_name:str|None, net:int, last_at:datetime|None} ]`.

- [ ] **Step 1: Write failing tests**

Create/append `backend/apps/inventory/tests/test_dispatch_history_service.py`:

```python
"""Test gộp theo SKU của DispatchHistoryService (view Theo SKU)."""
from __future__ import annotations

import pytest

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.dispatch_history_service import (
    DispatchHistoryService,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType
from apps.orders.models import Order, Product
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


def _seed_two_orders_one_sku():
    ch = Channel.objects.create(code="lp-sku", name="SKU")
    cust = Customer.objects.create(
        full_name="K", phone_raw="0900000009", phone_normalized="0900000009"
    )
    wh = Warehouse.objects.create(code="WH-SKU", name="Kho A")
    p1 = Product.objects.create(sku="SKU1", name="Áo")
    p2 = Product.objects.create(sku="SKU2", name="Quần")
    o1 = Order.objects.create(
        channel=ch, customer=cust, name="K", phone="0900000009",
        order_code="DH001", invoice_code="HD001", shipping_code="VD001",
        total_amount="1000",
    )
    o2 = Order.objects.create(
        channel=ch, customer=cust, name="K", phone="0900000009",
        order_code="DH002", invoice_code="HD002", shipping_code="VD002",
        total_amount="1000",
    )
    # SKU1 xuất ở cả 2 đơn (3 + 5), hoàn 1 ở đơn 1 → net = 8 - 1 = 7, order_count = 2
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-3,
        movement_type=MovementType.SALE, reference_type="order", reference_id=o1.id,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=1,
        movement_type=MovementType.SALE_REVERSAL, reference_type="order", reference_id=o1.id,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-5,
        movement_type=MovementType.SALE, reference_type="order", reference_id=o2.id,
    )
    # SKU2 chỉ ở đơn 2 (xuất 2)
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p2.id, qty_delta=-2,
        movement_type=MovementType.SALE, reference_type="order", reference_id=o2.id,
    )
    return wh, o1, o2, p1, p2


def test_skus_page_groups_by_sku():
    _seed_two_orders_one_sku()
    data = DispatchHistoryService.skus_page(pagination={"limit": 50, "offset": 0})
    assert data["total"] == 2  # SKU1, SKU2
    by_sku = {r["sku"]: r for r in data["items"]}
    assert by_sku["SKU1"]["order_count"] == 2
    assert by_sku["SKU1"]["net"] == 7
    assert by_sku["SKU2"]["order_count"] == 1
    assert by_sku["SKU2"]["net"] == 2
    # total_quantity = tổng net toàn bộ = 7 + 2 = 9
    assert data["total_quantity"] == 9


def test_sku_orders_drilldown_lists_orders():
    wh, o1, o2, p1, p2 = _seed_two_orders_one_sku()
    rows = DispatchHistoryService.sku_orders(product_id=p1.id)
    by_order = {r["order_id"]: r for r in rows}
    assert set(by_order) == {o1.id, o2.id}
    assert by_order[o1.id]["net"] == 2  # 3 - 1
    assert by_order[o2.id]["net"] == 5
    assert by_order[o2.id]["invoice_code"] == "HD002"


def test_skus_page_product_filter():
    _seed_two_orders_one_sku()
    data = DispatchHistoryService.skus_page(
        pagination={"limit": 50, "offset": 0}, product="SKU2"
    )
    assert data["total"] == 1
    assert data["items"][0]["sku"] == "SKU2"
```

- [ ] **Step 2: Run tests — verify they fail**

Run (Windows): `docker exec -e POSTGRES_DB=desi_skusvc desi-order-web-test pytest apps/inventory/tests/test_dispatch_history_service.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'skus_page'`.

- [ ] **Step 3: Implement `skus_page` and `sku_orders`**

Append to `dispatch_history_service.py` inside `class DispatchHistoryService` (after `order_lines`):

```python
    @staticmethod
    def skus_page(*, pagination: dict, **filters) -> dict:
        """Danh sách chính view Theo SKU tab Xuất: gộp theo product (mọi đơn)."""
        qs = DispatchHistoryService.base_queryset(**filters)
        grouped = (
            qs.values("product_id", "product__sku", "product__name")
            .annotate(
                sale_sum=Coalesce(Sum("qty_delta", filter=Q(movement_type=_SALE)), 0),
                rev_sum=Coalesce(
                    Sum("qty_delta", filter=Q(movement_type=_SALE_REVERSAL)), 0
                ),
                order_count=Count("reference_id", distinct=True),
                last_at=Max("created_at"),
            )
            .order_by("-last_at", "-product_id")
        )
        total = qs.values("product_id").distinct().count()
        offset = pagination["offset"]
        page = list(grouped[offset : offset + pagination["limit"]])
        items = []
        for r in page:
            dispatched = -r["sale_sum"]
            reversed_qty = r["rev_sum"]
            items.append(
                {
                    "product_id": r["product_id"],
                    "sku": r["product__sku"],
                    "name": r["product__name"],
                    "order_count": r["order_count"],
                    "dispatched": dispatched,
                    "reversed": reversed_qty,
                    "net": dispatched - reversed_qty,
                    "last_at": r["last_at"],
                }
            )
        total_quantity = -qs.aggregate(s=Coalesce(Sum("qty_delta"), 0))["s"]
        return {
            "items": items,
            "total": total,
            "total_quantity": total_quantity,
            "limit": pagination["limit"],
            "offset": offset,
        }

    @staticmethod
    def sku_orders(*, product_id: int, **filters) -> list[dict]:
        """Drill-down: các đơn chứa một SKU (net = dispatched − reversed)."""
        filters.pop("product_id", None)
        filters.pop("product", None)
        qs = DispatchHistoryService.base_queryset(product_id=product_id, **filters)
        grouped = qs.values("reference_id").annotate(
            sale_sum=Coalesce(Sum("qty_delta", filter=Q(movement_type=_SALE)), 0),
            rev_sum=Coalesce(
                Sum("qty_delta", filter=Q(movement_type=_SALE_REVERSAL)), 0
            ),
            last_at=Max("created_at"),
        )
        order_ids = [r["reference_id"] for r in grouped]
        order_map = {
            o["id"]: o
            for o in Order.objects.filter(id__in=order_ids).values(
                "id", "order_code", "invoice_code", "shipping_code"
            )
        }
        wh_names = dict(
            StockMovement.objects.filter(
                reference_type="order", reference_id__in=order_ids
            )
            .order_by("created_at", "id")
            .values_list("reference_id", "warehouse__name")
        )
        rows = []
        for r in grouped:
            oid = r["reference_id"]
            o = order_map.get(oid, {})
            dispatched = -r["sale_sum"]
            reversed_qty = r["rev_sum"]
            rows.append(
                {
                    "order_id": oid,
                    "order_code": o.get("order_code"),
                    "invoice_code": o.get("invoice_code") or None,
                    "shipping_code": o.get("shipping_code"),
                    "warehouse_name": wh_names.get(oid),
                    "net": dispatched - reversed_qty,
                    "last_at": r["last_at"],
                }
            )
        rows.sort(key=lambda x: x["net"], reverse=True)
        return rows
```

- [ ] **Step 4: Run tests — verify pass**

Run: `docker exec -e POSTGRES_DB=desi_skusvc desi-order-web-test pytest apps/inventory/tests/test_dispatch_history_service.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/application/services/dispatch_history_service.py backend/apps/inventory/tests/test_dispatch_history_service.py
git commit -m "feat(inventory): DispatchHistoryService SKU aggregation + drill-down"
```

---

## Task 2: BE — ReturnHistoryService SKU aggregation

**Files:**
- Modify: `backend/apps/inventory/application/services/return_history_service.py`
- Test: `backend/apps/inventory/tests/test_return_history_service.py` (create if absent)

**Interfaces:**
- Consumes: `ReturnHistoryService._base(**filters)` (đã có).
- Produces:
  - `ReturnHistoryService.skus_page(*, pagination: dict, **filters) -> dict` → `{"items": [ {product_id:int, sku:str|None, name:str|None, return_count:int, qty_total:int} ], "total":int, "total_quantity":int, "limit":int, "offset":int}`.
  - `ReturnHistoryService.sku_returns(*, product_id:int, **filters) -> list[dict]` → `[ {return_id:int, code:str, source_order_code:str|None, source_order_invoice_code:str|None, shipping_code:str|None, warehouse_name:str|None, return_type_label:str, quantity:int, created_at:datetime} ]`.

- [ ] **Step 1: Write failing tests**

Create `backend/apps/inventory/tests/test_return_history_service.py`:

```python
"""Test gộp theo SKU của ReturnHistoryService (view Theo SKU tab Nhập hoàn)."""
from __future__ import annotations

import pytest

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_history_service import (
    ReturnHistoryService,
)
from apps.inventory.models import (
    ReturnNote,
    ReturnNoteLine,
    ReturnStatus,
    ReturnType,
)
from apps.orders.models import Order, Product
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


def _seed_returns():
    ch = Channel.objects.create(code="lp-rsku", name="RSKU")
    cust = Customer.objects.create(
        full_name="K", phone_raw="0900000008", phone_normalized="0900000008"
    )
    wh = Warehouse.objects.create(code="WH-RSKU", name="Kho A")
    p1 = Product.objects.create(sku="SKU1", name="Áo")
    p2 = Product.objects.create(sku="SKU2", name="Quần")
    o1 = Order.objects.create(
        channel=ch, customer=cust, name="K", phone="0900000008",
        order_code="DH001", invoice_code="HD001", total_amount="1000",
    )
    rn1 = ReturnNote.objects.create(
        code="TH-0001", warehouse=wh, source_order=o1,
        return_type=ReturnType.DELIVERY_RETURNED, status=ReturnStatus.POSTED,
    )
    rn2 = ReturnNote.objects.create(
        code="TH-0002", warehouse=wh, source_order=o1,
        return_type=ReturnType.DELIVERY_RETURNED, status=ReturnStatus.POSTED,
    )
    # SKU1 hoàn ở 2 phiếu (2 + 3) → qty_total 5, return_count 2
    ReturnNoteLine.objects.create(return_note=rn1, product=p1, quantity=2)
    ReturnNoteLine.objects.create(return_note=rn2, product=p1, quantity=3)
    # SKU2 hoàn 1 ở phiếu 1
    ReturnNoteLine.objects.create(return_note=rn1, product=p2, quantity=1)
    # Phiếu nháp KHÔNG được tính
    draft = ReturnNote.objects.create(
        code="TH-DRAFT", warehouse=wh, source_order=o1,
        return_type=ReturnType.DELIVERY_RETURNED, status=ReturnStatus.DRAFT,
    )
    ReturnNoteLine.objects.create(return_note=draft, product=p1, quantity=9)
    return wh, o1, rn1, rn2, p1, p2


def test_return_skus_page_groups_by_sku():
    _seed_returns()
    data = ReturnHistoryService.skus_page(pagination={"limit": 50, "offset": 0})
    assert data["total"] == 2
    by_sku = {r["sku"]: r for r in data["items"]}
    assert by_sku["SKU1"]["return_count"] == 2
    assert by_sku["SKU1"]["qty_total"] == 5  # draft không tính
    assert by_sku["SKU2"]["qty_total"] == 1
    assert data["total_quantity"] == 6


def test_return_sku_returns_drilldown():
    wh, o1, rn1, rn2, p1, p2 = _seed_returns()
    rows = ReturnHistoryService.sku_returns(product_id=p1.id)
    by_id = {r["return_id"]: r for r in rows}
    assert set(by_id) == {rn1.id, rn2.id}
    assert by_id[rn1.id]["quantity"] == 2
    assert by_id[rn2.id]["quantity"] == 3
    assert by_id[rn1.id]["source_order_invoice_code"] == "HD001"
```

- [ ] **Step 2: Run — verify fail**

Run: `docker exec -e POSTGRES_DB=desi_skursvc desi-order-web-test pytest apps/inventory/tests/test_return_history_service.py -v`
Expected: FAIL — no attribute `skus_page`.

- [ ] **Step 3: Implement**

Append to `return_history_service.py` inside `class ReturnHistoryService` (after `lines`). Add `Count` to the existing `django.db.models` import line (`from django.db.models import Count, Exists, OuterRef, Q, Sum`):

```python
    @staticmethod
    def skus_page(*, pagination: dict, **filters) -> dict:
        """View Theo SKU tab Nhập hoàn: gộp dòng phiếu POSTED theo product."""
        note_qs = ReturnHistoryService._base(**filters)
        line_qs = ReturnNoteLine.objects.filter(return_note__in=note_qs)
        grouped = (
            line_qs.values("product_id", "product__sku", "product__name")
            .annotate(
                qty_total=Coalesce(Sum("quantity"), 0),
                return_count=Count("return_note", distinct=True),
            )
            .order_by("-qty_total", "-product_id")
        )
        total = line_qs.values("product_id").distinct().count()
        offset = pagination["offset"]
        page = list(grouped[offset : offset + pagination["limit"]])
        items = [
            {
                "product_id": r["product_id"],
                "sku": r["product__sku"],
                "name": r["product__name"],
                "return_count": r["return_count"],
                "qty_total": r["qty_total"],
            }
            for r in page
        ]
        total_quantity = line_qs.aggregate(t=Coalesce(Sum("quantity"), 0))["t"]
        return {
            "items": items,
            "total": total,
            "total_quantity": total_quantity,
            "limit": pagination["limit"],
            "offset": offset,
        }

    @staticmethod
    def sku_returns(*, product_id: int, **filters) -> list[dict]:
        """Drill-down: các phiếu trả POSTED chứa một SKU."""
        note_qs = ReturnHistoryService._base(**filters)
        rows = (
            ReturnNoteLine.objects.filter(
                return_note__in=note_qs, product_id=product_id
            )
            .select_related("return_note", "return_note__warehouse", "return_note__source_order")
            .order_by("-return_note__created_at", "-id")
        )
        out = []
        for r in rows:
            n = r.return_note
            out.append(
                {
                    "return_id": n.id,
                    "code": n.code,
                    "source_order_code": (
                        n.source_order.order_code if n.source_order_id else None
                    ),
                    "source_order_invoice_code": (
                        (n.source_order.invoice_code or None)
                        if n.source_order_id
                        else None
                    ),
                    "shipping_code": n.shipping_code or None,
                    "warehouse_name": n.warehouse.name,
                    "return_type_label": _RETURN_TYPE_LABELS.get(n.return_type)
                    or "Chưa phân loại",
                    "quantity": r.quantity,
                    "created_at": n.created_at,
                }
            )
        return out
```

- [ ] **Step 4: Run — verify pass**

Run: `docker exec -e POSTGRES_DB=desi_skursvc desi-order-web-test pytest apps/inventory/tests/test_return_history_service.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/application/services/return_history_service.py backend/apps/inventory/tests/test_return_history_service.py
git commit -m "feat(inventory): ReturnHistoryService SKU aggregation + drill-down"
```

---

## Task 3: BE — SKU endpoints + routes

**Files:**
- Modify: `backend/apps/inventory/transport/views/flow_history_views.py`
- Modify: `backend/apps/inventory/urls.py`
- Test: `backend/apps/inventory/tests/test_flow_history_views.py`

**Interfaces:**
- Consumes: services from Task 1 & 2; existing `_common_filters`, `parse_required_pagination`, `_int_or_none`.
- Produces 4 views: `issue_skus`, `issue_sku_orders`, `return_skus`, `return_sku_returns`. Routes:
  - `GET dispatch-history/skus` → `issue_skus`
  - `GET dispatch-history/skus/orders?product_id=` → `issue_sku_orders`
  - `GET return-history/skus` → `return_skus`
  - `GET return-history/skus/returns?product_id=` → `return_sku_returns`

- [ ] **Step 1: Write failing endpoint tests**

Append to `backend/apps/inventory/tests/test_flow_history_views.py` (reuse `client_admin` fixture + `_seed`; `_seed` creates 1 order with SKU1 dispatched 5 and 1 posted return of qty 2):

```python
def test_issue_skus_endpoint(client_admin):
    _seed()
    r = client_admin.get("/api/inventory/dispatch-history/skus?limit=50&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["sku"] == "SKU1"
    assert body["items"][0]["order_count"] == 1
    assert body["items"][0]["net"] == 5
    assert body["total_quantity"] == 5


def test_issue_sku_orders_endpoint(client_admin):
    _seed()
    p = Product.objects.get(sku="SKU1")
    r = client_admin.get(
        f"/api/inventory/dispatch-history/skus/orders?product_id={p.id}"
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert items[0]["invoice_code"] == "HD001"
    assert items[0]["net"] == 5


def test_issue_sku_orders_requires_product_id(client_admin):
    _seed()
    r = client_admin.get("/api/inventory/dispatch-history/skus/orders")
    assert r.status_code == 400


def test_return_skus_endpoint(client_admin):
    _seed()
    r = client_admin.get("/api/inventory/return-history/skus?limit=50&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["sku"] == "SKU1"
    assert body["items"][0]["return_count"] == 1
    assert body["items"][0]["qty_total"] == 2


def test_return_sku_returns_endpoint(client_admin):
    _seed()
    p = Product.objects.get(sku="SKU1")
    r = client_admin.get(
        f"/api/inventory/return-history/skus/returns?product_id={p.id}"
    )
    assert r.status_code == 200
    assert r.json()["items"][0]["quantity"] == 2
```

- [ ] **Step 2: Run — verify fail**

Run: `docker exec -e POSTGRES_DB=desi_skuviews desi-order-web-test pytest apps/inventory/tests/test_flow_history_views.py -k "sku" -v`
Expected: FAIL — 404 (routes not registered).

- [ ] **Step 3: Add views**

In `flow_history_views.py`, add SKU item serializers + views. Place the issue ones after `issue_export` (before the "Tab Nhập hoàn" section) and the return ones after `return_export`:

```python
_ISSUE_SKU_ITEM = {
    "product_id": drf.IntegerField(),
    "sku": drf.CharField(allow_null=True),
    "name": drf.CharField(allow_null=True),
    "order_count": drf.IntegerField(),
    "dispatched": drf.IntegerField(),
    "reversed": drf.IntegerField(),
    "net": drf.IntegerField(),
    "last_at": drf.DateTimeField(allow_null=True),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "IssueSkusHistory",
            fields={
                "items": inline_serializer(
                    "IssueSkusHistoryItem", many=True, fields=_ISSUE_SKU_ITEM
                ),
                "total": drf.IntegerField(),
                "total_quantity": drf.IntegerField(),
                "limit": drf.IntegerField(),
                "offset": drf.IntegerField(),
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def issue_skus(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    pagination, page_error = parse_required_pagination(
        request, max_limit=200, default_limit=50
    )
    if page_error is not None:
        return page_error
    return Response(
        DispatchHistoryService.skus_page(pagination=pagination, **filters),
        status=status.HTTP_200_OK,
    )


_ISSUE_SKU_ORDER_ITEM = {
    "order_id": drf.IntegerField(),
    "order_code": drf.CharField(allow_null=True),
    "invoice_code": drf.CharField(allow_null=True),
    "shipping_code": drf.CharField(allow_null=True),
    "warehouse_name": drf.CharField(allow_null=True),
    "net": drf.IntegerField(),
    "last_at": drf.DateTimeField(allow_null=True),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "IssueSkuOrders",
            fields={
                "items": inline_serializer(
                    "IssueSkuOrderItem", many=True, fields=_ISSUE_SKU_ORDER_ITEM
                )
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def issue_sku_orders(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    product_id = _int_or_none(request.query_params.get("product_id"))
    if not product_id:
        return Response(
            {"detail": "product_id là bắt buộc."}, status=status.HTTP_400_BAD_REQUEST
        )
    return Response(
        {"items": DispatchHistoryService.sku_orders(product_id=product_id, **filters)},
        status=status.HTTP_200_OK,
    )
```

And after `return_export`:

```python
_RETURN_SKU_ITEM = {
    "product_id": drf.IntegerField(),
    "sku": drf.CharField(allow_null=True),
    "name": drf.CharField(allow_null=True),
    "return_count": drf.IntegerField(),
    "qty_total": drf.IntegerField(),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "ReturnSkusHistory",
            fields={
                "items": inline_serializer(
                    "ReturnSkusHistoryItem", many=True, fields=_RETURN_SKU_ITEM
                ),
                "total": drf.IntegerField(),
                "total_quantity": drf.IntegerField(),
                "limit": drf.IntegerField(),
                "offset": drf.IntegerField(),
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def return_skus(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    pagination, page_error = parse_required_pagination(
        request, max_limit=200, default_limit=50
    )
    if page_error is not None:
        return page_error
    return Response(
        ReturnHistoryService.skus_page(pagination=pagination, **filters),
        status=status.HTTP_200_OK,
    )


_RETURN_SKU_RETURN_ITEM = {
    "return_id": drf.IntegerField(),
    "code": drf.CharField(),
    "source_order_code": drf.CharField(allow_null=True),
    "source_order_invoice_code": drf.CharField(allow_null=True),
    "shipping_code": drf.CharField(allow_null=True),
    "warehouse_name": drf.CharField(allow_null=True),
    "return_type_label": drf.CharField(),
    "quantity": drf.IntegerField(),
    "created_at": drf.DateTimeField(),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "ReturnSkuReturns",
            fields={
                "items": inline_serializer(
                    "ReturnSkuReturnItem", many=True, fields=_RETURN_SKU_RETURN_ITEM
                )
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def return_sku_returns(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    product_id = _int_or_none(request.query_params.get("product_id"))
    if not product_id:
        return Response(
            {"detail": "product_id là bắt buộc."}, status=status.HTTP_400_BAD_REQUEST
        )
    return Response(
        {"items": ReturnHistoryService.sku_returns(product_id=product_id, **filters)},
        status=status.HTTP_200_OK,
    )
```

- [ ] **Step 4: Register routes**

In `urls.py`, extend the flow_history import block to add `issue_skus, issue_sku_orders, return_skus, return_sku_returns`. Add routes — put the more specific `/skus/...` BEFORE `/skus` and before the generic order routes:

```python
    path(
        "dispatch-history/skus/orders",
        issue_sku_orders,
        name="inventory-issue-sku-orders",
    ),
    path("dispatch-history/skus", issue_skus, name="inventory-issue-skus"),
    path(
        "return-history/skus/returns",
        return_sku_returns,
        name="inventory-return-sku-returns",
    ),
    path("return-history/skus", return_skus, name="inventory-return-skus"),
```

Place these lines right after the existing `dispatch-history/export` line and `return-history/export` line respectively (order among sibling `path()` entries doesn't matter in Django since each is an exact match, but grouping keeps it readable).

- [ ] **Step 5: Run — verify pass**

Run: `docker exec -e POSTGRES_DB=desi_skuviews desi-order-web-test pytest apps/inventory/tests/test_flow_history_views.py -k "sku" -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory/transport/views/flow_history_views.py backend/apps/inventory/urls.py backend/apps/inventory/tests/test_flow_history_views.py
git commit -m "feat(inventory): SKU-view endpoints for flow history"
```

---

## Task 4: BE — SKU Excel export

**Files:**
- Modify: `backend/apps/inventory/infrastructure/flow_history_excel.py`
- Modify: `backend/apps/inventory/transport/views/flow_history_views.py` (issue_export, return_export)
- Test: `backend/apps/inventory/tests/test_flow_history_export.py`

**Interfaces:**
- Consumes: `DispatchHistoryService.skus_page`, `ReturnHistoryService.skus_page`.
- Produces: `export_issue_skus(rows, total_quantity=0) -> HttpResponse`, `export_return_skus(rows, total_quantity=0) -> HttpResponse`; `issue_export`/`return_export` branch on `?group=sku`.

- [ ] **Step 1: Write failing export tests**

Append to `backend/apps/inventory/tests/test_flow_history_export.py` (reuse its existing seed/fixture; mirror an existing test — it seeds at least one order + posted return). Assert xlsx content-type and that the header row uses SKU columns:

```python
def test_issue_export_group_sku(client_admin):
    _seed()  # or the module's existing seed helper
    r = client_admin.get("/api/inventory/dispatch-history/export?group=sku")
    assert r.status_code == 200
    assert r["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    assert [c.value for c in ws[1]] == ["SKU", "Tên", "Số đơn", "SL xuất"]


def test_return_export_group_sku(client_admin):
    _seed()
    r = client_admin.get("/api/inventory/return-history/export?group=sku")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb.active
    assert [c.value for c in ws[1]] == ["SKU", "Tên", "Số phiếu", "SL hoàn"]
```

Ensure the test module has `import io` and `from openpyxl import load_workbook` (add if missing). If the module uses a different seed helper name, use that helper instead of `_seed`.

- [ ] **Step 2: Run — verify fail**

Run: `docker exec -e POSTGRES_DB=desi_skuxl desi-order-web-test pytest apps/inventory/tests/test_flow_history_export.py -k "group_sku" -v`
Expected: FAIL — header mismatch (still exporting order columns).

- [ ] **Step 3: Add Excel builders**

Append to `flow_history_excel.py`:

```python
def export_issue_skus(rows: list[dict], total_quantity: int = 0) -> HttpResponse:
    headers = ["SKU", "Tên", "Số đơn", "SL xuất"]
    data = [
        [
            r.get("sku") or "",
            r.get("name") or "",
            r.get("order_count") or 0,
            r.get("net") or 0,
        ]
        for r in rows
    ]
    data.append(["Tổng", "", "", total_quantity])
    return _workbook_response("Xuat theo SKU", headers, data, "lich-su-xuat-sku.xlsx")


def export_return_skus(rows: list[dict], total_quantity: int = 0) -> HttpResponse:
    headers = ["SKU", "Tên", "Số phiếu", "SL hoàn"]
    data = [
        [
            r.get("sku") or "",
            r.get("name") or "",
            r.get("return_count") or 0,
            r.get("qty_total") or 0,
        ]
        for r in rows
    ]
    data.append(["Tổng", "", "", total_quantity])
    return _workbook_response("Nhap hoan theo SKU", headers, data, "lich-su-nhap-hoan-sku.xlsx")
```

- [ ] **Step 4: Branch export views on `group`**

In `flow_history_views.py`, update the import from `flow_history_excel` to include `export_issue_skus, export_return_skus`. Then modify `issue_export`:

```python
@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def issue_export(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    if request.query_params.get("group") == "sku":
        data = DispatchHistoryService.skus_page(
            pagination={"limit": 5000, "offset": 0}, **filters
        )
        return export_issue_skus(data["items"], data["total_quantity"])
    data = DispatchHistoryService.orders_page(
        pagination={"limit": 5000, "offset": 0}, **filters
    )
    pending = PendingScanService.list_unmatched(
        kind=PendingScanKind.DISPATCH,
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        warehouse_id=filters["warehouse_id"],
    )
    return export_issue_orders(data["items"], data["total_quantity"], pending)
```

And `return_export`:

```python
@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def return_export(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    if request.query_params.get("group") == "sku":
        data = ReturnHistoryService.skus_page(
            pagination={"limit": 5000, "offset": 0}, **filters
        )
        return export_return_skus(data["items"], data["total_quantity"])
    data = ReturnHistoryService.list_page(
        pagination={"limit": 5000, "offset": 0}, **filters
    )
    pending = PendingScanService.list_unmatched(
        kind=PendingScanKind.RETURN,
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        warehouse_id=filters["warehouse_id"],
        with_return_note=True,
    )
    return export_return_notes(data["items"], data["total_quantity"], pending)
```

- [ ] **Step 5: Run — verify pass**

Run: `docker exec -e POSTGRES_DB=desi_skuxl desi-order-web-test pytest apps/inventory/tests/test_flow_history_export.py -v`
Expected: all pass (new + existing).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory/infrastructure/flow_history_excel.py backend/apps/inventory/transport/views/flow_history_views.py backend/apps/inventory/tests/test_flow_history_export.py
git commit -m "feat(inventory): SKU-grouped Excel export for flow history"
```

---

## Task 5: FE — regenerate schema + typed API module

**Files:**
- Modify: `frontend/src/api/schema.d.ts` (generated)
- Modify: `frontend/src/lib/api/inventory-flow.ts`

**Interfaces:**
- Consumes: OpenAPI schema components from Task 3 (`IssueSkusHistory`, `IssueSkusHistoryItem`, `IssueSkuOrders`, `IssueSkuOrderItem`, `ReturnSkusHistory`, `ReturnSkusHistoryItem`, `ReturnSkuReturns`, `ReturnSkuReturnItem`).
- Produces: exported TS types in `inventory-flow.ts`.

- [ ] **Step 1: Regenerate OpenAPI schema + TS types**

Backend must be able to emit schema. From repo root:

```bash
docker exec desi-order-web-test python manage.py spectacular --file schema.yaml
```

Copy resulting `backend/schema.yaml` if the FE `gen:api` reads it, then:

```bash
cd frontend && npm run gen:api
```

Expected: `src/api/schema.d.ts` now contains `IssueSkusHistory`, `IssueSkuOrders`, `ReturnSkusHistory`, `ReturnSkuReturns` (grep to confirm):

```bash
grep -E "IssueSkusHistory|ReturnSkuReturns" src/api/schema.d.ts
```

- [ ] **Step 2: Add typed aliases**

Append to `frontend/src/lib/api/inventory-flow.ts`:

```typescript
// View Theo SKU — tab Xuất
export type IssueSkusHistory = components["schemas"]["IssueSkusHistory"];
export type IssueSkusHistoryItem = components["schemas"]["IssueSkusHistoryItem"];
export type IssueSkuOrders = components["schemas"]["IssueSkuOrders"];
export type IssueSkuOrderItem = components["schemas"]["IssueSkuOrderItem"];

// View Theo SKU — tab Nhập hoàn
export type ReturnSkusHistory = components["schemas"]["ReturnSkusHistory"];
export type ReturnSkusHistoryItem = components["schemas"]["ReturnSkusHistoryItem"];
export type ReturnSkuReturns = components["schemas"]["ReturnSkuReturns"];
export type ReturnSkuReturnItem = components["schemas"]["ReturnSkuReturnItem"];
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors referencing inventory-flow.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/schema.d.ts frontend/src/lib/api/inventory-flow.ts backend/schema.yaml
git commit -m "feat(inventory): typed FE schema for SKU-view endpoints"
```

---

## Task 6: FE — query hooks + export group param

**Files:**
- Modify: `frontend/src/features/inventory/issue-history-queries.ts`
- Modify: `frontend/src/features/inventory/return-history-queries.ts`

**Interfaces:**
- Consumes: types from Task 5; existing `buildFlowParams`, `downloadXlsxExport`.
- Produces:
  - issue: `useIssueSkus(filters, page, enabled?)`, `useIssueSkuOrders(productId, filters, enabled)`, `downloadIssueExport(filters, group?: "order"|"sku")`.
  - return: `useReturnSkus(filters, page, enabled?)`, `useReturnSkuReturns(productId, filters, enabled)`, `downloadReturnExport(filters, group?: "order"|"sku")`.

- [ ] **Step 1: Add issue hooks + export group param**

In `issue-history-queries.ts`, extend imports:

```typescript
import type {
  FlowPendingScans,
  IssueOrderLines,
  IssueOrdersHistory,
  IssueSkusHistory,
  IssueSkuOrders,
} from "@/lib/api/inventory-flow";
```

Add hooks (after `useIssuePending`):

```typescript
// View Theo SKU tab Xuất: gộp theo SKU. Ẩn khi lọc "Chưa khớp"? Không —
// pending không áp dụng cho SKU view; view này luôn hiện dữ liệu đã ghi sổ.
export function useIssueSkus(
  filters: FlowFilters,
  page: { limit: number; offset: number },
  enabled = true,
) {
  const params = buildFlowParams(filters);
  params.set("limit", String(page.limit));
  params.set("offset", String(page.offset));
  return useQuery({
    queryKey: ["inventory", "issue-skus", params.toString()],
    queryFn: () =>
      fetchJson(
        `/api/inventory/dispatch-history/skus?${params.toString()}`,
      ) as Promise<IssueSkusHistory>,
    staleTime: 10_000,
    enabled,
  });
}

// Drill-down: các đơn chứa một SKU.
export function useIssueSkuOrders(
  productId: number | null,
  filters: FlowFilters,
  enabled: boolean,
) {
  const params = buildFlowParams(filters);
  if (productId) params.set("product_id", String(productId));
  return useQuery({
    queryKey: ["inventory", "issue-sku-orders", params.toString()],
    queryFn: () =>
      fetchJson(
        `/api/inventory/dispatch-history/skus/orders?${params.toString()}`,
      ) as Promise<IssueSkuOrders>,
    staleTime: 10_000,
    enabled,
  });
}
```

Update `downloadIssueExport` to accept a group:

```typescript
export function downloadIssueExport(
  filters: FlowFilters,
  group: "order" | "sku" = "order",
): Promise<void> {
  const params = buildFlowParams(filters);
  if (group === "sku") params.set("group", "sku");
  const suffix = group === "sku" ? "-sku" : "";
  return downloadXlsxExport(
    `/api/inventory/dispatch-history/export?${params.toString()}`,
    `lich-su-xuat${suffix}.xlsx`,
  );
}
```

- [ ] **Step 2: Add return hooks + export group param**

In `return-history-queries.ts`, extend imports:

```typescript
import type {
  FlowPendingScans,
  ReturnHistory,
  ReturnHistoryLines,
  ReturnSkusHistory,
  ReturnSkuReturns,
} from "@/lib/api/inventory-flow";
```

Add hooks (after `useReturnPending`):

```typescript
export function useReturnSkus(
  filters: FlowFilters,
  page: { limit: number; offset: number },
  enabled = true,
) {
  const params = buildFlowParams(filters);
  params.set("limit", String(page.limit));
  params.set("offset", String(page.offset));
  return useQuery({
    queryKey: ["inventory", "return-skus", params.toString()],
    queryFn: () =>
      fetchJson(
        `/api/inventory/return-history/skus?${params.toString()}`,
      ) as Promise<ReturnSkusHistory>,
    staleTime: 10_000,
    enabled,
  });
}

export function useReturnSkuReturns(
  productId: number | null,
  filters: FlowFilters,
  enabled: boolean,
) {
  const params = buildFlowParams(filters);
  if (productId) params.set("product_id", String(productId));
  return useQuery({
    queryKey: ["inventory", "return-sku-returns", params.toString()],
    queryFn: () =>
      fetchJson(
        `/api/inventory/return-history/skus/returns?${params.toString()}`,
      ) as Promise<ReturnSkuReturns>,
    staleTime: 10_000,
    enabled,
  });
}
```

Update `downloadReturnExport`:

```typescript
export function downloadReturnExport(
  filters: FlowFilters,
  group: "order" | "sku" = "order",
): Promise<void> {
  const params = buildFlowParams(filters);
  if (group === "sku") params.set("group", "sku");
  const suffix = group === "sku" ? "-sku" : "";
  return downloadXlsxExport(
    `/api/inventory/return-history/export?${params.toString()}`,
    `lich-su-nhap-hoan${suffix}.xlsx`,
  );
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/inventory/issue-history-queries.ts frontend/src/features/inventory/return-history-queries.ts
git commit -m "feat(inventory): FE query hooks for SKU-view flow history"
```

---

## Task 7: FE — extract shared primitives + tab components (refactor, no behavior change)

**Files:**
- Create: `frontend/src/features/inventory/components/flow-shared.tsx`
- Create: `frontend/src/features/inventory/components/IssueTab.tsx`
- Create: `frontend/src/features/inventory/components/ReturnTab.tsx`
- Modify: `frontend/src/features/inventory/pages/DispatchHistoryPage.tsx`

**Interfaces:**
- Produces (exports from `flow-shared.tsx`): `PAGE_SIZE`, `ExportButton`, `Pager`, `InlineNoteCell`, `ReturnNoteCell`, `PendingNoteCell`, `PendingBadge`, `PendingTime`, `PendingActions`.
- Produces: `IssueTab({ filters, matchFilter })`, `ReturnTab({ filters, matchFilter })` — same props/behavior as current inline versions.

This task is a pure move: cut the existing components out of `DispatchHistoryPage.tsx` into new files, wire imports, no logic change. The `viewMode` prop is added in Task 8.

- [ ] **Step 1: Create `flow-shared.tsx`**

Move these from `DispatchHistoryPage.tsx` verbatim into `frontend/src/features/inventory/components/flow-shared.tsx` and `export` each: `PAGE_SIZE` constant, `ExportButton`, `PendingBadge`, `PendingTime`, `PendingActions`, `InlineNoteCell`, `ReturnNoteCell`, `PendingNoteCell`, `Pager`. Carry over the imports they need (`useState/useRef/useEffect/useCallback` from react, `Input`, `pendingBadge`/`pendingRowTimestamp` from `../pending-scan-display`, `FlowPendingScanItem` type, `useFlowMarkInvalid`/`useFlowDeletePending`/`useFlowUpdatePendingNote` from `../flow-match-queries`, `useUpdateReturnNote` from `../return-history-queries`). Note the relative import depth changes from `../` to `../` is now `../` → `../` (files move from `pages/` to `components/`, both one level under `features/inventory/`, so `../pending-scan-display` stays `../pending-scan-display`).

- [ ] **Step 2: Create `IssueTab.tsx` and `ReturnTab.tsx`**

Move the `IssueTab` and `ReturnTab` functions from `DispatchHistoryPage.tsx` into their own files under `components/`. Each imports the shared primitives from `./flow-shared` and its query hooks from `../issue-history-queries` / `../return-history-queries`, plus `PendingMatchPanel` from `../PendingMatchControl`. Export as named exports. Keep bodies identical to current.

- [ ] **Step 3: Slim down `DispatchHistoryPage.tsx`**

`DispatchHistoryPage.tsx` keeps only: imports for `PageHeader`, `Input`, `Select*`, `Tabs`, `DateFilter`, `fetchJson`, warehouse type, `FlowFilters`/`MatchFilter` types, and now `IssueTab`/`ReturnTab` from `./components/IssueTab` / `./components/ReturnTab`. The `tabs` array references imported `IssueTab`/`ReturnTab`. Delete the moved function bodies from this file.

- [ ] **Step 4: Typecheck + existing tests still pass**

Run:
```bash
cd frontend && npx tsc --noEmit && npm run test -- flow-update-pending-note
```
Expected: typecheck clean; existing `flow-update-pending-note.test.tsx` passes (may need its import path updated if it imported a moved component — update the import to `./components/...` or `../components/...` as needed).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/components/ frontend/src/features/inventory/pages/DispatchHistoryPage.tsx frontend/src/features/inventory/__tests__/
git commit -m "refactor(inventory): split flow-history page into tab + shared components"
```

---

## Task 8: FE — viewMode toggle + SKU view rendering

**Files:**
- Create: `frontend/src/features/inventory/components/ViewModeToggle.tsx`
- Modify: `frontend/src/features/inventory/pages/DispatchHistoryPage.tsx`
- Modify: `frontend/src/features/inventory/components/IssueTab.tsx`
- Modify: `frontend/src/features/inventory/components/ReturnTab.tsx`
- Test: `frontend/src/features/inventory/__tests__/flow-sku-view.test.tsx`

**Interfaces:**
- Consumes: hooks from Task 6; components from Task 7.
- Produces: `ViewMode = "order" | "sku"`; `ViewModeToggle({ value, onChange })`; `IssueTab`/`ReturnTab` gain a `viewMode: ViewMode` prop.

- [ ] **Step 1: Write failing FE test**

Create `frontend/src/features/inventory/__tests__/flow-sku-view.test.tsx`. Follow the render/query-client harness used by `flow-update-pending-note.test.tsx` (copy its provider/setup). Mock `fetchJson` to return an issue-skus payload when the URL contains `dispatch-history/skus` and an orders payload otherwise. Test: switching the toggle to "Theo SKU" renders the SKU table headers ("Số đơn", "SL xuất") and hides pending rows.

```tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import DispatchHistoryPage from "../pages/DispatchHistoryPage";
import * as http from "@/services/http";

vi.mock("@/services/http", async (orig) => {
  const actual = await orig<typeof import("@/services/http")>();
  return { ...actual, fetchJson: vi.fn() };
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DispatchHistoryPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(http.fetchJson).mockImplementation((url: string) => {
    if (url.includes("/warehouses")) return Promise.resolve({ items: [] });
    if (url.includes("dispatch-history/skus")) {
      return Promise.resolve({
        items: [
          { product_id: 1, sku: "SKU1", name: "Áo", order_count: 2, dispatched: 8, reversed: 1, net: 7, last_at: "2026-07-01T00:00:00Z" },
        ],
        total: 1, total_quantity: 7, limit: 50, offset: 0,
      });
    }
    if (url.includes("dispatch-history/orders")) {
      return Promise.resolve({ items: [], total: 0, total_quantity: 0, limit: 50, offset: 0 });
    }
    if (url.includes("pending")) return Promise.resolve({ items: [] });
    return Promise.resolve({ items: [] });
  });
});

describe("SKU view", () => {
  it("switches to SKU grouping and shows SKU columns", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /Theo SKU/i }));
    await waitFor(() => expect(screen.getByText("SKU1")).toBeInTheDocument());
    expect(screen.getByText("Số đơn")).toBeInTheDocument();
    expect(screen.getByText("SL xuất")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — verify fail**

Run: `cd frontend && npm run test -- flow-sku-view`
Expected: FAIL — no "Theo SKU" button yet.

- [ ] **Step 3: Create `ViewModeToggle.tsx`**

```tsx
export type ViewMode = "order" | "sku";

const OPTIONS: { key: ViewMode; label: string }[] = [
  { key: "order", label: "Theo đơn" },
  { key: "sku", label: "Theo SKU" },
];

export function ViewModeToggle({
  value,
  onChange,
}: {
  value: ViewMode;
  onChange: (v: ViewMode) => void;
}) {
  return (
    <div className="inline-flex rounded-md border border-border p-0.5">
      {OPTIONS.map((o) => (
        <button
          key={o.key}
          type="button"
          onClick={() => onChange(o.key)}
          className={[
            "rounded px-3 py-1 text-sm transition-colors",
            value === o.key
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground",
          ].join(" ")}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Wire toggle into shell**

In `DispatchHistoryPage.tsx`: add `const [viewMode, setViewMode] = useState<ViewMode>("order")`; render `<ViewModeToggle value={viewMode} onChange={setViewMode} />` at the end of the filter row `div`; pass `viewMode` to both `IssueTab` and `ReturnTab`. Include `viewMode` in `filtersKey` so tabs remount on change is NOT desired — instead pass `viewMode` as a normal prop (do not add to the remount key).

- [ ] **Step 5: Add SKU view to `IssueTab`**

Add `viewMode: ViewMode` to `IssueTab` props. Split render: when `viewMode === "order"`, keep the existing order table. When `"sku"`, render a SKU table using `useIssueSkus(filters, {limit: PAGE_SIZE, offset})` + `useIssueSkuOrders(openProduct, filters, openProduct != null)`; no pending rows; export button calls `downloadIssueExport(filters, "sku")`. Columns: `SKU · Tên · Số đơn · SL xuất`; drill sub-table columns `Mã hóa đơn · Mã vận đơn · Kho · SL xuất`; tfoot `Tổng SL xuất ({data.total} SKU)`.

```tsx
// inside IssueTab, sku branch
const skuList = useIssueSkus(filters, { limit: PAGE_SIZE, offset }, viewMode === "sku");
const skuOrders = useIssueSkuOrders(openProduct, filters, viewMode === "sku" && openProduct != null);
```

Render the SKU table analogous to the order table but keyed by `product_id`, drill row rendering `skuOrders.data?.items` with `invoice_code ?? order_code`, `shipping_code`, `warehouse_name`, `net`.

- [ ] **Step 6: Add SKU view to `ReturnTab`**

Same pattern: `viewMode` prop; `"sku"` branch uses `useReturnSkus` + `useReturnSkuReturns`, export `downloadReturnExport(filters, "sku")`. Columns `SKU · Tên · Số phiếu · SL hoàn`; drill sub-table `Mã phiếu trả · Mã hóa đơn nguồn · Mã vận đơn · Kho · Loại hoàn · SL`; tfoot `Tổng SL hoàn ({data.total} SKU)`.

- [ ] **Step 7: Run — verify pass**

Run: `cd frontend && npm run test -- flow-sku-view && npx tsc --noEmit`
Expected: pass; typecheck clean.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/inventory/
git commit -m "feat(inventory): SKU view toggle for flow history page"
```

---

## Task 9: Full verification + lint

- [ ] **Step 1: Backend full inventory suite**

Run: `docker exec -e POSTGRES_DB=desi_flowsku_full desi-order-web-test pytest apps/inventory/tests/test_flow_history_views.py apps/inventory/tests/test_flow_history_export.py apps/inventory/tests/test_dispatch_history_service.py apps/inventory/tests/test_return_history_service.py -v`
Expected: all pass.

- [ ] **Step 2: Backend ruff**

Run: `docker exec desi-order-web-test ruff check apps/inventory/`
Expected: no new violations.

- [ ] **Step 3: Frontend lint + typecheck + test**

Run: `cd frontend && npm run lint && npx tsc --noEmit && npm run test -- inventory`
Expected: no `no-restricted-imports` (react-select) violations; typecheck clean; tests pass.

- [ ] **Step 4: Manual smoke (verify skill)**

Use the `verify` skill or manual: open `/inventory/dispatch-history`, toggle Theo SKU on both tabs, drill a SKU row, export Excel in SKU mode, confirm pending rows hidden in SKU view and present in order view.

- [ ] **Step 5: Push + open MR**

```bash
git push -u origin feat/flow-history-sku-view
```
Then open MR to `master`, paste link.

---

## Self-Review Notes

- **Spec coverage:** toggle both tabs (Task 8) ✓; group-by-SKU + drill (Tasks 1–3, 8) ✓; hide pending in SKU view (Tasks 6 note, 8) ✓; Excel by view (Task 4, 6, 8) ✓; endpoint-per-view typing (Task 3, 5) ✓; file split (Task 7) ✓; default order view unchanged (Task 7 no-behavior-change, Task 8 prop) ✓.
- **Type consistency:** service return dicts (Task 1/2) match serializer fields (Task 3) match TS aliases (Task 5) match hook generics (Task 6). `net`, `order_count`, `return_count`, `qty_total` names consistent throughout.
- **Naming:** endpoints `issue_skus`/`issue_sku_orders`/`return_skus`/`return_sku_returns`; schema components `IssueSkusHistory`/`IssueSkuOrders`/`ReturnSkusHistory`/`ReturnSkuReturns`; hooks `useIssueSkus`/`useIssueSkuOrders`/`useReturnSkus`/`useReturnSkuReturns` — consistent.
- **Note (Task 8 Step 4):** `viewMode` passed as prop, NOT added to remount key — switching view should refetch via hook `enabled`, not remount + lose offset unexpectedly. Acceptable if offset resets on view change; implementer may reset `offset` to 0 when `viewMode` changes via a small `useEffect` in each tab.
