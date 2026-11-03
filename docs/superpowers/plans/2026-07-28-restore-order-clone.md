# Khôi phục đơn đã hủy bằng cách tạo đơn mới (clone) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đổi luồng khôi phục đơn đã hủy từ UPDATE in-place sang tạo một đơn mới (clone) giữ nguyên đơn hủy làm lịch sử.

**Architecture:** Task Celery `process_restore_orders` thôi tự UPDATE bản ghi cũ, thay vào đó gọi lại helper dùng chung `OrderService.duplicate_order` (đã tồn tại, phục vụ cả endpoint `/orders/<id>/duplicate`). Helper được mở rộng 3 tham số và copy thêm nhóm thuộc tính nghiệp vụ. Field mới `Order.restored_from` liên kết đơn mới → đơn hủy gốc và chặn khôi phục trùng. View, registry, và mọi lời gọi frontend giữ nguyên.

**Tech Stack:** Django 5 + DRF, Celery, PostgreSQL, pytest, React + TypeScript (chỉ sửa comment), drf-spectacular (schema contract).

**Spec:** `docs/superpowers/specs/2026-07-28-restore-order-clone-design.md`

## Global Constraints

- Worktree làm việc: `C:\Users\MYPC\Desktop\workspace\desi-order-restore-clone`, nhánh `feat/restore-order-clone`. Mọi lệnh chạy từ đây.
- Commit message bằng tiếng Anh, ngắn gọn, KHÔNG kèm dòng `Co-Authored-By`.
- Python 3.12, lint bắt buộc `ruff check .` theo `backend/pyproject.toml` (CI đỏ nếu vi phạm).
- KHÔNG đổi tên task `apps.orders.tasks.process_restore_orders`, không đổi chữ ký view, không đổi payload FE gửi lên.
- KHÔNG bao giờ gọi `cache.clear()` trong test (Redis dùng chung với dev).
- Test luôn patch hàm broadcast: `CHANNEL_LAYERS` trỏ Redis thật (`config/settings_parts/base.py:565`), không được bắn event thật khi chạy test.
- Migration trên bảng `orders` (bảng lớn, prod ~7GB DB) phải theo pattern có sẵn: `atomic = False`, `SeparateDatabaseAndState`, `SET LOCAL lock_timeout`, `CREATE INDEX CONCURRENTLY`. Tham chiếu mẫu: `backend/apps/orders/migrations/0105_order_reattached_invoice_codes.py`.

**Lệnh chạy test backend** (dùng nguyên văn, thay `<paths>`):

```bash
cd C:/Users/MYPC/Desktop/workspace/desi-order-restore-clone && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_restore_clone \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Lần chạy đầu dùng `--create-db`; các lần sau bỏ `--create-db` cho nhanh. Nếu image/network chưa sẵn sàng: image tên `desi-backend-test:latest`, và `desi-test-redis` cần `docker network connect desi-test-net desi-test-redis` một lần.

**Lệnh lint:**

```bash
cd C:/Users/MYPC/Desktop/workspace/desi-order-restore-clone && docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

---

## File Structure

| File | Trách nhiệm | Task |
|---|---|---|
| `backend/apps/orders/models.py` | Thêm field `Order.restored_from` + index | 1 |
| `backend/apps/orders/migrations/0111_order_restored_from.py` | Migration cột + index + FK constraint | 1 |
| `backend/apps/orders/tests/test_restore_cancelled_orders.py` | Test model link (task 1) + test task clone (task 3) | 1, 3 |
| `backend/apps/orders/application/services/order_service_helpers.py` | 3 tham số mới + nhóm field copy bổ sung cho `duplicate_order` | 2 |
| `backend/apps/orders/tests/test_order_duplicate_helper.py` | Test riêng cho helper `duplicate_order` | 2 |
| `backend/apps/orders/infrastructure/tasks/order_tasks_impl/restore_tasks.py` | Viết lại thân vòng lặp: clone thay vì UPDATE | 3 |
| `backend/apps/orders/transport/views/order_views_impl/order_action_view.py` | Sửa docstring (đi vào schema OpenAPI) | 4 |
| `backend/schema.yaml`, `frontend/src/api/schema.d.ts` | Regenerate theo docstring mới (CI check drift) | 4 |
| `frontend/src/hooks/use-crm-websocket.ts` | Sửa comment đã sai về hành vi | 4 |

---

## Task 1: Field `Order.restored_from` + migration

**Files:**
- Modify: `backend/apps/orders/models.py` (thêm field sau `reattached_invoice_codes`, dòng ~322-326; thêm index vào `Meta.indexes`)
- Create: `backend/apps/orders/migrations/0111_order_restored_from.py`
- Create: `backend/apps/orders/tests/test_restore_cancelled_orders.py`

**Interfaces:**
- Consumes: không có (task đầu tiên)
- Produces: `Order.restored_from` (FK tới `orders.Order`, nullable, `on_delete=SET_NULL`) và reverse accessor `order.restored_orders` (RelatedManager). Task 2 và 3 dựa vào hai tên này.

- [ ] **Step 1: Viết test thất bại cho quan hệ mới**

Tạo `backend/apps/orders/tests/test_restore_cancelled_orders.py`:

```python
"""Test luồng khôi phục đơn đã hủy: task tạo ĐƠN MỚI (clone), đơn cũ giữ nguyên.

Xem spec docs/superpowers/specs/2026-07-28-restore-order-clone-design.md.
"""
from __future__ import annotations

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderItem, OrderStatus, Product
from apps.users.test_helpers import make_admin


class RestoreOrderTestBase(TestCase):
    def setUp(self):
        self.actor = make_admin("restore-clone-actor")
        self.channel = Channel.objects.create(code="lp-restore", name="Restore Channel")
        self.product = Product.objects.create(name="SP Restore", sku="SKU-RESTORE-01")

    def _make_cancelled_order(self, suffix: str, **overrides) -> Order:
        customer = Customer.objects.create(
            full_name=f"Khach Restore {suffix}",
            phone_raw=f"09266000{suffix}",
            phone_normalized=f"09266000{suffix}",
            code=f"CUST-RES-{suffix}",
        )
        fields = dict(
            channel=self.channel,
            customer=customer,
            name=f"Khach Restore {suffix}",
            phone=f"09266000{suffix}",
            total_amount="200000",
            status=OrderStatus.NEW,
            invoice_status=2,
            invoice_code=f"HD-{suffix}",
            order_code=f"DH-{suffix}",
            shipping_code=f"SHIP-{suffix}",
            shipping_fee="30000",
        )
        fields.update(overrides)
        order = Order.objects.create(**fields)
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=2,
            unit_price=100000,
            line_total=200000,
        )
        return order


class RestoredFromFieldTests(RestoreOrderTestBase):
    def test_restored_from_links_new_order_to_source(self):
        source = self._make_cancelled_order("01")
        clone = self._make_cancelled_order("02", restored_from=source)

        self.assertEqual(clone.restored_from_id, source.id)
        self.assertEqual([o.id for o in source.restored_orders.all()], [clone.id])

    def test_restored_from_is_null_by_default(self):
        order = self._make_cancelled_order("03")

        self.assertIsNone(order.restored_from_id)
        self.assertEqual(order.restored_orders.count(), 0)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: lệnh test ở Global Constraints với `<paths>` = `apps/orders/tests/test_restore_cancelled_orders.py`

Expected: FAIL — `TypeError: Order() got unexpected keyword arguments: 'restored_from'`

- [ ] **Step 3: Thêm field vào model**

Trong `backend/apps/orders/models.py`, ngay sau field `reattached_invoice_codes` (kết thúc ở dòng ~326, trước `feedback`):

```python
    # Đơn hủy mà đơn này được clone ra khi bấm "Khôi phục". Null với đơn tạo
    # bình thường. Dùng để chặn khôi phục cùng một đơn hủy hai lần và để truy
    # vết đơn mới ↔ đơn hủy gốc. SET_NULL: xóa đơn hủy cũ không được kéo theo
    # đơn đang bán. db_index=False vì index đã khai báo tường minh ở Meta.
    restored_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="restored_orders",
        null=True,
        blank=True,
        db_index=False,
    )
```

Trong `class Meta.indexes` của `Order`, thêm vào cuối danh sách (sau index `ord_inv_status_cancel_idx`, dòng ~437-441):

```python
            # Task khôi phục hỏi "đơn hủy này đã có bản clone còn sống chưa" qua
            # `restored_orders`. Tập đơn có restored_from IS NOT NULL rất nhỏ so
            # với toàn bảng → partial index.
            models.Index(
                fields=["restored_from"],
                condition=Q(restored_from__isnull=False),
                name="ord_restored_from_idx",
            ),
```

- [ ] **Step 4: Viết migration**

Tạo `backend/apps/orders/migrations/0111_order_restored_from.py`:

```python
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Liên kết đơn khôi phục → đơn hủy gốc (`Order.restored_from`).

    ADD COLUMN nullable không default là metadata-only trên Postgres 11+, nhưng
    vẫn cần ACCESS EXCLUSIVE trong chốc lát → `lock_timeout` để fail nhanh thay
    vì xếp hàng chặn cả bảng `orders`. Index tạo CONCURRENTLY. FK thêm dạng
    NOT VALID rồi VALIDATE riêng để không scan toàn bảng khi đang giữ lock.
    """

    atomic = False

    dependencies = [
        ("orders", "0110_backfill_age_group"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name="order",
                    name="restored_from",
                    field=models.ForeignKey(
                        blank=True,
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="restored_orders",
                        to="orders.order",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "SET LOCAL lock_timeout = '5s';"
                        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS restored_from_id bigint NULL;"
                    ),
                    reverse_sql=(
                        "SET LOCAL lock_timeout = '5s';"
                        "ALTER TABLE orders DROP COLUMN IF EXISTS restored_from_id;"
                    ),
                ),
            ],
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="order",
                    index=models.Index(
                        condition=models.Q(("restored_from__isnull", False)),
                        fields=["restored_from"],
                        name="ord_restored_from_idx",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ord_restored_from_idx "
                        "ON orders (restored_from_id) WHERE restored_from_id IS NOT NULL;"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS ord_restored_from_idx;",
                ),
            ],
        ),
        migrations.RunSQL(
            sql=(
                "SET LOCAL lock_timeout = '5s';"
                "ALTER TABLE orders ADD CONSTRAINT orders_restored_from_id_fk "
                "FOREIGN KEY (restored_from_id) REFERENCES orders(id) "
                "DEFERRABLE INITIALLY DEFERRED NOT VALID;"
            ),
            reverse_sql="ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_restored_from_id_fk;",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE orders VALIDATE CONSTRAINT orders_restored_from_id_fk;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
```

> **NOTE (chốt sau final review 2026-07-29):** migration thật trong repo bọc
> `ADD CONSTRAINT` ở trên trong `DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM
> pg_constraint WHERE conname = 'orders_restored_from_id_fk') THEN ... END IF;
> END $$;` để re-runnable sau khi lỗi giữa chừng (Postgres không có `ADD
> CONSTRAINT IF NOT EXISTS`, và `atomic = False` nên lỗi ở bước VALIDATE để lại
> ADD CONSTRAINT đã áp dụng). Xem spec §3 và "Kết quả thực tế" cuối file.

- [ ] **Step 5: Chạy test để xác nhận PASS**

Run: lệnh test với `<paths>` = `apps/orders/tests/test_restore_cancelled_orders.py` (giữ `--create-db` để migration mới được áp dụng)

Expected: PASS 2 test.

- [ ] **Step 6: Kiểm tra không còn migration lệch state**

Run:

```bash
cd C:/Users/MYPC/Desktop/workspace/desi-order-restore-clone && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_restore_clone \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py makemigrations --check --dry-run
```

Expected: "No changes detected". Nếu Django báo còn thay đổi cho app `orders`, sửa state_operations cho khớp model rồi chạy lại.

- [ ] **Step 7: Lint + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/orders/models.py backend/apps/orders/migrations/0111_order_restored_from.py backend/apps/orders/tests/test_restore_cancelled_orders.py
git commit -m "feat(orders): add Order.restored_from link for restored orders"
```

---

## Task 2: Mở rộng helper `duplicate_order`

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_helpers.py:30-158`
- Create: `backend/apps/orders/tests/test_order_duplicate_helper.py`

**Interfaces:**
- Consumes: `Order.restored_from` (Task 1)
- Produces: chữ ký mới

```python
OrderService.duplicate_order(
    order: Order,
    actor,
    *,
    preserve_source: bool = False,
    reset_progress: bool = False,
    inherit_created_at: bool = False,
    default_sales_status: str | None = None,
    show_orders: bool | None = None,
    emit_event: bool = True,
    restored_from: Order | None = None,
    event_source: str = "orders.service.duplicate_order",
) -> Order
```

Task 3 gọi đúng chữ ký này.

- [ ] **Step 1: Viết test thất bại cho helper**

Tạo `backend/apps/orders/tests/test_order_duplicate_helper.py`:

```python
"""Test helper OrderService.duplicate_order.

Khóa 2 nhóm hành vi:
- nhóm field nghiệp vụ phải theo đơn gốc (VAT, thanh toán, gửi ngoài, ...);
- 3 tham số phục vụ luồng khôi phục: show_orders / emit_event / restored_from.
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import (
    Order,
    OrderItem,
    OrderSource,
    OrderStatus,
    OrderVat,
    Product,
    ShippingForm,
)
from apps.orders.services import OrderService
from apps.users.test_helpers import make_admin

PUSH_EVENT_PATH = (
    "apps.orders.application.services.order_service_helpers."
    "OrderServiceHelpers.push_event"
)


class DuplicateOrderHelperTests(TestCase):
    def setUp(self):
        self.actor = make_admin("duplicate-helper-actor")
        self.channel = Channel.objects.create(code="lp-dup", name="Dup Channel")
        self.product = Product.objects.create(name="SP Dup", sku="SKU-DUP-01")
        self.customer = Customer.objects.create(
            full_name="Khach Dup",
            phone_raw="0926600111",
            phone_normalized="0926600111",
            code="CUST-DUP-01",
        )
        self.order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Khach Dup",
            phone="0926600111",
            total_amount="200000",
            status=OrderStatus.NEW,
            order_source=OrderSource.LANDING,
            show_orders=True,
            payment_method="cod",
            vat=OrderVat.COMPANY,
            is_export_vat=True,
            vat_link="https://hoadon.example/abc",
            shipping_form=ShippingForm.TWO_WAY,
            outside=True,
            has_address=False,
        )
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            sku=self.product.sku,
            quantity=2,
            unit_price=100000,
            line_total=200000,
        )

    def test_copies_business_attributes(self):
        # NOTE (2026-07-29): quyết định cuối đã khác đoạn dưới — `is_export_vat`/
        # `vat_link` KHÔNG được copy nữa (marker "đã xuất VAT" của đơn gốc, copy
        # sang là sai nghiệp vụ). Test thật trong repo assert điều ngược lại của
        # 2 dòng `is_export_vat`/`vat_link` bên dưới. Xem "Kết quả thực tế" cuối file.
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertEqual(clone.payment_method, "cod")
        self.assertEqual(clone.vat, OrderVat.COMPANY)
        self.assertTrue(clone.is_export_vat)
        self.assertEqual(clone.vat_link, "https://hoadon.example/abc")
        self.assertEqual(clone.shipping_form, ShippingForm.TWO_WAY)
        self.assertTrue(clone.outside)
        self.assertFalse(clone.has_address)

    def test_show_orders_override_wins_over_reset_progress(self):
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(
                self.order,
                self.actor,
                preserve_source=True,
                reset_progress=True,
                show_orders=True,
            )

        self.assertTrue(clone.show_orders)

    def test_show_orders_none_keeps_legacy_behaviour(self):
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(
                self.order,
                self.actor,
                preserve_source=True,
                reset_progress=True,
            )

        self.assertFalse(clone.show_orders)

    def test_emit_event_false_does_not_push(self):
        with patch(PUSH_EVENT_PATH) as push_event:
            OrderService.duplicate_order(self.order, self.actor, emit_event=False)

        push_event.assert_not_called()

    def test_emit_event_default_pushes_once(self):
        with patch(PUSH_EVENT_PATH) as push_event:
            OrderService.duplicate_order(self.order, self.actor)

        self.assertEqual(push_event.call_count, 1)

    def test_restored_from_is_stored(self):
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(
                self.order, self.actor, restored_from=self.order
            )

        self.assertEqual(clone.restored_from_id, self.order.id)

    def test_restored_from_default_is_null(self):
        with patch(PUSH_EVENT_PATH):
            clone = OrderService.duplicate_order(self.order, self.actor)

        self.assertIsNone(clone.restored_from_id)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: lệnh test với `<paths>` = `apps/orders/tests/test_order_duplicate_helper.py`

Expected: FAIL — `TypeError: duplicate_order() got an unexpected keyword argument 'show_orders'` và các assert field nghiệp vụ sai (`clone.outside` là False, `clone.vat` là `"no"`).

- [ ] **Step 3: Mở rộng chữ ký helper**

Trong `backend/apps/orders/application/services/order_service_helpers.py`, sửa khối `def duplicate_order(...)` (bắt đầu dòng 30):

```python
    def duplicate_order(
        order: Order,
        actor,
        *,
        preserve_source: bool = False,
        reset_progress: bool = False,
        inherit_created_at: bool = False,
        default_sales_status: str | None = None,
        show_orders: bool | None = None,
        emit_event: bool = True,
        restored_from: Order | None = None,
        event_source: str = "orders.service.duplicate_order",
    ) -> Order:
```

- [ ] **Step 4: Copy thêm nhóm field nghiệp vụ + `restored_from`**

Trong cùng hàm, thêm vào `create_kwargs` ngay sau dòng `feedback=...` (dòng ~87):

```python
            # Thuộc tính nghiệp vụ của đơn (không phải marker hóa đơn/vận đơn) —
            # bản khôi phục và bản nhân đôi đều phải mang theo, nếu không đơn gửi
            # ngoài sẽ rơi khỏi trang "Đơn gửi ngoài" và đơn VAT tụt về "không xuất".
            payment_method=order.payment_method,
            vat=order.vat,
            is_export_vat=order.is_export_vat,
            vat_link=order.vat_link,
            shipping_form=order.shipping_form,
            outside=order.outside,
            has_address=order.has_address,
            restored_from=restored_from,
```

> **NOTE (chốt sau final review 2026-07-29):** code cuối cùng KHÔNG copy
> `is_export_vat` / `vat_link` (bỏ 2 dòng đó, giữ default model `False`/`""`) và
> có thêm `product_raw=order.product_raw`. `staff`/`branch_id` cũng đổi từ "bị
> `reset_progress` xoá" thành "luôn giữ đơn gốc" — xem chi tiết ở mục "Kết quả
> thực tế" cuối file và spec §2/§4.

- [ ] **Step 5: Cho `show_orders` quyền override**

Ngay sau khối `if preserve_source:` (kết thúc dòng ~94, trước `duplicated_order = Order.objects.create(...)`), thêm:

```python
        if show_orders is not None:
            # Luồng khôi phục quyết định đơn mới hiện ở danh sách nào (cờ
            # `restore_show_orders` từ FE), thắng mặc định của reset_progress.
            create_kwargs["show_orders"] = bool(show_orders)
```

- [ ] **Step 6: Cho phép tắt event**

Sửa khối push event ở cuối hàm (dòng ~150-157) thành:

```python
        if emit_event:
            OrderServiceHelpers.push_event(
                OrderEventContract.build_order_created(
                    order_id=duplicated_order.id,
                    channel_code=duplicated_order.channel.code,
                    status=duplicated_order.status,
                    source=event_source,
                )
            )
        return duplicated_order
```

- [ ] **Step 7: Cập nhật docstring/comment đầu hàm**

Ngay dưới dòng `) -> Order:`, thay khối comment `# reset_progress ...` hiện có bằng:

```python
        # `reset_progress` đưa đơn về đúng trạng thái "đơn mới ingest": rỗng staff,
        # rỗng kiotviet_channel/branch (do KiotViet sync sẽ tự gán lại), rỗng
        # result/feedback và bỏ qua bước clone OrderAssessment. Dùng cho luồng khôi phục
        # đơn đã hủy để đơn mới trông như đơn vừa ingest, không kế thừa trạng thái sale cũ.
        # `show_orders` (không None) override cờ hiển thị; `emit_event=False` để luồng
        # batch tự bắn một event tổng kết thay vì N event order.created; `restored_from`
        # trỏ về đơn hủy gốc khi đơn này sinh ra từ nút "Khôi phục".
```

- [ ] **Step 8: Chạy test để xác nhận PASS**

Run: lệnh test với `<paths>` = `apps/orders/tests/test_order_duplicate_helper.py`

Expected: PASS 7 test.

- [ ] **Step 9: Chạy hồi quy các test đụng tới duplicate/scope**

Run: lệnh test với `<paths>` = `apps/orders/tests/test_order_record_scope.py apps/orders/tests/test_close_case_record_scope.py`

Expected: PASS, không có test nào đỏ vì nhóm field mới.

- [ ] **Step 10: Lint + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/orders/application/services/order_service_helpers.py backend/apps/orders/tests/test_order_duplicate_helper.py
git commit -m "feat(orders): extend duplicate_order with show_orders, emit_event, restored_from"
```

---

## Task 3: Task `process_restore_orders` clone thay vì UPDATE

**Files:**
- Modify: `backend/apps/orders/infrastructure/tasks/order_tasks_impl/restore_tasks.py:1-175`
- Modify: `backend/apps/orders/tests/test_restore_cancelled_orders.py` (thêm class test mới vào file đã tạo ở Task 1)

**Interfaces:**
- Consumes: `Order.restored_from` / `order.restored_orders` (Task 1); chữ ký `duplicate_order` mới (Task 2)
- Produces: `process_restore_orders(order_ids: list[int], user_id: int | None = None, restore_show_orders: bool = False) -> dict` với các khóa `restored_count`, `skipped_count`, `restored_ids` (id đơn **mới**), `skipped_ids` (id đơn **gốc** bị bỏ qua). Chữ ký và tên task không đổi.

> **NOTE (chốt sau final review 2026-07-29):** dict trả về (và payload event
> `restore.completed`) có thêm 2 khóa `already_restored_count` /
> `already_restored_ids` — đơn "đã có bản khôi phục còn sống" tách khỏi
> `skipped_ids` sang bucket riêng này. Xem "Kết quả thực tế" cuối file.

- [ ] **Step 1: Viết test thất bại cho task**

Thêm vào cuối `backend/apps/orders/tests/test_restore_cancelled_orders.py`:

```python
class ProcessRestoreOrdersTests(RestoreOrderTestBase):
    """Task khôi phục phải TẠO ĐƠN MỚI và không đụng vào đơn hủy gốc."""

    def _run(self, order_ids, *, show_orders=False):
        from apps.orders.infrastructure.tasks.order_tasks_impl import restore_tasks

        with (
            patch.object(restore_tasks, "_broadcast_restore_completed") as broadcast,
            patch(PUSH_EVENT_PATH) as push_event,
        ):
            result = restore_tasks.process_restore_orders.run(
                order_ids,
                user_id=self.actor.id,
                restore_show_orders=show_orders,
            )
        return result, broadcast, push_event

    def test_creates_new_order_and_leaves_source_untouched(self):
        source = self._make_cancelled_order("10")

        result, _, _ = self._run([source.id])

        self.assertEqual(result["restored_count"], 1)
        new_id = result["restored_ids"][0]
        self.assertNotEqual(new_id, source.id)

        source.refresh_from_db()
        self.assertEqual(source.invoice_status, 2)
        self.assertEqual(source.invoice_code, "HD-10")
        self.assertEqual(source.order_code, "DH-10")
        self.assertEqual(source.shipping_code, "SHIP-10")

        clone = Order.objects.get(pk=new_id)
        self.assertEqual(clone.restored_from_id, source.id)
        self.assertEqual(clone.status, OrderStatus.NEW)
        self.assertIsNone(clone.invoice_status)
        self.assertEqual(clone.invoice_code, "")
        self.assertEqual(clone.order_code, "")
        self.assertEqual(clone.shipping_code, "")
        self.assertEqual(clone.shipping_fee, 0)

    def test_clone_copies_items_and_closed_assessment(self):
        source = self._make_cancelled_order("11")

        result, _, _ = self._run([source.id])
        clone = Order.objects.get(pk=result["restored_ids"][0])

        items = list(clone.items.all())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].product_id, self.product.id)
        self.assertEqual(items[0].quantity, 2)
        self.assertEqual(items[0].line_total, 200000)
        self.assertEqual(clone.assessment.sales_status_code, "closed")

    def test_clone_inherits_created_at(self):
        source = self._make_cancelled_order("12")
        source.refresh_from_db()

        result, _, _ = self._run([source.id])
        clone = Order.objects.get(pk=result["restored_ids"][0])

        self.assertEqual(clone.created_at, source.created_at)

    def test_show_orders_follows_flag(self):
        hidden_source = self._make_cancelled_order("13")
        shown_source = self._make_cancelled_order("14")

        hidden = self._run([hidden_source.id], show_orders=False)[0]
        shown = self._run([shown_source.id], show_orders=True)[0]

        self.assertFalse(Order.objects.get(pk=hidden["restored_ids"][0]).show_orders)
        self.assertTrue(Order.objects.get(pk=shown["restored_ids"][0]).show_orders)

    def test_copies_business_attributes(self):
        source = self._make_cancelled_order(
            "15",
            outside=True,
            vat=OrderVat.COMPANY,
            is_export_vat=True,
            payment_method="cod",
            shipping_form=ShippingForm.TWO_WAY,
            has_address=False,
        )

        result, _, _ = self._run([source.id])
        clone = Order.objects.get(pk=result["restored_ids"][0])

        self.assertTrue(clone.outside)
        self.assertEqual(clone.vat, OrderVat.COMPANY)
        self.assertTrue(clone.is_export_vat)
        self.assertEqual(clone.payment_method, "cod")
        self.assertEqual(clone.shipping_form, ShippingForm.TWO_WAY)
        self.assertFalse(clone.has_address)

    def test_second_restore_is_skipped(self):
        source = self._make_cancelled_order("16")

        first, _, _ = self._run([source.id])
        total_after_first = Order.objects.count()
        second, _, _ = self._run([source.id])

        self.assertEqual(first["restored_count"], 1)
        self.assertEqual(second["restored_count"], 0)
        self.assertEqual(second["skipped_ids"], [source.id])
        self.assertEqual(Order.objects.count(), total_after_first)

    def test_soft_deleted_clone_allows_restore_again(self):
        source = self._make_cancelled_order("17")

        first, _, _ = self._run([source.id])
        Order.objects.filter(pk=first["restored_ids"][0]).update(
            deleted_at=timezone.now()
        )
        second, _, _ = self._run([source.id])

        self.assertEqual(second["restored_count"], 1)

    def test_order_not_cancelled_is_skipped(self):
        source = self._make_cancelled_order("18", invoice_status=None)

        result, _, _ = self._run([source.id])

        self.assertEqual(result["restored_count"], 0)
        self.assertEqual(result["skipped_ids"], [source.id])

    def test_missing_and_deleted_orders_are_skipped(self):
        deleted = self._make_cancelled_order("19")
        Order.objects.filter(pk=deleted.id).update(deleted_at=timezone.now())

        result, _, _ = self._run([deleted.id, 99999999])

        self.assertEqual(result["restored_count"], 0)
        self.assertEqual(sorted(result["skipped_ids"]), sorted([deleted.id, 99999999]))

    def test_no_order_created_event_is_pushed(self):
        source = self._make_cancelled_order("20")

        _, broadcast, push_event = self._run([source.id])

        push_event.assert_not_called()
        broadcast.assert_called_once()

    def test_batch_continues_after_one_failure(self):
        good = self._make_cancelled_order("21")
        bad = self._make_cancelled_order("22")

        from apps.orders.infrastructure.tasks.order_tasks_impl import restore_tasks

        real_duplicate = OrderService.duplicate_order

        def flaky(order, actor, **kwargs):
            if order.id == bad.id:
                raise RuntimeError("boom")
            return real_duplicate(order, actor, **kwargs)

        with (
            patch.object(restore_tasks, "_broadcast_restore_completed"),
            patch(PUSH_EVENT_PATH),
            patch.object(OrderService, "duplicate_order", side_effect=flaky),
        ):
            result = restore_tasks.process_restore_orders.run(
                [bad.id, good.id], user_id=self.actor.id
            )

        self.assertEqual(result["restored_count"], 1)
        self.assertEqual(result["skipped_ids"], [bad.id])
```

Bổ sung import ở đầu file (sửa khối import đã viết ở Task 1):

```python
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import (
    Order,
    OrderItem,
    OrderStatus,
    OrderVat,
    Product,
    ShippingForm,
)
from apps.orders.services import OrderService
from apps.users.test_helpers import make_admin

PUSH_EVENT_PATH = (
    "apps.orders.application.services.order_service_helpers."
    "OrderServiceHelpers.push_event"
)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: lệnh test với `<paths>` = `apps/orders/tests/test_restore_cancelled_orders.py`

Expected: FAIL — task hiện tại UPDATE in-place nên `restored_ids[0] == source.id`, `source.invoice_status` bị đổi thành `None`, `restored_from` là None.

- [ ] **Step 3: Viết lại phần thân task**

Trong `backend/apps/orders/infrastructure/tasks/order_tasks_impl/restore_tasks.py`:

Thay khối docstring module (dòng 1-6) bằng:

```python
"""Task khôi phục đơn đã hủy.

Restore TẠO ĐƠN MỚI (clone qua `OrderService.duplicate_order`): đơn hủy gốc giữ
nguyên làm lịch sử, đơn mới trỏ về nó qua `Order.restored_from`. Cuối batch phát
một event `restore.completed` cho client hiện toast (không bắn `order.created`
từng đơn để tránh spam thông báo khi khôi phục lô lớn).
"""
```

Thay import models (dòng 17) bằng:

```python
from apps.orders.models import Order
from apps.orders.services import OrderService
```

Thay docstring + thân vòng lặp của `process_restore_orders` (dòng 71-158) bằng:

```python
    """Khôi phục danh sách đơn đã hủy → tạo đơn mới (clone), không sửa đơn cũ.

    - Chỉ xử lý đơn có invoice_status=2 (đã hủy hóa đơn) và chưa có bản khôi phục
      còn sống.
    - Đơn mới: status=NEW, sạch order_code/invoice_*/old_*/shipping_code để KiotViet
      push lại từ đầu, kế thừa created_at đơn gốc, show_orders theo cờ
      `restore_show_orders`, assessment preset sales_status_code='closed'.
    - Đơn cũ: KHÔNG đổi field nào, vẫn nằm tab "Đã hủy".
    - Broadcast duy nhất 1 event restore.completed cuối batch.
    """
    task_id = user_id
    logger.info(
        "Celery task started: process_restore_orders (task_id=%s, user_id=%s, count=%s)",
        task_id, user_id, len(order_ids or []),
    )

    actor = None
    if user_id:
        try:
            from apps.users.models import User

            actor = User.objects.filter(pk=user_id).first()
        except Exception:
            logger.exception("Failed to resolve actor user_id=%s", user_id)

    restored_ids: List[int] = []
    skipped_ids: List[int] = []

    for order_id in (order_ids or []):
        try:
            with transaction.atomic():
                order = (
                    Order.objects.select_for_update()
                    .select_related("channel")
                    .filter(pk=order_id, deleted_at__isnull=True)
                    .first()
                )
                if not order:
                    skipped_ids.append(order_id)
                    continue
                if order.invoice_status != 2:
                    # Chỉ khôi phục đơn thực sự đã hủy.
                    skipped_ids.append(order_id)
                    continue
                if order.restored_orders.filter(deleted_at__isnull=True).exists():
                    # Đã khôi phục rồi — không tạo bản thứ hai. Lock ở trên khiến
                    # hai request đồng thời không thể cùng lọt qua chỗ này.
                    skipped_ids.append(order_id)
                    continue

                new_order = OrderService.duplicate_order(
                    order,
                    actor,
                    preserve_source=True,
                    reset_progress=True,
                    inherit_created_at=True,
                    default_sales_status="closed",
                    show_orders=bool(restore_show_orders),
                    emit_event=False,
                    restored_from=order,
                    event_source="orders.tasks.restore_cancelled",
                )

            restored_ids.append(new_order.id)
        except Exception:
            logger.exception("Failed to restore order_id=%s", order_id)
            skipped_ids.append(order_id)
```

> **NOTE (chốt sau final review 2026-07-29):** code cuối cùng khác đoạn trên ở
> 2 điểm: (1) nhánh "đã có bản khôi phục còn sống" append vào `already_restored_ids`
> mới, không phải `skipped_ids`; (2) không truyền `event_source=...` cho
> `duplicate_order` nữa (dead arg, `emit_event=False` nên helper không dùng
> tới). Xem "Kết quả thực tế" cuối file.

Phần còn lại của hàm (`_broadcast_restore_completed`, `return {...}`) giữ nguyên. Riêng log finish ở dòng ~166 đang ghi `"Go task finished: ..."` — sửa thành `"Celery task finished: ..."` cho khớp với log start vừa đổi (luồng chạy bằng Celery, không còn Go worker).

- [ ] **Step 4: Dọn import và helper không còn dùng**

`OrderAssessment`, `OrderStatus`, `OrderEventContract` và hàm `_broadcast_payload` không còn được dùng sau khi bỏ UPDATE in-place và event `order.updated` từng đơn. Xóa `_broadcast_payload` (dòng 22-32) và các import tương ứng (`from apps.orders.contracts import OrderEventContract`). Giữ `_broadcast_restore_completed` và import `get_channel_layer` / `async_to_sync`.

- [ ] **Step 5: Chạy test để xác nhận PASS**

Run: lệnh test với `<paths>` = `apps/orders/tests/test_restore_cancelled_orders.py`

Expected: PASS toàn bộ (2 test Task 1 + 11 test mới).

- [ ] **Step 6: Chạy hồi quy test quyền của endpoint restore**

Run: lệnh test với `<paths>` = `apps/orders/tests/test_order_bulk_action_permissions.py`

Expected: PASS — các test này mock `dispatch` ở tầng view nên không phụ thuộc thân task.

- [ ] **Step 7: Lint + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/orders/infrastructure/tasks/order_tasks_impl/restore_tasks.py backend/apps/orders/tests/test_restore_cancelled_orders.py
git commit -m "feat(orders): restore cancelled orders by cloning instead of in-place update"
```

---

## Task 4: Cập nhật docstring, comment FE, schema contract

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_action_view.py:294`
- Modify: `frontend/src/hooks/use-crm-websocket.ts:138-140`
- Regenerate: `backend/schema.yaml`, `frontend/src/api/schema.d.ts`

**Interfaces:**
- Consumes: hành vi task mới (Task 3)
- Produces: không có API mới; chỉ đồng bộ tài liệu/contract

- [ ] **Step 1: Sửa docstring view**

Trong `backend/apps/orders/transport/views/order_views_impl/order_action_view.py`, dòng 294, thay:

```python
    """Enqueue Go task để UPDATE in-place đơn đã hủy. Worker bắn 'restore.completed' khi xong."""
```

bằng:

```python
    """Enqueue task tạo đơn mới (clone) từ đơn đã hủy; đơn cũ giữ nguyên làm lịch sử.

    Worker bắn 'restore.completed' khi xong batch."""
```

- [ ] **Step 2: Sửa comment frontend đã sai**

Trong `frontend/src/hooks/use-crm-websocket.ts`, thay comment ở dòng 138-139:

```ts
          // Refresh list hiện tại để row đã cập nhật biến mất khỏi tab "Đã hủy"
          // (vì invoice_status giờ là NULL).
```

bằng:

```ts
          // Refresh list hiện tại: khôi phục tạo ĐƠN MỚI (đơn hủy cũ vẫn nằm
          // nguyên ở tab "Đã hủy"), đơn mới chỉ xuất hiện sau khi load lại.
```

- [ ] **Step 3: Regenerate schema backend**

Run:

```bash
cd C:/Users/MYPC/Desktop/workspace/desi-order-restore-clone && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_restore_clone \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py spectacular --file schema.yaml
```

Expected: `backend/schema.yaml` thay đổi đúng ở mô tả endpoint `/api/orders/restore-cancelled`, không có dòng `Error [` trong output.

- [ ] **Step 4: Regenerate type frontend**

Run:

```bash
cd C:/Users/MYPC/Desktop/workspace/desi-order-restore-clone/frontend && npm run gen:api
```

Expected: `frontend/src/api/schema.d.ts` cập nhật mô tả endpoint tương ứng. Nếu `node_modules` chưa có trong worktree, tạo junction tới `node_modules` của repo chính trước khi chạy.

- [ ] **Step 5: Chạy toàn bộ test app orders**

Run: lệnh test với `<paths>` = `apps/orders`

Expected: PASS toàn bộ. Nếu có test đỏ, dừng lại sửa trước khi commit — không bỏ qua.

- [ ] **Step 6: Lint backend + frontend**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
cd frontend && npm run lint && npx tsc --noEmit
```

Expected: sạch cả ba.

- [ ] **Step 7: Commit + push + tạo MR**

```bash
git add backend/apps/orders/transport/views/order_views_impl/order_action_view.py backend/schema.yaml frontend/src/api/schema.d.ts frontend/src/hooks/use-crm-websocket.ts
git commit -m "docs(orders): update restore docs and API schema for clone behaviour"
git push -u origin feat/restore-order-clone
```

Sau khi push, lấy link MR từ output của `git push` (GitLab in ra sẵn) và gửi cho người dùng.

---

## Kiểm tra cuối (do người review chạy)

- [ ] Đơn hủy sau khi khôi phục vẫn nằm tab "Đã hủy" với đầy đủ `invoice_code`/`order_code`
- [ ] Đơn mới xuất hiện đúng danh sách: trang đơn thường/KOC/gửi ngoài (`show_orders=true`), tab Landing (`show_orders=false`)
- [ ] Đơn gửi ngoài khôi phục xong vẫn nằm trang "Đơn gửi ngoài" (`outside` được copy)
- [ ] Bấm khôi phục lần hai trên cùng đơn → toast riêng báo "đã khôi phục trước đó" (không gộp vào "không hợp lệ"), không sinh đơn thứ ba
- [ ] Khôi phục lô ~20 đơn → chỉ một toast tổng kết, không có mưa thông báo chuông

---

## Kết quả thực tế (cập nhật 2026-07-29)

Toàn bộ 4 Task đã merge vào `feat/restore-order-clone`. Commit đã land (cũ → mới):

1. `0ead50ee` feat(orders): add Order.restored_from link for restored orders
2. `6b856814` feat(orders): extend duplicate_order with show_orders, emit_event, restored_from
3. `04e701a5` feat(orders): restore cancelled orders by cloning instead of in-place update
4. `a0e9182a` docs(orders): update restore docs and API schema for clone behaviour
5. `a527b46e` fix(orders): gate shipping_fee reset behind reset_progress in duplicate_order
6. `fee22185` fix(orders): exclude cancelled orders with a live restore from summary_overview
7. `bced9bd7` fix(orders): keep staff/branch_id and drop VAT invoice markers on duplicate_order
8. `f3abbbf1` test(orders): cover staff/branch_id/VAT-marker copy and already_restored_ids

Commit 1-4 là 4 Task gốc của plan này, đúng như thiết kế ban đầu. Commit 5-8 là
đợt sửa sau **final review ngày 2026-07-29**, phát sinh 5 điểm khác so với plan
ban đầu (chi tiết đầy đủ + lý do nghiệp vụ ở
`docs/superpowers/specs/2026-07-28-restore-order-clone-design.md` §2/§4/§8/§9;
plan này chỉ tóm tắt, các NOTE inline ở trên đánh dấu đúng vị trí):

1. **`is_export_vat` / `vat_link` không còn được `duplicate_order` copy.** Plan
   ban đầu (Task 2, Step 4) liệt kê 2 field này vào nhóm copy vô điều kiện —
   sai. Đây là marker "đã xuất VAT" + link hóa đơn của đơn gốc, copy sang đơn
   mới khiến kế toán tưởng đã xuất VAT.
2. **`staff` và `branch_id` luôn được giữ từ đơn gốc**, kể cả khi
   `reset_progress=True`. Trước đó (code trước final review) 2 field này bị
   đặt `None` / `""` khi `reset_progress=True` — không có trong plan này vì nó
   là hành vi cũ của helper, chỉ lộ ra là bug khi dùng cho luồng khôi phục
   (sale mất quyền xem đơn mình vừa khôi phục; đơn chi nhánh nội bộ lọt vào
   báo cáo công ty).
3. **`product_raw` được thêm vào nhóm copy** của `duplicate_order` (không có
   trong Task 2 ban đầu).
4. **`shipping_fee` reset về 0 được chuyển vào gate trong helper**
   (`shipping_fee=0 if reset_progress else order.shipping_fee`) thay vì một
   UPDATE riêng sau khi tạo đơn ở task (cách plan Task 3 mô tả ban đầu, đã bị
   bỏ ở commit `a527b46e`).
5. **`summary_overview` loại đơn hủy có bản khôi phục còn sống** khỏi tập cơ
   sở (`backend/apps/orders/_repositories_reports.py`), bằng
   `Exists(Order.objects.filter(restored_from_id=OuterRef("pk"), deleted_at__isnull=True))`.
   File này **không có trong File Structure / bất kỳ Task nào của plan gốc** —
   phát sinh hoàn toàn từ final review, vì clone kế thừa `created_at` khiến
   đơn gốc (bucket hủy) và clone (bucket net/pending) rơi vào cùng kỳ báo cáo,
   cộng đúp `gross_revenue`. Cách làm SAI đã cân nhắc và bỏ:
   `.exclude(restored_orders__deleted_at__isnull=True)` — lookup `__isnull=True`
   qua reverse FK sinh LEFT JOIN nên khớp cả đơn không có bản khôi phục nào,
   xoá gần hết bảng `orders` nếu dùng nhầm.

Ngoài 5 điểm trên, `restore.completed` / kết quả trả về của task có thêm
`already_restored_count` / `already_restored_ids` tách khỏi `skipped_ids`
(commit `bced9bd7`) và FE (`use-crm-websocket.ts`) hiện toast riêng cho nhóm
này — cũng không có trong Task 3/4 gốc.

**Test cuối cùng** (`apps/orders`, container `desi-backend-test:latest`):
**833 passed, 16 subtests, 0 failed.**

**Việc chưa làm / ngoài phạm vi** (không đổi so với §Ngoài phạm vi trong spec):
không có UI hiển thị liên kết đơn mới ↔ đơn hủy; không backfill `restored_from`
cho các đơn từng khôi phục bằng in-place trước bản này.
