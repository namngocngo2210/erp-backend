# Inventory Sprint C — Resolution (xử lý tồn bảo hành/lỗi)

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps `- [ ]`.

**Goal:** Xử lý tồn ở trạng thái `warranty`/`defect`: **chuyển về bán được** (sửa xong), **gửi lại khách** (bảo hành), hoặc **huỷ** (lỗi không sửa) — đều ghi sổ immutable.

**Spec:** `docs/erp-inventory-returns.md` §4. Nền: Sprint A (state + `record_movement(..., state=)`, MovementType `RECLASS_IN/RECLASS_OUT/DISPOSAL`, `StockState`).

**Test BE** (docker — như các plan trước). FE từ `frontend/`.

---

## Task 1: `StockReclassService` + API (TDD)

**Files:** create `application/services/stock_reclass_service.py`; edit `transport/views/stock_views.py` + `transport/serializers/stock_serializers.py` (thêm input serializer) + `urls.py`; test `tests/test_stock_reclass.py` + bổ sung `tests/test_stock_api.py`.

- [ ] **Step 1: Test thất bại** `tests/test_stock_reclass.py`:
```python
from __future__ import annotations

import pytest
from django.test import TestCase

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.application.services.stock_reclass_service import StockReclassService
from apps.inventory.models import MovementType, StockItem, StockState
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


class StockReclassTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(code="W1", name="Kho 1")
        self.p = Product.objects.create(name="SP A", sku="A-1")
        # nạp 5 vào warranty + 4 vào defect
        StockCommandService.record_movement(warehouse_id=self.wh.id, product_id=self.p.id,
            qty_delta=5, movement_type=MovementType.RETURN_IN, state=StockState.WARRANTY)
        StockCommandService.record_movement(warehouse_id=self.wh.id, product_id=self.p.id,
            qty_delta=4, movement_type=MovementType.RETURN_IN, state=StockState.DEFECT)

    def _qty(self, state):
        item = StockItem.objects.filter(warehouse=self.wh, product=self.p, state=state).first()
        return item.quantity if item else 0

    def test_to_sellable_moves_warranty_to_sellable(self):
        StockReclassService.reclassify(warehouse_id=self.wh.id, product_id=self.p.id,
            quantity=3, from_state=StockState.WARRANTY, action="to_sellable")
        assert self._qty("warranty") == 2
        assert self._qty("sellable") == 3

    def test_send_back_removes_from_warranty(self):
        StockReclassService.reclassify(warehouse_id=self.wh.id, product_id=self.p.id,
            quantity=2, from_state=StockState.WARRANTY, action="send_back")
        assert self._qty("warranty") == 3
        assert self._qty("sellable") == 0  # đi khỏi tồn, không vào bán được

    def test_scrap_removes_from_defect(self):
        StockReclassService.reclassify(warehouse_id=self.wh.id, product_id=self.p.id,
            quantity=1, from_state=StockState.DEFECT, action="scrap")
        assert self._qty("defect") == 3

    def test_invalid_quantity_rejected(self):
        with pytest.raises(ValueError):
            StockReclassService.reclassify(warehouse_id=self.wh.id, product_id=self.p.id,
                quantity=0, from_state=StockState.WARRANTY, action="to_sellable")

    def test_invalid_action_rejected(self):
        with pytest.raises(ValueError):
            StockReclassService.reclassify(warehouse_id=self.wh.id, product_id=self.p.id,
                quantity=1, from_state=StockState.WARRANTY, action="nope")
```

- [ ] **Step 2: Chạy → FAIL.**

- [ ] **Step 3: Service** `stock_reclass_service.py`:
```python
from __future__ import annotations

from django.db import transaction

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import MovementType, StockState

_REFERENCE_TYPE = "reclass"
_VALID_ACTIONS = {"to_sellable", "send_back", "scrap"}
_RESOLVABLE_STATES = {StockState.WARRANTY, StockState.DEFECT}


class StockReclassService:
    @staticmethod
    @transaction.atomic
    def reclassify(
        *, warehouse_id: int, product_id: int, quantity: int, from_state: str,
        action: str, note: str = "", user_id: int | None = None,
    ) -> None:
        if quantity <= 0:
            raise ValueError("quantity phải > 0.")
        if action not in _VALID_ACTIONS:
            raise ValueError(f"action không hợp lệ: {action}.")
        if from_state not in _RESOLVABLE_STATES:
            raise ValueError(f"Chỉ xử lý tồn warranty/defect (nhận {from_state}).")

        ref = dict(reference_type=_REFERENCE_TYPE, reference_id=product_id, user_id=user_id)
        if action == "to_sellable":
            StockCommandService.record_movement(
                warehouse_id=warehouse_id, product_id=product_id, qty_delta=-quantity,
                movement_type=MovementType.RECLASS_OUT, state=from_state, note=note, **ref)
            StockCommandService.record_movement(
                warehouse_id=warehouse_id, product_id=product_id, qty_delta=quantity,
                movement_type=MovementType.RECLASS_IN, state=StockState.SELLABLE, note=note, **ref)
        else:  # send_back | scrap → xuất khỏi tồn (disposal)
            reason = note or ("Gửi lại khách" if action == "send_back" else "Huỷ hàng lỗi")
            StockCommandService.record_movement(
                warehouse_id=warehouse_id, product_id=product_id, qty_delta=-quantity,
                movement_type=MovementType.DISPOSAL, state=from_state, note=reason, **ref)
```

- [ ] **Step 4: Chạy → PASS (5).**

- [ ] **Step 5: API** — `stock_serializers.py` thêm:
```python
class ReclassInputSerializer(serializers.Serializer):
    warehouse_id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)
    from_state = serializers.ChoiceField(choices=["warranty", "defect"])
    action = serializers.ChoiceField(choices=["to_sellable", "send_back", "scrap"])
    note = serializers.CharField(required=False, allow_blank=True, default="")
```
`stock_views.py` thêm view `reclassify_stock` (POST, `require_section("stock")`): validate → `StockReclassService.reclassify(..., user_id=request.user.id)` → 200 `{detail:"ok"}`; ValueError→400.
`urls.py` thêm `path("stock/reclassify", reclassify_stock, name="inventory-stock-reclassify")`.

- [ ] **Step 6: Test API** trong `test_stock_api.py`: seed warranty 5; POST `/api/inventory/stock/reclassify` {warehouse_id, product_id, quantity:3, from_state:"warranty", action:"to_sellable"} → 200; GET stock: warranty 2, sellable 3. quantity 0 → 400.

- [ ] **Step 7:** Chạy `apps/inventory` → PASS. Ruff + commit `feat(inventory): add stock reclassification (resolution) service and API`.

---

## Task 2: FE — nút "Xử lý" trên tồn bảo hành/lỗi

**Files:** edit `frontend/src/features/inventory/queries.ts` (mutation), `pages/StockPage.tsx` (+ dialog xử lý).

- [ ] **Step 1: Mutation** trong `queries.ts`: `useReclassifyStock()` POST `/api/inventory/stock/reclassify`; on success invalidate `inventoryKeys.stock`.
- [ ] **Step 2: StockPage** — trên mỗi dòng pivot có `warranty > 0` hoặc `defect > 0`, thêm nút **"Xử lý"** mở dialog:
  - Chọn **trạng thái nguồn** (warranty/defect — chỉ hiện cái > 0).
  - Chọn **hành động**: *Chuyển về bán được* (to_sellable) / *Gửi lại khách* (send_back, chỉ gợi ý cho warranty) / *Huỷ* (scrap, gợi ý cho defect). Cho cả 3, không chặn cứng.
  - Nhập **số lượng** + ghi chú.
  - Submit gọi mutation với `warehouse_id`/`product_id` của dòng đó.
  - Cần `warehouse`/`product` id trên pivot row → đảm bảo `StockRow` (và pivot) giữ `warehouse` + `product` id (đã có trong StockRow). Pivot row phải mang theo warehouse id + product id.
- [ ] **Step 3:** `npx tsc --noEmit` sạch. Commit `feat(inventory): resolve warranty/defect stock from stock page (FE)`.

---

## Self-Review
- Coverage §4: service 3 hành động (T1), API (T1), FE nút Xử lý (T2).
- to_sellable = reclass_out + reclass_in (giữ bảo toàn sổ); send_back/scrap = disposal (xuất khỏi tồn).
- Cho âm + cảnh báo nhất quán Phase 1 (record_movement không chặn; reclass quá tồn sẽ làm state nguồn âm — chấp nhận, hiếm).
- Pivot row phải có warehouse_id + product_id để gọi API.
