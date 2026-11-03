# Flow History — SKU cho dòng "Đã khớp" + bỏ drill-down — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Dòng mã quét "Đã khớp" ở khối chờ khớp hiển thị SKU × SL giống dòng lịch sử; đồng thời bỏ hẳn drill-down (bấm dòng để bung bảng con) ở kiểu xem "Theo đơn" của cả 2 tab, vì SKU đã hiện sẵn trong cell.

**Architecture:** `PendingScanService.list_unmatched` gắn thêm `lines` (+ `sku_count`, `qty`) cho các dòng đã khớp — tab Xuất lấy từ `StockMovement` của `matched_order_id` (net), tab Nhập hoàn lấy từ `ReturnNoteLine` của `return_id` đã gắn sẵn. Mỗi nguồn 1 query gộp, không N+1. Frontend render `SkuLines` (đã có) trong cell mã hóa đơn của dòng pending, và gỡ toàn bộ state/JSX drill-down khỏi nhánh view "order" của `IssueTab`/`ReturnTab`. Endpoint + service + hook drill-down thành code chết → xoá.

**Tech Stack:** Django 5 + DRF + drf-spectacular, pytest; React 19 + TS + React Query + Vitest/RTL; type FE sinh từ OpenAPI (`npm run gen:api`).

## Global Constraints

- Branch: `feat/flow-history-pending-sku` (worktree `../desi-order-wt-pending-sku`), tạo từ master mới nhất. Commit message tiếng Anh, ngắn, KHÔNG `Co-Authored-By`.
- Shape `lines` phải TRÙNG với component OpenAPI đã có `FlowLineBrief`: `{product_id: int, sku: str|None, qty: int}`, sort `qty` giảm dần, tie-break `sku`. Dùng LẠI component đó, không tạo component mới.
- Dòng CHƯA khớp (pending/failed/invalid) không có đơn → `lines: []`, cột số lượng vẫn `—`.
- Tab Xuất: `qty` = NET (dispatched − reversed). Tab Nhập hoàn: `qty` = tổng `ReturnNoteLine.quantity` theo product của phiếu.
- Không N+1: tối đa 1 query gộp thêm cho mỗi kind, batch theo id của cả danh sách pending.
- Bỏ drill-down CHỈ ở kiểu xem "Theo đơn". Kiểu xem "Theo SKU" GIỮ NGUYÊN drill-down (nó bung ra đơn/phiếu, không phải SKU).
- Type FE lấy từ `src/api/schema.d.ts` (`npm run gen:api`), cấm khai báo tay. Sau `gen:api` phải chạy `npx prettier --write src/api/schema.d.ts` (không thì diff giả ~28k dòng).
- Test BE (Windows): image `desi-order-web-test:latest`, network `desi-order_default`, DB duy nhất mỗi lần (`-e POSTGRES_DB=desi_<unique>`).

## File Structure

| File | Trách nhiệm |
| --- | --- |
| `backend/apps/inventory/application/services/pending_scan_service.py` (sửa) | `list_unmatched` gắn `lines`/`sku_count`/`qty` cho dòng đã khớp |
| `backend/apps/inventory/transport/views/flow_history_views.py` (sửa) | `_PENDING_ITEM` serializer += 3 khóa; xoá 2 view drill-down |
| `backend/apps/inventory/urls.py` (sửa) | Xoá 2 route drill-down |
| `backend/apps/inventory/application/services/dispatch_history_service.py` (sửa) | Xoá `order_lines` |
| `backend/apps/inventory/application/services/return_history_service.py` (sửa) | Xoá `lines` |
| `frontend/src/features/inventory/components/IssueTab.tsx` (sửa) | Pending row dùng `SkuLines`; gỡ drill-down |
| `frontend/src/features/inventory/components/ReturnTab.tsx` (sửa) | Như trên |
| `frontend/src/features/inventory/{issue,return}-history-queries.ts` (sửa) | Xoá `useIssueOrderLines` / `useReturnLines` |
| Tests BE/FE (sửa) | Thêm test pending lines; xoá test của endpoint đã gỡ |

---

### Task 1: Backend — `lines` cho dòng mã quét đã khớp

**Files:**
- Modify: `backend/apps/inventory/application/services/pending_scan_service.py` (`list_unmatched`, dòng ~104–148)
- Modify: `backend/apps/inventory/transport/views/flow_history_views.py` (`_PENDING_ITEM`)
- Test: `backend/apps/inventory/tests/test_pending_scan_service.py` (hoặc file test pending sẵn có — tìm file đang test `list_unmatched` và thêm vào đó)

**Interfaces:**
- Consumes: `PendingShipmentScan.matched_order_id`; `it["return_id"]` do `_attach_return_notes` gắn (chỉ khi `with_return_note=True`, tức tab Nhập hoàn).
- Produces: mỗi item của `list_unmatched()` thêm `lines: [{product_id, sku, qty}]` (sort qty desc, tie-break sku), `sku_count: int`, `qty: int` (tổng SL của dòng đó). Dòng chưa khớp → `lines: []`, `sku_count: 0`, `qty: 0`.

- [ ] **Step 1: Viết test thất bại**

Tìm file test đang cover `PendingScanService.list_unmatched` (grep `list_unmatched` trong `backend/apps/inventory/tests/`). Thêm 2 test, seed theo đúng helper/fixture của file đó:

```python
def test_list_unmatched_dispatch_matched_row_has_sku_lines():
    """Mã quét XUẤT đã khớp → lines lấy từ StockMovement của đơn (net = xuất − hoàn)."""
    # seed: 1 PendingShipmentScan kind=dispatch, status=matched, matched_order=o1
    # o1 có SKU1 xuất 3 + hoàn 1 (net 2), SKU2 xuất 5
    items = PendingScanService.list_unmatched(kind="dispatch", match_filter="matched")
    row = items[0]
    assert [(l["sku"], l["qty"]) for l in row["lines"]] == [("SKU2", 5), ("SKU1", 2)]
    assert row["sku_count"] == 2
    assert row["qty"] == 7


def test_list_unmatched_pending_row_has_no_lines():
    """Mã CHƯA khớp thì chưa có đơn → lines rỗng."""
    # seed: 1 PendingShipmentScan kind=dispatch, status=pending, matched_order=None
    items = PendingScanService.list_unmatched(kind="dispatch", match_filter="unmatched")
    row = items[0]
    assert row["lines"] == []
    assert row["sku_count"] == 0
    assert row["qty"] == 0
```

Và 1 test cho tab Nhập hoàn:

```python
def test_list_unmatched_return_matched_row_has_sku_lines():
    """Mã quét HOÀN đã khớp → lines lấy từ ReturnNoteLine của phiếu trả tương ứng."""
    # seed: PendingShipmentScan kind=return, status=matched, code=<shipping_code>
    # + ReturnNote (chưa huỷ) cùng shipping_code, 2 dòng: SKU1 qty 2, SKU2 qty 1
    items = PendingScanService.list_unmatched(
        kind="return", match_filter="matched", with_return_note=True
    )
    row = items[0]
    assert [(l["sku"], l["qty"]) for l in row["lines"]] == [("SKU1", 2), ("SKU2", 1)]
    assert row["qty"] == 3
```

- [ ] **Step 2: Chạy test — phải FAIL**

Expected: `KeyError: 'lines'`.

- [ ] **Step 3: Cài đặt**

Trong `list_unmatched`, sau khi dựng `items` (và SAU `_attach_return_notes` vì tab hoàn cần `return_id`), gọi 1 helper mới gắn lines theo kind:

```python
        if with_return_note:
            PendingScanService._attach_return_notes(items)
        PendingScanService._attach_sku_lines(items, kind=kind)
        return items
```

Thêm 2 static method. Tab Xuất — gộp `StockMovement` theo `(reference_id, product)` cho mọi `matched_order_id` của trang; tab Nhập hoàn — gộp `ReturnNoteLine` theo `(return_note_id, product)`:

```python
    @staticmethod
    def _attach_sku_lines(items: list[dict], *, kind: str) -> None:
        """Gắn lines/sku_count/qty cho dòng ĐÃ khớp — 1 query gộp, không N+1.

        Dòng chưa khớp chưa có đơn/phiếu nên để rỗng. Shape trùng FlowLineBrief
        của danh sách lịch sử để FE dùng chung component SkuLines.
        """
        for it in items:
            it.setdefault("lines", [])
            it.setdefault("sku_count", 0)
            it.setdefault("qty", 0)
        if kind == "return":
            lines_map = PendingScanService._return_lines_map(
                [it["return_id"] for it in items if it.get("return_id")]
            )
            key = "return_id"
        else:
            lines_map = PendingScanService._issue_lines_map(
                [it["matched_order_id"] for it in items if it.get("matched_order_id")]
            )
            key = "matched_order_id"
        for it in items:
            rows = lines_map.get(it.get(key)) or []
            it["lines"] = rows
            it["sku_count"] = len(rows)
            it["qty"] = sum(r["qty"] for r in rows)
```

`items` hiện CHƯA có `matched_order_id` — thêm khóa đó vào dict dựng trong `list_unmatched` (`"matched_order_id": s.matched_order_id,`) để `_attach_sku_lines` dùng, và giữ nguyên `matched_order_code`.

Hai helper map (đặt cạnh nhau, cùng file):

```python
    @staticmethod
    def _issue_lines_map(order_ids: list[int]) -> dict[int, list[dict]]:
        """SKU × SL net (xuất − hoàn) theo đơn — dùng cho mã quét XUẤT đã khớp."""
        if not order_ids:
            return {}
        rows = (
            StockMovement.objects.filter(
                movement_type__in=[MovementType.SALE, MovementType.SALE_REVERSAL],
                reference_type="order",
                reference_id__in=order_ids,
            )
            .values("reference_id", "product_id", "product__sku")
            .annotate(
                sale_sum=Coalesce(
                    Sum("qty_delta", filter=Q(movement_type=MovementType.SALE)), 0
                ),
                rev_sum=Coalesce(
                    Sum(
                        "qty_delta",
                        filter=Q(movement_type=MovementType.SALE_REVERSAL),
                    ),
                    0,
                ),
            )
        )
        out: dict[int, list[dict]] = {}
        for r in rows:
            out.setdefault(r["reference_id"], []).append(
                {
                    "product_id": r["product_id"],
                    "sku": r["product__sku"],
                    "qty": -r["sale_sum"] - r["rev_sum"],
                }
            )
        for v in out.values():
            v.sort(key=lambda x: (-x["qty"], x["sku"] or ""))
        return out

    @staticmethod
    def _return_lines_map(note_ids: list[int]) -> dict[int, list[dict]]:
        """SKU × SL hoàn theo phiếu trả — dùng cho mã quét HOÀN đã khớp."""
        if not note_ids:
            return {}
        rows = (
            ReturnNoteLine.objects.filter(return_note_id__in=note_ids)
            .values("return_note_id", "product_id", "product__sku")
            .annotate(qty=Coalesce(Sum("quantity"), 0))
        )
        out: dict[int, list[dict]] = {}
        for r in rows:
            out.setdefault(r["return_note_id"], []).append(
                {
                    "product_id": r["product_id"],
                    "sku": r["product__sku"],
                    "qty": r["qty"],
                }
            )
        for v in out.values():
            v.sort(key=lambda x: (-x["qty"], x["sku"] or ""))
        return out
```

Thêm import còn thiếu ở đầu file (`Sum`, `Coalesce`, `StockMovement`, `MovementType`, `ReturnNoteLine`) theo đúng style import sẵn có.

Serializer trong `flow_history_views.py`: `_PENDING_ITEM` thêm 3 khóa, tái dùng serializer `FlowLineBrief` đã có (`_FLOW_LINE_BRIEF_SERIALIZER`):

```python
    "lines": _FLOW_LINE_BRIEF_SERIALIZER,
    "sku_count": drf.IntegerField(),
    "qty": drf.IntegerField(),
```
(Nếu `_PENDING_ITEM` được định nghĩa TRƯỚC `_FLOW_LINE_BRIEF_SERIALIZER` trong file thì di chuyển serializer lên trên, KHÔNG tạo instance thứ 2 — 2 instance cùng tên làm drf-spectacular sinh schema sai.)

- [ ] **Step 4: Chạy test — phải PASS**

Chạy cả file test pending vừa sửa + `pytest apps/inventory -q`.

- [ ] **Step 5: Regen schema + commit**

Regen `backend/schema.yaml` (`python manage.py spectacular --file schema.yaml` trong container), rồi `cd frontend && npm run gen:api && npx prettier --write src/api/schema.d.ts`. Xác nhận `FlowPendingScanItem` có `lines`/`sku_count`/`qty` và KHÔNG có warning "components with identical names".

```bash
git add -A && git commit -m "feat(inventory): add sku lines to matched pending scan rows"
```

---

### Task 2: Frontend — render SKU ở dòng đã khớp + bỏ drill-down

**Files:**
- Modify: `frontend/src/features/inventory/components/IssueTab.tsx`
- Modify: `frontend/src/features/inventory/components/ReturnTab.tsx`
- Modify: `frontend/src/features/inventory/issue-history-queries.ts` (xoá `useIssueOrderLines`)
- Modify: `frontend/src/features/inventory/return-history-queries.ts` (xoá `useReturnLines`)
- Test: `frontend/src/features/inventory/__tests__/flow-sku-lines.test.tsx` (thêm), và sửa test nào đang dựa vào drill-down

**Interfaces:**
- Consumes: `FlowPendingScanItem.lines` (`FlowLineBrief[]`), `.sku_count`, `.qty` từ Task 1; component `SkuLines` đã có trong `components/flow-shared.tsx`.

- [ ] **Step 1: Viết test thất bại**

Thêm test khẳng định dòng pending đã khớp render SKU (dùng RTL, mock query hook theo đúng cách các test flow hiện có làm — xem `flow-sku-view.test.tsx` làm mẫu), và test khẳng định bấm vào dòng lịch sử KHÔNG bung bảng con nữa.

- [ ] **Step 2: Chạy test — phải FAIL**

- [ ] **Step 3: Cài đặt**

`IssueTab.tsx`, nhánh view "order":
- Cell mã hóa đơn của dòng pending: thay `{p.matched_order_code && <span …>}` bằng `<SkuLines code={p.matched_order_code || null} lines={p.lines} />` (giữ `PendingBadge` bên cạnh, badge ở trên cùng hàng với mã).
- Cột "Số SKU" của dòng pending: `{p.sku_count || "—"}`.
- Xoá state `openOrder`, hook `useIssueOrderLines`, `<tr>` drill-down, `cursor-pointer`/`hover:bg-muted/50`/`onClick` trên `<tr>` dòng lịch sử.

`ReturnTab.tsx`, nhánh view "order": tương tự — cell "Mã hóa đơn nguồn" của dòng pending dùng `SkuLines code={p.matched_order_code || null} lines={p.lines}`; xoá state `openId`, hook `useReturnLines`, `<tr>` drill-down và onClick/hover.

KHÔNG đụng nhánh `viewMode === "sku"` (drill-down ở đó giữ nguyên).

Xoá `useIssueOrderLines` khỏi `issue-history-queries.ts` và `useReturnLines` khỏi `return-history-queries.ts`, cùng import type `IssueOrderLines`/`ReturnHistoryLines` nếu không còn ai dùng.

- [ ] **Step 4: Verify**

```bash
cd frontend && npx vitest run src/features/inventory && npm run lint && npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(inventory): show sku lines on matched scan rows, drop drill-down"
```

---

### Task 3: Dọn code chết ở backend

**Files:**
- Modify: `backend/apps/inventory/transport/views/flow_history_views.py` (xoá view `issue_order_lines`, `return_history_lines` + serializer `_LINE_ITEM`/`_RETURN_LINE_ITEM` nếu chỉ chúng dùng)
- Modify: `backend/apps/inventory/urls.py` (xoá 2 route `dispatch-history/orders/lines`, `return-history/lines`)
- Modify: `dispatch_history_service.py` (xoá `order_lines`), `return_history_service.py` (xoá `lines`)
- Modify: các test đang gọi chúng (`test_flow_history_views.py`, `test_issue_orders_history.py`, `test_return_history.py`) — xoá đúng test của endpoint/method đã gỡ, KHÔNG xoá test khác

**Interfaces:** không có gì mới. Sau task này FE không còn tham chiếu 2 endpoint đó (Task 2 đã gỡ).

- [ ] **Step 1: Xác minh không còn nơi nào dùng**

```bash
grep -rn "order_lines\|ReturnHistoryService.lines\|orders/lines\|return-history/lines" backend/apps frontend/src
```
Expected: chỉ còn chính các định nghĩa sắp xoá (và docs — bỏ qua docs).

- [ ] **Step 2: Xoá + chạy test**

Xoá view/route/service method/test tương ứng, regen `backend/schema.yaml`, `npm run gen:api && npx prettier --write src/api/schema.d.ts`.

```bash
pytest apps/inventory -q   # trong container, DB unique
cd frontend && npx tsc --noEmit && npm run lint && npm test
```
Expected: BE all pass; FE all pass.

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "refactor(inventory): remove unused flow history drill-down endpoints"
```

---

### Task 4: Hồi quy + MR

- [ ] **Step 1:** `pytest apps/inventory -q` + `ruff check apps/inventory` (container, DB unique) → pass.
- [ ] **Step 2:** `cd frontend && npm test && npx tsc --noEmit && npm run lint` → pass.
- [ ] **Step 3:** `git fetch origin master && git merge --no-commit --no-ff origin/master` → clean thì `git merge --abort`; conflict thì resolve.
- [ ] **Step 4:** push + tạo MR về master, gửi link.
