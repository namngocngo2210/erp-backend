# Đơn bán trực tiếp "xuất sau" — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tách mốc "tạo hoá đơn" khỏi mốc "hàng rời kho" cho đơn bán trực tiếp, để đơn lên nhanh hôm nay mà hôm sau mới xuất thì tồn kho trừ đúng ngày hàng đi thật.

**Architecture:** Thêm một cờ boolean trên `Order`. Đơn mang cờ vẫn tạo hoá đơn KiotViet ngay nhưng không trừ tồn và không gắn `dispatched`; nó nằm ở một màn hàng đợi trong phần Kho cho tới khi kho bấm "Xác nhận đã xuất", lúc đó mới chạy đúng đường trừ tồn hiện có. Mọi luồng huỷ/hoàn/hồi sinh không phải sửa vì chúng phán quyết bằng sổ chứ không bằng nhãn.

**Tech Stack:** Django 5 + DRF (backend), PostgreSQL, React + TypeScript + shadcn/ui + TanStack Query (frontend), pytest/Django TestCase, vitest.

**Spec:** [docs/superpowers/specs/2026-08-25-direct-sale-deferred-dispatch-design.md](../specs/2026-08-25-direct-sale-deferred-dispatch-design.md)

## Global Constraints

- Nhánh làm việc: `feat/direct-sale-deferred-dispatch`, worktree `.claude/worktrees/direct-deferred-dispatch`. Commit message tiếng Anh, ngắn, **không** kèm dòng `Co-Authored-By`.
- Cột mới tên chính xác: `Order.direct_dispatch_deferred`, `BooleanField(default=False)`.
- Action mới tên chính xác: `quick_sale_deferred_selected`.
- **KHÔNG** thêm giá trị mới vào `ShippingType`. **KHÔNG** thêm giá trị mới vào `OrderKiotVietJob.ACTION_*`.
- **KHÔNG** tạo permission mới. Đọc gate bằng `require_section("stock")`, ghi gate bằng `require_permission("stock:edit")`.
- Migration index dùng `AddIndexConcurrently` + `atomic = False`.
- Thứ tự deploy bắt buộc (ghi vào MR description): **migrate trước, roll code sau**.
- Test chạy trong container theo `docs/` hiện hành; container test có thể đã bị xoá, phải dựng lại trước khi chạy.
- Không rải test lẻ theo từng hàm — gom theo vòng đời vào đúng 2 file nêu trong plan.

---

### Task 1: Cột `direct_dispatch_deferred` + migration

**Files:**
- Modify: `backend/apps/orders/models.py` (class `Order`, cạnh `shipping_type` ~dòng 189; `Meta.indexes` ~dòng 352)
- Create: `backend/apps/orders/migrations/0XXX_order_direct_dispatch_deferred.py`
- Create: `backend/apps/orders/migrations/0XXX_order_direct_deferred_index.py`
- Test: `backend/apps/inventory/tests/test_direct_deferred_dispatch.py`

**Interfaces:**
- Consumes: không có (task đầu).
- Produces: `Order.direct_dispatch_deferred: bool` — mọi task sau đọc/ghi field này.

- [ ] **Step 1: Viết test thất bại**

Tạo file mới `backend/apps/inventory/tests/test_direct_deferred_dispatch.py`:

```python
"""Vòng đời đơn bán trực tiếp "xuất sau" (direct_dispatch_deferred).

Một file cho cả vòng đời thay vì rải test theo hàm: giá trị của cờ này nằm ở chỗ nó
nối "tạo hoá đơn nhanh" với "trừ tồn khi kho xác nhận" và "huỷ đơn đi đường nào".
Tách ra thì mỗi test đúng một mảnh mà đường nối vẫn có thể gãy.
"""
from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order


class DirectDeferredFieldTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="dd-ch", name="Deferred Channel")
        self.customer = Customer.objects.create(
            full_name="KH Deferred", phone_raw="0911000000", phone_normalized="0911000000"
        )

    def test_defaults_to_false(self):
        # Mặc định False là điều kiện để deploy không đổi hành vi đơn cũ.
        order = Order.objects.create(
            channel=self.channel, customer=self.customer, name="A", phone="0911000001"
        )
        self.assertIs(order.direct_dispatch_deferred, False)

    def test_flag_persists(self):
        order = Order.objects.create(
            channel=self.channel, customer=self.customer, name="B", phone="0911000002",
            direct_dispatch_deferred=True,
        )
        order.refresh_from_db()
        self.assertIs(order.direct_dispatch_deferred, True)
```

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: `python manage.py test apps.inventory.tests.test_direct_deferred_dispatch -v 2`
Expected: FAIL — `TypeError: Order() got unexpected keyword argument 'direct_dispatch_deferred'`

- [ ] **Step 3: Thêm field vào model**

Trong `backend/apps/orders/models.py`, class `Order`, đặt ngay dưới `shipping_type`:

```python
    # Đơn bán trực tiếp được tạo với thoả thuận "hàng xuất sau" (khách hẹn hôm sau qua
    # lấy, hoặc shipper nội bộ mang đi ngày hôm sau). Cờ này KHÔNG trả lời "đã xuất
    # chưa" — câu đó do sổ kho và delivery_business_status_code trả lời. Nó chỉ trả lời
    # "đơn này đi theo luật nào": trừ tồn ngay lúc tạo hoá đơn, hay chờ kho xác nhận.
    # Chỉ có nghĩa khi đi kèm shipping_type='direct'; đứng một mình thì vô hại.
    direct_dispatch_deferred = models.BooleanField(default=False)
```

Trong `Order.Meta.indexes`, thêm:

```python
            models.Index(
                fields=["-created_at"],
                name="order_direct_deferred_idx",
                condition=models.Q(direct_dispatch_deferred=True),
            ),
```

- [ ] **Step 4: Sinh migration AddField**

Run: `python manage.py makemigrations orders --name order_direct_dispatch_deferred`

Mở file vừa sinh, kiểm chỉ có đúng một `AddField`. Nếu `makemigrations` gộp luôn `AddIndex`, **tách** phần index ra khỏi file này (bước 5 cần nó ở file riêng với `atomic = False`).

- [ ] **Step 5: Viết tay migration index concurrently**

Tạo `backend/apps/orders/migrations/0XXX_order_direct_deferred_index.py` (thay `0XXX` bằng số kế tiếp, `dependencies` trỏ vào migration ở bước 4):

```python
"""Partial index cho hàng đợi "Chờ xuất kho".

Partial vì tập direct_dispatch_deferred=True rất nhỏ so với bảng đơn — index đầy đủ
là lãng phí dung lượng và chi phí ghi trên mọi INSERT đơn.

atomic=False + AddIndexConcurrently: bảng orders lớn, tạo index khoá ghi sẽ chặn luồng
tạo đơn. CHẠY GIỜ THẤP ĐIỂM. Đứt giữa chừng để lại index INVALID — kiểm bằng
`SELECT indisvalid FROM pg_index ...`, `DROP INDEX CONCURRENTLY` rồi migrate lại.
"""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("orders", "0XXX_order_direct_dispatch_deferred"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                fields=["-created_at"],
                name="order_direct_deferred_idx",
                condition=models.Q(direct_dispatch_deferred=True),
            ),
        ),
    ]
```

- [ ] **Step 6: Chạy test để chắc chắn nó pass**

Run: `python manage.py test apps.inventory.tests.test_direct_deferred_dispatch -v 2`
Expected: PASS (2 test)

- [ ] **Step 7: Kiểm không còn migration treo**

Run: `python manage.py makemigrations --check --dry-run`
Expected: "No changes detected"

- [ ] **Step 8: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations/ backend/apps/inventory/tests/test_direct_deferred_dispatch.py
git commit -m "feat(orders): add direct_dispatch_deferred flag"
```

---

### Task 2: Action `quick_sale_deferred_selected` và ghi cờ ở API

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_action_serializers.py:150-222`
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_action_view.py:829, 864-903`
- Test: `backend/apps/inventory/tests/test_direct_deferred_dispatch.py`

**Interfaces:**
- Consumes: `Order.direct_dispatch_deferred` (Task 1).
- Produces: action string `"quick_sale_deferred_selected"` mà FE (Task 7) sẽ gửi; sau lệnh này mọi đơn được chọn có `direct_dispatch_deferred` đúng giá trị.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `backend/apps/inventory/tests/test_direct_deferred_dispatch.py`:

```python
from unittest.mock import patch

from rest_framework.test import APIClient

from apps.orders.models import OrderStatus
from apps.users.test_helpers import make_admin
from apps.warehouses.models import Warehouse


class QuickSaleDeferredApiTests(TestCase):
    """API đặt lệnh: cờ phải được ghi TƯỜNG MINH cả hai chiều."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-deferred")
        self.client.force_authenticate(self.admin)
        self.channel = Channel.objects.create(code="dd-api", name="Deferred Api")
        self.kv_channel = self._make_kv_channel()
        self.warehouse = Warehouse.objects.create(name="Kho Deferred", code="KHO-DD")
        self.customer = Customer.objects.create(
            full_name="KH DD", phone_raw="0911000010", phone_normalized="0911000010"
        )

    def _make_kv_channel(self):
        from apps.kiotviet.models import KiotVietChannel

        return KiotVietChannel.objects.create(name="KV DD", code="KVDD")

    def _make_order(self, phone: str) -> Order:
        return Order.objects.create(
            channel=self.channel, customer=self.customer, name="KH DD", phone=phone,
            status=OrderStatus.AWAITING_SHIPMENT,
            kiotviet_channel=self.kv_channel, fulfillment_warehouse=self.warehouse,
        )

    def _post(self, action: str, order: Order):
        # dispatch bị patch: test này chỉ kiểm cờ được ghi, không chạy worker.
        with patch("apps.orders.transport.views.order_views_impl.order_action_view.dispatch"):
            return self.client.post(
                "/api/orders/kiotviet-actions",
                {"action": action, "ids": [order.id]},
                format="json",
            )

    def test_deferred_action_sets_flag(self):
        order = self._make_order("0911000011")
        response = self._post("quick_sale_deferred_selected", order)
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertIs(order.direct_dispatch_deferred, True)

    def test_normal_quick_sale_clears_stale_flag(self):
        # Đơn từng bấm "xuất sau" mà job hỏng, sau đó bấm "tạo nhanh" thường: cờ mồ côi
        # phải bị xoá, nếu không đơn bị hoãn oan và không ai trừ tồn.
        order = self._make_order("0911000012")
        order.direct_dispatch_deferred = True
        order.save(update_fields=["direct_dispatch_deferred"])
        response = self._post("quick_sale_selected", order)
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertIs(order.direct_dispatch_deferred, False)

    def test_deferred_action_keeps_existing_guards(self):
        # Guard "đã đẩy đi vận chuyển" phải áp cho CẢ hành động mới.
        order = self._make_order("0911000013")
        order.order_code = "PUSHED-1"
        order.save(update_fields=["order_code"])
        response = self._post("quick_sale_deferred_selected", order)
        self.assertEqual(response.status_code, 400)
        order.refresh_from_db()
        self.assertIs(order.direct_dispatch_deferred, False)

    def test_deferred_action_requires_warehouse(self):
        order = self._make_order("0911000014")
        order.fulfillment_warehouse = None
        order.save(update_fields=["fulfillment_warehouse"])
        response = self._post("quick_sale_deferred_selected", order)
        self.assertEqual(response.status_code, 400)
```

**Lưu ý cho người thực thi:** đường dẫn endpoint và tên `KiotVietChannel` phải kiểm lại bằng `grep -rn "kiotviet-action" backend/apps/orders/urls.py` và `grep -n "class KiotVietChannel" -A 15 backend/apps/kiotviet/models.py` trước khi chạy — sửa test cho khớp chữ ký thật, đừng sửa production code cho khớp test sai.

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: `python manage.py test apps.inventory.tests.test_direct_deferred_dispatch.QuickSaleDeferredApiTests -v 2`
Expected: FAIL — action không nằm trong `choices` nên serializer trả 400.

- [ ] **Step 3: Thêm action vào serializer**

Trong `order_action_serializers.py`, class `OrderKiotVietActionSerializer`:

```python
    ACTION_QUICK_SALE_SELECTED = "quick_sale_selected"
    ACTION_QUICK_SALE_DEFERRED_SELECTED = "quick_sale_deferred_selected"
```

Thêm vào `choices` của field `action` (ngay sau `ACTION_QUICK_SALE_SELECTED`), và vào tập trong `validate()` yêu cầu "phải chọn ít nhất 1 đơn":

```python
        if action in {
            self.ACTION_PUSH_SELECTED,
            self.ACTION_SYNC_SHIPPING_SELECTED,
            self.ACTION_SYNC_STATUS_SELECTED,
            self.ACTION_QUICK_SALE_SELECTED,
            self.ACTION_QUICK_SALE_DEFERRED_SELECTED,
        } and not ids and not order_ids:
```

Nhánh `step == STEP_PRECHECK` giữ nguyên — hành động mới cũng chỉ hỗ trợ `execute`, y như quick-sale thường.

- [ ] **Step 4: Gộp action mới vào guard + ghi cờ ở view**

Trong `order_action_view.py`, đổi điều kiện ở dòng ~829 và ~864 từ so bằng sang so trong tập:

```python
_QUICK_SALE_ACTIONS = {
    OrderKiotVietActionSerializer.ACTION_QUICK_SALE_SELECTED,
    OrderKiotVietActionSerializer.ACTION_QUICK_SALE_DEFERRED_SELECTED,
}
```

```python
    if action in _QUICK_SALE_ACTIONS:
        # ... giữ nguyên 3 guard: kênh KiotViet, kho xuất, chặn đơn đã có order_code
```

Sau khi cả 3 guard đã qua (tức ngay trước khối `if action in {...}: job_action = ...`), thêm:

```python
    if action in _QUICK_SALE_ACTIONS:
        # Ghi TƯỜNG MINH cả hai chiều. Chỉ ghi chiều True là để lại cờ mồ côi: đơn từng
        # bấm "xuất sau" mà job hỏng, sau đó bấm "tạo nhanh" thường, sẽ bị hoãn oan và
        # không ai trừ tồn cho nó.
        Order.objects.filter(id__in=[o.id for o in order_list]).update(
            direct_dispatch_deferred=(
                action == OrderKiotVietActionSerializer.ACTION_QUICK_SALE_DEFERRED_SELECTED
            )
        )
```

Và ánh xạ action → job (giữ `ACTION_CREATE_INVOICE` cho cả hai):

```python
    elif action in _QUICK_SALE_ACTIONS:
        job_action = OrderKiotVietJob.ACTION_CREATE_INVOICE
```

- [ ] **Step 5: Chạy test để chắc chắn nó pass**

Run: `python manage.py test apps.inventory.tests.test_direct_deferred_dispatch.QuickSaleDeferredApiTests -v 2`
Expected: PASS (4 test)

- [ ] **Step 6: Chạy lại test cũ của quick-sale để chắc không vỡ**

Run: `python manage.py test apps.orders.tests.test_direct_sale_shipping_type -v 2`
Expected: PASS toàn bộ

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders/transport backend/apps/inventory/tests/test_direct_deferred_dispatch.py
git commit -m "feat(orders): add deferred quick-sale bulk action"
```

---

### Task 3: Worker không trừ tồn cho đơn "xuất sau"

**Files:**
- Modify: `backend/apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py:287-345` (hàm `_apply_shipping_type_after_job`)
- Test: `backend/apps/inventory/tests/test_direct_deferred_dispatch.py`

**Interfaces:**
- Consumes: `Order.direct_dispatch_deferred` (Task 1).
- Produces: sau khi job `create_invoice` thành công trên đơn deferred → `shipping_type='direct'`, **không** có `StockMovement` SALE, **không** có `delivery_business_status_code='dispatched'`.

- [ ] **Step 1: Viết test thất bại**

Thêm class vào `test_direct_deferred_dispatch.py`:

```python
from apps.inventory.models import MovementType, StockMovement
from apps.orders.models import OrderAssessment, OrderKiotVietJob


class DeferredWorkerTests(TestCase):
    """Job create_invoice thành công trên đơn deferred: gắn nhãn, KHÔNG ghi sổ."""

    def setUp(self):
        from apps.inventory.tests.helpers import make_order

        self.channel = Channel.objects.create(code="dd-wk", name="Deferred Worker")
        self.warehouse = Warehouse.objects.create(name="Kho WK", code="KHO-WK")
        self.order = make_order(self.channel, self.warehouse, "21", shipping_code="")
        self.order.invoice_code = "HD-DD-21"
        self.order.direct_dispatch_deferred = True
        self.order.save(update_fields=["invoice_code", "direct_dispatch_deferred"])
        self.job = OrderKiotVietJob.objects.create(
            order=self.order, action=OrderKiotVietJob.ACTION_CREATE_INVOICE
        )

    def _sale_movements(self):
        return StockMovement.objects.filter(
            reference_type="order", reference_id=self.order.id, movement_type=MovementType.SALE
        )

    def test_deferred_order_is_labelled_but_not_issued(self):
        from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import (
            _apply_shipping_type_after_job,
        )

        _apply_shipping_type_after_job(self.job, self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_type, "direct")
        self.assertEqual(self._sale_movements().count(), 0)
        assessment = OrderAssessment.objects.filter(order=self.order).first()
        self.assertNotEqual(
            getattr(assessment, "delivery_business_status_code", None), "dispatched"
        )

    def test_non_deferred_order_still_issues_immediately(self):
        # Khoá hành vi cũ: đơn quầy giao ngay KHÔNG được đổi.
        self.order.direct_dispatch_deferred = False
        self.order.save(update_fields=["direct_dispatch_deferred"])

        from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import (
            _apply_shipping_type_after_job,
        )

        _apply_shipping_type_after_job(self.job, self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_type, "direct")
        self.assertGreater(self._sale_movements().count(), 0)
```

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: `python manage.py test apps.inventory.tests.test_direct_deferred_dispatch.DeferredWorkerTests -v 2`
Expected: FAIL ở `test_deferred_order_is_labelled_but_not_issued` — hiện worker vẫn trừ tồn, `count()` là 1 chứ không phải 0.

- [ ] **Step 3: Rẽ nhánh trong worker**

Trong `_apply_shipping_type_after_job`, **giữ nguyên** toàn bộ guard "đã rời kho qua đường vận chuyển" ở đầu. Trong khối `with transaction.atomic():`, chèn ngay sau đoạn gắn `shipping_type`:

```python
            if order.direct_dispatch_deferred:
                # Đơn "xuất sau": hoá đơn đã lên nhưng hàng còn trên kệ. Không trừ tồn,
                # không gắn dispatched — cả hai chờ kho bấm xác nhận ở màn "Chờ xuất kho"
                # (DirectDispatchService.confirm). Gắn nhãn direct thì vẫn phải làm ngay,
                # vì nhánh huỷ đơn đọc nhãn này để chọn đường.
                return
```

- [ ] **Step 4: Chạy test để chắc chắn nó pass**

Run: `python manage.py test apps.inventory.tests.test_direct_deferred_dispatch.DeferredWorkerTests -v 2`
Expected: PASS (2 test)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/infrastructure backend/apps/inventory/tests/test_direct_deferred_dispatch.py
git commit -m "feat(orders): skip stock issue for deferred direct sales"
```

---

### Task 4: Nhánh huỷ đơn phân biệt hai loại đơn direct

**Files:**
- Modify: `backend/apps/inventory/subscribers.py:62-71` (hàm `_is_direct_sale`)
- Test: `backend/apps/inventory/tests/test_direct_deferred_dispatch.py`

**Interfaces:**
- Consumes: `Order.direct_dispatch_deferred` (Task 1).
- Produces: `_is_direct_sale(order_id) -> bool` giữ nguyên chữ ký, đổi ngữ nghĩa thành "đơn bán quầy giao ngay".

- [ ] **Step 1: Viết test thất bại**

Thêm class vào `test_direct_deferred_dispatch.py`:

```python
from apps.inventory.models import CancelledDispatchWatch
from apps.inventory.tests.helpers import sellable


class DeferredCancelTests(TestCase):
    """Huỷ đơn: đơn chờ xuất KHÔNG mở watch; đơn đã xuất thì CÓ."""

    def setUp(self):
        from apps.inventory.tests.helpers import make_order

        self.channel = Channel.objects.create(code="dd-cx", name="Deferred Cancel")
        self.warehouse = Warehouse.objects.create(name="Kho CX", code="KHO-CX")
        self.order = make_order(self.channel, self.warehouse, "31", shipping_code="")
        self.order.shipping_type = "direct"
        self.order.direct_dispatch_deferred = True
        self.order.save(update_fields=["shipping_type", "direct_dispatch_deferred"])
        self.item = self.order.items.first()

    def _cancel(self):
        from apps.orders.contracts.order_events import OrderStatusChanged
        from apps.inventory.subscribers import _on_order_status_changed

        _on_order_status_changed(
            OrderStatusChanged(order_id=self.order.id, old_status=1, new_status=6)
        )

    def test_cancel_while_waiting_touches_nothing(self):
        before = sellable(self.warehouse.id, self.item.product_id)
        self._cancel()
        self.assertEqual(sellable(self.warehouse.id, self.item.product_id), before)
        self.assertFalse(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())

    def test_cancel_after_dispatch_opens_watch(self):
        from apps.inventory.contracts.stock_sales import StockSalesService
        from apps.inventory.contracts.stock_sales import SaleLine

        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.item.product_id, quantity=self.item.quantity)],
        )
        after_issue = sellable(self.warehouse.id, self.item.product_id)

        self._cancel()

        # Hàng đang ở ngoài: KHÔNG cộng trả, phải mở dòng chờ hàng về.
        self.assertEqual(sellable(self.warehouse.id, self.item.product_id), after_issue)
        self.assertTrue(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())

    def test_counter_sale_still_releases_immediately(self):
        # Khoá hành vi cũ của đơn quầy giao ngay.
        from apps.inventory.contracts.stock_sales import SaleLine, StockSalesService

        self.order.direct_dispatch_deferred = False
        self.order.save(update_fields=["direct_dispatch_deferred"])
        StockSalesService.issue_for_order(
            order_id=self.order.id,
            warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.item.product_id, quantity=self.item.quantity)],
        )
        after_issue = sellable(self.warehouse.id, self.item.product_id)

        self._cancel()

        self.assertGreater(sellable(self.warehouse.id, self.item.product_id), after_issue)
        self.assertFalse(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())
```

**Lưu ý:** chữ ký `OrderStatusChanged` và `issue_for_order` phải kiểm lại bằng `grep -n "class OrderStatusChanged" -A 10 backend/apps/orders/contracts/order_events.py` và `grep -n "def issue_for_order" -A 12 backend/apps/inventory/application/services/stock_sales_service.py`. `_on_order_status_changed` chạy trong `on_commit` ở production — trong test gọi thẳng hàm là đúng, nhưng nếu suite dùng `TransactionTestCase` cho nhóm này thì theo convention file test kho hiện có.

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: `python manage.py test apps.inventory.tests.test_direct_deferred_dispatch.DeferredCancelTests -v 2`
Expected: FAIL ở `test_cancel_after_dispatch_opens_watch` — hiện `_is_direct_sale` trả `True` nên tồn bị cộng trả và watch không được mở.

- [ ] **Step 3: Sửa `_is_direct_sale`**

```python
def _is_direct_sale(order_id: int) -> bool:
    """Đơn bán quầy GIAO NGAY không? Một truy vấn theo PK, không có N+1.

    LOẠI TRỪ đơn "xuất sau" (`direct_dispatch_deferred`): với đơn đó, huỷ KHÔNG có
    nghĩa là hàng còn trong kho — shipper nội bộ có thể đã mang đi hôm qua. Chúng đi
    nhánh "chờ hàng về" bên dưới, và nhánh đó tự đúng ở cả hai trạng thái vì nó phán
    quyết bằng SỔ (`order_goods_still_out`) chứ không bằng nhãn: đơn chưa xác nhận xuất
    có sổ rỗng nên `open_for_order` trả False, không mở watch, không bắn Lark.

    Dùng `all_objects` chứ không phải `objects` (`InternalOrderManager`, lọc bỏ đơn
    mirror Foxia): đây là đọc thẳng cờ theo PK, không phải liệt kê đơn nội bộ — lọc
    phạm vi ở đây chỉ khiến đơn mirror bị coi nhầm là "không phải direct" và tồn của nó
    rơi vào nhánh chờ hàng về sai.
    """
    from apps.orders.models import Order, ShippingType

    return Order.all_objects.filter(
        pk=order_id,
        shipping_type=ShippingType.DIRECT,
        direct_dispatch_deferred=False,
    ).exists()
```

- [ ] **Step 4: Chạy test để chắc chắn nó pass**

Run: `python manage.py test apps.inventory.tests.test_direct_deferred_dispatch.DeferredCancelTests -v 2`
Expected: PASS (3 test)

- [ ] **Step 5: Khoá hai ca biên "tự đúng" bằng test**

Spec §6 khẳng định hai ca này không cần code thêm vì các luồng liên quan đọc sổ. Khẳng
định đó phải được khoá lại, nếu không lần refactor sau sẽ phá mà không ai biết. Thêm vào
`DeferredCancelTests`:

```python
    def test_revived_waiting_order_is_not_issued(self):
        # Huỷ rồi bỏ huỷ đơn CHƯA xuất: reissue đọc sổ, không có bút toán hoàn nào để
        # trừ lại; resync no-op vì outstanding = 0. Đơn quay lại hàng đợi chờ xuất.
        from apps.orders.contracts.order_events import OrderStatusChanged
        from apps.inventory.subscribers import _on_order_status_changed

        before = sellable(self.warehouse.id, self.item.product_id)
        self._cancel()
        _on_order_status_changed(
            OrderStatusChanged(order_id=self.order.id, old_status=6, new_status=1)
        )
        self.assertEqual(sellable(self.warehouse.id, self.item.product_id), before)

    def test_waiting_order_pushed_to_shipment_is_issued_once(self):
        # Đơn chờ xuất sau đó được đẩy sang giao vận: luồng quét mã hiện tại trừ tồn,
        # và phải trừ ĐÚNG MỘT LẦN (issue_for_order idempotent).
        from apps.inventory.contracts.stock_sales import SaleLine, StockSalesService

        before = sellable(self.warehouse.id, self.item.product_id)
        for _ in range(2):
            StockSalesService.issue_for_order(
                order_id=self.order.id,
                warehouse_id=self.warehouse.id,
                lines=[SaleLine(product_id=self.item.product_id, quantity=self.item.quantity)],
            )
        self.assertEqual(
            sellable(self.warehouse.id, self.item.product_id), before - self.item.quantity
        )
```

Run: `python manage.py test apps.inventory.tests.test_direct_deferred_dispatch.DeferredCancelTests -v 2`
Expected: PASS (5 test) — hai test này phải xanh **ngay**, không cần sửa production code. Nếu đỏ thì giả định của spec §6 sai, **dừng lại và báo** thay vì sửa cho xanh.

- [ ] **Step 6: Chạy cả nhóm test huỷ/hoàn hiện có**

Run: `python manage.py test apps.inventory.tests.test_cancel_return_semantics apps.inventory.tests.test_awaiting_return -v 2`
Expected: PASS toàn bộ — nếu đỏ, so với master trước khi nghi nhánh mình.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/inventory/subscribers.py backend/apps/inventory/tests/test_direct_deferred_dispatch.py
git commit -m "fix(inventory): route deferred direct sales through awaiting-return on cancel"
```

---

### Task 5: `DirectDispatchService` + 2 endpoint

**Files:**
- Create: `backend/apps/inventory/application/services/direct_dispatch_service.py`
- Create: `backend/apps/inventory/transport/views/direct_dispatch_views.py`
- Modify: `backend/apps/inventory/urls.py`
- Modify: `backend/config/settings.py` (hằng số ngưỡng cảnh báo)
- Test: `backend/apps/inventory/tests/test_direct_deferred_dispatch.py`

**Interfaces:**
- Consumes: `Order.direct_dispatch_deferred` (Task 1); worker đã để đơn ở trạng thái chờ (Task 3).
- Produces:
  - `DirectDispatchService.list_pending(*, warehouse_ids: list[int] | None = None) -> list[dict]`
  - `DirectDispatchService.confirm(*, order_ids: list[int], actor) -> dict` trả `{"confirmed": list[int], "skipped": list[dict]}`
  - `GET /api/inventory/direct-dispatch/pending`, `POST /api/inventory/direct-dispatch/confirm`

- [ ] **Step 1: Viết test thất bại**

Thêm class vào `test_direct_deferred_dispatch.py`:

```python
class DirectDispatchServiceTests(TestCase):
    def setUp(self):
        from apps.inventory.tests.helpers import make_order

        self.channel = Channel.objects.create(code="dd-sv", name="Deferred Service")
        self.warehouse = Warehouse.objects.create(name="Kho SV", code="KHO-SV")
        self.admin = make_admin("admin-dd-service")
        self.order = make_order(self.channel, self.warehouse, "41", shipping_code="")
        self.order.shipping_type = "direct"
        self.order.direct_dispatch_deferred = True
        self.order.invoice_code = "HD-DD-41"
        self.order.save(
            update_fields=["shipping_type", "direct_dispatch_deferred", "invoice_code"]
        )
        self.item = self.order.items.first()

    def _pending_ids(self):
        from apps.inventory.application.services.direct_dispatch_service import (
            DirectDispatchService,
        )

        return [row["order_id"] for row in DirectDispatchService.list_pending()]

    def test_waiting_order_is_listed(self):
        self.assertIn(self.order.id, self._pending_ids())

    def test_counter_sale_is_not_listed(self):
        self.order.direct_dispatch_deferred = False
        self.order.save(update_fields=["direct_dispatch_deferred"])
        self.assertNotIn(self.order.id, self._pending_ids())

    def test_confirm_issues_stock_and_leaves_queue(self):
        from apps.inventory.application.services.direct_dispatch_service import (
            DirectDispatchService,
        )

        before = sellable(self.warehouse.id, self.item.product_id)
        result = DirectDispatchService.confirm(order_ids=[self.order.id], actor=self.admin)

        self.assertEqual(result["confirmed"], [self.order.id])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(
            sellable(self.warehouse.id, self.item.product_id), before - self.item.quantity
        )
        self.assertNotIn(self.order.id, self._pending_ids())

    def test_confirm_records_movement_at_confirm_time_not_invoice_time(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.inventory.application.services.direct_dispatch_service import (
            DirectDispatchService,
        )

        purchase_date = timezone.now() - timedelta(days=3)
        self.order.kiotviet_purchase_date = purchase_date
        self.order.save(update_fields=["kiotviet_purchase_date"])

        DirectDispatchService.confirm(order_ids=[self.order.id], actor=self.admin)

        movement = StockMovement.objects.filter(
            reference_type="order", reference_id=self.order.id, movement_type=MovementType.SALE
        ).first()
        self.assertIsNotNone(movement)
        # Mốc phải là lúc BẤM, không phải ngày hoá đơn — đây là toàn bộ lý do tồn tại
        # của tính năng này.
        self.assertGreater(movement.occurred_at, purchase_date + timedelta(days=1))

    def test_confirm_twice_is_skipped_not_double_issued(self):
        from apps.inventory.application.services.direct_dispatch_service import (
            DirectDispatchService,
        )

        DirectDispatchService.confirm(order_ids=[self.order.id], actor=self.admin)
        after_first = sellable(self.warehouse.id, self.item.product_id)

        result = DirectDispatchService.confirm(order_ids=[self.order.id], actor=self.admin)

        self.assertEqual(result["confirmed"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["order_id"], self.order.id)
        self.assertEqual(sellable(self.warehouse.id, self.item.product_id), after_first)

    def test_cancelled_order_is_skipped(self):
        from apps.inventory.application.services.direct_dispatch_service import (
            DirectDispatchService,
        )

        self.order.status = OrderStatus.CANCELLED
        self.order.save(update_fields=["status"])

        result = DirectDispatchService.confirm(order_ids=[self.order.id], actor=self.admin)
        self.assertEqual(result["confirmed"], [])
        self.assertEqual(len(result["skipped"]), 1)


class DirectDispatchApiTests(TestCase):
    """Phân quyền: đọc theo section stock, ghi theo stock:edit."""

    def setUp(self):
        from apps.inventory.tests.helpers import make_order

        self.client = APIClient()
        self.admin = make_admin("admin-dd-api")
        self.channel = Channel.objects.create(code="dd-ep", name="Deferred Endpoint")
        self.warehouse = Warehouse.objects.create(name="Kho EP", code="KHO-EP")
        self.order = make_order(self.channel, self.warehouse, "51", shipping_code="")
        self.order.shipping_type = "direct"
        self.order.direct_dispatch_deferred = True
        self.order.save(update_fields=["shipping_type", "direct_dispatch_deferred"])

    def test_pending_requires_auth(self):
        response = self.client.get("/api/inventory/direct-dispatch/pending")
        self.assertIn(response.status_code, (401, 403))

    def test_pending_lists_order(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/inventory/direct-dispatch/pending")
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.order.id, [row["order_id"] for row in response.data["items"]])

    def test_pending_hides_customer_contact(self):
        # Thao tác xuất kho không cần SĐT/địa chỉ khách.
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/inventory/direct-dispatch/pending")
        row = next(r for r in response.data["items"] if r["order_id"] == self.order.id)
        self.assertNotIn("phone", row)
        self.assertNotIn("address", row)

    def test_confirm_endpoint_issues_stock(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/inventory/direct-dispatch/confirm",
            {"order_ids": [self.order.id]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["confirmed"], [self.order.id])
```

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: `python manage.py test apps.inventory.tests.test_direct_deferred_dispatch.DirectDispatchServiceTests -v 2`
Expected: FAIL — `ModuleNotFoundError: apps.inventory.application.services.direct_dispatch_service`

- [ ] **Step 3: Thêm hằng số ngưỡng vào settings**

Trong `backend/config/settings.py` (cạnh các hằng số nghiệp vụ kho khác):

```python
# Số ngày một đơn "xuất sau" được nằm ở hàng đợi Chờ xuất kho trước khi bị tô cảnh báo.
# Hằng số, KHÔNG qua env: đổi ngưỡng là quyết định nghiệp vụ hiếm, không đáng thêm một
# biến phải đồng bộ ở 4 file docker-compose.
DIRECT_DISPATCH_WARN_DAYS = 2
```

- [ ] **Step 4: Viết service**

Tạo `backend/apps/inventory/application/services/direct_dispatch_service.py`:

```python
"""Hàng đợi "Chờ xuất kho" cho đơn bán trực tiếp xuất sau.

Đơn ở đây đã có hoá đơn KiotViet nhưng hàng còn trên kệ: worker cố tình không trừ tồn
(`_apply_shipping_type_after_job`). Kho bấm xác nhận thì mới chạy đúng đường trừ tồn của
đơn giao vận, với mốc thời gian là lúc BẤM.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Lý do bỏ qua khi xác nhận — trả thẳng cho FE thay vì im lặng bỏ dòng.
SKIP_NOT_WAITING = "not_waiting"
SKIP_CANCELLED = "cancelled"
SKIP_ALREADY_DISPATCHED = "already_dispatched"


def _dispatched_order_ids(order_ids: list[int]) -> set[int]:
    """Đơn đã rời kho — hai tín hiệu, đúng một cái là đủ.

    Cùng luật với `flow_order_lookup` và `_is_dispatched` ở luồng quét. Đơn không khớp
    sản phẩm nào sẽ không sinh bút toán, nên chỉ đọc sổ sẽ báo "chưa xuất" vĩnh viễn.
    """
    from apps.inventory.application.services.stock_sales_service import (
        order_ids_with_outstanding_stock,
    )
    from apps.orders.crm_catalog import DISPATCHED_DELIVERY_CODE
    from apps.orders.models import OrderAssessment

    result = set(order_ids_with_outstanding_stock(order_ids))
    result |= set(
        OrderAssessment.objects.filter(
            order_id__in=order_ids,
            delivery_business_status_code=DISPATCHED_DELIVERY_CODE,
        ).values_list("order_id", flat=True)
    )
    return result


class DirectDispatchService:
    @staticmethod
    def list_pending(*, warehouse_ids: list[int] | None = None) -> list[dict]:
        """Đơn bán trực tiếp đang chờ kho xác nhận xuất.

        Không trả SĐT/địa chỉ khách: thao tác xuất kho không cần tới, và danh sách này
        mở cho toàn bộ nhân sự có section `stock`.
        """
        from apps.orders.models import Order, OrderStatus, ShippingType

        queryset = (
            Order.all_objects.filter(
                shipping_type=ShippingType.DIRECT,
                direct_dispatch_deferred=True,
            )
            .exclude(status=OrderStatus.CANCELLED)
            .select_related("fulfillment_warehouse", "staff")
            .prefetch_related("items")
            .order_by("created_at")
        )
        if warehouse_ids:
            queryset = queryset.filter(fulfillment_warehouse_id__in=warehouse_ids)

        rows = list(queryset)
        dispatched = _dispatched_order_ids([o.id for o in rows])
        now = timezone.now()
        warn_days = getattr(settings, "DIRECT_DISPATCH_WARN_DAYS", 2)

        items: list[dict] = []
        for order in rows:
            if order.id in dispatched:
                continue
            invoiced_at = order.kiotviet_purchase_date or order.created_at
            waiting_days = (now - invoiced_at).days
            items.append(
                {
                    "order_id": order.id,
                    "invoice_code": order.invoice_code or "",
                    "warehouse_id": order.fulfillment_warehouse_id,
                    "warehouse_name": getattr(order.fulfillment_warehouse, "name", ""),
                    "created_by": getattr(order.staff, "username", "") or "",
                    "invoiced_at": invoiced_at,
                    "waiting_days": waiting_days,
                    "is_overdue": waiting_days >= warn_days,
                    "items": [
                        {
                            "product_id": item.product_id,
                            "sku": item.sku or "",
                            "product_name": item.product_name or "",
                            "quantity": item.quantity,
                        }
                        for item in order.items.all()
                        if item.product_id
                    ],
                }
            )
        return items

    @staticmethod
    def confirm(*, order_ids: list[int], actor) -> dict:
        """Kho xác nhận hàng đã rời kho → trừ tồn với mốc là thời điểm bấm.

        MỖI ĐƠN MỘT TRANSACTION: một đơn hỏng (hoá đơn KiotViet đã bị huỷ trong đêm)
        không được kéo cả lô về rollback.
        """
        from apps.orders.models import Order, OrderStatus, ShippingType
        from apps.orders.application.services.order_service import OrderService

        confirmed: list[int] = []
        skipped: list[dict] = []

        for order_id in order_ids:
            order = (
                Order.all_objects.filter(pk=order_id)
                .select_related("customer")
                .first()
            )
            if order is None:
                skipped.append({"order_id": order_id, "reason": SKIP_NOT_WAITING})
                continue
            if order.status == OrderStatus.CANCELLED:
                skipped.append({"order_id": order_id, "reason": SKIP_CANCELLED})
                continue
            if not (
                order.shipping_type == ShippingType.DIRECT and order.direct_dispatch_deferred
            ):
                skipped.append({"order_id": order_id, "reason": SKIP_NOT_WAITING})
                continue
            if order_id in _dispatched_order_ids([order_id]):
                skipped.append({"order_id": order_id, "reason": SKIP_ALREADY_DISPATCHED})
                continue

            try:
                # `_verified`: xác minh KiotViet (HTTP) chạy TRƯỚC transaction ghi sổ.
                # KHÔNG dùng verdict=OK như worker: ở worker, KiotViet vừa trả hoá đơn
                # sống vài giây trước; ở đây bằng chứng đó đã cũ cả ngày và hoá đơn có
                # thể đã bị huỷ trong đêm. Phải hỏi lại.
                #
                # Không truyền occurred_at: mặc định của record_movement là now(), đúng
                # bằng thời điểm bấm — chính là mốc ta cần.
                OrderService.update_order_inline_verified(
                    order,
                    {"crm": {"delivery_business_status_code": "dispatched"}},
                    actor,
                )
            except Exception as exc:
                logger.exception("Xác nhận xuất kho thất bại cho đơn %s", order_id)
                skipped.append({"order_id": order_id, "reason": str(exc)[:200]})
                continue
            confirmed.append(order_id)

        return {"confirmed": confirmed, "skipped": skipped}
```

**Lưu ý cho người thực thi:** kiểm `OrderService` có expose `update_order_inline_verified` không (`grep -rn "update_order_inline_verified" backend/apps/orders/application/services/order_service.py`). Nếu facade chưa expose thì gọi qua `OrderServiceCommands`, đừng tự thêm hàm mới. Kiểm `order_ids_with_outstanding_stock` nhận kiểu gì (`grep -n "def order_ids_with_outstanding_stock" -A 10 backend/apps/inventory/application/services/stock_sales_service.py`).

- [ ] **Step 5: Viết view + url**

Tạo `backend/apps/inventory/transport/views/direct_dispatch_views.py`:

```python
"""Màn "Chờ xuất kho" — đơn bán trực tiếp xuất sau.

Phân quyền theo đúng luật đã ghi ở `flow_pending_views.py`: danh sách ĐỌC gate bằng
section "stock", hành động GHI (trừ tồn) gate bằng "stock:edit". Không đẻ permission
mới — permission mới lại phải nhớ gán role.
"""
from __future__ import annotations

from django.views.decorators.cache import never_cache
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.inventory.application.services.direct_dispatch_service import DirectDispatchService
from apps.inventory.transport.views.flow_history_shared import _int_or_none
from apps.users.permissions import require_permission, require_section

_PENDING_ITEM = {
    "order_id": drf.IntegerField(),
    "invoice_code": drf.CharField(allow_blank=True),
    "warehouse_id": drf.IntegerField(allow_null=True),
    "warehouse_name": drf.CharField(allow_blank=True),
    "created_by": drf.CharField(allow_blank=True),
    "invoiced_at": drf.DateTimeField(),
    "waiting_days": drf.IntegerField(),
    "is_overdue": drf.BooleanField(),
    "items": inline_serializer(
        "DirectDispatchPendingLine",
        many=True,
        fields={
            "product_id": drf.IntegerField(),
            "sku": drf.CharField(allow_blank=True),
            "product_name": drf.CharField(allow_blank=True),
            "quantity": drf.IntegerField(),
        },
    ),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "DirectDispatchPending",
            fields={
                "items": inline_serializer(
                    "DirectDispatchPendingItem", many=True, fields=_PENDING_ITEM
                )
            },
        )
    }
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("stock")])
@never_cache
def direct_dispatch_pending(request):
    raw = (request.query_params.get("warehouse_ids") or "").strip()
    warehouse_ids = [
        value for value in (_int_or_none(part) for part in raw.split(",")) if value
    ]
    items = DirectDispatchService.list_pending(warehouse_ids=warehouse_ids or None)
    return Response({"items": items}, status=status.HTTP_200_OK)


class _ConfirmInputSerializer(drf.Serializer):
    order_ids = drf.ListField(
        child=drf.IntegerField(min_value=1), allow_empty=False, max_length=200
    )


@extend_schema(
    request=_ConfirmInputSerializer,
    responses={
        200: inline_serializer(
            "DirectDispatchConfirm",
            fields={
                "confirmed": drf.ListField(child=drf.IntegerField()),
                "skipped": inline_serializer(
                    "DirectDispatchSkipped",
                    many=True,
                    fields={
                        "order_id": drf.IntegerField(),
                        "reason": drf.CharField(),
                    },
                ),
            },
        ),
        400: OpenApiResponse(description="Danh sách đơn không hợp lệ"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, require_permission("stock:edit")])
def direct_dispatch_confirm(request):
    serializer = _ConfirmInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    result = DirectDispatchService.confirm(
        order_ids=serializer.validated_data["order_ids"], actor=request.user
    )
    return Response(result, status=status.HTTP_200_OK)
```

Trong `backend/apps/inventory/urls.py`, thêm import và 2 path (đặt cạnh nhóm `dispatch-history`):

```python
from apps.inventory.transport.views.direct_dispatch_views import (
    direct_dispatch_confirm,
    direct_dispatch_pending,
)
```

```python
    path(
        "direct-dispatch/pending",
        direct_dispatch_pending,
        name="inventory-direct-dispatch-pending",
    ),
    path(
        "direct-dispatch/confirm",
        direct_dispatch_confirm,
        name="inventory-direct-dispatch-confirm",
    ),
```

- [ ] **Step 6: Chạy test để chắc chắn nó pass**

Run: `python manage.py test apps.inventory.tests.test_direct_deferred_dispatch -v 2`
Expected: PASS toàn bộ file

- [ ] **Step 7: Kiểm import-linter không bị vi phạm tầng**

Run: `lint-imports --config .importlinter`
Expected: Kept — nếu đỏ vì `inventory → orders`, kiểm lại contract; import ngược `inventory → kiotviet` là cấm, `inventory → orders` thì không.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/inventory backend/config/settings.py
git commit -m "feat(inventory): pending direct-dispatch queue and confirm endpoint"
```

---

### Task 6: `pending_direct` trong tồn khả dụng (có nở combo)

**Files:**
- Modify: `backend/apps/inventory/application/services/stock_query_service.py:30-69` (hàm `availability`)
- Modify: `backend/apps/inventory/transport/serializers/stock_serializers.py` (`StockAvailabilityRowSerializer`)
- Test: `backend/apps/inventory/tests/test_direct_deferred_dispatch.py`

**Interfaces:**
- Consumes: `DirectDispatchService` khái niệm "đơn đang chờ xuất" (Task 5) — nhưng **query trực tiếp**, không gọi service (tránh vòng import và tránh dựng payload thừa).
- Produces: mỗi dòng của `availability` có thêm khoá `pending_direct: int`.

- [ ] **Step 1: Viết test thất bại**

Thêm class vào `test_direct_deferred_dispatch.py`:

```python
class AvailabilityPendingDirectTests(TestCase):
    def setUp(self):
        from apps.inventory.tests.helpers import make_order

        self.channel = Channel.objects.create(code="dd-av", name="Deferred Avail")
        self.warehouse = Warehouse.objects.create(name="Kho AV", code="KHO-AV")
        self.order = make_order(self.channel, self.warehouse, "61", shipping_code="", qty=3)
        self.order.shipping_type = "direct"
        self.order.direct_dispatch_deferred = True
        self.order.save(update_fields=["shipping_type", "direct_dispatch_deferred"])
        self.item = self.order.items.first()

    def _row(self):
        from apps.inventory.application.services.stock_query_service import StockQueryService

        rows = StockQueryService.availability(
            warehouse_id=self.warehouse.id, skus=[self.item.sku]
        )
        return rows[0]

    def test_waiting_quantity_is_reported(self):
        row = self._row()
        # sellable KHÔNG đổi nghĩa — nhiều nơi khác đang đọc con số này.
        self.assertEqual(row["sellable"], 3)
        self.assertEqual(row["pending_direct"], 3)

    def test_counter_sale_is_not_counted(self):
        self.order.direct_dispatch_deferred = False
        self.order.save(update_fields=["direct_dispatch_deferred"])
        self.assertEqual(self._row()["pending_direct"], 0)

    def test_combo_is_expanded_to_components(self):
        # Sổ trừ theo mã lẻ, nên "đang chờ xuất" cũng phải tính theo mã lẻ. Cộng theo
        # SKU combo sẽ lệch đúng ở nhóm đơn dễ sai nhất (bài học MR !1317/!1326).
        from apps.catalog.models import Product
        from apps.catalog.models import ProductComponent

        combo = Product.objects.create(name="Combo AV", sku="COMBO-AV")
        part = Product.objects.create(name="Le AV", sku="LE-AV")
        ProductComponent.objects.create(parent=combo, component=part, quantity=2)

        self.item.product = combo
        self.item.sku = combo.sku
        self.item.quantity = 2
        self.item.save(update_fields=["product", "sku", "quantity"])

        from apps.inventory.application.services.stock_query_service import StockQueryService

        rows = StockQueryService.availability(
            warehouse_id=self.warehouse.id, skus=[part.sku]
        )
        self.assertEqual(rows[0]["pending_direct"], 4)  # 2 combo × 2 thành phần
```

**Lưu ý:** tên model/field của `ProductComponent` phải kiểm bằng `grep -n "class ProductComponent" -A 15 backend/apps/catalog/models.py` — `parent`/`product`/`quantity` là phỏng đoán từ docstring, sửa test cho khớp thật.

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: `python manage.py test apps.inventory.tests.test_direct_deferred_dispatch.AvailabilityPendingDirectTests -v 2`
Expected: FAIL — `KeyError: 'pending_direct'`

- [ ] **Step 3: Thêm query thứ ba vào `availability`**

Trong `stock_query_service.py`, thay phần dựng `rows` cuối hàm `availability`:

```python
        product_ids = [row["id"] for row in products]
        pending_by_product = StockQueryService._pending_direct_by_product(
            warehouse_id=warehouse_id, product_ids=product_ids
        )
        rank = {sku: index for index, sku in enumerate(skus)}
        rows = [
            {
                "sku": row["sku"],
                "product_name": row["name"],
                "sellable": int(qty_by_product.get(row["id"]) or 0),
                "pending_direct": int(pending_by_product.get(row["id"]) or 0),
            }
            for row in products
        ]
```

Và thêm hàm phụ trong cùng class:

```python
    @staticmethod
    def _pending_direct_by_product(*, warehouse_id: int, product_ids: list[int]) -> dict[int, int]:
        """SL đang chờ kho xác nhận xuất, quy về MÃ LẺ.

        Đơn "xuất sau" đã có hoá đơn nhưng chưa trừ tồn, nên `sellable` vẫn đang tính
        số hàng đó là còn bán được. Con số này để giao diện CẢNH BÁO, không phải để
        khoá — hệ thống cố ý không có khái niệm giữ chỗ trong sổ.

        Nở combo là bắt buộc: sổ trừ theo mã lẻ (`expand_desired_quantities`), cộng theo
        SKU combo thì cảnh báo lệch đúng ở nhóm đơn dễ sai nhất.
        """
        from apps.inventory.application.services.combo_expansion import ComboExpander
        from apps.orders.models import Order, OrderItem, OrderStatus, ShippingType

        lines = list(
            OrderItem.objects.filter(
                order__shipping_type=ShippingType.DIRECT,
                order__direct_dispatch_deferred=True,
                order__fulfillment_warehouse_id=warehouse_id,
                product_id__isnull=False,
                quantity__gt=0,
            )
            .exclude(order__status=OrderStatus.CANCELLED)
            .values("product_id", "quantity")
        )
        if not lines:
            return {}

        expander = ComboExpander.for_product_ids({row["product_id"] for row in lines})
        totals: dict[int, int] = {}
        wanted = set(product_ids)
        for row in lines:
            components = expander.components_of(row["product_id"])
            if components:
                for component in components:
                    pid = component["product_id"]
                    if pid in wanted:
                        totals[pid] = totals.get(pid, 0) + row["quantity"] * component["quantity"]
            elif row["product_id"] in wanted:
                totals[row["product_id"]] = totals.get(row["product_id"], 0) + row["quantity"]
        return totals
```

Thêm `pending_direct = serializers.IntegerField()` vào `StockAvailabilityRowSerializer`.

**Lưu ý:** bộ lọc trên chưa loại đơn đã xác nhận xuất. Nếu `list_pending` và hàm này lệch nhau thì con số cảnh báo sai — người thực thi phải kiểm: đơn đã confirm vẫn mang `direct_dispatch_deferred=True`, nên **phải** loại thêm bằng cùng định nghĩa "đã xuất". Cách rẻ nhất: `.exclude(order_id__in=_dispatched_order_ids(...))` dùng lại helper ở Task 5, hoặc lọc theo `order__assessment__delivery_business_status_code`. Viết thêm một test khoá ca này trước khi sửa.

- [ ] **Step 4: Viết test cho ca "đã confirm thì không còn đếm"**

```python
    def test_confirmed_order_is_no_longer_pending(self):
        from apps.inventory.application.services.direct_dispatch_service import (
            DirectDispatchService,
        )
        from apps.users.test_helpers import make_admin

        DirectDispatchService.confirm(
            order_ids=[self.order.id], actor=make_admin("admin-av-confirm")
        )
        self.assertEqual(self._row()["pending_direct"], 0)
```

- [ ] **Step 5: Chạy test để chắc chắn nó pass**

Run: `python manage.py test apps.inventory.tests.test_direct_deferred_dispatch.AvailabilityPendingDirectTests -v 2`
Expected: PASS (4 test)

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory
git commit -m "feat(inventory): report pending direct-dispatch qty in availability"
```

---

### Task 7: FE — hành động hàng loạt "Tạo nhanh (xuất sau)"

**Files:**
- Modify: `frontend/src/features/orders/use-orders-bulk-actions.ts:45-51, 189-193`
- Modify: `frontend/src/features/orders/OrdersTable/OrdersTable.tsx:394-421`
- Test: `frontend/src/features/orders/__tests__/shipping-type.test.tsx` (thêm case) hoặc file test mới cùng thư mục

**Interfaces:**
- Consumes: action `"quick_sale_deferred_selected"` (Task 2).
- Produces: người dùng bấm được menu mới; không có interface cho task sau.

- [ ] **Step 1: Viết test thất bại**

Thêm vào file test đơn hàng (đặt viewport desktop trước khi render — jsdom mặc định rơi vào nhánh thẻ mobile):

```tsx
it("gửi action quick_sale_deferred_selected khi bấm Tạo nhanh (xuất sau)", async () => {
  setDesktopViewport();
  const post = vi.fn().mockResolvedValue({ queued_count: 1, target_count: 1 });
  renderOrdersTable({ post, rows: [orderWithWarehouse] });

  await userEvent.click(screen.getByRole("button", { name: /KiotViet/i }));
  await userEvent.click(screen.getByRole("button", { name: /Tạo nhanh \(xuất sau\)/i }));

  expect(post).toHaveBeenCalledWith(
    expect.stringContaining("kiotviet-action"),
    expect.objectContaining({ action: "quick_sale_deferred_selected" }),
  );
});
```

**Lưu ý:** `setDesktopViewport`/`renderOrdersTable` là tên minh hoạ — dùng đúng helper của file test hiện có, và chốt viewport bằng `src/test/viewport.ts`.

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: `npx vitest run src/features/orders/__tests__/shipping-type.test.tsx`
Expected: FAIL — không tìm thấy nút

- [ ] **Step 3: Thêm action vào union type**

Trong `use-orders-bulk-actions.ts`:

```ts
export type KiotVietOrderAction =
  | "push_selected"
  | "push_unsent"
  | "sync_shipping_selected"
  | "sync_status_selected"
  | "sync_status_has_shipping"
  | "quick_sale_selected"
  | "quick_sale_deferred_selected";
```

Và thêm vào `isSelectedAction`:

```ts
        const isSelectedAction =
          action === "push_selected" ||
          action === "sync_shipping_selected" ||
          action === "sync_status_selected" ||
          action === "quick_sale_selected" ||
          action === "quick_sale_deferred_selected";
```

- [ ] **Step 4: Thêm mục menu**

Trong `OrdersTable.tsx`, ngay sau nút "Tạo nhanh (bán nhanh)", thêm nút thứ hai dùng **cùng** guard thiếu-kho:

```tsx
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent"
                      disabled={selectedIds.length === 0}
                      onClick={() => {
                        const missing = findOrdersWithoutWarehouse(rows, selectedIds);
                        if (missing.length > 0) {
                          toast.error(
                            t(
                              "orders.toast.quickSaleMissingWarehouse",
                              "Đơn bán trực tiếp phải có kho xuất. Thiếu kho: {codes}",
                            ).replace(
                              "{codes}",
                              missing
                                .map((order) => order.invoice_code || `#${order.id}`)
                                .join(", "),
                            ),
                          );
                          return;
                        }
                        runKiotVietAction("quick_sale_deferred_selected", "selected");
                      }}
                    >
                      <Clock className="h-4 w-4" />
                      {t("orders.kiotviet.quickSaleDeferred", "Tạo nhanh (xuất sau)")}
                    </button>
```

Import `Clock` từ `lucide-react`.

- [ ] **Step 5: Chạy test để chắc chắn nó pass**

Run: `npx vitest run src/features/orders/__tests__/shipping-type.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/orders
git commit -m "feat(orders): deferred quick-sale menu action"
```

---

### Task 8: FE — trang "Chờ xuất kho"

**Files:**
- Create: `frontend/src/features/inventory/direct-dispatch-queries.ts`
- Create: `frontend/src/features/inventory/pages/DirectDispatchPage.tsx`
- Create: `frontend/src/features/inventory/__tests__/direct-dispatch.test.tsx`
- Modify: `frontend/src/app/routed-pages.tsx`, `frontend/src/app/routes.tsx`, `frontend/src/app/modules.ts`

**Interfaces:**
- Consumes: `GET /api/inventory/direct-dispatch/pending`, `POST /api/inventory/direct-dispatch/confirm` (Task 5).
- Produces: route `/inventory/direct-dispatch`, section key `direct-dispatch`.

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/inventory/__tests__/direct-dispatch.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

describe("Trang Chờ xuất kho", () => {
  it("hiện đơn đang chờ và gọi confirm với đúng danh sách", async () => {
    // setDesktopViewport() TRƯỚC render: jsdom mặc định rơi vào nhánh thẻ mobile.
    const confirm = vi.fn().mockResolvedValue({ confirmed: [7], skipped: [] });
    renderDirectDispatchPage({
      items: [
        {
          order_id: 7,
          invoice_code: "HD-7",
          warehouse_name: "Kho A",
          waiting_days: 3,
          is_overdue: true,
          items: [{ product_id: 1, sku: "SKU-1", product_name: "SP 1", quantity: 2 }],
        },
      ],
      confirm,
    });

    expect(await screen.findByText("HD-7")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /HD-7/i }));
    await userEvent.click(screen.getByRole("button", { name: /Xác nhận đã xuất/i }));
    await userEvent.click(screen.getByRole("button", { name: /Xác nhận$/i }));

    expect(confirm).toHaveBeenCalledWith({ order_ids: [7] });
  });

  it("hiện trạng thái rỗng khi không có đơn nào", async () => {
    renderDirectDispatchPage({ items: [] });
    expect(await screen.findByText(/Không có đơn nào chờ xuất/i)).toBeInTheDocument();
  });

  it("ẩn nút xác nhận khi thiếu quyền stock:edit", async () => {
    renderDirectDispatchPage({ items: [], permissions: [] });
    expect(screen.queryByRole("button", { name: /Xác nhận đã xuất/i })).toBeNull();
  });
});
```

`renderDirectDispatchPage` là helper cục bộ của file test — dựng theo đúng khuôn helper render của test kho hiện có (`frontend/src/features/inventory/__tests__/`), có `QueryClientProvider` và mock `fetchJson`.

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: `npx vitest run src/features/inventory/__tests__/direct-dispatch.test.tsx`
Expected: FAIL — module chưa tồn tại

- [ ] **Step 3: Viết queries**

Tạo `frontend/src/features/inventory/direct-dispatch-queries.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { fetchJson } from "@/services/http";
import { getApiErrorMessage } from "@/lib/errors";

const BASE = "/api/inventory/direct-dispatch";

export type DirectDispatchLine = {
  product_id: number;
  sku: string;
  product_name: string;
  quantity: number;
};

export type DirectDispatchRow = {
  order_id: number;
  invoice_code: string;
  warehouse_id: number | null;
  warehouse_name: string;
  created_by: string;
  invoiced_at: string;
  waiting_days: number;
  is_overdue: boolean;
  items: DirectDispatchLine[];
};

export function useDirectDispatchPending(warehouseIds: string[]) {
  const query = new URLSearchParams();
  if (warehouseIds.length > 0) query.set("warehouse_ids", warehouseIds.join(","));
  const search = query.toString();
  return useQuery({
    queryKey: ["inventory", "direct-dispatch", search],
    queryFn: () =>
      fetchJson(`${BASE}/pending${search ? `?${search}` : ""}`) as Promise<{
        items: DirectDispatchRow[];
      }>,
    staleTime: 10_000,
  });
}

// Xác nhận hàng đã rời kho → TRỪ TỒN. Không phải thao tác đánh dấu suông.
export function useConfirmDirectDispatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (orderIds: number[]) =>
      fetchJson(`${BASE}/confirm`, {
        method: "POST",
        body: JSON.stringify({ order_ids: orderIds }),
      }) as Promise<{ confirmed: number[]; skipped: { order_id: number; reason: string }[] }>,
    onSuccess: (result) => {
      if (result.confirmed.length > 0) {
        toast.success(`Đã xác nhận xuất ${result.confirmed.length} đơn.`);
      }
      // Bỏ qua im lặng là cách nhanh nhất để kho tin là đã xong trong khi chưa.
      if (result.skipped.length > 0) {
        toast.warning(`${result.skipped.length} đơn bị bỏ qua — xem lại danh sách.`);
      }
      qc.invalidateQueries({ queryKey: ["inventory", "direct-dispatch"] });
    },
    onError: (err) => {
      toast.error(getApiErrorMessage(err, "Xác nhận xuất kho thất bại."));
    },
  });
}
```

- [ ] **Step 4: Viết trang**

Tạo `frontend/src/features/inventory/pages/DirectDispatchPage.tsx`. Yêu cầu bắt buộc:

- Bảng shadcn/ui: cột chọn, mã hoá đơn, kho, sản phẩm (SKU × SL), người tạo, ngày lên hoá đơn, số ngày chờ.
- Dòng `is_overdue` tô cảnh báo (`text-amber-600` + icon), **không** dùng màu đỏ (đỏ dành cho lỗi).
- Nút "Xác nhận đã xuất" chỉ hiện khi user có `stock:edit`; mở `AlertDialog` nêu rõ **"Thao tác này trừ tồn kho theo kho xuất của đơn"** trước khi gọi.
- Trạng thái rỗng: "Không có đơn nào chờ xuất kho."
- **Không** dùng `h-full` cho trang này — trang cuộn theo document như trang kho khác. Nếu sau này muốn `h-full` thì phải thêm section vào `isFullHeightSection` ở `lib/routes.ts`, nếu không `h-full` rơi về `auto` một cách câm lặng.
- Responsive: dưới `md` đổi sang danh sách thẻ, **không** lồng bảng trong `td colSpan`.

- [ ] **Step 5: Đăng ký route + menu**

`routed-pages.tsx`: thêm lazy export `DirectDispatchRoute` trỏ vào page mới, theo đúng khuôn `StockRoute`.

`routes.tsx`: thêm vào children, cạnh `inventory/dispatch-history`:

```tsx
      {
        path: "inventory/direct-dispatch",
        element: (
          <Guard section="stock">
            <DirectDispatchRoute />
          </Guard>
        ),
      },
```

`modules.ts`: thêm vào `sections` của module `/inventory`, sau `dispatch-history`:

```ts
      {
        key: "direct-dispatch",
        labelKey: "topnav.tabs.directDispatch",
        icon: Clock,
        requires: "stock",
      },
```

Import `Clock` từ `lucide-react`. Thêm chuỗi i18n `topnav.tabs.directDispatch` = "Chờ xuất kho" vào file locale tương ứng.

- [ ] **Step 6: Chạy test để chắc chắn nó pass**

Run: `npx vitest run src/features/inventory/__tests__/direct-dispatch.test.tsx`
Expected: PASS (3 test)

- [ ] **Step 7: Chạy test điều hướng để chắc không vỡ**

Run: `npx vitest run src/app/__tests__/nav-layout.test.ts`
Expected: PASS — file này thường assert danh sách section, phải cập nhật nếu nó đếm tuyệt đối.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/inventory frontend/src/app
git commit -m "feat(inventory): pending direct-dispatch page"
```

---

### Task 9: FE — hiển thị "chờ xuất" trên form đơn

**Files:**
- Modify: `frontend/src/features/orders/order-form/OrderFormStockSummary.tsx:52` (vòng lặp render `stock.rows`)
- Test: `frontend/src/features/orders/__tests__/OrderFormDialog.stock-availability.test.tsx`

**Interfaces:**
- Consumes: field `pending_direct` (Task 6).
- Produces: không có.

**Phụ thuộc bắt buộc:** `StockAvailabilityRow` là **type sinh từ OpenAPI**
(`components["schemas"]["StockAvailabilityRow"]` trong `frontend/src/lib/api/stock-availability.ts`),
không phải type viết tay. Phải chạy regen schema (Task 10 bước 1-2) **trước** task này,
nếu không `tsc` sẽ báo `pending_direct` không tồn tại. Đây là ràng buộc thứ tự duy nhất
đi ngược số thứ tự task trong plan.

- [ ] **Step 1: Viết test thất bại**

```tsx
it("hiện số đang chờ xuất cạnh tồn khả dụng", async () => {
  renderOrderFormStock({ items: [{ sku: "SKU-1", product_name: "SP 1", sellable: 12, pending_direct: 3 }] });
  expect(await screen.findByText(/Tồn 12/)).toBeInTheDocument();
  expect(screen.getByText(/chờ xuất 3/)).toBeInTheDocument();
});

it("không hiện phần chờ xuất khi bằng 0", async () => {
  renderOrderFormStock({ items: [{ sku: "SKU-1", product_name: "SP 1", sellable: 12, pending_direct: 0 }] });
  expect(screen.queryByText(/chờ xuất/)).toBeNull();
});
```

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: `npx vitest run src/features/orders/__tests__/OrderFormDialog.stock-availability.test.tsx`
Expected: FAIL

- [ ] **Step 3: Thêm hiển thị**

Trong `OrderFormStockSummary.tsx`, ở vòng lặp `stock.rows.map`, render thêm phần chờ
xuất cạnh số tồn: `Tồn {row.sellable} (chờ xuất {row.pending_direct})` — phần trong
ngoặc **chỉ hiện khi > 0**, dùng `text-amber-600`. Đây là **cảnh báo**, không chặn chọn
hàng: hệ thống cố ý không có khái niệm giữ chỗ. Không đụng vào `useOrderFormStock` —
type đã tự có field mới sau regen.

- [ ] **Step 4: Chạy test để chắc chắn nó pass**

Run: `npx vitest run src/features/orders/__tests__/OrderFormDialog.stock-availability.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(orders): show pending direct-dispatch qty on order form"
```

---

### Task 10: Đóng gói — schema, lint, full test, MR

**Files:**
- Modify: `backend/schema.yaml` (regen)
- Modify: `frontend/src/api/schema.d.ts` (regen)

**Interfaces:**
- Consumes: tất cả task trước.
- Produces: nhánh sẵn sàng mở MR.

- [ ] **Step 1: Regen schema backend**

Run: `python manage.py spectacular --file schema.yaml`

`schema.yaml` đã cũ sẵn ~444 dòng so với code, nên regen sẽ kéo theo nhiễu của feature khác. Xem `git diff backend/schema.yaml` và **chỉ giữ** phần thuộc MR này (2 path `direct-dispatch/*`, component `DirectDispatchPending*`, field `pending_direct`, action enum mới). `git checkout -p` phần còn lại.

- [ ] **Step 2: Regen type frontend**

Run: `npm run generate:api` (kiểm tên script thật trong `frontend/package.json`)

Áp cùng luật lọc nhiễu như bước 1.

- [ ] **Step 3: Lint backend**

Run: `ruff check backend && ruff format --check backend && lint-imports --config .importlinter`
Expected: sạch

- [ ] **Step 4: Lint + typecheck frontend**

Run: `npx tsc --noEmit && npx eslint src --max-warnings 0`
Expected: sạch. **Lưu ý:** `src/features/audit/__tests__/audit-logs-api.test.ts` đỏ tsc **sẵn trên master** (`.at()` vs lib < es2022) — nếu chỉ lỗi đó thì không phải do nhánh này.

- [ ] **Step 5: Chạy full suite backend**

Run: `python manage.py test apps -v 1` (~19 phút — chạy nền, redirect ra file log, đừng để shell nền bị kill làm mất kết quả)
Expected: chỉ còn các test đỏ đã biết trên master. Đối chiếu với worktree sạch trước khi sửa bất cứ test nào không thuộc phạm vi.

- [ ] **Step 6: Chạy full suite frontend**

Run: `npx vitest run`
Expected: xanh (baseline master hiện 100%)

- [ ] **Step 7: Prettier đúng file mình chạm**

Run: `npx prettier --write <danh sách file FE đã sửa>`
Chỉ đích danh file của MR — chạy toàn repo sẽ tạo nhiễu format không thuộc MR.

- [ ] **Step 8: Push và mở MR**

```bash
git push -o merge_request.create -o merge_request.title="Direct sale deferred dispatch" -u origin feat/direct-sale-deferred-dispatch
```

MR description **phải** ghi rõ:
- Thứ tự deploy: **migrate trước, roll code sau** (lý do: `_is_direct_sale` chạy trong handler huỷ của mọi đơn).
- Migration index chạy **giờ thấp điểm**, kiểm `pg_index.indisvalid` sau khi xong.
- Sau deploy hành vi mặc định **không đổi**; chỉ đơn bấm nút mới đi luồng mới.
- Việc cần làm sau deploy: đếm số đơn đi luồng mới mỗi ngày để kiểm chứng giả định tần suất.

---

## Ghi chú cho người thực thi

- **Đừng sửa test cho khớp code sai.** Vài chữ ký trong plan này (`KiotVietChannel`, tên script npm) là suy ra từ đọc code xung quanh chứ chưa chạy. ĐÃ xác minh và sửa sẵn: URL `/api/orders/kiotviet-actions` (số nhiều), `ProductComponent(parent=, component=, quantity=)`, `OrderStatusChanged(order_id=, old_status=, new_status=)`, `OrderStatus.CANCELLED = 6`, `ComboExpander.for_product_ids/components_of`. Gặp lệch thì kiểm bằng `grep` rồi sửa **test**, giữ nguyên ý định đang được khoá.
- **Đừng gom nhiều task vào một commit.** Mỗi task là một điểm mà người review có thể chặn riêng.
- **Test đỏ sẵn trên master** (đã biết): `audit-logs-api.test.ts` (tsc), test N+1 export đơn hàng. Gặp thì kiểm chứng trên worktree sạch trước khi động vào.
