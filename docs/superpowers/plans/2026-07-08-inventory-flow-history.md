# Lịch sử nhập xuất — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đổi trang "Lịch sử xuất kho" thành "Lịch sử nhập xuất" với 2 danh sách nhóm-theo-đơn (Xuất hàng ra / Nhập hàng hoàn về) + xuất Excel theo bộ lọc.

**Architecture:** Backend Django clean-arch: mở rộng `DispatchHistoryService` (gộp `StockMovement sale/sale_reversal` theo đơn) + service mới `ReturnHistoryService` (danh sách `ReturnNote` đã ghi sổ). 6 endpoint GET dưới `/api/inventory/`, RBAC `stock`. Frontend: rework `DispatchHistoryPage` dùng `components/ui/tabs`, React Query per-feature, API typed từ `schema.d.ts`, nút export tải blob xlsx.

**Tech Stack:** Django REST Framework, drf-spectacular, openpyxl, React + TypeScript, TanStack Query, Vitest, pytest.

## Global Constraints

- Backend: clean architecture — business logic trong `application/services/`, transport chỉ ở `transport/views/`. Không đặt query DB trong view ngoài việc gọi service.
- Backend Python 3.12+, ruff sạch. Tránh N+1: resolve nhãn theo batch.
- RBAC tất cả endpoint mới: `@permission_classes([IsAuthenticated, require_section("stock")])`.
- Frontend code MỚI: GET dùng React Query (`useQuery` + `queries.ts` per-feature); API typed từ `src/api/schema.d.ts` (không khai báo type tay cho response); CẤM `react-select`.
- Giữ URL `/inventory/dispatch-history` + route key `dispatch-history` (chỉ đổi tiêu đề/nội dung).
- OpenAPI là hợp đồng: sau khi đổi API backend phải chạy `python manage.py spectacular --file schema.yaml` rồi `npm run gen:api`, commit cả hai — nếu không CI đỏ.
- Nhập hoàn CHỈ lấy `ReturnNote.status = "posted"`.
- Excel: 1 dòng = 1 đơn/phiếu khớp danh sách trên màn hình.

### Lệnh chạy test (Windows host, Git Bash) — dùng verbatim

Backend (thay `<paths>`):
```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -e POSTGRES_DB=desi_flowhist \
  -v "$PWD/backend:/app" -w /app desi-order-web-test:latest pytest <paths> -q
```
(Nếu network/redis khác, xác minh bằng `docker ps` — xem memory running-backend-tests. `-e POSTGRES_DB=desi_flowhist` bắt buộc để tránh test_desi hỏng.)

Frontend (trong `frontend/`): `npm run test:ci`, `npm run typecheck`, `npm run lint:strict`, `npm run build`.

Regen schema (trong `backend/`, container hoặc host có deps): `python manage.py spectacular --file schema.yaml`.

---

## File Structure

**Backend (tạo mới):**
- `apps/inventory/application/services/return_history_service.py` — `ReturnHistoryService`.
- `apps/inventory/infrastructure/flow_history_excel.py` — 2 hàm export xlsx.
- `apps/inventory/transport/views/flow_history_views.py` — 6 view GET.
- `apps/inventory/tests/test_issue_orders_history.py`
- `apps/inventory/tests/test_return_history.py`
- `apps/inventory/tests/test_flow_history_export.py`

**Backend (sửa):**
- `apps/inventory/application/services/dispatch_history_service.py` — thêm `order_id` filter, `orders_page()`, `order_lines()`.
- `apps/inventory/urls.py` — 6 route mới; xoá 2 route cũ (Task 7).

**Frontend (tạo mới):**
- `frontend/src/lib/api/inventory-flow.ts` — typed từ schema.d.ts.
- `frontend/src/features/inventory/issue-history-queries.ts`
- `frontend/src/features/inventory/return-history-queries.ts`
- `frontend/src/features/inventory/__tests__/flow-history-queries.test.ts`

**Frontend (sửa):**
- `frontend/src/features/inventory/pages/DispatchHistoryPage.tsx` — rework tabs.
- `frontend/src/i18n/messages/topnav.ts` — đổi nhãn.

**Xoá (Task 7):**
- BE: view `dispatch_history_summary/detail` + route; `test_dispatch_history_summary.py`, `test_dispatch_history_detail.py`.
- FE: `dispatch-history-queries.ts`, `__tests__/dispatch-history-queries.test.ts`.

---

## Task 1: Service — danh sách đơn Xuất (issue orders)

**Files:**
- Modify: `backend/apps/inventory/application/services/dispatch_history_service.py`
- Test: `backend/apps/inventory/tests/test_issue_orders_history.py`

**Interfaces:**
- Consumes: `StockMovement`, `Order` (đã import), `parse_required_pagination` (dùng ở view).
- Produces:
  - `DispatchHistoryService.base_queryset(..., order_id: int | None = None)` — thêm param.
  - `DispatchHistoryService.orders_page(*, pagination: dict, **filters) -> dict` → `{"items": [ {order_id, order_code, shipping_code, warehouse_name, sku_count, dispatched, reversed, net, last_at} ], "total": int, "limit": int, "offset": int}`. `total` = số đơn (distinct reference_id). Sắp xếp theo `last_at` giảm dần.
  - `DispatchHistoryService.order_lines(*, order_id: int, **filters) -> list[dict]` → mỗi item `{product_id, sku, name, dispatched, reversed, net}` cho 1 đơn.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_issue_orders_history.py`:

```python
import pytest
from django.utils import timezone

from apps.inventory.application.services.dispatch_history_service import (
    DispatchHistoryService,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType
from apps.orders.models import Order, Product
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


def _seed():
    wh = Warehouse.objects.create(name="Kho A")
    p1 = Product.objects.create(sku="SKU1", name="Áo")
    p2 = Product.objects.create(sku="SKU2", name="Quần")
    order = Order.objects.create(order_code="DH001", shipping_code="VD001")
    ref = {"reference_type": "order", "reference_id": order.id}
    # Đơn xuất 2 SKU, hoàn 1 phần SKU1.
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-5,
        movement_type=MovementType.SALE, **ref,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p2.id, qty_delta=-3,
        movement_type=MovementType.SALE, **ref,
    )
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=2,
        movement_type=MovementType.SALE_REVERSAL, **ref,
    )
    return wh, order, p1, p2


def test_orders_page_groups_by_order_with_net():
    wh, order, p1, p2 = _seed()
    result = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0}
    )
    assert result["total"] == 1
    row = result["items"][0]
    assert row["order_id"] == order.id
    assert row["order_code"] == "DH001"
    assert row["shipping_code"] == "VD001"
    assert row["warehouse_name"] == "Kho A"
    assert row["sku_count"] == 2
    assert row["dispatched"] == 8        # 5 + 3
    assert row["reversed"] == 2
    assert row["net"] == 6               # 8 - 2
    assert row["last_at"] is not None


def test_order_lines_returns_per_sku_net():
    wh, order, p1, p2 = _seed()
    lines = DispatchHistoryService.order_lines(order_id=order.id)
    by_sku = {l["sku"]: l for l in lines}
    assert by_sku["SKU1"]["dispatched"] == 5
    assert by_sku["SKU1"]["reversed"] == 2
    assert by_sku["SKU1"]["net"] == 3
    assert by_sku["SKU2"]["net"] == 3


def test_orders_page_order_filter():
    wh, order, p1, p2 = _seed()
    empty = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0}, order="NOPE"
    )
    assert empty["total"] == 0
    hit = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0}, order="VD001"
    )
    assert hit["total"] == 1
```

- [ ] **Step 2: Chạy test — kỳ vọng FAIL**

Run: lệnh backend test với `<paths>` = `apps/inventory/tests/test_issue_orders_history.py`
Expected: FAIL (`AttributeError: ... has no attribute 'orders_page'`).

- [ ] **Step 3: Cài đặt service**

Sửa `backend/apps/inventory/application/services/dispatch_history_service.py`. Thêm import `Count`, `Max`, `Min` từ `django.db.models`, và `abs`-net logic. Cập nhật `base_queryset` thêm `order_id`, thêm 2 method:

```python
from __future__ import annotations

from django.db.models import Count, Max, Q, QuerySet, Sum
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
        product_id: int | None = None,
        order: str = "",
        order_id: int | None = None,
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
        if product_id:
            qs = qs.filter(product_id=product_id)
        if order_id:
            qs = qs.filter(reference_id=order_id)
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

    # ── giữ nguyên summary()/detail_queryset() hiện có ──

    @staticmethod
    def orders_page(*, pagination: dict, **filters) -> dict:
        qs = DispatchHistoryService.base_queryset(**filters)
        grouped = (
            qs.values("reference_id")
            .annotate(
                sale_sum=Coalesce(Sum("qty_delta", filter=Q(movement_type=_SALE)), 0),
                rev_sum=Coalesce(
                    Sum("qty_delta", filter=Q(movement_type=_SALE_REVERSAL)), 0
                ),
                sku_count=Count("product_id", distinct=True),
                last_at=Max("created_at"),
            )
            .order_by("-last_at")
        )
        total = qs.values("reference_id").distinct().count()
        offset = pagination["offset"]
        page = list(grouped[offset : offset + pagination["limit"]])

        order_ids = [r["reference_id"] for r in page]
        order_map = {
            o["id"]: o
            for o in Order.objects.filter(id__in=order_ids).values(
                "id", "order_code", "shipping_code"
            )
        }
        # Nhãn kho: mỗi đơn có thể nhiều kho — lấy tên kho của movement mới nhất/đầu tiên.
        wh_names = dict(
            StockMovement.objects.filter(
                reference_type="order", reference_id__in=order_ids
            )
            .values_list("reference_id", "warehouse__name")
        )
        items = []
        for r in page:
            oid = r["reference_id"]
            o = order_map.get(oid, {})
            dispatched = -r["sale_sum"]
            reversed_qty = r["rev_sum"]
            items.append(
                {
                    "order_id": oid,
                    "order_code": o.get("order_code"),
                    "shipping_code": o.get("shipping_code"),
                    "warehouse_name": wh_names.get(oid),
                    "sku_count": r["sku_count"],
                    "dispatched": dispatched,
                    "reversed": reversed_qty,
                    "net": dispatched - reversed_qty,
                    "last_at": r["last_at"],
                }
            )
        return {
            "items": items,
            "total": total,
            "limit": pagination["limit"],
            "offset": offset,
        }

    @staticmethod
    def order_lines(*, order_id: int, **filters) -> list[dict]:
        filters.pop("order_id", None)
        qs = DispatchHistoryService.base_queryset(order_id=order_id, **filters)
        rows = qs.values("product_id", "product__sku", "product__name").annotate(
            sale_sum=Coalesce(Sum("qty_delta", filter=Q(movement_type=_SALE)), 0),
            rev_sum=Coalesce(
                Sum("qty_delta", filter=Q(movement_type=_SALE_REVERSAL)), 0
            ),
        )
        lines = []
        for r in rows:
            dispatched = -r["sale_sum"]
            reversed_qty = r["rev_sum"]
            lines.append(
                {
                    "product_id": r["product_id"],
                    "sku": r["product__sku"],
                    "name": r["product__name"],
                    "dispatched": dispatched,
                    "reversed": reversed_qty,
                    "net": dispatched - reversed_qty,
                }
            )
        lines.sort(key=lambda x: x["net"], reverse=True)
        return lines
```

> LƯU Ý: giữ nguyên `summary()` và `detail_queryset()` cũ trong file (Task 7 sẽ xoá cùng view cũ). Chỉ THÊM, không xoá ở task này.

- [ ] **Step 4: Chạy test — kỳ vọng PASS**

Run: lệnh backend test với `apps/inventory/tests/test_issue_orders_history.py`
Expected: 3 passed.

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/application/services/dispatch_history_service.py backend/apps/inventory/tests/test_issue_orders_history.py
git add backend/apps/inventory/application/services/dispatch_history_service.py backend/apps/inventory/tests/test_issue_orders_history.py
git commit -m "feat: issue orders history grouped by order"
```

---

## Task 2: Service — danh sách Nhập hoàn (return history)

**Files:**
- Create: `backend/apps/inventory/application/services/return_history_service.py`
- Test: `backend/apps/inventory/tests/test_return_history.py`

**Interfaces:**
- Consumes: `ReturnNote`, `ReturnNoteLine`, `ReturnStatus`, `ReturnType` từ `apps.inventory.models`.
- Produces:
  - `ReturnHistoryService.list_page(*, pagination: dict, date_from="", date_to="", warehouse_id=None, product="", order="") -> dict` → `{"items": [ {return_id, code, source_order_id, source_order_code, shipping_code, warehouse_name, return_type, return_type_label, status, status_label, qty_total, created_at} ], "total", "limit", "offset"}`. Chỉ `status=posted`. Sắp xếp `created_at` giảm.
  - `ReturnHistoryService.lines(*, return_id: int) -> list[dict]` → `{product_id, sku, name, quantity, return_type, return_type_label}`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_return_history.py`:

```python
import pytest

from apps.inventory.application.services.return_history_service import (
    ReturnHistoryService,
)
from apps.inventory.models import ReturnNote, ReturnNoteLine, ReturnStatus, ReturnType
from apps.orders.models import Order, Product
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


def _seed():
    wh = Warehouse.objects.create(name="Kho A")
    p1 = Product.objects.create(sku="SKU1", name="Áo")
    p2 = Product.objects.create(sku="SKU2", name="Quần")
    order = Order.objects.create(order_code="DH001", shipping_code="VD001")
    posted = ReturnNote.objects.create(
        code="TH20260708-0001", warehouse=wh, source_order=order,
        shipping_code="VD001", return_type=ReturnType.DELIVERY_RETURNED,
        status=ReturnStatus.POSTED,
    )
    ReturnNoteLine.objects.create(return_note=posted, product=p1, quantity=4)
    ReturnNoteLine.objects.create(return_note=posted, product=p2, quantity=1)
    # Phiếu nháp KHÔNG được tính.
    draft = ReturnNote.objects.create(
        code="TH20260708-0002", warehouse=wh, status=ReturnStatus.DRAFT,
    )
    ReturnNoteLine.objects.create(return_note=draft, product=p1, quantity=9)
    return wh, order, posted, p1, p2


def test_list_page_only_posted_with_qty_total():
    wh, order, posted, p1, p2 = _seed()
    result = ReturnHistoryService.list_page(pagination={"limit": 50, "offset": 0})
    assert result["total"] == 1
    row = result["items"][0]
    assert row["return_id"] == posted.id
    assert row["code"] == "TH20260708-0001"
    assert row["source_order_code"] == "DH001"
    assert row["shipping_code"] == "VD001"
    assert row["warehouse_name"] == "Kho A"
    assert row["qty_total"] == 5
    assert row["return_type"] == ReturnType.DELIVERY_RETURNED
    assert row["status"] == ReturnStatus.POSTED


def test_list_page_product_filter_via_line():
    _seed()
    hit = ReturnHistoryService.list_page(
        pagination={"limit": 50, "offset": 0}, product="SKU2"
    )
    assert hit["total"] == 1
    miss = ReturnHistoryService.list_page(
        pagination={"limit": 50, "offset": 0}, product="SKU-NONE"
    )
    assert miss["total"] == 0


def test_lines_returns_products():
    wh, order, posted, p1, p2 = _seed()
    lines = ReturnHistoryService.lines(return_id=posted.id)
    by_sku = {l["sku"]: l for l in lines}
    assert by_sku["SKU1"]["quantity"] == 4
    assert by_sku["SKU2"]["quantity"] == 1
```

- [ ] **Step 2: Chạy test — kỳ vọng FAIL**

Run: backend test với `apps/inventory/tests/test_return_history.py`
Expected: FAIL (ModuleNotFoundError `return_history_service`).

- [ ] **Step 3: Cài đặt service**

Tạo `backend/apps/inventory/application/services/return_history_service.py`:

```python
from __future__ import annotations

from django.db.models import Exists, OuterRef, Q, Sum

from apps.inventory.models import (
    ReturnNote,
    ReturnNoteLine,
    ReturnStatus,
    ReturnType,
)

_RETURN_TYPE_LABELS = dict(ReturnType.choices)
_RETURN_STATUS_LABELS = dict(ReturnStatus.choices)


class ReturnHistoryService:
    @staticmethod
    def _base(*, date_from="", date_to="", warehouse_id=None, product="", order=""):
        qs = ReturnNote.objects.filter(status=ReturnStatus.POSTED)
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        order = (order or "").strip()
        if order:
            qs = qs.filter(
                Q(source_order__order_code__icontains=order)
                | Q(shipping_code__icontains=order)
            )
        product = (product or "").strip()
        if product:
            line_match = ReturnNoteLine.objects.filter(
                return_note_id=OuterRef("pk")
            ).filter(
                Q(product__sku__icontains=product)
                | Q(product__name__icontains=product)
            )
            qs = qs.annotate(_has_prod=Exists(line_match)).filter(_has_prod=True)
        return qs

    @staticmethod
    def list_page(*, pagination: dict, **filters) -> dict:
        qs = ReturnHistoryService._base(**filters).select_related(
            "warehouse", "source_order"
        )
        total = qs.count()
        offset = pagination["offset"]
        page = list(
            qs.order_by("-created_at", "-id")[offset : offset + pagination["limit"]]
        )
        # Tổng SL theo phiếu (batch, không N+1).
        note_ids = [n.id for n in page]
        qty_map = dict(
            ReturnNoteLine.objects.filter(return_note_id__in=note_ids)
            .values_list("return_note_id")
            .annotate(total=Sum("quantity"))
            .values_list("return_note_id", "total")
        )
        items = [
            {
                "return_id": n.id,
                "code": n.code,
                "source_order_id": n.source_order_id,
                "source_order_code": (
                    n.source_order.order_code if n.source_order_id else None
                ),
                "shipping_code": n.shipping_code or None,
                "warehouse_name": n.warehouse.name,
                "return_type": n.return_type,
                "return_type_label": _RETURN_TYPE_LABELS.get(n.return_type)
                or "Chưa phân loại",
                "status": n.status,
                "status_label": _RETURN_STATUS_LABELS.get(n.status, n.status),
                "qty_total": qty_map.get(n.id, 0),
                "created_at": n.created_at,
            }
            for n in page
        ]
        return {
            "items": items,
            "total": total,
            "limit": pagination["limit"],
            "offset": offset,
        }

    @staticmethod
    def lines(*, return_id: int) -> list[dict]:
        rows = (
            ReturnNoteLine.objects.filter(return_note_id=return_id)
            .select_related("product")
            .order_by("id")
        )
        return [
            {
                "product_id": r.product_id,
                "sku": r.product.sku,
                "name": r.product.name,
                "quantity": r.quantity,
                "return_type": r.return_type,
                "return_type_label": _RETURN_TYPE_LABELS.get(r.return_type) or "",
            }
            for r in rows
        ]
```

- [ ] **Step 4: Chạy test — kỳ vọng PASS**

Run: backend test với `apps/inventory/tests/test_return_history.py`
Expected: 3 passed.

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/application/services/return_history_service.py backend/apps/inventory/tests/test_return_history.py
git add backend/apps/inventory/application/services/return_history_service.py backend/apps/inventory/tests/test_return_history.py
git commit -m "feat: return history service (posted return notes)"
```

---

## Task 3: Excel export helpers

**Files:**
- Create: `backend/apps/inventory/infrastructure/flow_history_excel.py`
- Test: `backend/apps/inventory/tests/test_flow_history_export.py`

**Interfaces:**
- Consumes: item dicts từ `DispatchHistoryService.orders_page(...)["items"]` và `ReturnHistoryService.list_page(...)["items"]`.
- Produces:
  - `export_issue_orders(rows: list[dict]) -> HttpResponse` — xlsx, cột: Thời gian, Mã đơn, Mã vận đơn, Kho, Số SKU, SL xuất (net).
  - `export_return_notes(rows: list[dict]) -> HttpResponse` — xlsx, cột: Thời gian, Mã phiếu trả, Mã đơn nguồn, Mã vận đơn, Kho, Loại hoàn, Trạng thái, SL hoàn.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_flow_history_export.py`:

```python
from apps.inventory.infrastructure.flow_history_excel import (
    export_issue_orders,
    export_return_notes,
)

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_export_issue_orders_returns_xlsx():
    rows = [
        {
            "last_at": None, "order_code": "DH001", "shipping_code": "VD001",
            "warehouse_name": "Kho A", "sku_count": 2, "net": 6,
        }
    ]
    resp = export_issue_orders(rows)
    assert resp.status_code == 200
    assert resp["Content-Type"] == _XLSX
    assert len(resp.content) > 0


def test_export_return_notes_returns_xlsx():
    rows = [
        {
            "created_at": None, "code": "TH20260708-0001",
            "source_order_code": "DH001", "shipping_code": "VD001",
            "warehouse_name": "Kho A", "return_type_label": "Hàng hoàn",
            "status_label": "Đã ghi sổ", "qty_total": 5,
        }
    ]
    resp = export_return_notes(rows)
    assert resp.status_code == 200
    assert resp["Content-Type"] == _XLSX
    assert len(resp.content) > 0
```

- [ ] **Step 2: Chạy test — kỳ vọng FAIL**

Run: backend test với `apps/inventory/tests/test_flow_history_export.py`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Cài đặt exporter**

Tạo `backend/apps/inventory/infrastructure/flow_history_excel.py`:

```python
from __future__ import annotations

import io

import openpyxl
from django.http import HttpResponse
from django.utils.timezone import localtime

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _fmt_dt(value):
    if not value:
        return ""
    return localtime(value).strftime("%d/%m/%Y %H:%M")


def _workbook_response(title: str, headers: list[str], rows: list[list], filename: str):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    resp = HttpResponse(buffer.getvalue(), content_type=_XLSX)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def export_issue_orders(rows: list[dict]) -> HttpResponse:
    headers = ["Thời gian", "Mã đơn", "Mã vận đơn", "Kho", "Số SKU", "SL xuất"]
    data = [
        [
            _fmt_dt(r.get("last_at")),
            r.get("order_code") or "",
            r.get("shipping_code") or "",
            r.get("warehouse_name") or "",
            r.get("sku_count") or 0,
            r.get("net") or 0,
        ]
        for r in rows
    ]
    return _workbook_response("Xuat hang", headers, data, "lich-su-xuat.xlsx")


def export_return_notes(rows: list[dict]) -> HttpResponse:
    headers = [
        "Thời gian", "Mã phiếu trả", "Mã đơn nguồn", "Mã vận đơn",
        "Kho", "Loại hoàn", "Trạng thái", "SL hoàn",
    ]
    data = [
        [
            _fmt_dt(r.get("created_at")),
            r.get("code") or "",
            r.get("source_order_code") or "",
            r.get("shipping_code") or "",
            r.get("warehouse_name") or "",
            r.get("return_type_label") or "",
            r.get("status_label") or "",
            r.get("qty_total") or 0,
        ]
        for r in rows
    ]
    return _workbook_response("Nhap hoan", headers, data, "lich-su-nhap-hoan.xlsx")
```

- [ ] **Step 4: Chạy test — kỳ vọng PASS**

Run: backend test với `apps/inventory/tests/test_flow_history_export.py`
Expected: 2 passed.

- [ ] **Step 5: Ruff + commit**

```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/infrastructure/flow_history_excel.py backend/apps/inventory/tests/test_flow_history_export.py
git add backend/apps/inventory/infrastructure/flow_history_excel.py backend/apps/inventory/tests/test_flow_history_export.py
git commit -m "feat: excel exporters for inventory flow history"
```

---

## Task 4: Transport views + URL wiring

**Files:**
- Create: `backend/apps/inventory/transport/views/flow_history_views.py`
- Modify: `backend/apps/inventory/urls.py`
- Test: `backend/apps/inventory/tests/test_flow_history_views.py`

**Interfaces:**
- Consumes: `DispatchHistoryService.orders_page/order_lines`, `ReturnHistoryService.list_page/lines`, `export_issue_orders/export_return_notes`, `parse_required_pagination`, `require_section`.
- Produces 6 view fns: `issue_orders`, `issue_order_lines`, `issue_orders_export`, `return_history`, `return_history_lines`, `return_history_export`; đăng ký URL:
  - `dispatch-history/orders`, `dispatch-history/orders/lines`, `dispatch-history/export`
  - `return-history`, `return-history/lines`, `return-history/export`

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_flow_history_views.py`:

```python
import pytest
from rest_framework.test import APIClient

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import (
    MovementType, ReturnNote, ReturnNoteLine, ReturnStatus, ReturnType,
)
from apps.orders.models import Order, Product
from apps.users.models import User
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_admin():
    user = User.objects.create_user(username="admin", password="x", is_superuser=True)
    c = APIClient()
    c.force_authenticate(user)
    return c


def _seed():
    wh = Warehouse.objects.create(name="Kho A")
    p1 = Product.objects.create(sku="SKU1", name="Áo")
    order = Order.objects.create(order_code="DH001", shipping_code="VD001")
    StockCommandService.record_movement(
        warehouse_id=wh.id, product_id=p1.id, qty_delta=-5,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )
    rn = ReturnNote.objects.create(
        code="TH20260708-0001", warehouse=wh, source_order=order,
        return_type=ReturnType.DELIVERY_RETURNED, status=ReturnStatus.POSTED,
    )
    ReturnNoteLine.objects.create(return_note=rn, product=p1, quantity=2)
    return wh, order, p1, rn


def test_issue_orders_endpoint(client_admin):
    wh, order, p1, rn = _seed()
    r = client_admin.get("/api/inventory/dispatch-history/orders?limit=50&offset=0")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["order_code"] == "DH001"


def test_issue_order_lines_endpoint(client_admin):
    wh, order, p1, rn = _seed()
    r = client_admin.get(
        f"/api/inventory/dispatch-history/orders/lines?order_id={order.id}"
    )
    assert r.status_code == 200
    assert r.json()["items"][0]["sku"] == "SKU1"


def test_return_history_endpoint(client_admin):
    wh, order, p1, rn = _seed()
    r = client_admin.get("/api/inventory/return-history?limit=50&offset=0")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["code"] == "TH20260708-0001"


def test_return_history_lines_endpoint(client_admin):
    wh, order, p1, rn = _seed()
    r = client_admin.get(f"/api/inventory/return-history/lines?return_id={rn.id}")
    assert r.status_code == 200
    assert r.json()["items"][0]["quantity"] == 2


def test_issue_export_endpoint(client_admin):
    _seed()
    r = client_admin.get("/api/inventory/dispatch-history/export")
    assert r.status_code == 200
    assert "spreadsheetml" in r["Content-Type"]


def test_return_export_endpoint(client_admin):
    _seed()
    r = client_admin.get("/api/inventory/return-history/export")
    assert r.status_code == 200
    assert "spreadsheetml" in r["Content-Type"]
```

> Chú ý: kiểm tra chữ ký `User.objects.create_user` thực tế trong repo (`apps/users/models.py`) — nếu khác, chỉnh fixture cho khớp (một số dự án dùng email thay username). Nếu có helper tạo user/superuser trong `conftest.py`, ưu tiên dùng lại.

- [ ] **Step 2: Chạy test — kỳ vọng FAIL**

Run: backend test với `apps/inventory/tests/test_flow_history_views.py`
Expected: FAIL (404 — route chưa có).

- [ ] **Step 3: Cài đặt views**

Tạo `backend/apps/inventory/transport/views/flow_history_views.py`:

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

from apps.common.pagination import parse_required_pagination
from apps.inventory.application.services.dispatch_history_service import (
    DispatchHistoryService,
)
from apps.inventory.application.services.return_history_service import (
    ReturnHistoryService,
)
from apps.inventory.infrastructure.flow_history_excel import (
    export_issue_orders,
    export_return_notes,
)
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


def _common_filters(request):
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


_ISSUE_ORDER_ITEM = {
    "order_id": drf.IntegerField(),
    "order_code": drf.CharField(allow_null=True),
    "shipping_code": drf.CharField(allow_null=True),
    "warehouse_name": drf.CharField(allow_null=True),
    "sku_count": drf.IntegerField(),
    "dispatched": drf.IntegerField(),
    "reversed": drf.IntegerField(),
    "net": drf.IntegerField(),
    "last_at": drf.DateTimeField(allow_null=True),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "IssueOrdersHistory",
            fields={
                "items": inline_serializer(
                    "IssueOrdersHistoryItem", many=True, fields=_ISSUE_ORDER_ITEM
                ),
                "total": drf.IntegerField(),
                "limit": drf.IntegerField(),
                "offset": drf.IntegerField(),
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def issue_orders(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    pagination, page_error = parse_required_pagination(
        request, max_limit=200, default_limit=50
    )
    if page_error is not None:
        return page_error
    return Response(
        DispatchHistoryService.orders_page(pagination=pagination, **filters),
        status=status.HTTP_200_OK,
    )


_LINE_ITEM = {
    "product_id": drf.IntegerField(),
    "sku": drf.CharField(allow_null=True),
    "name": drf.CharField(allow_null=True),
    "dispatched": drf.IntegerField(),
    "reversed": drf.IntegerField(),
    "net": drf.IntegerField(),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "IssueOrderLines",
            fields={
                "items": inline_serializer(
                    "IssueOrderLineItem", many=True, fields=_LINE_ITEM
                )
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def issue_order_lines(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    order_id = _int_or_none(request.query_params.get("order_id"))
    if not order_id:
        return Response(
            {"detail": "order_id là bắt buộc."}, status=status.HTTP_400_BAD_REQUEST
        )
    filters.pop("order", None)
    return Response(
        {"items": DispatchHistoryService.order_lines(order_id=order_id, **filters)},
        status=status.HTTP_200_OK,
    )


@extend_schema(responses={(200, _XLSX_CT := "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): drf.CharField()})
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def issue_orders_export(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    # Xuất toàn bộ khớp bộ lọc (giới hạn an toàn 5000 đơn).
    data = DispatchHistoryService.orders_page(
        pagination={"limit": 5000, "offset": 0}, **filters
    )
    return export_issue_orders(data["items"])


_RETURN_ITEM = {
    "return_id": drf.IntegerField(),
    "code": drf.CharField(),
    "source_order_id": drf.IntegerField(allow_null=True),
    "source_order_code": drf.CharField(allow_null=True),
    "shipping_code": drf.CharField(allow_null=True),
    "warehouse_name": drf.CharField(allow_null=True),
    "return_type": drf.CharField(allow_blank=True),
    "return_type_label": drf.CharField(),
    "status": drf.CharField(),
    "status_label": drf.CharField(),
    "qty_total": drf.IntegerField(),
    "created_at": drf.DateTimeField(),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "ReturnHistory",
            fields={
                "items": inline_serializer(
                    "ReturnHistoryItem", many=True, fields=_RETURN_ITEM
                ),
                "total": drf.IntegerField(),
                "limit": drf.IntegerField(),
                "offset": drf.IntegerField(),
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def return_history(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    pagination, page_error = parse_required_pagination(
        request, max_limit=200, default_limit=50
    )
    if page_error is not None:
        return page_error
    return Response(
        ReturnHistoryService.list_page(pagination=pagination, **filters),
        status=status.HTTP_200_OK,
    )


_RETURN_LINE_ITEM = {
    "product_id": drf.IntegerField(),
    "sku": drf.CharField(allow_null=True),
    "name": drf.CharField(allow_null=True),
    "quantity": drf.IntegerField(),
    "return_type": drf.CharField(allow_blank=True),
    "return_type_label": drf.CharField(allow_blank=True),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "ReturnHistoryLines",
            fields={
                "items": inline_serializer(
                    "ReturnHistoryLineItem", many=True, fields=_RETURN_LINE_ITEM
                )
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def return_history_lines(request):
    return_id = _int_or_none(request.query_params.get("return_id"))
    if not return_id:
        return Response(
            {"detail": "return_id là bắt buộc."}, status=status.HTTP_400_BAD_REQUEST
        )
    return Response(
        {"items": ReturnHistoryService.lines(return_id=return_id)},
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def return_history_export(request):
    filters, error = _common_filters(request)
    if error is not None:
        return error
    data = ReturnHistoryService.list_page(
        pagination={"limit": 5000, "offset": 0}, **filters
    )
    return export_return_notes(data["items"])
```

> LƯU Ý cú pháp: dòng `@extend_schema(responses={(200, _XLSX_CT := ...): ...})` dùng walrus có thể khó đọc — thay bằng bỏ hẳn `@extend_schema` cho 2 view export (chúng trả file nhị phân, không cần schema JSON). Nếu bỏ, drf-spectacular vẫn sinh schema mặc định; chấp nhận được. Ưu tiên: **bỏ `@extend_schema` ở 2 view export** cho gọn.

- [ ] **Step 4: Wire URLs**

Sửa `backend/apps/inventory/urls.py` — thêm import + 6 path (đặt cạnh nhóm dispatch-history hiện có):

```python
from apps.inventory.transport.views.flow_history_views import (
    issue_orders,
    issue_order_lines,
    issue_orders_export,
    return_history,
    return_history_lines,
    return_history_export,
)
```

Thêm vào `urlpatterns` (TRƯỚC path `dispatch-history` trần để tránh nuốt route, vì Django match theo thứ tự — `dispatch-history/orders` phải đứng trước nếu route cũ còn; sau Task 7 xoá route cũ thì thứ tự không còn quan trọng):

```python
    path("dispatch-history/orders/lines", issue_order_lines, name="inventory-issue-order-lines"),
    path("dispatch-history/orders", issue_orders, name="inventory-issue-orders"),
    path("dispatch-history/export", issue_orders_export, name="inventory-issue-export"),
    path("return-history/lines", return_history_lines, name="inventory-return-history-lines"),
    path("return-history/export", return_history_export, name="inventory-return-history-export"),
    path("return-history", return_history, name="inventory-return-history"),
```

- [ ] **Step 5: Chạy test — kỳ vọng PASS**

Run: backend test với `apps/inventory/tests/test_flow_history_views.py`
Expected: 6 passed. (Nếu fixture user sai chữ ký → sửa theo `apps/users/models.py`.)

- [ ] **Step 6: Regen schema + ruff + commit**

```bash
# Trong backend/ (container hoặc host có deps):
python manage.py spectacular --file schema.yaml
uv run --no-project --with ruff ruff check backend/apps/inventory/transport/views/flow_history_views.py
git add backend/apps/inventory/transport/views/flow_history_views.py backend/apps/inventory/urls.py backend/apps/inventory/tests/test_flow_history_views.py backend/schema.yaml
git commit -m "feat: inventory flow history endpoints"
```

---

## Task 5: Frontend — typed API + query hooks

**Files:**
- Create: `frontend/src/lib/api/inventory-flow.ts`
- Create: `frontend/src/features/inventory/issue-history-queries.ts`
- Create: `frontend/src/features/inventory/return-history-queries.ts`
- Test: `frontend/src/features/inventory/__tests__/flow-history-queries.test.ts`

**Interfaces:**
- Consumes: `fetchJson` từ `@/services/http`; types từ `@/api/schema.d.ts` (`components["schemas"]["IssueOrdersHistory"]`, `IssueOrderLines`, `ReturnHistory`, `ReturnHistoryLines`).
- Produces:
  - `buildFlowParams(filters: FlowFilters): URLSearchParams`
  - `type FlowFilters = { dateFrom, dateTo, warehouseId: number|null, product, order }`
  - Hooks: `useIssueOrders(filters, page)`, `useIssueOrderLines(orderId, filters, enabled)`, `useReturnHistory(filters, page)`, `useReturnLines(returnId, enabled)`.
  - Export helpers: `downloadIssueExport(filters)`, `downloadReturnExport(filters)`.

- [ ] **Step 1: Chạy `gen:api` để có types**

Run (trong `frontend/`): `npm run gen:api`
Expected: `src/api/schema.d.ts` cập nhật, có `IssueOrdersHistory`, `ReturnHistory`, ... (Yêu cầu Task 4 đã commit `schema.yaml`.)

- [ ] **Step 2: Viết test thất bại**

Tạo `frontend/src/features/inventory/__tests__/flow-history-queries.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { buildFlowParams } from "../issue-history-queries";

describe("buildFlowParams", () => {
  it("chỉ set param có giá trị", () => {
    const p = buildFlowParams({
      dateFrom: "2026-07-01",
      dateTo: "2026-07-08",
      warehouseId: 3,
      product: " SKU1 ",
      order: "",
    });
    expect(p.get("date_from")).toBe("2026-07-01");
    expect(p.get("date_to")).toBe("2026-07-08");
    expect(p.get("warehouse_id")).toBe("3");
    expect(p.get("product")).toBe("SKU1");
    expect(p.has("order")).toBe(false);
  });

  it("bỏ qua warehouseId null", () => {
    const p = buildFlowParams({
      dateFrom: "",
      dateTo: "",
      warehouseId: null,
      product: "",
      order: "VD1",
    });
    expect(p.has("warehouse_id")).toBe(false);
    expect(p.get("order")).toBe("VD1");
  });
});
```

- [ ] **Step 3: Chạy test — kỳ vọng FAIL**

Run: `npm run test:ci -- flow-history-queries`
Expected: FAIL (module `issue-history-queries` chưa có).

- [ ] **Step 4: Tạo typed API module**

Tạo `frontend/src/lib/api/inventory-flow.ts`:

```ts
import type { components } from "@/api/schema.d.ts";

export type IssueOrdersHistory = components["schemas"]["IssueOrdersHistory"];
export type IssueOrdersHistoryItem = components["schemas"]["IssueOrdersHistoryItem"];
export type IssueOrderLines = components["schemas"]["IssueOrderLines"];
export type IssueOrderLineItem = components["schemas"]["IssueOrderLineItem"];
export type ReturnHistory = components["schemas"]["ReturnHistory"];
export type ReturnHistoryItem = components["schemas"]["ReturnHistoryItem"];
export type ReturnHistoryLines = components["schemas"]["ReturnHistoryLines"];
export type ReturnHistoryLineItem = components["schemas"]["ReturnHistoryLineItem"];
```

- [ ] **Step 5: Tạo issue-history-queries.ts**

Tạo `frontend/src/features/inventory/issue-history-queries.ts`:

```ts
import { useQuery } from "@tanstack/react-query";

import { fetchJson, getValidAccessToken } from "@/services/http";
import type {
  IssueOrderLines,
  IssueOrdersHistory,
} from "@/lib/api/inventory-flow";

export type FlowFilters = {
  dateFrom: string;
  dateTo: string;
  warehouseId: number | null;
  product: string;
  order: string;
};

export function buildFlowParams(filters: FlowFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (filters.dateFrom) p.set("date_from", filters.dateFrom);
  if (filters.dateTo) p.set("date_to", filters.dateTo);
  if (filters.warehouseId) p.set("warehouse_id", String(filters.warehouseId));
  if (filters.product.trim()) p.set("product", filters.product.trim());
  if (filters.order.trim()) p.set("order", filters.order.trim());
  return p;
}

export function useIssueOrders(
  filters: FlowFilters,
  page: { limit: number; offset: number },
) {
  const params = buildFlowParams(filters);
  params.set("limit", String(page.limit));
  params.set("offset", String(page.offset));
  return useQuery({
    queryKey: ["inventory", "issue-orders", params.toString()],
    queryFn: () =>
      fetchJson(
        `/api/inventory/dispatch-history/orders?${params.toString()}`,
      ) as Promise<IssueOrdersHistory>,
    staleTime: 10_000,
  });
}

export function useIssueOrderLines(
  orderId: number | null,
  filters: FlowFilters,
  enabled: boolean,
) {
  const params = buildFlowParams(filters);
  if (orderId) params.set("order_id", String(orderId));
  return useQuery({
    queryKey: ["inventory", "issue-order-lines", params.toString()],
    queryFn: () =>
      fetchJson(
        `/api/inventory/dispatch-history/orders/lines?${params.toString()}`,
      ) as Promise<IssueOrderLines>,
    staleTime: 10_000,
    enabled,
  });
}

async function downloadXlsx(url: string, filename: string): Promise<void> {
  let token: string | null = null;
  try {
    token = await getValidAccessToken();
  } catch {
    token = null;
  }
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  // eslint-disable-next-line no-restricted-globals -- blob download (xlsx); fetchJson only handles JSON
  const res = await fetch(url, { method: "GET", credentials: "include", headers });
  if (!res.ok) throw new Error("Không thể xuất file.");
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

export function downloadIssueExport(filters: FlowFilters): Promise<void> {
  const params = buildFlowParams(filters);
  return downloadXlsx(
    `/api/inventory/dispatch-history/export?${params.toString()}`,
    "lich-su-xuat.xlsx",
  );
}
```

- [ ] **Step 6: Tạo return-history-queries.ts**

Tạo `frontend/src/features/inventory/return-history-queries.ts`:

```ts
import { useQuery } from "@tanstack/react-query";

import { fetchJson } from "@/services/http";
import type {
  ReturnHistory,
  ReturnHistoryLines,
} from "@/lib/api/inventory-flow";

import { buildFlowParams, downloadXlsxExport, type FlowFilters } from "./issue-history-queries";

export function useReturnHistory(
  filters: FlowFilters,
  page: { limit: number; offset: number },
) {
  const params = buildFlowParams(filters);
  params.set("limit", String(page.limit));
  params.set("offset", String(page.offset));
  return useQuery({
    queryKey: ["inventory", "return-history", params.toString()],
    queryFn: () =>
      fetchJson(
        `/api/inventory/return-history?${params.toString()}`,
      ) as Promise<ReturnHistory>,
    staleTime: 10_000,
  });
}

export function useReturnLines(returnId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ["inventory", "return-lines", returnId],
    queryFn: () =>
      fetchJson(
        `/api/inventory/return-history/lines?return_id=${returnId}`,
      ) as Promise<ReturnHistoryLines>,
    staleTime: 10_000,
    enabled,
  });
}

export function downloadReturnExport(filters: FlowFilters): Promise<void> {
  const params = buildFlowParams(filters);
  return downloadXlsxExport(
    `/api/inventory/return-history/export?${params.toString()}`,
    "lich-su-nhap-hoan.xlsx",
  );
}
```

> Để tránh trùng lặp `downloadXlsx`, export nó từ `issue-history-queries.ts` với tên `downloadXlsxExport`. Sửa `issue-history-queries.ts`: đổi `async function downloadXlsx` → `export async function downloadXlsxExport`, và `downloadIssueExport` gọi `downloadXlsxExport(...)`. Cập nhật lại Step 5 tương ứng khi implement.

- [ ] **Step 7: Chạy test + typecheck — kỳ vọng PASS**

Run: `npm run test:ci -- flow-history-queries` → PASS.
Run: `npm run typecheck` → 0 lỗi.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/schema.d.ts frontend/src/lib/api/inventory-flow.ts frontend/src/features/inventory/issue-history-queries.ts frontend/src/features/inventory/return-history-queries.ts frontend/src/features/inventory/__tests__/flow-history-queries.test.ts
git commit -m "feat: frontend query hooks for inventory flow history"
```

---

## Task 6: Frontend — rework trang thành "Lịch sử nhập xuất"

**Files:**
- Modify: `frontend/src/features/inventory/pages/DispatchHistoryPage.tsx`
- Modify: `frontend/src/i18n/messages/topnav.ts`

**Interfaces:**
- Consumes: `Tabs, TabItem` từ `@/components/ui/tabs`; hooks Task 5; `DateFilter, getDateRange`; `WarehouseListResponse`.

- [ ] **Step 1: Đổi nhãn menu**

Sửa `frontend/src/i18n/messages/topnav.ts` dòng `"topnav.tabs.dispatchHistory": "Lịch sử xuất kho",` → `"Lịch sử nhập xuất",`.

- [ ] **Step 2: Viết lại trang**

Thay toàn bộ `frontend/src/features/inventory/pages/DispatchHistoryPage.tsx`:

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
import { Tabs, type TabItem } from "@/components/ui/tabs";
import { DateFilter, getDateRange, type DateFilterValue } from "@/components/ui/date-filter";
import { fetchJson } from "@/services/http";
import type { WarehouseListResponse } from "@/lib/api/warehouses";

import {
  useIssueOrders,
  useIssueOrderLines,
  downloadIssueExport,
  type FlowFilters,
} from "../issue-history-queries";
import { useReturnHistory, useReturnLines, downloadReturnExport } from "../return-history-queries";

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
  const [tab, setTab] = useState("issue");

  const filters: FlowFilters = useMemo(
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

  const tabs: TabItem[] = [
    { key: "issue", label: "Xuất hàng ra", content: <IssueTab filters={filters} /> },
    { key: "return", label: "Nhập hàng hoàn về", content: <ReturnTab filters={filters} /> },
  ];

  return (
    <div className="space-y-4 p-3 sm:p-4">
      <PageHeader title="Lịch sử nhập xuất" />

      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
        <DateFilter value={date} onChange={setDate} inline />
        <Select value={warehouse} onValueChange={setWarehouse}>
          <SelectTrigger className="w-full sm:w-48">
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
          className="w-full sm:w-48"
          placeholder="Tìm sản phẩm (SKU/tên)"
          value={product}
          onChange={(e) => setProduct(e.target.value)}
        />
        <Input
          className="w-full sm:w-48"
          placeholder="Tìm mã đơn / mã vận đơn"
          value={order}
          onChange={(e) => setOrder(e.target.value)}
        />
      </div>

      <Tabs tabs={tabs} active={tab} onChange={setTab} />
    </div>
  );
}

function ExportButton({ onClick }: { onClick: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      className="rounded border border-border px-3 py-1.5 text-sm hover:bg-muted/50 disabled:opacity-50"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await Promise.resolve(onClick());
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? "Đang xuất…" : "Xuất Excel"}
    </button>
  );
}

function IssueTab({ filters }: { filters: FlowFilters }) {
  const [offset, setOffset] = useState(0);
  const [openOrder, setOpenOrder] = useState<number | null>(null);
  const list = useIssueOrders(filters, { limit: PAGE_SIZE, offset });
  const lines = useIssueOrderLines(openOrder, filters, openOrder != null);
  const data = list.data;

  return (
    <div className="space-y-3 pt-3">
      <div className="flex justify-end">
        <ExportButton onClick={() => downloadIssueExport(filters)} />
      </div>
      <div className="rounded-lg border border-border overflow-x-auto">
        <table data-slot="table" className="w-full text-sm">
          <thead className="text-left text-muted-foreground">
            <tr>
              <th className="whitespace-nowrap px-3 py-2">Thời gian</th>
              <th className="whitespace-nowrap px-3 py-2">Mã đơn</th>
              <th className="whitespace-nowrap px-3 py-2">Mã vận đơn</th>
              <th className="whitespace-nowrap px-3 py-2">Kho</th>
              <th className="px-3 py-2 text-right">Số SKU</th>
              <th className="px-3 py-2 text-right">SL xuất</th>
            </tr>
          </thead>
          <tbody>
            {(data?.items.length ?? 0) === 0 && (
              <tr>
                <td className="px-3 py-3 text-muted-foreground" colSpan={6}>
                  {list.isLoading ? "Đang tải…" : "Không có dữ liệu."}
                </td>
              </tr>
            )}
            {data?.items.map((r) => (
              <>
                <tr
                  key={r.order_id}
                  className="cursor-pointer border-t border-border hover:bg-muted/50"
                  onClick={() =>
                    setOpenOrder((cur) => (cur === r.order_id ? null : r.order_id))
                  }
                >
                  <td className="whitespace-nowrap px-3 py-2">
                    {r.last_at ? new Date(r.last_at).toLocaleString("vi-VN") : "—"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 font-medium">
                    {r.order_code ?? "—"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">{r.shipping_code ?? "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2">{r.warehouse_name ?? "—"}</td>
                  <td className="px-3 py-2 text-right">{r.sku_count}</td>
                  <td className="px-3 py-2 text-right">{r.net}</td>
                </tr>
                {openOrder === r.order_id && (
                  <tr key={`${r.order_id}-lines`} className="bg-muted/30">
                    <td colSpan={6} className="px-3 py-2">
                      {lines.isLoading ? (
                        <span className="text-muted-foreground">Đang tải chi tiết…</span>
                      ) : (
                        <table className="w-full text-xs">
                          <thead className="text-left text-muted-foreground">
                            <tr>
                              <th className="px-2 py-1">SKU</th>
                              <th className="px-2 py-1">Tên</th>
                              <th className="px-2 py-1 text-right">SL xuất</th>
                            </tr>
                          </thead>
                          <tbody>
                            {lines.data?.items.map((l) => (
                              <tr key={l.product_id} className="border-t border-border/60">
                                <td className="px-2 py-1 font-medium">{l.sku}</td>
                                <td className="px-2 py-1">{l.name}</td>
                                <td className="px-2 py-1 text-right">{l.net}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
        {data && data.total > PAGE_SIZE && (
          <Pager offset={offset} total={data.total} onChange={setOffset} />
        )}
      </div>
    </div>
  );
}

function ReturnTab({ filters }: { filters: FlowFilters }) {
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState<number | null>(null);
  const list = useReturnHistory(filters, { limit: PAGE_SIZE, offset });
  const lines = useReturnLines(openId, openId != null);
  const data = list.data;

  return (
    <div className="space-y-3 pt-3">
      <div className="flex justify-end">
        <ExportButton onClick={() => downloadReturnExport(filters)} />
      </div>
      <div className="rounded-lg border border-border overflow-x-auto">
        <table data-slot="table" className="w-full text-sm">
          <thead className="text-left text-muted-foreground">
            <tr>
              <th className="whitespace-nowrap px-3 py-2">Thời gian</th>
              <th className="whitespace-nowrap px-3 py-2">Mã phiếu trả</th>
              <th className="whitespace-nowrap px-3 py-2">Mã đơn nguồn</th>
              <th className="whitespace-nowrap px-3 py-2">Mã vận đơn</th>
              <th className="whitespace-nowrap px-3 py-2">Kho</th>
              <th className="whitespace-nowrap px-3 py-2">Loại hoàn</th>
              <th className="px-3 py-2 text-right">SL hoàn</th>
            </tr>
          </thead>
          <tbody>
            {(data?.items.length ?? 0) === 0 && (
              <tr>
                <td className="px-3 py-3 text-muted-foreground" colSpan={7}>
                  {list.isLoading ? "Đang tải…" : "Không có dữ liệu."}
                </td>
              </tr>
            )}
            {data?.items.map((r) => (
              <>
                <tr
                  key={r.return_id}
                  className="cursor-pointer border-t border-border hover:bg-muted/50"
                  onClick={() =>
                    setOpenId((cur) => (cur === r.return_id ? null : r.return_id))
                  }
                >
                  <td className="whitespace-nowrap px-3 py-2">
                    {new Date(r.created_at).toLocaleString("vi-VN")}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 font-medium">{r.code}</td>
                  <td className="whitespace-nowrap px-3 py-2">
                    {r.source_order_code ?? "—"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">{r.shipping_code ?? "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2">{r.warehouse_name ?? "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2">{r.return_type_label}</td>
                  <td className="px-3 py-2 text-right">{r.qty_total}</td>
                </tr>
                {openId === r.return_id && (
                  <tr key={`${r.return_id}-lines`} className="bg-muted/30">
                    <td colSpan={7} className="px-3 py-2">
                      {lines.isLoading ? (
                        <span className="text-muted-foreground">Đang tải chi tiết…</span>
                      ) : (
                        <table className="w-full text-xs">
                          <thead className="text-left text-muted-foreground">
                            <tr>
                              <th className="px-2 py-1">SKU</th>
                              <th className="px-2 py-1">Tên</th>
                              <th className="px-2 py-1 text-right">SL</th>
                            </tr>
                          </thead>
                          <tbody>
                            {lines.data?.items.map((l) => (
                              <tr key={l.product_id} className="border-t border-border/60">
                                <td className="px-2 py-1 font-medium">{l.sku}</td>
                                <td className="px-2 py-1">{l.name}</td>
                                <td className="px-2 py-1 text-right">{l.quantity}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
        {data && data.total > PAGE_SIZE && (
          <Pager offset={offset} total={data.total} onChange={setOffset} />
        )}
      </div>
    </div>
  );
}

function Pager({
  offset,
  total,
  onChange,
}: {
  offset: number;
  total: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center justify-between border-t border-border px-3 py-2 text-sm">
      <span className="text-muted-foreground">
        {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} / {total}
      </span>
      <div className="flex gap-2">
        <button
          className="rounded border border-border px-2 py-1 disabled:opacity-50"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}
        >
          Trước
        </button>
        <button
          className="rounded border border-border px-2 py-1 disabled:opacity-50"
          disabled={offset + PAGE_SIZE >= total}
          onClick={() => onChange(offset + PAGE_SIZE)}
        >
          Sau
        </button>
      </div>
    </div>
  );
}
```

> LƯU Ý JSX: React yêu cầu key trên phần tử trả về trong `.map`. Vì mỗi item render 2 `<tr>` (dòng + panel chi tiết), bọc bằng `<Fragment key={...}>` thay vì `<>` không key. Import `Fragment` từ `react` và đổi `<>...</>` thành `<Fragment key={r.order_id}>...</Fragment>` (bỏ key trên `<tr>` bên trong). Sửa cả 2 tab khi implement.

- [ ] **Step 3: Reset phân trang khi đổi bộ lọc**

Trong mỗi tab, khi `filters` đổi thì offset nên về 0 và đóng drill-down. Thêm vào `IssueTab`/`ReturnTab` một `useEffect` reset, hoặc key lại component theo filters ở parent:

Ở parent, đổi render tab content thành có `key` theo filters để remount (đơn giản, đúng React):

```tsx
{ key: "issue", label: "Xuất hàng ra",
  content: <IssueTab key={JSON.stringify(filters)} filters={filters} /> },
{ key: "return", label: "Nhập hàng hoàn về",
  content: <ReturnTab key={JSON.stringify(filters)} filters={filters} /> },
```

- [ ] **Step 4: Typecheck + lint + build**

Run (trong `frontend/`):
```bash
npm run typecheck
npm run lint:strict
npm run build
```
Expected: 0 lỗi. (Nếu `lint:strict` báo `no-restricted-globals` cho `fetch` → đã có eslint-disable ở helper; nếu báo chỗ khác, thêm disable đúng dòng.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/pages/DispatchHistoryPage.tsx frontend/src/i18n/messages/topnav.ts
git commit -m "feat: rework page into inventory flow history with tabs"
```

---

## Task 7: Dọn dẹp endpoint & code cũ

**Files:**
- Modify: `backend/apps/inventory/urls.py` (xoá 2 route cũ)
- Delete: `backend/apps/inventory/transport/views/dispatch_history_views.py`
- Delete: `backend/apps/inventory/tests/test_dispatch_history_summary.py`, `test_dispatch_history_detail.py`
- Modify: `backend/apps/inventory/application/services/dispatch_history_service.py` (xoá `summary()`, `detail_queryset()` không còn dùng)
- Delete: `frontend/src/features/inventory/dispatch-history-queries.ts`, `frontend/src/features/inventory/__tests__/dispatch-history-queries.test.ts`
- Modify: `backend/schema.yaml` (regen)

- [ ] **Step 1: Xác nhận không còn tham chiếu**

Run:
```bash
grep -rn "dispatch_history_summary\|dispatch_history_detail\|DispatchHistoryService.summary\|detail_queryset\|dispatch-history/summary\|dispatch-history-queries\|useDispatchSummary\|useDispatchDetail" backend/apps frontend/src
```
Expected: chỉ còn match trong các file sắp xoá. Nếu còn nơi khác dùng → dừng, xử lý trước.

- [ ] **Step 2: Xoá code cũ**

- Xoá file `backend/apps/inventory/transport/views/dispatch_history_views.py`.
- Trong `backend/apps/inventory/urls.py`: xoá import `dispatch_history_detail, dispatch_history_summary` và 2 path `dispatch-history/summary`, `dispatch-history` trần.
- Trong `dispatch_history_service.py`: xoá method `summary()` và `detail_queryset()` (giữ `base_queryset`, `orders_page`, `order_lines`). Bỏ import không dùng (`StockQueryService` không ở service này; kiểm tra `Max` vẫn dùng).
- Xoá `test_dispatch_history_summary.py`, `test_dispatch_history_detail.py`.
- Xoá `frontend/src/features/inventory/dispatch-history-queries.ts` + test của nó.

- [ ] **Step 3: Regen schema + chạy toàn bộ test inventory**

```bash
# backend/
python manage.py spectacular --file schema.yaml
```
Run backend test với `<paths>` = `apps/inventory/tests/`
Expected: pass (trừ baseline pre-existing `stock_negative_alert`, `dispatch_stock_cycle` cancel-release — xem memory known-failing-baseline; KHÔNG phải regression của task này).

Run (frontend/): `npm run gen:api && npm run test:ci && npm run typecheck && npm run build`
Expected: schema.d.ts không đổi thêm (đã khớp), test/build pass.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove legacy dispatch-history product view"
```

---

## Task 8: Verify end-to-end + hoàn tất

- [ ] **Step 1:** Chạy full test inventory backend + FE (như Task 7 Step 3), xác nhận xanh (trừ baseline đã biết).
- [ ] **Step 2:** Chạy app (skill `run` hoặc dev server), mở `/inventory/dispatch-history`:
  - Tiêu đề + menu hiển thị "Lịch sử nhập xuất".
  - Tab "Xuất hàng ra": có đơn, bấm mở chi tiết SKU, phân trang, nút Xuất Excel tải file.
  - Tab "Nhập hàng hoàn về": chỉ phiếu posted, drill-down lines, Xuất Excel tải file.
  - Đổi bộ lọc ngày/kho/SP/đơn → danh sách cập nhật, phân trang reset.
- [ ] **Step 3:** `git pull --rebase origin master`, resolve conflict nếu có, chạy lại test.
- [ ] **Step 4:** Push nhánh `feat/inventory-flow-history`, tạo MR vào master, gửi link MR.

---

## Self-Review (đã thực hiện khi viết plan)

- **Spec coverage:** 2 danh sách (Task 1/2/4/6) · export Excel (Task 3/4/6) · lọc theo ngày (common_filters mọi endpoint) · nhóm theo đơn (orders_page/return list) · posted-only (ReturnHistoryService) · RBAC stock (mọi view) · đổi tên (Task 6) · dọn code cũ (Task 7). Đủ.
- **Type consistency:** tên field khớp giữa service → view inline_serializer → schema → FE types (`order_id/order_code/shipping_code/warehouse_name/sku_count/dispatched/reversed/net/last_at`; return: `return_id/code/source_order_code/qty_total/...`). Hook names khớp giữa Task 5 và Task 6.
- **Lưu ý khi thực thi:** (1) `<>` → `Fragment key` trong `.map` (Step lưu ý Task 6). (2) `downloadXlsx` chia sẻ qua `downloadXlsxExport` export từ issue-history-queries. (3) Fixture user Task 4 cần khớp `apps/users/models.py`. (4) Bỏ `@extend_schema` ở 2 view export.
