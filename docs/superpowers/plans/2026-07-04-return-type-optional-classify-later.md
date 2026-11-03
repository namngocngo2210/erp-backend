# Phiếu trả hàng: bỏ chọn loại khi quét, phân loại sau — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bỏ bắt buộc chọn "loại trả hàng" khi quét/tạo phiếu hoàn; phân loại sau trong dialog chi tiết; khi ghi sổ phiếu "Hàng hoàn" có đơn khớp thì đặt đơn = RETURNED và đẩy KiotViet; huỷ phiếu thì hoàn tác.

**Architecture:** `ReturnNote.return_type` cho phép để trống (`""` = "Chưa phân loại"). Ghi sổ chặn nếu chưa phân loại. Một module mới `return_order_sync.py` bọc việc đổi `Order.status` + đẩy KiotViet (lỗi KiotViet chỉ log). Frontend bỏ `<Select>` loại ở luồng quét/tạo, thêm dropdown phân loại ở `ReturnNoteDetailDialog`.

**Tech Stack:** Django + DRF (backend), React + TanStack Query + Vitest (frontend).

## Global Constraints

- Spec nguồn: `docs/superpowers/specs/2026-07-04-return-type-optional-classify-later-design.md`.
- `""` (chuỗi rỗng) = "Chưa phân loại". KHÔNG thêm giá trị enum mới vào `ReturnType`.
- Sửa `return_type` chỉ khi phiếu còn `draft`. Đổi trạng thái đơn + đẩy KiotViet CHỈ khi `return_type == "delivery_returned"` VÀ có `source_order`.
- Lỗi KiotViet CHỈ log warning — KHÔNG được rollback ghi sổ/huỷ phiếu.
- **Lệnh chạy test backend** (dùng test container, KHÔNG dùng prod .env) — gọi tắt là `PYTEST_ENV`:
  ```
  cd /home/desi/desi-order/backend && POSTGRES_DB=desi_test POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass POSTGRES_HOST=localhost POSTGRES_PORT=5433 REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= APP_ENV=development DJANGO_DEBUG=0 /home/desi/desi-order/.venv/bin/python manage.py test <PATH> -v2
  ```
- **Lệnh chạy test frontend:** `cd /home/desi/desi-order/frontend && npx vitest run <PATH>`; typecheck: `npm run typecheck`.
- Commit thường xuyên, mỗi task ít nhất 1 commit.

---

## Task 1: Model — cho `return_type` để trống + thêm `order_status_before`

**Files:**
- Modify: `backend/apps/inventory/models.py:256` (field `return_type`), thêm field mới sau `posted_at` (dòng ~281)
- Create: `backend/apps/inventory/migrations/0012_returnnote_optional_type_status_before.py` (sinh bằng makemigrations)

**Interfaces:**
- Produces: `ReturnNote.return_type` cho phép `""`; `ReturnNote.order_status_before: int | None`.

- [ ] **Step 1: Sửa field `return_type` cho phép trống**

Trong `backend/apps/inventory/models.py`, đổi dòng 256:

```python
    return_type = models.CharField(
        max_length=20, choices=ReturnType.choices, blank=True, default=""
    )
```

- [ ] **Step 2: Thêm field `order_status_before`**

Trong `class ReturnNote`, ngay sau dòng `posted_at = models.DateTimeField(null=True, blank=True)` (dòng ~281), thêm:

```python
    # Trạng thái đơn nguồn ngay TRƯỚC khi ghi sổ đặt đơn = RETURNED — để huỷ phiếu
    # có thể trả đơn về đúng giá trị cũ. None = ghi sổ không đổi trạng thái đơn.
    order_status_before = models.IntegerField(null=True, blank=True)
```

- [ ] **Step 3: Sinh migration**

Run:
```
cd /home/desi/desi-order/backend && APP_ENV=development /home/desi/desi-order/.venv/bin/python manage.py makemigrations inventory --name returnnote_optional_type_status_before
```
Expected: tạo file `0012_returnnote_optional_type_status_before.py` với `AlterField` cho `return_type` và `AddField` cho `order_status_before`.

- [ ] **Step 4: Chạy migrate trên DB test để chắc migration hợp lệ**

Run:
```
cd /home/desi/desi-order/backend && POSTGRES_DB=desi_test POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass POSTGRES_HOST=localhost POSTGRES_PORT=5433 REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= APP_ENV=development DJANGO_DEBUG=0 /home/desi/desi-order/.venv/bin/python manage.py migrate inventory
```
Expected: `Applying inventory.0012_...  OK`

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/models.py backend/apps/inventory/migrations/0012_returnnote_optional_type_status_before.py
git commit -m "feat(inventory): make return_type optional, add order_status_before"
```

---

## Task 2: Bỏ bắt buộc `return_type` khi tạo + chặn ghi sổ khi chưa phân loại

**Files:**
- Modify: `backend/apps/inventory/transport/serializers/return_serializers.py:37-40` (CreateReturnNoteSerializer.return_type)
- Modify: `backend/apps/inventory/application/services/return_note_service.py:72` (create_return signature) và `:128-135` (post_return guard)
- Test: `backend/apps/inventory/tests/test_return_optional_type.py` (mới)

**Interfaces:**
- Consumes: model từ Task 1.
- Produces: POST `/api/inventory/returns` không cần `return_type`; POST `/post` trả 400 nếu `return_type == ""`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_return_optional_type.py`:

```python
from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from apps.orders.models import Product
from apps.users.test_helpers import make_admin
from apps.warehouses.models import Warehouse


class OptionalReturnTypeTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_opt_type")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WOT", name="Kho OT")
        self.product = Product.objects.create(name="SP OT", sku="OT-1")

    def _create_unclassified(self, qty=2):
        return self.client.post(
            "/api/inventory/returns",
            data={
                "warehouse_id": self.wh.id,
                "lines": [{"product_id": self.product.id, "quantity": qty}],
            },
            format="json",
        )

    def test_create_without_return_type_returns_201_unclassified(self):
        resp = self._create_unclassified()
        assert resp.status_code == 201, resp.json()
        assert resp.json()["return_type"] == ""
        assert resp.json()["status"] == "draft"

    def test_post_unclassified_return_returns_400(self):
        rid = self._create_unclassified().json()["id"]
        resp = self.client.post(f"/api/inventory/returns/{rid}/post", format="json")
        assert resp.status_code == 400
        assert "phân loại" in resp.json()["detail"]
```

- [ ] **Step 2: Chạy test — xác nhận FAIL**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory.tests.test_return_optional_type`
Expected: FAIL — hiện `return_type` required nên create trả 400 (không đạt 201).

- [ ] **Step 3: Sửa serializer cho `return_type` optional**

Trong `backend/apps/inventory/transport/serializers/return_serializers.py`, đổi dòng 38-40:

```python
    return_type = serializers.ChoiceField(
        choices=["delivery_returned", "external_return", "warranty", "defect"],
        required=False,
        allow_blank=True,
        default="",
    )
```

- [ ] **Step 4: Cho `create_return` mặc định `return_type=""`**

Trong `backend/apps/inventory/application/services/return_note_service.py`, đổi dòng 72:

```python
        return_type: str = "",
```

- [ ] **Step 5: Chặn ghi sổ khi chưa phân loại**

Trong `post_return`, ngay sau khối kiểm tra `rn.lines.exists()` (sau dòng 133, trước `_guard_double_credit`), thêm:

```python
        if not rn.return_type:
            raise ValueError(
                "Phiếu chưa phân loại, vui lòng chọn loại trả hàng trước khi ghi sổ."
            )
```

- [ ] **Step 6: Chạy test — xác nhận PASS**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory.tests.test_return_optional_type`
Expected: PASS (2 tests).

- [ ] **Step 7: Chạy lại test cũ để chắc không hồi quy**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory.tests.test_return_api apps.inventory.tests.test_return_unmatched`
Expected: PASS toàn bộ.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/inventory/transport/serializers/return_serializers.py backend/apps/inventory/application/services/return_note_service.py backend/apps/inventory/tests/test_return_optional_type.py
git commit -m "feat(inventory): allow creating unclassified return, block posting until classified"
```

---

## Task 3: Cho phép sửa `return_type` qua PATCH (phân loại sau)

**Files:**
- Modify: `backend/apps/inventory/transport/serializers/return_serializers.py:66-72` (UpdateReturnNoteSerializer)
- Modify: `backend/apps/inventory/application/services/return_note_service.py:179-184` (update_draft field loop)
- Test: `backend/apps/inventory/tests/test_return_optional_type.py` (thêm)

**Interfaces:**
- Consumes: Task 2.
- Produces: PATCH `/api/inventory/returns/<id>` chấp nhận `return_type` khi phiếu draft.

- [ ] **Step 1: Viết test thất bại (thêm vào class `OptionalReturnTypeTests`)**

Thêm method:

```python
    def test_patch_return_type_then_post_ok(self):
        rid = self._create_unclassified(qty=4).json()["id"]

        patch = self.client.patch(
            f"/api/inventory/returns/{rid}",
            data={"return_type": "warranty"},
            format="json",
        )
        assert patch.status_code == 200, patch.json()
        assert patch.json()["return_type"] == "warranty"

        post = self.client.post(f"/api/inventory/returns/{rid}/post", format="json")
        assert post.status_code == 200, post.json()

        stock = self.client.get(
            "/api/inventory/stock", {"limit": 50, "offset": 0, "state": "warranty"}
        )
        items = [i for i in stock.json()["items"] if i["product_sku"] == "OT-1"]
        assert items and items[0]["quantity"] == 4
```

- [ ] **Step 2: Chạy test — xác nhận FAIL**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory.tests.test_return_optional_type.OptionalReturnTypeTests.test_patch_return_type_then_post_ok`
Expected: FAIL — PATCH bỏ qua `return_type` nên post vẫn 400 (chưa phân loại).

- [ ] **Step 3: Thêm `return_type` vào UpdateReturnNoteSerializer**

Trong `return_serializers.py`, thêm vào `UpdateReturnNoteSerializer` (sau dòng 67 `source_order_id`):

```python
    return_type = serializers.ChoiceField(
        choices=["delivery_returned", "external_return", "warranty", "defect"],
        required=False,
        allow_blank=True,
    )
```

- [ ] **Step 4: Cho `update_draft` ghi `return_type`**

Trong `return_note_service.py`, thêm `"return_type"` vào tuple field ở dòng 179-182:

```python
        for field in (
            "source_order_id", "customer_name", "customer_phone",
            "source_note", "note", "return_type",
        ):
            if field in data:
                setattr(rn, field, data[field])
```

- [ ] **Step 5: Chạy test — xác nhận PASS**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory.tests.test_return_optional_type`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory/transport/serializers/return_serializers.py backend/apps/inventory/application/services/return_note_service.py backend/apps/inventory/tests/test_return_optional_type.py
git commit -m "feat(inventory): allow editing return_type via PATCH to classify later"
```

---

## Task 4: Module `return_order_sync` — đổi Order.status + đẩy KiotViet

**Files:**
- Create: `backend/apps/inventory/application/services/return_order_sync.py`
- Test: `backend/apps/inventory/tests/test_return_order_sync.py` (mới)

**Interfaces:**
- Produces:
  - `mark_order_returned(order, *, user_id=None) -> int | None` — đặt `order.status = RETURNED`, lưu, publish `OrderStatusChanged`, đẩy KiotViet; trả về trạng thái CŨ (int) hoặc `None` nếu đơn đã RETURNED (không đổi gì).
  - `revert_order_status(order, previous_status: int, *, user_id=None) -> None` — đặt `order.status = previous_status`, lưu, publish, đẩy KiotViet.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_return_order_sync.py`:

```python
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order, OrderStatus


def _make_order(*, status, with_kiotviet=True):
    channel = Channel.objects.create(code="ch-sync", name="Ch Sync")
    customer = Customer.objects.create(
        full_name="Khach S", phone_raw="0900000009",
        phone_normalized="0900000009", code="CUST-S1",
    )
    kwargs = dict(channel=channel, customer=customer, order_code="ORD-S1", status=status)
    if with_kiotviet:
        kv = KiotVietChannel.objects.create(retailer="R-sync")
        kwargs["kiotviet_channel"] = kv
        kwargs["invoice_id"] = 55555
    return Order.objects.create(**kwargs)


class ReturnOrderSyncTests(TestCase):
    @patch(
        "apps.kiotviet.infrastructure.services.invoice_mutations_push"
        ".update_invoice_delivery_status_by_id"
    )
    def test_mark_order_returned_sets_status_and_pushes(self, push):
        from apps.inventory.application.services.return_order_sync import (
            mark_order_returned,
        )

        order = _make_order(status=OrderStatus.DELIVERED_SUCCESS)
        previous = mark_order_returned(order)

        assert previous == int(OrderStatus.DELIVERED_SUCCESS)
        order.refresh_from_db()
        assert order.status == int(OrderStatus.RETURNED)
        push.assert_called_once()
        assert push.call_args.kwargs["status"] == int(OrderStatus.RETURNED)
        assert push.call_args.kwargs["invoice_id"] == 55555

    @patch(
        "apps.kiotviet.infrastructure.services.invoice_mutations_push"
        ".update_invoice_delivery_status_by_id"
    )
    def test_mark_order_returned_already_returned_is_noop(self, push):
        from apps.inventory.application.services.return_order_sync import (
            mark_order_returned,
        )

        order = _make_order(status=OrderStatus.RETURNED)
        assert mark_order_returned(order) is None
        push.assert_not_called()

    @patch(
        "apps.kiotviet.infrastructure.services.invoice_mutations_push"
        ".update_invoice_delivery_status_by_id"
    )
    def test_mark_order_returned_no_kiotviet_skips_push(self, push):
        from apps.inventory.application.services.return_order_sync import (
            mark_order_returned,
        )

        order = _make_order(status=OrderStatus.DELIVERED_SUCCESS, with_kiotviet=False)
        mark_order_returned(order)
        order.refresh_from_db()
        assert order.status == int(OrderStatus.RETURNED)
        push.assert_not_called()

    @patch(
        "apps.kiotviet.infrastructure.services.invoice_mutations_push"
        ".update_invoice_delivery_status_by_id",
        side_effect=Exception("KiotViet down"),
    )
    def test_mark_order_returned_push_error_does_not_raise(self, push):
        from apps.inventory.application.services.return_order_sync import (
            mark_order_returned,
        )

        order = _make_order(status=OrderStatus.DELIVERED_SUCCESS)
        mark_order_returned(order)  # không raise
        order.refresh_from_db()
        assert order.status == int(OrderStatus.RETURNED)

    @patch(
        "apps.kiotviet.infrastructure.services.invoice_mutations_push"
        ".update_invoice_delivery_status_by_id"
    )
    def test_revert_order_status_restores_and_pushes(self, push):
        from apps.inventory.application.services.return_order_sync import (
            revert_order_status,
        )

        order = _make_order(status=OrderStatus.RETURNED)
        revert_order_status(order, int(OrderStatus.DELIVERED_SUCCESS))
        order.refresh_from_db()
        assert order.status == int(OrderStatus.DELIVERED_SUCCESS)
        assert push.call_args.kwargs["status"] == int(OrderStatus.DELIVERED_SUCCESS)
```

- [ ] **Step 2: Chạy test — xác nhận FAIL**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory.tests.test_return_order_sync`
Expected: FAIL — module `return_order_sync` chưa tồn tại (ImportError).

- [ ] **Step 3: Viết module**

Tạo `backend/apps/inventory/application/services/return_order_sync.py`:

```python
from __future__ import annotations

import logging

from apps.orders.models import OrderStatus

logger = logging.getLogger(__name__)


def mark_order_returned(order, *, user_id: int | None = None) -> int | None:
    """Đặt đơn nguồn = RETURNED (đã chuyển hoàn) + đẩy KiotViet.

    Trả về trạng thái CŨ (int) để lưu vào ReturnNote.order_status_before phục vụ
    hoàn tác khi huỷ. Trả None nếu đơn đã RETURNED sẵn (không đổi gì).
    """
    if int(order.status) == int(OrderStatus.RETURNED):
        return None
    previous = int(order.status)
    order.status = int(OrderStatus.RETURNED)
    order.save(update_fields=["status"])
    _publish_status_changed(order, previous, int(OrderStatus.RETURNED))
    _push_kiotviet(order, int(OrderStatus.RETURNED))
    return previous


def revert_order_status(
    order, previous_status: int, *, user_id: int | None = None
) -> None:
    """Trả đơn về trạng thái trước khi bị đánh dấu RETURNED + đẩy KiotViet."""
    current = int(order.status)
    order.status = int(previous_status)
    order.save(update_fields=["status"])
    _publish_status_changed(order, current, int(previous_status))
    _push_kiotviet(order, int(previous_status))


def _publish_status_changed(order, old_status: int, new_status: int) -> None:
    if old_status == new_status:
        return
    from apps.common.events import event_bus
    from apps.orders.contracts.order_events import OrderStatusChanged

    event_bus.publish_on_commit(
        OrderStatusChanged(
            order_id=order.id,
            old_status=old_status,
            new_status=new_status,
        )
    )


def _push_kiotviet(order, status: int) -> None:
    """Đẩy deliveryDetail.status của invoice kênh chính lên KiotViet.

    Lỗi KiotViet CHỈ log — không được làm hỏng ghi sổ/huỷ phiếu.
    """
    if not order.kiotviet_channel_id or not order.invoice_id:
        logger.info(
            "Bỏ qua đẩy KiotViet cho đơn %s: thiếu kiotviet_channel/invoice_id.",
            order.id,
        )
        return
    from apps.kiotviet.infrastructure.services.invoice_mutations_push import (
        update_invoice_delivery_status_by_id,
    )

    try:
        update_invoice_delivery_status_by_id(
            invoice_id=order.invoice_id,
            status=status,
            order=order,
            channel=order.kiotviet_channel,
        )
    except Exception as exc:  # noqa: BLE001 — lỗi KiotViet không được chặn nghiệp vụ
        logger.warning(
            "Đẩy trạng thái KiotViet thất bại (order_id=%s, status=%s): %s",
            order.id, status, exc,
        )
```

- [ ] **Step 4: Chạy test — xác nhận PASS**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory.tests.test_return_order_sync`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/application/services/return_order_sync.py backend/apps/inventory/tests/test_return_order_sync.py
git commit -m "feat(inventory): add return_order_sync (mark returned / revert + KiotViet push)"
```

---

## Task 5: Ghi sổ phiếu "Hàng hoàn" có đơn → đặt đơn RETURNED

**Files:**
- Modify: `backend/apps/inventory/application/services/return_note_service.py` (import + `post_return` dòng ~147-150)
- Test: `backend/apps/inventory/tests/test_return_order_status.py` (mới)

**Interfaces:**
- Consumes: `mark_order_returned` (Task 4); model field `order_status_before` (Task 1).
- Produces: `post_return` set `order.status=RETURNED` + lưu `rn.order_status_before` khi đủ điều kiện.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_return_order_status.py`:

```python
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order, OrderItem, OrderStatus
from apps.orders.models import Product
from apps.users.test_helpers import make_admin
from apps.warehouses.models import Warehouse

_PUSH = (
    "apps.kiotviet.infrastructure.services.invoice_mutations_push"
    ".update_invoice_delivery_status_by_id"
)


class PostReturnOrderStatusTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_ret_status")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WRS", name="Kho RS")
        self.product = Product.objects.create(name="SP RS", sku="RS-1")

    def _order(self, *, with_kiotviet=True):
        channel = Channel.objects.create(code="ch-rs", name="Ch RS")
        customer = Customer.objects.create(
            full_name="Khach RS", phone_raw="0900001111",
            phone_normalized="0900001111", code="CUST-RS1",
        )
        kwargs = dict(
            channel=channel, customer=customer, order_code="ORD-RS1",
            status=OrderStatus.DELIVERED_SUCCESS, name="Khach RS", phone="0900001111",
        )
        if with_kiotviet:
            kwargs["kiotviet_channel"] = KiotVietChannel.objects.create(retailer="R-rs")
            kwargs["invoice_id"] = 77777
        order = Order.objects.create(**kwargs)
        OrderItem.objects.create(
            order=order, product=self.product, product_name="SP RS",
            sku="RS-1", quantity=3,
        )
        return order

    def _create_note(self, *, order_id, return_type):
        return self.client.post(
            "/api/inventory/returns",
            data={
                "return_type": return_type,
                "warehouse_id": self.wh.id,
                "source_order_id": order_id,
                "lines": [{"product_id": self.product.id, "quantity": 3}],
            },
            format="json",
        ).json()["id"]

    @patch(_PUSH)
    def test_post_delivery_returned_marks_order_returned(self, push):
        order = self._order()
        rid = self._create_note(order_id=order.id, return_type="delivery_returned")
        resp = self.client.post(f"/api/inventory/returns/{rid}/post", format="json")
        assert resp.status_code == 200, resp.json()
        order.refresh_from_db()
        assert order.status == int(OrderStatus.RETURNED)
        push.assert_called_once()
        assert push.call_args.kwargs["status"] == int(OrderStatus.RETURNED)

    @patch(_PUSH)
    def test_post_other_type_does_not_touch_order(self, push):
        order = self._order()
        rid = self._create_note(order_id=order.id, return_type="warranty")
        self.client.post(f"/api/inventory/returns/{rid}/post", format="json")
        order.refresh_from_db()
        assert order.status == int(OrderStatus.DELIVERED_SUCCESS)
        push.assert_not_called()

    @patch(_PUSH, side_effect=Exception("KiotViet down"))
    def test_post_survives_kiotviet_error(self, push):
        order = self._order()
        rid = self._create_note(order_id=order.id, return_type="delivery_returned")
        resp = self.client.post(f"/api/inventory/returns/{rid}/post", format="json")
        assert resp.status_code == 200, resp.json()
        order.refresh_from_db()
        assert order.status == int(OrderStatus.RETURNED)
```

- [ ] **Step 2: Chạy test — xác nhận FAIL**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory.tests.test_return_order_status.PostReturnOrderStatusTests.test_post_delivery_returned_marks_order_returned`
Expected: FAIL — order.status vẫn DELIVERED_SUCCESS (chưa wiring).

- [ ] **Step 3: Import helper + hằng ReturnType đã có**

Trong `return_note_service.py`, thêm import ở đầu file (sau dòng 8, khối import services):

```python
from apps.inventory.application.services.return_order_sync import mark_order_returned
```

- [ ] **Step 4: Wiring trong `post_return`**

Thay khối cuối `post_return` (dòng 147-150) bằng:

```python
        rn.status = ReturnStatus.POSTED
        rn.posted_at = timezone.now()
        update_fields = ["status", "posted_at"]

        if rn.return_type == ReturnType.DELIVERY_RETURNED and rn.source_order_id:
            from apps.orders.models import Order  # local import: giữ ranh giới module

            order = (
                Order.objects.select_for_update().filter(pk=rn.source_order_id).first()
            )
            if order is not None:
                previous = mark_order_returned(order, user_id=user_id)
                if previous is not None:
                    rn.order_status_before = previous
                    update_fields.append("order_status_before")

        rn.save(update_fields=update_fields)
        return rn
```

- [ ] **Step 5: Chạy test — xác nhận PASS**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory.tests.test_return_order_status`
Expected: PASS (3 tests).

- [ ] **Step 6: Chạy lại toàn bộ test returns để chắc không hồi quy**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory.tests.test_return_api apps.inventory.tests.test_return_unmatched apps.inventory.tests.test_return_optional_type`
Expected: PASS toàn bộ.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/inventory/application/services/return_note_service.py backend/apps/inventory/tests/test_return_order_status.py
git commit -m "feat(inventory): mark source order RETURNED when posting delivery-returned note"
```

---

## Task 6: Huỷ phiếu → hoàn tác trạng thái đơn

**Files:**
- Modify: `backend/apps/inventory/application/services/return_note_service.py` (import + `cancel_return` dòng ~238-240)
- Test: `backend/apps/inventory/tests/test_return_order_status.py` (thêm)

**Interfaces:**
- Consumes: `revert_order_status` (Task 4); `order_status_before` set bởi Task 5.
- Produces: `cancel_return` trả `order.status` về `order_status_before` rồi xoá cờ.

- [ ] **Step 1: Viết test thất bại (thêm vào `PostReturnOrderStatusTests`)**

```python
    @patch(_PUSH)
    def test_cancel_reverts_order_status(self, push):
        order = self._order()
        rid = self._create_note(order_id=order.id, return_type="delivery_returned")
        self.client.post(f"/api/inventory/returns/{rid}/post", format="json")
        order.refresh_from_db()
        assert order.status == int(OrderStatus.RETURNED)

        push.reset_mock()
        resp = self.client.post(f"/api/inventory/returns/{rid}/cancel", format="json")
        assert resp.status_code == 200, resp.json()
        order.refresh_from_db()
        assert order.status == int(OrderStatus.DELIVERED_SUCCESS)
        push.assert_called_once()
        assert push.call_args.kwargs["status"] == int(OrderStatus.DELIVERED_SUCCESS)
```

- [ ] **Step 2: Chạy test — xác nhận FAIL**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory.tests.test_return_order_status.PostReturnOrderStatusTests.test_cancel_reverts_order_status`
Expected: FAIL — order.status vẫn RETURNED sau huỷ.

- [ ] **Step 3: Import `revert_order_status`**

Trong `return_note_service.py`, gộp vào import đã thêm ở Task 5:

```python
from apps.inventory.application.services.return_order_sync import (
    mark_order_returned,
    revert_order_status,
)
```

- [ ] **Step 4: Wiring trong `cancel_return`**

Thay khối cuối `cancel_return` (dòng 238-240) bằng:

```python
        rn.status = ReturnStatus.CANCELLED
        update_fields = ["status"]

        if rn.source_order_id and rn.order_status_before is not None:
            from apps.orders.models import Order, OrderStatus  # local import

            order = (
                Order.objects.select_for_update().filter(pk=rn.source_order_id).first()
            )
            if order is not None and int(order.status) == int(OrderStatus.RETURNED):
                revert_order_status(order, rn.order_status_before, user_id=user_id)
                rn.order_status_before = None
                update_fields.append("order_status_before")

        rn.save(update_fields=update_fields)
        return rn
```

- [ ] **Step 5: Chạy test — xác nhận PASS**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory.tests.test_return_order_status`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory/application/services/return_note_service.py backend/apps/inventory/tests/test_return_order_status.py
git commit -m "feat(inventory): revert source order status when cancelling return"
```

---

## Task 7: Frontend types + `buildReturnPayloads` bỏ `return_type`

**Files:**
- Modify: `frontend/src/features/inventory/returns-types.ts`
- Modify: `frontend/src/features/inventory/scan/scan-return-rows.ts:36-59`
- Test: `frontend/src/features/inventory/scan/__tests__/scan-return-rows.test.ts:52-77`

**Interfaces:**
- Produces: `buildReturnPayloads(rows, warehouseId)` (bỏ tham số `returnType`); payload không có `return_type`. `CreateReturnNotePayload.return_type` optional; `UpdateReturnNotePayload.return_type?`; `ReturnNoteRow.return_type` cho phép `""`.

- [ ] **Step 1: Sửa test cho khớp API mới**

Trong `scan-return-rows.test.ts`, thay `describe("buildReturnPayloads")` (dòng 52-77) bằng:

```typescript
describe("buildReturnPayloads", () => {
  it("dòng khớp: có đơn + khách + lines merge trùng product, bỏ item thiếu product", () => {
    const rows: ScanReturnRow[] = [{ kind: "matched", code: "VD-1", order: order() }];
    const [p] = buildReturnPayloads(rows, 7);
    expect(p).toEqual({
      warehouse_id: 7,
      shipping_code: "VD-1",
      source_order_id: 1,
      customer_name: "Khách A",
      customer_phone: "0900000000",
      lines: [{ product_id: 10, quantity: 5 }],
    });
  });

  it("dòng không khớp: chỉ mã + lines rỗng", () => {
    const rows: ScanReturnRow[] = [{ kind: "unmatched", code: "VD-LOST" }];
    const [p] = buildReturnPayloads(rows, 7);
    expect(p).toEqual({
      warehouse_id: 7,
      shipping_code: "VD-LOST",
      lines: [],
    });
  });
});
```

- [ ] **Step 2: Chạy test — xác nhận FAIL**

Run: `cd /home/desi/desi-order/frontend && npx vitest run src/features/inventory/scan/__tests__/scan-return-rows.test.ts`
Expected: FAIL — `buildReturnPayloads` vẫn nhận 3 tham số và set `return_type`.

- [ ] **Step 3: Sửa `buildReturnPayloads`**

Trong `scan-return-rows.ts`, thay dòng 36-59:

```typescript
export function buildReturnPayloads(
  rows: ScanReturnRow[],
  warehouseId: number,
): CreateReturnNotePayload[] {
  return rows.map((r) =>
    r.kind === "matched"
      ? {
          warehouse_id: warehouseId,
          shipping_code: r.code,
          source_order_id: r.order.order_id,
          customer_name: r.order.customer_name,
          customer_phone: r.order.customer_phone,
          lines: mergedLines(r.order),
        }
      : {
          warehouse_id: warehouseId,
          shipping_code: r.code,
          lines: [],
        },
  );
}
```

Và sửa import dòng 1 (bỏ `ReturnType` không dùng nữa):

```typescript
import type { CreateReturnNotePayload } from "../returns-types";
```

- [ ] **Step 4: Sửa types**

Trong `returns-types.ts`:
- Dòng 15: `return_type: ReturnType;` → `return_type: ReturnType | "";`
- Dòng 38 (trong `CreateReturnNotePayload`): `return_type: ReturnType;` → `return_type?: ReturnType;`
- Trong `UpdateReturnNotePayload` (sau dòng 50 `source_order_id?`), thêm: `return_type?: ReturnType;`

- [ ] **Step 5: Chạy test — xác nhận PASS**

Run: `cd /home/desi/desi-order/frontend && npx vitest run src/features/inventory/scan/__tests__/scan-return-rows.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/inventory/returns-types.ts frontend/src/features/inventory/scan/scan-return-rows.ts frontend/src/features/inventory/scan/__tests__/scan-return-rows.test.ts
git commit -m "feat(inventory): drop return_type from scan payload builder + types"
```

---

## Task 8: `ScanReturnDialog` — bỏ dropdown "Loại trả về"

**Files:**
- Modify: `frontend/src/features/inventory/scan/ScanReturnDialog.tsx`

**Interfaces:**
- Consumes: `buildReturnPayloads(rows, warehouseId)` (Task 7).

- [ ] **Step 1: Bỏ state + import không dùng**

- Xoá dòng 40: `const [returnType, setReturnType] = useState<ReturnType>("delivery_returned");`
- Xoá import `ReturnType` (dòng 19) và import `Select*` (dòng 9-15) — chỉ khi không còn `<Select>` nào khác. LƯU Ý: `<Select>` kho nhận (dòng 227) vẫn dùng → GIỮ import `Select*`. Chỉ xoá import `ReturnType` (dòng 19).

- [ ] **Step 2: Bỏ cột `<Select>` "Loại trả về"**

Xoá nguyên khối `<div>` "Loại trả về" (dòng 205-224). Đổi grid bao ngoài (dòng 204) từ `sm:grid-cols-2` thành `sm:grid-cols-1` (chỉ còn kho nhận), hoặc giữ `grid-cols-1` đơn giản:

```tsx
          <div className="grid grid-cols-1 gap-3">
            <div>
              <label className="mb-1 block text-sm text-muted-foreground">Kho nhận</label>
              {/* ...Select kho nhận giữ nguyên... */}
            </div>
          </div>
```

- [ ] **Step 3: Sửa lời gọi `buildReturnPayloads` + `reset`**

- Dòng 138: `const payloads = buildReturnPayloads(rows, returnType, Number(warehouseId));` → `const payloads = buildReturnPayloads(rows, Number(warehouseId));`
- Trong `reset()` (dòng 167-172): xoá dòng `setReturnType("delivery_returned");`

- [ ] **Step 4: Typecheck**

Run: `cd /home/desi/desi-order/frontend && npm run typecheck`
Expected: PASS, không lỗi về `returnType`/`ReturnType` unused.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/scan/ScanReturnDialog.tsx
git commit -m "feat(inventory): remove return-type select from scan dialog"
```

---

## Task 9: `CreateReturnDialog` — bỏ dropdown "Loại trả về"

**Files:**
- Modify: `frontend/src/features/inventory/pages/CreateReturnDialog.tsx`

- [ ] **Step 1: Bỏ state + payload field**

- Xoá dòng 69: `const [returnType, setReturnType] = useState<ReturnType>("delivery_returned");`
- Trong `resetForm()` (dòng 158-168): xoá `setReturnType("delivery_returned");`
- Trong `handleSubmit` (dòng 213-217): bỏ `return_type: returnType,` khỏi object `payload`:

```tsx
    const payload: CreateReturnNotePayload = {
      warehouse_id: Number(warehouseId),
      lines: validLines,
    };
```

- [ ] **Step 2: Bỏ cột `<Select>` "Loại trả về"**

Xoá khối `<div>` "Loại trả về" (dòng 255-274). `<Select>` kho nhận (dòng 276+) giữ nguyên → GIỮ import `Select*`. Bỏ import `ReturnType` khỏi dòng 19 (giữ `CreateReturnNotePayload`):

```tsx
import type { CreateReturnNotePayload } from "@/features/inventory/returns-types";
```

- [ ] **Step 3: Typecheck**

Run: `cd /home/desi/desi-order/frontend && npm run typecheck`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/inventory/pages/CreateReturnDialog.tsx
git commit -m "feat(inventory): remove return-type select from manual create dialog"
```

---

## Task 10: `ReturnNoteDetailDialog` — thêm dropdown phân loại

**Files:**
- Modify: `frontend/src/features/inventory/components/ReturnNoteDetailDialog.tsx`

**Interfaces:**
- Consumes: `useUpdateReturn` (đã có), `UpdateReturnNotePayload.return_type` (Task 7).

- [ ] **Step 1: Thêm import Select + hằng nhãn "Chưa phân loại"**

Thêm import (sau dòng 7):

```tsx
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ReturnType } from "@/features/inventory/returns-types";
```

Trong `RETURN_TYPE_LABELS` (dòng 11-16) KHÔNG thêm key `""` (dropdown chỉ 4 loại thật); dùng chuỗi "Chưa phân loại" trực tiếp ở JSX.

- [ ] **Step 2: Handler đổi loại**

Trong component, sau `attachOrder` (dòng 44), thêm:

```tsx
  async function handleChangeType(returnType: string) {
    if (returnId == null) return;
    try {
      const updated = await updateMutation.mutateAsync({
        id: returnId,
        payload: { return_type: returnType as ReturnType },
      });
      toast.success(`Đã cập nhật loại cho phiếu ${updated.code}.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Cập nhật loại thất bại.");
    }
  }
```

- [ ] **Step 3: Thay ô hiển thị "Loại" read-only bằng dropdown khi draft**

Thay khối `<p>` "Loại" (dòng 93-96) bằng:

```tsx
              <div>
                <span className="text-muted-foreground">Loại: </span>
                {note.status === "draft" ? (
                  <Select
                    value={note.return_type || undefined}
                    onValueChange={handleChangeType}
                  >
                    <SelectTrigger className="mt-1 h-8 w-full">
                      <SelectValue placeholder="Chưa phân loại — chọn loại" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="delivery_returned">Hàng hoàn</SelectItem>
                      <SelectItem value="external_return">Trả hàng ngoài</SelectItem>
                      <SelectItem value="warranty">Bảo hành</SelectItem>
                      <SelectItem value="defect">Đổi lỗi</SelectItem>
                    </SelectContent>
                  </Select>
                ) : (
                  RETURN_TYPE_LABELS[note.return_type] ?? "Chưa phân loại"
                )}
              </div>
```

- [ ] **Step 4: Typecheck**

Run: `cd /home/desi/desi-order/frontend && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/components/ReturnNoteDetailDialog.tsx
git commit -m "feat(inventory): classify return type from detail dialog"
```

---

## Task 11: Hiển thị "Chưa phân loại" + chặn ghi sổ khi chưa phân loại (ledger)

**Files:**
- Modify: `backend/apps/inventory/transport/views/ledger_views.py` (nhãn `type_label` cho `""`)
- Modify: `frontend/src/features/inventory/pages/StockDocumentsPage.tsx:892-910` (disable nút Ghi sổ)
- Modify: `frontend/src/features/inventory/components/ReturnCard.tsx:43` (nhãn "Chưa phân loại")

**Interfaces:**
- Consumes: ledger row `return_type` (đã có, giờ có thể `""`).

- [ ] **Step 1: Backend — nhãn ledger cho loại rỗng**

Trong `backend/apps/inventory/transport/views/ledger_views.py`, tại dòng gán `type_label` cho return (dòng ~176):

```python
            type_label = (
                "Trả hàng: "
                + (_RETURN_TYPE_LABELS.get(row["type_code"]) or "Chưa phân loại")
            )
```

- [ ] **Step 2: Frontend — disable Ghi sổ khi chưa phân loại**

Trong `StockDocumentsPage.tsx`, sửa nút Ghi sổ của return (dòng 893-909): thêm điều kiện `!r.return_type` vào `disabled` và `title`:

```tsx
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 px-2 text-xs text-green-700 border-green-300 hover:bg-green-50"
                              disabled={
                                postReturnMutation.isPending ||
                                (!!r.shipping_code && r.source_order == null) ||
                                !r.return_type
                              }
                              title={
                                !r.return_type
                                  ? "Phiếu chưa phân loại — mở Xem để chọn loại"
                                  : r.shipping_code && r.source_order == null
                                    ? "Phiếu chưa khớp đơn — mở Xem để gắn đơn trước"
                                    : undefined
                              }
                              onClick={() => handlePostReturn(r.id)}
                            >
                              Ghi sổ
                            </Button>
```

- [ ] **Step 3: Frontend — nhãn "Chưa phân loại" trên ReturnCard**

Trong `ReturnCard.tsx` dòng 43:

```tsx
            {RETURN_TYPE_LABELS[row.return_type as ReturnType] ?? "Chưa phân loại"}
```

- [ ] **Step 4: Typecheck**

Run: `cd /home/desi/desi-order/frontend && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Chạy test backend ledger (nếu có) + toàn bộ returns**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory.tests`
Expected: PASS toàn bộ (bỏ qua 2 lỗi tiền tồn về role "Administrator" nếu xuất hiện — theo memory running-backend-tests).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory/transport/views/ledger_views.py frontend/src/features/inventory/pages/StockDocumentsPage.tsx frontend/src/features/inventory/components/ReturnCard.tsx
git commit -m "feat(inventory): surface 'unclassified' and block posting until classified in ledger"
```

---

## Task 12: Kiểm thử tổng thể end-to-end

**Files:** (không sửa code — chỉ chạy verify)

- [ ] **Step 1: Toàn bộ test backend inventory**

Run: `PYTEST_ENV` với `<PATH>=apps.inventory`
Expected: PASS (trừ 2 lỗi tiền tồn về role nếu có).

- [ ] **Step 2: Toàn bộ test frontend inventory + typecheck**

Run:
```
cd /home/desi/desi-order/frontend && npx vitest run src/features/inventory && npm run typecheck
```
Expected: PASS.

- [ ] **Step 3: Lint frontend các file đã sửa**

Run: `cd /home/desi/desi-order/frontend && npm run lint`
Expected: Không lỗi mới ở các file trong plan.

- [ ] **Step 4: Commit (nếu có chỉnh sửa nhỏ do verify)**

```bash
git add -A && git commit -m "test(inventory): verify optional return_type + classify-later flow" || echo "nothing to commit"
```
