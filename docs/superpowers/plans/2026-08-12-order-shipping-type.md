# Order `shipping_type` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tách đơn giao vận (`shipment`) và đơn bán trực tiếp (`direct`); đơn `direct` trừ tồn ngay khi tạo hoá đơn KiotViet theo kho xuất đã chọn, và hoàn tồn ngay khi huỷ.

**Architecture:** Thêm cột `Order.shipping_type` (nullable, choices). Hệ thống tự gắn cờ tại khối hậu xử lý job KiotViet trong app `orders` — nơi đã có sẵn ba điều kiện "quick-sale thành công / push thành công / kship thành công". Đơn `direct` trừ tồn bằng cách tái dùng đúng đường `delivery_business_status_code = "dispatched"` đã có (idempotent, có guard huỷ đơn, có ghi sổ). Huỷ đơn `direct` thì subscriber kho hoàn tồn ngay thay vì mở hàng đợi chờ hàng về.

**Tech Stack:** Django 5 + DRF + PostgreSQL, Celery (qua `apps.dispatch`), React + TypeScript + Vite, pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-08-12-order-shipping-type-design.md`

## Global Constraints

- Worktree: `C:/Users/MYPC/Desktop/workspace/desi-order-wt-shipping-type`, nhánh `feat/order-shipping-type`. Không tạo nhánh khác.
- Giá trị hợp lệ, viết đúng từng ký tự: `"shipment"`, `"direct"`. `NULL` là trạng thái thứ ba hợp lệ (chưa xác định).
- Không backfill đơn cũ. Không sửa luật tồn kho của đơn giao vận.
- `shipping_type` là read-only với client: không thêm vào `OrderCreateSerializer` hay `OrderInlineUpdateSerializer`.
- Không ghi đè giá trị đã có: chỉ set `shipment` khi đang `NULL`.
- Python: `ruff check .` phải sạch (ruleset trong `backend/pyproject.toml`). Không `print`.
- Bình luận code viết tiếng Việt, giải thích **tại sao**, theo văn phong file đang sửa. Commit message tiếng Anh, ngắn.
- Không regen `backend/schema.yaml` trong nhánh này (master đang stale sẵn, regen sẽ kéo nhiễu của feature khác).

### Lệnh chạy test backend

Chạy từ thư mục worktree. Đổi `POSTGRES_DB` mỗi lần chạy song song để không đụng nhau:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_shiptype -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Bỏ `--create-db` ở các lần chạy sau để tiết kiệm ~2 phút migrate (chỉ bỏ khi KHÔNG thêm migration mới).

Lint: `docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .`

### Lệnh chạy test frontend

```bash
cd frontend && npx vitest run <paths>
```

Worktree này nằm ngang hàng repo chính nên có thể tạo junction `node_modules` sang worktree anh em; nếu không có thì `npm ci`.

## File Structure

**Backend**

| File | Trách nhiệm |
|---|---|
| `backend/apps/orders/models.py` | `ShippingType` choices + field `Order.shipping_type` |
| `backend/apps/orders/migrations/00XX_order_shipping_type.py` | Thêm cột nullable |
| `backend/apps/orders/migrations/00XY_order_shipping_type_index.py` | `AddIndexConcurrently`, `atomic = False` |
| `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py` | Phơi field ở serializer đọc |
| `backend/apps/orders/transport/views/order_views_impl/order_action_view.py` | Guard 400 cho `quick_sale_selected` thiếu kho |
| `backend/apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py` | Guard trước khi gọi KiotViet; gắn cờ + trừ tồn sau khi job thành công |
| `backend/apps/inventory/subscribers.py` | Huỷ đơn `direct` → hoàn tồn ngay |
| `backend/apps/orders/transport/views/order_views_impl/_view_helpers.py` | Nhận filter `shipping_type` |
| `backend/apps/orders/repositories.py` | Áp filter vào queryset |
| `backend/apps/orders/export_excel.py` | Cột "Loại xuất hàng" trong file Excel |
| `backend/apps/orders/tests/test_direct_sale_shipping_type.py` | **Mới** — toàn bộ vòng đời đơn direct |

**Frontend**

| File | Trách nhiệm |
|---|---|
| `frontend/src/features/orders/order-types.ts` | Type `shipping_type`, column key, nhãn cột |
| `frontend/src/features/orders/OrdersTable/OrderCell.tsx` | Render badge |
| `frontend/src/i18n/messages/orders.ts` | Chuỗi tiếng Việt |
| `frontend/src/features/orders/orders-page/use-orders-filter-options.ts` | Options bộ lọc |
| `frontend/src/features/orders/OrdersFilterPanel.tsx` | Ô lọc |
| `frontend/src/pages/OrdersPage.tsx` | State + payload bộ lọc |
| `frontend/src/features/orders/use-orders-data.ts` | Gửi filter lên API |
| `frontend/src/features/orders/OrdersTable/OrdersTable.tsx` | Chặn "Tạo nhanh" khi đơn thiếu kho |
| `frontend/src/features/orders/__tests__/shipping-type.test.tsx` | **Mới** — badge + guard |

---

### Task 1: Model, choices, migrations

**Files:**
- Modify: `backend/apps/orders/models.py` (thêm `ShippingType` cạnh `OrderVat`, thêm field vào `Order` cạnh `fulfillment_warehouse`)
- Create: `backend/apps/orders/migrations/00XX_order_shipping_type.py` (số thứ tự do `makemigrations` sinh)
- Create: `backend/apps/orders/migrations/00XY_order_shipping_type_index.py`
- Test: `backend/apps/orders/tests/test_direct_sale_shipping_type.py`

**Interfaces:**
- Consumes: không có.
- Produces: `apps.orders.models.ShippingType` với `ShippingType.SHIPMENT == "shipment"`, `ShippingType.DIRECT == "direct"`; `Order.shipping_type` (`str | None`).

- [ ] **Step 1: Write the failing test**

Tạo file `backend/apps/orders/tests/test_direct_sale_shipping_type.py`:

```python
"""Vòng đời đơn bán trực tiếp (shipping_type=direct).

Một file cho cả vòng đời thay vì rải test theo từng hàm: giá trị của cờ này nằm ở chỗ
nó nối "tạo hoá đơn nhanh" với "trừ tồn" và "hoàn tồn khi huỷ" — tách ra thì mỗi test
đúng một mảnh mà đường nối vẫn có thể gãy.
"""
from django.test import TestCase

from apps.channels_core.models import Channel
from apps.orders.models import Order, ShippingType


class ShippingTypeFieldTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="st-ch", name="Shipping Type Channel")

    def test_new_order_has_no_shipping_type(self):
        # NULL = "chưa biết đơn này đi đường nào", khác hẳn shipment. Đơn cũ giữ NULL
        # vĩnh viễn nên mọi chỗ đọc cờ đều phải chịu được None.
        order = Order.objects.create(channel=self.channel, name="A", phone="0900000001")
        self.assertIsNone(order.shipping_type)

    def test_shipping_type_accepts_both_values(self):
        order = Order.objects.create(
            channel=self.channel, name="B", phone="0900000002",
            shipping_type=ShippingType.DIRECT,
        )
        order.refresh_from_db()
        self.assertEqual(order.shipping_type, "direct")

        order.shipping_type = ShippingType.SHIPMENT
        order.save(update_fields=["shipping_type"])
        order.refresh_from_db()
        self.assertEqual(order.shipping_type, "shipment")
```

- [ ] **Step 2: Run test to verify it fails**

Run: lệnh docker ở Global Constraints với `<paths>` = `apps/orders/tests/test_direct_sale_shipping_type.py`
Expected: FAIL — `ImportError: cannot import name 'ShippingType'`

- [ ] **Step 3: Add choices and field**

Trong `backend/apps/orders/models.py`, ngay sau `class OrderVat(models.TextChoices)`:

```python
class ShippingType(models.TextChoices):
    """Hình thức xuất hàng của đơn.

    NULL (chưa xác định) là trạng thái thứ ba, hợp lệ: đơn tạo trước tính năng này và
    đơn chưa qua thao tác nào của người dùng đều mang NULL. Không dùng chuỗi rỗng —
    chuỗi rỗng lẫn với dữ liệu nhập thiếu.
    """

    SHIPMENT = "shipment", "Shipment"  # Gửi vận chuyển
    DIRECT = "direct", "Direct"  # Bán trực tiếp tại quầy
```

Trong `class Order`, ngay dưới field `fulfillment_warehouse`:

```python
    # Cờ do HỆ THỐNG gắn theo thao tác thật (xem ingest_job_tasks), không cho sửa tay:
    # nó quyết định thời điểm trừ tồn và luật hoàn tồn khi huỷ, sửa tay là sửa luật kho
    # của một đơn đã ghi sổ.
    shipping_type = models.CharField(
        max_length=16, choices=ShippingType.choices, null=True, blank=True, default=None
    )
```

- [ ] **Step 4: Generate the column migration**

Run:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_shiptype -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py makemigrations orders --name order_shipping_type
```

Expected: sinh file `apps/orders/migrations/00XX_order_shipping_type.py` chỉ chứa một `AddField`. Cột nullable không default → PostgreSQL không rewrite bảng.

- [ ] **Step 5: Write the index migration by hand**

Tạo `backend/apps/orders/migrations/00XY_order_shipping_type_index.py` (`00XX` = tên file vừa sinh ở Step 4):

```python
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    """Index cho bộ lọc shipping_type trên trang Đơn hàng.

    CONCURRENTLY vì bảng orders lớn: CREATE INDEX thường khoá ghi suốt thời gian quét
    bảng. Đổi lại migration này KHÔNG chạy trong transaction (atomic = False) — hỏng
    giữa chừng sẽ để lại index INVALID, phải DROP rồi chạy lại.

    Cột ghép với created_at DESC vì bộ lọc luôn đi kèm khoảng thời gian và danh sách
    đơn sắp xếp theo created_at giảm dần.
    """

    atomic = False

    dependencies = [
        ("orders", "00XX_order_shipping_type"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="order",
            index=models.Index(
                fields=["shipping_type", "-created_at"], name="order_shiptype_created_idx"
            ),
        ),
    ]
```

Thêm index tương ứng vào `Order.Meta.indexes` trong `models.py` để state khớp:

```python
            models.Index(fields=["shipping_type", "-created_at"], name="order_shiptype_created_idx"),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: docker pytest với `apps/orders/tests/test_direct_sale_shipping_type.py --create-db`
Expected: 2 passed

- [ ] **Step 7: Verify no migration drift**

Run: `python manage.py makemigrations --check --dry-run` (trong cùng docker command như Step 4)
Expected: "No changes detected"

- [ ] **Step 8: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations backend/apps/orders/tests/test_direct_sale_shipping_type.py
git commit -m "feat(orders): add shipping_type field with concurrent index"
```

---

### Task 2: Phơi field ra API đọc

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/order_core_serializers.py` (thêm `"shipping_type"` vào `Meta.fields` của `OrderSerializer`, cạnh `"fulfillment_warehouse"`)
- Test: `backend/apps/orders/tests/test_direct_sale_shipping_type.py`

**Interfaces:**
- Consumes: `Order.shipping_type` (Task 1).
- Produces: response của `GET /api/orders/<id>` và list đơn có khoá `shipping_type` với giá trị `"direct" | "shipment" | null`.

- [ ] **Step 1: Write the failing test**

Thêm vào `test_direct_sale_shipping_type.py`:

```python
from rest_framework.test import APIClient

from apps.orders.models import OrderStatus
from apps.users.test_helpers import make_admin


class ShippingTypeApiExposureTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-shipping-type")
        self.channel = Channel.objects.create(code="st-api", name="Shipping Type Api")

    def test_order_detail_exposes_shipping_type(self):
        order = Order.objects.create(
            channel=self.channel, name="C", phone="0900000003",
            status=OrderStatus.NEW, shipping_type=ShippingType.DIRECT,
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/orders/{order.id}")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["shipping_type"], "direct")

    def test_shipping_type_is_read_only_on_patch(self):
        # Cờ do hệ thống gắn: client PATCH thẳng vào phải bị bỏ qua, không phải chấp nhận.
        order = Order.objects.create(
            channel=self.channel, name="D", phone="0900000004", status=OrderStatus.NEW,
        )
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/orders/{order.id}", {"shipping_type": "direct"}, format="json"
        )
        self.assertIn(response.status_code, (200, 400))
        order.refresh_from_db()
        self.assertIsNone(order.shipping_type)
```

- [ ] **Step 2: Run test to verify it fails**

Run: docker pytest `apps/orders/tests/test_direct_sale_shipping_type.py -q`
Expected: FAIL — `KeyError: 'shipping_type'` ở test đầu

- [ ] **Step 3: Add the field to the read serializer**

Trong `order_core_serializers.py`, `class OrderSerializer.Meta.fields`, ngay sau `"fulfillment_warehouse_name",`:

```python
            "shipping_type",
```

KHÔNG thêm vào `LIST_EXCLUDED_ORDER_FIELDS` — cột "Loại xuất hàng" của trang danh sách cần field này, và nó là một chuỗi ngắn nên không đáng để cắt.

- [ ] **Step 4: Run tests to verify they pass**

Run: docker pytest `apps/orders/tests/test_direct_sale_shipping_type.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders
git commit -m "feat(orders): expose shipping_type on read serializer"
```

---

### Task 3: Chặn "Tạo nhanh" khi đơn thiếu kho xuất (API)

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_action_view.py:739-743`
- Test: `backend/apps/orders/tests/test_direct_sale_shipping_type.py`

**Interfaces:**
- Consumes: `Order.fulfillment_warehouse_id`.
- Produces: `POST /api/orders/kiotviet-action` với `action="quick_sale_selected"` trả 400 và body `{"detail": ..., "orders_without_warehouse": [<order_id>, ...]}` khi có đơn thiếu kho.

- [ ] **Step 1: Write the failing test**

```python
from unittest.mock import patch


class QuickSaleWarehouseGuardTests(TestCase):
    def setUp(self):
        from apps.kiotviet.models import KiotVietChannel
        from apps.warehouses.models import Warehouse

        self.client = APIClient()
        self.admin = make_admin("admin-quick-sale-guard")
        self.channel = Channel.objects.create(code="st-qs", name="Quick Sale Channel")
        self.kv_channel = KiotVietChannel.objects.create(
            name="KV QS", branch_id="1", branch_ids=["1"],
            client_id="c", client_secret="s", retailer="r",
        )
        self.warehouse = Warehouse.objects.create(code="KHO-QS", name="Kho Quick Sale")

    def _make_order(self, phone, warehouse=None):
        return Order.objects.create(
            channel=self.channel, kiotviet_channel=self.kv_channel,
            name="QS", phone=phone, status=OrderStatus.NEW,
            fulfillment_warehouse=warehouse,
        )

    def test_quick_sale_rejects_order_without_warehouse(self):
        order = self._make_order("0900000005", warehouse=None)
        self.client.force_authenticate(user=self.admin)
        with patch("apps.orders.transport.views.order_views_impl.order_action_view.dispatch") as mock_dispatch:
            response = self.client.post(
                "/api/orders/kiotviet-action",
                {"action": "quick_sale_selected", "step": "execute", "ids": [order.id]},
                format="json",
            )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["orders_without_warehouse"], [order.id])
        # Không được tạo job: hoá đơn KiotViet tạo ra mà tồn không trừ là ca tệ nhất.
        mock_dispatch.assert_not_called()

    def test_quick_sale_allows_order_with_warehouse(self):
        order = self._make_order("0900000006", warehouse=self.warehouse)
        self.client.force_authenticate(user=self.admin)
        with patch("apps.orders.transport.views.order_views_impl.order_action_view.dispatch"):
            response = self.client.post(
                "/api/orders/kiotviet-action",
                {"action": "quick_sale_selected", "step": "execute", "ids": [order.id]},
                format="json",
            )
        self.assertEqual(response.status_code, 202, response.data)
```

Nếu URL `/api/orders/kiotviet-action` không khớp, tra tên thật bằng `grep -n "run_kiotviet_action" backend/apps/orders/urls.py` và sửa trong test.

- [ ] **Step 2: Run test to verify it fails**

Run: docker pytest `apps/orders/tests/test_direct_sale_shipping_type.py::QuickSaleWarehouseGuardTests -q`
Expected: FAIL — trả 202 thay vì 400

- [ ] **Step 3: Add the guard**

Trong `order_action_view.py`, thay khối:

```python
    if action == OrderKiotVietActionSerializer.ACTION_QUICK_SALE_SELECTED:
        if any(order.kiotviet_channel_id is None for order in order_list):
            return Response({"detail": "Đơn hàng chưa được gán kênh KiotViet."}, status=status.HTTP_400_BAD_REQUEST)
        # Quick sale: chỉ chuyển sang "Giao thành công" SAU khi tạo hóa đơn KV
        # thành công, không set eager ở đây để tránh sai trạng thái.
```

bằng:

```python
    if action == OrderKiotVietActionSerializer.ACTION_QUICK_SALE_SELECTED:
        if any(order.kiotviet_channel_id is None for order in order_list):
            return Response({"detail": "Đơn hàng chưa được gán kênh KiotViet."}, status=status.HTTP_400_BAD_REQUEST)
        # Bán trực tiếp trừ tồn ngay theo kho xuất của đơn. Thiếu kho mà vẫn cho chạy
        # thì hoá đơn KiotViet được tạo trong khi tồn không trừ — nghiệp vụ coi như
        # xong còn kho sai âm thầm. Chặn TRƯỚC khi tạo job, không phải sau.
        orders_without_warehouse = [
            order.id for order in order_list if not order.fulfillment_warehouse_id
        ]
        if orders_without_warehouse:
            return Response(
                {
                    "detail": "Đơn bán trực tiếp phải có kho xuất. Hãy chọn kho xuất trước khi tạo nhanh.",
                    "orders_without_warehouse": orders_without_warehouse,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Quick sale: chỉ chuyển sang "Giao thành công" SAU khi tạo hóa đơn KV
        # thành công, không set eager ở đây để tránh sai trạng thái.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: docker pytest `apps/orders/tests/test_direct_sale_shipping_type.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders
git commit -m "feat(orders): block quick sale for orders without warehouse"
```

---

### Task 4: Worker gắn cờ `direct` và trừ tồn

**Files:**
- Modify: `backend/apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py` (guard trước `_execute_kiotviet_action`; gắn cờ + trừ tồn trong khối hậu xử lý cạnh `auto_flip_source`)
- Test: `backend/apps/orders/tests/test_direct_sale_shipping_type.py`

**Interfaces:**
- Consumes: `ShippingType` (Task 1), `OrderServiceCommands.update_order_inline(order, data, actor, *, verdict=None, occurred_at=None)`, `apps.orders.contracts.kiotviet_cancellation_port.CancelVerdict`.
- Produces: hàm module-level `_apply_shipping_type_after_job(job, order) -> None` trong `ingest_job_tasks.py` — Task 5 mở rộng chính hàm này.

- [ ] **Step 1: Write the failing test**

```python
class DirectSaleStockTests(TestCase):
    def setUp(self):
        from apps.kiotviet.models import KiotVietChannel
        from apps.orders.models import OrderItem, Product
        from apps.warehouses.models import Warehouse

        self.admin = make_admin("admin-direct-stock")
        self.channel = Channel.objects.create(code="st-ds", name="Direct Sale Channel")
        self.kv_channel = KiotVietChannel.objects.create(
            name="KV DS", branch_id="1", branch_ids=["1"],
            client_id="c", client_secret="s", retailer="r",
        )
        self.warehouse = Warehouse.objects.create(code="KHO-DS", name="Kho Direct Sale")
        self.product = Product.objects.create(sku="SKU-DS", name="San pham DS")
        self.order = Order.objects.create(
            channel=self.channel, kiotviet_channel=self.kv_channel,
            name="Direct", phone="0900000007", status=OrderStatus.NEW,
            fulfillment_warehouse=self.warehouse,
        )
        OrderItem.objects.create(order=self.order, product=self.product, quantity=2)

    def _run_job(self, action="create_invoice", fake_invoice_code="HD-1"):
        from apps.orders.models import OrderKiotVietJob
        from apps.orders.infrastructure.tasks.order_tasks_impl import ingest_job_tasks

        job = OrderKiotVietJob.objects.create(
            order=self.order, action=action, requested_by=self.admin
        )

        def fake_execute(order, job_action, **kwargs):
            if fake_invoice_code:
                Order.objects.filter(pk=order.pk).update(invoice_code=fake_invoice_code)
            return {"status": "created", "payload": {"status": "created"}, "update_fields": []}

        with patch.object(ingest_job_tasks, "_execute_kiotviet_action", side_effect=fake_execute):
            ingest_job_tasks.process_kiotviet_job(job.id)
        return job

    def test_quick_sale_marks_direct_and_issues_stock(self):
        from apps.inventory.models import StockMovement

        self._run_job()
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipping_type, "direct")
        self.assertEqual(self.order.assessment.delivery_business_status_code, "dispatched")
        movements = StockMovement.objects.filter(
            order_id=self.order.id, warehouse_id=self.warehouse.id
        )
        self.assertTrue(movements.exists())
        self.assertEqual(sum(m.quantity for m in movements), -2)

    def test_quick_sale_is_idempotent_when_job_runs_twice(self):
        from apps.inventory.models import StockMovement

        self._run_job()
        self._run_job()
        self.order.refresh_from_db()
        movements = StockMovement.objects.filter(order_id=self.order.id)
        self.assertEqual(sum(m.quantity for m in movements), -2)

    def test_worker_refuses_quick_sale_without_warehouse(self):
        from apps.orders.models import OrderKiotVietJob, OrderKiotVietJobStatus
        from apps.orders.infrastructure.tasks.order_tasks_impl import ingest_job_tasks

        # Kho bị gỡ SAU khi job đã vào hàng đợi — lớp guard ở view không bắt được ca này.
        Order.objects.filter(pk=self.order.pk).update(fulfillment_warehouse=None)
        job = OrderKiotVietJob.objects.create(
            order=self.order, action="create_invoice", requested_by=self.admin
        )
        with patch.object(ingest_job_tasks, "_execute_kiotviet_action") as mock_exec:
            with self.assertRaises(Exception):
                ingest_job_tasks.process_kiotviet_job(job.id)
        mock_exec.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, OrderKiotVietJobStatus.FAILED)
        self.assertIn("kho xuất", job.error_message.lower())
```

Kiểm tra dấu của `StockMovement.quantity` cho bút toán xuất bằng `grep -n "quantity" backend/apps/inventory/application/services/stock_sales_service.py | head -20` trước khi chạy; nếu hệ thống lưu số dương kèm `movement_type`, đổi assert sang lọc theo `movement_type` thay vì dấu âm.

- [ ] **Step 2: Run test to verify it fails**

Run: docker pytest `apps/orders/tests/test_direct_sale_shipping_type.py::DirectSaleStockTests -q`
Expected: FAIL — `shipping_type` vẫn `None`, không có `StockMovement`

- [ ] **Step 3: Add the pre-flight guard**

Trong `ingest_job_tasks.py`, ngay sau khối `order = Order.objects.select_related("channel").get(id=order_id)` và **trước** `execution = _execute_kiotviet_action(`:

```python
        # Chốt cuối trước khi gọi KiotViet: đơn bán nhanh phải có kho xuất vì nó trừ tồn
        # ngay sau khi hoá đơn được tạo. View đã chặn lúc bấm nút, nhưng kho có thể bị gỡ
        # trong lúc job nằm hàng đợi — đọc DB tươi ở đây mới bịt được khe đó. Task không
        # cấu hình autoretry nên lỗi này không lặp vô hạn.
        if job.action == "create_invoice" and not order.fulfillment_warehouse_id:
            raise ValueError(
                "Đơn bán trực tiếp chưa có kho xuất — không tạo hoá đơn để tránh bán mà không trừ tồn."
            )
```

- [ ] **Step 4: Add the flag + stock hook**

Thêm hàm module-level trong `ingest_job_tasks.py` (đặt ngay trên `process_kiotviet_job`):

```python
def _apply_shipping_type_after_job(job, order) -> None:
    """Gắn cờ hình thức xuất hàng theo thao tác vừa thành công, và trừ tồn cho đơn direct.

    Ba mốc gom về một chỗ vì chúng là cùng một câu hỏi: đơn này rời kho bằng đường nào.
    `direct` trừ tồn ngay tại đây thay vì chờ kho quét — đơn bán tại quầy không bao giờ
    được quét, nên không có cờ này thì tồn phồng đúng bằng lượng hàng bán trực tiếp.
    """
    from apps.orders.contracts.kiotviet_cancellation_port import CancelVerdict
    from apps.orders.models import ShippingType

    if job.action == "create_invoice" and str(order.invoice_code or "").strip():
        if order.shipping_type != ShippingType.DIRECT:
            order.shipping_type = ShippingType.DIRECT
            order.save(update_fields=["shipping_type", "updated_at"])
        # Tái dùng đúng đường trừ tồn của đơn giao vận: đã idempotent (no-op khi đơn
        # đã xuất), đã có guard đơn huỷ, đã ghi sổ StockMovement nên màn "Lịch sử nhập
        # xuất" thấy đơn bán trực tiếp như mọi đơn khác.
        #
        # verdict=OK: KiotViet vừa trả hoá đơn SỐNG cho chính đơn này vài giây trước —
        # bằng chứng dương, khỏi round-trip HTTP hỏi lại. Lớp đọc DB tươi trong guard
        # vẫn chạy nguyên vẹn nên webhook huỷ rơi đúng khe đó vẫn chặn được.
        OrderServiceCommands.update_order_inline(
            order,
            {"crm": {"delivery_business_status_code": "dispatched"}},
            job.requested_by,
            verdict=CancelVerdict.OK,
            occurred_at=order.kiotviet_purchase_date,
        )
        return

    if order.shipping_type is not None:
        # Không ghi đè: đơn đã direct (đã trừ tồn) mà bị push lại sẽ mang nhãn shipment
        # trong khi sổ ghi theo luật direct — nhãn nói dối sổ.
        return
    if job.action == "push" and str(order.order_code or "").strip():
        order.shipping_type = ShippingType.SHIPMENT
        order.save(update_fields=["shipping_type", "updated_at"])
```

Gọi nó trong khối hậu xử lý, ngay TRƯỚC đoạn tính `auto_flip_source` (sau `order.refresh_from_db()`):

```python
        if job.status == OrderKiotVietJobStatus.SUCCESS:
            _apply_shipping_type_after_job(job, order)
            order.refresh_from_db()
```

Import `OrderServiceCommands` theo đúng cách file này đang dùng (kiểm tra đầu file: nếu đã có `from apps.orders.services import OrderService` thì dùng `OrderService` — nó là facade cùng hàm; xác nhận bằng `grep -n "update_order_inline" backend/apps/orders/services.py`).

- [ ] **Step 5: Run tests to verify they pass**

Run: docker pytest `apps/orders/tests/test_direct_sale_shipping_type.py -q`
Expected: 9 passed

- [ ] **Step 6: Run the neighbouring suites for regressions**

Run: docker pytest `apps/orders/tests/test_ingest_api.py apps/orders/tests/test_dispatch_stock.py apps/kiotviet/tests -q`
Expected: tất cả pass

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders
git commit -m "feat(orders): mark direct sales and issue stock on quick sale"
```

---

### Task 5: Gắn `shipment` khi tạo vận đơn KShip

**Files:**
- Modify: `backend/apps/orders/infrastructure/tasks/order_tasks_impl/ingest_job_tasks.py` (mở rộng `_apply_shipping_type_after_job`)
- Test: `backend/apps/orders/tests/test_direct_sale_shipping_type.py`

**Interfaces:**
- Consumes: `_apply_shipping_type_after_job(job, order)` (Task 4).
- Produces: không có interface mới.

- [ ] **Step 1: Write the failing test**

```python
class ShipmentFlagTests(TestCase):
    def setUp(self):
        from apps.kiotviet.models import KiotVietChannel

        self.admin = make_admin("admin-shipment-flag")
        self.channel = Channel.objects.create(code="st-sf", name="Shipment Flag Channel")
        self.kv_channel = KiotVietChannel.objects.create(
            name="KV SF", branch_id="1", branch_ids=["1"],
            client_id="c", client_secret="s", retailer="r",
        )

    def _run(self, order, action, mutate):
        from apps.orders.models import OrderKiotVietJob
        from apps.orders.infrastructure.tasks.order_tasks_impl import ingest_job_tasks

        job = OrderKiotVietJob.objects.create(order=order, action=action, requested_by=self.admin)

        def fake_execute(o, job_action, **kwargs):
            Order.objects.filter(pk=o.pk).update(**mutate)
            return {"status": "created", "payload": {"status": "created"}, "update_fields": []}

        with patch.object(ingest_job_tasks, "_execute_kiotviet_action", side_effect=fake_execute):
            ingest_job_tasks.process_kiotviet_job(job.id)

    def _order(self, phone, **kwargs):
        return Order.objects.create(
            channel=self.channel, kiotviet_channel=self.kv_channel,
            name="SF", phone=phone, status=OrderStatus.NEW, **kwargs
        )

    def test_kship_delivery_marks_shipment(self):
        order = self._order("0900000008")
        self._run(order, "create_kship_delivery", {"shipping_code": "KS-1"})
        order.refresh_from_db()
        self.assertEqual(order.shipping_type, "shipment")

    def test_push_marks_shipment(self):
        order = self._order("0900000009")
        self._run(order, "push", {"order_code": "DH-1"})
        order.refresh_from_db()
        self.assertEqual(order.shipping_type, "shipment")

    def test_push_does_not_overwrite_direct(self):
        order = self._order("0900000010", shipping_type="direct")
        self._run(order, "push", {"order_code": "DH-2"})
        order.refresh_from_db()
        self.assertEqual(order.shipping_type, "direct")
```

- [ ] **Step 2: Run test to verify it fails**

Run: docker pytest `apps/orders/tests/test_direct_sale_shipping_type.py::ShipmentFlagTests -q`
Expected: FAIL ở `test_kship_delivery_marks_shipment` — `None != "shipment"`

- [ ] **Step 3: Extend the helper**

Trong `_apply_shipping_type_after_job`, đổi nhánh cuối thành:

```python
    if job.action == "push" and str(order.order_code or "").strip():
        order.shipping_type = ShippingType.SHIPMENT
        order.save(update_fields=["shipping_type", "updated_at"])
    elif job.action == "create_kship_delivery" and str(order.shipping_code or "").strip():
        order.shipping_type = ShippingType.SHIPMENT
        order.save(update_fields=["shipping_type", "updated_at"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: docker pytest `apps/orders/tests/test_direct_sale_shipping_type.py -q`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders
git commit -m "feat(orders): mark shipment on push and kship delivery"
```

---

### Task 6: Huỷ đơn `direct` → hoàn tồn ngay

**Files:**
- Modify: `backend/apps/inventory/subscribers.py` (`_on_order_status_changed`, nhánh `event.new_status in _RELEASE_STATUSES`)
- Test: `backend/apps/orders/tests/test_direct_sale_shipping_type.py`

**Interfaces:**
- Consumes: `Order.shipping_type`, `StockSalesService.release_for_order(order_id, user_id=None, note="")`.
- Produces: không có interface mới.

- [ ] **Step 1: Write the failing test**

```python
class DirectSaleCancelTests(TestCase):
    def setUp(self):
        from apps.inventory.contracts.stock_sales import SaleLine, StockSalesService
        from apps.orders.models import OrderItem, Product
        from apps.warehouses.models import Warehouse

        self.channel = Channel.objects.create(code="st-dc", name="Direct Cancel Channel")
        self.warehouse = Warehouse.objects.create(code="KHO-DC", name="Kho Direct Cancel")
        self.product = Product.objects.create(sku="SKU-DC", name="San pham DC")
        self.order = Order.objects.create(
            channel=self.channel, name="DC", phone="0900000011",
            status=OrderStatus.NEW, fulfillment_warehouse=self.warehouse,
            shipping_type="direct",
        )
        OrderItem.objects.create(order=self.order, product=self.product, quantity=3)
        StockSalesService.issue_for_order(
            order_id=self.order.id, warehouse_id=self.warehouse.id,
            lines=[SaleLine(product_id=self.product.id, quantity=3)],
        )

    def _cancel(self):
        from apps.common.events import event_bus
        from apps.inventory import subscribers
        from apps.orders.contracts.order_events import OrderStatusChanged

        subscribers.register()
        event_bus.publish(
            OrderStatusChanged(order_id=self.order.id, old_status=0, new_status=6)
        )

    def test_direct_order_cancel_releases_stock_immediately(self):
        from apps.inventory.models import CancelledDispatchWatch
        from apps.inventory.application.services.stock_sales_service import order_goods_still_out

        self._cancel()
        # Hàng bán tại quầy nếu huỷ thì đang nằm ngay trong kho — không có khâu "hàng
        # đang trên đường về" để chờ.
        self.assertFalse(order_goods_still_out(self.order.id))
        self.assertFalse(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())

    def test_shipment_order_cancel_keeps_waiting_for_return(self):
        from apps.inventory.models import CancelledDispatchWatch

        Order.objects.filter(pk=self.order.pk).update(shipping_type="shipment")
        self._cancel()
        self.assertTrue(CancelledDispatchWatch.objects.filter(order_id=self.order.id).exists())
```

Nếu `event_bus.publish` trong repo này chỉ chạy sau commit, dùng đúng helper mà `apps/kiotviet/tests/test_status_change_releases_stock.py` đang dùng để bắn event trong test (đọc file đó trước khi viết).

- [ ] **Step 2: Run test to verify it fails**

Run: docker pytest `apps/orders/tests/test_direct_sale_shipping_type.py::DirectSaleCancelTests -q`
Expected: FAIL ở `test_direct_order_cancel_releases_stock_immediately` — hàng vẫn "còn ngoài kho" và có dòng chờ hàng về

- [ ] **Step 3: Branch on shipping_type in the subscriber**

Trong `backend/apps/inventory/subscribers.py`, thay đoạn:

```python
        logger.info(
            "Huỷ đơn %s → giữ nguyên outstanding, chờ hàng về kho", event.order_id
        )
        # Import cục bộ: awaiting_return_service import ngược lại stock_sales_service.
        from apps.inventory.application.services.awaiting_return_service import (
            AwaitingReturnService,
        )

        AwaitingReturnService.open_for_order(event.order_id)
```

bằng:

```python
        # Đơn bán trực tiếp: khách lấy hàng tại quầy, huỷ nghĩa là hàng còn ngay trong
        # kho → cộng trả tồn luôn. Bắt kho quét nhập hoàn cho đơn quầy là tạo ra một
        # hàng đợi không bao giờ có ai xử lý.
        if _is_direct_sale(event.order_id):
            logger.info("Huỷ đơn bán trực tiếp %s → hoàn tồn ngay", event.order_id)
            StockSalesService.release_for_order(
                order_id=event.order_id,
                note="Hoàn tồn do huỷ đơn bán trực tiếp",
            )
            return

        logger.info(
            "Huỷ đơn %s → giữ nguyên outstanding, chờ hàng về kho", event.order_id
        )
        # Import cục bộ: awaiting_return_service import ngược lại stock_sales_service.
        from apps.inventory.application.services.awaiting_return_service import (
            AwaitingReturnService,
        )

        AwaitingReturnService.open_for_order(event.order_id)
```

Thêm helper module-level ngay trên `_on_order_status_changed`:

```python
def _is_direct_sale(order_id: int) -> bool:
    """Đơn này có phải đơn bán trực tiếp không? Một truy vấn theo PK, không có N+1."""
    from apps.orders.models import Order, ShippingType

    return (
        Order.objects.filter(pk=order_id, shipping_type=ShippingType.DIRECT).exists()
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: docker pytest `apps/orders/tests/test_direct_sale_shipping_type.py -q`
Expected: 14 passed

- [ ] **Step 5: Run inventory suites for regressions**

Run: docker pytest `apps/inventory/tests -q`
Expected: tất cả pass — đặc biệt các test về `CancelledDispatchWatch` và hồi sinh đơn huỷ

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory
git commit -m "feat(inventory): release stock immediately when direct sale is cancelled"
```

---

### Task 7: Bộ lọc backend + cột Excel

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/_view_helpers.py:321` (thêm khoá vào `build_orders_filters`)
- Modify: `backend/apps/orders/repositories.py:768-775` (áp filter)
- Modify: `backend/apps/orders/export_excel.py` (nhãn cột)
- Test: `backend/apps/orders/tests/test_direct_sale_shipping_type.py`

**Interfaces:**
- Consumes: `Order.shipping_type`.
- Produces: filter key `"shipping_type"` nhận list các giá trị `"shipment" | "direct" | "none"`; `"none"` nghĩa là `shipping_type IS NULL`.

- [ ] **Step 1: Write the failing test**

```python
class ShippingTypeFilterTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-shiptype-filter")
        self.channel = Channel.objects.create(code="st-fl", name="Filter Channel")
        self.direct = Order.objects.create(
            channel=self.channel, name="F1", phone="0900000012",
            status=OrderStatus.NEW, shipping_type="direct",
        )
        self.shipment = Order.objects.create(
            channel=self.channel, name="F2", phone="0900000013",
            status=OrderStatus.NEW, shipping_type="shipment",
        )
        self.unknown = Order.objects.create(
            channel=self.channel, name="F3", phone="0900000014", status=OrderStatus.NEW,
        )

    def _list_ids(self, payload):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post("/api/orders", payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data["results"] if isinstance(response.data, dict) else response.data
        return {row["id"] for row in rows}

    def test_filter_direct_only(self):
        self.assertEqual(self._list_ids({"shipping_type": ["direct"]}), {self.direct.id})

    def test_filter_unknown_uses_none_token(self):
        # "none" là quy ước của tầng filter cho IS NULL — chuỗi rỗng đã mang nghĩa
        # "không lọc" ở mọi trục khác nên không tái dùng được.
        self.assertEqual(self._list_ids({"shipping_type": ["none"]}), {self.unknown.id})

    def test_no_filter_returns_all(self):
        ids = self._list_ids({})
        self.assertTrue({self.direct.id, self.shipment.id, self.unknown.id} <= ids)
```

Kiểm tra hình dạng response thật của `POST /api/orders` bằng `grep -n "def list_orders" -A 30 backend/apps/orders/transport/views/order_views_impl/order_crud_view.py` và chỉnh `_list_ids` cho khớp trước khi chạy.

- [ ] **Step 2: Run test to verify it fails**

Run: docker pytest `apps/orders/tests/test_direct_sale_shipping_type.py::ShippingTypeFilterTests -q`
Expected: FAIL — `test_filter_direct_only` trả về cả 3 đơn (filter bị bỏ qua)

- [ ] **Step 3: Accept the filter key**

Trong `_view_helpers.py`, `build_orders_filters`, ngay sau dòng `"order_type": _parse_multi_query_values(request, "order_type"),`:

```python
        "shipping_type": _parse_multi_query_values(request, "shipping_type"),
```

- [ ] **Step 4: Apply the filter**

Trong `repositories.py`, ngay sau khối `order_type_filter`:

```python
        shipping_type_filter = filters.get("shipping_type")
        if shipping_type_filter:
            st_values = (
                shipping_type_filter
                if isinstance(shipping_type_filter, list)
                else [shipping_type_filter]
            )
            st_values = [str(v).strip() for v in st_values if str(v).strip()]
            if st_values:
                # "none" = đơn chưa xác định (NULL). Đơn cũ đều nằm nhóm này nên bỏ qua
                # nó là làm mất một tập lớn khỏi bộ lọc.
                concrete = [v for v in st_values if v != "none"]
                shipping_type_q = Q(shipping_type__in=concrete) if concrete else Q()
                if "none" in st_values:
                    shipping_type_q = (
                        shipping_type_q | Q(shipping_type__isnull=True)
                        if concrete
                        else Q(shipping_type__isnull=True)
                    )
                queryset = queryset.filter(shipping_type_q)
            else:
                queryset = queryset.none()
```

- [ ] **Step 5: Add the Excel column**

Trong `export_excel.py`, thêm nhánh cạnh nhánh `fulfillment_warehouse`:

```python
        elif key == "shipping_type":
            # Xuất NHÃN tiếng Việt, không xuất mã: file Excel là để người đọc.
            val = {"shipment": "Giao vận", "direct": "Bán trực tiếp"}.get(order.shipping_type, "")
```

Thêm `"shipping_type"` vào bảng nhãn cột của file này (tìm bằng `grep -n "Kho xuất\|fulfillment_warehouse" backend/apps/orders/export_excel.py` — nhãn nằm cùng chỗ với các cột khác), nhãn `"Loại xuất hàng"`.

- [ ] **Step 6: Run tests to verify they pass**

Run: docker pytest `apps/orders/tests/test_direct_sale_shipping_type.py apps/orders/tests/test_orders_export_definition.py apps/orders/tests/test_order_list_filters_api.py -q`
Expected: tất cả pass

- [ ] **Step 7: Commit**

```bash
git add backend/apps/orders
git commit -m "feat(orders): filter and export orders by shipping_type"
```

---

### Task 8: Cột "Loại xuất hàng" trên bảng đơn

**Files:**
- Modify: `frontend/src/features/orders/order-types.ts` (type `OrderRow.shipping_type`, thêm `"shipping_type"` vào `OrderColumnKey` và `ORDER_COLUMN_OPTIONS`; KHÔNG thêm vào `DEFAULT_ORDER_COLUMNS`)
- Modify: `frontend/src/features/orders/OrdersTable/OrderCell.tsx` (case render badge)
- Modify: `frontend/src/i18n/messages/orders.ts`
- Test: `frontend/src/features/orders/__tests__/shipping-type.test.tsx`

**Interfaces:**
- Consumes: `shipping_type` trong response đơn (Task 2).
- Produces: column key `"shipping_type"`; nhãn i18n `orders.columns.shippingType`, `shippingType.shipment`, `shippingType.direct`.

- [ ] **Step 1: Write the failing test**

Tạo `frontend/src/features/orders/__tests__/shipping-type.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ORDER_COLUMN_OPTIONS, DEFAULT_ORDER_COLUMNS } from "@/features/orders/order-types";
import { OrderCell } from "@/features/orders/OrdersTable/OrderCell";

describe("cột Loại xuất hàng", () => {
  it("nằm trong danh sách cột chọn được nhưng không bật sẵn", () => {
    expect(ORDER_COLUMN_OPTIONS.some((c) => c.key === "shipping_type")).toBe(true);
    // Mặc định ẩn: bật sẵn là đẩy bố cục bảng của mọi người sang phải một cột.
    expect(DEFAULT_ORDER_COLUMNS).not.toContain("shipping_type");
  });

  it("hiện nhãn tiếng Việt cho từng giá trị", () => {
    const row = { id: 1, shipping_type: "direct" } as never;
    render(<OrderCell column="shipping_type" row={row} />);
    expect(screen.getByText("Bán trực tiếp")).toBeInTheDocument();
  });

  it("hiện gạch ngang khi chưa xác định", () => {
    const row = { id: 2, shipping_type: null } as never;
    render(<OrderCell column="shipping_type" row={row} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
```

Trước khi chạy, mở một test có sẵn của bảng đơn (`ls frontend/src/features/orders/__tests__`) và khớp đúng cách nó render `OrderCell` — props thật có thể khác (`column` vs `columnKey`, cần provider i18n). Sửa test theo file mẫu đó, không sửa component cho vừa test.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/orders/__tests__/shipping-type.test.tsx`
Expected: FAIL — không có key `shipping_type`

- [ ] **Step 3: Add type, column key and label**

`order-types.ts` — thêm vào type dòng đơn (cạnh `fulfillment_warehouse`):

```ts
  shipping_type?: "shipment" | "direct" | null;
```

thêm `| "shipping_type"` vào union `OrderColumnKey`, và vào `ORDER_COLUMN_OPTIONS` ngay sau mục `fulfillment_warehouse`:

```ts
  { key: "shipping_type", label: "orders.columns.shippingType" },
```

`i18n/messages/orders.ts`:

```ts
    "orders.columns.shippingType": "Loại xuất hàng",
    "shippingType.shipment": "Giao vận",
    "shippingType.direct": "Bán trực tiếp",
    "orders.filters.shippingType": "Loại xuất hàng",
    "orders.filters.shippingTypeAll": "Tất cả",
    "shippingType.none": "Chưa xác định",
```

- [ ] **Step 4: Render the badge**

`OrderCell.tsx`, thêm case cạnh case `"fulfillment_warehouse"`:

```tsx
    case "shipping_type": {
      // Read-only: cờ do hệ thống gắn theo thao tác thật, không có ô sửa như order_type.
      const value = row.shipping_type;
      if (!value) return <span className="text-muted-foreground">—</span>;
      const styles: Record<string, string> = {
        shipment: "bg-sky-100 text-sky-700",
        direct: "bg-amber-100 text-amber-700",
      };
      return (
        <span className={`inline-flex rounded px-1.5 py-0.5 text-xs ${styles[value] ?? ""}`}>
          {t(`shippingType.${value}`, value)}
        </span>
      );
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/features/orders/__tests__/shipping-type.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(orders): add shipping type column to orders table"
```

---

### Task 9: Bộ lọc trên giao diện

**Files:**
- Modify: `frontend/src/features/orders/orders-page/use-orders-filter-options.ts` (thêm `shippingTypeFilterOptions`)
- Modify: `frontend/src/features/orders/OrdersFilterPanel.tsx` (ô lọc, đặt cạnh ô "Loại đơn hàng")
- Modify: `frontend/src/pages/OrdersPage.tsx` (state, đếm bộ lọc đang bật, truyền xuống panel và payload)
- Modify: `frontend/src/features/orders/use-orders-data.ts` (gửi `shipping_type` lên API, và đưa vào bộ override của "Xoá lọc")
- Test: `frontend/src/features/orders/__tests__/shipping-type.test.tsx`

**Interfaces:**
- Consumes: filter key `"shipping_type"` của API (Task 7).
- Produces: không có interface mới.

- [ ] **Step 1: Write the failing test**

Thêm vào `shipping-type.test.tsx` một test kiểm tra payload gửi lên chứa `shipping_type`. Bám theo đúng khuôn test đã có cho `order_type` — tìm bằng:

```bash
grep -rln "order_type" frontend/src/features/orders/__tests__ frontend/src/lib/api/__tests__
```

Nếu có file test dựng payload lọc, sao chép cấu trúc của nó và đổi trục lọc sang `shipping_type` (giá trị `["direct"]` → body chứa `shipping_type: ["direct"]`). Nếu KHÔNG có test nào như vậy, bỏ bước test payload và chỉ test `shippingTypeFilterOptions` trả đúng 3 option `direct` / `shipment` / `none`:

```tsx
it("bộ lọc có đủ 3 lựa chọn", () => {
  // "none" phải có: đơn cũ đều chưa xác định, thiếu nó là mất một tập lớn khỏi bộ lọc.
  const values = SHIPPING_TYPE_FILTER_VALUES;
  expect(values).toEqual(["shipment", "direct", "none"]);
});
```

và export hằng số đó từ `use-orders-filter-options.ts`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/orders/__tests__/shipping-type.test.tsx`
Expected: FAIL

- [ ] **Step 3: Add filter options**

`use-orders-filter-options.ts`, cạnh `orderTypeFilterOptions`:

```ts
export const SHIPPING_TYPE_FILTER_VALUES = ["shipment", "direct", "none"] as const;
```

```ts
  const shippingTypeFilterOptions: FilterSelectOption[] = useMemo(
    () =>
      SHIPPING_TYPE_FILTER_VALUES.map((v) => ({
        value: v,
        label: t(`shippingType.${v}`, v),
      })),
    [t],
  );
```

Nhớ thêm `shippingTypeFilterOptions` vào object mà hook này trả về.

- [ ] **Step 4: Wire the panel**

`OrdersFilterPanel.tsx` — thêm props `shippingTypeOptions`, `shippingType`, `onShippingTypeChange` vào type props và tham số, rồi copy đúng khối `<MultiSelect>` của "Loại đơn hàng" ngay dưới nó:

```tsx
        <div className="w-full">
          <label
            htmlFor="orders-filter-shipping-type"
            className="mb-1 block text-xs font-medium text-muted-foreground"
          >
            {t("orders.filters.shippingType", "Loại xuất hàng")}
          </label>
          <MultiSelect
            id="orders-filter-shipping-type"
            options={shippingTypeOptions}
            selected={shippingType}
            onChange={onShippingTypeChange}
            placeholder={t("orders.filters.shippingTypeAll", "Tất cả")}
            className="w-full"
          />
        </div>
```

- [ ] **Step 5: Wire the page state**

`OrdersPage.tsx` — bám theo `orderType`, thêm song song ở **mọi** chỗ nó xuất hiện: khai báo type props, destructure, đếm bộ lọc đang bật (`if (shippingType.length > 0) n += 1;`), mảng dependency, payload `loadOrders`, và props truyền vào `OrdersFilterPanel`. Dùng `grep -n "orderType" src/pages/OrdersPage.tsx` để không sót chỗ nào.

`use-orders-data.ts` — thêm `shipping_type` vào cả nơi dựng payload lẫn `filtersToLoadOverrides` (bộ override của "Xoá lọc"). Bỏ sót chỗ thứ hai là lỗi đã từng xảy ra với `vat` và `order_type`: bấm "Xoá lọc" xong danh sách vẫn bị lọc theo trục đó.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/features/orders`
Expected: PASS (trừ `RequireAuth.test.tsx` vốn đã đỏ sẵn trên master — không phải do thay đổi này)

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(orders): add shipping type filter to orders page"
```

---

### Task 10: Chặn "Tạo nhanh" ở giao diện khi thiếu kho

**Files:**
- Modify: `frontend/src/features/orders/OrdersTable/OrdersTable.tsx:355-366` (nút "Tạo nhanh (bán nhanh)")
- Modify: `frontend/src/i18n/messages/orders.ts`
- Test: `frontend/src/features/orders/__tests__/shipping-type.test.tsx`

**Interfaces:**
- Consumes: `rows` của bảng (đã có `fulfillment_warehouse`), `runKiotVietAction`.
- Produces: không có interface mới.

- [ ] **Step 1: Write the failing test**

```tsx
it("không gọi API khi đơn được chọn thiếu kho xuất", async () => {
  // Chặn ngay ở đây để nhân viên biết trước khi hoá đơn KiotViet được tạo — backend
  // vẫn có guard riêng, cái này là để báo lỗi kịp lúc chứ không phải lớp bảo vệ duy nhất.
  const runKiotVietAction = vi.fn();
  // ... render OrdersTable với 1 dòng shipping-less đang được chọn, bấm "Tạo nhanh (bán nhanh)"
  expect(runKiotVietAction).not.toHaveBeenCalled();
});
```

Dựng render theo đúng khuôn test `OrdersTable` đã có (`ls frontend/src/features/orders/__tests__ | grep -i table`). Nếu `OrdersTable` quá nặng để render trong test, tách phần kiểm tra thành hàm thuần trong cùng file component và test hàm đó:

```ts
export function findOrdersWithoutWarehouse(rows: OrderRow[], selectedIds: number[]): OrderRow[] {
  const selected = new Set(selectedIds);
  return rows.filter((r) => selected.has(r.id) && !r.fulfillment_warehouse);
}
```

Ưu tiên phương án hàm thuần — nó cũng là chỗ đặt logic đúng hơn.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/orders/__tests__/shipping-type.test.tsx`
Expected: FAIL

- [ ] **Step 3: Guard the button**

Trong `OrdersTable.tsx`, đổi `onClick` của nút "Tạo nhanh (bán nhanh)":

```tsx
                      onClick={() => {
                        const missing = findOrdersWithoutWarehouse(rows, selectedIds);
                        if (missing.length > 0) {
                          toast.error(
                            t(
                              "orders.toast.quickSaleMissingWarehouse",
                              "Đơn bán trực tiếp phải có kho xuất. Thiếu kho: {{codes}}",
                            ).replace(
                              "{{codes}}",
                              missing.map((o) => o.invoice_code || `#${o.id}`).join(", "),
                            ),
                          );
                          return;
                        }
                        runKiotVietAction("quick_sale_selected", "selected");
                      }}
```

Thêm chuỗi i18n tương ứng vào `i18n/messages/orders.ts`. Kiểm tra `toast` đã được import trong file chưa (`grep -n "from \"sonner\"" src/features/orders/OrdersTable/OrdersTable.tsx`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/features/orders`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(orders): warn before quick sale when warehouse is missing"
```

---

### Task 11: Chốt chất lượng và mở MR

**Files:** không sửa code trừ khi có lỗi.

- [ ] **Step 1: Lint backend**

Run: `docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .`
Expected: "All checks passed!"

- [ ] **Step 2: Chạy trọn các suite bị ảnh hưởng**

Run: docker pytest với `apps/orders/tests apps/inventory/tests apps/kiotviet/tests -q` (đặt `POSTGRES_DB` riêng, chạy nền và ghi log ra file vì suite này dài hơn 10 phút)
Expected: không có failure MỚI. Đối chiếu với danh sách test đã đỏ sẵn trên master trước khi kết luận.

- [ ] **Step 3: Chạy test frontend của trang đơn**

Run: `cd frontend && npx vitest run src/features/orders`
Expected: chỉ còn các test vốn đã đỏ trên master

- [ ] **Step 4: Cập nhật trạng thái spec**

Sửa dòng `- Trạng thái:` ở đầu `docs/superpowers/specs/2026-08-12-order-shipping-type-design.md` thành `đã triển khai, chờ merge`.

- [ ] **Step 5: Push và mở MR**

```bash
git add docs
git commit -m "docs: mark shipping_type spec as implemented"
git push -u origin feat/order-shipping-type \
  -o merge_request.create \
  -o merge_request.target=master \
  -o merge_request.title="feat(orders): shipping_type for direct sales stock deduction" \
  -o merge_request.remove_source_branch
```

Gửi link MR cho người dùng, kèm ghi chú: migration index chạy `CONCURRENTLY`, nên deploy vào giờ thấp điểm.

## Self-Review

**Spec coverage:**

| Mục spec | Task |
|---|---|
| §3 Dữ liệu (choices, field, 2 migration) | Task 1 |
| §4 Ai gắn cờ — direct | Task 4 |
| §4 Ai gắn cờ — shipment (push, kship) | Task 5 |
| §4 Không ghi đè giá trị đã có | Task 4 (nhánh `if order.shipping_type is not None: return`), test ở Task 5 |
| §4 Read-only với client | Task 2 |
| §5 Trừ tồn qua đường `dispatched` | Task 4 |
| §5 Guard 3 lớp | Task 3 (API), Task 4 (worker), Task 10 (FE) |
| §6 Huỷ đơn direct hoàn tồn ngay | Task 6 |
| §7 Cột badge | Task 8 |
| §7 Bộ lọc | Task 7 (BE) + Task 9 (FE) |
| §7 Cột Excel | Task 7 |
| §8 Kiểm thử vòng đời | Task 1–6 dồn vào một file test |

**Ghi chú về một mục kiểm thử của spec:** "đơn direct huỷ rồi hồi sinh → trừ lại đúng, không trừ đôi" không có task riêng vì code hồi sinh không phải sửa (`reissue_after_cancel_release` đọc sổ). Test cho nó nằm trong bước chạy `apps/inventory/tests` ở Task 6 và Task 11 — nếu suite hiện có chưa phủ ca này với đơn `direct`, thêm một test vào `DirectSaleCancelTests` khi làm Task 6.

**Type consistency:** `ShippingType.DIRECT`/`SHIPMENT` (Python) và `"direct"`/`"shipment"` (TS) khớp nhau; `_apply_shipping_type_after_job(job, order)` giữ nguyên chữ ký giữa Task 4 và Task 5; filter key `"shipping_type"` với token `"none"` dùng thống nhất ở Task 7 và Task 9.
