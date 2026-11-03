# Ghi nhận hàng gửi bù (đổi lỗi 2 chiều) khi khớp tay — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Khi khớp tay một mã vận đơn chờ khớp vào đơn **đã xuất kho trước đó**, cho người dùng ghi nhận đây là **hàng gửi bù** và sinh Phiếu Xuất kho (trạng thái chờ duyệt) ngay tại chỗ, thay vì phải nhớ sang màn hình Phiếu kho.

**Architecture:** Không nới guard `already_dispatched` (giữ invariant tồn treo theo đơn). Thay vào đó, sau khi `_dispatch_and_finalize` trả về warning `already_dispatched`, service sinh một `StockDocument` doc_type=`issue` ở trạng thái `PENDING_APPROVAL`, mốc nghiệp vụ = ngày kho quét. Một request duy nhất: FE hỏi trước dựa trên cờ ở lookup, BE mới là nơi quyết định có tạo phiếu hay không.

**Tech Stack:** Python 3.12 / Django / DRF / pytest · React + TypeScript / React Query / shadcn/ui / vitest · PostgreSQL.

**Spec:** [docs/superpowers/specs/2026-07-24-replacement-issue-on-manual-match-design.md](../specs/2026-07-24-replacement-issue-on-manual-match-design.md)

## Global Constraints

- Worktree: `c:/Users/MYPC/Desktop/workspace/desi-order-replacement-issue`, branch `feat/replacement-issue-on-manual-match` (cắt từ `origin/master`).
- Commit message **tiếng Anh, ngắn**, KHÔNG kèm `Co-Authored-By`.
- Backend: clean architecture sẵn có — logic ở `application/services`, view mỏng ở `transport/views`.
- Comment code bằng **tiếng Việt**, giải thích **tại sao** (bám phong cách file hiện tại), không comment lại điều code đã nói.
- TUYỆT ĐỐI không đụng: guard `already_dispatched`, `issue_for_order`, `ScanDispatchDialog`, cron `match_pending_scans`, `ReturnEntryService`.
- Mọi thay đổi phải giữ nguyên hành vi khi `replacement=None` / `occurred_at=None` (đường cũ không được hồi quy).
- **Lệnh chạy pytest** (dùng nguyên văn ở mọi task backend, thay `<paths>`):

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-replacement-issue && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=test_replacement_issue \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$PWD/backend:/app" -w /app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Bẫy đã biết: image tên `desi-backend-test:latest`; thiếu `ENCRYPTION_KEY` → mọi test ERROR ở migration; nếu `desi-test-redis` chưa ở network thì chạy một lần `docker network connect desi-test-net desi-test-redis`; **không bao giờ** gọi `cache.clear()` trong test.

- **Lệnh chạy vitest:** `cd frontend && npm run test -- <đường dẫn file test>`

## File Structure

| File | Trách nhiệm | Task |
|---|---|---|
| `backend/apps/inventory/models.py` | thêm cột `StockDocument.occurred_at` | 1 |
| `backend/apps/inventory/migrations/0029_stockdocument_occurred_at.py` | migration cột nullable | 1 |
| `backend/apps/inventory/application/services/stock_document_service.py` | nhận + truyền `occurred_at` xuống bút toán | 1 |
| `backend/apps/inventory/tests/test_stock_document_occurred_at.py` | test mốc nghiệp vụ của phiếu | 1 |
| `backend/apps/inventory/application/services/pending_scan_service.py` | `manual_match(replacement=…)` + `_create_replacement_issue` + guard | 2 |
| `backend/apps/inventory/tests/test_manual_match_replacement_issue.py` | test 4 guard + idempotent | 2 |
| `backend/apps/inventory/transport/views/flow_history_views.py` | validate `replacement`, mở rộng `flow_order_lookup` | 3 |
| `backend/schema.yaml` + `frontend/src/api/schema.d.ts` | regen OpenAPI | 3 |
| `backend/apps/inventory/tests/test_flow_history_views.py` | test endpoint | 3 |
| `frontend/src/features/inventory/PendingMatchControl.tsx` | hộp xác nhận hàng gửi bù | 4 |
| `frontend/src/features/inventory/flow-match-queries.ts` | payload `replacement` + invalidate `documents` | 4 |
| `frontend/src/features/inventory/__tests__/pending-match-replacement.test.tsx` | test hộp xác nhận | 4 |

---

### Task 1: Mốc nghiệp vụ cho phiếu kho (`StockDocument.occurred_at`)

Không có cột này thì bút toán mang **ngày duyệt**; duyệt muộn 2 hôm là sản lượng rơi sai ngày trên báo cáo (báo cáo lọc/sắp theo `occurred_at`).

**Files:**
- Modify: `backend/apps/inventory/models.py:183` (ngay dưới `created_at` của `StockDocument`)
- Create: `backend/apps/inventory/migrations/0029_stockdocument_occurred_at.py`
- Modify: `backend/apps/inventory/application/services/stock_document_service.py` (`create_document`, `_apply_line`)
- Test: `backend/apps/inventory/tests/test_stock_document_occurred_at.py`

**Interfaces:**
- Consumes: `StockCommandService.record_movement(..., occurred_at: datetime | None = None)` — đã có sẵn, mặc định `now()`.
- Produces: `StockDocumentService.create_document(..., occurred_at: datetime | None = None) -> StockDocument`; field `StockDocument.occurred_at`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_stock_document_occurred_at.py`:

```python
"""Mốc nghiệp vụ của phiếu kho: bút toán phải mang ngày HÀNG RỜI KHO, không phải ngày duyệt."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.inventory.models import DocType, MovementType, StockMovement
from apps.orders.models import Product
from apps.users.test_helpers import make_admin
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="Kho phieu", code="KPX1")


@pytest.fixture
def product():
    return Product.objects.create(name="SP phieu", sku="SKU-PXK-1")


@pytest.fixture
def actor():
    return make_admin("stock-doc-occurred-at")


def _approve(doc_id: int, user_id: int):
    StockDocumentService.submit_for_approval(document_id=doc_id, user_id=user_id)
    return StockDocumentService.approve_document(document_id=doc_id, user_id=user_id)


def _issue_movement(doc_id: int) -> StockMovement:
    return StockMovement.objects.get(
        reference_type="document", reference_id=doc_id, movement_type=MovementType.ISSUE
    )


def test_issue_movement_uses_document_occurred_at(warehouse, product, actor):
    """Duyệt muộn 2 ngày không được đẩy sản lượng sang ngày duyệt."""
    scanned_at = timezone.now() - timedelta(days=2)
    doc = StockDocumentService.create_document(
        doc_type=DocType.ISSUE,
        source_warehouse_id=warehouse.id,
        lines=[{"product_id": product.id, "quantity": 1}],
        user_id=actor.id,
        occurred_at=scanned_at,
    )
    _approve(doc.id, actor.id)
    movement = _issue_movement(doc.id)
    assert movement.occurred_at == scanned_at
    assert movement.created_at > scanned_at


def test_manual_document_without_occurred_at_keeps_now(warehouse, product, actor):
    """Phiếu tay hiện có (occurred_at=None) giữ NGUYÊN hành vi cũ: bút toán mang lúc duyệt."""
    before = timezone.now()
    doc = StockDocumentService.create_document(
        doc_type=DocType.ISSUE,
        source_warehouse_id=warehouse.id,
        lines=[{"product_id": product.id, "quantity": 1}],
        user_id=actor.id,
    )
    assert doc.occurred_at is None
    _approve(doc.id, actor.id)
    assert _issue_movement(doc.id).occurred_at >= before
```

- [ ] **Step 2: Chạy test để xác nhận nó FAIL**

Chạy lệnh pytest ở Global Constraints với `<paths>` = `apps/inventory/tests/test_stock_document_occurred_at.py`
Kỳ vọng: FAIL — `TypeError: create_document() got an unexpected keyword argument 'occurred_at'`.

- [ ] **Step 3: Thêm field vào model**

Trong `backend/apps/inventory/models.py`, class `StockDocument`, chèn ngay **sau** dòng `created_at = models.DateTimeField(default=timezone.now)`:

```python
    # Mốc nghiệp vụ = lúc hàng thực rời/về kho, tách khỏi `created_at` (lúc lập phiếu)
    # và `posted_at` (lúc duyệt). None → bút toán lấy now() lúc duyệt: giữ nguyên hành
    # vi của mọi phiếu tay. Phiếu sinh tự động (hàng gửi bù) set = ngày kho QUÉT để sản
    # lượng rơi đúng ngày làm việc dù phiếu được duyệt muộn.
    occurred_at = models.DateTimeField(null=True, blank=True)
```

- [ ] **Step 4: Viết migration**

Tạo `backend/apps/inventory/migrations/0029_stockdocument_occurred_at.py`:

```python
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0028_stockmove_occurred_index")]

    operations = [
        migrations.AddField(
            model_name="stockdocument",
            name="occurred_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
```

- [ ] **Step 5: Cho `create_document` nhận `occurred_at`**

Trong `stock_document_service.py`, hàm `create_document`: thêm tham số `occurred_at=None` vào chữ ký (đặt ngay sau `created_at=None`), và thêm khoá vào `create_kwargs`:

```python
        create_kwargs = {
            "doc_type": doc_type,
            "source_warehouse_id": source_warehouse_id,
            "dest_warehouse_id": dest_warehouse_id,
            "note": note,
            "created_by_id": user_id,
            "occurred_at": occurred_at,
        }
```

- [ ] **Step 6: Truyền `occurred_at` xuống bút toán**

Trong `_apply_line`, sửa dict `ref` (một chỗ duy nhất — mọi nhánh doc_type đều dùng `**ref`):

```python
        # occurred_at của phiếu (None với phiếu tay) → record_movement tự lấy now().
        # Nhờ đi qua `ref`, cả 4 nhánh doc_type dùng chung một luật, không lệch nhau.
        ref = dict(
            reference_type=_REFERENCE_TYPE,
            reference_id=doc.id,
            user_id=user_id,
            occurred_at=doc.occurred_at,
        )
```

- [ ] **Step 7: Chạy test để xác nhận PASS**

`<paths>` = `apps/inventory/tests/test_stock_document_occurred_at.py`
Kỳ vọng: `2 passed`.

- [ ] **Step 8: Chạy hồi quy phiếu kho + báo cáo**

`<paths>` = `apps/inventory/tests/test_ledger_documents.py apps/inventory/tests/test_issue_document_history.py`
Kỳ vọng: toàn bộ passed (phiếu tay `occurred_at=None` không đổi hành vi).

- [ ] **Step 9: Commit**

```bash
git add backend/apps/inventory/models.py backend/apps/inventory/migrations/0029_stockdocument_occurred_at.py backend/apps/inventory/application/services/stock_document_service.py backend/apps/inventory/tests/test_stock_document_occurred_at.py
git commit -m "feat(inventory): add business date to stock documents"
```

---

### Task 2: `manual_match(replacement=…)` sinh Phiếu Xuất kho hàng gửi bù

**Files:**
- Modify: `backend/apps/inventory/application/services/pending_scan_service.py` (`_complete_dispatch`, `_dispatch_and_finalize`, `manual_match`, thêm `_create_replacement_issue`)
- Test: `backend/apps/inventory/tests/test_manual_match_replacement_issue.py`

**Interfaces:**
- Consumes: `StockDocumentService.create_document(..., occurred_at=…)` và `StockDocumentService.submit_for_approval(document_id, user_id)` (Task 1).
- Produces:
  - `PendingScanService.manual_match(*, scan_id, order_id, actor=None, replacement: dict | None = None) -> dict` — dict trả thêm 2 khoá `stock_doc_code: str` và `replacement_skipped: str` (đều rỗng khi không áp dụng).
  - `replacement` shape: `{"lines": [{"product_id": int, "quantity": int}], "note": str}`.
  - Hằng số lý do bỏ qua: `"order_was_not_dispatched"`, `"already_created"`, `"invalid_lines"`.
  - `_dispatch_and_finalize` trả thêm khoá `"warnings": list[str]`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/inventory/tests/test_manual_match_replacement_issue.py`:

```python
"""Hàng gửi bù (đổi lỗi 2 chiều): khớp tay vào đơn ĐÃ xuất → sinh Phiếu Xuất kho chờ duyệt.

Guard quan trọng nhất là chống TRỪ TỒN 2 LẦN: chỉ tạo phiếu khi lần khớp đó thực sự
bị `already_dispatched` chặn, tức là không có bút toán SALE nào vừa được ghi.
"""
import pytest

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.inventory.application.services.pending_scan_service import PendingScanService
from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_document_service import (
    StockDocumentService,
)
from apps.inventory.models import (
    DocStatus,
    DocType,
    MovementType,
    PendingScanKind,
    PendingScanStatus,
    PendingShipmentScan,
    StockDocument,
    StockItem,
    StockMovement,
    StockState,
)
from apps.orders.models import Order, OrderItem, OrderStatus, Product
from apps.users.test_helpers import make_admin
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


@pytest.fixture
def warehouse():
    return Warehouse.objects.create(name="Kho bu", code="KBU1")


@pytest.fixture
def actor():
    return make_admin("replacement-issue-actor")


@pytest.fixture
def channel():
    return Channel.objects.create(code="lp-replacement", name="Replacement Channel")


@pytest.fixture
def order(channel, warehouse):
    """Đơn AWAITING_SHIPMENT, 1 SKU × 1, tồn kho đủ 5 cái."""
    customer = Customer.objects.create(
        full_name="Khach Bu",
        phone_raw="0929111111",
        phone_normalized="0929111111",
        code="CUST-BU-1",
    )
    product = Product.objects.create(name="SP Bu", sku="MSC06-T")
    o = Order.objects.create(
        channel=channel,
        customer=customer,
        name="Khach Bu",
        phone="0929111111",
        total_amount="200000",
        status=OrderStatus.AWAITING_SHIPMENT,
        shipping_code="VD-GOC",
        invoice_code="HD-TEST-1",
        fulfillment_warehouse=warehouse,
    )
    OrderItem.objects.create(
        order=o,
        product=product,
        product_name=product.name,
        sku=product.sku,
        quantity=1,
        unit_price=100000,
        line_total=100000,
    )
    StockCommandService.record_movement(
        warehouse_id=warehouse.id,
        product_id=product.id,
        qty_delta=5,
        movement_type=MovementType.RECEIPT,
    )
    return o


def _product_id(order) -> int:
    return order.items.first().product_id


def _sellable(warehouse_id: int, product_id: int) -> int:
    item = StockItem.objects.filter(
        warehouse_id=warehouse_id, product_id=product_id, state=StockState.SELLABLE
    ).first()
    return item.quantity if item else 0


def _scan(warehouse, actor, code: str) -> PendingShipmentScan:
    return PendingShipmentScan.objects.create(
        code=code,
        kind=PendingScanKind.DISPATCH,
        warehouse=warehouse,
        scanned_by=actor,
    )


def _dispatch_first_time(order, warehouse, actor):
    """Lần xuất gốc: khớp tay mã VĐ gốc → ghi SALE, đơn có tồn treo."""
    scan = _scan(warehouse, actor, "VD-GOC")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor
    )
    assert result["ok"] is True
    return result


def _replacement(product_id: int, qty: int = 1, note: str = "") -> dict:
    return {"lines": [{"product_id": product_id, "quantity": qty}], "note": note}


def test_creates_pending_approval_issue_document(order, warehouse, actor):
    product_id = _product_id(order)
    _dispatch_first_time(order, warehouse, actor)
    after_first = _sellable(warehouse.id, product_id)

    scan = _scan(warehouse, actor, "VD-DOI-2CHIEU")
    result = PendingScanService.manual_match(
        scan_id=scan.id,
        order_id=order.id,
        actor=actor,
        replacement=_replacement(product_id, note="ko vao dien"),
    )

    assert result["ok"] is True
    assert result["replacement_skipped"] == ""
    doc = StockDocument.objects.get(code=result["stock_doc_code"])
    assert doc.doc_type == DocType.ISSUE
    assert doc.status == DocStatus.PENDING_APPROVAL
    assert doc.source_warehouse_id == warehouse.id
    # Mốc nghiệp vụ = lúc kho QUÉT, không phải lúc khớp/duyệt.
    assert doc.occurred_at == scan.created_at
    # Note mang mã đơn + mã VĐ để đối soát ngược, kèm ghi chú người dùng.
    assert "HD-TEST-1" in doc.note
    assert "VD-DOI-2CHIEU" in doc.note
    assert "ko vao dien" in doc.note
    # Chưa duyệt → tồn CHƯA đổi.
    assert _sellable(warehouse.id, product_id) == after_first

    scan.refresh_from_db()
    assert scan.status == PendingScanStatus.MATCHED
    assert scan.result_ref == f"stock-doc:{doc.id}"


def test_approving_document_deducts_stock_once(order, warehouse, actor):
    product_id = _product_id(order)
    _dispatch_first_time(order, warehouse, actor)
    after_first = _sellable(warehouse.id, product_id)

    scan = _scan(warehouse, actor, "VD-DOI-2")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement=_replacement(product_id),
    )
    doc = StockDocument.objects.get(code=result["stock_doc_code"])
    StockDocumentService.approve_document(document_id=doc.id, user_id=actor.id)

    assert after_first - _sellable(warehouse.id, product_id) == 1
    movement = StockMovement.objects.get(
        reference_type="document", reference_id=doc.id, movement_type=MovementType.ISSUE
    )
    assert movement.occurred_at == scan.created_at


def test_second_call_does_not_create_duplicate_document(order, warehouse, actor):
    product_id = _product_id(order)
    _dispatch_first_time(order, warehouse, actor)
    scan = _scan(warehouse, actor, "VD-DOI-3")
    PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement=_replacement(product_id),
    )
    # Khớp lại cùng scan (double-click / retry): scan đã MATCHED nên bị chặn từ đầu.
    second = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement=_replacement(product_id),
    )
    assert second["ok"] is False
    assert StockDocument.objects.filter(doc_type=DocType.ISSUE).count() == 1


def test_not_dispatched_order_skips_document(order, warehouse, actor):
    """Đơn CHƯA từng xuất: lần khớp này ghi SALE → tuyệt đối không tạo thêm phiếu."""
    product_id = _product_id(order)
    scan = _scan(warehouse, actor, "VD-GOC")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement=_replacement(product_id),
    )
    assert result["ok"] is True
    assert result["stock_doc_code"] == ""
    assert result["replacement_skipped"] == "order_was_not_dispatched"
    assert StockDocument.objects.count() == 0
    assert StockMovement.objects.filter(
        reference_type="order", reference_id=order.id, movement_type=MovementType.SALE
    ).exists()


def test_invalid_lines_skip_document(order, warehouse, actor):
    product_id = _product_id(order)
    _dispatch_first_time(order, warehouse, actor)
    scan = _scan(warehouse, actor, "VD-DOI-4")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement={"lines": [{"product_id": product_id, "quantity": 0}], "note": ""},
    )
    assert result["ok"] is True
    assert result["replacement_skipped"] == "invalid_lines"
    assert StockDocument.objects.count() == 0


def test_document_appears_in_issue_report_on_scan_date(order, warehouse, actor):
    """Phiếu bù phải rơi đúng NGÀY QUÉT trong báo cáo Xuất; huỷ phiếu → net về 0."""
    from django.utils import timezone

    from apps.inventory.application.services.issue_document_history import (
        MergedIssueHistory,
    )

    product_id = _product_id(order)
    _dispatch_first_time(order, warehouse, actor)
    scan = _scan(warehouse, actor, "VD-DOI-6")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement=_replacement(product_id),
    )
    doc = StockDocument.objects.get(code=result["stock_doc_code"])
    StockDocumentService.approve_document(document_id=doc.id, user_id=actor.id)

    day = timezone.localtime(scan.created_at).date().isoformat()
    page = MergedIssueHistory.orders_page(
        pagination={"limit": 50, "offset": 0}, date_from=day, date_to=day
    )
    assert page["doc_quantity"] == 1
    assert any(
        item.get("kind") == "document" and item.get("doc_code") == doc.code
        for item in page["items"]
    )

    StockDocumentService.cancel_document(document_id=doc.id, user_id=actor.id)
    after = MergedIssueHistory.orders_page(
        pagination={"limit": 50, "offset": 0}, date_from=day, date_to=day
    )
    assert after["doc_quantity"] == 0


def test_report_search_finds_document_by_order_and_tracking_code(order, warehouse, actor):
    """Ô tìm khớp doc.note → gõ mã đơn gốc HOẶC mã VĐ đều phải ra dòng phiếu bù."""
    from django.utils import timezone

    from apps.inventory.application.services.issue_document_history import (
        MergedIssueHistory,
    )

    product_id = _product_id(order)
    _dispatch_first_time(order, warehouse, actor)
    scan = _scan(warehouse, actor, "VD-DOI-7")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor,
        replacement=_replacement(product_id),
    )
    doc = StockDocument.objects.get(code=result["stock_doc_code"])
    StockDocumentService.approve_document(document_id=doc.id, user_id=actor.id)

    day = timezone.localtime(scan.created_at).date().isoformat()
    for term in ("HD-TEST-1", "VD-DOI-7"):
        page = MergedIssueHistory.orders_page(
            pagination={"limit": 50, "offset": 0},
            date_from=day, date_to=day, order=term,
        )
        codes = [i.get("doc_code") for i in page["items"] if i.get("kind") == "document"]
        assert doc.code in codes, f"tìm '{term}' không ra dòng phiếu bù"


def test_without_replacement_behaviour_unchanged(order, warehouse, actor):
    """Đường cũ (replacement=None) không được đổi một chút nào."""
    _dispatch_first_time(order, warehouse, actor)
    scan = _scan(warehouse, actor, "VD-DOI-5")
    result = PendingScanService.manual_match(
        scan_id=scan.id, order_id=order.id, actor=actor
    )
    assert result["ok"] is True
    assert result["stock_doc_code"] == ""
    assert result["replacement_skipped"] == ""
    assert StockDocument.objects.count() == 0
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

`<paths>` = `apps/inventory/tests/test_manual_match_replacement_issue.py`
Kỳ vọng: FAIL — `TypeError: manual_match() got an unexpected keyword argument 'replacement'`.

- [ ] **Step 3: Cho `_complete_dispatch` trả về danh sách warning thô**

Trong `pending_scan_service.py`, sửa `_complete_dispatch` — đổi kiểu trả về từ `tuple[str, str]` thành `tuple[str, str, list[str]]`, và câu cuối:

```python
        warnings = list(result.get("warnings") or [])
        return "", ", ".join(warnings), warnings
```

Cập nhật docstring: `Trả về (result_ref, thông điệp, danh sách warning thô)`. Khớp chuỗi trong thông điệp là mong manh — nhánh gửi bù cần **list gốc** để so `already_dispatched`.

- [ ] **Step 4: Cho `_dispatch_and_finalize` mang warning ra ngoài**

Trong `_dispatch_and_finalize`:

- Ngay trước `try:`, thêm `warnings: list[str] = []`.
- Nhánh thành công đổi thành:

```python
            result_ref, note, warnings = PendingScanService._complete_dispatch(
                scan, order, actor=actor
            )
```

- Cả 4 câu `return` (nhánh `stale` và nhánh cuối) thêm khoá `"warnings"`:

```python
                return {
                    "ok": False, "outcome": None, "error": "stale",
                    "result_ref": "", "warnings": [],
                }
```

```python
        return {
            "ok": ok, "outcome": outcome, "error": message,
            "result_ref": result_ref, "warnings": warnings,
        }
```

- [ ] **Step 5: Viết `_create_replacement_issue`**

Thêm hằng số ở đầu module (cạnh `_VERIFY_RETRY_BACKOFF_SECONDS`):

```python
# Lý do BỎ QUA việc sinh phiếu hàng gửi bù — trả thẳng cho FE để nói rõ với người dùng
# thay vì im lặng không tạo gì.
SKIP_NOT_DISPATCHED = "order_was_not_dispatched"
SKIP_ALREADY_CREATED = "already_created"
SKIP_INVALID_LINES = "invalid_lines"
```

Thêm method vào `PendingScanService` (đặt ngay sau `manual_match`):

```python
    @staticmethod
    def _create_replacement_issue(
        *, scan: PendingShipmentScan, order, actor, replacement: dict | None,
        warnings: list[str],
    ) -> tuple[str, str]:
        """Sinh Phiếu Xuất kho cho HÀNG GỬI BÙ (đổi lỗi 2 chiều). Trả (mã phiếu, lý do bỏ qua).

        Nghiệp vụ: đơn đã xuất kho nay gửi THÊM một cái thay thế bằng mã vận đơn mới.
        Guard `already_dispatched` chặn trừ tồn lần 2 trên cùng đơn (đúng), nên cái
        thay thế phải trừ tồn bằng phiếu xuất kho riêng — xem
        docs/erp-issue-history-include-stock-documents.md.

        Gọi NGOÀI mọi transaction đang mở: `_dispatch_and_finalize` đã đóng transaction
        của nó trước khi trả về (không được giữ transaction qua HTTP tới KiotViet). Ở
        đây khoá lại scan để re-check idempotent trước khi ghi.
        """
        from apps.inventory.application.services.stock_document_service import (
            StockDocumentService,
        )

        if not replacement:
            return "", ""
        if "already_dispatched" not in warnings:
            # Lần khớp này VỪA ghi SALE cho đơn → tạo thêm phiếu là trừ tồn 2 lần.
            # Fail-safe: thà thiếu phiếu (người dùng thấy cảnh báo) còn hơn sai tồn.
            return "", SKIP_NOT_DISPATCHED
        lines = [
            {"product_id": int(row["product_id"]), "quantity": int(row["quantity"])}
            for row in (replacement.get("lines") or [])
            if row.get("product_id") and int(row.get("quantity") or 0) > 0
        ]
        if not lines:
            return "", SKIP_INVALID_LINES

        order_label = order.invoice_code or order.order_code or f"#{order.id}"
        parts = [f"Hàng gửi bù đổi 2 chiều · đơn {order_label} · VĐ {scan.code}"]
        user_note = (replacement.get("note") or "").strip()
        if user_note:
            parts.append(user_note)
        user_id = actor.id if actor else scan.scanned_by_id

        with transaction.atomic():
            # result_ref="" trong điều kiện lọc = khoá idempotent: bản ghi đã sinh phiếu
            # thì lần gọi thứ 2 không match, không tạo phiếu trùng.
            locked = (
                PendingShipmentScan.objects.select_for_update()
                .filter(id=scan.id, status=PendingScanStatus.MATCHED, result_ref="")
                .first()
            )
            if locked is None:
                return "", SKIP_ALREADY_CREATED
            doc = StockDocumentService.create_document(
                doc_type=DocType.ISSUE,
                source_warehouse_id=scan.warehouse_id,
                lines=lines,
                note=" · ".join(parts),
                user_id=user_id,
                # Ngày kho QUÉT, không phải ngày duyệt: duyệt muộn vẫn thuộc đúng ngày
                # làm việc. Cùng luật với `_complete_dispatch` / `_complete_return`.
                occurred_at=scan.created_at,
            )
            # Vào thẳng hàng chờ duyệt: giữ nguyên vòng kiểm soát xuất kho (tồn chỉ trừ
            # khi có người duyệt), đồng thời không để lại phiếu nháp mồ côi.
            StockDocumentService.submit_for_approval(
                document_id=doc.id, user_id=user_id
            )
            locked.result_ref = f"stock-doc:{doc.id}"
            locked.save(update_fields=["result_ref", "updated_at"])
        return doc.code, ""
```

Thêm `DocType` vào import model ở đầu file (khối `from apps.inventory.models import (...)`), giữ thứ tự alphabet: chèn `DocType,` ngay trước `MovementType,`.

- [ ] **Step 6: Nối vào `manual_match`**

Sửa chữ ký: `def manual_match(*, scan_id: int, order_id: int, actor=None, replacement: dict | None = None) -> dict:` và bổ sung vào docstring:

```
        ``replacement`` (chỉ kind=dispatch): dict ``{"lines": [...], "note": str}`` —
        đánh dấu mã này là HÀNG GỬI BÙ của đơn đã xuất, sinh thêm Phiếu Xuất kho.
        ``None`` (mặc định) → hành vi cũ, không tạo gì.
```

Nhánh `return` (kind=return, khoảng dòng 757) thêm 2 khoá cho đồng nhất shape:

```python
                return {
                    "ok": True,
                    "result_ref": result_ref,
                    "note": note or "",
                    "order_id": order.id,
                    "stock_doc_code": "",
                    "replacement_skipped": "",
                }
```

Cuối hàm (nhánh dispatch) đổi thành:

```python
        doc_code, skipped = PendingScanService._create_replacement_issue(
            scan=scan,
            order=order,
            actor=actor,
            replacement=replacement,
            warnings=result["warnings"],
        )
        return {
            "ok": True,
            "result_ref": result["result_ref"],
            "note": result["error"],
            "order_id": order.id,
            "stock_doc_code": doc_code,
            "replacement_skipped": skipped,
        }
```

- [ ] **Step 7: Chạy test để xác nhận PASS**

`<paths>` = `apps/inventory/tests/test_manual_match_replacement_issue.py`
Kỳ vọng: `8 passed`.

- [ ] **Step 8: Chạy hồi quy toàn bộ luồng mã chờ khớp**

`<paths>` = `apps/inventory/tests/test_pending_scan_service_match.py apps/inventory/tests/test_pending_scan_cancelled.py apps/inventory/tests/test_pending_scan_occurred_at.py`
Kỳ vọng: toàn bộ passed.

- [ ] **Step 9: Commit**

```bash
git add backend/apps/inventory/application/services/pending_scan_service.py backend/apps/inventory/tests/test_manual_match_replacement_issue.py
git commit -m "feat(scan): create issue document for two-way exchange replacement"
```

---

### Task 3: Endpoint — nhận `replacement`, lookup trả cờ đã-xuất

**Files:**
- Modify: `backend/apps/inventory/transport/views/flow_history_views.py:763-842` (`flow_order_lookup`, `flow_manual_match`)
- Modify: `backend/schema.yaml`, `frontend/src/api/schema.d.ts` (regen)
- Test: `backend/apps/inventory/tests/test_flow_history_views.py` (thêm ca mới ở cuối file)

**Interfaces:**
- Consumes: `PendingScanService.manual_match(..., replacement=…)` (Task 2).
- Produces:
  - `POST /api/inventory/flow-pending/{scan_id}/match` body thêm `replacement` (optional); response thêm `stock_doc_code: str`, `replacement_skipped: str`.
  - `GET /api/inventory/flow-order-lookup` mỗi item thêm `already_dispatched: bool` và `items: [{product_id: int, sku: str, quantity: int}]`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/inventory/tests/test_flow_history_views.py`:

```python
# ── Hàng gửi bù (đổi lỗi 2 chiều) qua khớp tay ───────────────────────────────


def test_flow_order_lookup_returns_dispatched_flag_and_items(client_admin):
    from apps.inventory.application.services.stock_command_service import (
        StockCommandService,
    )
    from apps.inventory.models import MovementType
    from apps.orders.models import Order, OrderItem, OrderStatus, Product
    from apps.warehouses.models import Warehouse

    warehouse = Warehouse.objects.create(name="Kho LK", code="KLK1")
    product = Product.objects.create(name="SP LK", sku="SKU-LK-1")
    order = Order.objects.create(
        name="Khach LK",
        phone="0929222222",
        total_amount="100000",
        status=OrderStatus.AWAITING_SHIPMENT,
        invoice_code="HD-LOOKUP-1",
        fulfillment_warehouse=warehouse,
    )
    OrderItem.objects.create(
        order=order, product=product, product_name=product.name, sku=product.sku,
        quantity=3, unit_price=100000, line_total=300000,
    )
    # Đơn đã xuất kho: còn tồn NET đã trừ chưa hoàn.
    StockCommandService.record_movement(
        warehouse_id=warehouse.id, product_id=product.id, qty_delta=-3,
        movement_type=MovementType.SALE, reference_type="order", reference_id=order.id,
    )

    res = client_admin.get("/api/inventory/flow-order-lookup?q=HD-LOOKUP-1")
    assert res.status_code == 200
    item = next(i for i in res.json()["items"] if i["id"] == order.id)
    assert item["already_dispatched"] is True
    assert item["items"] == [
        {"product_id": product.id, "sku": "SKU-LK-1", "quantity": 3}
    ]


def test_flow_manual_match_rejects_replacement_without_lines(client_admin):
    from apps.inventory.models import PendingScanKind, PendingShipmentScan
    from apps.warehouses.models import Warehouse

    warehouse = Warehouse.objects.create(name="Kho RJ", code="KRJ1")
    scan = PendingShipmentScan.objects.create(
        code="VD-RJ-1", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    res = client_admin.post(
        f"/api/inventory/flow-pending/{scan.id}/match",
        data={"order_id": 1, "replacement": {"lines": [], "note": ""}},
        content_type="application/json",
    )
    assert res.status_code == 400
    assert "ít nhất 1 dòng" in res.json()["detail"]


def test_flow_manual_match_rejects_non_positive_quantity(client_admin):
    from apps.inventory.models import PendingScanKind, PendingShipmentScan
    from apps.warehouses.models import Warehouse

    warehouse = Warehouse.objects.create(name="Kho RJ2", code="KRJ2")
    scan = PendingShipmentScan.objects.create(
        code="VD-RJ-2", kind=PendingScanKind.DISPATCH, warehouse=warehouse
    )
    res = client_admin.post(
        f"/api/inventory/flow-pending/{scan.id}/match",
        data={
            "order_id": 1,
            "replacement": {"lines": [{"product_id": 1, "quantity": 0}], "note": ""},
        },
        content_type="application/json",
    )
    assert res.status_code == 400
    assert "quantity" in res.json()["detail"]
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

`<paths>` = `apps/inventory/tests/test_flow_history_views.py -k "dispatched_flag or replacement or non_positive"`
Kỳ vọng: FAIL — lookup thiếu khoá `already_dispatched`; 2 ca còn lại trả 400 sai thông điệp hoặc không 400.

- [ ] **Step 3: Mở rộng `flow_order_lookup`**

Thay thân hàm `flow_order_lookup` bằng:

```python
def flow_order_lookup(request):
    """Tìm đơn theo mã hóa đơn / mã đơn / mã vận đơn để khớp thủ công.

    Kèm ``already_dispatched`` + ``items`` để panel khớp tay biết có phải hỏi
    "hàng gửi bù?" hay không. Cờ này CHỈ để dựng UI — quyết định thật là ở
    `PendingScanService._create_replacement_issue` (đọc warning của chính lần khớp).
    """
    from apps.inventory.application.services.stock_sales_service import (
        order_ids_with_outstanding_stock,
    )
    from apps.orders.crm_catalog import DISPATCHED_DELIVERY_CODE
    from apps.orders.models import OrderAssessment, OrderItem

    q = (request.query_params.get("q") or "").strip()
    if len(q) < 2:
        return Response({"items": []}, status=status.HTTP_200_OK)
    rows = list(OrderRepository.search_for_lookup(q, limit=20))
    order_ids = [o.id for o in rows]
    # Hai tín hiệu, đúng một cái là đủ — cùng luật với `_is_dispatched` ở luồng quét:
    # đơn không khớp sản phẩm nào xuất kho với lines=[] nên KHÔNG sinh bút toán, chỉ
    # đọc sổ tồn sẽ báo "chưa xuất" vĩnh viễn.
    dispatched_ids = set(order_ids_with_outstanding_stock(order_ids))
    dispatched_ids |= set(
        OrderAssessment.objects.filter(
            order_id__in=order_ids,
            delivery_business_status_code=DISPATCHED_DELIVERY_CODE,
        ).values_list("order_id", flat=True)
    )
    # 1 query gộp cho cả trang kết quả, không N+1.
    items_map: dict[int, list[dict]] = {}
    for row in (
        OrderItem.objects.filter(order_id__in=order_ids, product_id__isnull=False)
        .order_by("id")
        .values("order_id", "product_id", "sku", "quantity")
    ):
        items_map.setdefault(row["order_id"], []).append(
            {
                "product_id": row["product_id"],
                "sku": row["sku"] or "",
                "quantity": row["quantity"],
            }
        )
    items = [
        {
            "id": o.id,
            "order_code": o.order_code or "",
            "invoice_code": o.invoice_code or "",
            "shipping_code": o.shipping_code or "",
            "kiotviet_channel": o.kiotviet_channel.name if o.kiotviet_channel_id else "",
            "name": o.name or "",
            # Chỉ cần đủ để đối chiếu khớp mã, không cần SĐT đầy đủ.
            "phone": mask_keep_last(o.phone or ""),
            "already_dispatched": o.id in dispatched_ids,
            "items": items_map.get(o.id, []),
        }
        for o in rows
    ]
    return Response({"items": items}, status=status.HTTP_200_OK)
```

- [ ] **Step 4: Validate `replacement` ở `flow_manual_match`**

Thêm helper ngay **trên** `flow_manual_match`:

```python
def _parse_replacement(raw):
    """Chuẩn hoá payload hàng gửi bù. Trả (replacement | None, thông điệp lỗi | None).

    Validate ở view để trả 400 SẠCH (chưa đụng gì tới sổ), thay vì để service ném lỗi
    sau khi mã đã được khớp — lúc đó 400 sẽ mâu thuẫn với trạng thái đã thay đổi.
    """
    if not raw:
        return None, None
    if not isinstance(raw, dict):
        return None, "replacement phải là object."
    raw_lines = raw.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        return None, "Hàng gửi bù phải có ít nhất 1 dòng sản phẩm."
    lines = []
    for line in raw_lines:
        if not isinstance(line, dict):
            return None, "Dòng hàng gửi bù không hợp lệ."
        product_id = _int_or_none(line.get("product_id"))
        quantity = _int_or_none(line.get("quantity"))
        if not product_id or quantity is None or quantity <= 0:
            return None, "Dòng hàng gửi bù phải có product_id và quantity > 0."
        lines.append({"product_id": product_id, "quantity": quantity})
    return {"lines": lines, "note": str(raw.get("note") or "")}, None
```

Trong `flow_manual_match`, ngay **sau** khối kiểm tra `order_id`:

```python
    replacement, replacement_error = _parse_replacement(request.data.get("replacement"))
    if replacement_error:
        return Response(
            {"detail": replacement_error}, status=status.HTTP_400_BAD_REQUEST
        )
```

và truyền xuống service:

```python
    result = PendingScanService.manual_match(
        scan_id=scan_id, order_id=order_id, actor=request.user, replacement=replacement
    )
```

- [ ] **Step 5: Cập nhật `@extend_schema` của 2 endpoint**

`flow_manual_match` — request + response:

```python
@extend_schema(
    request=inline_serializer(
        "FlowManualMatchRequest",
        fields={
            "order_id": drf.IntegerField(),
            # {"lines": [{"product_id", "quantity"}], "note"} — đánh dấu HÀNG GỬI BÙ.
            "replacement": drf.JSONField(required=False),
        },
    ),
    responses={
        200: inline_serializer(
            "FlowManualMatchResult",
            fields={
                "ok": drf.BooleanField(),
                "result_ref": drf.CharField(allow_blank=True),
                "note": drf.CharField(allow_blank=True),
                "order_id": drf.IntegerField(),
                "stock_doc_code": drf.CharField(allow_blank=True),
                "replacement_skipped": drf.CharField(allow_blank=True),
            },
        )
    },
)
```

`flow_order_lookup` — tìm `inline_serializer("FlowOrderLookupItem", ...)` trong decorator của hàm này và thêm 2 field:

```python
                "already_dispatched": drf.BooleanField(),
                "items": inline_serializer(
                    "FlowOrderLookupOrderItem",
                    fields={
                        "product_id": drf.IntegerField(),
                        "sku": drf.CharField(allow_blank=True),
                        "quantity": drf.IntegerField(),
                    },
                    many=True,
                ),
```

- [ ] **Step 6: Chạy test để xác nhận PASS**

`<paths>` = `apps/inventory/tests/test_flow_history_views.py`
Kỳ vọng: toàn bộ passed (gồm 3 ca mới).

- [ ] **Step 7: Regen OpenAPI schema**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-replacement-issue && MSYS_NO_PATHCONV=1 docker run --rm \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$PWD/backend:/app" -w /app desi-backend-test:latest \
  python manage.py spectacular --file schema.yaml
cd frontend && npm run gen:api && npx prettier --write src/api/schema.d.ts
```

Kỳ vọng: `schema.yaml` xuất hiện `stock_doc_code`, `replacement_skipped`, `already_dispatched`; `schema.d.ts` diff 2-space (không phải 4-space).

- [ ] **Step 8: Commit**

```bash
git add backend/apps/inventory/transport/views/flow_history_views.py backend/apps/inventory/tests/test_flow_history_views.py backend/schema.yaml frontend/src/api/schema.d.ts
git commit -m "feat(api): accept replacement payload on manual scan match"
```

---

### Task 4: Panel khớp tay — hộp xác nhận hàng gửi bù

**Files:**
- Modify: `frontend/src/features/inventory/flow-match-queries.ts:22-46` (`useFlowManualMatch`)
- Modify: `frontend/src/features/inventory/PendingMatchControl.tsx` (toàn bộ component)
- Test: `frontend/src/features/inventory/__tests__/pending-match-replacement.test.tsx`

**Interfaces:**
- Consumes: `FlowOrderLookupItem.already_dispatched: boolean`, `FlowOrderLookupItem.items: {product_id: number; sku: string; quantity: number}[]`, `FlowManualMatchResult.stock_doc_code: string`, `FlowManualMatchResult.replacement_skipped: string` (Task 3).
- Produces: `useFlowManualMatch().mutate({scanId, orderId, replacement?})` với `replacement?: {lines: {product_id: number; quantity: number}[]; note: string}`.

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/inventory/__tests__/pending-match-replacement.test.tsx`:

```tsx
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// Đơn ĐÃ xuất kho mà quét thêm một mã vận đơn nữa = hàng gửi bù (đổi lỗi 2 chiều).
// Panel phải HỎI trước khi khớp, không được im lặng khớp rồi bỏ sót phiếu xuất kho.

const fetchJson = vi.fn();
vi.mock("@/services/http", () => ({ fetchJson: (...a: unknown[]) => fetchJson(...a) }));

import { PendingMatchPanel } from "../PendingMatchControl";

const DISPATCHED_ORDER = {
  id: 7,
  order_code: "DH012628",
  invoice_code: "HD013124",
  shipping_code: "84859697925080",
  kiotviet_channel: "",
  name: "Khach A",
  phone: "***1111",
  already_dispatched: true,
  items: [{ product_id: 3, sku: "MSC06", quantity: 1 }],
};

const FRESH_ORDER = { ...DISPATCHED_ORDER, id: 8, already_dispatched: false };

function mockLookup(order: Record<string, unknown>) {
  fetchJson.mockImplementation((url: string) => {
    if (url.startsWith("/api/inventory/flow-order-lookup")) {
      return Promise.resolve({ items: [order] });
    }
    return Promise.resolve({ ok: true, stock_doc_code: "XK20260724-0001", replacement_skipped: "" });
  });
}

function renderPanel(onDone = vi.fn()) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  render(<PendingMatchPanel scanId={42} onDone={onDone} />, { wrapper });
  return onDone;
}

async function searchAndPick(label: string) {
  fireEvent.change(screen.getByPlaceholderText(/Nhập mã hóa đơn/i), {
    target: { value: "HD013124" },
  });
  const button = await screen.findByRole("button", { name: new RegExp(label) }, { timeout: 3000 });
  fireEvent.click(button);
}

function matchCalls() {
  return fetchJson.mock.calls.filter((c) => String(c[0]).includes("/match"));
}

beforeEach(() => fetchJson.mockReset());
afterEach(cleanup);

describe("PendingMatchPanel — hàng gửi bù", () => {
  it("đơn chưa xuất: khớp ngay, không hỏi gì", async () => {
    mockLookup(FRESH_ORDER);
    renderPanel();
    await searchAndPick("HD013124");
    await waitFor(() => expect(matchCalls()).toHaveLength(1));
    expect(screen.queryByText(/hàng gửi bù/i)).not.toBeInTheDocument();
  });

  it("đơn đã xuất: hiện hộp xác nhận, CHƯA gọi match", async () => {
    mockLookup(DISPATCHED_ORDER);
    renderPanel();
    await searchAndPick("HD013124");
    expect(await screen.findByText(/hàng gửi bù/i)).toBeInTheDocument();
    expect(matchCalls()).toHaveLength(0);
  });

  it("chọn 'Không' → khớp KHÔNG kèm replacement", async () => {
    mockLookup(DISPATCHED_ORDER);
    renderPanel();
    await searchAndPick("HD013124");
    fireEvent.click(await screen.findByRole("button", { name: /Không — chỉ khớp/i }));
    await waitFor(() => expect(matchCalls()).toHaveLength(1));
    const body = JSON.parse(String((matchCalls()[0][1] as { body: string }).body));
    expect(body.order_id).toBe(7);
    expect(body.replacement).toBeUndefined();
  });

  it("sửa SL rồi chọn 'Có' → payload replacement đúng số đã sửa", async () => {
    mockLookup(DISPATCHED_ORDER);
    renderPanel();
    await searchAndPick("HD013124");
    fireEvent.change(await screen.findByLabelText(/SL MSC06/i), { target: { value: "2" } });
    fireEvent.change(screen.getByPlaceholderText(/Ghi chú/i), { target: { value: "ko vao dien" } });
    fireEvent.click(screen.getByRole("button", { name: /Có — tạo phiếu xuất kho/i }));
    await waitFor(() => expect(matchCalls()).toHaveLength(1));
    const body = JSON.parse(String((matchCalls()[0][1] as { body: string }).body));
    expect(body.replacement).toEqual({
      lines: [{ product_id: 3, quantity: 2 }],
      note: "ko vao dien",
    });
  });

  it("BE bỏ qua tạo phiếu → hiện cảnh báo, không im lặng", async () => {
    fetchJson.mockImplementation((url: string) => {
      if (url.startsWith("/api/inventory/flow-order-lookup")) {
        return Promise.resolve({ items: [DISPATCHED_ORDER] });
      }
      return Promise.resolve({
        ok: true,
        stock_doc_code: "",
        replacement_skipped: "order_was_not_dispatched",
      });
    });
    renderPanel();
    await searchAndPick("HD013124");
    fireEvent.click(await screen.findByRole("button", { name: /Có — tạo phiếu xuất kho/i }));
    expect(await screen.findByText(/chưa từng xuất kho/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-replacement-issue/frontend && npm run test -- src/features/inventory/__tests__/pending-match-replacement.test.tsx
```

Kỳ vọng: FAIL — không tìm thấy text `hàng gửi bù` / nút `Không — chỉ khớp`.

- [ ] **Step 3: Cho mutation nhận `replacement`**

Trong `flow-match-queries.ts`, sửa `useFlowManualMatch`:

```typescript
type ReplacementPayload = {
  lines: { product_id: number; quantity: number }[];
  note: string;
};

export function useFlowManualMatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      scanId,
      orderId,
      replacement,
    }: {
      scanId: number;
      orderId: number;
      replacement?: ReplacementPayload;
    }) =>
      fetchJson(`/api/inventory/flow-pending/${scanId}/match`, {
        method: "POST",
        body: JSON.stringify(
          replacement ? { order_id: orderId, replacement } : { order_id: orderId },
        ),
      }) as Promise<FlowManualMatchResult>,
    onSuccess: () => {
      for (const key of [
        "issue-pending",
        "return-pending",
        "issue-orders",
        "return-history",
        "stock",
        "ledger",
        "returns",
        // Hàng gửi bù sinh Phiếu Xuất kho → danh sách phiếu phải tươi lại.
        "documents",
      ]) {
        void qc.invalidateQueries({ queryKey: ["inventory", key] });
      }
    },
  });
}
```

- [ ] **Step 4: Thêm bước xác nhận vào `PendingMatchPanel`**

Thay toàn bộ `frontend/src/features/inventory/PendingMatchControl.tsx` bằng:

```tsx
import { useEffect, useState } from "react";
import { toast } from "sonner";

import type { FlowOrderLookupItem } from "@/lib/api/inventory-flow";

import { useFlowManualMatch, useFlowOrderLookup } from "./flow-match-queries";

// Panel khớp mã vận đơn chờ khớp: nhập mã hóa đơn → chọn đơn để khớp.
// Render dạng hàng full-width dưới dòng chờ khớp (tránh lệch/che do overflow của bảng).
//
// Đơn ĐÃ xuất kho: guard `already_dispatched` chặn trừ tồn lần 2 (đúng), nên nếu đây là
// hàng gửi bù của đơn đổi lỗi 2 chiều thì cái thay thế phải trừ tồn bằng Phiếu Xuất kho
// riêng. Hỏi ngay tại đây thay vì để nhân viên nhớ sang màn Phiếu kho — đã có ca quên và
// làm báo cáo thiếu 1 (xem docs/superpowers/specs/2026-07-24-…-design.md).

const SKIP_MESSAGES: Record<string, string> = {
  order_was_not_dispatched:
    "Đơn này chưa từng xuất kho — đã xuất kho bình thường, KHÔNG tạo phiếu bù (tránh trừ tồn 2 lần).",
  already_created: "Mã này đã sinh phiếu xuất kho trước đó — không tạo thêm.",
  invalid_lines: "Dòng hàng gửi bù không hợp lệ — không tạo phiếu.",
};

type Line = { product_id: number; sku: string; quantity: number };

export function PendingMatchPanel({ scanId, onDone }: { scanId: number; onDone: () => void }) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Đơn đã xuất đang chờ người dùng trả lời "có phải hàng gửi bù không".
  const [confirming, setConfirming] = useState<FlowOrderLookupItem | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [note, setNote] = useState("");

  const lookup = useFlowOrderLookup(debounced, true);
  const match = useFlowManualMatch();

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  function doMatch(orderId: number, replacement?: { lines: Line[]; note: string }) {
    setError(null);
    match.mutate(
      {
        scanId,
        orderId,
        replacement: replacement
          ? {
              lines: replacement.lines.map((l) => ({
                product_id: l.product_id,
                quantity: l.quantity,
              })),
              note: replacement.note,
            }
          : undefined,
      },
      {
        onSuccess: (res) => {
          if (res.replacement_skipped) {
            setError(SKIP_MESSAGES[res.replacement_skipped] ?? res.replacement_skipped);
            return; // giữ panel mở: người dùng phải đọc được lý do
          }
          if (res.stock_doc_code) {
            toast.success(`Đã tạo phiếu xuất kho ${res.stock_doc_code} — chờ duyệt.`);
          }
          onDone();
        },
        onError: (e) => setError(e instanceof Error ? e.message : "Khớp thất bại."),
      },
    );
  }

  function pick(order: FlowOrderLookupItem) {
    if (!order.already_dispatched) {
      doMatch(order.id);
      return;
    }
    setConfirming(order);
    setLines(order.items.map((it) => ({ ...it })));
    setNote("");
  }

  const items = lookup.data?.items ?? [];
  const orderLabel = confirming
    ? confirming.invoice_code || confirming.order_code || `#${confirming.id}`
    : "";

  if (confirming) {
    return (
      <div className="flex flex-col gap-2 py-1">
        <div className="text-sm font-medium">
          Đơn {orderLabel} đã xuất kho trước đó. Đây có phải <b>hàng gửi bù</b> (đổi lỗi 2
          chiều) không?
        </div>
        <div className="text-xs text-amber-700">
          Chỉ chọn &quot;Có&quot; khi kho đã gửi <b>thêm</b> một kiện nữa. Nếu chỉ là quét
          trùng cùng một kiện, chọn &quot;Không&quot;.
        </div>
        <div className="flex flex-col gap-1">
          {lines.map((l, i) => (
            <div key={l.product_id} className="flex items-center gap-2 text-xs">
              <span className="w-24 font-medium">{l.sku}</span>
              <label className="text-muted-foreground" htmlFor={`repl-qty-${l.product_id}`}>
                SL {l.sku}
              </label>
              <input
                id={`repl-qty-${l.product_id}`}
                type="number"
                min={0}
                value={l.quantity}
                onChange={(e) =>
                  setLines((prev) =>
                    prev.map((row, idx) =>
                      idx === i ? { ...row, quantity: Number(e.target.value) } : row,
                    ),
                  )
                }
                className="h-7 w-20 rounded border border-input bg-background px-2"
              />
              <button
                className="rounded border border-border px-2 py-0.5 hover:bg-muted/50"
                onClick={() => setLines((prev) => prev.filter((_, idx) => idx !== i))}
              >
                Xoá
              </button>
            </div>
          ))}
        </div>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Ghi chú (tuỳ chọn)"
          className="h-8 w-96 rounded border border-input bg-background px-2 text-sm"
        />
        {error && <div className="text-xs text-red-600">{error}</div>}
        <div className="flex items-center gap-2">
          <button
            className="rounded border border-border px-2 py-1 text-xs hover:bg-muted/50"
            disabled={match.isPending}
            onClick={() => doMatch(confirming.id)}
          >
            Không — chỉ khớp
          </button>
          <button
            className="rounded bg-sky-600 px-2 py-1 text-xs text-white hover:bg-sky-700 disabled:opacity-50"
            disabled={match.isPending || lines.every((l) => l.quantity <= 0)}
            onClick={() =>
              doMatch(confirming.id, {
                lines: lines.filter((l) => l.quantity > 0),
                note,
              })
            }
          >
            Có — tạo phiếu xuất kho
          </button>
          <button
            className="rounded border border-border px-2 py-1 text-xs hover:bg-muted/50"
            onClick={() => setConfirming(null)}
          >
            Quay lại
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 py-1">
      <div className="flex items-center gap-2">
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Nhập mã hóa đơn để tìm đơn…"
          className="h-8 w-64 rounded border border-input bg-background px-2 text-sm focus:outline-hidden focus:ring-2 focus:ring-ring/50"
        />
        <button
          className="rounded border border-border px-2 py-1 text-xs hover:bg-muted/50"
          onClick={onDone}
        >
          Hủy
        </button>
        {match.isPending && <span className="text-xs text-muted-foreground">Đang khớp…</span>}
      </div>
      {error && <div className="text-xs text-red-600">{error}</div>}
      <div className="max-h-56 overflow-auto rounded-md border border-border">
        {debounced.trim().length < 2 ? (
          <div className="px-3 py-2 text-xs text-muted-foreground">
            Nhập ≥ 2 ký tự để tìm đơn theo mã hóa đơn.
          </div>
        ) : lookup.isLoading ? (
          <div className="px-3 py-2 text-xs text-muted-foreground">Đang tìm…</div>
        ) : items.length === 0 ? (
          <div className="px-3 py-2 text-xs text-muted-foreground">Không tìm thấy đơn.</div>
        ) : (
          items.map((o) => (
            <button
              key={o.id}
              disabled={match.isPending}
              onClick={() => pick(o)}
              className="flex w-full items-center gap-2 border-t border-border/60 px-3 py-1.5 text-left text-xs first:border-t-0 hover:bg-muted/60 disabled:opacity-50"
            >
              <span className="font-medium">{o.invoice_code || o.order_code || `#${o.id}`}</span>
              {o.already_dispatched ? (
                <span className="rounded bg-amber-100 px-1 py-0.5 text-[10px] text-amber-700">
                  đã xuất kho
                </span>
              ) : null}
              {o.kiotviet_channel ? (
                <span className="rounded bg-sky-100 px-1 py-0.5 text-[10px] text-sky-700">
                  {o.kiotviet_channel}
                </span>
              ) : null}
              {o.name ? <span className="text-muted-foreground">· {o.name}</span> : null}
              {o.shipping_code ? (
                <span className="text-muted-foreground">· {o.shipping_code}</span>
              ) : null}
            </button>
          ))
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Chạy test để xác nhận PASS**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-replacement-issue/frontend && npm run test -- src/features/inventory/__tests__/pending-match-replacement.test.tsx
```

Kỳ vọng: `5 passed`.

- [ ] **Step 6: Typecheck + lint + hồi quy FE**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-replacement-issue/frontend && npm run typecheck && npm run lint:strict && npm run test
```

Kỳ vọng: typecheck sạch, 0 warning, toàn bộ vitest passed.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/inventory/PendingMatchControl.tsx frontend/src/features/inventory/flow-match-queries.ts frontend/src/features/inventory/__tests__/pending-match-replacement.test.tsx
git commit -m "feat(inventory): confirm replacement shipment when matching a dispatched order"
```

---

### Task 5: Hồi quy toàn suite, ghi tiến độ, MR

**Files:**
- Modify: `docs/superpowers/specs/2026-07-24-replacement-issue-on-manual-match-design.md` (thêm mục tiến độ)

- [ ] **Step 1: Chạy toàn bộ suite inventory + orders**

`<paths>` = `apps/inventory apps/orders`
Kỳ vọng: toàn bộ passed. Nếu có test đỏ, sửa **nguyên nhân gốc**, không sửa test cho qua.

- [ ] **Step 2: Đồng bộ master**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-replacement-issue && git fetch origin && git merge origin/master
```

Có conflict thì resolve, chạy lại Step 1.

- [ ] **Step 3: Ghi tiến độ vào spec**

Thêm mục cuối file spec:

```markdown
## 12. Tiến độ thực hiện (2026-07-24)

- **Task 1 — XONG.** `StockDocument.occurred_at` (migration 0029) + `create_document` /
  `_apply_line` truyền xuống bút toán. Phiếu tay (`occurred_at=None`) không hồi quy.
- **Task 2 — XONG.** `_complete_dispatch` trả warning thô, `_dispatch_and_finalize` mang
  ra ngoài, `manual_match(replacement=…)` + `_create_replacement_issue` với 4 guard.
- **Task 3 — XONG.** `flow_manual_match` validate `replacement` (400 sạch ở view),
  `flow_order_lookup` trả `already_dispatched` + `items`. Regen schema.
- **Task 4 — XONG.** `PendingMatchPanel` hộp xác nhận hàng gửi bù.
- **Còn lại:** smoke tay trên dữ liệu thật + tạo phiếu bù thủ công cho ca `HD013124` đã lỡ (§11).
```

- [ ] **Step 4: Commit + push**

```bash
git add docs/superpowers/specs/2026-07-24-replacement-issue-on-manual-match-design.md
git commit -m "docs: record implementation progress"
git push
```

- [ ] **Step 5: Smoke tay + gửi MR**

Kiểm tra trên môi trường thật đúng ca trong spec: mở Lịch sử nhập xuất → dòng mã chờ khớp → Khớp → chọn một đơn **đã xuất kho** → xác nhận hộp "hàng gửi bù" → kiểm tra phiếu `XK…` xuất hiện ở màn Phiếu kho với trạng thái **chờ duyệt**, note mang mã đơn + mã VĐ. Duyệt phiếu → tile `PHIẾU XUẤT KHO` tăng 1, tổng SL xuất tăng 1, dòng phiếu rơi đúng **ngày quét**.

Gửi MR: https://gitlab.com/desi3512912/desi-order/-/merge_requests/new?merge_request%5Bsource_branch%5D=feat%2Freplacement-issue-on-manual-match

---

## Ghi chú lệch so với spec

Spec §5.2 guard 4 viết "`lines` rỗng hoặc `quantity <= 0` → `ValueError` → 400". Plan chuyển
validate lên **view** (`_parse_replacement`, Task 3 Step 4) để trả 400 khi **chưa đụng gì tới
sổ** — nếu để service ném lỗi thì lúc đó mã đã được khớp xong, 400 sẽ mâu thuẫn với trạng
thái đã thay đổi. Service giữ một tầng phòng thủ, trả `replacement_skipped="invalid_lines"`
thay vì ném.
