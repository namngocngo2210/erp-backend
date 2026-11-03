# Flow History — SKU + SL trong cột mã hóa đơn — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cell mã hóa đơn ở kiểu xem "Theo đơn" của cả 2 tab trang Lịch sử nhập xuất liệt kê đầy đủ SKU × số lượng ngay dưới mã, không cần bấm mở drill-down.

**Architecture:** Backend trả kèm khóa `lines: [{product_id, sku, qty}]` trong payload danh sách của 2 endpoint list (`dispatch-history/orders`, `return-history`), lấy bằng đúng 1 query gộp theo id của trang hiện tại (không N+1). Frontend thêm 1 component trình bày dùng chung `SkuLines` và gắn vào cell mã hóa đơn của `IssueTab` và `ReturnTab`.

**Tech Stack:** Django 5 + DRF + drf-spectacular (BE), pytest; React 19 + TypeScript + React Query + Vitest/RTL (FE); type FE sinh từ OpenAPI (`npm run gen:api`).

## Global Constraints

- Branch: `feat/flow-history-sku-in-invoice-cell` (worktree `../desi-order-wt-flow-sku`). Commit message tiếng Anh, ngắn gọn, KHÔNG kèm dòng `Co-Authored-By`.
- Tab Xuất: `qty` của mỗi dòng SKU là **net = dispatched − reversed** (nhất quán với cột "SL xuất" và drill-down hiện có).
- Tab Nhập hoàn: `qty` là tổng `quantity` các dòng phiếu trả của product đó trong phiếu.
- KHÔNG đụng: kiểu xem "Theo SKU", dòng "chờ khớp" (PendingShipmentScan), Excel export, drill-down.
- Không N+1: mỗi endpoint list chỉ được thêm tối đa 1 query.
- Type FE lấy từ `src/api/schema.d.ts` (chạy `npm run gen:api`), cấm khai báo type tay.
- Chạy test BE trên Windows: `docker compose -f docker-compose.test.yml run --rm -e POSTGRES_DB=desi_flowsku web-test pytest <path>` (xem chi tiết ở Task 1 Step 2).

## File Structure

| File | Trách nhiệm |
| --- | --- |
| `backend/apps/inventory/application/services/dispatch_history_service.py` (sửa) | `orders_page` thêm `lines` cho mỗi đơn |
| `backend/apps/inventory/application/services/return_history_service.py` (sửa) | `list_page` thêm `lines` cho mỗi phiếu trả |
| `backend/apps/inventory/transport/views/flow_history_views.py` (sửa) | Serializer `_FLOW_LINE_BRIEF` nhúng vào 2 item serializer |
| `backend/apps/inventory/tests/test_dispatch_history_service.py` (sửa) | Test `lines` tab Xuất |
| `backend/apps/inventory/tests/test_return_history_service.py` (sửa) | Test `lines` tab Nhập hoàn |
| `frontend/src/features/inventory/components/flow-shared.tsx` (sửa) | Component `SkuLines` dùng chung |
| `frontend/src/features/inventory/components/IssueTab.tsx` (sửa) | Cell "Mã hóa đơn" dùng `SkuLines` |
| `frontend/src/features/inventory/components/ReturnTab.tsx` (sửa) | Cell "Mã hóa đơn nguồn" dùng `SkuLines` |
| `frontend/src/features/inventory/__tests__/flow-sku-lines.test.tsx` (tạo) | Render test `SkuLines` |

---

### Task 1: Backend — `lines` cho tab Xuất

**Files:**
- Modify: `backend/apps/inventory/application/services/dispatch_history_service.py` (hàm `orders_page`, dòng 55–118)
- Test: `backend/apps/inventory/tests/test_dispatch_history_service.py`

**Interfaces:**
- Consumes: `DispatchHistoryService.base_queryset(**filters)` (đã có).
- Produces: mỗi phần tử của `orders_page()["items"]` có thêm khóa `lines: list[dict]`, mỗi dòng là `{"product_id": int, "sku": str | None, "qty": int}`, sắp xếp `qty` giảm dần.

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/inventory/tests/test_dispatch_history_service.py` (file đã có helper `_seed_two_orders_one_sku()` seed: đơn `o1` xuất SKU1 3 cái + hoàn 1 → net 2; đơn `o2` xuất SKU1 5 + SKU2 2):

```python
def test_orders_page_includes_sku_lines():
    _, o1, o2, p1, p2 = _seed_two_orders_one_sku()
    data = DispatchHistoryService.orders_page(pagination={"limit": 50, "offset": 0})
    by_order = {i["order_id"]: i for i in data["items"]}

    # Đơn 1: SKU1 xuất 3, hoàn 1 → qty là NET = 2
    assert by_order[o1.id]["lines"] == [
        {"product_id": p1.id, "sku": "SKU1", "qty": 2}
    ]
    # Đơn 2: 2 SKU, sắp xếp SL giảm dần (SKU1=5 trước SKU2=2)
    assert by_order[o2.id]["lines"] == [
        {"product_id": p1.id, "sku": "SKU1", "qty": 5},
        {"product_id": p2.id, "sku": "SKU2", "qty": 2},
    ]


def test_orders_page_lines_respect_product_filter():
    _seed_two_orders_one_sku()
    data = DispatchHistoryService.orders_page(
        pagination={"limit": 50, "offset": 0}, product="SKU2"
    )
    items = data["items"]
    assert len(items) == 1
    assert [line["sku"] for line in items[0]["lines"]] == ["SKU2"]
```

- [ ] **Step 2: Chạy test cho chắc là FAIL**

Run:
```bash
cd ../desi-order-wt-flow-sku
docker compose -f docker-compose.test.yml run --rm -e POSTGRES_DB=desi_flowsku web-test \
  pytest apps/inventory/tests/test_dispatch_history_service.py -v
```
Expected: 2 test mới FAIL với `KeyError: 'lines'`.

- [ ] **Step 3: Cài đặt tối thiểu**

Trong `orders_page`, ngay sau khối dựng `wh_names` (dòng ~83–89), thêm query gộp dòng SKU và dựng `lines_map`:

```python
        # Dòng SKU của các đơn trong TRANG hiện tại — 1 query gộp, không N+1.
        lines_map: dict[int, list[dict]] = {}
        line_rows = (
            DispatchHistoryService.base_queryset(**filters)
            .filter(reference_id__in=order_ids)
            .values("reference_id", "product_id", "product__sku")
            .annotate(
                sale_sum=Coalesce(Sum("qty_delta", filter=Q(movement_type=_SALE)), 0),
                rev_sum=Coalesce(
                    Sum("qty_delta", filter=Q(movement_type=_SALE_REVERSAL)), 0
                ),
            )
        )
        for r in line_rows:
            lines_map.setdefault(r["reference_id"], []).append(
                {
                    "product_id": r["product_id"],
                    "sku": r["product__sku"],
                    "qty": -r["sale_sum"] - r["rev_sum"],
                }
            )
        for rows in lines_map.values():
            rows.sort(key=lambda x: x["qty"], reverse=True)
```

Trong vòng `for r in page`, thêm khóa `lines` vào dict `items.append({...})`, đặt ngay trước `"sku_count"`:

```python
                    "lines": lines_map.get(oid, []),
```

- [ ] **Step 4: Chạy test cho chắc là PASS**

Run:
```bash
docker compose -f docker-compose.test.yml run --rm -e POSTGRES_DB=desi_flowsku web-test \
  pytest apps/inventory/tests/test_dispatch_history_service.py -v
```
Expected: PASS toàn bộ file (test cũ không được đỏ).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/application/services/dispatch_history_service.py \
        backend/apps/inventory/tests/test_dispatch_history_service.py
git commit -m "feat(inventory): return sku lines in issue history orders page"
```

---

### Task 2: Backend — `lines` cho tab Nhập hoàn

**Files:**
- Modify: `backend/apps/inventory/application/services/return_history_service.py` (hàm `list_page`, dòng 45–95)
- Test: `backend/apps/inventory/tests/test_return_history_service.py`

**Interfaces:**
- Consumes: `ReturnHistoryService._base(**filters)` (đã có).
- Produces: mỗi phần tử của `list_page()["items"]` có thêm `lines: [{"product_id": int, "sku": str, "qty": int}]` (cùng shape với Task 1), sắp xếp `qty` giảm dần. `qty_total` giữ nguyên hành vi cũ.

- [ ] **Step 1: Viết test thất bại**

Thêm vào cuối `backend/apps/inventory/tests/test_return_history_service.py` (helper `_seed_returns()` đã seed: phiếu `TH-0001` có SKU1 qty 2 + SKU2 qty 1; phiếu `TH-0002` có SKU1 qty 3; 1 phiếu nháp không được tính):

```python
def test_list_page_includes_sku_lines():
    _seed_returns()
    data = ReturnHistoryService.list_page(pagination={"limit": 50, "offset": 0})
    by_code = {i["code"]: i for i in data["items"]}

    # Phiếu 1: 2 SKU, sắp xếp SL giảm dần
    assert [(l["sku"], l["qty"]) for l in by_code["TH-0001"]["lines"]] == [
        ("SKU1", 2),
        ("SKU2", 1),
    ]
    assert by_code["TH-0001"]["qty_total"] == 3
    # Phiếu 2: 1 SKU
    assert [(l["sku"], l["qty"]) for l in by_code["TH-0002"]["lines"]] == [("SKU1", 3)]
    # Phiếu nháp không xuất hiện
    assert "TH-DRAFT" not in by_code
```

- [ ] **Step 2: Chạy test cho chắc là FAIL**

Run:
```bash
docker compose -f docker-compose.test.yml run --rm -e POSTGRES_DB=desi_flowsku web-test \
  pytest apps/inventory/tests/test_return_history_service.py -v
```
Expected: test mới FAIL với `KeyError: 'lines'`.

- [ ] **Step 3: Cài đặt tối thiểu**

Trong `list_page`, thay khối `qty_map` hiện tại (dòng ~57–62) bằng 1 query gộp theo `(phiếu, product)` dựng cả `lines_map` lẫn `qty_map`:

```python
        # 1 query gộp: vừa ra dòng SKU của từng phiếu, vừa ra tổng SL.
        line_rows = (
            ReturnNoteLine.objects.filter(return_note_id__in=note_ids)
            .values("return_note_id", "product_id", "product__sku")
            .annotate(qty=Coalesce(Sum("quantity"), 0))
        )
        lines_map: dict[int, list[dict]] = {}
        qty_map: dict[int, int] = {}
        for r in line_rows:
            nid = r["return_note_id"]
            lines_map.setdefault(nid, []).append(
                {
                    "product_id": r["product_id"],
                    "sku": r["product__sku"],
                    "qty": r["qty"],
                }
            )
            qty_map[nid] = qty_map.get(nid, 0) + r["qty"]
        for rows in lines_map.values():
            rows.sort(key=lambda x: x["qty"], reverse=True)
```

Trong dict comprehension dựng `items`, thêm khóa `lines` ngay trước `"qty_total"`:

```python
                "lines": lines_map.get(n.id, []),
```

- [ ] **Step 4: Chạy test cho chắc là PASS**

Run:
```bash
docker compose -f docker-compose.test.yml run --rm -e POSTGRES_DB=desi_flowsku web-test \
  pytest apps/inventory/tests/test_return_history_service.py apps/inventory/tests/test_return_history.py -v
```
Expected: PASS toàn bộ (kể cả test cũ về `qty_total`).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/application/services/return_history_service.py \
        backend/apps/inventory/tests/test_return_history_service.py
git commit -m "feat(inventory): return sku lines in return history list page"
```

---

### Task 3: Serializer + OpenAPI schema

**Files:**
- Modify: `backend/apps/inventory/transport/views/flow_history_views.py` (`_ISSUE_ORDER_ITEM` dòng ~122–133; serializer item của `return_history` list)
- Modify: `frontend/src/api/schema.d.ts` (sinh tự động)
- Test: `backend/apps/inventory/tests/test_flow_history_views.py`

**Interfaces:**
- Consumes: `lines` từ Task 1 & Task 2.
- Produces: component OpenAPI `FlowLineBrief` với `{product_id: int, sku: string|null, qty: int}`; `IssueOrdersHistoryItem.lines` và `ReturnHistoryItem.lines` là `FlowLineBrief[]` → FE dùng `IssueOrdersHistoryItem["lines"]`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `backend/apps/inventory/tests/test_flow_history_views.py` (đọc file trước để dùng đúng fixture client/auth có sẵn trong đó; ví dụ nếu file dùng fixture `auth_client` và helper seed thì bám theo). Test khẳng định payload API có `lines`:

```python
def test_issue_orders_endpoint_returns_lines(auth_client):
    # dùng đúng helper seed đã có trong file này để tạo 1 đơn đã xuất
    _seed_issue_movement()
    res = auth_client.get("/api/inventory/dispatch-history/orders?limit=50&offset=0")
    assert res.status_code == 200
    item = res.json()["items"][0]
    assert isinstance(item["lines"], list)
    assert {"product_id", "sku", "qty"} <= set(item["lines"][0])
```

- [ ] **Step 2: Chạy test cho chắc là FAIL**

Run:
```bash
docker compose -f docker-compose.test.yml run --rm -e POSTGRES_DB=desi_flowsku web-test \
  pytest apps/inventory/tests/test_flow_history_views.py -v
```
Expected: FAIL — `KeyError: 'lines'` (serializer chưa khai báo nên DRF chưa chắc cắt, nhưng schema chưa có → test khẳng định shape).

- [ ] **Step 3: Cài đặt tối thiểu**

Trong `flow_history_views.py`, thêm serializer dùng chung phía trên `_ISSUE_ORDER_ITEM`:

```python
# Dòng SKU rút gọn hiển thị ngay trong cell mã hóa đơn (dùng chung 2 tab).
_FLOW_LINE_BRIEF = {
    "product_id": drf.IntegerField(),
    "sku": drf.CharField(allow_null=True),
    "qty": drf.IntegerField(),
}
```

Thêm khóa `lines` vào `_ISSUE_ORDER_ITEM`:

```python
    "lines": inline_serializer("FlowLineBrief", many=True, fields=_FLOW_LINE_BRIEF),
```

Tìm dict fields của item danh sách return history trong cùng file (serializer của endpoint `return_history`, có các khóa `return_id`/`code`/`qty_total`) và thêm cùng dòng đó — **tái dùng cùng tên component** `FlowLineBrief` để drf-spectacular gộp làm một (giống cách `FlowPendingScans` đang dùng chung):

```python
    "lines": inline_serializer("FlowLineBrief", many=True, fields=_FLOW_LINE_BRIEF),
```

- [ ] **Step 4: Chạy test + sinh lại schema**

Run:
```bash
docker compose -f docker-compose.test.yml run --rm -e POSTGRES_DB=desi_flowsku web-test \
  pytest apps/inventory/tests/test_flow_history_views.py -v
```
Expected: PASS.

Sinh lại `backend/schema.yaml` rồi type FE:
```bash
docker compose -f docker-compose.test.yml run --rm web-test \
  python manage.py spectacular --file schema.yaml
cd frontend && npm run gen:api
```
Expected: `frontend/src/api/schema.d.ts` xuất hiện `FlowLineBrief`, và `IssueOrdersHistoryItem`/`ReturnHistoryItem` có `lines`.
(Nếu `spectacular` ghi file trong container không ra host, chạy `python manage.py spectacular --file schema.yaml` bằng lệnh compose có mount source — bám theo cách các MR trước đã sinh `backend/schema.yaml`.)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/inventory/transport/views/flow_history_views.py \
        backend/apps/inventory/tests/test_flow_history_views.py \
        backend/schema.yaml frontend/src/api/schema.d.ts
git commit -m "feat(inventory): expose sku lines in flow history schema"
```

---

### Task 4: Frontend — component `SkuLines` + gắn vào 2 tab

**Files:**
- Modify: `frontend/src/features/inventory/components/flow-shared.tsx`
- Modify: `frontend/src/features/inventory/components/IssueTab.tsx` (cell mã hóa đơn ở nhánh view "order", dòng ~231–233)
- Modify: `frontend/src/features/inventory/components/ReturnTab.tsx` (cell mã hóa đơn nguồn ở nhánh view "order", dòng ~239–241)
- Create: `frontend/src/features/inventory/__tests__/flow-sku-lines.test.tsx`

**Interfaces:**
- Consumes: `IssueOrdersHistoryItem["lines"]` / `ReturnHistoryItem["lines"]` từ Task 3.
- Produces: `export function SkuLines({ code, lines }: { code: string | null; lines: FlowLineBrief[] })` trong `flow-shared.tsx`.

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/inventory/__tests__/flow-sku-lines.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SkuLines } from "../components/flow-shared";

describe("SkuLines", () => {
  it("hiển thị mã kèm từng SKU × số lượng", () => {
    render(
      <SkuLines
        code="HD00123"
        lines={[
          { product_id: 1, sku: "DESI-A01", qty: 2 },
          { product_id: 2, sku: "DESI-B07", qty: 1 },
        ]}
      />,
    );
    expect(screen.getByText("HD00123")).toBeInTheDocument();
    expect(screen.getByText("DESI-A01 × 2")).toBeInTheDocument();
    expect(screen.getByText("DESI-B07 × 1")).toBeInTheDocument();
  });

  it("không có dòng SKU thì chỉ hiện mã", () => {
    const { container } = render(<SkuLines code="HD00124" lines={[]} />);
    expect(screen.getByText("HD00124")).toBeInTheDocument();
    expect(container.querySelectorAll("li")).toHaveLength(0);
  });

  it("thiếu mã thì hiện dấu gạch", () => {
    render(<SkuLines code={null} lines={[]} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Chạy test cho chắc là FAIL**

Run:
```bash
cd frontend && npx vitest run src/features/inventory/__tests__/flow-sku-lines.test.tsx
```
Expected: FAIL — `SkuLines` chưa export.

- [ ] **Step 3: Cài đặt tối thiểu**

Trong `frontend/src/lib/api/inventory-flow.ts`, thêm type dùng chung:

```ts
// Dòng SKU rút gọn hiển thị trong cell mã hóa đơn (dùng chung 2 tab)
export type FlowLineBrief = components["schemas"]["FlowLineBrief"];
```

Trong `frontend/src/features/inventory/components/flow-shared.tsx`, thêm import và component (đặt ngay dưới `ExportButton`):

```tsx
import type { FlowLineBrief, FlowPendingScanItem } from "@/lib/api/inventory-flow";

// Cell mã hóa đơn: mã ở dòng trên, bên dưới liệt kê đầy đủ SKU × SL của đơn/phiếu.
// Bộ lọc sản phẩm áp cả vào lines (giống drill-down) nên gõ 1 SKU thì chỉ hiện SKU đó.
export function SkuLines({ code, lines }: { code: string | null; lines: FlowLineBrief[] }) {
  return (
    <div className="min-w-[9rem]">
      <div className="font-medium">{code || "—"}</div>
      {lines.length > 0 && (
        <ul className="mt-0.5 space-y-0.5 text-xs text-muted-foreground">
          {lines.map((l) => (
            <li key={l.product_id}>
              {l.sku ?? "—"} × {l.qty}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

Trong `IssueTab.tsx`, thay cell mã hóa đơn của dòng lịch sử (nhánh view "order"):

```tsx
                  <td className="px-3 py-2">
                    <SkuLines code={r.invoice_code ?? r.order_code} lines={r.lines} />
                  </td>
```
và thêm `SkuLines` vào import từ `./flow-shared`.

Trong `ReturnTab.tsx`, thay cell mã hóa đơn nguồn của dòng lịch sử:

```tsx
                  <td className="px-3 py-2">
                    <SkuLines
                      code={r.source_order_invoice_code ?? r.source_order_code}
                      lines={r.lines}
                    />
                  </td>
```
và thêm `SkuLines` vào import từ `./flow-shared`.

- [ ] **Step 4: Chạy test + lint + typecheck cho chắc là PASS**

Run:
```bash
cd frontend && npx vitest run src/features/inventory/__tests__/flow-sku-lines.test.tsx src/features/inventory/__tests__/flow-sku-view.test.tsx && npm run lint && npx tsc --noEmit
```
Expected: test PASS, lint sạch, `tsc` không lỗi (nếu `tsc` báo `lines` không tồn tại → Task 3 chưa chạy `gen:api`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/components/flow-shared.tsx \
        frontend/src/features/inventory/components/IssueTab.tsx \
        frontend/src/features/inventory/components/ReturnTab.tsx \
        frontend/src/lib/api/inventory-flow.ts \
        frontend/src/features/inventory/__tests__/flow-sku-lines.test.tsx
git commit -m "feat(inventory): show sku lines in flow history invoice column"
```

---

### Task 5: Kiểm tra hồi quy + đẩy MR

**Files:** không sửa code (chỉ chạy kiểm tra; nếu có test đỏ thì sửa đúng chỗ gây đỏ).

- [ ] **Step 1: Chạy full test inventory (BE)**

Run:
```bash
docker compose -f docker-compose.test.yml run --rm -e POSTGRES_DB=desi_flowsku_full web-test \
  pytest apps/inventory -q
```
Expected: PASS (baseline inventory hiện đang xanh).

- [ ] **Step 2: Chạy full test FE**

Run:
```bash
cd frontend && npm test
```
Expected: chỉ còn các test đỏ đã biết từ trước (OrderFormDialog + 2 test mobile-viewport) — không được có test đỏ mới.

- [ ] **Step 3: Kiểm tra conflict với master mới nhất**

Run:
```bash
git fetch origin master && git merge --no-commit --no-ff origin/master
```
Expected: `Already up to date` hoặc merge sạch. Nếu merge sạch thì `git merge --abort` (không giữ merge commit thừa); nếu conflict thì resolve rồi commit.

- [ ] **Step 4: Push + tạo MR**

```bash
git push -u origin feat/flow-history-sku-in-invoice-cell \
  -o merge_request.create -o merge_request.target=master \
  -o merge_request.title="feat(inventory): show SKU lines in flow history invoice column"
```
Expected: GitLab in ra link MR — gửi link đó cho user.

---

## Self-Review

- **Spec coverage:** hiển thị đầy đủ SKU×SL (Task 4) · tab Xuất net (Task 1) · tab Nhập hoàn (Task 2) · serializer/type (Task 3) · không đụng SKU-view/pending/Excel (không có task nào chạm) · test BE+FE (Task 1/2/4) — đủ.
- **Placeholder scan:** không có TBD/TODO; mọi step có lệnh và code cụ thể.
- **Type consistency:** khóa `lines` với shape `{product_id, sku, qty}` giống nhau ở Task 1, 2, 3 (serializer `FlowLineBrief`) và 4 (`FlowLineBrief` FE).
