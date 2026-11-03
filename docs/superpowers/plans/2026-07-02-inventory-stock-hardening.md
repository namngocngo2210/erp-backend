# Inventory Stock Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Củng cố tầng kho `apps/inventory` — dựng lưới test cho primitive ghi tồn, đổi `record_movement` sang atomic upsert, và partition ledger `stock_movements` theo tháng.

**Architecture:** Ledger bất biến (`StockMovement`) + số dư materialized (`StockItem`), ghi qua `StockCommandService.record_movement` trong `@transaction.atomic`. Giữ nguyên contract của service; chỉ đổi cơ chế bên trong (upsert) và storage của ledger (partitioned table qua `SeparateDatabaseAndState`).

**Tech Stack:** Django 6.0.3, DRF, PostgreSQL (partitioning + `ON CONFLICT`), Celery (`@shared_task` + `CELERY_BEAT_SCHEDULE` tĩnh), pytest-django.

## Global Constraints

- **Python 3.12+, Django 6.0.3** — dùng ORM/migrations idiomatic.
- **PostgreSQL only** — upsert `ON CONFLICT` và RANGE partition là PG-specific; không cần tương thích SQLite.
- **Clean layering:** transport → business (`application/services`) → model. Không đưa SQL vào transport; primitive nằm ở `StockCommandService`.
- **TDD bắt buộc:** test đỏ trước, code sau; refactor (Task 3) phải giữ test Task 1–2 xanh nguyên vẹn.
- **Commit nhỏ, tiếng Anh, không kèm Co-Authored-By.**
- **Contract bất biến:** `record_movement(...) -> MovementResult(movement_id, balance_after, went_negative)` không đổi chữ ký.
- **Celery time limit:** global `CELERY_TASK_TIME_LIMIT=30`; task DDL phải override `soft_time_limit`/`time_limit`.

### Running tests

Lệnh test đã validate (chạy từ **repo root**, Git Bash trên Windows). Postgres + redis đang chạy sẵn (`desi-order-postgres-1`, `desi-order-redis-1`) trên network `desi-order_default`; redis password = `testpassword`:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-order_default \
  --env-file .env --env-file backend/.env \
  -e POSTGRES_HOST=postgres -e REDIS_HOST=redis -e REDIS_PASSWORD=testpassword \
  -v "$PWD/backend:/app" -w /app \
  desi-order-web-test:latest pytest <path> -q
```

`<path>` là đường dẫn tương đối trong `backend/` (vd `apps/inventory/tests/test_return_api.py`). Các bước dưới ghi `pytest <path>` cho gọn — luôn bọc bằng wrapper trên. `MSYS_NO_PATHCONV=1` bắt buộc để Git Bash không mangle `-w /app`.

---

### Task 1: Unit test trực tiếp cho `record_movement`

**Files:**
- Create: `backend/apps/inventory/tests/test_stock_command_service.py`

**Interfaces:**
- Consumes: `StockCommandService.record_movement(*, warehouse_id, product_id, qty_delta, movement_type, state=StockState.SELLABLE, reference_type=None, reference_id=None, note="", user_id=None) -> MovementResult` từ `apps/inventory/application/services/stock_command_service.py`.
- Produces: không có (chỉ test).

- [ ] **Step 1: Viết test đỏ — happy path + zero-guard + oversell**

```python
# backend/apps/inventory/tests/test_stock_command_service.py
from __future__ import annotations

import threading

import pytest
from django.test import TestCase, TransactionTestCase

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType, StockItem, StockMovement, StockState
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


class RecordMovementTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(code="W-CMD", name="Kho CMD")
        self.product = Product.objects.create(name="SP CMD", sku="CMD-1")

    def _record(self, qty_delta, **kw):
        return StockCommandService.record_movement(
            warehouse_id=self.wh.id,
            product_id=self.product.id,
            qty_delta=qty_delta,
            movement_type=kw.pop("movement_type", MovementType.RECEIPT),
            **kw,
        )

    def test_first_movement_creates_stock_item_and_matches_balance(self):
        result = self._record(10)
        item = StockItem.objects.get(
            warehouse=self.wh, product=self.product, state=StockState.SELLABLE
        )
        assert item.quantity == 10
        assert result.balance_after == 10
        assert result.went_negative is False
        assert StockMovement.objects.get(id=result.movement_id).balance_after == 10

    def test_accumulates_across_movements(self):
        self._record(10)
        self._record(5)
        r = self._record(-3)
        assert r.balance_after == 12
        item = StockItem.objects.get(
            warehouse=self.wh, product=self.product, state=StockState.SELLABLE
        )
        assert item.quantity == 12

    def test_zero_delta_raises(self):
        with pytest.raises(ValueError):
            self._record(0)

    def test_oversell_sets_went_negative(self):
        r = self._record(-4)
        assert r.went_negative is True
        assert r.balance_after == -4

    def test_state_isolation(self):
        self._record(10, state=StockState.SELLABLE)
        self._record(2, state=StockState.DEFECT)
        assert StockItem.objects.get(
            warehouse=self.wh, product=self.product, state=StockState.SELLABLE
        ).quantity == 10
        assert StockItem.objects.get(
            warehouse=self.wh, product=self.product, state=StockState.DEFECT
        ).quantity == 2
```

- [ ] **Step 2: Chạy test — xác nhận PASS trên code hiện tại**

Run: `pytest apps/inventory/tests/test_stock_command_service.py -v`
Expected: 5 PASS (đây là characterization test cho hành vi hiện có, dùng làm mốc cho Task 3).

- [ ] **Step 3: Thêm test concurrency (chống lost update)**

Bổ sung vào cùng file:

```python
class RecordMovementConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.wh = Warehouse.objects.create(code="W-CC", name="Kho CC")
        self.product = Product.objects.create(name="SP CC", sku="CC-1")
        StockCommandService.record_movement(
            warehouse_id=self.wh.id, product_id=self.product.id,
            qty_delta=100, movement_type=MovementType.RECEIPT,
        )

    def test_concurrent_decrements_no_lost_update(self):
        from django.db import connection

        threads = []
        errors = []

        def worker():
            try:
                StockCommandService.record_movement(
                    warehouse_id=self.wh.id, product_id=self.product.id,
                    qty_delta=-1, movement_type=MovementType.SALE,
                    state=StockState.SELLABLE,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        for _ in range(20):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        item = StockItem.objects.get(
            warehouse=self.wh, product=self.product, state=StockState.SELLABLE
        )
        assert item.quantity == 80  # 100 - 20*1, không mất update
```

- [ ] **Step 4: Chạy toàn file — xác nhận PASS**

Run: `pytest apps/inventory/tests/test_stock_command_service.py -v`
Expected: tất cả PASS. Nếu concurrency test flaky/không đủ isolation (SQLite), xác nhận đang chạy trên PostgreSQL container.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/tests/test_stock_command_service.py
git commit -m "test(inventory): direct unit + concurrency tests for record_movement"
```

---

### Task 2: Test mapping đủ 4 `return_type` → `StockState`

**Files:**
- Modify: `backend/apps/inventory/tests/test_return_api.py` (thêm test, giữ nguyên test cũ)

**Interfaces:**
- Consumes: endpoint `POST /api/inventory/returns`, `POST /api/inventory/returns/{id}/post`, `GET /api/inventory/stock?state=...`; ánh xạ `_RETURN_STATE` trong `return_note_service.py`.
- Produces: không có.

- [ ] **Step 1: Viết test đỏ — parametrize 4 loại**

Thêm class mới vào cuối `test_return_api.py`:

```python
class ReturnTypeStateMappingTests(TestCase):
    def setUp(self):
        self.user = make_admin("admin_ret_map")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wh = Warehouse.objects.create(code="WR-MAP", name="Kho Map")
        self.product = Product.objects.create(name="SP Map", sku="MAP-1")

    def _create_and_post(self, return_type):
        resp = self.client.post(
            "/api/inventory/returns",
            data={
                "return_type": return_type,
                "warehouse_id": self.wh.id,
                "lines": [{"product_id": self.product.id, "quantity": 7}],
            },
            format="json",
        )
        assert resp.status_code == 201, resp.json()
        rid = resp.json()["id"]
        post = self.client.post(f"/api/inventory/returns/{rid}/post", format="json")
        assert post.status_code == 200, post.json()

    def _qty_in_state(self, state):
        resp = self.client.get(
            "/api/inventory/stock", {"limit": 50, "offset": 0, "state": state}
        )
        items = [i for i in resp.json()["items"] if i["product_sku"] == "MAP-1"]
        return items[0]["quantity"] if items else 0

    def test_delivery_returned_goes_to_sellable(self):
        self._create_and_post("delivery_returned")
        assert self._qty_in_state("sellable") == 7

    def test_external_return_goes_to_sellable(self):
        self._create_and_post("external_return")
        assert self._qty_in_state("sellable") == 7

    def test_warranty_goes_to_warranty(self):
        self._create_and_post("warranty")
        assert self._qty_in_state("warranty") == 7

    def test_defect_goes_to_defect(self):
        self._create_and_post("defect")
        assert self._qty_in_state("defect") == 7
```

- [ ] **Step 2: Chạy — xác nhận PASS (mapping đã đúng trong code hiện tại)**

Run: `pytest apps/inventory/tests/test_return_api.py::ReturnTypeStateMappingTests -v`
Expected: 4 PASS. Nếu FAIL, mapping `_RETURN_STATE` sai → dừng và báo (không sửa test cho khớp bug).

- [ ] **Step 3: Chạy cả file return — không hồi quy**

Run: `pytest apps/inventory/tests/test_return_api.py -v`
Expected: toàn bộ (cũ + mới) PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/apps/inventory/tests/test_return_api.py
git commit -m "test(inventory): cover all 4 return_type -> stock state mappings"
```

---

### Task 3: Đổi `record_movement` sang atomic upsert

**Files:**
- Modify: `backend/apps/inventory/application/services/stock_command_service.py:22-63`

**Interfaces:**
- Consumes: `StockItem` unique constraint `uniq_stock_item_wh_prod_state` trên `(warehouse_id, product_id, state)`.
- Produces: `MovementResult` không đổi — mọi caller (`stock_sales_service`, `return_note_service`, `stock_document_service`, `stock_reclass_service`) không phải sửa.

- [ ] **Step 1: Chạy lại test Task 1 làm mốc vàng (phải xanh trước khi đổi)**

Run: `pytest apps/inventory/tests/test_stock_command_service.py -v`
Expected: tất cả PASS. Đây là hợp đồng hành vi trước refactor.

- [ ] **Step 2: Thay thân `record_movement` bằng upsert**

Thay khối từ `StockItem.objects.get_or_create(...)` đến `item.save(...)` bằng:

```python
    @staticmethod
    @transaction.atomic
    def record_movement(
        *,
        warehouse_id: int,
        product_id: int,
        qty_delta: int,
        movement_type: str,
        state: str = StockState.SELLABLE,
        reference_type: str | None = None,
        reference_id: int | None = None,
        note: str = "",
        user_id: int | None = None,
    ) -> MovementResult:
        if qty_delta == 0:
            raise ValueError("qty_delta must be non-zero.")

        # Atomic upsert: 1 statement, lock ngầm trên đúng 1 row (warehouse, product, state).
        # RETURNING trả số dư sau cộng dồn, bỏ được race của get_or_create + select_for_update.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO stock_items
                    (warehouse_id, product_id, state, quantity, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (warehouse_id, product_id, state)
                DO UPDATE SET quantity = stock_items.quantity + EXCLUDED.quantity,
                              updated_at = now()
                RETURNING quantity;
                """,
                [warehouse_id, product_id, state, qty_delta],
            )
            new_balance = cursor.fetchone()[0]

        movement = StockMovement.objects.create(
            warehouse_id=warehouse_id,
            product_id=product_id,
            qty_delta=qty_delta,
            movement_type=movement_type,
            state=state,
            balance_after=new_balance,
            reference_type=reference_type,
            reference_id=reference_id,
            note=note,
            created_by_id=user_id,
        )
        return MovementResult(
            movement_id=movement.id, balance_after=new_balance, went_negative=new_balance < 0
        )
```

- [ ] **Step 3: Thêm import `connection`**

Sửa đầu file `stock_command_service.py`:

```python
from django.db import connection, transaction
```

Và bỏ import `StockItem` nếu không còn dùng ở chỗ khác trong file (kiểm tra: `StockItem` chỉ còn trong upsert SQL → xoá khỏi dòng `from apps.inventory.models import StockItem, StockMovement, StockState`, giữ lại `StockMovement, StockState`).

- [ ] **Step 4: Chạy test Task 1 + Task 2 — phải xanh y nguyên**

Run: `pytest apps/inventory/tests/test_stock_command_service.py apps/inventory/tests/test_return_api.py -v`
Expected: tất cả PASS (bằng chứng refactor không đổi hành vi).

- [ ] **Step 5: Chạy test hồi quy rộng — document approval + dispatch**

Run: `pytest apps/inventory/tests/ apps/orders/tests/test_dispatch_stock.py -v`
Expected: tất cả PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory/application/services/stock_command_service.py
git commit -m "perf(inventory): atomic upsert for stock balance in record_movement"
```

---

### Task 4: Partition `stock_movements` theo tháng (migration swap)

**Files:**
- Create: `backend/apps/inventory/migrations/0008_partition_stock_movements.py`
- Create: `backend/apps/inventory/tests/test_stock_movements_partition.py`

**Interfaces:**
- Consumes: bảng `stock_movements` hiện tại (cột theo model `StockMovement`), sequence `stock_movements_id_seq`.
- Produces: bảng cha partitioned `stock_movements` PK `(id, created_at)` + partitions theo tháng + partition `DEFAULT`. Model Django không đổi (state giữ PK `id`).

- [ ] **Step 1: Viết test đỏ — insert route đúng & query theo tháng**

```python
# backend/apps/inventory/tests/test_stock_movements_partition.py
from __future__ import annotations

from django.db import connection
from django.test import TransactionTestCase

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType, StockMovement
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


class StockMovementsPartitionTests(TransactionTestCase):
    reset_sequences = True

    def test_parent_table_is_partitioned_by_range(self):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT partstrat FROM pg_partitioned_table pt "
                "JOIN pg_class c ON c.oid = pt.partrelid "
                "WHERE c.relname = 'stock_movements';"
            )
            row = cur.fetchone()
        assert row is not None, "stock_movements phải là partitioned table"
        assert row[0] == "r"  # RANGE

    def test_insert_routes_and_reads_back(self):
        wh = Warehouse.objects.create(code="W-PART", name="Kho Part")
        product = Product.objects.create(name="SP Part", sku="PART-1")
        result = StockCommandService.record_movement(
            warehouse_id=wh.id, product_id=product.id,
            qty_delta=5, movement_type=MovementType.RECEIPT,
        )
        mv = StockMovement.objects.get(id=result.movement_id)
        assert mv.qty_delta == 5
        assert mv.balance_after == 5
```

- [ ] **Step 2: Chạy — xác nhận FAIL (bảng chưa partitioned)**

Run: `pytest apps/inventory/tests/test_stock_movements_partition.py::StockMovementsPartitionTests::test_parent_table_is_partitioned_by_range -v`
Expected: FAIL — `row is None` (bảng phẳng). `test_insert_routes_and_reads_back` có thể PASS sẵn.

- [ ] **Step 3: Viết migration swap**

```python
# backend/apps/inventory/migrations/0008_partition_stock_movements.py
from django.db import migrations

FORWARD_SQL = r"""
-- 1. Đổi tên bảng phẳng hiện tại
ALTER TABLE stock_movements RENAME TO stock_movements_old;

-- 2. Bảng cha partitioned, PK gồm partition key (created_at)
CREATE TABLE stock_movements (
    id bigint NOT NULL DEFAULT nextval('stock_movements_id_seq'::regclass),
    warehouse_id bigint NOT NULL,
    product_id bigint NOT NULL,
    qty_delta integer NOT NULL,
    movement_type varchar(20) NOT NULL,
    state varchar(20) NOT NULL,
    balance_after integer NOT NULL,
    reference_type varchar(20),
    reference_id bigint,
    note text NOT NULL DEFAULT '',
    created_by_id bigint,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

ALTER SEQUENCE stock_movements_id_seq OWNED BY stock_movements.id;

-- 3. Partition DEFAULT (lưới an toàn, không bao giờ rớt insert)
CREATE TABLE stock_movements_default PARTITION OF stock_movements DEFAULT;

-- 4. Partition phủ toàn bộ dữ liệu cũ (1 partition rộng cho lịch sử) — biên phải mở tới
--    đầu tháng sau để bao trọn "now()"; các tháng tiếp theo do Celery beat tạo dần.
DO $$
DECLARE
    lo date := date_trunc('month', COALESCE((SELECT min(created_at) FROM stock_movements_old), now()))::date;
    hi date := (date_trunc('month', now()) + interval '2 month')::date;
BEGIN
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS stock_movements_hist PARTITION OF stock_movements '
        'FOR VALUES FROM (%L) TO (%L)', lo, hi
    );
END $$;

-- 5. Copy dữ liệu
INSERT INTO stock_movements
    (id, warehouse_id, product_id, qty_delta, movement_type, state, balance_after,
     reference_type, reference_id, note, created_by_id, created_at)
SELECT id, warehouse_id, product_id, qty_delta, movement_type, state, balance_after,
       reference_type, reference_id, note, created_by_id, created_at
FROM stock_movements_old;

-- 6. Sequence về đúng max id
SELECT setval('stock_movements_id_seq', COALESCE((SELECT max(id) FROM stock_movements), 1));

-- 7. Tạo lại index (propagate xuống partition)
CREATE INDEX stockmove_wh_prod_idx ON stock_movements (warehouse_id, product_id, created_at);
CREATE INDEX stockmove_ref_idx ON stock_movements (reference_type, reference_id);

-- 8. Bỏ bảng cũ
DROP TABLE stock_movements_old;
"""

REVERSE_SQL = r"""
ALTER TABLE stock_movements RENAME TO stock_movements_part;

CREATE TABLE stock_movements (
    id bigint NOT NULL DEFAULT nextval('stock_movements_id_seq'::regclass),
    warehouse_id bigint NOT NULL,
    product_id bigint NOT NULL,
    qty_delta integer NOT NULL,
    movement_type varchar(20) NOT NULL,
    state varchar(20) NOT NULL,
    balance_after integer NOT NULL,
    reference_type varchar(20),
    reference_id bigint,
    note text NOT NULL DEFAULT '',
    created_by_id bigint,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (id)
);
ALTER SEQUENCE stock_movements_id_seq OWNED BY stock_movements.id;

INSERT INTO stock_movements
    (id, warehouse_id, product_id, qty_delta, movement_type, state, balance_after,
     reference_type, reference_id, note, created_by_id, created_at)
SELECT id, warehouse_id, product_id, qty_delta, movement_type, state, balance_after,
       reference_type, reference_id, note, created_by_id, created_at
FROM stock_movements_part;

SELECT setval('stock_movements_id_seq', COALESCE((SELECT max(id) FROM stock_movements), 1));
CREATE INDEX stockmove_wh_prod_idx ON stock_movements (warehouse_id, product_id, created_at);
CREATE INDEX stockmove_ref_idx ON stock_movements (reference_type, reference_id);
DROP TABLE stock_movements_part;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0007_alter_stockdocument_created_at"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(FORWARD_SQL, reverse_sql=REVERSE_SQL)],
            state_operations=[],  # model Django giữ nguyên PK id → không ripple
        ),
    ]
```

- [ ] **Step 4: Chạy migration test-DB + test partition**

Run: `pytest apps/inventory/tests/test_stock_movements_partition.py -v`
Expected: cả 2 PASS. pytest-django dựng schema từ migrations nên bảng test đã partitioned.

- [ ] **Step 5: Chạy hồi quy toàn inventory + dispatch (đảm bảo insert/PK còn đúng)**

Run: `pytest apps/inventory/tests/ apps/orders/tests/test_dispatch_stock.py -v`
Expected: tất cả PASS. Đặc biệt các test đọc `StockMovement.objects.get(...)`/`.filter(reference_...)` phải còn xanh.

- [ ] **Step 6: Kiểm tra `makemigrations --check` không phát sinh diff state**

Run: `python manage.py makemigrations inventory --check --dry-run`
Expected: "No changes detected" (vì `state_operations=[]` giữ model khớp DB ở góc nhìn Django).

- [ ] **Step 7: Commit**

```bash
git add backend/apps/inventory/migrations/0008_partition_stock_movements.py \
        backend/apps/inventory/tests/test_stock_movements_partition.py
git commit -m "feat(inventory): partition stock_movements by month (range on created_at)"
```

---

### Task 5: Celery task tự tạo partition tháng kế + đăng ký beat

**Files:**
- Create: `backend/apps/inventory/tasks.py`
- Create: `backend/apps/inventory/application/services/partition_service.py`
- Modify: `backend/config/settings_parts/base.py` (thêm entry vào `CELERY_BEAT_SCHEDULE`)
- Create: `backend/apps/inventory/tests/test_partition_service.py`

**Interfaces:**
- Consumes: bảng cha partitioned `stock_movements` (Task 4).
- Produces:
  - `PartitionService.ensure_month_partition(year: int, month: int) -> str` — trả tên partition, idempotent (`CREATE TABLE IF NOT EXISTS`).
  - `@shared_task ensure_stock_movements_partition()` name `apps.inventory.tasks.ensure_stock_movements_partition`.

- [ ] **Step 1: Viết test đỏ cho `PartitionService`**

```python
# backend/apps/inventory/tests/test_partition_service.py
from __future__ import annotations

from django.db import connection
from django.test import TransactionTestCase

from apps.inventory.application.services.partition_service import PartitionService


class PartitionServiceTests(TransactionTestCase):
    def _partition_exists(self, name):
        with connection.cursor() as cur:
            cur.execute("SELECT to_regclass(%s);", [f"public.{name}"])
            return cur.fetchone()[0] is not None

    def test_ensure_month_partition_creates_and_is_idempotent(self):
        name = PartitionService.ensure_month_partition(2030, 3)
        assert name == "stock_movements_203003"
        assert self._partition_exists(name)
        # Gọi lại không lỗi
        again = PartitionService.ensure_month_partition(2030, 3)
        assert again == name
```

- [ ] **Step 2: Chạy — FAIL (module chưa tồn tại)**

Run: `pytest apps/inventory/tests/test_partition_service.py -v`
Expected: FAIL — `ModuleNotFoundError: partition_service`.

- [ ] **Step 3: Viết `PartitionService`**

```python
# backend/apps/inventory/application/services/partition_service.py
from __future__ import annotations

from django.db import connection


class PartitionService:
    """Quản lý partition theo tháng cho bảng ledger stock_movements."""

    @staticmethod
    def ensure_month_partition(year: int, month: int) -> str:
        """Tạo partition cho (year, month) nếu chưa có. Idempotent. Trả tên partition."""
        name = f"stock_movements_{year:04d}{month:02d}"
        start = f"{year:04d}-{month:02d}-01"
        if month == 12:
            end = f"{year + 1:04d}-01-01"
        else:
            end = f"{year:04d}-{month + 1:02d}-01"
        with connection.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {name} "
                "PARTITION OF stock_movements FOR VALUES FROM (%s) TO (%s);",
                [start, end],
            )
        return name
```

> **Lưu ý an toàn:** tên partition được tạo từ int đã format, không nội suy giá trị người dùng → không có SQL injection. Biên tháng truyền qua tham số hoá.

- [ ] **Step 4: Chạy test service — PASS**

Run: `pytest apps/inventory/tests/test_partition_service.py -v`
Expected: PASS.

- [ ] **Step 5: Viết Celery task**

```python
# backend/apps/inventory/tasks.py
import logging

from celery import shared_task
from django.utils import timezone

from apps.inventory.application.services.partition_service import PartitionService

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="apps.inventory.tasks.ensure_stock_movements_partition",
    soft_time_limit=60,
    time_limit=90,
)
def ensure_stock_movements_partition(self) -> dict:
    """Đảm bảo partition của tháng hiện tại + tháng kế đã tồn tại (chạy trước khi tháng lăn)."""
    now = timezone.now()
    created = []
    for offset in (0, 1):
        month0 = now.month - 1 + offset
        year = now.year + month0 // 12
        month = month0 % 12 + 1
        created.append(PartitionService.ensure_month_partition(year, month))
    logger.info("ensured stock_movements partitions: %s", created)
    return {"partitions": created}
```

- [ ] **Step 6: Đăng ký vào `CELERY_BEAT_SCHEDULE`**

Trong `backend/config/settings_parts/base.py`, thêm entry cuối dict `CELERY_BEAT_SCHEDULE` (trước dấu `}` đóng):

```python
    # Tạo trước partition ledger cho tháng kế (chạy ngày 25 hàng tháng, dư thời gian trước khi lăn tháng).
    "inventory-ensure-stock-partition": {
        "task": "apps.inventory.tasks.ensure_stock_movements_partition",
        "schedule": crontab(day_of_month=25, hour=0, minute=30),
    },
```

- [ ] **Step 7: Test task gọi service đúng (không cần broker)**

Thêm vào `test_partition_service.py`:

```python
class EnsurePartitionTaskTests(TransactionTestCase):
    def test_task_creates_current_and_next_month(self):
        from apps.inventory.tasks import ensure_stock_movements_partition

        result = ensure_stock_movements_partition.run()
        assert len(result["partitions"]) == 2
        assert all(p.startswith("stock_movements_") for p in result["partitions"])
```

- [ ] **Step 8: Chạy test + hồi quy inventory**

Run: `pytest apps/inventory/tests/test_partition_service.py apps/inventory/tests/ -v`
Expected: tất cả PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/apps/inventory/tasks.py \
        backend/apps/inventory/application/services/partition_service.py \
        backend/apps/inventory/tests/test_partition_service.py \
        backend/config/settings_parts/base.py
git commit -m "feat(inventory): scheduled task to pre-create monthly stock partitions"
```

---

### Task 6: Runbook triển khai prod cho partition

**Files:**
- Create: `docs/runbooks/2026-07-02-partition-stock-movements.md`

**Interfaces:** không có (tài liệu vận hành).

- [ ] **Step 1: Viết runbook**

Nội dung tối thiểu:

```markdown
# Runbook: Partition stock_movements (migration inventory.0008)

## Trước khi chạy
1. Backup bảng: `pg_dump -t stock_movements -Fc $DB > stock_movements_backup_$(date +%F).dump`
2. Kiểm tra kích thước: `SELECT count(*), min(created_at), max(created_at) FROM stock_movements;`
3. Chọn cửa sổ ít tải (swap nằm trong 1 transaction; bảng nhỏ → dưới giây, nhưng khoá bảng trong lúc INSERT copy).

## Chạy
- `python manage.py migrate inventory 0008`

## Kiểm tra sau
- `SELECT partstrat FROM pg_partitioned_table pt JOIN pg_class c ON c.oid=pt.partrelid WHERE c.relname='stock_movements';` → `r`
- Đếm dòng khớp backup: `SELECT count(*) FROM stock_movements;`
- Xác nhận có partition DEFAULT + hist + tháng hiện tại/kế (task beat sẽ bù tháng kế).
- Ghi 1 movement thử (receipt nội bộ) → đọc lại đúng.

## Rollback
- `python manage.py migrate inventory 0007` (chạy REVERSE_SQL dựng lại bảng phẳng).
- Nếu lỗi giữa chừng: restore từ dump: `pg_restore -t stock_movements -d $DB --clean stock_movements_backup_*.dump`

## Sau triển khai
- Xác nhận beat job `inventory-ensure-stock-partition` xuất hiện ở trang cron admin.
- Chạy tay 1 lần: `celery -A config call apps.inventory.tasks.ensure_stock_movements_partition` (hoặc shell `.run()`).
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/2026-07-02-partition-stock-movements.md
git commit -m "docs(inventory): runbook for stock_movements partition rollout"
```

---

## Self-Review

**Spec coverage:**
- Spec §2 (Workstream A) → Task 1 (record_movement direct + concurrency) + Task 2 (4 return_type mappings). ✅
- Spec §3 (Workstream B upsert) → Task 3. ✅
- Spec §4.1–4.2 (partition swap, SeparateDatabaseAndState, PK `(id, created_at)`, DEFAULT) → Task 4. ✅
- Spec §4.3 (Celery tạo partition tháng) → Task 5. ✅
- Spec §4.4 (an toàn prod, backup, rollback, runbook) → Task 6. ✅
- Spec §5 (verify bằng docker test image) → "Running tests" + mỗi task. ✅

**Placeholder scan:** không có TBD/TODO; mọi step code có nội dung thật. ✅

**Type consistency:** `record_movement` chữ ký & `MovementResult(movement_id, balance_after, went_negative)` nhất quán Task 1/3/4; `ensure_month_partition(year, month) -> str` và tên `stock_movements_YYYYMM` khớp giữa Task 5 service/task/test. ✅

**Ghi chú thực thi:** biến môi trường docker test (redis password, DB host) xác nhận từ `docker-compose.local.yml` tại thời điểm chạy — không đoán trong plan.
