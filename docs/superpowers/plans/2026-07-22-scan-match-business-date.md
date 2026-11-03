# Ngày nghiệp vụ cho bút toán kho — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đơn/mã quét vào bảng chờ ngày nào thì sau khi khớp vẫn được tính vào đúng ngày đó, thay vì ngày cron chạy.

**Architecture:** Thêm `StockMovement.occurred_at` = ngày nghiệp vụ, tách khỏi `created_at` (ngày ghi sổ, đồng thời là partition key nên không được đụng). Luồng khớp mã chờ truyền `occurred_at = scan.created_at` xuống tận `record_movement`; mọi luồng khác giữ mặc định `now()`. Các báo cáo theo ngày chuyển sang lọc `occurred_at`, kèm điều kiện phụ trên `created_at` để không mất partition pruning. Bên bảo hành, `warehouse_received_at` cũng lấy ngày quét.

**Tech Stack:** Django 5 + PostgreSQL (bảng `stock_movements` RANGE-partition theo tháng trên `created_at`), pytest + pytest-django.

**Spec:** [docs/superpowers/specs/2026-07-22-scan-match-business-date-design.md](../specs/2026-07-22-scan-match-business-date-design.md)

## Global Constraints

- Bất biến toàn hệ thống: **`occurred_at <= created_at`**. Không luồng nào được ghi ngày nghiệp vụ ở tương lai. `record_movement` ép bất biến này bằng cách raise.
- **Không bao giờ sửa `created_at`** của `StockMovement` — nó là partition key của `stock_movements` (xem `PartitionService`); đổi giá trị sẽ đẩy dòng sang partition khác.
- **Không nắn `sale_reversal`**: đảo sổ là nghiệp vụ xảy ra tại thời điểm đảo, không phải tại thời điểm quét.
- Múi giờ hệ thống: `TIME_ZONE = "Asia/Bangkok"` (`config/settings_parts/base.py:524`), `USE_TZ = True`. Mọi chỗ quy `datetime` → ngày phải qua `timezone.localtime(...)` (Python) hoặc `AT TIME ZONE 'Asia/Bangkok'` (SQL), không dùng `.date()` trên giá trị UTC.
- Chạy test: `cd backend && pytest <path> -v` (config ở `backend/pytest.ini`, `DJANGO_SETTINGS_MODULE=config.settings`).
- Migration đụng bảng lớn phải `atomic = False` + chia lô, để mỗi statement ngắn.
- Commit message tiếng Anh, ngắn gọn, không kèm dòng `Co-Authored-By`.

---

### Task 1: Cột `occurred_at` trên `StockMovement`

**Files:**
- Modify: `backend/apps/inventory/models.py:78` (ngay dưới `created_at`)
- Create: `backend/apps/inventory/migrations/0024_stockmovement_occurred_at.py`
- Create: `backend/apps/inventory/migrations/0025_backfill_occurred_at.py`
- Create: `backend/apps/inventory/migrations/0026_occurred_at_not_null.py`
- Test: `backend/apps/inventory/tests/test_movement_occurred_at.py`

**Interfaces:**
- Consumes: —
- Produces: `StockMovement.occurred_at` (`DateTimeField`, NOT NULL, model default `django.utils.timezone.now`). Index `stockmove_reftype_occurred_idx` trên `(reference_type, movement_type, occurred_at)`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_movement_occurred_at.py`:

```python
"""Cột ngày nghiệp vụ của bút toán kho — xem
docs/superpowers/specs/2026-07-22-scan-match-business-date-design.md
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.inventory.application.services.stock_command_service import (
    StockCommandService,
)
from apps.inventory.models import MovementType, StockMovement
from apps.orders.models import Product
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="Kho occurred", code="KOC1")


@pytest.fixture
def product():
    return Product.objects.create(name="SP occurred", sku="SKU-OCC-1")


def test_movement_defaults_occurred_at_to_write_time(warehouse, product):
    """Luồng thường không truyền gì → ngày nghiệp vụ xấp xỉ ngày ghi sổ."""
    before = timezone.now()
    result = StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=5,
        movement_type=MovementType.RECEIPT,
    )
    movement = StockMovement.objects.get(id=result.movement_id)
    assert movement.occurred_at is not None
    assert before <= movement.occurred_at <= timezone.now()
    # Bất biến toàn hệ thống.
    assert movement.occurred_at <= movement.created_at
    assert abs(movement.created_at - movement.occurred_at) < timedelta(seconds=5)
```

- [ ] **Step 2: Chạy test để xác nhận nó FAIL**

Run: `cd backend && pytest apps/inventory/tests/test_movement_occurred_at.py -v`
Expected: FAIL — `AttributeError: 'StockMovement' object has no attribute 'occurred_at'`

- [ ] **Step 3: Thêm field vào model**

Trong `backend/apps/inventory/models.py`, ngay dưới dòng `created_at = models.DateTimeField(auto_now_add=True)` (dòng 78):

```python
    created_at = models.DateTimeField(auto_now_add=True)
    # Ngày NGHIỆP VỤ: lúc hàng thực sự rời/về kho. Lệch `created_at` khi mã quét
    # ngày D chỉ khớp được đơn ở ngày D+n (đơn KiotViet về muộn / quét sát nửa
    # đêm). MỌI báo cáo theo ngày đọc cột này; `created_at` giữ hai vai trò
    # KHÁC: dấu vết audit "ghi sổ lúc nào" và PARTITION KEY của bảng — đừng đụng.
    # Bất biến: occurred_at <= created_at (ép ở StockCommandService).
    occurred_at = models.DateTimeField(default=timezone.now)
```

`from django.utils import timezone` đã có sẵn ở đầu `models.py` — không cần thêm import.

Trong `class Meta` của `StockMovement`, thêm vào `indexes`:

```python
            models.Index(
                fields=["reference_type", "movement_type", "occurred_at"],
                name="stockmove_reftype_occurred_idx",
            ),
```

- [ ] **Step 4: Sinh migration 1/3 — thêm cột nullable**

Tạo `backend/apps/inventory/migrations/0024_stockmovement_occurred_at.py`:

```python
from django.db import migrations, models


class Migration(migrations.Migration):
    """Thêm cột nullable — thao tác metadata, KHÔNG rewrite bảng partition.

    Backfill và NOT NULL nằm ở 0025/0026 để mỗi migration giữ khoá ngắn.
    """

    dependencies = [("inventory", "0023_returnnoteline_quantity_positive")]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="occurred_at",
            field=models.DateTimeField(null=True),
        ),
    ]
```

- [ ] **Step 5: Sinh migration 2/3 — backfill theo lô**

Tạo `backend/apps/inventory/migrations/0025_backfill_occurred_at.py`:

```python
from django.db import migrations

_BATCH = 50_000


def backfill(apps, schema_editor):
    """occurred_at = created_at cho dữ liệu cũ, chia lô theo id.

    `atomic = False` bên dưới là bắt buộc: nếu cả migration nằm trong 1
    transaction thì chia lô vô nghĩa (khoá giữ tới cuối). Mỗi UPDATE tự commit.
    Chạy lại được: điều kiện `occurred_at IS NULL` khiến lô đã xong thành no-op.
    """
    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT COALESCE(MIN(id), 0), COALESCE(MAX(id), -1) FROM stock_movements;")
        low, high = cur.fetchone()
        while low <= high:
            cur.execute(
                "UPDATE stock_movements SET occurred_at = created_at "
                "WHERE id >= %s AND id < %s AND occurred_at IS NULL;",
                [low, low + _BATCH],
            )
            low += _BATCH


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("inventory", "0024_stockmovement_occurred_at")]

    operations = [
        # Chiều ngược: cột sẽ bị 0024 xoá nguyên vẹn, không cần undo dữ liệu.
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
```

- [ ] **Step 6: Sinh migration 3/3 — NOT NULL + index**

Tạo `backend/apps/inventory/migrations/0026_occurred_at_not_null.py`:

```python
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """Chốt NOT NULL sau khi 0025 đã lấp hết, rồi tạo index.

    CREATE INDEX CONCURRENTLY không dùng được trên bảng partition, nên index này
    tạo thường (khoá ghi trong lúc build). Chạy vào giờ thấp điểm.
    """

    dependencies = [("inventory", "0025_backfill_occurred_at")]

    operations = [
        migrations.AlterField(
            model_name="stockmovement",
            name="occurred_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(
                fields=["reference_type", "movement_type", "occurred_at"],
                name="stockmove_reftype_occurred_idx",
            ),
        ),
    ]
```

- [ ] **Step 7: Xác nhận không còn migration nào thiếu**

Run: `cd backend && python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`

Nếu Django báo còn thay đổi, nghĩa là 3 migration trên chưa mô tả đúng state model — sửa migration cho khớp, **không** để Django tự sinh file thứ tư.

- [ ] **Step 8: Chạy test để xác nhận PASS**

Run: `cd backend && pytest apps/inventory/tests/test_movement_occurred_at.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/apps/inventory/models.py backend/apps/inventory/migrations/0024_stockmovement_occurred_at.py backend/apps/inventory/migrations/0025_backfill_occurred_at.py backend/apps/inventory/migrations/0026_occurred_at_not_null.py backend/apps/inventory/tests/test_movement_occurred_at.py
git commit -m "feat(inventory): add business date column to stock movements"
```

---

### Task 2: `record_movement` nhận `occurred_at` và ép bất biến

**Files:**
- Modify: `backend/apps/inventory/application/services/stock_command_service.py:24-70`
- Test: `backend/apps/inventory/tests/test_movement_occurred_at.py`

**Interfaces:**
- Consumes: `StockMovement.occurred_at` (Task 1).
- Produces: `StockCommandService.record_movement(..., occurred_at: datetime | None = None) -> MovementResult`. `None` ⇒ `timezone.now()`. `occurred_at` ở tương lai ⇒ `ValueError`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/inventory/tests/test_movement_occurred_at.py`:

```python
def test_movement_records_explicit_occurred_at(warehouse, product):
    """Ngày nghiệp vụ do caller chỉ định; ngày ghi sổ vẫn là bây giờ."""
    scanned_at = timezone.now() - timedelta(days=2)
    result = StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=-1,
        movement_type=MovementType.SALE,
        occurred_at=scanned_at,
    )
    movement = StockMovement.objects.get(id=result.movement_id)
    assert movement.occurred_at == scanned_at
    assert movement.created_at - movement.occurred_at >= timedelta(days=2)


def test_movement_rejects_future_occurred_at(warehouse, product):
    """Bất biến occurred_at <= created_at: ngày tương lai là lỗi lập trình."""
    with pytest.raises(ValueError, match="tương lai"):
        StockCommandService.record_movement(
            warehouse_id=warehouse.id,
            product_id=product.id,
            qty_delta=-1,
            movement_type=MovementType.SALE,
            occurred_at=timezone.now() + timedelta(hours=1),
        )
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && pytest apps/inventory/tests/test_movement_occurred_at.py -v`
Expected: FAIL — `TypeError: record_movement() got an unexpected keyword argument 'occurred_at'`

- [ ] **Step 3: Thêm tham số vào `record_movement`**

Trong `backend/apps/inventory/application/services/stock_command_service.py`, sửa import và chữ ký:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import connection, transaction
from django.utils import timezone

from apps.inventory.models import StockMovement, StockState
```

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
        occurred_at: datetime | None = None,
    ) -> MovementResult:
        """``occurred_at``: thời điểm nghiệp vụ (hàng thực sự rời/về kho).

        Mặc định ``now()`` ⇒ mọi caller cũ không đổi hành vi. Luồng khớp mã chờ
        truyền thời điểm QUÉT để số liệu rơi vào đúng ngày kho làm việc, thay vì
        ngày cron khớp được đơn.
        """
        if qty_delta == 0:
            raise ValueError("qty_delta must be non-zero.")
        now = timezone.now()
        when = occurred_at or now
        if when > now:
            # Bất biến occurred_at <= created_at là cơ sở cho tối ưu partition
            # pruning ở `filter_occurred_between` — phá nó là làm sai báo cáo,
            # không chỉ sai một dòng.
            raise ValueError("occurred_at không được ở tương lai.")
```

Và trong lệnh `StockMovement.objects.create(...)`, thêm dòng cuối:

```python
            created_by_id=user_id,
            occurred_at=when,
        )
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd backend && pytest apps/inventory/tests/test_movement_occurred_at.py -v`
Expected: PASS (4 test)

- [ ] **Step 5: Chạy hồi quy toàn bộ inventory**

Run: `cd backend && pytest apps/inventory -q`
Expected: PASS — không test nào hỏng vì tham số mới có mặc định.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/inventory/application/services/stock_command_service.py backend/apps/inventory/tests/test_movement_occurred_at.py
git commit -m "feat(inventory): accept business date in record_movement"
```

---

### Task 3: Nhánh XUẤT truyền ngày quét

**Files:**
- Modify: `backend/apps/inventory/application/services/stock_sales_service.py:142-181` (`issue_for_order`)
- Modify: `backend/apps/orders/application/services/order_service_commands.py:284` (chữ ký `update_order_inline`), `:461-466` (lời gọi `issue_for_order`), `:649` (chữ ký `dispatch_orders_batch`), `:733-741` (lời gọi `update_order_inline`)
- Modify: `backend/apps/inventory/application/services/pending_scan_service.py:777-797` (`_complete_dispatch`)
- Test: `backend/apps/inventory/tests/test_pending_scan_occurred_at.py`

**Interfaces:**
- Consumes: `StockCommandService.record_movement(occurred_at=...)` (Task 2).
- Produces:
  - `StockSalesService.issue_for_order(*, order_id, warehouse_id, lines, user_id=None, occurred_at=None)`
  - `OrderServiceCommands.update_order_inline(order, data, actor, *, verdict=None, occurred_at=None)`
  - `OrderServiceCommands.dispatch_orders_batch(*, order_ids, warehouse_id, actor, occurred_at=None)`

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_pending_scan_occurred_at.py`. Fixture dựng lại theo đúng khuôn của `apps/inventory/tests/test_pending_scan_service_match.py` (đơn không có `kiotviet_channel`/`invoice_code` ⇒ verifier trả `UNVERIFIABLE`, không gọi HTTP):

```python
"""Khớp mã chờ phải ghi sổ theo NGÀY QUÉT, không phải ngày cron chạy.

Xem docs/superpowers/specs/2026-07-22-scan-match-business-date-design.md
"""
from datetime import date, datetime, timezone as dt_timezone

import pytest

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.application.services.stock_command_service import (
    StockCommandService,
)
from apps.inventory.models import (
    MovementType,
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    StockMovement,
)
from apps.orders.models import Order, OrderItem, OrderStatus, Product
from apps.users.test_helpers import make_admin
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db

# 03/07 17:30 UTC = 04/07 00:30 giờ VN. Ngày cố định cứng để bắt được lỗi dùng
# `.date()` trên giá trị UTC thay vì `timezone.localtime(...)`.
SCANNED_AT = datetime(2026, 7, 3, 17, 30, tzinfo=dt_timezone.utc)
SCANNED_LOCAL_DATE = date(2026, 7, 4)


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="Kho scan date", code="KSD1")


@pytest.fixture
def staff_user():
    return make_admin("scan-occurred-at-actor")


@pytest.fixture
def channel():
    return Channel.objects.create(code="lp-scan-date", name="Scan Date Channel")


@pytest.fixture
def order(channel, warehouse):
    customer = Customer.objects.create(
        full_name="Khach Scan Date",
        phone_raw="0929000111",
        phone_normalized="0929000111",
        code="CUST-SD-1",
    )
    product = Product.objects.create(name="SP Scan Date", sku="SKU-SD-1")
    o = Order.objects.create(
        channel=channel,
        customer=customer,
        name="Khach Scan Date",
        phone="0929000111",
        total_amount="200000",
        status=OrderStatus.AWAITING_SHIPMENT,
        shipping_code="SDX1",
        fulfillment_warehouse=warehouse,
    )
    OrderItem.objects.create(
        order=o,
        product=product,
        product_name=product.name,
        sku=product.sku,
        quantity=2,
        unit_price=100000,
        line_total=200000,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=10,
        movement_type=MovementType.RECEIPT,
    )
    return o


def _scan_backdated(*, warehouse, staff_user, kind, return_type="") -> PendingShipmentScan:
    scan = PendingShipmentScan.objects.create(
        code="SDX1",
        kind=kind,
        warehouse=warehouse,
        return_type=return_type,
        scanned_by=staff_user,
    )
    # auto_now_add bỏ qua kwarg lúc create → phải UPDATE mới ghi đè được created_at.
    PendingShipmentScan.objects.filter(pk=scan.pk).update(created_at=SCANNED_AT)
    scan.refresh_from_db()
    return scan


def test_dispatch_match_stamps_scan_date_not_cron_date(order, warehouse, staff_user):
    """Quét 04/07, cron khớp hôm nay → bút toán xuất thuộc ngày 04/07."""
    _scan_backdated(warehouse=warehouse, staff_user=staff_user, kind=PendingScanKind.DISPATCH)

    PendingScanService.match_pending_scans()

    movement = StockMovement.objects.get(
        reference_type="order", reference_id=order.id, movement_type=MovementType.SALE
    )
    assert movement.occurred_at == SCANNED_AT
    # created_at giữ nguyên nghĩa "ghi sổ lúc nào" → vẫn là hôm nay.
    assert movement.created_at > movement.occurred_at
    assert movement.occurred_at <= movement.created_at


def test_manual_match_stamps_scan_date(order, warehouse, staff_user):
    """Khớp tay cũng đi qua cùng đường ghi sổ → cùng luật ngày."""
    scan = _scan_backdated(
        warehouse=warehouse, staff_user=staff_user, kind=PendingScanKind.DISPATCH
    )

    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=staff_user
    )

    assert result["ok"] is True
    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    movement = StockMovement.objects.get(
        reference_type="order", reference_id=order.id, movement_type=MovementType.SALE
    )
    assert movement.occurred_at == SCANNED_AT
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && pytest apps/inventory/tests/test_pending_scan_occurred_at.py -v`
Expected: FAIL — `assert movement.occurred_at == SCANNED_AT` sai, `occurred_at` đang là thời điểm chạy test.

- [ ] **Step 3: Cho `issue_for_order` nhận `occurred_at`**

Trong `backend/apps/inventory/application/services/stock_sales_service.py`, sửa `issue_for_order` (dòng 142-181):

```python
    @staticmethod
    @transaction.atomic
    def issue_for_order(
        *,
        order_id: int,
        warehouse_id: int,
        lines: list[SaleLine],
        user_id: int | None = None,
        occurred_at: datetime | None = None,
    ) -> list[StockWarning]:
        """Trừ tồn khi đơn xuất kho. Idempotent theo trạng thái NET: nếu đơn đang
        có tồn đã trừ chưa hoàn thì bỏ qua; nếu đã hoàn hết (huỷ → mở lại) thì
        cho phép trừ lại — không bị "nuốt" như guard exists() cũ.

        ``occurred_at``: thời điểm hàng thực sự rời kho. Luồng khớp mã chờ truyền
        thời điểm QUÉT; mặc định None ⇒ bây giờ.
        """
```

Thêm `from datetime import datetime` vào phần import đầu file nếu chưa có. Trong vòng lặp, truyền xuống:

```python
            result = StockCommandService.record_movement(
                warehouse_id=warehouse_id,
                product_id=product_id,
                qty_delta=-quantity,
                movement_type=MovementType.SALE,
                state=StockState.SELLABLE,
                reference_type=_REFERENCE_TYPE,
                reference_id=order_id,
                user_id=user_id,
                occurred_at=occurred_at,
            )
```

**Không** đụng `release_for_order`, `resync_for_order`, `transfer_issue_to_sibling`: đảo sổ / resync / chuyển sổ là nghiệp vụ xảy ra tại thời điểm chúng chạy (xem Global Constraints).

- [ ] **Step 4: Luồn qua `update_order_inline`**

Trong `backend/apps/orders/application/services/order_service_commands.py`, sửa chữ ký ở dòng 284:

```python
    def update_order_inline(order: Order, data: dict, actor, *, verdict=None, occurred_at=None):
```

Bổ sung vào cuối docstring hiện có của hàm:

```
        ``occurred_at``: thời điểm hàng thực sự rời kho, chỉ dùng cho nhánh trừ tồn
        (``issue_for_order``). Luồng khớp mã chờ truyền thời điểm QUÉT. Nhánh
        un-dispatch bên dưới KHÔNG nhận: hoàn tồn xảy ra ngay lúc bỏ trạng thái.
```

Sửa lời gọi ở dòng 461:

```python
            warnings = StockSalesService.issue_for_order(
                order_id=order.id,
                warehouse_id=order.fulfillment_warehouse_id,
                lines=lines,
                user_id=getattr(actor, "id", None),
                occurred_at=occurred_at,
            )
```

- [ ] **Step 5: Luồn qua `dispatch_orders_batch`**

Cùng file, sửa chữ ký ở dòng 649:

```python
    def dispatch_orders_batch(
        *, order_ids: list[int], warehouse_id: int, actor, occurred_at=None
    ) -> list[dict]:
```

và lời gọi `update_order_inline` ở dòng 733:

```python
                    stock_warnings = OrderServiceCommands.update_order_inline(
                        order,
                        {
                            "fulfillment_warehouse": warehouse,
                            "crm": {"delivery_business_status_code": "dispatched"},
                        },
                        actor,
                        verdict=check,
                        occurred_at=occurred_at,
                    )
```

- [ ] **Step 6: `_complete_dispatch` truyền ngày quét**

Trong `backend/apps/inventory/application/services/pending_scan_service.py`, sửa thân `_complete_dispatch` (dòng 777-797):

```python
        results = OrderService.dispatch_orders_batch(
            order_ids=[order.id],
            warehouse_id=scan.warehouse_id,
            actor=actor or scan.scanned_by,
            # Ngày nghiệp vụ = lúc kho QUÉT mã, không phải lúc cron khớp được đơn:
            # đơn KiotViet về muộn vài ngày thì sản lượng xuất vẫn thuộc ngày kho
            # làm việc. Cùng luật với doc_date ở `_complete_return`.
            occurred_at=scan.created_at,
        )
```

- [ ] **Step 7: Chạy test để xác nhận PASS**

Run: `cd backend && pytest apps/inventory/tests/test_pending_scan_occurred_at.py -v`
Expected: PASS (2 test)

- [ ] **Step 8: Hồi quy hai app bị đụng**

Run: `cd backend && pytest apps/inventory apps/orders -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/apps/inventory/application/services/stock_sales_service.py backend/apps/orders/application/services/order_service_commands.py backend/apps/inventory/application/services/pending_scan_service.py backend/apps/inventory/tests/test_pending_scan_occurred_at.py
git commit -m "feat(inventory): stamp dispatch movements with scan date"
```

---

### Task 4: Nhánh HOÀN truyền ngày quét

**Files:**
- Modify: `backend/apps/inventory/application/services/return_entry_service.py:230-329` (`post_entry`, `create_and_post_entry`)
- Modify: `backend/apps/inventory/application/services/pending_scan_service.py:841-861` (lời gọi `create_and_post_entry`)
- Test: `backend/apps/inventory/tests/test_pending_scan_occurred_at.py`

**Interfaces:**
- Consumes: `StockCommandService.record_movement(occurred_at=...)` (Task 2).
- Produces:
  - `ReturnEntryService.post_entry(*, entry_id, user_id=None, occurred_at=None) -> ReturnEntry`
  - `ReturnEntryService.create_and_post_entry(*, user_id=None, occurred_at=None, **create_kwargs) -> ReturnEntry`

- [ ] **Step 1: Viết test thất bại**

Thêm vào `backend/apps/inventory/tests/test_pending_scan_occurred_at.py`:

```python
def test_return_match_stamps_scan_date_and_keeps_doc_date(order, warehouse, staff_user):
    """Nhánh hoàn: bút toán mang ngày quét, phiếu ngày vẫn đúng như trước."""
    from apps.inventory.models import ReturnEntry

    _scan_backdated(
        warehouse=warehouse,
        staff_user=staff_user,
        kind=PendingScanKind.RETURN,
        return_type="delivery_returned",
    )

    PendingScanService.match_pending_scans()

    entry = ReturnEntry.objects.get(shipping_code="SDX1")
    # Hành vi đang ĐÚNG, không được hồi quy.
    assert entry.return_note.doc_date == SCANNED_LOCAL_DATE
    movement = StockMovement.objects.get(
        reference_type="return_entry",
        reference_id=entry.id,
        movement_type=MovementType.RETURN_IN,
    )
    assert movement.occurred_at == SCANNED_AT
    assert movement.created_at > movement.occurred_at
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && pytest apps/inventory/tests/test_pending_scan_occurred_at.py::test_return_match_stamps_scan_date_and_keeps_doc_date -v`
Expected: FAIL ở `assert movement.occurred_at == SCANNED_AT` (`doc_date` thì đã đúng sẵn).

- [ ] **Step 3: Cho `post_entry` nhận `occurred_at`**

Trong `backend/apps/inventory/application/services/return_entry_service.py`, sửa chữ ký `post_entry` (dòng 232):

```python
    def post_entry(
        *, entry_id: int, user_id: int | None = None, occurred_at=None
    ) -> ReturnEntry:
```

và lời gọi `record_movement` trong vòng lặp (dòng 292-301):

```python
            StockCommandService.record_movement(
                warehouse_id=note_obj.warehouse_id,
                product_id=line.product_id,
                qty_delta=line.quantity,
                movement_type=MovementType.RETURN_IN,
                state=_RETURN_STATE[line.return_type],
                reference_type=RETURN_ENTRY_REFERENCE,
                reference_id=entry.id,
                user_id=user_id,
                occurred_at=occurred_at,
            )
```

- [ ] **Step 4: Luồn qua `create_and_post_entry`**

Cùng file, sửa (dòng 318-329):

```python
    @staticmethod
    @transaction.atomic
    def create_and_post_entry(
        *, user_id: int | None = None, occurred_at=None, **create_kwargs
    ) -> ReturnEntry:
        """Tạo + ghi sổ trong 1 transaction (luồng quét khớp mã).

        Lỗi ở create hoặc post → rollback toàn bộ: không nhóm đơn, không dòng,
        không movement, không đổi status đơn.

        ``occurred_at`` chỉ đi vào bút toán kho; ngày chứng từ của phiếu vẫn do
        ``doc_date`` trong ``create_kwargs`` quyết định (hai thứ khác nhau: phiếu
        gộp theo NGÀY, bút toán giữ cả GIỜ).
        """
        entry = ReturnEntryService.create_entry(user_id=user_id, **create_kwargs)
        return ReturnEntryService.post_entry(
            entry_id=entry.id, user_id=user_id, occurred_at=occurred_at
        )
```

- [ ] **Step 5: `_complete_return` truyền ngày quét**

Trong `backend/apps/inventory/application/services/pending_scan_service.py`, thêm một tham số vào lời gọi `ReturnEntryService.create_and_post_entry(...)` (dòng 841), ngay dưới `doc_date=...`:

```python
        entry = ReturnEntryService.create_and_post_entry(
            warehouse_id=scan.warehouse_id,
            # Ngày chứng từ = ngày mã được QUÉT (hàng về kho), không phải ngày cron
            # chạy: mã quét 23h hôm qua, cron khớp sáng nay vẫn thuộc phiếu hôm qua.
            doc_date=timezone.localtime(scan.created_at).date(),
            # Bút toán kho mang đúng thời điểm quét (cả giờ) — cùng lý do trên,
            # nhưng cho các báo cáo đọc thẳng ledger thay vì đọc phiếu ngày.
            occurred_at=scan.created_at,
```

(giữ nguyên phần còn lại của lời gọi)

- [ ] **Step 6: Chạy test để xác nhận PASS**

Run: `cd backend && pytest apps/inventory/tests/test_pending_scan_occurred_at.py -v`
Expected: PASS (3 test)

- [ ] **Step 7: Hồi quy**

Run: `cd backend && pytest apps/inventory -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/apps/inventory/application/services/return_entry_service.py backend/apps/inventory/application/services/pending_scan_service.py backend/apps/inventory/tests/test_pending_scan_occurred_at.py
git commit -m "feat(inventory): stamp return movements with scan date"
```

---

### Task 5: Báo cáo đọc theo `occurred_at`

**Files:**
- Create: `backend/apps/inventory/application/services/movement_window.py`
- Modify: `backend/apps/inventory/application/services/dispatch_history_service.py:25-38` (`_dispatch_last_at`), `:65-68` (lọc ngày)
- Modify: `backend/apps/inventory/application/services/issue_document_history.py:49-52`
- Modify: `backend/apps/inventory/application/services/transfer_document_history.py:58-61`
- Modify: `backend/apps/inventory/application/services/stock_query_service.py:40-53`
- Test: `backend/apps/inventory/tests/test_dispatch_history_occurred_at.py`

**Interfaces:**
- Consumes: `StockMovement.occurred_at` (Task 1) và bất biến `occurred_at <= created_at` (Task 2).
- Produces: `filter_occurred_between(qs, date_from="", date_to="") -> QuerySet[StockMovement]` trong `apps.inventory.application.services.movement_window`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_dispatch_history_occurred_at.py`:

```python
"""Tab Xuất lọc theo NGÀY NGHIỆP VỤ của bút toán, không theo ngày ghi sổ."""
from datetime import date, datetime, timezone as dt_timezone

import pytest

from apps.inventory.application.services.dispatch_history_service import (
    DispatchHistoryService,
)
from apps.inventory.application.services.stock_command_service import (
    StockCommandService,
)
from apps.inventory.models import MovementType, StockMovement
from apps.orders.models import Product
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db

OCCURRED_AT = datetime(2026, 7, 4, 3, 0, tzinfo=dt_timezone.utc)  # 10:00 giờ VN 04/07
OCCURRED_DATE = "2026-07-04"


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="Kho tab xuat", code="KTX1")


@pytest.fixture
def product():
    return Product.objects.create(name="SP tab xuat", sku="SKU-TX-1")


@pytest.fixture
def backdated_sale(warehouse, product):
    """Bút toán xuất: nghiệp vụ ngày 04/07, ghi sổ ngày 06/07."""
    result = StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=-3,
        movement_type=MovementType.SALE,
        reference_type="order",
        reference_id=4242,
        occurred_at=OCCURRED_AT,
    )
    StockMovement.objects.filter(id=result.movement_id).update(
        created_at=datetime(2026, 7, 6, 3, 0, tzinfo=dt_timezone.utc)
    )
    return StockMovement.objects.get(id=result.movement_id)


def test_dispatch_history_finds_movement_on_business_date(backdated_sale):
    total = DispatchHistoryService.total_issued(
        date_from=OCCURRED_DATE, date_to=OCCURRED_DATE
    )
    assert total == 3


def test_dispatch_history_excludes_movement_on_write_date(backdated_sale):
    total = DispatchHistoryService.total_issued(
        date_from="2026-07-06", date_to="2026-07-06"
    )
    assert total == 0


def test_dispatch_history_last_at_is_business_date(backdated_sale):
    groups = list(
        DispatchHistoryService.order_groups(
            date_from=OCCURRED_DATE, date_to=OCCURRED_DATE
        )
    )
    assert len(groups) == 1
    assert groups[0]["last_at"] == OCCURRED_AT
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && pytest apps/inventory/tests/test_dispatch_history_occurred_at.py -v`
Expected: FAIL cả 3 — hiện đang lọc theo `created_at` nên bút toán chỉ hiện ở 06/07.

- [ ] **Step 3: Tạo helper lọc dùng chung**

Tạo `backend/apps/inventory/application/services/movement_window.py`:

```python
"""Lọc bút toán kho theo NGÀY NGHIỆP VỤ mà vẫn cắt được partition.

`stock_movements` được RANGE-partition theo THÁNG trên `created_at` (xem
`PartitionService`), nên lọc thuần `occurred_at` sẽ quét mọi partition.

Nhờ bất biến `occurred_at <= created_at` (ép ở `StockCommandService`), thêm điều
kiện `created_at >= date_from` KHÔNG đổi kết quả: mọi dòng bị nó loại thêm đều có
`created_at < date_from`, kéo theo `occurred_at < date_from`, tức đằng nào cũng bị
loại — nhưng nó cắt sạch các partition cũ hơn kỳ lọc.

Cận trên không có điều kiện đối xứng (`created_at` được phép lớn hơn `date_to`),
nên các partition mới hơn kỳ lọc vẫn bị quét; đó chỉ là vài tháng gần nhất.

Đây là NƠI DUY NHẤT giữ luật này — đừng chép rời ra từng service, hai bản sao sẽ
lệch nhau lúc nào không hay.
"""
from __future__ import annotations


def filter_occurred_between(qs, date_from: str = "", date_to: str = ""):
    if date_from:
        qs = qs.filter(
            occurred_at__date__gte=date_from, created_at__date__gte=date_from
        )
    if date_to:
        qs = qs.filter(occurred_at__date__lte=date_to)
    return qs
```

- [ ] **Step 4: Đổi `DispatchHistoryService`**

Trong `backend/apps/inventory/application/services/dispatch_history_service.py`, thêm import:

```python
from apps.inventory.application.services.movement_window import filter_occurred_between
```

Sửa `_dispatch_last_at()` (dòng 25-38) — đổi cả docstring lẫn cột:

```python
def _dispatch_last_at():
    """Ngày xuất hiển thị = lần SALE THẬT gần nhất (loại SALE hành chính).

    Dùng `occurred_at` (ngày hàng rời kho) chứ không phải `created_at` (ngày ghi
    sổ): mã quét ngày D khớp được đơn ở ngày D+n vẫn phải hiện ở ngày D.

    Fallback ``Max(occurred_at)`` cho nhóm chỉ còn bút toán hành chính trong
    khoảng lọc (vd. lọc đúng ngày hồi sinh → cặp release/reissue net 0) —
    không được trả null vì cột ngày và sort đều dựa vào giá trị này.
    """
    return Coalesce(
        Max(
            "occurred_at",
            filter=Q(movement_type=_SALE) & ~Q(note__in=_ADMIN_SALE_NOTES),
        ),
        Max("occurred_at"),
    )
```

Thay hai dòng lọc ngày (dòng 65-68):

```python
        qs = filter_occurred_between(qs, date_from, date_to)
```

**Giữ nguyên** `.order_by("created_at", "id")` trong `enrich_orders` và `sku_orders`: hai chỗ đó chọn "bút toán ghi SAU thắng" để lấy nhãn kho / người thực hiện — đó là thứ tự GHI SỔ, không phải thứ tự nghiệp vụ.

- [ ] **Step 5: Đổi `IssueDocumentHistoryService` và `TransferDocumentHistoryService`**

Ở cả hai file, thêm import `filter_occurred_between` như trên và thay khối:

```python
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
```

bằng:

```python
        # Phiếu kho tay luôn có occurred_at == created_at; dùng chung helper để
        # trong codebase chỉ còn MỘT luật về "ngày của bút toán".
        qs = filter_occurred_between(qs, date_from, date_to)
```

- [ ] **Step 6: Đổi `StockQueryService`**

Trong `backend/apps/inventory/application/services/stock_query_service.py`, sửa khối truy vấn (dòng 40-53):

```python
        now = timezone.now()
        since_3d = now - timedelta(days=3)
        since_7d = now - timedelta(days=7)
        rows = (
            StockMovement.objects.filter(
                movement_type__in=[MovementType.SALE, MovementType.SALE_REVERSAL],
                # Tốc độ bán tính theo ngày hàng RỜI KHO. `created_at` đi kèm chỉ
                # để cắt partition — hợp lệ nhờ bất biến occurred_at <= created_at
                # (xem movement_window).
                occurred_at__gte=since_7d,
                created_at__gte=since_7d,
                warehouse_id__in={wh for wh, _ in pairs},
                product_id__in={prod for _, prod in pairs},
            )
            .values("warehouse_id", "product_id")
            .annotate(
                net_7d=Sum("qty_delta"),
                net_3d=Sum("qty_delta", filter=Q(occurred_at__gte=since_3d)),
            )
        )
```

- [ ] **Step 7: Chạy test để xác nhận PASS**

Run: `cd backend && pytest apps/inventory/tests/test_dispatch_history_occurred_at.py -v`
Expected: PASS (3 test)

- [ ] **Step 8: Hồi quy các test báo cáo**

Run: `cd backend && pytest apps/inventory apps/orders -q`
Expected: PASS. Nếu `test_dispatch_history_service.py` / `test_flow_summary.py` / `test_issue_document_history.py` hỏng, đọc kỹ: test cũ dựng bút toán bằng `record_movement` nên `occurred_at == created_at`, chúng phải vẫn xanh. Test hỏng ⇒ có chỗ dựng movement bằng `StockMovement.objects.create(...)` thẳng rồi `.update(created_at=...)` — sửa test đó để set cả `occurred_at`.

- [ ] **Step 9: Commit**

```bash
git add backend/apps/inventory/application/services/movement_window.py backend/apps/inventory/application/services/dispatch_history_service.py backend/apps/inventory/application/services/issue_document_history.py backend/apps/inventory/application/services/transfer_document_history.py backend/apps/inventory/application/services/stock_query_service.py backend/apps/inventory/tests/test_dispatch_history_occurred_at.py
git commit -m "feat(inventory): report stock flows by business date"
```

---

### Task 6: Nắn lại bút toán lịch sử

**Files:**
- Create: `backend/apps/inventory/application/services/occurred_at_backfill.py`
- Create: `backend/apps/inventory/migrations/0027_backfill_occurred_at_from_scans.py`
- Test: `backend/apps/inventory/tests/test_occurred_at_backfill.py`

**Interfaces:**
- Consumes: `StockMovement.occurred_at` (Task 1).
- Produces: `backfill_from_pending_scans(connection) -> dict` trong `apps.inventory.application.services.occurred_at_backfill`, trả `{"dispatch": <số dòng>, "return": <số dòng>}`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_occurred_at_backfill.py`:

```python
"""Nắn ngày nghiệp vụ cho bút toán đã ghi sai trước khi có occurred_at."""
from datetime import datetime, timezone as dt_timezone

import pytest
from django.db import connection

from apps.inventory.application.services.occurred_at_backfill import (
    backfill_from_pending_scans,
)
from apps.inventory.application.services.stock_command_service import (
    StockCommandService,
)
from apps.inventory.models import (
    MovementType,
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    StockMovement,
)
from apps.orders.models import Product
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db

SCANNED_AT = datetime(2026, 7, 4, 3, 0, tzinfo=dt_timezone.utc)
POSTED_AT = datetime(2026, 7, 6, 3, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="Kho backfill", code="KBF1")


@pytest.fixture
def product():
    return Product.objects.create(name="SP backfill", sku="SKU-BF-1")


def _movement(warehouse, product, *, movement_type, reference_type, reference_id):
    """Dựng bút toán "đời cũ": occurred_at == created_at == ngày cron chạy."""
    result = StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=-1 if movement_type == MovementType.SALE else 1,
        movement_type=movement_type,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    StockMovement.objects.filter(id=result.movement_id).update(
        created_at=POSTED_AT, occurred_at=POSTED_AT
    )
    return StockMovement.objects.get(id=result.movement_id)


def test_backfill_moves_dispatch_sale_to_scan_time(warehouse, product):
    # reference_id là BigIntegerField thuần (không FK) nên không cần Order thật.
    order_id = 90001
    sale = _movement(
        warehouse, product,
        movement_type=MovementType.SALE,
        reference_type="order", reference_id=order_id,
    )
    scan = PendingShipmentScan.objects.create(
        code="BF1", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=SCANNED_AT, matched_order_id=order_id
    )

    stats = backfill_from_pending_scans(connection)

    sale.refresh_from_db()
    assert stats["dispatch"] == 1
    assert sale.occurred_at == SCANNED_AT
    # created_at là partition key — tuyệt đối không đụng.
    assert sale.created_at == POSTED_AT


def test_backfill_leaves_sale_reversal_alone(warehouse, product):
    order_id = 90002
    reversal = _movement(
        warehouse, product,
        movement_type=MovementType.SALE_REVERSAL,
        reference_type="order", reference_id=order_id,
    )
    scan = PendingShipmentScan.objects.create(
        code="BF2", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=SCANNED_AT, matched_order_id=order_id
    )

    backfill_from_pending_scans(connection)

    reversal.refresh_from_db()
    assert reversal.occurred_at == POSTED_AT


def test_backfill_is_idempotent(warehouse, product):
    order_id = 90003
    sale = _movement(
        warehouse, product,
        movement_type=MovementType.SALE,
        reference_type="order", reference_id=order_id,
    )
    scan = PendingShipmentScan.objects.create(
        code="BF3", kind=PendingScanKind.DISPATCH, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=SCANNED_AT, matched_order_id=order_id
    )

    backfill_from_pending_scans(connection)
    second = backfill_from_pending_scans(connection)

    sale.refresh_from_db()
    assert second["dispatch"] == 0  # lần hai không còn gì để nắn
    assert sale.occurred_at == SCANNED_AT
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && pytest apps/inventory/tests/test_occurred_at_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.inventory.application.services.occurred_at_backfill'`

- [ ] **Step 3: Viết hàm backfill**

Tạo `backend/apps/inventory/application/services/occurred_at_backfill.py`:

```python
"""Nắn `occurred_at` cho bút toán đã ghi trước khi có cột ngày nghiệp vụ.

Tách khỏi file migration để CÓ THỂ TEST được — migration chỉ gọi vào đây. Đánh
đổi đã biết: nếu module này đổi về sau, migration 0027 chạy lại trên DB mới sẽ
chạy phiên bản mới. Chấp nhận được vì đây là thao tác một lần, idempotent, và
mọi câu lệnh đều tự bảo vệ bằng điều kiện `scanned_at < occurred_at`.

Set-based hoàn toàn (2 câu UPDATE): loop từng dòng trên bảng ledger prod sẽ giữ
khoá hàng phút.
"""
from __future__ import annotations

# Nhiều bản ghi quét có thể cùng trỏ về một đơn (unique chỉ ép giữa các bản
# PENDING) → gộp MIN(created_at) để kết quả tất định, không phụ thuộc bản nào
# được planner chọn.
_DISPATCH_SQL = """
UPDATE stock_movements m
SET occurred_at = x.scanned_at
FROM (
    SELECT matched_order_id AS order_id, MIN(created_at) AS scanned_at
    FROM pending_shipment_scans
    WHERE kind = 'dispatch'
      AND status = 'matched'
      AND matched_order_id IS NOT NULL
    GROUP BY matched_order_id
) x
WHERE m.reference_type = 'order'
  AND m.reference_id = x.order_id
  AND m.movement_type = 'sale'
  AND x.scanned_at < m.occurred_at;
"""

# Bút toán hoàn treo ở ReturnEntry; nối về mã quét qua shipping_code.
_RETURN_SQL = """
UPDATE stock_movements m
SET occurred_at = x.scanned_at
FROM (
    SELECT e.id AS entry_id, MIN(s.created_at) AS scanned_at
    FROM pending_shipment_scans s
    JOIN stock_return_entries e ON e.shipping_code = s.code
    WHERE s.kind = 'return'
      AND s.status = 'matched'
      AND s.code <> ''
    GROUP BY e.id
) x
WHERE m.reference_type = 'return_entry'
  AND m.reference_id = x.entry_id
  AND x.scanned_at < m.occurred_at;
"""


def backfill_from_pending_scans(connection) -> dict:
    """Trả số dòng đã nắn theo từng nhánh. `sale_reversal` không bao giờ bị đụng.

    `occurred_at` KHÔNG phải partition key nên UPDATE không làm dòng nhảy
    partition — đó chính là lý do ta không sửa `created_at`.
    """
    stats = {}
    with connection.cursor() as cur:
        cur.execute(_DISPATCH_SQL)
        stats["dispatch"] = cur.rowcount
        cur.execute(_RETURN_SQL)
        stats["return"] = cur.rowcount
    return stats
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `cd backend && pytest apps/inventory/tests/test_occurred_at_backfill.py -v`
Expected: PASS (3 test)

- [ ] **Step 5: Gắn vào migration**

Tạo `backend/apps/inventory/migrations/0027_backfill_occurred_at_from_scans.py`:

```python
from django.db import migrations

from apps.inventory.application.services.occurred_at_backfill import (
    backfill_from_pending_scans,
)


def forwards(apps, schema_editor):
    backfill_from_pending_scans(schema_editor.connection)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("inventory", "0026_occurred_at_not_null")]

    operations = [
        # Không có chiều ngược: giá trị cũ (= created_at) là thứ SAI mà migration
        # này đang sửa, khôi phục lại không có ý nghĩa nghiệp vụ nào.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
```

- [ ] **Step 6: Xác nhận migration chạy được từ đầu**

Run: `cd backend && pytest apps/inventory/tests/test_occurred_at_backfill.py -q --create-db`
Expected: PASS — `--create-db` ép dựng lại schema, tức toàn bộ chuỗi 0024→0027 phải chạy trót lọt.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/inventory/application/services/occurred_at_backfill.py backend/apps/inventory/migrations/0027_backfill_occurred_at_from_scans.py backend/apps/inventory/tests/test_occurred_at_backfill.py
git commit -m "fix(inventory): backfill business date for movements matched from scans"
```

---

### Task 7: Ngày kho nhận hàng bảo hành theo ngày quét

**Files:**
- Modify: `backend/apps/warranty/journey_service.py:103-130` (`mark_returned_by_tracking`)
- Modify: `backend/apps/inventory/application/services/pending_scan_service.py:494-520` (lời gọi trong `_process_one`), `:650-656` (`_match_warranty_journey_return`)
- Create: `backend/apps/warranty/received_at_backfill.py`
- Create: `backend/apps/warranty/migrations/0016_backfill_warehouse_received_at.py`
- Test: `backend/apps/warranty/tests/test_journey_received_at_scan_date.py`

**Interfaces:**
- Consumes: `PendingShipmentScan.created_at`, `PendingShipmentScan.result_ref` (định dạng `warranty-journey:<id>`).
- Produces:
  - `WarrantyJourneyService.mark_returned_by_tracking(code: str, received_at: date | None = None) -> WarrantyJourney | None`
  - `PendingScanService._match_warranty_journey_return(code: str, received_at)`
  - `backfill_warehouse_received_at(connection) -> int` trong `apps.warranty.received_at_backfill`

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/warranty/tests/test_journey_received_at_scan_date.py`. Fixture `order_factory` có sẵn từ `apps/warranty/tests/conftest.py` — **bắt buộc dùng**: `WarrantyClaim.order` là `OneToOneField` PROTECT không null, `WarrantyClaim.objects.create()` không tham số sẽ lỗi.

```python
"""Kho nhận hàng bảo hành ghi theo NGÀY QUÉT, không phải ngày cron khớp."""
from datetime import date, datetime, timezone as dt_timezone

import pytest
from django.db import connection

from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.models import (
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
)
from apps.warehouses.models import Warehouse
from apps.warranty.models import JourneyStatus, WarrantyClaim, WarrantyJourney
from apps.warranty.received_at_backfill import backfill_warehouse_received_at

pytestmark = pytest.mark.django_db

# 03/07 17:30 UTC = 04/07 00:30 giờ VN — bắt lỗi dùng `.date()` trên giá trị UTC.
SCANNED_AT = datetime(2026, 7, 3, 17, 30, tzinfo=dt_timezone.utc)
SCANNED_LOCAL_DATE = date(2026, 7, 4)
MATCHED_AT = datetime(2026, 7, 6, 3, 0, tzinfo=dt_timezone.utc)
MATCHED_LOCAL_DATE = date(2026, 7, 6)


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="Kho BH", code="KBH1")


@pytest.fixture
def journey(order_factory):
    # order_factory dựng Order tối thiểu kèm Channel/Customer (conftest của app).
    # Order đó có shipping_code rỗng nên KHÔNG khớp mã quét "BHX1" — mã phải rơi
    # xuống đúng nhánh dò hành trình bảo hành trong `_process_one`.
    claim = WarrantyClaim.objects.create(order=order_factory())
    return WarrantyJourney.objects.create(
        claim=claim, seq=1, recall_code="BHX1", status=JourneyStatus.CREATED
    )


def test_return_scan_sets_received_at_to_scan_date(journey, warehouse):
    scan = PendingShipmentScan.objects.create(
        code="BHX1", kind=PendingScanKind.RETURN, warehouse=warehouse
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(created_at=SCANNED_AT)

    PendingScanService.match_pending_scans()

    journey.refresh_from_db()
    assert journey.status == JourneyStatus.RETURNED
    assert journey.warehouse_received_at == SCANNED_LOCAL_DATE


def test_existing_received_at_is_not_overwritten(journey, warehouse):
    journey.warehouse_received_at = date(2026, 6, 30)
    journey.save(update_fields=["warehouse_received_at"])
    scan = PendingShipmentScan.objects.create(
        code="BHX1", kind=PendingScanKind.RETURN, warehouse=warehouse
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(created_at=SCANNED_AT)

    PendingScanService.match_pending_scans()

    journey.refresh_from_db()
    assert journey.warehouse_received_at == date(2026, 6, 30)


def test_backfill_fixes_value_written_by_cron(journey, warehouse):
    """Giá trị còn đúng bằng thứ cron ghi → nắn về ngày quét."""
    journey.warehouse_received_at = MATCHED_LOCAL_DATE
    journey.save(update_fields=["warehouse_received_at"])
    scan = PendingShipmentScan.objects.create(
        code="BHX1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
        result_ref=f"warranty-journey:{journey.id}",
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=SCANNED_AT, updated_at=MATCHED_AT
    )

    fixed = backfill_warehouse_received_at(connection)

    journey.refresh_from_db()
    assert fixed == 1
    assert journey.warehouse_received_at == SCANNED_LOCAL_DATE


def test_backfill_keeps_user_edited_value(journey, warehouse):
    """Người dùng đã sửa tay → không khớp ngày cron ghi → giữ nguyên."""
    journey.warehouse_received_at = date(2026, 6, 30)
    journey.save(update_fields=["warehouse_received_at"])
    scan = PendingShipmentScan.objects.create(
        code="BHX1", kind=PendingScanKind.RETURN, warehouse=warehouse,
        status=PendingScanStatus.MATCHED,
        result_ref=f"warranty-journey:{journey.id}",
    )
    PendingShipmentScan.objects.filter(pk=scan.pk).update(
        created_at=SCANNED_AT, updated_at=MATCHED_AT
    )

    fixed = backfill_warehouse_received_at(connection)

    journey.refresh_from_db()
    assert fixed == 0
    assert journey.warehouse_received_at == date(2026, 6, 30)
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `cd backend && pytest apps/warranty/tests/test_journey_received_at_scan_date.py -v`
Expected: FAIL — `ModuleNotFoundError` cho `received_at_backfill`, và test đầu tiên ra ngày hôm nay thay vì 04/07.

- [ ] **Step 3: `mark_returned_by_tracking` nhận `received_at`**

Trong `backend/apps/warranty/journey_service.py`, sửa (dòng 103-130):

```python
    @staticmethod
    def mark_returned_by_tracking(code: str, received_at=None) -> WarrantyJourney | None:
        """Quét HÀNG HOÀN khớp 1 trong 3 mã của hành trình → kho đã nhận hàng BH.

        Set trạng thái nội bộ `returned` + ngày kho nhận (nếu trống). KHÔNG ghi
        sổ tồn — hàng BH nhập kho qua phiếu kho riêng.

        ``received_at``: ngày kho THỰC SỰ nhận hàng. Luồng quét truyền ngày QUÉT
        (mã quét ngày D, cron khớp được ở ngày D+n thì kho vẫn nhận ngày D).
        Mặc định None ⇒ hôm nay, giữ nguyên hành vi cho caller khác.
        """
        from django.utils import timezone

        code = (code or "").strip()
        if not code:
            return None
        journey = (
            WarrantyJourney.objects.filter(
                Q(new_tracking_code=code) | Q(recall_code=code) | Q(return_tracking_code=code)
            )
            .order_by("-id")
            .first()
        )
        if journey is None:
            return None
        journey.status = JourneyStatus.RETURNED
        update_fields = ["status", "updated_at"]
        if journey.warehouse_received_at is None:
            journey.warehouse_received_at = received_at or timezone.localdate()
            update_fields.append("warehouse_received_at")
        journey.save(update_fields=update_fields)
        return journey
```

- [ ] **Step 4: Luồng quét truyền ngày quét**

Trong `backend/apps/inventory/application/services/pending_scan_service.py`, sửa `_match_warranty_journey_return` (dòng 650-656):

```python
    @staticmethod
    def _match_warranty_journey_return(code: str, received_at=None):
        """Quét HOÀN khớp 1 trong 3 mã hành trình BH → returned + ngày kho nhận
        (lazy import như trên)."""
        from apps.warranty.journey_service import WarrantyJourneyService

        return WarrantyJourneyService.mark_returned_by_tracking(
            code, received_at=received_at
        )
```

và lời gọi trong `_process_one` (dòng 503):

```python
                    journey = PendingScanService._match_warranty_journey_return(
                        scan.code,
                        # Ngày kho nhận = ngày QUÉT, không phải ngày cron khớp.
                        received_at=timezone.localtime(scan.created_at).date(),
                    )
```

- [ ] **Step 5: Viết hàm backfill bảo hành**

Tạo `backend/apps/warranty/received_at_backfill.py`:

```python
"""Nắn `warehouse_received_at` cho các hành trình bị cron đóng dấu ngày sai.

`warehouse_received_at` là trường NGƯỜI DÙNG SỬA ĐƯỢC (xem
`JOURNEY_EDITABLE_FIELDS`) nên không được nắn mù. Chỉ nắn khi giá trị hiện tại
vẫn ĐÚNG BẰNG thứ cron đã ghi — tức bằng ngày bản ghi quét chuyển sang `matched`
(`pending_shipment_scans.updated_at`; bản ghi matched không bị đụng sau đó, cùng
giả định mà `PendingScanService.list_unmatched` đang dùng cho `matched_at`). Ai
đã sửa tay sẽ không khớp vế đó ⇒ được giữ nguyên.

Tách khỏi migration để test được; xem ghi chú đánh đổi ở
`apps/inventory/application/services/occurred_at_backfill.py`.
"""
from __future__ import annotations

# TIME_ZONE = "Asia/Bangkok" (config/settings_parts/base.py) — phải khớp với
# `timezone.localdate()` bên Python, nếu không sẽ lệch đúng 1 ngày quanh nửa đêm.
_SQL = """
UPDATE warranty_journeys j
SET warehouse_received_at = (s.created_at AT TIME ZONE 'Asia/Bangkok')::date
FROM pending_shipment_scans s
WHERE s.kind = 'return'
  AND s.status = 'matched'
  AND s.result_ref = 'warranty-journey:' || j.id
  AND j.warehouse_received_at = (s.updated_at AT TIME ZONE 'Asia/Bangkok')::date
  AND j.warehouse_received_at <> (s.created_at AT TIME ZONE 'Asia/Bangkok')::date;
"""


def backfill_warehouse_received_at(connection) -> int:
    """Trả số hành trình đã nắn."""
    with connection.cursor() as cur:
        cur.execute(_SQL)
        return cur.rowcount
```

- [ ] **Step 6: Gắn vào migration**

Tạo `backend/apps/warranty/migrations/0016_backfill_warehouse_received_at.py`:

```python
from django.db import migrations

from apps.warranty.received_at_backfill import backfill_warehouse_received_at


def forwards(apps, schema_editor):
    backfill_warehouse_received_at(schema_editor.connection)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("warranty", "0015_journey_best_tracking_status")]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
```

- [ ] **Step 7: Chạy test để xác nhận PASS**

Run: `cd backend && pytest apps/warranty/tests/test_journey_received_at_scan_date.py -v`
Expected: PASS (4 test)

- [ ] **Step 8: Hồi quy toàn bộ**

Run: `cd backend && pytest apps/inventory apps/orders apps/warranty -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/apps/warranty/journey_service.py backend/apps/inventory/application/services/pending_scan_service.py backend/apps/warranty/received_at_backfill.py backend/apps/warranty/migrations/0016_backfill_warehouse_received_at.py backend/apps/warranty/tests/test_journey_received_at_scan_date.py
git commit -m "fix(warranty): record warehouse received date from scan date"
```

---

### Task 8: Chạy toàn bộ test và mở MR

**Files:** —

**Interfaces:**
- Consumes: toàn bộ Task 1-7.
- Produces: MR trên GitLab.

- [ ] **Step 1: Chạy toàn bộ test suite trên DB dựng mới**

Run: `cd backend && pytest -q --create-db`
Expected: PASS toàn bộ. `--create-db` bắt buộc ở bước này: nó ép chạy lại chuỗi migration 0024→0027 + warranty 0016 từ đầu, đúng như khi deploy.

Nếu có test hỏng, sửa và commit trước khi đi tiếp — **không** mở MR với test đỏ.

- [ ] **Step 2: Ghi lại kết quả thật**

Dán số test pass/fail vào phần mô tả MR. Nếu có test bị skip, nói rõ test nào và vì sao.

- [ ] **Step 3: Push và mở MR**

```bash
git push -u origin fix/scan-match-business-date
```

Mở MR vào `master` với mô tả gồm: vấn đề (số liệu tab Xuất nhảy sang ngày cron khớp), cách sửa (cột `occurred_at`), và **cảnh báo vận hành**: migration 0027 nắn lại lịch sử nên báo cáo các ngày đã qua sẽ đổi số so với ảnh chụp trước đó; migration 0026 tạo index trên bảng partition lớn nên deploy vào giờ thấp điểm.

---

## Ghi chú triển khai

Thứ tự deploy an toàn: 0024 (thêm cột) → 0025 (backfill) → 0026 (NOT NULL + index) → 0027 (nắn lịch sử) → warranty 0016. Code đọc theo `occurred_at` (Task 5) chỉ đúng SAU khi 0025 đã lấp hết cột — nếu deploy code trước migration, các dòng `occurred_at IS NULL` sẽ biến mất khỏi báo cáo. Chạy migration trước, rồi mới rollout code.
