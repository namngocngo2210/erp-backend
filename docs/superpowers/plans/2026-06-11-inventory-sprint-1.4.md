# Inventory Sprint 1.4 Implementation Plan — Auto-deduct stock on order confirm

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Đơn được **xác nhận** (status → PENDING_PROCESSING) và có chọn kho xuất → tự ghi movement `sale` (−tồn) qua contract đồng bộ; **huỷ** đơn (→CANCELLED) → ghi `sale_reversal` (+tồn). Cho tồn âm + cảnh báo (event `StockWentNegative`, consumer đầu tiên của event bus).

**Architecture:** Gated theo `Order.fulfillment_warehouse` (nullable) — CHỈ đơn có chọn kho mới trừ (rollout an toàn, đơn cũ không đổi hành vi). orders gọi **đồng bộ** `inventory.contracts.stock_sales.StockSalesService` trong cùng transaction `update_order_inline` (ACID, trả cảnh báo ngay). Idempotent theo `reference=(order, order_id)`.

**Spec:** `docs/erp-inventory.md` §3.4, §4.4, §5. Nền: Sprint 1.1-1.3 + event bus `apps/common/events.py` (`DomainEvent`, `event_bus.publish_on_commit`).

**Tích hợp orders (từ khảo sát):**
- `OrderStatus.PENDING_PROCESSING = 1` (confirm), `OrderStatus.CANCELLED = 6` (cancel) — `backend/apps/orders/models.py`.
- Hook tại `OrderServiceCommands.update_order_inline(order, data, actor)` — `@transaction.atomic`, `backend/apps/orders/application/services/order_service_commands.py` (~dòng 267-405); `old_status` lưu trước khi set field, `order.save()` ~dòng 333, `push_event` ~dòng 379.
- Items: `order.items.all()` (related_name="items"; `item.product_id`, `item.quantity`).
- Serializer input: `OrderInlineUpdateSerializer` (`order_serializers_impl/kiotviet_landing_serializers.py`); output: `OrderSerializer`.
- View: `update_order_inline` (`order_views_impl/order_action_view.py`).

**Test BE:** docker. **FE:** từ `frontend/`.

---

## Task 1: `Order.fulfillment_warehouse` + serializer (TDD)

**Files:** edit `backend/apps/orders/models.py`; migration; edit `OrderInlineUpdateSerializer` + `OrderSerializer`; test.

- [ ] **Step 1: Model field** — trong `Order` model (`backend/apps/orders/models.py`), thêm:
```python
    fulfillment_warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT,
        related_name="orders", null=True, blank=True,
    )
```

- [ ] **Step 2: Migration**
```bash
cd /home/desi/desi-order
docker inspect desi-web -f '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^[A-Z_]+=' | grep -vE '^PATH=|^LANG=|^GPG|^PYTHON' > /tmp/web.env
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env -v "$(pwd)/backend:/app" -w /app desi-order-web python manage.py makemigrations orders
```
Đọc migration (chỉ add field nullable). Kiểm `migrate orders --plan`.

- [ ] **Step 3: Serializers**
- `OrderInlineUpdateSerializer` (`kiotviet_landing_serializers.py`): thêm `"fulfillment_warehouse"` vào `Meta.fields` (FK PrimaryKeyRelatedField mặc định, cho phép null). Không cần validate đặc biệt (DRF tự validate FK tồn tại).
- `OrderSerializer` (output — tìm file định nghĩa, thường `order_serializers_impl/order_serializers.py`): thêm `"fulfillment_warehouse"` vào fields để FE đọc lại. Nếu có map id→name tiện thì thêm `fulfillment_warehouse_name = CharField(source="fulfillment_warehouse.name", read_only=True, default=None)` (tuỳ chọn).

- [ ] **Step 4: Test** — `backend/apps/orders/tests/test_order_fulfillment_warehouse.py`: PATCH `/api/orders/<id>` set `fulfillment_warehouse` → 200 và GET trả về đúng id. (Dùng auth + factory như các test order hiện có — đọc một test order sẵn để bắt chước cách tạo order + user.)

- [ ] **Step 5: Chạy test + orders suite không hồi quy.** Ruff + commit `feat(orders): add fulfillment_warehouse field`.

---

## Task 2: inventory — event + `StockSalesService` (TDD, thuần inventory)

**Files:** create `backend/apps/inventory/contracts/__init__.py`, `contracts/stock_events.py`, `contracts/stock_sales.py`, `application/services/stock_sales_service.py`, `subscribers.py`; edit `apps.py`; test `tests/test_stock_sales_service.py`.

- [ ] **Step 1: Domain event** — `backend/apps/inventory/contracts/stock_events.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from apps.common.events import DomainEvent


@dataclass(frozen=True)
class StockWentNegative(DomainEvent):
    event_type: ClassVar[str] = "inventory.stock_negative"
    warehouse_id: int = 0
    product_id: int = 0
    balance_after: int = 0
```

- [ ] **Step 2: Test thất bại** — `tests/test_stock_sales_service.py`:
```python
from __future__ import annotations

from django.test import TestCase

from apps.inventory.application.services.stock_sales_service import (
    SaleLine,
    StockSalesService,
)
from apps.inventory.models import MovementType, StockItem, StockMovement
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


class StockSalesServiceTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(code="W1", name="Kho 1")
        self.p1 = Product.objects.create(name="SP A", sku="A-1")
        self.p2 = Product.objects.create(name="SP B", sku="B-1")

    def _seed(self, product, qty):
        from apps.inventory.application.services.stock_command_service import (
            StockCommandService,
        )
        StockCommandService.record_movement(
            warehouse_id=self.wh.id, product_id=product.id, qty_delta=qty,
            movement_type=MovementType.RECEIPT)

    def test_issue_for_order_deducts_and_records_sale(self):
        self._seed(self.p1, 10)
        warnings = StockSalesService.issue_for_order(
            order_id=101, warehouse_id=self.wh.id,
            lines=[SaleLine(product_id=self.p1.id, quantity=4)])
        assert warnings == []
        assert StockItem.objects.get(warehouse=self.wh, product=self.p1).quantity == 6
        mv = StockMovement.objects.get(reference_type="order", reference_id=101,
                                       movement_type=MovementType.SALE)
        assert mv.qty_delta == -4

    def test_issue_negative_returns_warning(self):
        warnings = StockSalesService.issue_for_order(
            order_id=102, warehouse_id=self.wh.id,
            lines=[SaleLine(product_id=self.p1.id, quantity=3)])
        assert len(warnings) == 1
        assert warnings[0].product_id == self.p1.id
        assert warnings[0].balance_after == -3

    def test_issue_is_idempotent(self):
        self._seed(self.p1, 10)
        StockSalesService.issue_for_order(order_id=103, warehouse_id=self.wh.id,
                                          lines=[SaleLine(self.p1.id, 4)])
        StockSalesService.issue_for_order(order_id=103, warehouse_id=self.wh.id,
                                          lines=[SaleLine(self.p1.id, 4)])
        assert StockItem.objects.get(warehouse=self.wh, product=self.p1).quantity == 6
        assert StockMovement.objects.filter(
            reference_type="order", reference_id=103,
            movement_type=MovementType.SALE).count() == 1

    def test_release_reverses_sale(self):
        self._seed(self.p1, 10)
        StockSalesService.issue_for_order(order_id=104, warehouse_id=self.wh.id,
                                          lines=[SaleLine(self.p1.id, 4)])
        StockSalesService.release_for_order(order_id=104)
        assert StockItem.objects.get(warehouse=self.wh, product=self.p1).quantity == 10

    def test_release_is_idempotent(self):
        self._seed(self.p1, 10)
        StockSalesService.issue_for_order(order_id=105, warehouse_id=self.wh.id,
                                          lines=[SaleLine(self.p1.id, 4)])
        StockSalesService.release_for_order(order_id=105)
        StockSalesService.release_for_order(order_id=105)
        assert StockItem.objects.get(warehouse=self.wh, product=self.p1).quantity == 10
```

- [ ] **Step 3: Service** — `backend/apps/inventory/application/services/stock_sales_service.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from apps.common.events import event_bus
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.contracts.stock_events import StockWentNegative
from apps.inventory.models import MovementType, StockMovement

_REFERENCE_TYPE = "order"


@dataclass(frozen=True)
class SaleLine:
    product_id: int
    quantity: int


@dataclass(frozen=True)
class StockWarning:
    product_id: int
    balance_after: int


class StockSalesService:
    @staticmethod
    @transaction.atomic
    def issue_for_order(
        *, order_id: int, warehouse_id: int, lines: list[SaleLine], user_id: int | None = None
    ) -> list[StockWarning]:
        already = StockMovement.objects.filter(
            reference_type=_REFERENCE_TYPE, reference_id=order_id,
            movement_type=MovementType.SALE,
        ).exists()
        if already:
            return []

        warnings: list[StockWarning] = []
        for line in lines:
            if line.quantity <= 0:
                continue
            result = StockCommandService.record_movement(
                warehouse_id=warehouse_id, product_id=line.product_id,
                qty_delta=-line.quantity, movement_type=MovementType.SALE,
                reference_type=_REFERENCE_TYPE, reference_id=order_id, user_id=user_id)
            if result.went_negative:
                warnings.append(StockWarning(line.product_id, result.balance_after))
                event_bus.publish_on_commit(
                    StockWentNegative(
                        warehouse_id=warehouse_id, product_id=line.product_id,
                        balance_after=result.balance_after))
        return warnings

    @staticmethod
    @transaction.atomic
    def release_for_order(*, order_id: int, user_id: int | None = None) -> None:
        already_reversed = StockMovement.objects.filter(
            reference_type=_REFERENCE_TYPE, reference_id=order_id,
            movement_type=MovementType.SALE_REVERSAL,
        ).exists()
        if already_reversed:
            return
        sales = list(
            StockMovement.objects.filter(
                reference_type=_REFERENCE_TYPE, reference_id=order_id,
                movement_type=MovementType.SALE)
        )
        for mv in sales:
            StockCommandService.record_movement(
                warehouse_id=mv.warehouse_id, product_id=mv.product_id,
                qty_delta=-mv.qty_delta, movement_type=MovementType.SALE_REVERSAL,
                reference_type=_REFERENCE_TYPE, reference_id=order_id, user_id=user_id)
```

- [ ] **Step 4: Contract facade** — `backend/apps/inventory/contracts/stock_sales.py` (public boundary cho orders):
```python
"""Public stock-sales contract for other modules (e.g. orders). Import from here,
not from inventory internals."""

from apps.inventory.application.services.stock_sales_service import (  # noqa: F401
    SaleLine,
    StockSalesService,
    StockWarning,
)
```
Tạo `contracts/__init__.py` rỗng.

- [ ] **Step 5: Consumer event** — `backend/apps/inventory/subscribers.py`:
```python
from __future__ import annotations

import logging

from apps.common.events import event_bus
from apps.inventory.contracts.stock_events import StockWentNegative

logger = logging.getLogger(__name__)


def _on_stock_negative(event: StockWentNegative) -> None:
    logger.warning(
        "Tồn âm: kho=%s sp=%s số dư=%s",
        event.warehouse_id, event.product_id, event.balance_after,
    )


def register() -> None:
    event_bus.subscribe(StockWentNegative.event_type, _on_stock_negative)
```
Trong `backend/apps/inventory/apps.py` `InventoryConfig`, thêm:
```python
    def ready(self):
        from apps.inventory import subscribers
        subscribers.register()
```

- [ ] **Step 6: Test** — chạy `apps/inventory/tests/test_stock_sales_service.py` → PASS (5). Chạy cả `apps/inventory` không hồi quy. Ruff + commit `feat(inventory): add stock sales service and negative-stock event`.

---

## Task 3: Tích hợp orders confirm/cancel (TDD — cẩn trọng, không hồi quy)

**Files:** edit `backend/apps/orders/application/services/order_service_commands.py` (hàm `update_order_inline`) + view `order_action_view.py`; test `backend/apps/orders/tests/test_order_stock_integration.py`.

- [ ] **Step 1: ĐỌC hàm `update_order_inline` đầy đủ** trước khi sửa. Xác định: biến `old_status` lưu trạng thái cũ, vị trí sau khi `order` đã có giá trị mới (sau vòng setattr, trước/sau `order.save()`), và lệnh `return` (hiện không trả gì hữu ích).

- [ ] **Step 2: Test thất bại** — `backend/apps/orders/tests/test_order_stock_integration.py`:
- Tạo order (status NEW) có `fulfillment_warehouse` + 1 OrderItem (product, quantity=2); seed tồn 10 ở kho đó.
- Gọi `OrderService.update_order_inline(order, {"status": OrderStatus.PENDING_PROCESSING}, actor)` → tồn còn 8; có movement `sale` reference order.
- Gọi tiếp `update_order_inline(order, {"status": OrderStatus.CANCELLED}, actor)` → tồn về 10 (sale_reversal).
- Order KHÔNG có `fulfillment_warehouse` → confirm KHÔNG sinh movement (giữ hành vi cũ).
(Đọc một test order hiện có để biết cách tạo Order + OrderItem + Channel + actor hợp lệ.)

- [ ] **Step 3: Chèn hook** trong `update_order_inline`, sau khi status mới đã áp vào `order` và trong cùng `@transaction.atomic`, đặt cạnh chỗ so sánh `old_status != order.status` (trước `push_event`):
```python
        stock_warnings: list = []
        if old_status != order.status and order.fulfillment_warehouse_id:
            from apps.inventory.contracts.stock_sales import SaleLine, StockSalesService
            from apps.orders.models import OrderStatus

            actor_id = getattr(actor, "id", None)
            if order.status == OrderStatus.PENDING_PROCESSING:
                lines = [
                    SaleLine(product_id=it.product_id, quantity=it.quantity)
                    for it in order.items.all()
                ]
                warnings = StockSalesService.issue_for_order(
                    order_id=order.id, warehouse_id=order.fulfillment_warehouse_id,
                    lines=lines, user_id=actor_id)
                stock_warnings = [
                    {"product_id": w.product_id, "balance_after": w.balance_after}
                    for w in warnings
                ]
            elif order.status == OrderStatus.CANCELLED:
                StockSalesService.release_for_order(order_id=order.id, user_id=actor_id)
```
Và sửa `return` của hàm để trả `stock_warnings` (mặc định `[]`). Nếu hàm hiện `return None`, đổi thành `return stock_warnings`. **Kiểm các caller khác** của `update_order_inline` — trả về list mới không phá ai (họ bỏ qua giá trị trả về). Báo lại nếu có caller phụ thuộc giá trị cũ.

- [ ] **Step 4: View surface warning** — trong `order_action_view.py` nhánh PATCH:
```python
        warnings = OrderService.update_order_inline(order, serializer.validated_data, request.user)
        data = OrderSerializer(order, context={"can_edit_fn": lambda _x: True}).data
        if warnings:
            data = {**data, "stock_warnings": warnings}
        return Response(data)
```
(Giữ nguyên phần còn lại.)

- [ ] **Step 5: Chạy** test mới + **toàn bộ `apps/orders`** để chắc KHÔNG hồi quy:
```bash
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" -v "$(pwd)/backend:/app" -w /app desi-order-web sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders apps/inventory --reuse-db -q'
```
Lưu ý: baseline orders có thể có vài test đỏ sẵn (xem memory known-failing-baseline-tests) — phân biệt đỏ-sẵn vs đỏ-do-mình. Mục tiêu: không THÊM đỏ.

- [ ] **Step 6: Ruff + commit** `feat(orders): deduct stock on confirm and restore on cancel`.

---

## Task 4: FE — chọn kho xuất trên đơn + cảnh báo tồn âm

**Files:** edit order form/detail (`frontend/src/features/orders/OrderFormDialog.tsx` và/hoặc trang chi tiết đơn) + payload (`order-payload.ts`) + nơi gọi PATCH cập nhật đơn.

> Đọc `OrderFormDialog.tsx`, `order-payload.ts`, và hook cập nhật đơn (tìm nơi PATCH `/api/orders/<id>`), `lib/api/warehouses.ts` (list kho) trước.

- [ ] **Step 1: Chọn kho** — thêm một select "Kho xuất" (fulfillment_warehouse) trên form/chi tiết đơn, nạp danh sách qua `/api/warehouses`. Gửi `fulfillment_warehouse` trong payload cập nhật đơn (`order-payload.ts`).

- [ ] **Step 2: Cảnh báo** — khi PATCH trả về có `stock_warnings` (mảng `{product_id, balance_after}`), hiện toast cảnh báo "Tồn âm sau khi xuất: ...". Dùng `sonner` toast (đã dùng trong app).

- [ ] **Step 3: Verify** — `cd frontend && npx tsc --noEmit` sạch. Chạy test order form nếu có (lưu ý OrderFormDialog test đỏ-sẵn baseline — không THÊM đỏ).

- [ ] **Step 4: Commit** `feat(orders): pick fulfillment warehouse and show stock warnings (FE)`.

---

## Self-Review
- Coverage: field+serializer (Task1 §3.4), sales service issue/release idempotent + event (Task2 §4.4/§5), tích hợp confirm/cancel (Task3 §4.4), FE chọn kho + cảnh báo (Task4 §7).
- An toàn rollout: deduction GATED theo `fulfillment_warehouse_id` → đơn không chọn kho giữ nguyên hành vi cũ (giảm blast radius trên luồng đơn quan trọng).
- ACID: hook trong `@transaction.atomic` của `update_order_inline`; event `publish_on_commit` → consumer chạy sau commit.
- Idempotent: SALE theo reference order (issue 1 lần), SALE_REVERSAL guard (release 1 lần).
- Ranh giới: orders import `apps.inventory.contracts.stock_sales` (public contract), KHÔNG import model inventory. (inventory vẫn FK orders.Product — thoả hiệp §2.2 đã ghi.)
- Rủi ro cao nhất: sửa `update_order_inline` (luồng đơn). Bắt buộc chạy full `apps/orders` để không thêm đỏ; hook tối thiểu, gated, đặt cạnh logic status sẵn có.
