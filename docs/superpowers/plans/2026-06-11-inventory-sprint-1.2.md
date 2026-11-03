# Inventory Sprint 1.2 Implementation Plan — Stock Documents (Receipt / Issue / Adjustment)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Phiếu kho `StockDocument` + lines, với hành vi **post** (sinh movement qua `record_movement`) và **cancel** (đảo movement). Loại: receipt (nhập), issue (xuất), adjustment (kiểm kê). Transfer ở Sprint 1.3.

**Architecture:** Document là draft → posted → cancelled. Post sinh movement immutable + cập nhật `StockItem` (qua `StockCommandService.record_movement` đã có). Cancel sinh movement đảo (query movement của doc rồi negate). Tất cả atomic.

**Spec:** `docs/erp-inventory.md` §3.3, §4.3, §6, §7. Nền: Sprint 1.1 (app `apps/inventory` đã có models/services/transport, `record_movement(*, warehouse_id, product_id, qty_delta, movement_type, reference_type=None, reference_id=None, note="", user_id=None)`).

**Test BE** (docker — như Sprint 1.1 plan). **FE** từ `frontend/`.

---

## Task 1: Models `StockDocument` + `StockDocumentLine` + section + migration

**Files:** edit `backend/apps/inventory/models.py`; edit `backend/apps/users/models.py`; migration.

- [ ] **Step 1: Thêm models vào `backend/apps/inventory/models.py`** (cuối file)
```python
class DocType(models.TextChoices):
    RECEIPT = "receipt", "Nhập kho"
    ISSUE = "issue", "Xuất kho"
    ADJUSTMENT = "adjustment", "Điều chỉnh"
    TRANSFER = "transfer", "Chuyển kho"


class DocStatus(models.TextChoices):
    DRAFT = "draft", "Nháp"
    POSTED = "posted", "Đã ghi sổ"
    CANCELLED = "cancelled", "Đã huỷ"


class StockDocument(models.Model):
    code = models.CharField(max_length=32, unique=True)
    doc_type = models.CharField(max_length=20, choices=DocType.choices)
    source_warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT,
        related_name="source_documents", null=True, blank=True,
    )
    dest_warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT,
        related_name="dest_documents", null=True, blank=True,
    )
    status = models.CharField(max_length=20, choices=DocStatus.choices, default=DocStatus.DRAFT)
    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "stock_documents"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["doc_type", "status"], name="stockdoc_type_status_idx"),
            models.Index(fields=["created_at"], name="stockdoc_created_idx"),
        ]

    def __str__(self):
        return f"{self.code} ({self.doc_type}/{self.status})"


class StockDocumentLine(models.Model):
    document = models.ForeignKey(
        StockDocument, on_delete=models.CASCADE, related_name="lines"
    )
    product = models.ForeignKey(
        "orders.Product", on_delete=models.PROTECT, related_name="stock_doc_lines"
    )
    quantity = models.PositiveIntegerField()

    class Meta:
        db_table = "stock_document_lines"
        constraints = [
            models.UniqueConstraint(
                fields=["document", "product"], name="uniq_stock_doc_line"
            )
        ]

    def __str__(self):
        return f"doc{self.document_id} p{self.product_id} x{self.quantity}"
```

- [ ] **Step 2: Đăng ký section** — trong `backend/apps/users/models.py` `VALID_SECTIONS`, sau `"stock",` thêm `    "stock-documents",`.

- [ ] **Step 3: Migration**
```bash
cd /home/desi/desi-order
docker inspect desi-web -f '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^[A-Z_]+=' | grep -vE '^PATH=|^LANG=|^GPG|^PYTHON' > /tmp/web.env
docker run --rm --network desi-order_default --user root --env-file /tmp/web.env -v "$(pwd)/backend:/app" -w /app desi-order-web python manage.py makemigrations inventory
```
Đọc migration sinh ra (StockDocument + StockDocumentLine). Kiểm `migrate inventory --plan`.

- [ ] **Step 4: Ruff + commit**
```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/models.py
git add backend/apps/inventory/models.py backend/apps/inventory/migrations backend/apps/users/models.py
git commit -m "feat(inventory): add StockDocument models and stock-documents section"
```

---

## Task 2: `StockDocumentService` — create / post / cancel (TDD)

**Files:** create `backend/apps/inventory/application/services/stock_document_service.py`; test `backend/apps/inventory/tests/test_stock_document_service.py`.

- [ ] **Step 1: Viết test thất bại**
```python
from __future__ import annotations

import pytest
from django.test import TestCase

from apps.inventory.application.services.stock_document_service import StockDocumentService
from apps.inventory.models import DocStatus, DocType, StockItem, StockMovement
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


class StockDocumentServiceTests(TestCase):
    def setUp(self):
        self.w1 = Warehouse.objects.create(code="W1", name="Kho 1")
        self.p1 = Product.objects.create(name="SP A", sku="A-1")
        self.p2 = Product.objects.create(name="SP B", sku="B-1")

    def _create(self, doc_type, lines, source=None, dest=None):
        return StockDocumentService.create_document(
            doc_type=doc_type,
            source_warehouse_id=source,
            dest_warehouse_id=dest,
            lines=lines,
        )

    def test_create_generates_code_and_draft(self):
        doc = self._create(DocType.RECEIPT, [{"product_id": self.p1.id, "quantity": 5}], dest=self.w1.id)
        assert doc.status == DocStatus.DRAFT
        assert doc.code.startswith("NK")
        assert doc.lines.count() == 1

    def test_post_receipt_increases_stock(self):
        doc = self._create(DocType.RECEIPT,
                           [{"product_id": self.p1.id, "quantity": 5},
                            {"product_id": self.p2.id, "quantity": 3}], dest=self.w1.id)
        StockDocumentService.post_document(document_id=doc.id)
        doc.refresh_from_db()
        assert doc.status == DocStatus.POSTED
        assert StockItem.objects.get(warehouse=self.w1, product=self.p1).quantity == 5
        assert StockItem.objects.get(warehouse=self.w1, product=self.p2).quantity == 3

    def test_post_issue_decreases_stock(self):
        recv = self._create(DocType.RECEIPT, [{"product_id": self.p1.id, "quantity": 10}], dest=self.w1.id)
        StockDocumentService.post_document(document_id=recv.id)
        issue = self._create(DocType.ISSUE, [{"product_id": self.p1.id, "quantity": 4}], source=self.w1.id)
        StockDocumentService.post_document(document_id=issue.id)
        assert StockItem.objects.get(warehouse=self.w1, product=self.p1).quantity == 6

    def test_post_adjustment_sets_balance_to_counted(self):
        recv = self._create(DocType.RECEIPT, [{"product_id": self.p1.id, "quantity": 10}], dest=self.w1.id)
        StockDocumentService.post_document(document_id=recv.id)
        adj = self._create(DocType.ADJUSTMENT, [{"product_id": self.p1.id, "quantity": 7}], dest=self.w1.id)
        StockDocumentService.post_document(document_id=adj.id)
        assert StockItem.objects.get(warehouse=self.w1, product=self.p1).quantity == 7
        # adjustment delta = 7 - 10 = -3
        adj_mv = StockMovement.objects.filter(movement_type="adjustment").latest("id")
        assert adj_mv.qty_delta == -3

    def test_cannot_post_twice(self):
        doc = self._create(DocType.RECEIPT, [{"product_id": self.p1.id, "quantity": 5}], dest=self.w1.id)
        StockDocumentService.post_document(document_id=doc.id)
        with pytest.raises(ValueError):
            StockDocumentService.post_document(document_id=doc.id)

    def test_cancel_reverses_stock(self):
        doc = self._create(DocType.RECEIPT, [{"product_id": self.p1.id, "quantity": 5}], dest=self.w1.id)
        StockDocumentService.post_document(document_id=doc.id)
        StockDocumentService.cancel_document(document_id=doc.id)
        doc.refresh_from_db()
        assert doc.status == DocStatus.CANCELLED
        assert StockItem.objects.get(warehouse=self.w1, product=self.p1).quantity == 0
```

- [ ] **Step 2: Chạy → FAIL** (`apps/inventory/tests/test_stock_document_service.py`).

- [ ] **Step 3: Viết service**

`backend/apps/inventory/application/services/stock_document_service.py`:
```python
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import (
    DocStatus,
    DocType,
    MovementType,
    StockDocument,
    StockDocumentLine,
    StockItem,
    StockMovement,
)

_CODE_PREFIX = {
    DocType.RECEIPT: "NK",
    DocType.ISSUE: "XK",
    DocType.ADJUSTMENT: "DC",
    DocType.TRANSFER: "CK",
}
_REFERENCE_TYPE = "document"


class StockDocumentService:
    @staticmethod
    def _generate_code(doc_type: str) -> str:
        prefix = _CODE_PREFIX.get(doc_type, "PK")
        today = timezone.now().strftime("%Y%m%d")
        same_day = StockDocument.objects.filter(code__startswith=f"{prefix}{today}").count()
        return f"{prefix}{today}-{same_day + 1:04d}"

    @staticmethod
    @transaction.atomic
    def create_document(
        *,
        doc_type: str,
        lines: list[dict],
        source_warehouse_id: int | None = None,
        dest_warehouse_id: int | None = None,
        note: str = "",
        user_id: int | None = None,
    ) -> StockDocument:
        doc = StockDocument.objects.create(
            code=StockDocumentService._generate_code(doc_type),
            doc_type=doc_type,
            source_warehouse_id=source_warehouse_id,
            dest_warehouse_id=dest_warehouse_id,
            note=note,
            created_by_id=user_id,
        )
        StockDocumentLine.objects.bulk_create(
            [
                StockDocumentLine(
                    document=doc, product_id=line["product_id"], quantity=line["quantity"]
                )
                for line in lines
            ]
        )
        return doc

    @staticmethod
    @transaction.atomic
    def post_document(*, document_id: int, user_id: int | None = None) -> StockDocument:
        doc = StockDocument.objects.select_for_update().get(id=document_id)
        if doc.status != DocStatus.DRAFT:
            raise ValueError(f"Chỉ phiếu nháp mới ghi sổ được (đang {doc.status}).")

        for line in doc.lines.select_related("product").all():
            StockDocumentService._apply_line(doc, line, user_id)

        doc.status = DocStatus.POSTED
        doc.posted_at = timezone.now()
        doc.save(update_fields=["status", "posted_at"])
        return doc

    @staticmethod
    def _apply_line(doc: StockDocument, line: StockDocumentLine, user_id: int | None) -> None:
        ref = dict(reference_type=_REFERENCE_TYPE, reference_id=doc.id, user_id=user_id)
        if doc.doc_type == DocType.RECEIPT:
            StockCommandService.record_movement(
                warehouse_id=doc.dest_warehouse_id, product_id=line.product_id,
                qty_delta=line.quantity, movement_type=MovementType.RECEIPT, **ref)
        elif doc.doc_type == DocType.ISSUE:
            StockCommandService.record_movement(
                warehouse_id=doc.source_warehouse_id, product_id=line.product_id,
                qty_delta=-line.quantity, movement_type=MovementType.ISSUE, **ref)
        elif doc.doc_type == DocType.ADJUSTMENT:
            current = StockItem.objects.filter(
                warehouse_id=doc.dest_warehouse_id, product_id=line.product_id
            ).values_list("quantity", flat=True).first() or 0
            delta = line.quantity - current
            if delta != 0:
                StockCommandService.record_movement(
                    warehouse_id=doc.dest_warehouse_id, product_id=line.product_id,
                    qty_delta=delta, movement_type=MovementType.ADJUSTMENT, **ref)
        else:
            raise ValueError(f"doc_type {doc.doc_type} chưa hỗ trợ ở post (transfer ở 1.3).")

    @staticmethod
    @transaction.atomic
    def cancel_document(*, document_id: int, user_id: int | None = None) -> StockDocument:
        doc = StockDocument.objects.select_for_update().get(id=document_id)
        if doc.status != DocStatus.POSTED:
            raise ValueError(f"Chỉ phiếu đã ghi sổ mới huỷ được (đang {doc.status}).")

        movements = StockMovement.objects.filter(
            reference_type=_REFERENCE_TYPE, reference_id=doc.id
        )
        for mv in movements:
            StockCommandService.record_movement(
                warehouse_id=mv.warehouse_id, product_id=mv.product_id,
                qty_delta=-mv.qty_delta, movement_type=mv.movement_type,
                reference_type=_REFERENCE_TYPE, reference_id=doc.id,
                note=f"Huỷ phiếu {doc.code}", user_id=user_id)

        doc.status = DocStatus.CANCELLED
        doc.save(update_fields=["status"])
        return doc
```

- [ ] **Step 4: Chạy → PASS** (6 tests).

- [ ] **Step 5: Ruff + commit**
```bash
uv run --no-project --with ruff ruff check backend/apps/inventory/application/services/stock_document_service.py backend/apps/inventory/tests/test_stock_document_service.py
git add backend/apps/inventory/application/services/stock_document_service.py backend/apps/inventory/tests/test_stock_document_service.py
git commit -m "feat(inventory): add stock document post/cancel service"
```

---

## Task 3: API phiếu (TDD)

**Files:** create `backend/apps/inventory/transport/serializers/document_serializers.py`, `backend/apps/inventory/transport/views/document_views.py`; edit `backend/apps/inventory/urls.py`; test `backend/apps/inventory/tests/test_document_api.py`.

> Theo ĐÚNG pattern `stock_views.py`/`stock_serializers.py` của Sprint 1.1 (read chúng trước).

- [ ] **Step 1: Serializers** — `document_serializers.py`:
```python
from __future__ import annotations

from rest_framework import serializers

from apps.inventory.models import StockDocument, StockDocumentLine


class StockDocumentLineSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockDocumentLine
        fields = ["id", "product", "product_sku", "product_name", "quantity"]


class StockDocumentSerializer(serializers.ModelSerializer):
    lines = StockDocumentLineSerializer(many=True, read_only=True)
    source_warehouse_name = serializers.CharField(source="source_warehouse.name", read_only=True, default=None)
    dest_warehouse_name = serializers.CharField(source="dest_warehouse.name", read_only=True, default=None)

    class Meta:
        model = StockDocument
        fields = [
            "id", "code", "doc_type", "status",
            "source_warehouse", "source_warehouse_name",
            "dest_warehouse", "dest_warehouse_name",
            "note", "created_at", "posted_at", "lines",
        ]


class _LineInput(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)


class CreateStockDocumentSerializer(serializers.Serializer):
    doc_type = serializers.ChoiceField(choices=["receipt", "issue", "adjustment"])
    source_warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    dest_warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")
    lines = _LineInput(many=True)

    def validate(self, attrs):
        dt = attrs["doc_type"]
        if dt == "issue" and not attrs.get("source_warehouse_id"):
            raise serializers.ValidationError("issue cần source_warehouse_id.")
        if dt in ("receipt", "adjustment") and not attrs.get("dest_warehouse_id"):
            raise serializers.ValidationError(f"{dt} cần dest_warehouse_id.")
        if not attrs.get("lines"):
            raise serializers.ValidationError("Phiếu phải có ít nhất 1 dòng.")
        return attrs
```

- [ ] **Step 2: Views** — `document_views.py`: `list_create_documents` (GET list paginated + POST create draft via `StockDocumentService.create_document`), `document_detail` (GET một phiếu), `post_document_view` (POST → `post_document`), `cancel_document_view` (POST → `cancel_document`). Permission `require_section("stock-documents")`. Bắt `ValueError` → 400, `StockDocument.DoesNotExist` → 404. GET list dùng `parse_required_pagination` + filter `doc_type`/`status`. Mỗi view trả `StockDocumentSerializer`. Lấy `user_id=request.user.id`. (Theo pattern `stock_views.py`.)

- [ ] **Step 3: URLs** — thêm vào `backend/apps/inventory/urls.py`:
```python
    path("documents", list_create_documents, name="inventory-documents"),
    path("documents/<int:document_id>", document_detail, name="inventory-document-detail"),
    path("documents/<int:document_id>/post", post_document_view, name="inventory-document-post"),
    path("documents/<int:document_id>/cancel", cancel_document_view, name="inventory-document-cancel"),
```
(import các view; giữ 2 route stock cũ.)

- [ ] **Step 4: Test API** — `test_document_api.py`: tạo user admin (như `test_stock_api.py`), POST tạo phiếu receipt → 201 + status draft; POST `/post` → 200 + tồn tăng (GET `/inventory/stock`); POST `/cancel` → tồn về 0. Validate issue thiếu source → 400.

- [ ] **Step 5: Chạy toàn bộ `apps/inventory` → PASS.** Ruff. Commit `feat(inventory): add stock document API`.

---

## Task 4: FE — section `stock-documents` + trang Phiếu kho

**Files:** edit `frontend/src/lib/routes.ts`, `frontend/src/app/modules.ts`, `frontend/src/app/routes.tsx`, `frontend/src/app/routed-pages.tsx`, `frontend/src/i18n/messages/topnav.ts`, `frontend/src/app/__tests__/modules.test.ts`, `frontend/src/lib/routes.test.ts`; create `frontend/src/features/inventory/documents-types.ts`, `documents-queries.ts`, `pages/StockDocumentsPage.tsx`.

- [ ] **Step 1: Section** — `lib/routes.ts`: thêm `"stock-documents"` vào `AppSection` + `"stock-documents": "/inventory/stock-documents"` vào `SECTION_TO_PATH`. `modules.ts`: thêm `{ key: "stock-documents", labelKey: "topnav.tabs.stockDocuments", icon: ClipboardList }` vào module `inventory` (sau `stock`); import `ClipboardList`. i18n: `topnav.tabs.stockDocuments` vi "Phiếu kho" / en "Stock documents".

- [ ] **Step 2: Cập nhật test fixtures** — thêm `"stock-documents"` vào `ALL_SECTIONS` trong `modules.test.ts` và `routes.test.ts` (LEGACY giữ nguyên).

- [ ] **Step 3: types + queries** — `documents-types.ts` (`StockDocumentRow`, `StockDocumentLineRow`, list response), `documents-queries.ts` (`useStockDocuments(filters)`, mutations `useCreateDocument`, `usePostDocument`, `useCancelDocument` qua `fetchJson` POST; invalidate query keys). Theo pattern `queries.ts` Sprint 1.1.

- [ ] **Step 4: StockDocumentsPage** — danh sách phiếu (mã/loại/trạng thái/kho) + nút "Tạo phiếu" mở dialog (chọn loại, kho nguồn/đích theo loại, thêm dòng product+sl), nút Ghi sổ (post)/Huỷ theo trạng thái. Dùng TanStack Query. shadcn/light theme, ≤400 dòng. Lấy danh sách warehouses qua API `/api/warehouses` và products qua API có sẵn (xem `features/orders` hoặc warehouses queries) để chọn trong dialog.

- [ ] **Step 5: Route** — `routed-pages.tsx` thêm `StockDocumentsRoute`; `routes.tsx` thêm `{ path: "inventory/stock-documents", element: <Guard section="stock-documents"><StockDocumentsRoute /></Guard> }`.

- [ ] **Step 6: Verify** — `npx tsc --noEmit` sạch; `npx vitest run src/lib/routes.test.ts src/app/__tests__/modules.test.ts` xanh.

- [ ] **Step 7: Commit** `feat(inventory): add stock documents section and page (FE)`.

---

## Self-Review
- Coverage §3.3 (models Task1), §4.3 adjustment delta (Task2 `_apply_line`), post/cancel (Task2), API (Task3), FE (Task4). Transfer (§3.3 doc_type transfer) HOÃN Sprint 1.3 — `_apply_line` raise nếu gặp transfer (an toàn).
- Type consistency: `create_document(doc_type, lines, source_warehouse_id, dest_warehouse_id)` ↔ serializer input ↔ test. `record_movement` kwargs khớp Sprint 1.1.
- Cancel reverse bằng query movement theo reference (robust mọi doc_type).
- Cần kiểm khi code: pattern `stock_views.py` cho transport; `fetchJson` POST signature; API list warehouses/products cho dialog.
