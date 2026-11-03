# Inventory Sprint B — Return Notes (Phiếu trả hàng)

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps `- [ ]`.

**Goal:** Phiếu trả hàng cho 4 loại (hàng hoàn / trả ngoài / bảo hành / đổi lỗi), **gắn nguồn gốc** (đơn gốc + khách), post → +tồn vào **đúng trạng thái** (sellable/warranty/defect), cancel → đảo.

**Spec:** `docs/erp-inventory-returns.md` §3. Nền: Sprint A (state dimension + `record_movement(..., state=)` + `MovementType.RETURN_IN`). Pattern tham chiếu: `StockDocument`/`StockDocumentService`/document API/`StockDocumentsPage` (Sprint 1.2).

**Test BE** (docker — như các plan trước). FE từ `frontend/`.

---

## Task 1: Model `ReturnNote` + `ReturnNoteLine` + section + migration

**Files:** edit `backend/apps/inventory/models.py`; `backend/apps/users/models.py`; migration.

- [ ] **Step 1: Models** (cuối `models.py`):
```python
class ReturnType(models.TextChoices):
    DELIVERY_RETURNED = "delivery_returned", "Hàng hoàn"
    EXTERNAL_RETURN = "external_return", "Trả hàng ngoài"
    WARRANTY = "warranty", "Bảo hành"
    DEFECT = "defect", "Đổi lỗi"


class ReturnStatus(models.TextChoices):
    DRAFT = "draft", "Nháp"
    POSTED = "posted", "Đã ghi sổ"
    CANCELLED = "cancelled", "Đã huỷ"


class ReturnNote(models.Model):
    code = models.CharField(max_length=32, unique=True)
    return_type = models.CharField(max_length=20, choices=ReturnType.choices)
    source_order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="return_notes",
    )
    customer_name = models.CharField(max_length=255, blank=True, default="")
    customer_phone = models.CharField(max_length=32, blank=True, default="")
    source_note = models.TextField(blank=True, default="")
    warehouse = models.ForeignKey(
        "warehouses.Warehouse", on_delete=models.PROTECT, related_name="return_notes"
    )
    status = models.CharField(max_length=20, choices=ReturnStatus.choices, default=ReturnStatus.DRAFT)
    note = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "stock_return_notes"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["return_type", "status"], name="returnnote_type_status_idx"),
            models.Index(fields=["source_order"], name="returnnote_order_idx"),
            models.Index(fields=["created_at"], name="returnnote_created_idx"),
        ]

    def __str__(self):
        return f"{self.code} ({self.return_type}/{self.status})"


class ReturnNoteLine(models.Model):
    return_note = models.ForeignKey(ReturnNote, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(
        "orders.Product", on_delete=models.PROTECT, related_name="return_lines"
    )
    quantity = models.PositiveIntegerField()
    condition_note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "stock_return_note_lines"
        constraints = [
            models.UniqueConstraint(fields=["return_note", "product"], name="uniq_return_line")
        ]
```

- [ ] **Step 2: Section** — `VALID_SECTIONS` trong `apps/users/models.py`: thêm `"returns"` (sau `"stock-documents"`).

- [ ] **Step 3: Migration** — makemigrations inventory + users? (`VALID_SECTIONS` là list Python, KHÔNG cần migration). makemigrations inventory cho 2 model mới; verify `--plan`. Đọc migration.

- [ ] **Step 4: Ruff + commit** `feat(inventory): add ReturnNote models and returns section`.

---

## Task 2: `ReturnNoteService` create/post/cancel (TDD)

**Files:** create `application/services/return_note_service.py`; test `tests/test_return_note_service.py`.

- [ ] **Step 1: Test thất bại**:
```python
from __future__ import annotations

import pytest
from django.test import TestCase

from apps.inventory.application.services.return_note_service import ReturnNoteService
from apps.inventory.models import ReturnStatus, ReturnType, StockItem
from apps.orders.models import Product
from apps.warehouses.models import Warehouse


class ReturnNoteServiceTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(code="W1", name="Kho 1")
        self.p = Product.objects.create(name="SP A", sku="A-1")

    def _create(self, return_type):
        return ReturnNoteService.create_return(
            return_type=return_type, warehouse_id=self.wh.id,
            lines=[{"product_id": self.p.id, "quantity": 5}])

    def _qty(self, state):
        item = StockItem.objects.filter(warehouse=self.wh, product=self.p, state=state).first()
        return item.quantity if item else 0

    def test_create_generates_code_draft(self):
        note = self._create(ReturnType.DELIVERY_RETURNED)
        assert note.status == ReturnStatus.DRAFT
        assert note.code.startswith("TH")
        assert note.lines.count() == 1

    def test_post_delivery_returned_adds_sellable(self):
        note = self._create(ReturnType.DELIVERY_RETURNED)
        ReturnNoteService.post_return(return_note_id=note.id)
        assert self._qty("sellable") == 5
        assert self._qty("warranty") == 0

    def test_post_external_return_adds_sellable(self):
        note = self._create(ReturnType.EXTERNAL_RETURN)
        ReturnNoteService.post_return(return_note_id=note.id)
        assert self._qty("sellable") == 5

    def test_post_warranty_adds_warranty_state(self):
        note = self._create(ReturnType.WARRANTY)
        ReturnNoteService.post_return(return_note_id=note.id)
        assert self._qty("warranty") == 5
        assert self._qty("sellable") == 0

    def test_post_defect_adds_defect_state(self):
        note = self._create(ReturnType.DEFECT)
        ReturnNoteService.post_return(return_note_id=note.id)
        assert self._qty("defect") == 5

    def test_cannot_post_twice(self):
        note = self._create(ReturnType.WARRANTY)
        ReturnNoteService.post_return(return_note_id=note.id)
        with pytest.raises(ValueError):
            ReturnNoteService.post_return(return_note_id=note.id)

    def test_cancel_reverses(self):
        note = self._create(ReturnType.WARRANTY)
        ReturnNoteService.post_return(return_note_id=note.id)
        ReturnNoteService.cancel_return(return_note_id=note.id)
        assert self._qty("warranty") == 0
```

- [ ] **Step 2: Chạy → FAIL.**

- [ ] **Step 3: Service** `return_note_service.py`:
```python
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.inventory.application.services.stock_command_service import StockCommandService
from apps.inventory.models import (
    MovementType, ReturnNote, ReturnNoteLine, ReturnStatus, ReturnType, StockMovement, StockState,
)

_REFERENCE_TYPE = "return"
_RETURN_STATE = {
    ReturnType.DELIVERY_RETURNED: StockState.SELLABLE,
    ReturnType.EXTERNAL_RETURN: StockState.SELLABLE,
    ReturnType.WARRANTY: StockState.WARRANTY,
    ReturnType.DEFECT: StockState.DEFECT,
}


class ReturnNoteService:
    @staticmethod
    def _generate_code() -> str:
        today = timezone.now().strftime("%Y%m%d")
        same_day = ReturnNote.objects.filter(code__startswith=f"TH{today}").count()
        return f"TH{today}-{same_day + 1:04d}"

    @staticmethod
    @transaction.atomic
    def create_return(
        *, return_type: str, warehouse_id: int, lines: list[dict],
        source_order_id: int | None = None, customer_name: str = "",
        customer_phone: str = "", source_note: str = "", note: str = "",
        user_id: int | None = None,
    ) -> ReturnNote:
        rn = ReturnNote.objects.create(
            code=ReturnNoteService._generate_code(), return_type=return_type,
            warehouse_id=warehouse_id, source_order_id=source_order_id,
            customer_name=customer_name, customer_phone=customer_phone,
            source_note=source_note, note=note, created_by_id=user_id,
        )
        ReturnNoteLine.objects.bulk_create([
            ReturnNoteLine(return_note=rn, product_id=l["product_id"], quantity=l["quantity"],
                           condition_note=l.get("condition_note", ""))
            for l in lines
        ])
        return rn

    @staticmethod
    @transaction.atomic
    def post_return(*, return_note_id: int, user_id: int | None = None) -> ReturnNote:
        rn = ReturnNote.objects.select_for_update().get(id=return_note_id)
        if rn.status != ReturnStatus.DRAFT:
            raise ValueError(f"Chỉ phiếu nháp mới ghi sổ được (đang {rn.status}).")
        state = _RETURN_STATE[rn.return_type]
        for line in rn.lines.all():
            StockCommandService.record_movement(
                warehouse_id=rn.warehouse_id, product_id=line.product_id,
                qty_delta=line.quantity, movement_type=MovementType.RETURN_IN,
                state=state, reference_type=_REFERENCE_TYPE, reference_id=rn.id, user_id=user_id)
        rn.status = ReturnStatus.POSTED
        rn.posted_at = timezone.now()
        rn.save(update_fields=["status", "posted_at"])
        return rn

    @staticmethod
    @transaction.atomic
    def cancel_return(*, return_note_id: int, user_id: int | None = None) -> ReturnNote:
        rn = ReturnNote.objects.select_for_update().get(id=return_note_id)
        if rn.status != ReturnStatus.POSTED:
            raise ValueError(f"Chỉ phiếu đã ghi sổ mới huỷ được (đang {rn.status}).")
        movements = StockMovement.objects.filter(reference_type=_REFERENCE_TYPE, reference_id=rn.id)
        for mv in movements:
            StockCommandService.record_movement(
                warehouse_id=mv.warehouse_id, product_id=mv.product_id, qty_delta=-mv.qty_delta,
                movement_type=mv.movement_type, state=mv.state,
                reference_type=_REFERENCE_TYPE, reference_id=rn.id,
                note=f"Huỷ phiếu {rn.code}", user_id=user_id)
        rn.status = ReturnStatus.CANCELLED
        rn.save(update_fields=["status"])
        return rn
```

- [ ] **Step 4: Chạy → PASS (7).** Ruff + commit `feat(inventory): add return note post/cancel service`.

---

## Task 3: API trả hàng (TDD)

**Files:** create `transport/serializers/return_serializers.py`, `transport/views/return_views.py`; edit `urls.py`; test `tests/test_return_api.py`.

> Theo ĐÚNG pattern `document_serializers.py`/`document_views.py` (đọc trước).

- [ ] **Step 1: Serializers** — `ReturnNoteLineSerializer` (product, product_sku, product_name, quantity, condition_note), `ReturnNoteSerializer` (id, code, return_type, status, source_order, warehouse, warehouse_name, customer_name, customer_phone, source_note, note, created_at, posted_at, lines), `CreateReturnNoteSerializer` (return_type ChoiceField 4 loại; warehouse_id required int; source_order_id/customer_name/customer_phone/source_note/note optional; lines = list of {product_id, quantity, condition_note?}; validate: ≥1 line).

- [ ] **Step 2: Views** — `list_create_returns` (GET paginated, filter return_type/status; POST create via `ReturnNoteService.create_return(... user_id=request.user.id)`), `return_detail`, `post_return_view`, `cancel_return_view`. Permission `require_section("returns")`. ValueError→400, DoesNotExist→404. (Pattern document_views.)

- [ ] **Step 3: URLs** — thêm:
```python
    path("returns", list_create_returns, name="inventory-returns"),
    path("returns/<int:return_id>", return_detail, name="inventory-return-detail"),
    path("returns/<int:return_id>/post", post_return_view, name="inventory-return-post"),
    path("returns/<int:return_id>/cancel", cancel_return_view, name="inventory-return-cancel"),
```

- [ ] **Step 4: Test API** — tạo user admin; POST tạo phiếu warranty → 201 draft; POST /post → 200 + GET `/api/inventory/stock?state=warranty` thấy tồn tăng; POST /cancel → về 0; validate thiếu line → 400.

- [ ] **Step 5:** Chạy `apps/inventory` → PASS. Ruff + commit `feat(inventory): add return note API`.

---

## Task 4: FE — section "Trả hàng"

**Files:** edit `lib/routes.ts`, `app/modules.ts`, `app/routes.tsx`, `app/routed-pages.tsx`, `i18n/messages/topnav.ts`, `app/__tests__/modules.test.ts`, `lib/routes.test.ts`; create `features/inventory/returns-types.ts`, `returns-queries.ts`, `pages/ReturnsPage.tsx`.

- [ ] **Step 1: Section** — `AppSection` + `SECTION_TO_PATH["returns"] = "/inventory/returns"`; `modules.ts` inventory thêm `{ key: "returns", labelKey: "topnav.tabs.returns", icon: Undo2 }` (import `Undo2`); i18n `topnav.tabs.returns` vi "Trả hàng" / en "Returns".
- [ ] **Step 2: Test fixtures** — thêm `"returns"` vào `ALL_SECTIONS` trong `modules.test.ts` + `routes.test.ts`.
- [ ] **Step 3: types + queries** — `returns-types.ts` (ReturnNoteRow + line + list resp), `returns-queries.ts` (`useReturns`, `useCreateReturn`, `usePostReturn`, `useCancelReturn`; invalidate returns list + `inventoryKeys.stock`). Theo pattern `documents-queries.ts`.
- [ ] **Step 4: ReturnsPage** — danh sách phiếu trả (mã/loại/trạng thái/kho/nguồn) + dialog tạo: chọn **loại trả về** (4), **kho nhận**, **tìm đơn gốc** (tuỳ chọn — tìm theo id đơn; nếu khó thì nhập id + tên/đt khách + ghi chú nguồn), thêm dòng SP (tái dùng product search như StockDocumentsPage), nút Ghi sổ/Huỷ. Hiển thị nhãn loại tiếng Việt. Dùng layout 2 cột như dialog phiếu kho.
- [ ] **Step 5: Route** — routed-pages `ReturnsRoute`; routes.tsx `{ path: "inventory/returns", element: <Guard section="returns"><ReturnsRoute/></Guard> }`.
- [ ] **Step 6:** `npx tsc --noEmit` sạch; `npx vitest run src/lib/routes.test.ts src/app/__tests__/modules.test.ts` xanh. Commit `feat(inventory): add returns section and page (FE)`.

---

## Self-Review
- Coverage §3: model + section (T1), service map state + post/cancel (T2), API (T3), FE (T4). Báo cáo cơ bản = danh sách + filter theo loại (đủ MVP; báo cáo sâu sau).
- `_RETURN_STATE` map đúng 4 loại → state. Cancel reverse theo reference (robust).
- Nguồn gốc: source_order + customer + source_note (đủ "trả về từ đâu").
- Chống cộng trùng: phiếu trả hàng là chứng từ + tồn; KHÔNG đụng release_for_order (chỉ chạy khi CANCELLED).
