# Inventory Sprint 1.3 Implementation Plan — Stock Transfer (between warehouses)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Hỗ trợ phiếu **chuyển kho** (`doc_type=transfer`): xuất từ kho nguồn + nhập vào kho đích trong một phiếu. Tái dùng toàn bộ hạ tầng `StockDocument` của Sprint 1.2; chỉ mở rộng logic post + validation + FE dialog.

**Architecture:** transfer là một `doc_type` (không phải section mới). Post sinh **cặp** movement cho mỗi line: `transfer_out`(−) ở `source_warehouse`, `transfer_in`(+) ở `dest_warehouse` — qua `record_movement` đã có. Cancel tự đảo (logic cancel generic của 1.2 query movement theo reference). FE: thêm lựa chọn "Chuyển kho" trong dialog tạo phiếu (hiện cả 2 select kho).

**Spec:** `docs/erp-inventory.md` §3.3. Nền: Sprint 1.2 (`StockDocumentService`, `_apply_line`, API `/inventory/documents`, FE `StockDocumentsPage`).

**Test BE:** docker (như các plan trước). **FE:** từ `frontend/`.

---

## Task 1: Mở rộng `StockDocumentService._apply_line` cho transfer (TDD)

**Files:** edit `backend/apps/inventory/application/services/stock_document_service.py`; test `backend/apps/inventory/tests/test_stock_transfer.py`.

- [ ] **Step 1: Viết test thất bại** — `backend/apps/inventory/tests/test_stock_transfer.py`:
```python
from __future__ import annotations

from django.test import TestCase

from apps.inventory.application.services.stock_document_service import StockDocumentService
from apps.inventory.models import DocType, StockItem
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


class StockTransferTests(TestCase):
    def setUp(self):
        self.src = Warehouse.objects.create(code="W1", name="Kho nguồn")
        self.dst = Warehouse.objects.create(code="W2", name="Kho đích")
        self.p1 = Product.objects.create(name="SP A", sku="A-1")
        # nạp tồn ban đầu cho kho nguồn
        recv = StockDocumentService.create_document(
            doc_type=DocType.RECEIPT,
            lines=[{"product_id": self.p1.id, "quantity": 10}],
            dest_warehouse_id=self.src.id,
        )
        StockDocumentService.post_document(document_id=recv.id)

    def _qty(self, wh):
        item = StockItem.objects.filter(warehouse=wh, product=self.p1).first()
        return item.quantity if item else 0

    def test_post_transfer_moves_stock_between_warehouses(self):
        doc = StockDocumentService.create_document(
            doc_type=DocType.TRANSFER,
            lines=[{"product_id": self.p1.id, "quantity": 4}],
            source_warehouse_id=self.src.id,
            dest_warehouse_id=self.dst.id,
        )
        StockDocumentService.post_document(document_id=doc.id)
        assert self._qty(self.src) == 6
        assert self._qty(self.dst) == 4

    def test_cancel_transfer_restores_stock(self):
        doc = StockDocumentService.create_document(
            doc_type=DocType.TRANSFER,
            lines=[{"product_id": self.p1.id, "quantity": 4}],
            source_warehouse_id=self.src.id,
            dest_warehouse_id=self.dst.id,
        )
        StockDocumentService.post_document(document_id=doc.id)
        StockDocumentService.cancel_document(document_id=doc.id)
        assert self._qty(self.src) == 10
        assert self._qty(self.dst) == 0
```

- [ ] **Step 2: Chạy → FAIL** (`_apply_line` hiện raise cho transfer).
```bash
cd /home/desi/desi-order
docker inspect desi-web -f '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^[A-Z_]+=' | grep -vE '^PATH=|^LANG=|^GPG|^PYTHON' > /tmp/web.env
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" -v "$(pwd)/backend:/app" -w /app desi-order-web sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/inventory/tests/test_stock_transfer.py --reuse-db -q'
```

- [ ] **Step 3: Sửa `_apply_line`** — thay nhánh `else: raise` bằng xử lý transfer:
```python
        elif doc.doc_type == DocType.TRANSFER:
            StockCommandService.record_movement(
                warehouse_id=doc.source_warehouse_id, product_id=line.product_id,
                qty_delta=-line.quantity, movement_type=MovementType.TRANSFER_OUT, **ref)
            StockCommandService.record_movement(
                warehouse_id=doc.dest_warehouse_id, product_id=line.product_id,
                qty_delta=line.quantity, movement_type=MovementType.TRANSFER_IN, **ref)
        else:
            raise ValueError(f"doc_type {doc.doc_type} không hỗ trợ.")
```
(Giữ các nhánh receipt/issue/adjustment như cũ; chỉ thêm nhánh transfer trước `else`.)

- [ ] **Step 4: Chạy → PASS** (2 tests). Chạy lại cả `apps/inventory` để chắc không hồi quy.

- [ ] **Step 5: Ruff + commit**
```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/application/services/stock_document_service.py backend/apps/inventory/tests/test_stock_transfer.py
git add backend/apps/inventory/application/services/stock_document_service.py backend/apps/inventory/tests/test_stock_transfer.py
git commit -m "feat(inventory): support stock transfer documents"
```

---

## Task 2: API validation cho transfer (TDD)

**Files:** edit `backend/apps/inventory/transport/serializers/document_serializers.py`; test bổ sung `backend/apps/inventory/tests/test_document_api.py`.

- [ ] **Step 1: Bổ sung test** vào `test_document_api.py`:
- transfer thiếu source HOẶC dest → 400.
- tạo transfer hợp lệ (source≠dest) → 201; post → tồn chuyển đúng.
- transfer source == dest → 400.

(Viết các test method mới trong class test hiện có; dùng auth admin như các test khác.)

- [ ] **Step 2: Chạy → FAIL** (serializer chưa cho "transfer").

- [ ] **Step 3: Sửa `CreateStockDocumentSerializer`**:
- Thêm `"transfer"` vào `doc_type` choices: `["receipt", "issue", "adjustment", "transfer"]`.
- Trong `validate`: nếu `doc_type == "transfer"`: yêu cầu CẢ `source_warehouse_id` và `dest_warehouse_id`, và `source_warehouse_id != dest_warehouse_id` (raise ValidationError nếu vi phạm).

```python
        if dt == "transfer":
            if not attrs.get("source_warehouse_id") or not attrs.get("dest_warehouse_id"):
                raise serializers.ValidationError("transfer cần cả source và dest warehouse.")
            if attrs["source_warehouse_id"] == attrs["dest_warehouse_id"]:
                raise serializers.ValidationError("Kho nguồn và kho đích phải khác nhau.")
```

- [ ] **Step 4: Chạy toàn bộ `apps/inventory` → PASS.** Ruff + commit `feat(inventory): validate transfer documents in API`.

---

## Task 3: FE — thêm "Chuyển kho" vào dialog tạo phiếu

**Files:** edit `frontend/src/features/inventory/pages/StockDocumentsPage.tsx` (và `documents-types.ts` nếu cần nới `doc_type`).

- [ ] **Step 1: Cho phép doc_type "transfer"** trong dialog:
- Thêm lựa chọn "Chuyển kho" (transfer) vào ô chọn loại phiếu.
- Khi loại = transfer: hiện **cả** ô chọn kho nguồn (source) và kho đích (dest). (receipt: chỉ dest; issue: chỉ source; adjustment: chỉ dest — như hiện tại.)
- Payload gửi gồm cả `source_warehouse_id` và `dest_warehouse_id` khi transfer.
- Nếu `CreateStockDocumentPayload.doc_type` là union literal, thêm `"transfer"`.

- [ ] **Step 2: Hiển thị** — bảng danh sách phiếu nên hiện đúng nhãn loại "Chuyển kho" và cả 2 kho (nguồn → đích) cho phiếu transfer (cột kho có thể hiện `source_warehouse_name → dest_warehouse_name`).

- [ ] **Step 3: Verify** — `cd frontend && npx tsc --noEmit` sạch. (Không có test mới bắt buộc; nếu có test page thì chạy.)

- [ ] **Step 4: Commit**
```bash
cd /home/desi/desi-order
git add frontend/src/features/inventory
git commit -m "feat(inventory): allow stock transfer in documents dialog (FE)"
```

---

## Self-Review
- Coverage: transfer post (Task1 `_apply_line`), validation (Task2 serializer), FE (Task3). Cancel transfer tái dùng logic generic 1.2 (test ở Task1).
- Type consistency: `MovementType.TRANSFER_OUT/TRANSFER_IN` đã định nghĩa ở Sprint 1.1 models. `record_movement` kwargs khớp.
- Không thêm section mới (transfer ⊂ stock-documents) → không phải đụng registry/section/test fixtures.
