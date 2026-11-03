# Ghi chú cho mọi dòng ở Lịch sử nhập xuất — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mọi dòng của trang Lịch sử nhập xuất (trừ kiểu xem theo SKU) đều nhập được ghi chú tự do, hiện cả trên màn hình lẫn file Excel.

**Architecture:** Bảng mới `stock_flow_notes` chỉ phục vụ 2 loại dòng chưa có chỗ lưu (đơn xuất, chờ hàng hoàn về); 3 loại còn lại ghi thẳng vào field sẵn có. Một endpoint `PATCH /api/inventory/flow-notes` điều hướng theo `target_type`. Note được nhồi vào row **bên trong** `MergedIssueHistory.orders_page` / `MergedReturnHistory.list_page` — hai hàm mà **cả view lẫn job export Excel cùng gọi**, nên không thể lệch hai nhánh.

**Tech Stack:** Django 5 + DRF + drf-spectacular, PostgreSQL, pytest; React + TanStack Query + vitest.

**Spec:** `docs/flow-history-notes.md`

## Global Constraints

- Backend clean architecture: model ở `apps/inventory/models.py`, nghiệp vụ ở `application/services/`, HTTP ở `transport/views/`. View **không** chứa logic ghi.
- Quyền ghi ghi chú: `stock:edit` (đúng mức 2 endpoint note hiện có).
- Ruff bắt buộc xanh (ruleset ở `backend/pyproject.toml`).
- `backend/schema.yaml` **sửa tay** phần của mình, không regen đè (file lệch sẵn ~444 dòng trên master); đối chiếu bằng regen ra file tạm rồi diff riêng khối liên quan.
- Tiếng Việt trong mọi chuỗi hiển thị và comment.
- Commit message tiếng Anh, ngắn.

**Lệnh test backend** (chạy từ gốc worktree, đổi `POSTGRES_DB` mỗi lần chạy song song):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=fhnotes -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

**Lệnh test frontend:** `cd frontend && npm ci --prefer-offline --no-audit --no-fund` (một lần), rồi `npx vitest run <file>`, `npx tsc --noEmit`.

---

## Sprint 1 — Chỗ lưu + API

### Task 1: Model `StockFlowNote` + migration

**Files:**
- Modify: `backend/apps/inventory/models.py` (thêm cuối file)
- Create: `backend/apps/inventory/migrations/0041_stock_flow_note.py`
- Test: `backend/apps/inventory/tests/test_flow_notes.py`

**Interfaces:**
- Produces: `StockFlowNoteTarget` (TextChoices: `ORDER_DISPATCH="order_dispatch"`, `AWAITING_RETURN="awaiting_return"`), `StockFlowNote` model với field `target_type`, `target_id`, `note`, `updated_by`, `updated_at`, `created_at`.

- [ ] **Step 1: Viết test đỏ** — `test_flow_notes.py`:

```python
class StockFlowNoteModelTests(TestCase):
    def test_unique_per_target(self):
        StockFlowNote.objects.create(
            target_type=StockFlowNoteTarget.ORDER_DISPATCH, target_id=1, note="a"
        )
        with self.assertRaises(IntegrityError):
            StockFlowNote.objects.create(
                target_type=StockFlowNoteTarget.ORDER_DISPATCH, target_id=1, note="b"
            )

    def test_same_id_different_type_coexist(self):
        StockFlowNote.objects.create(
            target_type=StockFlowNoteTarget.ORDER_DISPATCH, target_id=7, note="xuất"
        )
        StockFlowNote.objects.create(
            target_type=StockFlowNoteTarget.AWAITING_RETURN, target_id=7, note="chờ hoàn"
        )
        self.assertEqual(StockFlowNote.objects.filter(target_id=7).count(), 2)
```

- [ ] **Step 2: Chạy test, xác nhận đỏ** — `ImportError: cannot import name 'StockFlowNote'`.

- [ ] **Step 3: Thêm model**

```python
class StockFlowNoteTarget(models.TextChoices):
    ORDER_DISPATCH = "order_dispatch", "Dòng đơn xuất"
    AWAITING_RETURN = "awaiting_return", "Dòng chờ hàng hoàn về"


class StockFlowNote(models.Model):
    """Ghi chú tự do cho dòng ở trang Lịch sử nhập xuất KHÔNG có chỗ lưu sẵn.

    Ba loại dòng khác (mã quét / nhóm đơn hoàn / phiếu kho) đã có field note
    riêng và KHÔNG đi qua bảng này — xem `FlowNoteService.update`.

    CỐ Ý không FK sang `orders`: đây là sổ ghi chú của module kho, gắn FK sẽ kéo
    phụ thuộc ngược tầng và chặn xoá đơn. Dòng mồ côi vô hại.
    """

    target_type = models.CharField(max_length=32, choices=StockFlowNoteTarget.choices)
    target_id = models.BigIntegerField()
    note = models.TextField(blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "stock_flow_notes"
        constraints = [
            models.UniqueConstraint(
                fields=["target_type", "target_id"], name="stock_flow_note_target_uniq"
            )
        ]
```

- [ ] **Step 4: Sinh migration** — `python manage.py makemigrations inventory` trong container; kiểm tên file ra đúng `0041_*`, đổi tên thành `0041_stock_flow_note.py` nếu khác.
- [ ] **Step 5: Chạy test, xác nhận xanh.**
- [ ] **Step 6: Commit** — `feat(inventory): add stock_flow_notes table`

### Task 2: `FlowNoteService`

**Files:**
- Create: `backend/apps/inventory/application/services/flow_note_service.py`
- Test: `backend/apps/inventory/tests/test_flow_notes.py`

**Interfaces:**
- Produces:
  - `FlowNoteService.notes_for(target_type: str, target_ids: list[int]) -> dict[int, str]`
  - `FlowNoteService.update(*, target_type: str, target_id: int, note: str, user_id: int | None) -> dict` trả `{"target_type", "target_id", "note"}`; ném `ValueError` khi `target_type` lạ, `LookupError` khi không tìm thấy thực thể.
- Consumes: `ReturnEntryService.update_note(entry_id=, note=)`, `PendingScanService.update_note(scan_id=, note=)`.

- [ ] **Step 1: Viết test đỏ** — 5 target_type ghi đúng chỗ + type lạ ném ValueError:

```python
class FlowNoteServiceTests(TestCase):
    def test_order_dispatch_upsert_overwrites(self):
        FlowNoteService.update(
            target_type="order_dispatch", target_id=9, note="lần 1", user_id=None
        )
        FlowNoteService.update(
            target_type="order_dispatch", target_id=9, note="lần 2", user_id=None
        )
        rows = StockFlowNote.objects.filter(target_type="order_dispatch", target_id=9)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().note, "lần 2")

    def test_stock_document_writes_document_field(self):
        doc = StockDocument.objects.create(code="PX-1", doc_type=DocType.ISSUE)
        FlowNoteService.update(
            target_type="stock_document", target_id=doc.id, note="ghi", user_id=None
        )
        doc.refresh_from_db()
        self.assertEqual(doc.note, "ghi")

    def test_unknown_target_type(self):
        with self.assertRaises(ValueError):
            FlowNoteService.update(
                target_type="linh tinh", target_id=1, note="x", user_id=None
            )

    def test_notes_for_returns_map(self):
        FlowNoteService.update(
            target_type="awaiting_return", target_id=3, note="khách hẹn", user_id=None
        )
        self.assertEqual(
            FlowNoteService.notes_for("awaiting_return", [3, 4]), {3: "khách hẹn"}
        )
```

- [ ] **Step 2: Chạy test, xác nhận đỏ.**

- [ ] **Step 3: Viết service.** `update` dùng `update_or_create` cho 2 loại ở bảng mới; 3 loại còn lại ủy quyền cho service sẵn có (`ReturnEntryService.update_note`, `PendingScanService.update_note`) hoặc ghi `StockDocument.note` với `update_fields=["note", "updated_at"]`. `notes_for` trả dict rỗng ngay khi `target_ids` rỗng (không bắn query).

- [ ] **Step 4: Chạy test, xác nhận xanh.**
- [ ] **Step 5: Commit** — `feat(inventory): add FlowNoteService`

### Task 3: Endpoint `PATCH /api/inventory/flow-notes`

**Files:**
- Create: `backend/apps/inventory/transport/views/flow_note_views.py`
- Modify: `backend/apps/inventory/urls.py`
- Test: `backend/apps/inventory/tests/test_flow_notes.py`

**Interfaces:**
- Produces: route name `inventory-flow-note-update`, path `flow-notes`, method PATCH, body `{target_type, target_id, note}`, response `{target_type, target_id, note}`.

- [ ] **Step 1: Viết test đỏ** — 200 ghi đúng chỗ; 400 khi `target_type` lạ / `target_id` không phải số; 403 khi thiếu `stock:edit`; 404 khi thực thể không tồn tại.
- [ ] **Step 2: Chạy test, xác nhận đỏ.**
- [ ] **Step 3: Viết view** — `@permission_classes([IsAuthenticated, require_permission("stock:edit")])`, `@never_cache`, `@extend_schema` với `inline_serializer("FlowNoteUpdate"/"FlowNoteResult")`. View chỉ parse + map exception → HTTP code, gọi `FlowNoteService.update`.
- [ ] **Step 4: Chạy test, xác nhận xanh.**
- [ ] **Step 5: Commit** — `feat(inventory): add flow note update endpoint`

---

## Sprint 2 — Nhồi note vào 3 danh sách

### Task 4: Tab Xuất hàng ra — `note` + `doc_id` cho mọi dòng

**Files:**
- Modify: `backend/apps/inventory/application/services/issue_document_history.py` (`enrich_docs`, `MergedIssueHistory.orders_page`)
- Modify: `backend/apps/inventory/application/services/transfer_document_history.py` (`enrich_out`)
- Modify: `backend/apps/inventory/transport/views/flow_issue_views.py` (`_ISSUE_ORDER_ITEM`)
- Test: `backend/apps/inventory/tests/test_flow_notes.py`

**Interfaces:**
- Produces: mỗi item của `MergedIssueHistory.orders_page()["items"]` có thêm `"note": str` và `"doc_id": int | None`.

- [ ] **Step 1: Viết test đỏ** — tạo 1 đơn đã xuất + 1 phiếu xuất kho có note; đặt note `order_dispatch` cho đơn; gọi `orders_page` và assert dòng đơn mang note của bảng mới, dòng phiếu mang `StockDocument.note`, và `doc_id` khớp.
- [ ] **Step 2: Chạy test, xác nhận đỏ (KeyError `note`).**
- [ ] **Step 3: Thực thi:**
  - `enrich_docs`: thêm `"note"` vào `.values(...)` của `StockDocument.objects.filter(...)`; trả thêm `"doc_id": rid` và `"note": d.get("note") or ""`.
  - `enrich_out` (transfer): trả thêm `"doc_id": rid`, `"note": d.get("note", "")` (`_doc_meta` đã lấy sẵn `note`).
  - `orders_page`: sau khi ghép trang, với các item `kind == "order"` gọi `FlowNoteService.notes_for("order_dispatch", order_ids)` **một query**, gán `item["note"]`; item nào không có note thì `""`. Item `kind=order` cũng phải có `doc_id=None`.
- [ ] **Step 4: Thêm `"note"` và `"doc_id"` vào `_ISSUE_ORDER_ITEM`.**
- [ ] **Step 5: Chạy test, xác nhận xanh.**
- [ ] **Step 6: Commit** — `feat(inventory): return note on dispatch history rows`

### Task 5: Tab Nhập hàng hoàn về — `doc_id` cho dòng chuyển kho

**Files:**
- Modify: `backend/apps/inventory/application/services/transfer_document_history.py` (`enrich_in`)
- Modify: `backend/apps/inventory/transport/views/flow_return_views.py` (`_RETURN_ITEM`)
- Test: `backend/apps/inventory/tests/test_flow_notes.py`

**Interfaces:**
- Produces: item `kind="transfer"` của `MergedReturnHistory.list_page` có thêm `"doc_id": int`; item `kind="return"` có `"doc_id": None`.

- [ ] **Step 1: Viết test đỏ** — dòng chuyển kho trả `doc_id` và `note` của phiếu.
- [ ] **Step 2: Chạy test, xác nhận đỏ.**
- [ ] **Step 3: Thêm `doc_id` vào `enrich_in` + `_RETURN_ITEM`** (dòng `kind="return"` gán `doc_id=None` tại chỗ ghép trang).
- [ ] **Step 4: Chạy test, xác nhận xanh.**
- [ ] **Step 5: Commit** — `feat(inventory): return doc_id on return history transfer rows`

### Task 6: Tab Chờ hàng hoàn về — `note`

**Files:**
- Modify: `backend/apps/inventory/application/services/awaiting_return_service.py` (`list_page`)
- Modify: `backend/apps/inventory/transport/views/awaiting_return_views.py` (`_AWAITING_RETURN_ITEM`)
- Test: `backend/apps/inventory/tests/test_flow_notes.py`

**Interfaces:**
- Produces: mỗi row của `AwaitingReturnService.list_page` có thêm `"note": str` (nguồn: `stock_flow_notes` type `awaiting_return`, khoá `order_id`).

- [ ] **Step 1: Viết test đỏ** — note của `awaiting_return` hiện trong `list_page`, và **không** đè lên note `order_dispatch` cùng `order_id`.
- [ ] **Step 2: Chạy test, xác nhận đỏ.**
- [ ] **Step 3: Nhồi note** — sau khi dựng `rows`, gọi `FlowNoteService.notes_for("awaiting_return", order_ids)` một query rồi gán.
- [ ] **Step 4: Chạy test, xác nhận xanh.**
- [ ] **Step 5: Commit** — `feat(inventory): return note on awaiting return rows`

---

## Sprint 3 — Giao diện

### Task 7: `useFlowNoteUpdate` + `FlowNoteCell`

**Files:**
- Create: `frontend/src/features/inventory/flow-note-queries.ts`
- Modify: `frontend/src/features/inventory/components/flow-shared.tsx`
- Modify: `backend/schema.yaml`, `frontend/src/api/schema.d.ts` (sửa tay theo recipe)
- Test: `frontend/src/features/inventory/__tests__/flow-note-cell.test.tsx`

**Interfaces:**
- Produces: `useFlowNoteUpdate()` (mutation nhận `{targetType, targetId, note}`), `FlowNoteCell({ targetType, targetId, note })` — hiện `<input>` khi có `stock:edit`, ngược lại hiện text (`note || "—"`).

- [ ] **Step 1: Sửa `schema.yaml`** cho các schema đổi (`IssueOrdersHistoryItem` thêm `note`/`doc_id`, `ReturnHistoryItem` thêm `doc_id`, `AwaitingReturnItem` thêm `note`, thêm `FlowNoteUpdate`/`FlowNoteResult`). Kiểm chứng: regen ra `/tmp/schema-new.yaml` rồi diff riêng khối liên quan — **bản regen là chân lý**. Nhớ `required` sort alphabet.
- [ ] **Step 2: `npm run gen:api`** ra file tạm, chỉ áp hunk của mình vào `src/api/schema.d.ts` (kèm khối JSDoc `@description`).
- [ ] **Step 3: Viết test đỏ** — gõ vào ô note gọi PATCH đúng body; thiếu quyền `stock:edit` thì không render `<input>`.
- [ ] **Step 4: Chạy test, xác nhận đỏ.**
- [ ] **Step 5: Viết `flow-note-queries.ts` + `FlowNoteCell`** — mutation invalidate `["inventory","issue-orders"]`, `["inventory","return-history"]`, `["inventory","awaiting-return"]`; `onError` toast nguyên văn lỗi BE và **reject** để `InlineNoteCell` revert.
- [ ] **Step 6: Chạy test, xác nhận xanh.**
- [ ] **Step 7: Commit** — `feat(inventory): add flow note cell`

### Task 8: Gắn `FlowNoteCell` vào 3 tab + bản mobile

**Files:**
- Modify: `frontend/src/features/inventory/components/IssueTab.tsx`
- Modify: `frontend/src/features/inventory/components/ReturnTab.tsx`
- Modify: `frontend/src/features/inventory/components/AwaitingReturnTab.tsx`
- Modify: `frontend/src/features/inventory/components/flow-mobile.tsx`
- Test: `frontend/src/features/inventory/__tests__/` (file test sẵn có của 3 tab)

- [ ] **Step 1: Viết test đỏ** — mỗi tab render đúng ô ghi chú; tab Chờ hàng hoàn về có header "Ghi chú". Chốt viewport bằng `src/test/viewport.ts` (jsdom mặc định rơi vào nhánh thẻ mobile).
- [ ] **Step 2: Chạy test, xác nhận đỏ.**
- [ ] **Step 3: IssueTab** — thay 2 ô `—` (cột Ghi chú và dòng lịch sử) bằng `FlowNoteCell` với `targetType = r.kind === "order" ? "order_dispatch" : "stock_document"`, `targetId = r.kind === "order" ? r.order_id : r.doc_id`.
- [ ] **Step 4: ReturnTab** — dòng `kind="transfer"`: thay `<span>{r.note || "—"}</span>` bằng `FlowNoteCell targetType="stock_document" targetId={r.doc_id}`.
- [ ] **Step 5: AwaitingReturnTab** — thêm `<th>Ghi chú</th>` + ô `FlowNoteCell targetType="awaiting_return" targetId={row.order_id}`; sửa `colSpan` của dòng rỗng từ 9 → 10; thêm `CardField label="Ghi chú"` cho thẻ mobile.
- [ ] **Step 6: flow-mobile** — thẻ dòng đơn/phiếu nhận `note` là `FlowNoteCell`.
- [ ] **Step 7: Chạy test + `npx tsc --noEmit`, xác nhận xanh.**
- [ ] **Step 8: Commit** — `feat(inventory): editable note on all flow history rows`

---

## Sprint 4 — Excel

### Task 9: Nối note vào cột Ghi chú của 2 export

**Files:**
- Modify: `backend/apps/inventory/exports_definitions.py` (`DispatchHistoryExport`, `ReturnHistoryExport`)
- Test: `backend/apps/inventory/tests/test_inventory_export_definitions.py`

- [ ] **Step 1: Viết test đỏ** — đơn có cả note `order_dispatch` lẫn note mã quét → ô Ghi chú trong file = `"<note đơn>; <note mã quét>"`; dòng phiếu kho mang note của phiếu.
- [ ] **Step 2: Chạy test, xác nhận đỏ.**
- [ ] **Step 3: Sửa export** — nhánh theo đơn: ghép `r.get("note")` với `notes_by_code.get(...)`, bỏ trùng, nối `"; "` (đúng cách `ReturnHistoryExport` đang gộp scan note). Nhánh theo SKU **không đụng** (§2.2 spec).
- [ ] **Step 4: Chạy test, xác nhận xanh.**
- [ ] **Step 5: Commit** — `feat(inventory): include row notes in flow history export`

### Task 10: Chốt sprint

- [ ] **Step 1:** Chạy toàn bộ test kho backend: `apps/inventory` + `apps/exports`.
- [ ] **Step 2:** Chạy `ruff check .` (container ruff) — phải sạch.
- [ ] **Step 3:** Chạy `npx vitest run src/features/inventory` + `npx tsc --noEmit`.
- [ ] **Step 4:** Cập nhật bảng Sprint trong `docs/flow-history-notes.md`: đã làm gì / còn nợ gì.
- [ ] **Step 5:** Commit + push `-o merge_request.create`, gửi link MR.

---

## Rủi ro & bẫy đã biết

- **Hai nhánh code màn hình / export:** kế hoạch này nhồi note **bên trong** `orders_page`/`list_page` nên export tự có note. Nhưng phần **ghép chuỗi** ở export vẫn là code riêng (Task 9) — đừng quên.
- **Test export có chỉ số cột cứng** — không thêm cột mới vào Excel (chỉ đổ dữ liệu vào cột Ghi chú sẵn có) nên chỉ số không đổi; nếu buộc phải thêm cột thì sửa cả test.
- **`ResponsiveList` + `matchMedia`** trong vitest: jsdom mặc định rơi vào nhánh thẻ mobile → chốt viewport bằng `src/test/viewport.ts`.
- **`schema.yaml` lệch sẵn ~444 dòng** — chỉ sửa tay khối của mình, không regen đè.
- **Prettier hook** — chỉ chạy `--write` đích danh file mình chạm.
