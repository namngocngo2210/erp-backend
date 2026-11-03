# Scan Shipment → Dispatch / Return — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backend APIs so warehouse staff can scan a shipment waybill (`shipping_code`), map it to an order, and either mark it dispatched (xuất giao, auto-deducts stock) or create a return note (nhập hoàn, adds stock), without double-counting.

**Architecture:** Reuse existing battle-tested flows. Dispatch reuses `OrderService.update_order_inline` with `delivery_business_status_code="dispatched"` (already triggers idempotent `issue_for_order`). Return reuses `ReturnNoteService.create_return` + `post_return`. Remove the `RETURNED` auto-release so the physical return scan is the single source of truth for restock. Add one read-only lookup endpoint plus two batch endpoints.

**Tech Stack:** Django 6 + DRF, PostgreSQL, pytest (run via ephemeral docker — see Global Constraints).

## Global Constraints

- Python 3.12+, Django 6, clean architecture (business logic in `application/services`, thin views in `transport/views`).
- Commit messages in English, short, **no** `Co-Authored-By` line.
- Work on branch `feat/scan-shipment-stock-document` (already checked out).
- Spec: `docs/erp-inventory-scan-shipment.md`. Decisions Q1–Q7 there are authoritative.
- Run backend tests via ephemeral container (the running `desi-web` is the prod image, lacks tests dir):
  ```bash
  docker run --rm --user "$(id -u):$(id -g)" --network desi-order_default \
    --env-file .env -e POSTGRES_HOST=postgres -e REDIS_HOST=redis \
    -e POSTGRES_DB=desi_scantest -e DJANGO_SETTINGS_MODULE=config.settings \
    -v "$PWD/backend:/app" -w /app desi-order-web:latest \
    python -m pytest <paths> -q
  ```
  (`POSTGRES_DB=desi_scantest` sidesteps a locked leftover `test_desi`.)
- Eligible order statuses (warnings only, never block): dispatch expects `AWAITING_SHIPMENT(11)`, `IN_DELIVERY(2)`; return expects `IN_RETURN(4)`, `RETURNED(5)`, `AWAITING_RETURN_CONFIRMATION(13)`.

---

## File Structure

- `backend/apps/inventory/subscribers.py` — modify: drop `RETURNED(5)` from `_RELEASE_STATUSES`.
- `backend/apps/inventory/tests/test_release_on_status_change.py` — modify: RETURNED no longer releases.
- `backend/apps/orders/tests/test_dispatch_stock.py` — modify: `test_returned_releases_via_event` → no longer releases.
- `backend/apps/orders/transport/views/shipping_lookup_view.py` — create: `lookup_by_shipping_code` GET view.
- `backend/apps/orders/transport/views/dispatch_batch_view.py` — create: `dispatch_batch` POST view.
- `backend/apps/orders/application/services/order_service_commands.py` — modify: add `dispatch_orders_batch` static method.
- `backend/apps/orders/services.py` — modify: expose `dispatch_orders_batch` on `OrderService` facade (match existing facade pattern).
- `backend/apps/orders/urls.py` — modify: register the two new routes.
- `backend/apps/inventory/application/services/return_note_service.py` — modify: add `create_returns_from_orders` static method.
- `backend/apps/inventory/transport/views/return_views.py` — modify: add `create_returns_batch_view`.
- `backend/apps/inventory/urls.py` — modify: register `returns/batch` route.
- `backend/apps/orders/tests/test_scan_dispatch_batch.py` — create.
- `backend/apps/orders/tests/test_shipping_lookup.py` — create.
- `backend/apps/inventory/tests/test_return_batch.py` — create.

---

### Task 1: Drop RETURNED from auto-release

**Files:**
- Modify: `backend/apps/inventory/subscribers.py:14-15`
- Modify: `backend/apps/inventory/tests/test_release_on_status_change.py:41-56`
- Modify: `backend/apps/orders/tests/test_dispatch_stock.py:219-255`

**Interfaces:**
- Produces: `_RELEASE_STATUSES = frozenset({6})` — only CANCELLED triggers `release_for_order`.

- [ ] **Step 1: Update the two existing tests to the new behavior**

In `test_release_on_status_change.py`, replace `test_release_on_returned_status` (lines 41-56) with:

```python
@pytest.mark.django_db
def test_no_release_on_returned_status(warehouse, product, seeded_stock):
    # RETURNED no longer auto-releases: physical ReturnNote is the source of truth.
    StockSalesService.issue_for_order(
        order_id=999, warehouse_id=warehouse.id,
        lines=[SaleLine(product_id=product.id, quantity=3)], user_id=None,
    )
    item = StockItem.objects.get(warehouse=warehouse, product=product, state=StockState.SELLABLE)
    before = item.quantity
    event_bus.publish(OrderStatusChanged(order_id=999, old_status=2, new_status=5))
    item.refresh_from_db()
    assert item.quantity == before  # unchanged
    assert not StockMovement.objects.filter(
        reference_type="order", reference_id=999, movement_type=MovementType.SALE_REVERSAL
    ).exists()
```

In `test_dispatch_stock.py`, replace `test_returned_releases_via_event` (lines 222-255) with:

```python
    def test_returned_no_longer_releases_via_event(self):
        """RETURNED must NOT auto-release stock (physical ReturnNote handles restock)."""
        _seed_stock(self.warehouse, self.product, 10)
        order = self._make_order_with_warehouse("w4")

        OrderService.update_order_inline(
            order,
            {"crm": {"delivery_business_status_code": "dispatched"}},
            self.actor,
        )
        item = StockItem.objects.get(warehouse=self.warehouse, product=self.product)
        self.assertEqual(item.quantity, 8, "Should be 8 after dispatched")

        with self.captureOnCommitCallbacks(execute=True):
            OrderService.update_order_inline(
                order,
                {"status": OrderStatus.RETURNED},
                self.actor,
            )

        item.refresh_from_db()
        self.assertEqual(item.quantity, 8, "RETURNED must NOT restore stock anymore")
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=order.id,
                movement_type=MovementType.SALE_REVERSAL,
            ).exists(),
            "No SALE_REVERSAL should be created on RETURNED",
        )
```

- [ ] **Step 2: Run the tests to verify they FAIL (current code still releases on RETURNED)**

Run (see Global Constraints for the full docker wrapper):
`python -m pytest apps/inventory/tests/test_release_on_status_change.py::test_no_release_on_returned_status apps/orders/tests/test_dispatch_stock.py::DispatchStockTests::test_returned_no_longer_releases_via_event -q`
Expected: FAIL (stock gets restored / SALE_REVERSAL exists).

- [ ] **Step 3: Change the subscriber**

In `backend/apps/inventory/subscribers.py` replace lines 14-15:

```python
# OrderStatus cần hoàn kho TỰ ĐỘNG: chỉ CANCELLED=6.
# RETURNED=5 KHÔNG còn auto-release — hàng hoàn nhập lại qua ReturnNote (quét vật lý)
# để tránh cộng tồn 2 lần. Xem docs/erp-inventory-scan-shipment.md §3.4.
_RELEASE_STATUSES = frozenset({6})
```

- [ ] **Step 4: Run the full affected test files to verify PASS**

Run: `python -m pytest apps/inventory/tests/test_release_on_status_change.py apps/orders/tests/test_dispatch_stock.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/subscribers.py backend/apps/inventory/tests/test_release_on_status_change.py backend/apps/orders/tests/test_dispatch_stock.py
git commit -m "feat(inventory): stop auto-releasing stock on RETURNED (physical return note is source of truth)"
```

---

### Task 2: Lookup order by shipping code

**Files:**
- Create: `backend/apps/orders/transport/views/shipping_lookup_view.py`
- Modify: `backend/apps/orders/urls.py`
- Test: `backend/apps/orders/tests/test_shipping_lookup.py`

**Interfaces:**
- Produces: `GET /api/orders/lookup-by-shipping-code?code=<str>`. 200 single order `{order_id, order_code, shipping_code, status, status_label, customer_name, customer_phone, fulfillment_warehouse_id, items:[{product_id, sku, name, quantity}]}`; 200 `{multiple:true, orders:[...]}` if >1; 404 `{detail:"not_found"}`; 400 if `code` blank.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/orders/tests/test_shipping_lookup.py`:

```python
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderItem, OrderStatus, Product
from apps.users.test_helpers import make_user_with_permissions
from apps.warehouses.models import Warehouse


class ShippingLookupTests(TestCase):
    def setUp(self):
        self.user = make_user_with_permissions("wh-staff", ["shipments:view"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.channel = Channel.objects.create(code="lp-lk", name="Lookup Channel")
        self.wh = Warehouse.objects.create(code="WH-LK", name="Kho Lookup")
        self.product = Product.objects.create(name="SP LK", sku="SKU-LK-1")

    def _order(self, code, ship, status=OrderStatus.AWAITING_SHIPMENT):
        customer = Customer.objects.create(
            full_name="Khach LK", phone_raw="0911000001",
            phone_normalized="0911000001", code=f"CUST-LK-{code}",
        )
        o = Order.objects.create(
            channel=self.channel, customer=customer, name="Khach LK",
            phone="0911000001", order_code=code, shipping_code=ship, status=status,
        )
        OrderItem.objects.create(
            order=o, product=self.product, product_name=self.product.name,
            sku=self.product.sku, quantity=2, unit_price=1000, line_total=2000,
        )
        return o

    def test_lookup_found_returns_order_and_items(self):
        self._order("DH1", "VD12345")
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=VD12345")
        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert body["shipping_code"] == "VD12345"
        assert body["order_code"] == "DH1"
        assert body["items"][0]["sku"] == "SKU-LK-1"
        assert body["items"][0]["quantity"] == 2

    def test_lookup_not_found_404(self):
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=NOPE")
        assert resp.status_code == 404

    def test_lookup_blank_code_400(self):
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=")
        assert resp.status_code == 400

    def test_lookup_multiple_returns_list(self):
        self._order("DH2", "DUP1")
        self._order("DH3", "DUP1")
        resp = self.client.get("/api/orders/lookup-by-shipping-code?code=DUP1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["multiple"] is True
        assert len(body["orders"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/orders/tests/test_shipping_lookup.py -q`
Expected: FAIL (404 for the route — view not registered).

- [ ] **Step 3: Create the view**

Create `backend/apps/orders/transport/views/shipping_lookup_view.py`:

```python
from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.models import Order, OrderStatus
from apps.users.permissions import require_section


def _serialize(o: Order) -> dict:
    return {
        "order_id": o.id,
        "order_code": o.order_code,
        "shipping_code": o.shipping_code,
        "status": int(o.status),
        "status_label": OrderStatus(o.status).label,
        "customer_name": o.name,
        "customer_phone": o.phone,
        "fulfillment_warehouse_id": o.fulfillment_warehouse_id,
        "items": [
            {
                "product_id": it.product_id,
                "sku": it.sku,
                "name": it.product_name,
                "quantity": it.quantity,
            }
            for it in o.items.all()
        ],
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("shipments")])
def lookup_by_shipping_code(request):
    code = (request.query_params.get("code") or "").strip()
    if not code:
        return Response({"detail": "code is required."}, status=status.HTTP_400_BAD_REQUEST)
    orders = list(
        Order.objects.filter(shipping_code=code, deleted_at__isnull=True)
        .prefetch_related("items")[:20]
    )
    if not orders:
        return Response({"detail": "not_found"}, status=status.HTTP_404_NOT_FOUND)
    if len(orders) > 1:
        return Response({"multiple": True, "orders": [_serialize(o) for o in orders]})
    return Response(_serialize(orders[0]))
```

- [ ] **Step 4: Register the route**

In `backend/apps/orders/urls.py`, import and add the path BEFORE the `path("<int:order_id>", ...)` catch-all (so `lookup-by-shipping-code` isn't swallowed). Add the import near the other view imports and this entry into the `orders` urlpatterns list:

```python
from apps.orders.transport.views.shipping_lookup_view import lookup_by_shipping_code
```
```python
    path("lookup-by-shipping-code", lookup_by_shipping_code, name="orders-lookup-shipping-code"),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest apps/orders/tests/test_shipping_lookup.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/transport/views/shipping_lookup_view.py backend/apps/orders/urls.py backend/apps/orders/tests/test_shipping_lookup.py
git commit -m "feat(orders): lookup order by exact shipping_code endpoint"
```

---

### Task 3: Dispatch batch (xuất giao)

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_commands.py` (add `dispatch_orders_batch`)
- Modify: `backend/apps/orders/services.py` (expose on `OrderService`)
- Create: `backend/apps/orders/transport/views/dispatch_batch_view.py`
- Modify: `backend/apps/orders/urls.py`
- Test: `backend/apps/orders/tests/test_scan_dispatch_batch.py`

**Interfaces:**
- Consumes: `OrderService.update_order_inline(order, data, actor)` (existing). Passing `data={"fulfillment_warehouse": <Warehouse>, "crm": {"delivery_business_status_code": "dispatched"}}` sets the warehouse and triggers idempotent `issue_for_order`.
- Produces:
  - `OrderServiceCommands.dispatch_orders_batch(*, order_ids: list[int], warehouse_id: int, actor) -> list[dict]` → each `{order_id, ok: bool, warnings: list[str], stock_warnings: list, error?: str}`.
  - `POST /api/orders/dispatch-batch` body `{"fulfillment_warehouse_id": int, "order_ids": [int]}` → `{"results": [...]}`.

- [ ] **Step 1: Write the failing service test**

Create `backend/apps/orders/tests/test_scan_dispatch_batch.py`:

```python
from __future__ import annotations

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType, StockItem, StockMovement, StockState
from apps.orders.models import Order, OrderItem, OrderStatus, Product
from apps.orders.services import OrderService
from apps.users.test_helpers import make_admin
from apps.warehouses.models import Warehouse


class DispatchBatchServiceTests(TestCase):
    def setUp(self):
        self.actor = make_admin("dispatch-batch-actor")
        self.channel = Channel.objects.create(code="lp-db", name="Dispatch Batch")
        self.wh = Warehouse.objects.create(code="WH-DB", name="Kho DB")
        self.product = Product.objects.create(name="SP DB", sku="SKU-DB-1")
        StockCommandService.record_movement(
            warehouse_id=self.wh.id, product_id=self.product.id,
            qty_delta=10, movement_type=MovementType.RECEIPT,
        )

    def _order(self, suffix, status=OrderStatus.AWAITING_SHIPMENT, qty=2):
        customer = Customer.objects.create(
            full_name=f"K {suffix}", phone_raw=f"092000000{suffix}",
            phone_normalized=f"092000000{suffix}", code=f"CUST-DB-{suffix}",
        )
        o = Order.objects.create(
            channel=self.channel, customer=customer, name=f"K {suffix}",
            phone=f"092000000{suffix}", order_code=f"DH-DB-{suffix}",
            shipping_code=f"VD-DB-{suffix}", status=status,
        )
        OrderItem.objects.create(
            order=o, product=self.product, product_name=self.product.name,
            sku=self.product.sku, quantity=qty, unit_price=1000, line_total=qty * 1000,
        )
        return o

    def test_dispatch_batch_deducts_and_marks_dispatched(self):
        o1 = self._order("1")
        o2 = self._order("2")
        results = OrderService.dispatch_orders_batch(
            order_ids=[o1.id, o2.id], warehouse_id=self.wh.id, actor=self.actor,
        )
        assert all(r["ok"] for r in results)
        item = StockItem.objects.get(warehouse=self.wh, product=self.product, state=StockState.SELLABLE)
        assert item.quantity == 6  # 10 - 2 - 2
        for o in (o1, o2):
            o.refresh_from_db()
            assert o.fulfillment_warehouse_id == self.wh.id
            assert o.assessment.delivery_business_status_code == "dispatched"

    def test_dispatch_batch_idempotent(self):
        o1 = self._order("3")
        OrderService.dispatch_orders_batch(order_ids=[o1.id], warehouse_id=self.wh.id, actor=self.actor)
        OrderService.dispatch_orders_batch(order_ids=[o1.id], warehouse_id=self.wh.id, actor=self.actor)
        count = StockMovement.objects.filter(
            reference_type="order", reference_id=o1.id, movement_type=MovementType.SALE,
        ).count()
        assert count == 1

    def test_dispatch_batch_warns_unexpected_status(self):
        o1 = self._order("4", status=OrderStatus.NEW)
        results = OrderService.dispatch_orders_batch(
            order_ids=[o1.id], warehouse_id=self.wh.id, actor=self.actor,
        )
        assert results[0]["ok"] is True
        assert "unexpected_status" in results[0]["warnings"]

    def test_dispatch_batch_missing_order(self):
        results = OrderService.dispatch_orders_batch(
            order_ids=[999999], warehouse_id=self.wh.id, actor=self.actor,
        )
        assert results[0]["ok"] is False
        assert results[0]["error"] == "not_found"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/orders/tests/test_scan_dispatch_batch.py -q`
Expected: FAIL (`OrderService` has no attribute `dispatch_orders_batch`).

- [ ] **Step 3: Add the service method**

In `backend/apps/orders/application/services/order_service_commands.py`, add this static method to the `OrderServiceCommands` class (place it right after `update_order_inline`):

```python
    @staticmethod
    def dispatch_orders_batch(*, order_ids: list[int], warehouse_id: int, actor) -> list[dict]:
        from django.db import transaction

        from apps.inventory.models import MovementType, StockMovement
        from apps.warehouses.models import Warehouse

        _EXPECTED = {2, 11}  # IN_DELIVERY, AWAITING_SHIPMENT
        warehouse = Warehouse.objects.get(id=warehouse_id)
        results: list[dict] = []
        for oid in order_ids:
            order = (
                Order.objects.select_related("channel", "customer")
                .prefetch_related("items")
                .filter(id=oid, deleted_at__isnull=True)
                .first()
            )
            if order is None:
                results.append({"order_id": oid, "ok": False, "error": "not_found"})
                continue
            warnings: list[str] = []
            if int(order.status) not in _EXPECTED:
                warnings.append("unexpected_status")
            already = StockMovement.objects.filter(
                reference_type="order", reference_id=order.id,
                movement_type=MovementType.SALE,
            ).exists()
            if already:
                warnings.append("already_dispatched")
            try:
                with transaction.atomic():
                    stock_warnings = OrderServiceCommands.update_order_inline(
                        order,
                        {
                            "fulfillment_warehouse": warehouse,
                            "crm": {"delivery_business_status_code": "dispatched"},
                        },
                        actor,
                    )
            except Exception as exc:  # ValidationError or others → fail this order only
                results.append({"order_id": oid, "ok": False, "error": str(exc)})
                continue
            results.append({
                "order_id": oid, "ok": True,
                "warnings": warnings, "stock_warnings": stock_warnings,
            })
        return results
```

- [ ] **Step 4: Expose on the `OrderService` facade**

In `backend/apps/orders/services.py`, the `OrderService` facade already delegates to `OrderServiceCommands` (find an existing line like `update_order_inline = OrderServiceCommands.update_order_inline` or a wrapper). Add the matching delegation in the same style. If the facade uses explicit `@staticmethod` wrappers, add:

```python
    @staticmethod
    def dispatch_orders_batch(*, order_ids, warehouse_id, actor):
        return OrderServiceCommands.dispatch_orders_batch(
            order_ids=order_ids, warehouse_id=warehouse_id, actor=actor
        )
```

(If it uses direct attribute assignment, add `dispatch_orders_batch = OrderServiceCommands.dispatch_orders_batch` next to the others instead.)

- [ ] **Step 5: Run the service test to verify PASS**

Run: `python -m pytest apps/orders/tests/test_scan_dispatch_batch.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Add the view + route**

Create `backend/apps/orders/transport/views/dispatch_batch_view.py`:

```python
from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.services import OrderService
from apps.users.permissions import require_section


@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("shipments")])
def dispatch_batch(request):
    warehouse_id = request.data.get("fulfillment_warehouse_id")
    order_ids = request.data.get("order_ids") or []
    if not warehouse_id:
        return Response({"detail": "fulfillment_warehouse_id is required."}, status=status.HTTP_400_BAD_REQUEST)
    if not isinstance(order_ids, list) or not order_ids:
        return Response({"detail": "order_ids must be a non-empty list."}, status=status.HTTP_400_BAD_REQUEST)
    results = OrderService.dispatch_orders_batch(
        order_ids=[int(x) for x in order_ids],
        warehouse_id=int(warehouse_id),
        actor=request.user,
    )
    return Response({"results": results})
```

In `backend/apps/orders/urls.py` add the import and a path (anywhere before the `<int:order_id>` catch-all):

```python
from apps.orders.transport.views.dispatch_batch_view import dispatch_batch
```
```python
    path("dispatch-batch", dispatch_batch, name="orders-dispatch-batch"),
```

- [ ] **Step 7: Add an API smoke test**

Append to `backend/apps/orders/tests/test_scan_dispatch_batch.py`:

```python
from rest_framework.test import APIClient


class DispatchBatchApiTests(DispatchBatchServiceTests):
    def test_dispatch_batch_endpoint(self):
        o1 = self._order("9")
        client = APIClient()
        client.force_authenticate(self.actor)
        resp = client.post(
            "/api/orders/dispatch-batch",
            data={"fulfillment_warehouse_id": self.wh.id, "order_ids": [o1.id]},
            format="json",
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["results"][0]["ok"] is True
```

- [ ] **Step 8: Run the full file to verify PASS**

Run: `python -m pytest apps/orders/tests/test_scan_dispatch_batch.py -q`
Expected: PASS (5 tests).

- [ ] **Step 9: Commit**

```bash
git add backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/services.py backend/apps/orders/transport/views/dispatch_batch_view.py backend/apps/orders/urls.py backend/apps/orders/tests/test_scan_dispatch_batch.py
git commit -m "feat(orders): dispatch-batch endpoint to mark scanned orders dispatched"
```

---

### Task 4: Return batch (nhập hoàn)

**Files:**
- Modify: `backend/apps/inventory/application/services/return_note_service.py` (add `create_returns_from_orders`)
- Modify: `backend/apps/inventory/transport/views/return_views.py` (add `create_returns_batch_view`)
- Modify: `backend/apps/inventory/urls.py`
- Test: `backend/apps/inventory/tests/test_return_batch.py`

**Interfaces:**
- Consumes: `ReturnNoteService.create_return(...)` and `ReturnNoteService.post_return(...)` (existing). `Order.items` → `OrderItem(product_id, quantity)`.
- Produces:
  - `ReturnNoteService.create_returns_from_orders(*, warehouse_id: int, items: list[dict], user_id: int | None = None) -> list[dict]` where each input item is `{order_id, return_type}`; each result `{order_id, ok, return_note_id?, code?, warnings: list[str], error?}`.
  - `POST /api/inventory/returns/batch` body `{"warehouse_id": int, "items": [{"order_id": int, "return_type": str}]}` → `{"results": [...]}`.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/inventory/tests/test_return_batch.py`:

```python
from __future__ import annotations

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_note_service import ReturnNoteService
from apps.inventory.models import ReturnNote, ReturnStatus, StockItem, StockState
from apps.orders.models import Order, OrderItem, OrderStatus, Product
from apps.warehouses.models import Warehouse


class ReturnBatchServiceTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="lp-rb", name="Return Batch")
        self.wh = Warehouse.objects.create(code="WH-RB", name="Kho RB")
        self.product = Product.objects.create(name="SP RB", sku="SKU-RB-1")

    def _order(self, suffix, status=OrderStatus.RETURNED, qty=3):
        customer = Customer.objects.create(
            full_name=f"K {suffix}", phone_raw=f"093000000{suffix}",
            phone_normalized=f"093000000{suffix}", code=f"CUST-RB-{suffix}",
        )
        o = Order.objects.create(
            channel=self.channel, customer=customer, name=f"K {suffix}",
            phone=f"093000000{suffix}", order_code=f"DH-RB-{suffix}",
            shipping_code=f"VD-RB-{suffix}", status=status,
        )
        OrderItem.objects.create(
            order=o, product=self.product, product_name=self.product.name,
            sku=self.product.sku, quantity=qty, unit_price=1000, line_total=qty * 1000,
        )
        return o

    def test_creates_one_returnnote_per_order_and_adds_defect_stock(self):
        o1 = self._order("1")
        results = ReturnNoteService.create_returns_from_orders(
            warehouse_id=self.wh.id,
            items=[{"order_id": o1.id, "return_type": "defect"}],
        )
        assert results[0]["ok"] is True
        rn = ReturnNote.objects.get(id=results[0]["return_note_id"])
        assert rn.source_order_id == o1.id
        assert rn.status == ReturnStatus.POSTED
        assert rn.customer_name == "K 1"
        item = StockItem.objects.get(warehouse=self.wh, product=self.product, state=StockState.DEFECT)
        assert item.quantity == 3

    def test_delivery_returned_goes_to_sellable(self):
        o1 = self._order("2")
        ReturnNoteService.create_returns_from_orders(
            warehouse_id=self.wh.id,
            items=[{"order_id": o1.id, "return_type": "delivery_returned"}],
        )
        item = StockItem.objects.get(warehouse=self.wh, product=self.product, state=StockState.SELLABLE)
        assert item.quantity == 3

    def test_dedup_already_returned(self):
        o1 = self._order("3")
        ReturnNoteService.create_returns_from_orders(
            warehouse_id=self.wh.id, items=[{"order_id": o1.id, "return_type": "defect"}],
        )
        results = ReturnNoteService.create_returns_from_orders(
            warehouse_id=self.wh.id, items=[{"order_id": o1.id, "return_type": "defect"}],
        )
        assert results[0]["ok"] is False
        assert results[0]["error"] == "already_returned"

    def test_warns_unexpected_status_but_still_creates(self):
        o1 = self._order("4", status=OrderStatus.NEW)
        results = ReturnNoteService.create_returns_from_orders(
            warehouse_id=self.wh.id, items=[{"order_id": o1.id, "return_type": "defect"}],
        )
        assert results[0]["ok"] is True
        assert "unexpected_status" in results[0]["warnings"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/inventory/tests/test_return_batch.py -q`
Expected: FAIL (`ReturnNoteService` has no `create_returns_from_orders`).

- [ ] **Step 3: Add the service method**

In `backend/apps/inventory/application/services/return_note_service.py`, add this static method to `ReturnNoteService`:

```python
    @staticmethod
    def create_returns_from_orders(
        *, warehouse_id: int, items: list[dict], user_id: int | None = None
    ) -> list[dict]:
        from django.db import transaction

        from apps.orders.models import Order

        _EXPECTED = {4, 5, 13}  # IN_RETURN, RETURNED, AWAITING_RETURN_CONFIRMATION
        results: list[dict] = []
        for item in items:
            oid = item["order_id"]
            return_type = item["return_type"]
            order = (
                Order.objects.prefetch_related("items")
                .filter(id=oid, deleted_at__isnull=True)
                .first()
            )
            if order is None:
                results.append({"order_id": oid, "ok": False, "error": "not_found"})
                continue
            if ReturnNote.objects.filter(
                source_order_id=order.id, status=ReturnStatus.POSTED
            ).exists():
                results.append({"order_id": oid, "ok": False, "error": "already_returned"})
                continue
            warnings: list[str] = []
            if int(order.status) not in _EXPECTED:
                warnings.append("unexpected_status")
            order_lines = [
                {"product_id": it.product_id, "quantity": it.quantity}
                for it in order.items.all()
            ]
            if not order_lines:
                results.append({"order_id": oid, "ok": False, "error": "no_items"})
                continue
            try:
                with transaction.atomic():
                    rn = ReturnNoteService.create_return(
                        return_type=return_type,
                        warehouse_id=warehouse_id,
                        lines=order_lines,
                        source_order_id=order.id,
                        customer_name=order.name,
                        customer_phone=order.phone,
                        user_id=user_id,
                    )
                    ReturnNoteService.post_return(return_note_id=rn.id, user_id=user_id)
            except Exception as exc:
                results.append({"order_id": oid, "ok": False, "error": str(exc)})
                continue
            results.append({
                "order_id": oid, "ok": True,
                "return_note_id": rn.id, "code": rn.code, "warnings": warnings,
            })
        return results
```

- [ ] **Step 4: Run the service test to verify PASS**

Run: `python -m pytest apps/inventory/tests/test_return_batch.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Add the view + route**

In `backend/apps/inventory/transport/views/return_views.py`, add (match the existing imports/decorator style in that file — it already uses `require_section`):

```python
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_section("returns")])
def create_returns_batch_view(request):
    warehouse_id = request.data.get("warehouse_id")
    items = request.data.get("items") or []
    if not warehouse_id:
        return Response({"detail": "warehouse_id is required."}, status=status.HTTP_400_BAD_REQUEST)
    if not isinstance(items, list) or not items:
        return Response({"detail": "items must be a non-empty list."}, status=status.HTTP_400_BAD_REQUEST)
    results = ReturnNoteService.create_returns_from_orders(
        warehouse_id=int(warehouse_id),
        items=[{"order_id": int(i["order_id"]), "return_type": i["return_type"]} for i in items],
        user_id=request.user.id,
    )
    return Response({"results": results})
```

(Confirm `ReturnNoteService`, `api_view`, `permission_classes`, `IsAuthenticated`, `require_section`, `status`, `Response` are imported at the top of `return_views.py`; add any that are missing, mirroring the existing `post_return_view`. The correct section name is the one guarding the existing returns endpoints — use the same string `post_return_view` uses.)

In `backend/apps/inventory/urls.py`, register before/after the existing `returns/<int:return_id>/post` route:

```python
    path("returns/batch", create_returns_batch_view, name="inventory-returns-batch"),
```
(and add `create_returns_batch_view` to the import from `return_views`.)

- [ ] **Step 6: Add an API smoke test**

Append to `backend/apps/inventory/tests/test_return_batch.py`:

```python
from rest_framework.test import APIClient

from apps.users.test_helpers import make_admin


class ReturnBatchApiTests(ReturnBatchServiceTests):
    def test_return_batch_endpoint(self):
        o1 = self._order("9")
        client = APIClient()
        client.force_authenticate(make_admin("return-batch-admin"))
        resp = client.post(
            "/api/inventory/returns/batch",
            data={"warehouse_id": self.wh.id, "items": [{"order_id": o1.id, "return_type": "defect"}]},
            format="json",
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["results"][0]["ok"] is True
```

- [ ] **Step 7: Run the full file to verify PASS**

Run: `python -m pytest apps/inventory/tests/test_return_batch.py -q`
Expected: PASS (5 tests).

- [ ] **Step 8: Commit**

```bash
git add backend/apps/inventory/application/services/return_note_service.py backend/apps/inventory/transport/views/return_views.py backend/apps/inventory/urls.py backend/apps/inventory/tests/test_return_batch.py
git commit -m "feat(inventory): batch-create return notes from scanned orders"
```

---

### Task 5: Regression sweep

**Files:** none (verification only)

- [ ] **Step 1: Run the inventory + orders test suites**

Run: `python -m pytest apps/inventory apps/orders -q`
Expected: PASS (no regressions; known-baseline failures per memory `known-failing-baseline-tests` are unrelated — confirm any failure predates this branch with `git stash` if unsure).

- [ ] **Step 2: Push the branch**

```bash
git push origin feat/scan-shipment-stock-document
```

---

## Self-Review

**Spec coverage:**
- §3.1 lookup → Task 2. §3.2 dispatch → Task 3. §3.3 return batch → Task 4. §3.4 drop RETURNED auto-release → Task 1. §3.5 tests → folded into each task + Task 5. Frontend (§4) is intentionally a **separate plan** (depends on these endpoints; written after backend merges).
- Q1 (dispatch reuses status flow) → Task 3. Q2 (ReturnNote per order) → Task 4. Q6 (no double count) → Task 1. Status-warning sets → Tasks 3/4 `_EXPECTED`.

**Placeholders:** Two spots require reading neighboring code rather than guessing — the `OrderService` facade delegation style (Task 3 Step 4) and the `return_views.py` import/section name (Task 4 Step 5). Both give exact instructions on what to mirror; not open-ended.

**Type consistency:** `dispatch_orders_batch(order_ids, warehouse_id, actor)` and `create_returns_from_orders(warehouse_id, items, user_id)` signatures match between service definition, facade, views, and tests. Result dict keys (`ok`, `warnings`, `error`, `return_note_id`, `code`, `stock_warnings`) are consistent across producer and test assertions.

**Open follow-up (not blocking):** Frontend plan (`ScanShipmentInput` + dispatch UI on shipments page + return batch UI on returns page) to be written next.
