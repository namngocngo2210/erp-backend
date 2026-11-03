# Inventory Sprint A — Stock State Dimension

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Thêm chiều **Trạng thái** (`sellable`/`warranty`/`defect`) vào tồn kho: tồn = (kho × SP × state). Bán hàng chỉ trừ `sellable`. Trang Tồn kho hiển thị theo 3 trạng thái.

**Spec:** `docs/erp-inventory-returns.md` §2. Nền: module `inventory` (Phase 1, đã merge master). Chưa chạy prod → migration sạch.

**Test BE** (docker, như các plan trước):
```bash
cd /home/desi/desi-order
docker inspect desi-web -f '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^[A-Z_]+=' | grep -vE '^PATH=|^LANG=|^GPG|^PYTHON' > /tmp/web.env
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" -v "$(pwd)/backend:/app" -w /app desi-order-web sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> --reuse-db -q'
```
Ruff host: `uv run --no-project --with ruff ruff check <files>`. FE: `cd frontend && npx tsc --noEmit`.

---

## Task 1: Model — `StockState` + field `state` + migration

**Files:** edit `backend/apps/inventory/models.py`; migration.

- [ ] **Step 1: Thêm enum + field**

Trong `backend/apps/inventory/models.py`:
- Thêm enum (đặt cạnh `MovementType`):
```python
class StockState(models.TextChoices):
    SELLABLE = "sellable", "Bán được"
    WARRANTY = "warranty", "Bảo hành"
    DEFECT = "defect", "Lỗi"
```
- Bổ sung `MovementType` các giá trị mới:
```python
    RETURN_IN = "return_in", "Nhập trả hàng"
    RECLASS_IN = "reclass_in", "Chuyển trạng thái (vào)"
    RECLASS_OUT = "reclass_out", "Chuyển trạng thái (ra)"
    DISPOSAL = "disposal", "Xuất huỷ/gửi lại"
```
- `StockMovement`: thêm field (sau `movement_type`):
```python
    state = models.CharField(max_length=20, choices=StockState.choices, default=StockState.SELLABLE)
```
- `StockItem`: thêm field (sau `quantity` hoặc trước) + đổi unique constraint:
```python
    state = models.CharField(max_length=20, choices=StockState.choices, default=StockState.SELLABLE)
```
  Đổi `UniqueConstraint(fields=["warehouse", "product"], name="uniq_stock_item_wh_product")`
  → `UniqueConstraint(fields=["warehouse", "product", "state"], name="uniq_stock_item_wh_prod_state")`.

- [ ] **Step 2: Migration + verify**
```bash
docker run ... python manage.py makemigrations inventory
docker run ... sh -c 'mkdir -p /tmp/prom_multiproc && python manage.py migrate inventory --plan'
```
Đọc migration: thêm `state` cho 2 model + đổi unique constraint. (Module chưa prod nên thêm field có default + đổi constraint là an toàn.)

- [ ] **Step 3: Ruff + commit**
```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/models.py
git add backend/apps/inventory/models.py backend/apps/inventory/migrations
git commit -m "feat(inventory): add stock state dimension to models"
```

---

## Task 2: Service — `state` xuyên suốt + sale chỉ trừ sellable (TDD)

**Files:** edit `stock_command_service.py`, `stock_query_service.py`, `stock_sales_service.py`, `stock_document_service.py`, `management/commands/rebuild_stock_items.py`; test mới `tests/test_stock_state.py`.

- [ ] **Step 1: Viết test thất bại** `backend/apps/inventory/tests/test_stock_state.py`:
```python
from __future__ import annotations

from django.test import TestCase

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_query_service import StockQueryService
from apps.inventory.application.services.stock_sales_service import SaleLine, StockSalesService
from apps.inventory.models import MovementType, StockItem, StockState
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


class StockStateTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(code="W1", name="Kho 1")
        self.p = Product.objects.create(name="SP A", sku="A-1")

    def test_record_movement_default_state_sellable(self):
        StockCommandService.record_movement(
            warehouse_id=self.wh.id, product_id=self.p.id, qty_delta=10,
            movement_type=MovementType.RECEIPT)
        item = StockItem.objects.get(warehouse=self.wh, product=self.p, state=StockState.SELLABLE)
        assert item.quantity == 10

    def test_states_are_independent(self):
        StockCommandService.record_movement(warehouse_id=self.wh.id, product_id=self.p.id,
            qty_delta=10, movement_type=MovementType.RECEIPT, state=StockState.SELLABLE)
        StockCommandService.record_movement(warehouse_id=self.wh.id, product_id=self.p.id,
            qty_delta=3, movement_type=MovementType.RETURN_IN, state=StockState.WARRANTY)
        assert StockItem.objects.get(warehouse=self.wh, product=self.p, state="sellable").quantity == 10
        assert StockItem.objects.get(warehouse=self.wh, product=self.p, state="warranty").quantity == 3

    def test_sale_only_deducts_sellable(self):
        StockCommandService.record_movement(warehouse_id=self.wh.id, product_id=self.p.id,
            qty_delta=10, movement_type=MovementType.RECEIPT, state=StockState.SELLABLE)
        StockCommandService.record_movement(warehouse_id=self.wh.id, product_id=self.p.id,
            qty_delta=5, movement_type=MovementType.RETURN_IN, state=StockState.WARRANTY)
        StockSalesService.issue_for_order(order_id=900, warehouse_id=self.wh.id,
            lines=[SaleLine(product_id=self.p.id, quantity=4)])
        assert StockItem.objects.get(warehouse=self.wh, product=self.p, state="sellable").quantity == 6
        assert StockItem.objects.get(warehouse=self.wh, product=self.p, state="warranty").quantity == 5

    def test_query_filter_by_state(self):
        StockCommandService.record_movement(warehouse_id=self.wh.id, product_id=self.p.id,
            qty_delta=10, movement_type=MovementType.RECEIPT, state=StockState.SELLABLE)
        StockCommandService.record_movement(warehouse_id=self.wh.id, product_id=self.p.id,
            qty_delta=3, movement_type=MovementType.RETURN_IN, state=StockState.WARRANTY)
        assert StockQueryService.list_stock(state="warranty").count() == 1
        assert StockQueryService.list_stock().count() == 2
```

- [ ] **Step 2: Chạy → FAIL.**

- [ ] **Step 3: Sửa services**
- `stock_command_service.py` `record_movement`: thêm param `state: str = StockState.SELLABLE` (import StockState). get_or_create + `select_for_update().get(...)` theo `(warehouse_id, product_id, state)`. `StockMovement.objects.create(..., state=state)`.
- `stock_query_service.py` `list_stock`: thêm param `state: str | None = None`; nếu có → `.filter(state=state)`. (movements query có thể thêm state sau, không bắt buộc.)
- `stock_sales_service.py` `issue_for_order`: khi gọi `record_movement(...)` thêm `state=StockState.SELLABLE` (import StockState). (release_for_order reverse theo movement gốc — nên reverse cũng vào đúng state của movement đó: khi tạo reversal, truyền `state=mv.state`.)
- `stock_document_service.py` `_apply_line`: với adjustment, đọc số dư hiện tại phải lọc `state=StockState.SELLABLE`:
  `current = StockItem.objects.filter(warehouse_id=..., product_id=..., state=StockState.SELLABLE).values_list("quantity", flat=True).first() or 0`.
  (receipt/issue/transfer dùng default sellable của record_movement → không cần truyền state.)
- `rebuild_stock_items.py`: group `("warehouse_id", "product_id", "state")`, `update_or_create(warehouse_id=, product_id=, state=, defaults={quantity})`.

- [ ] **Step 4: Chạy `test_stock_state.py` → PASS; chạy CẢ `apps/inventory` để không hồi quy** (các test cũ dùng `StockItem.objects.get(warehouse, product)` vẫn ok vì chỉ có 1 row sellable; nếu test nào vỡ do thiếu state, thêm `state="sellable"` vào assertion đó).

- [ ] **Step 5: Ruff + commit** `feat(inventory): thread stock state through services; sales deduct sellable only`.

---

## Task 3: API — serializer + query state

**Files:** edit `transport/serializers/stock_serializers.py`, `transport/views/stock_views.py`; test bổ sung `tests/test_stock_api.py`.

- [ ] **Step 1:** `StockItemSerializer` + `StockMovementSerializer`: thêm `"state"` vào `fields`.
- [ ] **Step 2:** `list_stock` view: đọc query `?state=` và truyền vào `StockQueryService.list_stock(state=...)`.
- [ ] **Step 3:** Test API: seed sellable + warranty cho 1 SP; GET `/api/inventory/stock` → 2 items có field `state`; GET `?state=warranty` → 1 item.
- [ ] **Step 4:** Chạy `apps/inventory` → PASS. Ruff + commit `feat(inventory): expose stock state in API`.

---

## Task 4: FE — Trang Tồn kho theo 3 trạng thái (pivot)

**Files:** edit `frontend/src/features/inventory/types.ts`, `queries.ts`, `pages/StockPage.tsx`.

- [ ] **Step 1:** `types.ts` `StockRow`: thêm `state: "sellable" | "warranty" | "defect"`.
- [ ] **Step 2:** `StockPage.tsx`: gộp (pivot) các row theo `(warehouse, product)` thành 1 dòng với 4 cột số: **Bán được / Bảo hành / Lỗi / Tổng**. (Gom `data.items` theo key `${warehouse}-${product}`, cộng quantity vào cột theo `state`.) Giữ ô tìm SKU/tên. Tô đỏ nếu cột nào < 0. Cột Bảo hành/Lỗi > 0 thì nổi bật nhẹ (badge màu).
- [ ] **Step 3:** `npx tsc --noEmit` sạch. Commit `feat(inventory): show stock by state (sellable/warranty/defect) on stock page`.

---

## Self-Review
- Coverage §2: model state (T1), service+sale-sellable (T2), API (T3), FE pivot (T4).
- Rủi ro R-A2 (sale phải trừ sellable): test `test_sale_only_deducts_sellable` (T2) bắt buộc.
- Migration chưa prod → đổi unique key an toàn.
- Backward: test cũ dùng `.get(warehouse, product)` vẫn chạy (1 row sellable); chỉ sửa nếu thực sự vỡ.
