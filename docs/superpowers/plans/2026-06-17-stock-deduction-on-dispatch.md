# Trừ/hoàn tồn kho theo trạng thái xuất kho — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trừ tồn kho khi đơn chuyển `delivery_business_status_code → "dispatched"` (Đã xuất kho), và tự hoàn tồn kho khi `order.status → RETURNED/CANCELLED` (kể cả do đồng bộ KiotViet).

**Architecture:** Deduct gọi trực tiếp (idempotent `StockSalesService.issue_for_order`) trong transaction của `update_order_inline`, chặn nếu thiếu kho. Release qua domain event `OrderStatusChanged` phát ở mọi nơi đổi `order.status` (inline + KiotViet sync) → subscriber inventory gọi `release_for_order` (idempotent). Tái dùng `Order.fulfillment_warehouse` + thêm cột "Kho xuất" inline trên trang Vận đơn.

**Tech Stack:** Django + DRF, event bus in-process (`apps/common/events.py`), pytest; React + TS, OrdersTable.

**Spec:** `docs/superpowers/specs/2026-06-17-stock-deduction-on-dispatch-design.md`

## Global Constraints

- Tồn kho LOCAL authoritative — không sync từ KiotViet. Không sửa core kho (`StockCommandService`, movements).
- Tái dùng `StockSalesService.issue_for_order` / `release_for_order` (đã idempotent). KHÔNG sửa 2 hàm này.
- Backend test chạy bằng ephemeral docker (prod image thiếu tests):
  ```
  wc=$(docker ps -qf name=web | head -1)
  docker run --rm --user root --network desi-order_default \
    --env-file <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$wc" | grep -E '^(DATABASE|POSTGRES|DJANGO|REDIS|SECRET|DB_|CELERY|MINIO|ALLOWED|CACHE)') \
    -v "$(pwd)/backend:/app" -w /app desi-order-web python -m pytest <paths> -q --reuse-db
  ```
- `OrderStatus.RETURNED = 5`, `OrderStatus.CANCELLED = 6`, `OrderStatus.PENDING_PROCESSING = 1` (`apps/orders/models.py:15-29`).
- `delivery_business_status` code "Đã xuất kho" = `"dispatched"` (`apps/orders/crm_catalog.py`).

---

## File Structure

- Create: `backend/apps/orders/contracts/order_events.py` — `OrderStatusChanged` event.
- Modify: `backend/apps/inventory/subscribers.py` — subscriber hoàn kho.
- Modify: `backend/apps/orders/application/services/order_service_commands.py` — bỏ confirm-deduct; phát event; deduct-on-dispatch + guard kho.
- Modify: `backend/apps/kiotviet/webhook_impl.py`, `backend/apps/kiotviet/infrastructure/services/service_actions.py` — phát event khi status đổi.
- Modify/Test: `backend/apps/orders/tests/test_order_stock_integration.py` (+ test mới trong cùng file hoặc file mới).
- Frontend: `frontend/src/features/orders/order-types.ts`, `frontend/src/components/OrdersTable.tsx`, `frontend/src/features/inventory/shipments/{shipment-columns.ts,use-shipments.ts}`, `frontend/src/features/inventory/pages/ShipmentsPage.tsx`, i18n.

---

## SPRINT 1 — Backend: event + subscriber hoàn kho

### Task 1: `OrderStatusChanged` event + subscriber release

**Files:**
- Create: `backend/apps/orders/contracts/order_events.py`
- Modify: `backend/apps/inventory/subscribers.py`
- Test: `backend/apps/inventory/tests/test_release_on_status_change.py`

**Interfaces:**
- Produces: `OrderStatusChanged(order_id: int, old_status: int, new_status: int)`, `event_type="orders.status_changed"`. Dùng ở Task 2, 3.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_release_on_status_change.py`. Bám fixture/helper test inventory hiện có (xem `apps/inventory/tests/test_stock_sales_service.py` để tạo warehouse/product/seed tồn + cách gọi service). Test:

```python
import pytest
from apps.common.events import event_bus
from apps.orders.contracts.order_events import OrderStatusChanged
from apps.inventory.application.services.stock_sales_service import StockSalesService, SaleLine
from apps.inventory.models import StockItem, StockMovement, MovementType, StockState


@pytest.mark.django_db
def test_release_on_returned_status(warehouse, product):
    # seed: trừ kho cho order 999 trước (giả lập đã xuất kho)
    StockSalesService.issue_for_order(
        order_id=999, warehouse_id=warehouse.id,
        lines=[SaleLine(product_id=product.id, quantity=3)], user_id=None,
    )
    item = StockItem.objects.get(warehouse=warehouse, product=product, state=StockState.SELLABLE)
    before = item.quantity
    # publish event RETURNED -> subscriber hoàn kho
    event_bus.publish(OrderStatusChanged(order_id=999, old_status=2, new_status=5))
    item.refresh_from_db()
    assert item.quantity == before + 3
    assert StockMovement.objects.filter(
        reference_type="order", reference_id=999, movement_type=MovementType.SALE_REVERSAL
    ).exists()


@pytest.mark.django_db
def test_no_release_on_non_terminal_status(warehouse, product):
    StockSalesService.issue_for_order(
        order_id=998, warehouse_id=warehouse.id,
        lines=[SaleLine(product_id=product.id, quantity=2)], user_id=None,
    )
    item = StockItem.objects.get(warehouse=warehouse, product=product, state=StockState.SELLABLE)
    before = item.quantity
    event_bus.publish(OrderStatusChanged(order_id=998, old_status=1, new_status=2))  # IN_DELIVERY
    item.refresh_from_db()
    assert item.quantity == before  # không hoàn
```

Nếu chưa có fixture `warehouse`/`product`, tạo trong test (hoặc conftest) theo cách `test_stock_sales_service.py` làm. Đảm bảo `subscribers.register()` đã chạy (app ready) — nếu test không tự nạp, gọi `from apps.inventory import subscribers; subscribers.register()` ở đầu test/fixture.

- [ ] **Step 2: Chạy test — FAIL** (ImportError `order_events`).

- [ ] **Step 3: Tạo event**

`backend/apps/orders/contracts/order_events.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from apps.common.events import DomainEvent


@dataclass(frozen=True)
class OrderStatusChanged(DomainEvent):
    """Phát khi Order.status đổi (inline edit hoặc đồng bộ KiotViet)."""

    event_type: ClassVar[str] = "orders.status_changed"
    order_id: int
    old_status: int
    new_status: int
```

- [ ] **Step 4: Thêm subscriber hoàn kho**

Sửa `backend/apps/inventory/subscribers.py` — thêm handler + đăng ký trong `register()`:
```python
from apps.orders.contracts.order_events import OrderStatusChanged
from apps.inventory.contracts.stock_sales import StockSalesService

# OrderStatus terminal cần hoàn kho: RETURNED=5, CANCELLED=6
_RELEASE_STATUSES = frozenset({5, 6})


def _on_order_status_changed(event: OrderStatusChanged) -> None:
    if event.new_status in _RELEASE_STATUSES:
        StockSalesService.release_for_order(order_id=event.order_id)
```
Trong `register()`, thêm:
```python
    event_bus.subscribe(OrderStatusChanged.event_type, _on_order_status_changed)
```
(giữ nguyên đăng ký `StockWentNegative` sẵn có; import `StockSalesService` từ contracts `apps.inventory.contracts.stock_sales` — kiểm tra đường import này khớp như `order_service_commands.py:343`.)

- [ ] **Step 5: Chạy test — PASS** (2 test).

- [ ] **Step 6: Commit**
```bash
git add backend/apps/orders/contracts/order_events.py backend/apps/inventory/subscribers.py backend/apps/inventory/tests/test_release_on_status_change.py
git commit -m "feat(inventory): release stock on order returned/cancelled via event"
```

---

## SPRINT 2 — Backend: deduct-on-dispatch + bỏ confirm-deduct + phát event (inline)

### Task 2: Sửa `update_order_inline`

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_commands.py:339-363` (xóa block cũ) + thêm logic mới + phát event.
- Test: `backend/apps/orders/tests/test_dispatch_stock.py`

**Interfaces:**
- Consumes: `OrderStatusChanged` (Task 1), `StockSalesService.issue_for_order`, `SaleLine`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/orders/tests/test_dispatch_stock.py`. Bám `apps/orders/tests/test_order_stock_integration.py` để tạo Order + OrderItem + Warehouse + seed tồn, và gọi `OrderService.update_order_inline(order, data, actor)`. Test:

```python
# (1) dispatched + có kho -> trừ tồn
def test_dispatch_deducts_stock(...):
    # order có fulfillment_warehouse, 1 item qty=2, seed tồn 10
    OrderService.update_order_inline(order, {"crm": {"delivery_business_status_code": "dispatched"}}, actor)
    # assert StockItem.quantity == 8 và có movement SALE

# (2) dispatched + KHÔNG kho -> ValidationError, không trừ, không lưu status
def test_dispatch_without_warehouse_blocks(...):
    order.fulfillment_warehouse = None; order.save()
    with pytest.raises(<DRF ValidationError>):
        OrderService.update_order_inline(order, {"crm": {"delivery_business_status_code": "dispatched"}}, actor)
    order.refresh_from_db()
    assert order.assessment... delivery_business_status_code != "dispatched"  # rollback
    # không có movement SALE

# (3) dispatched 2 lần -> chỉ trừ 1 lần (idempotent)

# (4) confirm (status -> PENDING_PROCESSING) KHÔNG còn trừ tồn
def test_confirm_no_longer_deducts(...):
    OrderService.update_order_inline(order, {"status": 1}, actor)
    # assert KHÔNG có movement SALE

# (5) status -> RETURNED phát event -> hoàn (integration với subscriber)
def test_returned_releases_via_event(...):
    # trước: dispatched đã trừ; rồi update status=5 -> sau commit, tồn được hoàn
```

Dùng `crm_payload` đúng dạng mà `update_order_inline` mong đợi (xem cách view/test hiện tại truyền `crm`). Với ValidationError, import từ `rest_framework.exceptions`. Với test (5) cần subscriber chạy + on_commit — dùng `django.test.TestCase` với `captureOnCommitCallbacks` hoặc pytest `django_capture_on_commit_callbacks` fixture để chạy on_commit; đảm bảo `subscribers.register()` đã nạp.

- [ ] **Step 2: Chạy test — FAIL.**

- [ ] **Step 3: Xóa block confirm-deduct/cancel-release**

Trong `order_service_commands.py`, XÓA nguyên block dòng 339-363 (từ comment `# Stock deduction hook:` đến hết nhánh `elif ... CANCELLED: release_for_order`). Giữ lại biến `stock_warnings: list = []` (khởi tạo lại ở chỗ phù hợp, xem Step 5).

- [ ] **Step 4: Phát `OrderStatusChanged` khi status đổi (inline)**

Ngay sau `order.save(update_fields=[*data.keys(), "updated_at"])` (dòng 337), thêm:
```python
        stock_warnings: list = []
        if int(old_status) != int(order.status):
            from apps.common.events import event_bus
            from apps.orders.contracts.order_events import OrderStatusChanged

            event_bus.publish_on_commit(
                OrderStatusChanged(
                    order_id=order.id,
                    old_status=int(old_status),
                    new_status=int(order.status),
                )
            )
```

- [ ] **Step 5: Deduct-on-dispatch + guard kho**

Ngay SAU khối `_upsert_order_assessment` (sau dòng `order.assessment = assessment`, ~415), thêm:
```python
        if "delivery_business_status_code" in changed_assessment_fields and (
            assessment is not None
            and assessment.delivery_business_status_code == "dispatched"
        ):
            from rest_framework.exceptions import ValidationError

            if not order.fulfillment_warehouse_id:
                raise ValidationError(
                    {"fulfillment_warehouse": "Hãy chọn kho xuất trước khi chuyển sang Đã xuất kho."}
                )
            from apps.inventory.contracts.stock_sales import SaleLine, StockSalesService

            lines = [
                SaleLine(product_id=it.product_id, quantity=it.quantity)
                for it in order.items.all()
            ]
            warnings = StockSalesService.issue_for_order(
                order_id=order.id,
                warehouse_id=order.fulfillment_warehouse_id,
                lines=lines,
                user_id=getattr(actor, "id", None),
            )
            stock_warnings = [
                {"product_id": w.product_id, "balance_after": w.balance_after}
                for w in warnings
            ]
```
(`raise ValidationError` trong `@transaction.atomic` → rollback toàn bộ kể cả assessment vừa lưu.)

- [ ] **Step 6: Chạy test — PASS** (5 test).

- [ ] **Step 7: Commit**
```bash
git add backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/tests/test_dispatch_stock.py
git commit -m "feat(orders): deduct stock on dispatched, release via status event"
```

---

## SPRINT 3 — Backend: phát event từ đồng bộ KiotViet

### Task 3: Phát `OrderStatusChanged` ở các điểm KiotViet đổi status

**Files:**
- Modify: `backend/apps/kiotviet/webhook_impl.py` (~509-510)
- Modify: `backend/apps/kiotviet/infrastructure/services/service_actions.py` (~138-141)
- Audit: `backend/apps/kiotviet/infrastructure/services/_sync_service_sync_mixin.py` — xác định có ghi `order.status` xuống DB không; nếu CÓ thì phát event tương tự, nếu chỉ build payload thì bỏ qua.
- Test: `backend/apps/kiotviet/tests/test_status_change_releases_stock.py`

**Interfaces:**
- Consumes: `OrderStatusChanged` (Task 1).

- [ ] **Step 1: Viết test thất bại**

Tạo test mô phỏng đường KiotViet đổi status sang RETURNED và assert tồn được hoàn (qua subscriber). Cách thực tế: seed order đã có movement SALE (gọi `issue_for_order`), rồi gọi trực tiếp hàm webhook/service_actions với payload khiến status→5, bọc trong `django_capture_on_commit_callbacks(execute=True)`; assert SALE_REVERSAL xuất hiện. Nếu việc gọi hàm KiotViet quá nặng để dựng, tối thiểu test ở mức: hàm phát event đúng (patch `event_bus.publish_on_commit` và assert được gọi với `OrderStatusChanged(order_id, old, 5)`).

- [ ] **Step 2: Chạy test — FAIL.**

- [ ] **Step 3: webhook_impl.py**

Cần `old_status` TRƯỚC khi `order.status` bị ghi đè ở dòng 510. Tại dòng 477-479 (`if next_status is not None and next_status != order.status:`), capture giá trị cũ ngay tại đó:
```python
        if next_status is not None and next_status != order.status:
            prev_status_for_event = int(order.status)   # <-- thêm dòng này
            update_values["status"] = next_status
            status_changed = True
```
Rồi sau block `if "status" in update_values: order.status = update_values["status"]` (dòng 509-510), thêm:
```python
            if "status" in update_values:
                from apps.common.events import event_bus
                from apps.orders.contracts.order_events import OrderStatusChanged

                event_bus.publish_on_commit(
                    OrderStatusChanged(
                        order_id=order.id,
                        old_status=prev_status_for_event,
                        new_status=int(update_values["status"]),
                    )
                )
```

- [ ] **Step 4: service_actions.py**

Tại dòng 138-141, khi `invoice_status is not None`:
```python
                if invoice_status is not None:
                    prev = int(order.status)
                    order.status = invoice_status
                    update_fields.append("status")
                    changed = changed or previous_status != invoice_status
                    if prev != int(invoice_status):
                        from apps.common.events import event_bus
                        from apps.orders.contracts.order_events import OrderStatusChanged

                        event_bus.publish_on_commit(
                            OrderStatusChanged(
                                order_id=order.id,
                                old_status=prev,
                                new_status=int(invoice_status),
                            )
                        )
```
(release subscriber chỉ cần order_id + new_status; thời điểm save không ảnh hưởng vì `release_for_order` đảo theo StockMovement.)

- [ ] **Step 5: Audit sync mixin**

Mở `_sync_service_sync_mixin.py`. Nếu nó GHI `order.status` xuống DB (vd `Order.objects...update(status=...)` hoặc `order.status = ...; save`), thêm phát `OrderStatusChanged` y hệt. Nếu chỉ build dict payload cho service_actions/webhook xử lý (không tự ghi DB) → KHÔNG cần sửa; ghi 1 dòng `log` xác nhận trong report. Liệt kê kết luận audit.

- [ ] **Step 6: Chạy test — PASS.**

- [ ] **Step 7: Commit**
```bash
git add backend/apps/kiotviet/ backend/apps/kiotviet/tests/test_status_change_releases_stock.py
git commit -m "feat(kiotviet): publish OrderStatusChanged on synced status change"
```

---

## SPRINT 4 — Backend: sửa test cũ + field policy

### Task 4: Cập nhật test cũ + cho phép sửa fulfillment_warehouse inline

**Files:**
- Modify: `backend/apps/orders/tests/test_order_stock_integration.py`
- Verify/Modify: field policy của `fulfillment_warehouse` (xem `FieldPolicyWriteMixin` + `order_field_policy`)

- [ ] **Step 1:** Chạy `test_order_stock_integration.py` — kỳ vọng test `test_confirm_order_deducts_stock` (và tương tự) FAIL vì confirm không còn trừ.

- [ ] **Step 2:** Sửa các test này theo hành vi MỚI: confirm KHÔNG trừ; thay bằng kịch bản dispatched trừ / RETURNED hoàn (hoặc chuyển các assertion sang `test_dispatch_stock.py` và xóa test lỗi thời). Giữ coverage cho release.

- [ ] **Step 3:** Field policy: kiểm tra user section `shipments` PATCH `fulfillment_warehouse` qua `update_order_inline` không bị `FieldPolicyWriteMixin` chặn. Viết 1 test nhỏ: user `shipments` PATCH `{"fulfillment_warehouse": <id>}` → 200 và order.fulfillment_warehouse_id cập nhật. Nếu bị chặn, nới policy cho field này ở luồng vận đơn (đọc `_view_helpers.order_field_policy` + mixin để sửa đúng chỗ, tối thiểu quyền).

- [ ] **Step 4:** Chạy lại toàn bộ test orders+inventory liên quan — PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/apps/orders/tests/test_order_stock_integration.py backend/apps/orders/
git commit -m "test(orders): update stock tests to dispatch-based deduction"
```

---

## SPRINT 5 — Frontend: cột "Kho xuất" trên trang Vận đơn

### Task 5: Type + nạp options kho + truyền vào bảng

**Files:**
- Modify: `frontend/src/features/orders/order-types.ts` (OrderRow + ORDER_COLUMN_OPTIONS + ORDER_COLUMN_FIELD_BY_KEY)
- Modify: `frontend/src/features/inventory/shipments/use-shipments.ts` (nạp warehouses)
- Modify: `frontend/src/features/inventory/pages/ShipmentsPage.tsx` (truyền options)

**Interfaces:**
- Produces: `OrderRow.fulfillment_warehouse?: number | null`, `OrderRow.fulfillment_warehouse_name?: string | null`; prop `warehouseOptions` cho OrdersTable (Task 6).

- [ ] **Step 1: OrderRow type**

Trong `order-types.ts`, thêm vào `OrderRow`:
```typescript
  fulfillment_warehouse?: number | null;
  fulfillment_warehouse_name?: string | null;
```
Thêm `"fulfillment_warehouse"` vào type `OrderColumnKey` (union), vào `ORDER_COLUMN_OPTIONS`:
```typescript
  { key: "fulfillment_warehouse", label: "orders.columns.fulfillmentWarehouse" },
```
và `ORDER_COLUMN_FIELD_BY_KEY`:
```typescript
  fulfillment_warehouse: "fulfillment_warehouse_name",
```

- [ ] **Step 2: i18n label**

Thêm `"orders.columns.fulfillmentWarehouse": "Kho xuất"` (vi) + `"Fulfillment warehouse"` (en) trong `frontend/src/i18n/messages/orders.ts` (cạnh các `orders.columns.*`).

- [ ] **Step 3: Nạp warehouses trong use-shipments**

Trong `use-shipments.ts`, thêm state `warehouses: {id:number; name:string}[]` + fetch 1 lần `GET /api/warehouses/` (dùng `fetchJson`). Export `warehouses` từ hook. (Kiểm tra dạng response `/api/warehouses/` — list trực tiếp hay `{results}`; parse phù hợp.)

- [ ] **Step 4: Truyền vào ShipmentsPage → OrdersTable**

Trong `ShipmentsPage.tsx`, lấy `ship.warehouses`, truyền prop `warehouseOptions={ship.warehouses}` cho `<OrdersTable ... />`.

- [ ] **Step 5: tsc + commit**
```bash
cd frontend && npx tsc --noEmit -p tsconfig.json
git add frontend/src/features/orders/order-types.ts frontend/src/features/inventory/ frontend/src/i18n/messages/orders.ts
git commit -m "feat(shipments): warehouse type + options plumbing"
```

### Task 6: Cột select "Kho xuất" trong OrdersTable + SHIPMENT_COLUMNS

**Files:**
- Modify: `frontend/src/components/OrdersTable.tsx` (prop + cell render)
- Modify: `frontend/src/features/inventory/shipments/shipment-columns.ts`

**Interfaces:**
- Consumes: `warehouseOptions` prop (Task 5).

- [ ] **Step 1: Prop warehouseOptions**

Trong `OrdersTable` Props type, thêm:
```typescript
  warehouseOptions?: Array<{ id: number; name: string }>;
```
Destructure `warehouseOptions = []` trong component (cạnh các prop khác).

- [ ] **Step 2: Cell render**

Trong switch render cell (cạnh `case "delivery_business_status"`, ~OrdersTable.tsx:1031), thêm:
```tsx
    case "fulfillment_warehouse": {
      const current = row.fulfillment_warehouse ?? "";
      const canEdit = !!row.can_edit && !!onPatch;
      return (
        <select
          className="h-7 w-full min-w-[120px] cursor-pointer rounded border border-border bg-transparent px-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-ring disabled:cursor-default disabled:opacity-60"
          value={current === null ? "" : String(current)}
          disabled={!canEdit}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => {
            e.stopPropagation();
            const raw = e.target.value;
            const id = raw ? Number(raw) : null;
            const name = warehouseOptions.find((w) => w.id === id)?.name ?? null;
            onPatch?.(row.id, {
              fulfillment_warehouse: id,
              fulfillment_warehouse_name: name,
            } as Partial<OrderRow>);
          }}
        >
          <option value="">-</option>
          {warehouseOptions.map((w) => (
            <option key={w.id} value={String(w.id)}>
              {w.name}
            </option>
          ))}
        </select>
      );
    }
```

- [ ] **Step 3: SHIPMENT_COLUMNS**

Trong `shipment-columns.ts`, thêm `"fulfillment_warehouse"` ngay sau `"shipping_code"`:
```typescript
  "shipping_code", // Mã vận đơn
  "fulfillment_warehouse", // Kho xuất
```

- [ ] **Step 4: Verify**

`cd frontend && npx tsc --noEmit -p tsconfig.json && npm run build`. Mở trang Vận đơn: thấy cột "Kho xuất" (select), chọn kho → PATCH lưu; chuyển "Đã xuất kho" khi chưa chọn kho → toast lỗi từ server (đảm bảo `handlePatch` hiện message lỗi từ response).

- [ ] **Step 5: Commit**
```bash
git add frontend/src/components/OrdersTable.tsx frontend/src/features/inventory/shipments/shipment-columns.ts
git commit -m "feat(shipments): inline warehouse select column"
```

---

## SPRINT 6 — Kiểm thử tổng + dọn

### Task 7: E2E verify + docs

- [ ] **Step 1:** Chạy toàn bộ test backend liên quan (inventory + orders dispatch/stock + kiotviet) bằng ephemeral docker — PASS.
- [ ] **Step 2:** `npm run build` FE — PASS.
- [ ] **Step 3:** Kịch bản tay: chọn kho → "Đã xuất kho" → tồn giảm; đổi đơn sang "Đã chuyển hoàn"/"Đã hủy" → tồn hoàn; thiếu kho → bị chặn.
- [ ] **Step 4:** Cập nhật spec: đánh dấu "Đã triển khai" + ghi kết luận audit sync mixin (Task 3 Step 5).
- [ ] **Step 5: Commit**
```bash
git add docs/superpowers/specs/2026-06-17-stock-deduction-on-dispatch-design.md
git commit -m "docs(inventory): mark stock-on-dispatch implemented"
```

---

## Rủi ro / lưu ý

- **Bỏ sót điểm set status KiotViet** → đơn đó không tự hoàn. Task 3 Step 5 audit bắt buộc.
- `publish_on_commit` ngoài transaction sẽ chạy ngay — subscriber `release_for_order` chỉ cần `order_id`+`new_status` nên an toàn.
- Deduct dùng `order.items` — nếu đơn KiotViet thiếu OrderItem thì không trừ (no-op). Theo dõi dữ liệu thật.
- Field policy có thể chặn ghi `fulfillment_warehouse` — Task 4 Step 3 xử lý.
- `release_for_order`/`issue_for_order` idempotent — KHÔNG sửa.
