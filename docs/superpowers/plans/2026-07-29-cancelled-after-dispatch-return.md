# Đơn huỷ sau khi đã xuất kho — Kế hoạch triển khai

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đơn bị huỷ sau khi kho đã quét xuất phải nhập hoàn được khi hàng về, và tồn kho chỉ cộng đúng một lần — tại thời điểm hàng thật về kho.

**Architecture:** Bỏ auto-release tồn lúc huỷ đơn (giữ nguyên chuyển sổ sang đơn xuất lại `.NN`), để outstanding của đơn còn nguyên cho tới khi phiếu hoàn ghi sổ. Một bảng hàng đợi nhỏ theo dõi các đơn huỷ đang chờ hàng về; ledger vẫn là nguồn số duy nhất. Dữ liệu huỷ trước ngày deploy (đã auto-release) đi đường tương thích: quét nhập hoàn ghi thêm bút toán thu hồi để tồn ròng không đổi.

**Tech Stack:** Django 5 + DRF, PostgreSQL (bảng `stock_movements` partition theo `created_at`), Celery beat, React + TanStack Query + shadcn/ui, pytest, vitest.

## Global Constraints

- Spec gốc: `docs/superpowers/specs/2026-07-29-cancelled-after-dispatch-return-design.md`. Mọi quyết định nghiệp vụ bám spec, không tự mở rộng.
- `StockMovement` là sổ append-only: không UPDATE, không DELETE. Sai sót sửa bằng bút toán đảo.
- Mọi thao tác đọc-rồi-ghi movement của một đơn phải nằm trong advisory lock `lock_order_stock(order_id)`.
- Thứ tự khoá bất di bất dịch: `Order.select_for_update()` **trước** advisory 61001. Đảo ngược ở một luồng là deadlock với luồng dispatch.
- Bút toán mới của đơn phải mang `reference_type="order"`, `reference_id=order_id`; bút toán phiếu hoàn mang `reference_type="return_entry"`.
- Lint bắt buộc: `ruff check .` theo `backend/pyproject.toml` (CI đỏ nếu vi phạm).
- Tiếng Việt cho mọi chuỗi hiển thị và thông báo lỗi ra người dùng kho.
- Note bút toán là hằng số trong `stock_sales_service.py`, không hardcode chuỗi rải rác.

**Lệnh chạy test backend** (chạy từ thư mục worktree, Git Bash):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_cancel_ret \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Lần chạy đầu thêm `--create-db`; các lần sau bỏ đi cho nhanh. `POSTGRES_DB` phải khác các worktree khác.

**Lệnh chạy test frontend** — tạo junction một lần rồi dùng vitest:

```powershell
New-Item -ItemType Junction -Path "<worktree>\frontend\node_modules" `
         -Target "C:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
npx vitest run <file>
```

Lưu ý: `src/app/__tests__/RequireAuth.test.tsx` đỏ sẵn trên master — không phải do thay đổi của bạn.

---

## File Structure

**Backend**

| File | Trách nhiệm |
|---|---|
| `backend/apps/inventory/application/services/stock_sales_service.py` (sửa) | Thêm hằng số note thu hồi + hàm `pending_cancel_release_by_key` / `has_pending_cancel_release` / `reclaim_auto_release`; `reissue_after_cancel_release` dùng lại hàm chung |
| `backend/apps/inventory/subscribers.py` (sửa) | Huỷ đơn: không release nữa, mở dòng theo dõi; hồi sinh: điều kiện đọc sổ |
| `backend/apps/inventory/models.py` (sửa) | Model `CancelledDispatchWatch` + enum lý do đóng |
| `backend/apps/inventory/migrations/0030_cancelled_dispatch_watch.py` (tạo) | Migration bảng mới |
| `backend/apps/inventory/application/services/awaiting_return_service.py` (tạo) | Vòng đời hàng đợi: `open_for_order` / `settle_if_returned` / `close` / `list_page` |
| `backend/apps/inventory/application/services/return_entry_service.py` (sửa) | Guard cho qua ca huỷ-đã-xuất; ghi bút toán thu hồi cho dữ liệu cũ; gọi `settle_if_returned` |
| `backend/apps/inventory/transport/views/awaiting_return_views.py` (tạo) | 2 endpoint list + close |
| `backend/apps/inventory/urls.py` (sửa) | Đăng ký route |
| `backend/apps/inventory/tasks.py` (sửa) | Celery task cảnh báo Lark đơn chờ quá hạn |
| `backend/config/settings_parts/celery.py` (sửa) | Lịch beat hằng ngày |

**Frontend**

| File | Trách nhiệm |
|---|---|
| `frontend/src/lib/api/awaiting-return.ts` (tạo) | Kiểu dữ liệu + hàm gọi API |
| `frontend/src/features/inventory/awaiting-return-queries.ts` (tạo) | useQuery/useMutation |
| `frontend/src/features/inventory/components/AwaitingReturnTab.tsx` (tạo) | Bảng danh sách + nút Đóng |
| `frontend/src/features/inventory/components/CloseAwaitingReturnDialog.tsx` (tạo) | Dialog chọn lý do |
| `frontend/src/features/inventory/pages/DispatchHistoryPage.tsx` (sửa) | Thêm tab thứ ba |

---

## Task 1: Ngừng auto-release khi huỷ đơn đã xuất

**Files:**
- Modify: `backend/apps/inventory/application/services/stock_sales_service.py:345-405` (thêm hàm chung, refactor `reissue_after_cancel_release`)
- Modify: `backend/apps/inventory/subscribers.py:57-111`
- Test: `backend/apps/inventory/tests/test_cancel_keeps_outstanding.py` (tạo)

**Interfaces:**
- Produces:
  - `AUTO_RELEASE_RECLAIM_NOTE: str` — hằng số note bút toán thu hồi (Task 3 dùng)
  - `pending_cancel_release_by_key(order_id: int) -> dict[tuple[int, int], int]` — `{(warehouse_id, product_id): qty}` phần tồn đã auto-hoàn lúc huỷ mà chưa được bù, chỉ trả phần > 0
  - `has_pending_cancel_release(order_id: int) -> bool`

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_cancel_keeps_outstanding.py`:

```python
"""Huỷ đơn ĐÃ xuất kho: tồn KHÔNG được tự cộng lại — hàng còn ngoài kho.

Tồn chỉ cộng khi hàng thật về (phiếu hoàn ghi sổ). Xem
docs/superpowers/specs/2026-07-29-cancelled-after-dispatch-return-design.md §3.
"""
from __future__ import annotations

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.common.events import event_bus
from apps.customers.models import Customer
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    SaleLine,
    StockSalesService,
    outstanding_by_key,
)
from apps.inventory.models import MovementType, StockItem, StockMovement
from apps.orders.contracts.order_events import OrderStatusChanged
from apps.orders.models import Order, OrderStatus, Product
from apps.warehouses.models import Warehouse


class CancelKeepsOutstandingTests(TestCase):
    def setUp(self):
        from apps.inventory import subscribers

        subscribers.register()
        self.warehouse = Warehouse.objects.create(code="WH-CKO", name="Kho CKO")
        self.product = Product.objects.create(name="SP CKO", sku="SKU-CKO1")
        self.channel = Channel.objects.create(code="ch-cko", name="CKO Channel")
        self.customer = Customer.objects.create(
            full_name="Khach CKO",
            phone_raw="0912345701",
            phone_normalized="0912345701",
            code="CUST-CKO",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            order_code="ORD-CKO-1",
            shipping_code="TRACK-CKO-1",
            name="Khach CKO",
            phone="0912345701",
            status=OrderStatus.IN_DELIVERY,
            fulfillment_warehouse=self.warehouse,
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )

    def _sellable(self) -> int:
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=self.product, state="sellable"
        ).first()
        return item.quantity if item else 0

    def _dispatch(self, qty: int = 2) -> None:
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=qty)],
        )

    def _cancel(self) -> None:
        previous = int(self.order.status)
        Order.objects.filter(pk=self.order.id).update(
            status=OrderStatus.CANCELLED, invoice_status=2
        )
        self.order.refresh_from_db()
        event_bus.publish(
            OrderStatusChanged(
                order_id=self.order.id,
                old_status=previous,
                new_status=int(OrderStatus.CANCELLED),
            )
        )

    def test_cancel_after_dispatch_does_not_credit_stock(self):
        self._dispatch(2)
        self.assertEqual(self._sellable(), 8)

        self._cancel()

        # Hàng vẫn ngoài kho → tồn giữ nguyên 8, không có bút toán đảo nào.
        self.assertEqual(self._sellable(), 8)
        self.assertFalse(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                movement_type=MovementType.SALE_REVERSAL,
            ).exists()
        )
        # Outstanding còn nguyên → phiếu hoàn sau này cộng tồn được.
        self.assertEqual(
            outstanding_by_key(self.order.id),
            {(self.warehouse.id, self.product.id): 2},
        )

    def test_cancel_before_dispatch_is_still_noop(self):
        self._cancel()
        self.assertEqual(self._sellable(), 10)
        self.assertEqual(outstanding_by_key(self.order.id), {})

    def test_revive_without_cancel_release_does_not_deduct_again(self):
        """Đơn huỷ (không release) được hồi sinh: KHÔNG trừ lại — tồn chưa từng được cộng."""
        self._dispatch(2)
        self._cancel()
        self.assertEqual(self._sellable(), 8)

        Order.objects.filter(pk=self.order.id).update(status=OrderStatus.IN_DELIVERY)
        event_bus.publish(
            OrderStatusChanged(
                order_id=self.order.id,
                old_status=int(OrderStatus.CANCELLED),
                new_status=int(OrderStatus.IN_DELIVERY),
            )
        )

        self.assertEqual(self._sellable(), 8)
        self.assertEqual(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                movement_type=MovementType.SALE,
            ).count(),
            1,
        )
```

- [ ] **Step 2: Chạy test để thấy nó đỏ**

```bash
# ... lệnh docker ở Global Constraints ...
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_cancel_keeps_outstanding.py -q --create-db'
```

Kỳ vọng: FAIL — `test_cancel_after_dispatch_does_not_credit_stock` báo tồn 10 thay vì 8 (auto-release vẫn chạy).

- [ ] **Step 3: Thêm hàm chung vào `stock_sales_service.py`**

Thêm hằng số cạnh các note khác (sau `SIBLING_TRANSFER_REISSUE_NOTE`, dòng ~38):

```python
# Bút toán thu hồi phần tồn đã auto-hoàn lúc huỷ, ghi khi hàng của đơn huỷ CŨ
# (trước 2026-07-29) mới về kho. Đường tương thích dữ liệu cũ — đơn huỷ từ sau
# ngày đó không auto-hoàn nữa nên không sinh thêm ca này.
AUTO_RELEASE_RECLAIM_NOTE = "Thu hồi tồn đã hoàn tự động khi huỷ"
```

Thêm `AUTO_RELEASE_RECLAIM_NOTE` vào `ADMIN_SALE_NOTES` (dòng ~44) — nó không phải một lần hàng rời kho:

```python
ADMIN_SALE_NOTES = (
    REVIVE_REISSUE_NOTE,
    RESYNC_NOTE,
    SIBLING_TRANSFER_REISSUE_NOTE,
    AUTO_RELEASE_RECLAIM_NOTE,
)
```

Thêm hai hàm module-level cạnh `outstanding_by_key`:

```python
def pending_cancel_release_by_key(order_id: int) -> dict[tuple[int, int], int]:
    """Phần tồn đã được auto-hoàn lúc huỷ mà CHƯA được bù, theo (kho, sản phẩm).

    Công thức DUY NHẤT cho cả ba chỗ đọc: điều kiện vào nhánh hồi sinh
    (`subscribers._on_order_status_changed`), `reissue_after_cancel_release`, và
    bút toán thu hồi khi hàng về (`reclaim_auto_release`). Hai công thức song
    song sẽ lệch nhau và trừ tồn hai lần.

    Reversal do HUỶ = note rỗng (dữ liệu cũ) hoặc `CANCEL_RELEASE_NOTE`. Phần bù =
    SALE mang `REVIVE_REISSUE_NOTE` (hồi sinh) hoặc `AUTO_RELEASE_RECLAIM_NOTE`
    (hàng về). Reversal của un-dispatch/resync mang note khác nên không lẫn vào.
    """
    rows = (
        StockMovement.objects.filter(
            Q(
                movement_type=MovementType.SALE_REVERSAL,
                note__in=("", CANCEL_RELEASE_NOTE),
            )
            | Q(
                movement_type=MovementType.SALE,
                note__in=(REVIVE_REISSUE_NOTE, AUTO_RELEASE_RECLAIM_NOTE),
            ),
            reference_type=_REFERENCE_TYPE,
            reference_id=order_id,
        )
        .values("warehouse_id", "product_id")
        .annotate(net=Sum("qty_delta"))
    )
    return {
        (row["warehouse_id"], row["product_id"]): row["net"]
        for row in rows
        if row["net"] > 0
    }


def has_pending_cancel_release(order_id: int) -> bool:
    return bool(pending_cancel_release_by_key(order_id))
```

- [ ] **Step 4: Cho `reissue_after_cancel_release` dùng lại hàm chung**

Trong `reissue_after_cancel_release` (dòng ~366), thay khối `rows = (...)` và vòng lặp bằng:

```python
        lock_order_stock(order_id)
        warnings: list[StockWarning] = []
        for (warehouse_id, product_id), pending in sorted(
            pending_cancel_release_by_key(order_id).items()
        ):
            # Đây là bút toán TỰ ĐỘNG trừ tồn, chạy trong on_commit handler (lỗi bị bus
            # nuốt) → phải có vết để đối soát khi số tồn bị nghi ngờ.
            logger.info(
                "Trừ lại tồn đơn %s: kho=%s sp=%s sl=%s",
                order_id, warehouse_id, product_id, pending,
            )
            result = StockCommandService.record_movement(
                warehouse_id=warehouse_id,
                product_id=product_id,
                qty_delta=-pending,
                movement_type=MovementType.SALE,
                state=StockState.SELLABLE,
                reference_type=_REFERENCE_TYPE,
                reference_id=order_id,
                note=REVIVE_REISSUE_NOTE,
                user_id=user_id,
            )
            if result.went_negative:
                warnings.append(StockWarning(product_id, result.balance_after))
```

Giữ nguyên phần publish `StockWentNegative` và `return warnings` phía dưới.

- [ ] **Step 5: Sửa subscriber**

Trong `backend/apps/inventory/subscribers.py`, đổi import (dòng 9) và hai nhánh:

```python
from apps.inventory.application.services.stock_sales_service import (
    has_pending_cancel_release,
)
```

Nhánh huỷ (thay `logger.info(...)` + `StockSalesService.release_for_order(...)` ở dòng 84-87):

```python
        # KHÔNG hoàn tồn ở đây: hàng đã rời kho và đang trên đường về. Tồn chỉ được
        # cộng khi kho quét nhập hoàn (phiếu hoàn ghi sổ) — xem spec
        # docs/superpowers/specs/2026-07-29-cancelled-after-dispatch-return-design.md §3.
        # Outstanding giữ nguyên chính là "khoản nợ" để phiếu hoàn cộng vào.
        logger.info(
            "Huỷ đơn %s → giữ nguyên outstanding, chờ hàng về kho", event.order_id
        )
```

Nhánh hồi sinh (dòng 88): đổi điều kiện `elif` để đọc sổ thay vì suy từ `old_status`:

```python
    elif event.old_status in _RELEASE_STATUSES and has_pending_cancel_release(
        event.order_id
    ):
```

Cập nhật docstring của handler cho khớp: nêu rõ huỷ đơn KHÔNG hoàn tồn nữa, và nhánh hồi sinh chỉ chạy khi sổ còn phần auto-hoàn chưa bù (đơn cũ trước 2026-07-29, hoặc đơn đi qua `transfer_issue_to_sibling`).

- [ ] **Step 6: Chạy test — phải xanh**

```bash
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_cancel_keeps_outstanding.py -q'
```

Kỳ vọng: 3 passed.

- [ ] **Step 7: Chạy hồi quy nhóm test tồn kho**

```bash
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests apps/orders/tests/test_dispatch_cancelled_guard.py -q'
```

Kỳ vọng: các test cũ giả định auto-release sẽ đỏ — **ghi lại danh sách**, sửa ở Task 3 (chúng phụ thuộc luồng nhập hoàn). Test không liên quan phải xanh.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/inventory/application/services/stock_sales_service.py \
        backend/apps/inventory/subscribers.py \
        backend/apps/inventory/tests/test_cancel_keeps_outstanding.py
git commit -m "feat(inventory): keep outstanding stock when cancelling a dispatched order"
```

---

## Task 2: Bảng hàng đợi đơn chờ hàng về

**Files:**
- Modify: `backend/apps/inventory/models.py` (thêm cuối file)
- Create: `backend/apps/inventory/migrations/0030_cancelled_dispatch_watch.py`
- Create: `backend/apps/inventory/application/services/awaiting_return_service.py`
- Modify: `backend/apps/inventory/subscribers.py` (gọi `open_for_order`)
- Test: `backend/apps/inventory/tests/test_awaiting_return_watch.py` (tạo)

**Interfaces:**
- Consumes: `has_pending_cancel_release` (Task 1), `outstanding_by_key`, `order_has_outstanding_stock`
- Produces:
  - `CancelledDispatchWatch` model, `WatchStatus` (`waiting`/`returned`/`closed`), `CloseReason`
  - `AwaitingReturnService.open_for_order(order_id: int) -> None`
  - `AwaitingReturnService.settle_if_returned(order_id: int) -> None`
  - `AwaitingReturnService.close(order_id: int, reason: str, note: str, user_id: int | None) -> CancelledDispatchWatch`

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_awaiting_return_watch.py`:

```python
"""Hàng đợi 'đơn huỷ chờ hàng về': mở khi huỷ, tự đóng khi hàng về, đóng tay khi mất hàng."""
from __future__ import annotations

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.common.events import event_bus
from apps.customers.models import Customer
from apps.inventory.application.services.awaiting_return_service import (
    AwaitingReturnService,
)
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    SaleLine,
    StockSalesService,
)
from apps.inventory.models import (
    CancelledDispatchWatch,
    MovementType,
    StockMovement,
    WatchStatus,
)
from apps.orders.contracts.order_events import OrderStatusChanged
from apps.orders.models import Order, OrderStatus, Product
from apps.users.test_helpers import make_admin
from apps.warehouses.models import Warehouse


class AwaitingReturnWatchTests(TestCase):
    def setUp(self):
        from apps.inventory import subscribers

        subscribers.register()
        self.actor = make_admin("awaiting-return-actor")
        self.warehouse = Warehouse.objects.create(code="WH-AWR", name="Kho AWR")
        self.product = Product.objects.create(name="SP AWR", sku="SKU-AWR1")
        self.channel = Channel.objects.create(code="ch-awr", name="AWR Channel")
        self.customer = Customer.objects.create(
            full_name="Khach AWR",
            phone_raw="0912345702",
            phone_normalized="0912345702",
            code="CUST-AWR",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            order_code="ORD-AWR-1",
            shipping_code="TRACK-AWR-1",
            name="Khach AWR",
            phone="0912345702",
            status=OrderStatus.IN_DELIVERY,
            fulfillment_warehouse=self.warehouse,
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )

    def _dispatch(self, qty: int = 2) -> None:
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=qty)],
        )

    def _cancel(self) -> None:
        previous = int(self.order.status)
        Order.objects.filter(pk=self.order.id).update(status=OrderStatus.CANCELLED)
        self.order.refresh_from_db()
        event_bus.publish(
            OrderStatusChanged(
                order_id=self.order.id,
                old_status=previous,
                new_status=int(OrderStatus.CANCELLED),
            )
        )

    def test_cancel_after_dispatch_opens_a_waiting_row(self):
        self._dispatch(2)
        self._cancel()

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.WAITING)
        self.assertEqual(watch.warehouse_id, self.warehouse.id)
        self.assertIsNotNone(watch.cancelled_at)

    def test_cancel_before_dispatch_opens_nothing(self):
        self._cancel()
        self.assertFalse(CancelledDispatchWatch.objects.exists())

    def test_open_is_idempotent(self):
        self._dispatch(2)
        self._cancel()
        AwaitingReturnService.open_for_order(self.order.id)
        self.assertEqual(CancelledDispatchWatch.objects.count(), 1)

    def test_settle_marks_returned_when_outstanding_is_cleared(self):
        self._dispatch(2)
        self._cancel()
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=2,
            movement_type=MovementType.SALE_REVERSAL,
            reference_type="order",
            reference_id=self.order.id,
            note="test-clear",
        )

        AwaitingReturnService.settle_if_returned(self.order.id)

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.RETURNED)

    def test_settle_keeps_waiting_while_stock_is_still_out(self):
        self._dispatch(2)
        self._cancel()
        AwaitingReturnService.settle_if_returned(self.order.id)
        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.WAITING)

    def test_close_records_the_decision_without_touching_stock(self):
        self._dispatch(2)
        self._cancel()
        before = StockMovement.objects.count()

        watch = AwaitingReturnService.close(
            order_id=self.order.id,
            reason="customer_kept",
            note="Khách xác nhận giữ hàng",
            user_id=self.actor.id,
        )

        self.assertEqual(watch.status, WatchStatus.CLOSED)
        self.assertEqual(watch.closed_reason, "customer_kept")
        self.assertEqual(watch.closed_by_id, self.actor.id)
        self.assertIsNotNone(watch.closed_at)
        # Đóng = ghi nhận quyết định, KHÔNG phải bút toán kho.
        self.assertEqual(StockMovement.objects.count(), before)

    def test_close_requires_note_for_other_reason(self):
        self._dispatch(2)
        self._cancel()
        with self.assertRaises(ValueError):
            AwaitingReturnService.close(
                order_id=self.order.id, reason="other", note="", user_id=self.actor.id
            )

    def test_goods_arriving_after_close_reopens_as_returned(self):
        self._dispatch(2)
        self._cancel()
        AwaitingReturnService.close(
            order_id=self.order.id,
            reason="lost_in_transit",
            note="",
            user_id=self.actor.id,
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=2,
            movement_type=MovementType.SALE_REVERSAL,
            reference_type="order",
            reference_id=self.order.id,
            note="test-clear",
        )

        AwaitingReturnService.settle_if_returned(self.order.id)

        watch = CancelledDispatchWatch.objects.get(order_id=self.order.id)
        self.assertEqual(watch.status, WatchStatus.RETURNED)
```

- [ ] **Step 2: Chạy test để thấy nó đỏ**

```bash
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_awaiting_return_watch.py -q --create-db'
```

Kỳ vọng: ERROR — `ImportError: cannot import name 'CancelledDispatchWatch'`.

- [ ] **Step 3: Thêm model**

Cuối `backend/apps/inventory/models.py`:

```python
class WatchStatus(models.TextChoices):
    WAITING = "waiting", "Chờ hàng về"
    RETURNED = "returned", "Hàng đã về"
    CLOSED = "closed", "Đã đóng"


class CloseReason(models.TextChoices):
    CUSTOMER_KEPT = "customer_kept", "Khách đã nhận và giữ hàng"
    LOST_IN_TRANSIT = "lost_in_transit", "Thất lạc, đơn vị vận chuyển đền"
    DISPOSED_OUTSIDE = "disposed_outside", "Huỷ tại chỗ, không trả về"
    OTHER = "other", "Khác"


class CancelledDispatchWatch(models.Model):
    """Hàng đợi công việc: đơn đã huỷ nhưng hàng còn ngoài kho.

    KHÔNG phải nguồn số. Số lượng còn nợ luôn đọc từ sổ (`outstanding_by_key`);
    bảng này chỉ trả lời "đơn nào cần theo dõi" và giữ dấu vết quyết định đóng.
    Có bảng vì dựng danh sách thuần từ ledger phải gộp `stock_movements` trên
    toàn bộ đơn mỗi lần mở tab.
    """

    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="cancelled_dispatch_watch"
    )
    warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT, related_name="+"
    )
    cancelled_at = models.DateTimeField()
    status = models.CharField(
        max_length=10, choices=WatchStatus.choices, default=WatchStatus.WAITING
    )
    closed_reason = models.CharField(
        max_length=20, choices=CloseReason.choices, blank=True, default=""
    )
    closed_note = models.TextField(blank=True, default="")
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cancelled_dispatch_watches"
        ordering = ["cancelled_at", "id"]
        indexes = [
            models.Index(
                fields=["status", "cancelled_at"], name="cancelwatch_status_idx"
            ),
            models.Index(fields=["warehouse"], name="cancelwatch_wh_idx"),
        ]

    def __str__(self):
        return f"watch order#{self.order_id} ({self.status})"
```

- [ ] **Step 4: Sinh migration**

```bash
docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_cancel_ret \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py makemigrations inventory --name cancelled_dispatch_watch
```

Kiểm tra file sinh ra là `0030_cancelled_dispatch_watch.py` và chỉ chứa `CreateModel` + `AddIndex` — không đụng bảng khác.

- [ ] **Step 5: Viết service**

Tạo `backend/apps/inventory/application/services/awaiting_return_service.py`:

```python
"""Vòng đời hàng đợi 'đơn huỷ chờ hàng về'.

Ledger là nguồn số; service này chỉ quản dòng theo dõi. Xem spec
docs/superpowers/specs/2026-07-29-cancelled-after-dispatch-return-design.md §5.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.inventory.application.services.stock_sales_service import (
    order_has_outstanding_stock,
)
from apps.inventory.models import CancelledDispatchWatch, CloseReason, WatchStatus

logger = logging.getLogger(__name__)


class AwaitingReturnService:
    @staticmethod
    @transaction.atomic
    def open_for_order(order_id: int) -> None:
        """Mở dòng theo dõi cho đơn vừa huỷ mà hàng còn ngoài kho. Idempotent."""
        from apps.orders.models import Order  # local import: giữ ranh giới module

        if not order_has_outstanding_stock(order_id):
            return
        order = (
            Order.objects.filter(pk=order_id)
            .values("fulfillment_warehouse_id")
            .first()
        )
        if order is None or not order["fulfillment_warehouse_id"]:
            logger.warning("Đơn %s huỷ khi đã xuất nhưng không có kho giao", order_id)
            return
        CancelledDispatchWatch.objects.get_or_create(
            order_id=order_id,
            defaults={
                "warehouse_id": order["fulfillment_warehouse_id"],
                "cancelled_at": timezone.now(),
                "status": WatchStatus.WAITING,
            },
        )

    @staticmethod
    @transaction.atomic
    def settle_if_returned(order_id: int) -> None:
        """Hàng đã về hết (outstanding = 0) → đóng dòng theo dõi.

        Chạy cả với dòng đã `closed`: hàng về muộn sau khi ai đó đóng vì tưởng mất
        thì trạng thái phải phản ánh sự thật, không cần nút mở lại.
        """
        watch = (
            CancelledDispatchWatch.objects.select_for_update()
            .filter(order_id=order_id)
            .exclude(status=WatchStatus.RETURNED)
            .first()
        )
        if watch is None or order_has_outstanding_stock(order_id):
            return
        watch.status = WatchStatus.RETURNED
        watch.save(update_fields=["status", "updated_at"])
        logger.info("Đơn %s: hàng đã về đủ, đóng dòng theo dõi", order_id)

    @staticmethod
    @transaction.atomic
    def close(
        *, order_id: int, reason: str, note: str = "", user_id: int | None = None
    ) -> CancelledDispatchWatch:
        """Đóng sổ đơn treo vì hàng không bao giờ về.

        KHÔNG ghi bút toán kho: hàng không quay về nghĩa là tồn hiện tại đã đúng.
        """
        if reason not in CloseReason.values:
            raise ValueError("Lý do đóng không hợp lệ.")
        if reason == CloseReason.OTHER and not note.strip():
            raise ValueError("Chọn lý do 'Khác' thì phải ghi chú.")
        watch = (
            CancelledDispatchWatch.objects.select_for_update()
            .filter(order_id=order_id)
            .first()
        )
        if watch is None:
            raise ValueError("Đơn không nằm trong danh sách chờ hàng về.")
        if watch.status == WatchStatus.RETURNED:
            raise ValueError("Hàng của đơn này đã về kho — không cần đóng.")
        watch.status = WatchStatus.CLOSED
        watch.closed_reason = reason
        watch.closed_note = note.strip()
        watch.closed_by_id = user_id
        watch.closed_at = timezone.now()
        watch.save(
            update_fields=[
                "status", "closed_reason", "closed_note",
                "closed_by", "closed_at", "updated_at",
            ]
        )
        return watch
```

- [ ] **Step 6: Gọi `open_for_order` trong subscriber**

Trong nhánh huỷ của `_on_order_status_changed` (Task 1 Step 5), sau dòng `logger.info("Huỷ đơn %s → giữ nguyên outstanding...")`:

```python
        AwaitingReturnService.open_for_order(event.order_id)
```

Import cục bộ trong hàm để tránh vòng import:

```python
    from apps.inventory.application.services.awaiting_return_service import (
        AwaitingReturnService,
    )
```

- [ ] **Step 7: Chạy test — phải xanh**

```bash
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_awaiting_return_watch.py -q --create-db'
```

Kỳ vọng: 8 passed.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/inventory/models.py \
        backend/apps/inventory/migrations/0030_cancelled_dispatch_watch.py \
        backend/apps/inventory/application/services/awaiting_return_service.py \
        backend/apps/inventory/subscribers.py \
        backend/apps/inventory/tests/test_awaiting_return_watch.py
git commit -m "feat(inventory): track cancelled orders awaiting goods return"
```

---

## Task 3: Nhập hoàn cho đơn đã huỷ + đường tương thích dữ liệu cũ

**Files:**
- Modify: `backend/apps/inventory/application/services/stock_sales_service.py` (thêm `reclaim_auto_release`)
- Modify: `backend/apps/inventory/application/services/return_entry_service.py:109-159` (guard), `:292-304` (post_entry)
- Modify: `backend/apps/inventory/tests/test_return_cancelled_order_no_double_credit.py` (đổi kỳ vọng theo luật mới)
- Test: `backend/apps/inventory/tests/test_return_after_cancel_legacy.py` (tạo)

**Interfaces:**
- Consumes: `pending_cancel_release_by_key`, `AUTO_RELEASE_RECLAIM_NOTE` (Task 1); `AwaitingReturnService.settle_if_returned` (Task 2)
- Produces: `StockSalesService.reclaim_auto_release(order_id: int, needed_by_product: dict[int, int], user_id: int | None = None) -> None`

- [ ] **Step 1: Sửa test cũ cho khớp luật mới**

Trong `backend/apps/inventory/tests/test_return_cancelled_order_no_double_credit.py`, thay
`test_dispatched_then_cancelled_then_return_scan_does_not_double_credit` bằng:

```python
    def test_dispatched_then_cancelled_then_return_scan_credits_once(self):
        """Đã xuất kho → huỷ (KHÔNG auto-hoàn) → quét nhập hoàn: tồn cộng đúng một lần."""
        self._dispatch(2)
        self.assertEqual(self._sellable(), 8)

        self._cancel()
        self.assertEqual(self._sellable(), 8)  # hàng còn ngoài kho

        entry = self._scan_return(2)

        self.assertEqual(entry.status, "posted")
        self.assertEqual(self._sellable(), 10)  # về đủ, không phồng thành 12

    def test_posting_return_does_not_trigger_revive_deduction(self):
        """Bẫy status 6→5: ghi sổ phiếu hoàn đặt đơn = RETURNED, subscriber KHÔNG
        được hiểu đó là 'đơn huỷ hồi sinh' và trừ lại tồn (spec §4)."""
        self._dispatch(2)
        self._cancel()
        self._scan_return(2)

        self.assertEqual(self._sellable(), 10)
        # Không có bút toán SALE thứ hai nào (trừ lại khi hồi sinh / resync).
        self.assertEqual(
            StockMovement.objects.filter(
                reference_type="order",
                reference_id=self.order.id,
                movement_type=MovementType.SALE,
            ).count(),
            1,
        )

    def test_return_scan_twice_is_still_blocked(self):
        """Không hồi quy: quét nhập hoàn lần hai vẫn bị chặn (cộng tồn hai lần)."""
        self._dispatch(2)
        self._cancel()
        self._scan_return(2)

        with self.assertRaises(ValueError):
            ReturnEntryService.create_and_post_entry(
                warehouse_id=self.warehouse.id,
                lines=[
                    {
                        "product_id": self.product.id,
                        "quantity": 2,
                        "return_type": "delivery_returned",
                    }
                ],
                source_order_id=self.order.id,
                shipping_code="TRACK-CNC-1-B",
            )
        self.assertEqual(self._sellable(), 10)
```

Bổ sung import ở đầu file (file hiện chỉ import `MovementType, ReturnEntry, ReturnNote, StockItem`):

```python
from apps.inventory.models import (
    MovementType,
    ReturnEntry,
    ReturnNote,
    StockItem,
    StockMovement,
)
```

Cập nhật docstring đầu file: chuỗi thật giờ là *xuất → huỷ (giữ outstanding) → hàng về → quét nhập hoàn cộng tồn một lần*. Giữ nguyên hai test còn lại (`cancelled_before_dispatch`, `live_order`).

- [ ] **Step 2: Viết test đường tương thích dữ liệu cũ**

Tạo `backend/apps/inventory/tests/test_return_after_cancel_legacy.py`:

```python
"""Đơn huỷ TRƯỚC 2026-07-29 (đã auto-hoàn tồn) mà hàng nay mới về.

Quét nhập hoàn vẫn nhận, nhưng ghi kèm bút toán thu hồi để tồn ròng không đổi —
phần tồn đó đã được cộng từ lúc huỷ. Xem spec §6.
"""
from __future__ import annotations

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.return_entry_service import ReturnEntryService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    AUTO_RELEASE_RECLAIM_NOTE,
    CANCEL_RELEASE_NOTE,
    SaleLine,
    StockSalesService,
    has_pending_cancel_release,
)
from apps.inventory.models import MovementType, StockItem, StockMovement
from apps.orders.models import Order, OrderStatus, Product
from apps.warehouses.models import Warehouse


class ReturnAfterLegacyCancelTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(code="WH-LEG", name="Kho LEG")
        self.product = Product.objects.create(name="SP LEG", sku="SKU-LEG1")
        self.channel = Channel.objects.create(code="ch-leg", name="LEG Channel")
        self.customer = Customer.objects.create(
            full_name="Khach LEG",
            phone_raw="0912345703",
            phone_normalized="0912345703",
            code="CUST-LEG",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            order_code="ORD-LEG-1",
            shipping_code="TRACK-LEG-1",
            name="Khach LEG",
            phone="0912345703",
            status=OrderStatus.CANCELLED,
            fulfillment_warehouse=self.warehouse,
        )
        StockCommandService.record_movement(
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            qty_delta=10,
            movement_type=MovementType.RECEIPT,
        )
        # Dựng đúng dấu vết dữ liệu cũ: xuất kho rồi auto-hoàn khi huỷ.
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=2)],
        )
        StockSalesService.release_for_order(
            order_id=self.order.id, note=CANCEL_RELEASE_NOTE
        )

    def _sellable(self) -> int:
        item = StockItem.objects.filter(
            warehouse=self.warehouse, product=self.product, state="sellable"
        ).first()
        return item.quantity if item else 0

    def test_legacy_cancelled_order_accepts_return_without_double_credit(self):
        self.assertEqual(self._sellable(), 10)  # đã được cộng từ lúc huỷ

        entry = ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": 2,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=self.order.id,
            shipping_code=self.order.shipping_code,
        )

        self.assertEqual(entry.status, "posted")
        # Hàng về được ghi nhận, nhưng tồn KHÔNG phồng: return_in +2 và thu hồi -2.
        self.assertEqual(self._sellable(), 10)
        reclaim = StockMovement.objects.filter(
            reference_type="order",
            reference_id=self.order.id,
            movement_type=MovementType.SALE,
            note=AUTO_RELEASE_RECLAIM_NOTE,
        )
        self.assertEqual(reclaim.count(), 1)
        self.assertEqual(reclaim.first().qty_delta, -2)
        self.assertTrue(
            StockMovement.objects.filter(
                reference_type="return_entry",
                reference_id=entry.id,
                movement_type=MovementType.RETURN_IN,
                qty_delta=2,
            ).exists()
        )

    def test_reclaim_clears_pending_so_revive_does_not_deduct_twice(self):
        ReturnEntryService.create_and_post_entry(
            warehouse_id=self.warehouse.id,
            lines=[
                {
                    "product_id": self.product.id,
                    "quantity": 2,
                    "return_type": "delivery_returned",
                }
            ],
            source_order_id=self.order.id,
            shipping_code=self.order.shipping_code,
        )
        # Phần auto-hoàn đã được thu hồi → hồi sinh không còn gì để trừ lại.
        self.assertFalse(has_pending_cancel_release(self.order.id))
        StockSalesService.reissue_after_cancel_release(order_id=self.order.id)
        self.assertEqual(self._sellable(), 10)
```

- [ ] **Step 3: Chạy hai file test — phải đỏ**

```bash
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest \
  apps/inventory/tests/test_return_cancelled_order_no_double_credit.py \
  apps/inventory/tests/test_return_after_cancel_legacy.py -q'
```

Kỳ vọng: FAIL — guard vẫn chặn với "đã hủy nên tồn kho đã được tự động hoàn lại".

- [ ] **Step 4: Thêm `reclaim_auto_release` vào `StockSalesService`**

```python
    @staticmethod
    @transaction.atomic
    def reclaim_auto_release(
        *,
        order_id: int,
        needed_by_product: dict[int, int],
        user_id: int | None = None,
    ) -> None:
        """Thu hồi phần tồn đã auto-hoàn lúc huỷ, đúng bằng lượng hàng vừa về.

        CHỈ chạm dữ liệu cũ (đơn huỷ trước 2026-07-29 còn mang reversal do huỷ).
        Đơn huỷ theo luật mới không có phần dư nào nên hàm này là no-op.

        Ghi SALE (âm) chứ không sửa bút toán cũ: sổ append-only.
        """
        lock_order_stock(order_id)
        pending = pending_cancel_release_by_key(order_id)
        for (warehouse_id, product_id), available in sorted(pending.items()):
            needed = needed_by_product.get(product_id, 0)
            if needed <= 0:
                continue
            qty = min(needed, available)
            needed_by_product[product_id] = needed - qty
            StockCommandService.record_movement(
                warehouse_id=warehouse_id,
                product_id=product_id,
                qty_delta=-qty,
                movement_type=MovementType.SALE,
                state=StockState.SELLABLE,
                reference_type=_REFERENCE_TYPE,
                reference_id=order_id,
                note=AUTO_RELEASE_RECLAIM_NOTE,
                user_id=user_id,
            )
```

- [ ] **Step 5: Sửa guard trong `return_entry_service.py`**

Trong `_guard_double_credit`, phần tính `deducted` (dòng ~137) cộng thêm phần đã auto-hoàn — nó cũng là lượng hàng của đơn đang ở ngoài kho:

```python
        deducted: dict[int, int] = defaultdict(int)
        for (_wh, product_id), qty in outstanding_by_key(entry.source_order_id).items():
            deducted[product_id] += qty
        # Đơn huỷ CŨ (trước 2026-07-29) đã được auto-hoàn tồn: outstanding = 0 nhưng
        # hàng vẫn từng rời kho và nay mới về. Tính vào hạn mức để phiếu hoàn được
        # nhận; `post_entry` ghi bút toán thu hồi nên tồn không cộng hai lần.
        for (_wh, product_id), qty in pending_cancel_release_by_key(
            entry.source_order_id
        ).items():
            deducted[product_id] += qty
```

Thêm import ở đầu file cạnh các import từ `stock_sales_service`:

```python
from apps.inventory.application.services.stock_sales_service import (
    pending_cancel_release_by_key,
)
```

Xoá `_cancelled_source_order_code` / `_cancelled_message` và hai chỗ gọi chúng ở nhánh
`line.quantity > available` (dòng ~148-154): thông điệp "đơn huỷ nên tồn đã tự hoàn" không
còn đúng với bất kỳ ca nào — đơn huỷ mới thì hạn mức còn nguyên, đơn cũ thì đã được cộng vào
hạn mức ở trên. Giữ nguyên nhánh `not order_has_sales(...)` (đơn huỷ chưa từng xuất → chặn
tồn ảo), nhưng đổi thông điệp cho đúng nghĩa:

```python
        if not order_has_sales(entry.source_order_id):
            cancelled_code = ReturnEntryService._never_dispatched_order_code(
                entry.source_order_id
            )
            if cancelled_code:
                raise ValueError(
                    f"Đơn {cancelled_code} đã huỷ khi chưa xuất kho — hàng chưa bao giờ "
                    "rời kho nên không có gì để nhập hoàn."
                )
            return
```

Đổi tên `_cancelled_source_order_code` thành `_never_dispatched_order_code` và giữ nguyên thân
hàm (vẫn trả mã đơn nếu đơn đang huỷ, ngược lại `None`).

- [ ] **Step 6: Ghi bút toán thu hồi + settle trong `post_entry`**

Trong `post_entry`, ngay **trước** vòng lặp ghi `RETURN_IN` (dòng ~293):

```python
        if entry.source_order_id:
            needed = defaultdict(int)
            for line in lines:
                needed[line.product_id] += line.quantity
            StockSalesService.reclaim_auto_release(
                order_id=entry.source_order_id,
                needed_by_product=needed,
                user_id=user_id,
            )
```

Sau `entry.save(update_fields=update_fields)` (dòng ~318), trước `return entry`:

```python
        if entry.source_order_id:
            AwaitingReturnService.settle_if_returned(entry.source_order_id)
```

Import `StockSalesService` và `AwaitingReturnService` ở đầu file (cùng nhóm import service).

- [ ] **Step 7: Chạy test — phải xanh**

```bash
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest \
  apps/inventory/tests/test_return_cancelled_order_no_double_credit.py \
  apps/inventory/tests/test_return_after_cancel_legacy.py \
  apps/inventory/tests/test_cancel_keeps_outstanding.py \
  apps/inventory/tests/test_awaiting_return_watch.py -q'
```

Kỳ vọng: all passed.

- [ ] **Step 8: Chạy toàn bộ test inventory + orders để bắt hồi quy**

```bash
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory apps/orders -q'
```

Mọi test đỏ còn lại phải được sửa **kỳ vọng** (không sửa luật mới) và giải thích trong commit message. Nếu một test đỏ vì luật mới thật sự sai, dừng lại và báo — đừng sửa test cho xanh.

- [ ] **Step 9: Lint + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/inventory
git commit -m "feat(inventory): allow return scan for cancelled dispatched orders"
```

---

## Task 4: API danh sách + đóng đơn

**Files:**
- Create: `backend/apps/inventory/transport/views/awaiting_return_views.py`
- Modify: `backend/apps/inventory/urls.py:59+` (thêm 2 route), phần import ở đầu file
- Modify: `backend/apps/inventory/application/services/awaiting_return_service.py` (thêm `list_page`)
- Test: `backend/apps/inventory/tests/test_awaiting_return_api.py` (tạo)

**Interfaces:**
- Consumes: `AwaitingReturnService.close` (Task 2), `outstanding_by_key`
- Produces:
  - `GET /api/inventory/dispatch-history/awaiting-return` → `{"count": int, "results": [...]}`; mỗi phần tử: `order_id`, `order_code`, `invoice_code`, `shipping_code`, `customer_name`, `warehouse_id`, `warehouse_name`, `dispatched_at`, `cancelled_at`, `days_waiting`, `status`, `closed_reason`, `closed_note`, `lines: [{product_id, sku, qty}]`
  - `POST /api/inventory/dispatch-history/awaiting-return/<order_id>/close` body `{"reason": str, "note": str}` → 200 `{"status": "closed"}`; 400 khi lý do sai/thiếu ghi chú; 404 khi đơn không có trong hàng đợi

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_awaiting_return_api.py`:

```python
"""API tab 'Chờ hàng hoàn về': danh sách đọc số từ sổ, đóng đơn cần quyền returns:approve."""
from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_sales_service import (
    SaleLine,
    StockSalesService,
)
from apps.inventory.models import (
    CancelledDispatchWatch,
    MovementType,
    WatchStatus,
)
from apps.orders.models import Order, OrderStatus, Product
from apps.users.test_helpers import make_admin
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db

LIST_URL = "/api/inventory/dispatch-history/awaiting-return"


@pytest.fixture
def setup_watch():
    warehouse = Warehouse.objects.create(code="WH-API", name="Kho API")
    product = Product.objects.create(name="SP API", sku="SKU-API1")
    channel = Channel.objects.create(code="ch-api", name="API Channel")
    customer = Customer.objects.create(
        full_name="Khach API",
        phone_raw="0912345704",
        phone_normalized="0912345704",
        code="CUST-API",
    )
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        order_code="ORD-API-1",
        invoice_code="HD-API-1",
        shipping_code="TRACK-API-1",
        name="Khach API",
        phone="0912345704",
        status=OrderStatus.CANCELLED,
        fulfillment_warehouse=warehouse,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=10,
        movement_type=MovementType.RECEIPT,
    )
    StockSalesService.issue_for_order(
        order_id=order.id,
        warehouse_id=warehouse.id,
        lines=[SaleLine(product_id=product.id, quantity=2)],
    )
    from django.utils import timezone

    CancelledDispatchWatch.objects.create(
        order_id=order.id,
        warehouse_id=warehouse.id,
        cancelled_at=timezone.now(),
        status=WatchStatus.WAITING,
    )
    return {"order": order, "product": product, "warehouse": warehouse}


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_list_returns_row_with_quantity_from_ledger(setup_watch):
    admin = make_admin("awaiting-api-admin")
    response = _client(admin).get(LIST_URL)

    assert response.status_code == 200
    assert response.data["count"] == 1
    row = response.data["results"][0]
    assert row["order_id"] == setup_watch["order"].id
    assert row["shipping_code"] == "TRACK-API-1"
    assert row["status"] == "waiting"
    assert row["lines"] == [
        {
            "product_id": setup_watch["product"].id,
            "sku": "SKU-API1",
            "qty": 2,
        }
    ]
    assert row["days_waiting"] == 0


def test_list_filters_by_warehouse(setup_watch):
    admin = make_admin("awaiting-api-admin-2")
    other = Warehouse.objects.create(code="WH-API-2", name="Kho khác")
    response = _client(admin).get(f"{LIST_URL}?warehouse_id={other.id}")
    assert response.data["count"] == 0


def test_close_marks_row_closed(setup_watch):
    admin = make_admin("awaiting-api-admin-3")
    order_id = setup_watch["order"].id
    response = _client(admin).post(
        f"{LIST_URL}/{order_id}/close",
        {"reason": "customer_kept", "note": "Khách giữ hàng"},
        format="json",
    )

    assert response.status_code == 200
    watch = CancelledDispatchWatch.objects.get(order_id=order_id)
    assert watch.status == WatchStatus.CLOSED
    assert watch.closed_reason == "customer_kept"


def test_close_rejects_other_reason_without_note(setup_watch):
    admin = make_admin("awaiting-api-admin-4")
    response = _client(admin).post(
        f"{LIST_URL}/{setup_watch['order'].id}/close",
        {"reason": "other", "note": ""},
        format="json",
    )
    assert response.status_code == 400


def test_close_unknown_order_is_404(setup_watch):
    admin = make_admin("awaiting-api-admin-5")
    response = _client(admin).post(
        f"{LIST_URL}/999999/close",
        {"reason": "customer_kept", "note": ""},
        format="json",
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Chạy test — phải đỏ**

```bash
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_awaiting_return_api.py -q'
```

Kỳ vọng: 404 ở mọi test (route chưa tồn tại).

- [ ] **Step 3: Thêm `list_page` vào service**

```python
    @staticmethod
    def list_page(
        *,
        warehouse_id: int | None = None,
        status: str = WatchStatus.WAITING,
        min_days: int = 0,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[dict]]:
        """Trang danh sách. SL còn nợ đọc từ sổ — một truy vấn gộp cho cả trang."""
        from apps.orders.models import Product

        qs = CancelledDispatchWatch.objects.select_related(
            "order", "warehouse"
        ).order_by("cancelled_at", "id")
        if status:
            qs = qs.filter(status=status)
        if warehouse_id:
            qs = qs.filter(warehouse_id=warehouse_id)
        if min_days > 0:
            qs = qs.filter(
                cancelled_at__lte=timezone.now() - timedelta(days=min_days)
            )
        total = qs.count()
        watches = list(qs[offset : offset + limit])
        if not watches:
            return total, []

        # Gộp outstanding của TẤT CẢ đơn trong trang bằng một truy vấn (không N+1).
        order_ids = [w.order_id for w in watches]
        rows = (
            StockMovement.objects.filter(
                reference_type="order",
                reference_id__in=order_ids,
                movement_type__in=(MovementType.SALE, MovementType.SALE_REVERSAL),
            )
            .values("reference_id", "product_id")
            .annotate(net=Sum("qty_delta"))
        )
        by_order: dict[int, list[tuple[int, int]]] = defaultdict(list)
        product_ids = set()
        for row in rows:
            if row["net"] >= 0:
                continue
            by_order[row["reference_id"]].append((row["product_id"], -row["net"]))
            product_ids.add(row["product_id"])
        skus = dict(
            Product.objects.filter(id__in=product_ids).values_list("id", "sku")
        )

        now = timezone.now()
        results = []
        for watch in watches:
            results.append(
                {
                    "order_id": watch.order_id,
                    "order_code": watch.order.order_code,
                    "invoice_code": watch.order.invoice_code,
                    "shipping_code": watch.order.shipping_code,
                    "customer_name": watch.order.name,
                    "warehouse_id": watch.warehouse_id,
                    "warehouse_name": watch.warehouse.name,
                    "cancelled_at": watch.cancelled_at,
                    "days_waiting": (now - watch.cancelled_at).days,
                    "status": watch.status,
                    "closed_reason": watch.closed_reason,
                    "closed_note": watch.closed_note,
                    "lines": [
                        {
                            "product_id": pid,
                            "sku": skus.get(pid, str(pid)),
                            "qty": qty,
                        }
                        for pid, qty in sorted(by_order.get(watch.order_id, []))
                    ],
                }
            )
        return total, results
```

Thêm import đầu file: `from collections import defaultdict`, `from datetime import timedelta`,
`from django.db.models import Sum`, `from apps.inventory.models import MovementType, StockMovement`.

- [ ] **Step 4: Viết view**

Tạo `backend/apps/inventory/transport/views/awaiting_return_views.py`:

```python
"""Endpoint tab 'Chờ hàng hoàn về' — đơn đã huỷ mà hàng còn ngoài kho."""
from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import parse_required_pagination
from apps.inventory.application.services.awaiting_return_service import (
    AwaitingReturnService,
)
from apps.users.permissions import require_permission, require_section


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@extend_schema(tags=["inventory"])
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("returns")])
@never_cache
def awaiting_return_list(request):
    pagination, error = parse_required_pagination(request)
    if error is not None:
        return error
    total, results = AwaitingReturnService.list_page(
        warehouse_id=_int_or_none(request.query_params.get("warehouse_id")),
        status=(request.query_params.get("status") or "waiting").strip(),
        min_days=_int_or_none(request.query_params.get("min_days")) or 0,
        limit=pagination["limit"],
        offset=pagination["offset"],
    )
    return Response({"count": total, "results": results})


@extend_schema(tags=["inventory"])
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("returns:approve")])
def awaiting_return_close(request, order_id: int):
    try:
        AwaitingReturnService.close(
            order_id=order_id,
            reason=(request.data.get("reason") or "").strip(),
            note=request.data.get("note") or "",
            user_id=request.user.id,
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "không nằm trong danh sách" in message
            else status.HTTP_400_BAD_REQUEST
        )
        return Response({"detail": message}, status=code)
    return Response({"status": "closed"})
```

Kiểm tra chữ ký thật của `parse_required_pagination` trong `backend/apps/common/pagination.py`
trước khi dùng — nếu nó trả về kiểu khác (object thay vì dict), sửa cho khớp thay vì đoán.

- [ ] **Step 5: Đăng ký route**

Trong `backend/apps/inventory/urls.py`, thêm import và hai `path` cạnh nhóm `dispatch-history/*`:

```python
from apps.inventory.transport.views.awaiting_return_views import (
    awaiting_return_close,
    awaiting_return_list,
)
```

```python
    path(
        "dispatch-history/awaiting-return",
        awaiting_return_list,
        name="inventory-awaiting-return",
    ),
    path(
        "dispatch-history/awaiting-return/<int:order_id>/close",
        awaiting_return_close,
        name="inventory-awaiting-return-close",
    ),
```

- [ ] **Step 6: Chạy test — phải xanh**

```bash
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_awaiting_return_api.py -q'
```

Kỳ vọng: 5 passed.

- [ ] **Step 7: Lint + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/inventory
git commit -m "feat(inventory): awaiting-return list and close endpoints"
```

---

## Task 5: Tab "Chờ hàng hoàn về" trên giao diện

**Files:**
- Create: `frontend/src/lib/api/awaiting-return.ts`
- Create: `frontend/src/features/inventory/awaiting-return-queries.ts`
- Create: `frontend/src/features/inventory/components/AwaitingReturnTab.tsx`
- Create: `frontend/src/features/inventory/components/CloseAwaitingReturnDialog.tsx`
- Modify: `frontend/src/features/inventory/pages/DispatchHistoryPage.tsx:104-129`
- Test: `frontend/src/features/inventory/__tests__/awaiting-return-tab.test.tsx` (tạo)

**Interfaces:**
- Consumes: API ở Task 4
- Produces: `AwaitingReturnRow` type, `useAwaitingReturn(filters)`, `useCloseAwaitingReturn()`

- [ ] **Step 1: Chuẩn bị node_modules cho worktree (một lần)**

```powershell
New-Item -ItemType Junction -Path "<worktree>\frontend\node_modules" `
         -Target "C:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```

- [ ] **Step 2: Viết test thất bại**

Tạo `frontend/src/features/inventory/__tests__/awaiting-return-tab.test.tsx`. Bám đúng cách
mock `fetchJson` và bọc `QueryClientProvider` của các test cùng thư mục (đọc
`flow-update-pending-note.test.tsx` làm mẫu trước khi viết):

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AwaitingReturnTab } from "../components/AwaitingReturnTab";
import { renderWithClient } from "./test-utils"; // dùng helper sẵn có của thư mục

const ROW = {
  order_id: 1,
  order_code: "ORD-1",
  invoice_code: "HD-1",
  shipping_code: "TRACK-1",
  customer_name: "Khách A",
  warehouse_id: 1,
  warehouse_name: "Kho 1",
  cancelled_at: "2026-07-20T02:00:00Z",
  days_waiting: 9,
  status: "waiting",
  closed_reason: "",
  closed_note: "",
  lines: [{ product_id: 5, sku: "SKU-1", qty: 2 }],
};

describe("AwaitingReturnTab", () => {
  it("hiện dòng đơn chờ với SL dương và số ngày chờ", async () => {
    renderWithClient(<AwaitingReturnTab warehouseId={null} />, {
      "/api/inventory/dispatch-history/awaiting-return": { count: 1, results: [ROW] },
    });

    expect(await screen.findByText("TRACK-1")).toBeInTheDocument();
    expect(screen.getByText("SKU-1")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("9 ngày")).toBeInTheDocument();
  });

  it("dialog đóng bắt buộc ghi chú khi chọn lý do Khác", async () => {
    renderWithClient(<AwaitingReturnTab warehouseId={null} />, {
      "/api/inventory/dispatch-history/awaiting-return": { count: 1, results: [ROW] },
    });

    await userEvent.click(await screen.findByRole("button", { name: /đóng/i }));
    await userEvent.click(screen.getByRole("radio", { name: /khác/i }));
    const confirm = screen.getByRole("button", { name: /xác nhận/i });
    expect(confirm).toBeDisabled();

    await userEvent.type(screen.getByRole("textbox"), "Hàng mất");
    await waitFor(() => expect(confirm).toBeEnabled());
  });
});
```

- [ ] **Step 3: Chạy test — phải đỏ**

```bash
npx vitest run src/features/inventory/__tests__/awaiting-return-tab.test.tsx
```

Kỳ vọng: FAIL — không import được `AwaitingReturnTab`.

- [ ] **Step 4: Viết lớp API**

`frontend/src/lib/api/awaiting-return.ts`:

```ts
// Tab "Chờ hàng hoàn về": đơn đã huỷ nhưng hàng còn ngoài kho.
// SL trả về từ backend LUÔN là số dương (lượng hàng đang nợ kho) — không cần
// luật hiển thị |net| như tab Xuất hàng ra.
export type AwaitingReturnLine = {
  product_id: number;
  sku: string;
  qty: number;
};

export type AwaitingReturnRow = {
  order_id: number;
  order_code: string;
  invoice_code: string;
  shipping_code: string;
  customer_name: string;
  warehouse_id: number;
  warehouse_name: string;
  cancelled_at: string;
  days_waiting: number;
  status: "waiting" | "returned" | "closed";
  closed_reason: string;
  closed_note: string;
  lines: AwaitingReturnLine[];
};

export type AwaitingReturnPage = {
  count: number;
  results: AwaitingReturnRow[];
};

export const CLOSE_REASONS = [
  { value: "customer_kept", label: "Khách đã nhận và giữ hàng" },
  { value: "lost_in_transit", label: "Thất lạc, đơn vị vận chuyển đền" },
  { value: "disposed_outside", label: "Huỷ tại chỗ, không trả về" },
  { value: "other", label: "Khác" },
] as const;
```

- [ ] **Step 5: Viết hook query**

`frontend/src/features/inventory/awaiting-return-queries.ts` — dùng đúng `fetchJson` /
`useQuery` / `useMutation` như `return-history-queries.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchJson } from "@/services/http";
import type { AwaitingReturnPage } from "@/lib/api/awaiting-return";

const BASE = "/api/inventory/dispatch-history/awaiting-return";

export function useAwaitingReturn(params: {
  warehouseId: number | null;
  status?: string;
  limit: number;
  offset: number;
}) {
  const query = new URLSearchParams({
    limit: String(params.limit),
    offset: String(params.offset),
    status: params.status ?? "waiting",
  });
  if (params.warehouseId) query.set("warehouse_id", String(params.warehouseId));
  return useQuery({
    queryKey: ["awaiting-return", params],
    queryFn: () => fetchJson(`${BASE}?${query}`) as Promise<AwaitingReturnPage>,
  });
}

export function useCloseAwaitingReturn() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { orderId: number; reason: string; note: string }) =>
      fetchJson(`${BASE}/${input.orderId}/close`, {
        method: "POST",
        body: JSON.stringify({ reason: input.reason, note: input.note }),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["awaiting-return"] }),
  });
}
```

- [ ] **Step 6: Viết dialog đóng**

`frontend/src/features/inventory/components/CloseAwaitingReturnDialog.tsx`:

```tsx
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { CLOSE_REASONS } from "@/lib/api/awaiting-return";

type Props = {
  open: boolean;
  shippingCode: string;
  pending: boolean;
  onClose: () => void;
  onConfirm: (input: { reason: string; note: string }) => void;
};

export function CloseAwaitingReturnDialog({
  open,
  shippingCode,
  pending,
  onClose,
  onConfirm,
}: Props) {
  const [reason, setReason] = useState<string>(CLOSE_REASONS[0].value);
  const [note, setNote] = useState("");
  // Lý do "Khác" không nói lên điều gì nếu không kèm giải thích — chặn ở nút.
  const blocked = reason === "other" && !note.trim();

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Đóng đơn {shippingCode}</DialogTitle>
        </DialogHeader>

        <RadioGroup value={reason} onValueChange={setReason} className="gap-2">
          {CLOSE_REASONS.map((item) => (
            <div key={item.value} className="flex items-center gap-2">
              <RadioGroupItem value={item.value} id={`reason-${item.value}`} />
              <Label htmlFor={`reason-${item.value}`}>{item.label}</Label>
            </div>
          ))}
        </RadioGroup>

        <Textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Ghi chú (bắt buộc khi chọn Khác)"
        />

        <p className="text-sm text-muted-foreground">
          Đóng đơn không cộng tồn — hàng coi như đã rời kho vĩnh viễn.
        </p>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Huỷ
          </Button>
          <Button
            disabled={blocked || pending}
            onClick={() => onConfirm({ reason, note: note.trim() })}
          >
            Xác nhận
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 7: Viết bảng**

`frontend/src/features/inventory/components/AwaitingReturnTab.tsx`:

```tsx
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PAGE_SIZE } from "./flow-constants";
import { CloseAwaitingReturnDialog } from "./CloseAwaitingReturnDialog";
import {
  useAwaitingReturn,
  useCloseAwaitingReturn,
} from "../awaiting-return-queries";
import type { AwaitingReturnRow } from "@/lib/api/awaiting-return";

const STATUS_LABEL: Record<AwaitingReturnRow["status"], string> = {
  waiting: "Chờ hàng về",
  returned: "Hàng đã về",
  closed: "Đã đóng",
};

export function AwaitingReturnTab({ warehouseId }: { warehouseId: number | null }) {
  const [target, setTarget] = useState<AwaitingReturnRow | null>(null);
  const { data, isLoading } = useAwaitingReturn({
    warehouseId,
    limit: PAGE_SIZE,
    offset: 0,
  });
  const close = useCloseAwaitingReturn();

  if (isLoading) return <p className="text-sm text-muted-foreground">Đang tải…</p>;

  const rows = data?.results ?? [];
  if (rows.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Không có đơn huỷ nào đang chờ hàng về.
      </p>
    );
  }

  return (
    <>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Mã vận đơn</TableHead>
            <TableHead>Mã hoá đơn</TableHead>
            <TableHead>Khách</TableHead>
            <TableHead>Kho</TableHead>
            <TableHead>SKU / SL</TableHead>
            <TableHead>Ngày huỷ</TableHead>
            <TableHead>Số ngày chờ</TableHead>
            <TableHead>Trạng thái</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.order_id}>
              <TableCell>{row.shipping_code}</TableCell>
              <TableCell>{row.invoice_code}</TableCell>
              <TableCell>{row.customer_name}</TableCell>
              <TableCell>{row.warehouse_name}</TableCell>
              <TableCell>
                {row.lines.map((line) => (
                  <div key={line.product_id} className="flex gap-2">
                    <span>{line.sku}</span>
                    <span className="font-medium">{line.qty}</span>
                  </div>
                ))}
              </TableCell>
              <TableCell>
                {new Date(row.cancelled_at).toLocaleDateString("vi-VN")}
              </TableCell>
              <TableCell>{row.days_waiting} ngày</TableCell>
              <TableCell>{STATUS_LABEL[row.status]}</TableCell>
              <TableCell>
                {row.status === "waiting" && (
                  <Button size="sm" variant="outline" onClick={() => setTarget(row)}>
                    Đóng
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <CloseAwaitingReturnDialog
        open={target !== null}
        shippingCode={target?.shipping_code ?? ""}
        pending={close.isPending}
        onClose={() => setTarget(null)}
        onConfirm={(input) => {
          if (!target) return;
          close.mutate(
            { orderId: target.order_id, ...input },
            { onSuccess: () => setTarget(null) },
          );
        }}
      />
    </>
  );
}
```

Kiểm tra đường dẫn import các component shadcn (`@/components/ui/*`) khớp với repo trước khi
chạy — nếu repo dùng alias khác, sửa theo file cùng thư mục.

- [ ] **Step 8: Gắn tab vào trang**

Trong `DispatchHistoryPage.tsx`, thêm phần tử thứ ba vào mảng `tabs` (sau tab `return`):

```tsx
    {
      key: "awaiting",
      label: "Chờ hàng hoàn về",
      content: <AwaitingReturnTab warehouseId={filters.warehouse_id ?? null} />,
    },
```

- [ ] **Step 9: Chạy test + typecheck**

```bash
npx vitest run src/features/inventory/__tests__/awaiting-return-tab.test.tsx
npx tsc --noEmit
npm run lint:strict
```

Kỳ vọng: test xanh, tsc sạch.

- [ ] **Step 10: Commit**

```bash
git add frontend/src
git commit -m "feat(inventory): awaiting-return tab with close dialog"
```

---

## Task 6: Cảnh báo Lark cho đơn chờ quá 14 ngày

**Files:**
- Modify: `backend/apps/inventory/tasks.py`
- Modify: `backend/config/settings_parts/celery.py` (lịch beat + route queue)
- Test: `backend/apps/inventory/tests/test_awaiting_return_alert.py` (tạo)

**Interfaces:**
- Consumes: `CancelledDispatchWatch`, `send_stock_negative_alert` (dùng lại kênh Lark kho)
- Produces: Celery task `alert_awaiting_return_overdue()`

- [ ] **Step 1: Viết test thất bại**

```python
"""Cảnh báo Lark: đơn huỷ chờ hàng về quá 14 ngày."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.inventory.models import CancelledDispatchWatch, WatchStatus
from apps.inventory.tasks import alert_awaiting_return_overdue

pytestmark = pytest.mark.django_db

_SEND = "apps.inventory.tasks.send_stock_negative_alert"


def _watch(order, warehouse, *, days: int, status=WatchStatus.WAITING):
    return CancelledDispatchWatch.objects.create(
        order_id=order.id,
        warehouse_id=warehouse.id,
        cancelled_at=timezone.now() - timedelta(days=days),
        status=status,
    )


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(code="WH-ALR", name="Kho ALR")


@pytest.fixture
def orders():
    channel = Channel.objects.create(code="ch-alr", name="ALR Channel")
    built = []
    for index in range(3):
        customer = Customer.objects.create(
            full_name=f"Khach ALR {index}",
            phone_raw=f"09123458{index:02d}",
            phone_normalized=f"09123458{index:02d}",
            code=f"CUST-ALR-{index}",
        )
        built.append(
            Order.objects.create(
                channel=channel,
                customer=customer,
                order_code=f"ORD-ALR-{index}",
                shipping_code=f"TRACK-ALR-{index}",
                name=f"Khach ALR {index}",
                phone=f"09123458{index:02d}",
                status=OrderStatus.CANCELLED,
            )
        )
    return built


def test_alert_lists_only_overdue_waiting_rows(orders, warehouse):
    _watch(orders[0], warehouse, days=20)
    _watch(orders[1], warehouse, days=3)
    _watch(orders[2], warehouse, days=30, status=WatchStatus.CLOSED)

    with patch(_SEND) as send:
        alert_awaiting_return_overdue()

    send.assert_called_once()
    message = send.call_args[0][0]
    assert orders[0].shipping_code in message
    assert orders[1].shipping_code not in message
    assert orders[2].shipping_code not in message


def test_no_alert_when_nothing_overdue(orders, warehouse):
    _watch(orders[0], warehouse, days=2)
    with patch(_SEND) as send:
        alert_awaiting_return_overdue()
    send.assert_not_called()
```

Import kèm theo ở đầu file: `from apps.channels_core.models import Channel`,
`from apps.customers.models import Customer`, `from apps.orders.models import Order, OrderStatus`,
`from apps.warehouses.models import Warehouse`.

- [ ] **Step 2: Chạy test — phải đỏ**

```bash
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_awaiting_return_alert.py -q'
```

Kỳ vọng: ImportError `alert_awaiting_return_overdue`.

- [ ] **Step 3: Viết task**

Trong `backend/apps/inventory/tasks.py` (bám đúng decorator `@shared_task` và cách đặt tên của các task sẵn có trong file):

```python
AWAITING_RETURN_OVERDUE_DAYS = 14


@shared_task(name="inventory.alert_awaiting_return_overdue")
def alert_awaiting_return_overdue() -> int:
    """Nhắc mỗi ngày: đơn huỷ đã xuất mà hàng chưa về sau 14 ngày.

    Không tự đóng đơn — quyết định coi như mất hàng phải có người chịu trách nhiệm
    (spec §5). Task chỉ đẩy danh sách lên Lark để có người xử lý.
    """
    from apps.inventory.models import CancelledDispatchWatch, WatchStatus

    cutoff = timezone.now() - timedelta(days=AWAITING_RETURN_OVERDUE_DAYS)
    rows = list(
        CancelledDispatchWatch.objects.select_related("order", "warehouse")
        .filter(status=WatchStatus.WAITING, cancelled_at__lte=cutoff)
        .order_by("cancelled_at")[:30]
    )
    if not rows:
        return 0
    lines = "\n".join(
        f"• {w.order.shipping_code or w.order.order_code} — kho {w.warehouse.name} "
        f"— chờ {(timezone.now() - w.cancelled_at).days} ngày"
        for w in rows
    )
    send_stock_negative_alert(
        f"📦 {len(rows)} đơn huỷ đã xuất kho quá {AWAITING_RETURN_OVERDUE_DAYS} ngày "
        f"chưa thấy hàng về:\n{lines}"
    )
    return len(rows)
```

Import `send_stock_negative_alert` từ `apps.orders.infrastructure.lark_notifier`, `timezone`
từ `django.utils`, `timedelta` từ `datetime` ở đầu file nếu chưa có.

- [ ] **Step 4: Đăng ký lịch beat**

Trong `backend/config/settings_parts/celery.py`, thêm vào `beat_schedule` theo đúng định dạng
các entry sẵn có (kiểm tra tên biến thật trước khi sửa):

```python
    "inventory-alert-awaiting-return": {
        "task": "inventory.alert_awaiting_return_overdue",
        "schedule": crontab(hour=1, minute=30),  # 08:30 giờ VN
    },
```

Nếu file có `task_routes`, thêm route cho task mới vào queue mà các task inventory khác dùng.

- [ ] **Step 5: Chạy test — phải xanh**

```bash
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_awaiting_return_alert.py apps/common/tests/test_celery_routes.py -q'
```

`test_celery_routes.py` chốt mọi task đều có route — nếu nó đỏ, bổ sung route rồi chạy lại.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory/tasks.py backend/config/settings_parts/celery.py \
        backend/apps/inventory/tests/test_awaiting_return_alert.py
git commit -m "feat(inventory): daily Lark alert for overdue awaiting-return orders"
```

---

## Task 7: Rà hiển thị số dương, tài liệu và checklist deploy

**Files:**
- Verify: `frontend/src/features/inventory/flow-quantity.ts`, `backend/apps/inventory/infrastructure/flow_history_excel.py`
- Create: `docs/runbooks/2026-07-29-cancelled-after-dispatch-return.md`
- Modify: `docs/erp-inventory-scan-shipment.md` (mục hoàn tồn khi huỷ)

- [ ] **Step 1: Kiểm tra luật hiển thị số dương còn đúng sau thay đổi**

Chạy nhóm test hiển thị:

```bash
npx vitest run src/features/inventory/__tests__/flow-quantity.test.ts
```

```bash
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests -k "flow or export or issued" -q'
```

Kỳ vọng: xanh. Luật `|net| → |gross|` không bị ảnh hưởng vì bút toán đảo do huỷ không còn được
sinh ra; các test về `Hoàn tồn do huỷ đơn` vẫn phải xanh (dữ liệu cũ vẫn có bút toán đó).

- [ ] **Step 2: Ghi lại một dòng quan sát nếu có test nào đổi ý nghĩa**

Nếu một test hiển thị phải sửa kỳ vọng, ghi rõ lý do vào commit message — không sửa im lặng.

- [ ] **Step 3: Viết runbook**

Tạo `docs/runbooks/2026-07-29-cancelled-after-dispatch-return.md` gồm: tóm tắt thay đổi hành vi
(huỷ sau xuất không còn tự cộng tồn), cách nhận biết đơn treo trên tab mới, câu SQL đối chiếu
đơn huỷ còn outstanding:

```sql
SELECT w.order_id, o.shipping_code, o.invoice_code, w.status,
       w.cancelled_at, now()::date - w.cancelled_at::date AS days_waiting
FROM cancelled_dispatch_watches w
JOIN orders o ON o.id = w.order_id
WHERE w.status = 'waiting'
ORDER BY w.cancelled_at;
```

và cách xử lý sự cố: nếu một đơn kẹt không quét nhập hoàn được, kiểm tra `outstanding_by_key`
và `pending_cancel_release_by_key` của đơn đó trước khi động vào sổ.

- [ ] **Step 4: Cập nhật tài liệu nghiệp vụ**

Trong `docs/erp-inventory-scan-shipment.md`, sửa mục nói "huỷ đơn → auto hoàn tồn" thành luật
mới, kèm link sang spec `docs/superpowers/specs/2026-07-29-cancelled-after-dispatch-return-design.md`.

- [ ] **Step 5: Chạy toàn bộ suite backend một lượt cuối**

```bash
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest -q' > /tmp/full-suite.log 2>&1
```

Suite đầy đủ ~19 phút — chạy nền và ghi ra file log, đừng để shell nền bị kill mất kết quả.
Kỳ vọng: không có test đỏ ngoài các test đã biết đỏ sẵn trên master.

- [ ] **Step 6: Chạy suite frontend**

```bash
npm run test:ci
```

Kỳ vọng: chỉ `RequireAuth.test.tsx` đỏ (đỏ sẵn trên master).

- [ ] **Step 7: Commit + push + mở MR**

```bash
git add docs
git commit -m "docs: runbook for cancelled-after-dispatch return flow"
git push
```

Gửi link MR cho người review kèm phần "Ảnh hưởng vận hành": tồn sellable từ nay thấp hơn đúng
bằng lượng hàng đang trên đường về của các đơn huỷ sau xuất.

---

## Thứ tự thực hiện và phụ thuộc

```
Task 1 (luật ghi sổ)
  └─ Task 2 (hàng đợi)      ← cần has_pending_cancel_release
       └─ Task 3 (nhập hoàn) ← cần settle_if_returned + reclaim
            ├─ Task 4 (API)
            │    └─ Task 5 (UI)
            └─ Task 6 (cảnh báo)
                 └─ Task 7 (rà soát + tài liệu)
```

Task 1-3 là một khối nghiệp vụ: **không deploy riêng Task 1 hoặc Task 2 lên prod**. Task 1 một
mình sẽ khiến đơn huỷ giữ outstanding trong khi luồng nhập hoàn vẫn chặn — kho không có đường
xử lý. Ba task đầu phải cùng lên trong một lần release.
