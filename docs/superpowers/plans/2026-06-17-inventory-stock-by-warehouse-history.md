# Inventory Stock by Warehouse + Movement History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trang tồn kho lọc theo 1 kho; click sản phẩm mở accordion 10 giao dịch cộng/trừ tồn gần nhất (thời gian, mã hoá đơn, loại giao dịch, số lượng ±, tồn cuối).

**Architecture:** Backend chỉ thêm `reference_label` (enrich id→mã người đọc, batch-query) vào movements serializer; không model/migration mới. Frontend thêm `<Select>` kho, accordion mở rộng dùng hook lazy `useStockMovements`, component trình bày `StockMovementHistory`.

**Tech Stack:** Django REST (Python 3.12), pytest/Django TestCase; React + Vite + TypeScript, @tanstack/react-query, shadcn/ui, vitest + @testing-library/react.

## Global Constraints

- Spec nguồn: `docs/superpowers/specs/2026-06-17-inventory-stock-by-warehouse-history-design.md`.
- Backend clean architecture: query logic ở `application/services`, serializer ở `transport/serializers`, view ở `transport/views`. Không sửa luồng ghi sổ `record_movement`.
- Backend giữ tương thích ngược: chỉ THÊM field vào `StockMovementSerializer`, không bỏ/đổi field cũ.
- Frontend: TypeScript, shadcn/ui (không tự code style), light theme xanh da trời, lucide-react icons, feature-slice trong `frontend/src/features/inventory`.
- Tiếng Việt cho mọi label hiển thị.
- Chạy test backend theo memory `running-backend-tests`: dùng ephemeral `docker run` trên network `desi-order_default`, KHÔNG dùng image prod (thiếu tests/ruff). Pytest, không `manage.py test`.
- Commit message tiếng Anh, ngắn gọn, KHÔNG kèm dòng "Co-Authored-By".

## Reference mapping (dùng xuyên suốt)

| `reference_type` | Model | Mã hiển thị |
|---|---|---|
| `order` | `apps.orders.models.Order` | `order_code` → `invoice_code` → `f"#{id}"` |
| `document` | `apps.inventory.models.StockDocument` | `code` |
| `return` | `apps.inventory.models.ReturnNote` | `code` |
| `reclass`/khác | — | `None` (FE: "—") |

Order detail route (cho link mã đơn): `/sales/orders/:orderId` với `orderId = reference_id`.

---

## Task 1: Backend — enrich `reference_label` cho movements

**Files:**
- Modify: `backend/apps/inventory/application/services/stock_query_service.py`
- Modify: `backend/apps/inventory/transport/serializers/stock_serializers.py:22-32`
- Modify: `backend/apps/inventory/transport/views/stock_views.py:84-104`
- Test: `backend/apps/inventory/tests/test_stock_query_service.py`

**Interfaces:**
- Produces: `StockQueryService.resolve_reference_labels(movements) -> dict[tuple[str, int], str]`
  — nhận iterable `StockMovement`, trả map `{(reference_type, reference_id): label}`, batch tối đa 1 query/loại.
- Produces: `StockMovementSerializer` thêm field `reference_label` (string|null), đọc từ
  `context["reference_labels"]`.
- Consumes (FE Task 2): JSON movement item nay có thêm `reference_label`.

- [ ] **Step 1: Viết test thất bại cho `resolve_reference_labels`**

Thêm vào cuối class `StockQueryTests` trong `backend/apps/inventory/tests/test_stock_query_service.py`
(thêm import ở đầu file: `from apps.customers.models import Customer`,
`from apps.orders.models import Order`,
`from apps.inventory.models import StockDocument`):

```python
    def test_resolve_reference_labels_order_document_and_fallback(self):
        customer = Customer.objects.create(
            full_name="Khách", phone_raw="", phone_normalized="")
        order = Order.objects.create(customer=customer, order_code="DH-001")
        order_no_code = Order.objects.create(customer=customer)
        doc = StockDocument.objects.create(
            code="PN-001", doc_type="receipt", warehouse=self.w1)
        StockCommandService.record_movement(
            warehouse_id=self.w1.id, product_id=self.p1.id, qty_delta=-1,
            movement_type=MovementType.SALE,
            reference_type="order", reference_id=order.id)
        StockCommandService.record_movement(
            warehouse_id=self.w1.id, product_id=self.p1.id, qty_delta=-1,
            movement_type=MovementType.SALE,
            reference_type="order", reference_id=order_no_code.id)
        StockCommandService.record_movement(
            warehouse_id=self.w1.id, product_id=self.p1.id, qty_delta=5,
            movement_type=MovementType.RECEIPT,
            reference_type="document", reference_id=doc.id)
        StockCommandService.record_movement(
            warehouse_id=self.w1.id, product_id=self.p1.id, qty_delta=2,
            movement_type=MovementType.ADJUSTMENT,
            reference_type="reclass", reference_id=999)

        movements = list(StockQueryService.list_movements(warehouse_id=self.w1.id))
        labels = StockQueryService.resolve_reference_labels(movements)

        assert labels[("order", order.id)] == "DH-001"
        assert labels[("order", order_no_code.id)] == f"#{order_no_code.id}"
        assert labels[("document", doc.id)] == "PN-001"
        assert ("reclass", 999) not in labels

    def test_resolve_reference_labels_batches_orders_in_one_query(self):
        customer = Customer.objects.create(
            full_name="Khách", phone_raw="", phone_normalized="")
        o1 = Order.objects.create(customer=customer, order_code="DH-1")
        o2 = Order.objects.create(customer=customer, order_code="DH-2")
        for oid in (o1.id, o2.id):
            StockCommandService.record_movement(
                warehouse_id=self.w1.id, product_id=self.p1.id, qty_delta=-1,
                movement_type=MovementType.SALE,
                reference_type="order", reference_id=oid)
        movements = [m for m in StockQueryService.list_movements(warehouse_id=self.w1.id)
                     if m.reference_type == "order"]
        with self.assertNumQueries(1):
            StockQueryService.resolve_reference_labels(movements)
```

> Lưu ý: `record_movement` đã hỗ trợ `reference_type`/`reference_id` (xem các service hiện có).
> Nếu chữ ký khác, điều chỉnh call cho khớp `StockCommandService.record_movement`.

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Theo memory `running-backend-tests` (ephemeral docker run trên `desi-order_default`). Ví dụ:

Run: `docker run --rm --network desi-order_default -v "$PWD/backend:/app" -w /app <test-image> pytest apps/inventory/tests/test_stock_query_service.py -k resolve_reference_labels -v`
Expected: FAIL — `AttributeError: type object 'StockQueryService' has no attribute 'resolve_reference_labels'`

- [ ] **Step 3: Cài đặt `resolve_reference_labels`**

Thêm vào `backend/apps/inventory/application/services/stock_query_service.py` (trong class `StockQueryService`):

```python
    @staticmethod
    def resolve_reference_labels(movements) -> dict[tuple[str, int], str]:
        from apps.orders.models import Order
        from apps.inventory.models import ReturnNote, StockDocument

        ids_by_type: dict[str, set[int]] = {}
        for m in movements:
            if m.reference_type and m.reference_id:
                ids_by_type.setdefault(m.reference_type, set()).add(m.reference_id)

        labels: dict[tuple[str, int], str] = {}

        order_ids = ids_by_type.get("order")
        if order_ids:
            for row in Order.objects.filter(id__in=order_ids).values(
                "id", "order_code", "invoice_code"
            ):
                labels[("order", row["id"])] = (
                    row["order_code"] or row["invoice_code"] or f"#{row['id']}"
                )

        doc_ids = ids_by_type.get("document")
        if doc_ids:
            for row in StockDocument.objects.filter(id__in=doc_ids).values("id", "code"):
                labels[("document", row["id"])] = row["code"]

        return_ids = ids_by_type.get("return")
        if return_ids:
            for row in ReturnNote.objects.filter(id__in=return_ids).values("id", "code"):
                labels[("return", row["id"])] = row["code"]

        return labels
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `docker run --rm --network desi-order_default -v "$PWD/backend:/app" -w /app <test-image> pytest apps/inventory/tests/test_stock_query_service.py -k resolve_reference_labels -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Thêm field `reference_label` vào serializer**

Sửa `backend/apps/inventory/transport/serializers/stock_serializers.py` — class `StockMovementSerializer`:

```python
class StockMovementSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    reference_label = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            "id", "warehouse", "warehouse_name", "product", "product_sku",
            "qty_delta", "movement_type", "state", "balance_after", "reference_type",
            "reference_id", "reference_label", "note", "created_at",
        ]

    def get_reference_label(self, obj):
        labels = self.context.get("reference_labels") or {}
        return labels.get((obj.reference_type, obj.reference_id))
```

- [ ] **Step 6: Truyền context trong view `list_movements`**

Sửa `backend/apps/inventory/transport/views/stock_views.py`, hàm `list_movements`, đoạn tạo `items`/`Response`:

```python
    total = qs.count()
    items = list(qs[pagination["offset"] : pagination["offset"] + pagination["limit"]])
    reference_labels = StockQueryService.resolve_reference_labels(items)
    return Response(
        {
            "items": StockMovementSerializer(
                items, many=True, context={"reference_labels": reference_labels}
            ).data,
            "total": total,
            "limit": pagination["limit"],
            "offset": pagination["offset"],
        },
        status=status.HTTP_200_OK,
    )
```

- [ ] **Step 7: Chạy toàn bộ test inventory + ruff**

Run: `docker run --rm --network desi-order_default -v "$PWD/backend:/app" -w /app <test-image> pytest apps/inventory/tests/test_stock_query_service.py apps/inventory/tests/test_stock_api.py -v`
Expected: PASS toàn bộ
Run: `docker run --rm -v "$PWD/backend:/app" -w /app <test-image> ruff check apps/inventory`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add backend/apps/inventory/application/services/stock_query_service.py \
        backend/apps/inventory/transport/serializers/stock_serializers.py \
        backend/apps/inventory/transport/views/stock_views.py \
        backend/apps/inventory/tests/test_stock_query_service.py
git commit -m "feat(inventory): enrich movement reference label"
```

---

## Task 2: Frontend — types, labels, hook & component lịch sử

**Files:**
- Modify: `frontend/src/features/inventory/types.ts`
- Modify: `frontend/src/features/inventory/queries.ts:6-9` (keys) và cuối file (hook)
- Create: `frontend/src/features/inventory/components/StockMovementHistory.tsx`
- Test: `frontend/src/features/inventory/__tests__/StockMovementHistory.test.tsx`

**Interfaces:**
- Consumes (Task 1): movement JSON có `reference_label: string | null`.
- Produces: type `StockMovementRow`, `StockMovementListResponse`, const `MOVEMENT_TYPE_LABELS`,
  `STATE_LABELS`.
- Produces: `useStockMovements(warehouseId: number | null, productId: number | null, enabled: boolean)`
  → react-query trả `StockMovementListResponse`.
- Produces: component `<StockMovementHistory rows={StockMovementRow[]} isLoading={boolean} colSpan={number} />`
  (presentational, render `<tr>` con — consumer bọc trong `<tbody>`/`<table>`).

- [ ] **Step 1: Thêm types & labels**

Thêm vào cuối `frontend/src/features/inventory/types.ts`:

```typescript
export type StockMovementRow = {
  id: number;
  warehouse: number;
  warehouse_name: string;
  product: number;
  product_sku: string;
  qty_delta: number;
  movement_type: string;
  state: "sellable" | "warranty" | "defect";
  balance_after: number;
  reference_type: string | null;
  reference_id: number | null;
  reference_label: string | null;
  note: string;
  created_at: string;
};

export type StockMovementListResponse = {
  items: StockMovementRow[];
  total: number;
  limit: number;
  offset: number;
};

export const MOVEMENT_TYPE_LABELS: Record<string, string> = {
  receipt: "Nhập kho",
  issue: "Xuất kho",
  adjustment: "Điều chỉnh",
  transfer_in: "Nhập chuyển kho",
  transfer_out: "Xuất chuyển kho",
  sale: "Bán hàng",
  sale_reversal: "Hoàn bán",
  return_in: "Nhập trả hàng",
  reclass_in: "Xử lý (vào)",
  reclass_out: "Xử lý (ra)",
  disposal: "Xuất huỷ",
};

export const STATE_LABELS: Record<StockMovementRow["state"], string> = {
  sellable: "Bán được",
  warranty: "Bảo hành",
  defect: "Lỗi",
};
```

- [ ] **Step 2: Thêm query key & hook `useStockMovements`**

Trong `frontend/src/features/inventory/queries.ts` — thêm key vào object `inventoryKeys`:

```typescript
  movements: (warehouseId: number | null, productId: number | null) =>
    ["inventory", "movements", warehouseId, productId] as const,
```

Cập nhật import type ở đầu file để gồm `StockMovementListResponse`, rồi thêm hook ở cuối file:

```typescript
export function useStockMovements(
  warehouseId: number | null,
  productId: number | null,
  enabled: boolean,
) {
  const params = new URLSearchParams({ limit: "10", offset: "0" });
  if (warehouseId) params.set("warehouse_id", String(warehouseId));
  if (productId) params.set("product_id", String(productId));
  return useQuery({
    queryKey: inventoryKeys.movements(warehouseId, productId),
    queryFn: () =>
      fetchJson(
        `/api/inventory/stock/movements?${params.toString()}`,
      ) as Promise<StockMovementListResponse>,
    enabled: enabled && warehouseId != null && productId != null,
  });
}
```

- [ ] **Step 3: Viết test thất bại cho `StockMovementHistory`**

Tạo `frontend/src/features/inventory/__tests__/StockMovementHistory.test.tsx`:

```tsx
import { describe, it, expect, afterEach } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { StockMovementHistory } from "@/features/inventory/components/StockMovementHistory";
import type { StockMovementRow } from "@/features/inventory/types";

const baseRow: StockMovementRow = {
  id: 1, warehouse: 1, warehouse_name: "Kho HN", product: 2, product_sku: "SKU-1",
  qty_delta: -3, movement_type: "sale", state: "sellable", balance_after: 7,
  reference_type: "order", reference_id: 55, reference_label: "DH-001",
  note: "", created_at: "2026-06-17T03:30:00Z",
};

function renderRows(rows: StockMovementRow[], isLoading = false) {
  return render(
    <MemoryRouter>
      <table><tbody>
        <StockMovementHistory rows={rows} isLoading={isLoading} colSpan={9} />
      </tbody></table>
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("StockMovementHistory", () => {
  it("renders movement type label, signed qty, balance and reference", () => {
    renderRows([baseRow]);
    expect(screen.getByText("Bán hàng")).toBeInTheDocument();
    expect(screen.getByText("Bán được")).toBeInTheDocument();
    expect(screen.getByText("-3")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("DH-001")).toBeInTheDocument();
  });

  it("shows dash when reference_label is null", () => {
    renderRows([{ ...baseRow, reference_type: "reclass", reference_label: null }]);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders positive qty with plus sign", () => {
    renderRows([{ ...baseRow, qty_delta: 5, movement_type: "receipt" }]);
    expect(screen.getByText("+5")).toBeInTheDocument();
  });

  it("shows empty message when no rows", () => {
    renderRows([]);
    expect(screen.getByText("Chưa có giao dịch.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Chạy test để xác nhận FAIL**

Run: `cd frontend && npx vitest run src/features/inventory/__tests__/StockMovementHistory.test.tsx`
Expected: FAIL — không resolve được module `StockMovementHistory`.

- [ ] **Step 5: Cài đặt component `StockMovementHistory`**

Tạo `frontend/src/features/inventory/components/StockMovementHistory.tsx`:

```tsx
import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import {
  MOVEMENT_TYPE_LABELS,
  STATE_LABELS,
  type StockMovementRow,
} from "@/features/inventory/types";

interface Props {
  rows: StockMovementRow[];
  isLoading: boolean;
  colSpan: number;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("vi-VN");
}

function qtyText(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}

function ReferenceCell({ row }: { row: StockMovementRow }) {
  if (!row.reference_label) return <span className="text-muted-foreground">—</span>;
  if (row.reference_type === "order" && row.reference_id != null) {
    return (
      <Link className="text-primary hover:underline" to={`/sales/orders/${row.reference_id}`}>
        {row.reference_label}
      </Link>
    );
  }
  return <span>{row.reference_label}</span>;
}

export function StockMovementHistory({ rows, isLoading, colSpan }: Props) {
  if (isLoading) {
    return (
      <tr>
        <td colSpan={colSpan} className="bg-muted/30 px-6 py-3 text-sm text-muted-foreground">
          Đang tải…
        </td>
      </tr>
    );
  }
  if (rows.length === 0) {
    return (
      <tr>
        <td colSpan={colSpan} className="bg-muted/30 px-6 py-3 text-sm text-muted-foreground">
          Chưa có giao dịch.
        </td>
      </tr>
    );
  }
  return (
    <tr>
      <td colSpan={colSpan} className="bg-muted/30 px-6 py-3">
        <table className="w-full text-xs">
          <thead className="text-left text-muted-foreground">
            <tr>
              <th className="px-2 py-1">Thời gian</th>
              <th className="px-2 py-1">Mã hoá đơn</th>
              <th className="px-2 py-1">Loại giao dịch</th>
              <th className="px-2 py-1 text-right">Số lượng</th>
              <th className="px-2 py-1 text-right">Tồn cuối</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className="border-t border-border/60">
                <td className="px-2 py-1 whitespace-nowrap">{formatTime(row.created_at)}</td>
                <td className="px-2 py-1"><ReferenceCell row={row} /></td>
                <td className="px-2 py-1">
                  <span>{MOVEMENT_TYPE_LABELS[row.movement_type] ?? row.movement_type}</span>
                  <Badge variant="outline" className="ml-2">{STATE_LABELS[row.state]}</Badge>
                </td>
                <td
                  className={`px-2 py-1 text-right font-medium ${
                    row.qty_delta > 0 ? "text-emerald-600" : "text-destructive"
                  }`}
                >
                  {qtyText(row.qty_delta)}
                </td>
                <td className="px-2 py-1 text-right">{row.balance_after}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </td>
    </tr>
  );
}
```

- [ ] **Step 6: Chạy test để xác nhận PASS**

Run: `cd frontend && npx vitest run src/features/inventory/__tests__/StockMovementHistory.test.tsx`
Expected: PASS (4 tests)

- [ ] **Step 7: Lint/typecheck**

Run: `cd frontend && npx tsc --noEmit && npx eslint src/features/inventory/components/StockMovementHistory.tsx src/features/inventory/types.ts src/features/inventory/queries.ts`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/inventory/types.ts \
        frontend/src/features/inventory/queries.ts \
        frontend/src/features/inventory/components/StockMovementHistory.tsx \
        frontend/src/features/inventory/__tests__/StockMovementHistory.test.tsx
git commit -m "feat(inventory): movement history component + hook"
```

---

## Task 3: Frontend — bộ lọc kho + accordion trong `StockPage`

**Files:**
- Modify: `frontend/src/features/inventory/pages/StockPage.tsx`
- Modify: `frontend/src/features/inventory/components/StockCard.tsx`
- Test: `frontend/src/features/inventory/__tests__/StockCard.test.tsx`

**Interfaces:**
- Consumes (Task 2): `useStockMovements`, `StockMovementHistory`, `StockMovementRow`.
- Consumes: `getWarehouses()` từ `@/lib/api/warehouses`; `Select*` từ `@/components/ui/select`.
- Produces: `StockCard` thêm prop optional `onToggleHistory?: (row: StockPivotRow) => void`
  và `expanded?: boolean`; nút "Xem lịch sử" gọi `onToggleHistory`.

- [ ] **Step 1: Thêm warehouse Select + state vào `StockPage`**

Trong `frontend/src/features/inventory/pages/StockPage.tsx`:
- Thêm imports:

```tsx
import { useEffect } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { getWarehouses } from "@/lib/api/warehouses";
import { useStockMovements } from "@/features/inventory/queries";
import { StockMovementHistory } from "@/features/inventory/components/StockMovementHistory";
```

- Trong component, thêm state + load kho (mặc định kho đầu tiên):

```tsx
  const [warehouseId, setWarehouseId] = useState<number | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const { data: whData } = useQuery({
    queryKey: ["warehouses", "all-active"],
    queryFn: () => getWarehouses({ isActive: true, limit: 200 }),
  });
  const warehouses = useMemo(
    () => (whData?.items ?? []).slice().sort((a, b) => a.name.localeCompare(b.name)),
    [whData?.items],
  );

  useEffect(() => {
    if (warehouseId == null && warehouses.length > 0) setWarehouseId(warehouses[0].id);
  }, [warehouseId, warehouses]);
```

- Đổi `useStockList(null, search)` → `useStockList(warehouseId, search)`.

- [ ] **Step 2: Render Select trong header**

Thay khối header (đoạn `<div className="flex items-center justify-between">…`) bằng:

```tsx
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-foreground">{t("topnav.tabs.stock")}</h1>
        <div className="flex items-center gap-2">
          <Select
            value={warehouseId != null ? String(warehouseId) : undefined}
            onValueChange={(v) => {
              setWarehouseId(Number(v));
              setExpandedKey(null);
            }}
          >
            <SelectTrigger className="w-48">
              <SelectValue placeholder="Chọn kho" />
            </SelectTrigger>
            <SelectContent>
              {warehouses.map((w) => (
                <SelectItem key={w.id} value={String(w.id)}>
                  {w.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("common.search", "Tìm SKU / tên")}
            className="rounded-lg border border-border bg-white px-3 py-2 text-sm outline-none focus:border-primary"
          />
        </div>
      </div>
```

- [ ] **Step 3: Bỏ cột "Kho", thêm cột expand, render accordion**

`colSpan` đổi từ `8` → `9` (bỏ Kho nhưng thêm cột mũi tên: 8 - 1 + 1 = 8 cũ... tính lại: cột mới = [mũi tên, SKU, Sản phẩm, Bán được, Bảo hành, Lỗi, Tổng, Thao tác] = 8). Đặt `const colSpan = 8;`.

Sửa `<thead>`: bỏ `<th>Kho</th>`, thêm `<th className="w-8" />` ở đầu.

Sửa thân bảng: bỏ `<td>{r.warehouse_name}</td>`, thêm ô mũi tên + cho dòng click toggle, và render `StockMovementHistory` ngay sau mỗi dòng đang mở. Thay khối `pivotRows.map(...)` bằng:

```tsx
                {pivotRows.map((r) => (
                  <ExpandableStockRow
                    key={r.key}
                    row={r}
                    warehouseId={warehouseId}
                    expanded={expandedKey === r.key}
                    onToggle={() =>
                      setExpandedKey((cur) => (cur === r.key ? null : r.key))
                    }
                    onResolve={handleResolve}
                    colSpan={colSpan}
                  />
                ))}
```

Thêm component `ExpandableStockRow` trong cùng file (trên `export default`):

```tsx
function ExpandableStockRow({
  row, warehouseId, expanded, onToggle, onResolve, colSpan,
}: {
  row: PivotRow;
  warehouseId: number | null;
  expanded: boolean;
  onToggle: () => void;
  onResolve: (r: PivotRow) => void;
  colSpan: number;
}) {
  const { data, isLoading } = useStockMovements(warehouseId, row.product, expanded);
  return (
    <>
      <tr className="cursor-pointer border-t border-border hover:bg-muted/40" onClick={onToggle}>
        <td className="px-2 py-2 text-muted-foreground">
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </td>
        <td className="px-3 py-2 font-medium">{row.product_sku}</td>
        <td className="px-3 py-2">{row.product_name}</td>
        <td className={`px-3 py-2 text-right ${qtyClass(row.sellable)}`}>{row.sellable}</td>
        <td className={`px-3 py-2 text-right ${qtyClass(row.warranty, "warranty")}`}>{row.warranty}</td>
        <td className={`px-3 py-2 text-right ${qtyClass(row.defect, "defect")}`}>{row.defect}</td>
        <td className={`px-3 py-2 text-right ${qtyClass(row.total)}`}>{row.total}</td>
        <td className="px-3 py-2 text-center">
          {(row.warranty > 0 || row.defect > 0) && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 px-2 text-xs"
              onClick={(e) => {
                e.stopPropagation();
                onResolve(row);
              }}
            >
              <Wrench className="mr-1 h-3 w-3" />
              Xử lý
            </Button>
          )}
        </td>
      </tr>
      {expanded && (
        <StockMovementHistory rows={data?.items ?? []} isLoading={isLoading} colSpan={colSpan} />
      )}
    </>
  );
}
```

Cập nhật `<thead>` cho khớp 8 cột (mũi tên, SKU, Sản phẩm, Bán được, Bảo hành, Lỗi, Tổng, Thao tác); và 2 dòng trạng thái loading/empty đổi `colSpan={colSpan}` (=8).

- [ ] **Step 4: Mobile — `StockCard` mở lịch sử**

Trong `frontend/src/features/inventory/components/StockCard.tsx`:
- Thêm prop:

```tsx
interface StockCardProps {
  row: StockPivotRow;
  onResolve: (row: StockPivotRow) => void;
  onToggleHistory?: (row: StockPivotRow) => void;
  expanded?: boolean;
  children?: React.ReactNode;
}
```

- Import: `import { ChevronDown, ChevronRight, Wrench } from "lucide-react";` và `import type { ReactNode } from "react";` (dùng `ReactNode` thay `React.ReactNode`).
- Trong phần footer (cạnh nút Xử lý), thêm nút lịch sử và render `children` khi `expanded`:

```tsx
      <div className="mt-3 flex items-center justify-between gap-2">
        {onToggleHistory ? (
          <button
            type="button"
            className="inline-flex items-center gap-1 text-xs text-primary"
            onClick={() => onToggleHistory(row)}
          >
            {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            Lịch sử
          </button>
        ) : <span />}
        {canResolve && (
          <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => onResolve(row)}>
            <Wrench className="mr-1 h-3 w-3" />
            Xử lý
          </Button>
        )}
      </div>
      {expanded && children}
```

(Bỏ khối `{canResolve && (<div className="mt-3 flex justify-end">…)}` cũ.)

- Trong `StockPage.tsx`, `renderCard` truyền props mobile: bọc `StockMovementHistory` dạng list dọc. Để tái dùng, `renderCard` dùng cùng `expandedKey`/`useStockMovements` qua một wrapper card nhỏ `ExpandableStockCard` (đặt cạnh `ExpandableStockRow`):

```tsx
function ExpandableStockCard({
  row, warehouseId, expanded, onToggle, onResolve,
}: {
  row: PivotRow;
  warehouseId: number | null;
  expanded: boolean;
  onToggle: () => void;
  onResolve: (r: PivotRow) => void;
}) {
  const { data, isLoading } = useStockMovements(warehouseId, row.product, expanded);
  return (
    <StockCard row={row} onResolve={onResolve} onToggleHistory={onToggle} expanded={expanded}>
      <div className="mt-2 space-y-2 border-t border-border pt-2 text-xs">
        {isLoading && <p className="text-muted-foreground">Đang tải…</p>}
        {!isLoading && (data?.items ?? []).length === 0 && (
          <p className="text-muted-foreground">Chưa có giao dịch.</p>
        )}
        {(data?.items ?? []).map((m) => (
          <div key={m.id} className="rounded-md bg-muted/40 px-2 py-1.5">
            <div className="flex justify-between">
              <span>{MOVEMENT_TYPE_LABELS[m.movement_type] ?? m.movement_type}</span>
              <span className={m.qty_delta > 0 ? "text-emerald-600" : "text-destructive"}>
                {m.qty_delta > 0 ? `+${m.qty_delta}` : m.qty_delta}
              </span>
            </div>
            <div className="flex justify-between text-muted-foreground">
              <span>{m.reference_label ?? "—"}</span>
              <span>Tồn: {m.balance_after}</span>
            </div>
            <div className="text-muted-foreground">{new Date(m.created_at).toLocaleString("vi-VN")}</div>
          </div>
        ))}
      </div>
    </StockCard>
  );
}
```

Và `renderCard={(r) => (<ExpandableStockCard row={r} warehouseId={warehouseId} expanded={expandedKey === r.key} onToggle={() => setExpandedKey((c) => (c === r.key ? null : r.key))} onResolve={handleResolve} />)}`.
Thêm import `MOVEMENT_TYPE_LABELS` vào `StockPage.tsx`.

- [ ] **Step 5: Cập nhật/chạy test `StockCard` (đảm bảo không vỡ)**

Thêm test vào `frontend/src/features/inventory/__tests__/StockCard.test.tsx`:

```tsx
  it("calls onToggleHistory when history button clicked", () => {
    const onToggle = vi.fn();
    renderCard(<StockCard row={mockRow} onResolve={vi.fn()} onToggleHistory={onToggle} />);
    fireEvent.click(screen.getByRole("button", { name: /Lịch sử/ }));
    expect(onToggle).toHaveBeenCalledWith(mockRow);
  });
```

> Nút "Lịch sử" dùng `<button>` nên `getByRole("button", { name: /Lịch sử/ })` hợp lệ.

Run: `cd frontend && npx vitest run src/features/inventory/__tests__/StockCard.test.tsx`
Expected: PASS (4 tests)

- [ ] **Step 6: Typecheck + lint toàn feature**

Run: `cd frontend && npx tsc --noEmit && npx eslint src/features/inventory`
Expected: no errors

- [ ] **Step 7: Build kiểm tra**

Run: `cd frontend && npx vite build`
Expected: build thành công

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/inventory/pages/StockPage.tsx \
        frontend/src/features/inventory/components/StockCard.tsx \
        frontend/src/features/inventory/__tests__/StockCard.test.tsx
git commit -m "feat(inventory): warehouse filter + stock history accordion"
```

---

## Self-Review

**Spec coverage:**
- Bộ lọc kho mặc định kho đầu (D1) → Task 3 Step 1-2. ✔
- Enrich mã hoá đơn ở backend (D2) → Task 1. ✔
- Badge trạng thái trong lịch sử (D3) → Task 2 (component) Step 5. ✔
- 10 giao dịch gần nhất → Task 2 hook `limit=10`. ✔
- 5 cột (thời gian, mã hoá đơn, loại giao dịch, số lượng ±, tồn cuối) → Task 2 component. ✔
- Lazy fetch khi mở → Task 2 hook `enabled`, Task 3 `ExpandableStockRow`. ✔
- Responsive mobile → Task 3 Step 4 (`ExpandableStockCard` list dọc). ✔
- Testing BE (enrich + no N+1) → Task 1 Step 1. FE render → Task 2 Step 3, Task 3 Step 5. ✔

**Placeholder scan:** Không có TBD/TODO; mọi step có code/command cụ thể. `<test-image>` là tên image động theo memory `running-backend-tests` — không phải placeholder code mà là tham số môi trường người chạy điền (đã ghi rõ ở Global Constraints).

**Type consistency:** `useStockMovements(warehouseId, productId, enabled)` — chữ ký nhất quán giữa Task 2 (định nghĩa) và Task 3 (sử dụng). `StockMovementHistory` props `{rows, isLoading, colSpan}` nhất quán Task 2 ↔ Task 3. `StockCard` thêm `onToggleHistory/expanded/children` nhất quán Task 3 Step 4 ↔ Step 5. `colSpan = 8` thống nhất trong Task 3.
