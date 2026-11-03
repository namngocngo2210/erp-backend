# Warranty Table: Columns + Inline Edit + Detail Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the warranty claims list behave like the orders table — a dynamic show/hide/reorder/pin column system (persisted to localStorage), inline-editable cells for the claim's business fields (typed editors, PATCH per change), row-click to a detail page `/sales/warranty/:id`, and no edit/action column.

**Architecture:** A warranty-scoped column system (config + localStorage + hook + dnd-kit picker, replicated rather than coupled to the orders implementation). A new `WarrantyClaimsTable` renders columns dynamically with inline cell editors that call an `onPatch` callback (wired to the existing `useUpdateWarrantyClaim` PATCH mutation). A new full-page detail route reuses the existing `GET/DELETE /api/warranty/claims/:id` and the existing `WarrantyClaimFormDialog` for editing. Backend is unchanged.

**Tech Stack:** React 19 + TypeScript (Vite), @tanstack/react-query, @dnd-kit/core + @dnd-kit/sortable, shadcn/ui, lucide-react, sonner, vitest + @testing-library/react.

## Global Constraints

- TypeScript; functional components + hooks; shadcn/ui; light theme (blue); Vietnamese UI copy; `lucide-react` icons.
- Order-derived columns (customer/phone/address/product/sku/amount/shipping_code/order_code) are READ-ONLY. Only the claim's own business fields are inline-editable.
- Inline editors by data kind: `text` → input (save on blur/Enter, Esc cancels, PATCH only if changed); `status` → `<select>` dropdown; `require_carrier_call` (bool) → `Switch`. Each change PATCHes via the existing update mutation.
- Editable cells must `stopPropagation` so editing does not trigger row navigation. Row click navigates only when `window.getSelection()?.toString()` is empty.
- Column system: visible + locked (pinned left, sticky) + drag-reorder, persisted to localStorage keys `desi_warranty_visible_columns`, `desi_warranty_locked_columns`, `desi_warranty_column_order`.
- Do NOT modify the orders implementation; do NOT import `OrderColumnKey`-typed pieces — replicate a small warranty column item (decoupled).
- Keep `ResponsiveList` mobile card (read-only, tap → detail). Keep the create dialog ("Thêm bảo hành").
- Backend unchanged. Commit messages English, short. Branch `feat/warranty-table-inline-edit` (already created).
- FE checks: `cd frontend && npm run typecheck` (exit 0) and `npx vitest run <path>`; final `npm run build`. (`node_modules` already installed; baseline clean.)

---

## File Structure

**Create:**
- `frontend/src/features/warranty-claims/warranty-claim-columns.ts` — column keys/options/defaults + display-value helper
- `frontend/src/features/warranty-claims/warranty-claims-storage.ts` — localStorage get/set (pure functions)
- `frontend/src/features/warranty-claims/useWarrantyColumns.ts` — column state hook
- `frontend/src/features/warranty-claims/components/WarrantyColumnItem.tsx` — one picker row (replica)
- `frontend/src/features/warranty-claims/components/WarrantyColumnPicker.tsx` — "Cột" dropdown
- `frontend/src/features/warranty-claims/components/WarrantyClaimsTable.tsx` — dynamic table + inline cells
- `frontend/src/pages/WarrantyClaimDetailPage.tsx` — detail page
- Tests under `frontend/src/features/warranty-claims/__tests__/`

**Modify:**
- `frontend/src/lib/api/warrantyClaims.ts` — add `getWarrantyClaim`
- `frontend/src/features/warranty-claims/queries.ts` — add `useWarrantyClaim`
- `frontend/src/app/routes.tsx` — add `/sales/warranty/:claimId` route
- `frontend/src/app/routed-pages.tsx` — add `WarrantyClaimDetailRoute`
- `frontend/src/pages/WarrantyClaimsPage.tsx` — toolbar + table wiring, remove row actions

---

### Task 1: Column config + display-value helper

**Files:**
- Create: `frontend/src/features/warranty-claims/warranty-claim-columns.ts`
- Test: `frontend/src/features/warranty-claims/__tests__/warranty-claim-columns.test.ts`

**Interfaces:**
- Consumes: `WarrantyClaim` type from `@/features/warranty-claims/warranty-claim-types`.
- Produces:
  - `type WarrantyColumnKey` (string union below).
  - `type WarrantyColumnKind = "readonly" | "text" | "status" | "bool"`.
  - `WARRANTY_COLUMN_OPTIONS: { key: WarrantyColumnKey; label: string; kind: WarrantyColumnKind }[]`.
  - `DEFAULT_WARRANTY_COLUMNS: WarrantyColumnKey[]`.
  - `STATUS_BADGE_CLASS: Record<1|2|3|4, string>`.
  - `columnDisplayValue(row: WarrantyClaim, key: WarrantyColumnKey): string` — formats a cell's read display (money/date/dash for empty).

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/warranty-claims/__tests__/warranty-claim-columns.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import {
  DEFAULT_WARRANTY_COLUMNS,
  WARRANTY_COLUMN_OPTIONS,
  columnDisplayValue,
} from "@/features/warranty-claims/warranty-claim-columns";
import type { WarrantyClaim } from "@/features/warranty-claims/warranty-claim-types";

const claim = {
  id: 1,
  order: {
    id: 5,
    name: "Nguyen A",
    phone: "0901",
    address: "Addr",
    product: "Ao",
    sku: "S1",
    quantity: 1,
    total_after_discount: "150000.00",
    shipping_code: "VD1",
    order_code: "OD1",
  },
  status: 2,
  status_label: "Đang xử lý",
  contact_channel: "Shopee",
  require_carrier_call: true,
  social_nickname: "",
  send_reason: "Lỗi",
  send_method: "",
  classification: "Đổi",
  recall_code: "",
  return_status: "",
  accounting_check: "",
  accounting_note: "",
  created_by_name: "",
  created_at: "2026-06-24T10:00:00Z",
  updated_at: "2026-06-24T10:00:00Z",
} as WarrantyClaim;

describe("warranty-claim-columns", () => {
  it("default columns are all valid option keys", () => {
    const keys = new Set(WARRANTY_COLUMN_OPTIONS.map((o) => o.key));
    for (const k of DEFAULT_WARRANTY_COLUMNS) expect(keys.has(k)).toBe(true);
  });

  it("each option has a kind and label", () => {
    for (const o of WARRANTY_COLUMN_OPTIONS) {
      expect(o.label.length).toBeGreaterThan(0);
      expect(["readonly", "text", "status", "bool"]).toContain(o.kind);
    }
  });

  it("columnDisplayValue reads order + claim fields", () => {
    expect(columnDisplayValue(claim, "customer")).toBe("Nguyen A");
    expect(columnDisplayValue(claim, "shipping_code")).toBe("VD1");
    expect(columnDisplayValue(claim, "classification")).toBe("Đổi");
    expect(columnDisplayValue(claim, "status")).toBe("Đang xử lý");
    expect(columnDisplayValue(claim, "amount")).toContain("150");
    expect(columnDisplayValue(claim, "social_nickname")).toBe("—");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`):
```
npx vitest run src/features/warranty-claims/__tests__/warranty-claim-columns.test.ts
```
Expected: FAIL — cannot resolve `warranty-claim-columns`.

- [ ] **Step 3: Create the column config**

Create `frontend/src/features/warranty-claims/warranty-claim-columns.ts`:

```ts
import type { WarrantyClaim } from "@/features/warranty-claims/warranty-claim-types";

export type WarrantyColumnKind = "readonly" | "text" | "status" | "bool";

export type WarrantyColumnKey =
  | "customer"
  | "phone"
  | "address"
  | "product"
  | "sku"
  | "amount"
  | "shipping_code"
  | "order_code"
  | "status"
  | "contact_channel"
  | "require_carrier_call"
  | "social_nickname"
  | "send_reason"
  | "send_method"
  | "classification"
  | "recall_code"
  | "return_status"
  | "accounting_check"
  | "accounting_note"
  | "created_by_name"
  | "created_at";

export const WARRANTY_COLUMN_OPTIONS: {
  key: WarrantyColumnKey;
  label: string;
  kind: WarrantyColumnKind;
}[] = [
  { key: "customer", label: "Khách hàng", kind: "readonly" },
  { key: "phone", label: "SĐT", kind: "readonly" },
  { key: "address", label: "Địa chỉ", kind: "readonly" },
  { key: "product", label: "Sản phẩm", kind: "readonly" },
  { key: "sku", label: "SKU", kind: "readonly" },
  { key: "amount", label: "Số tiền", kind: "readonly" },
  { key: "shipping_code", label: "Mã vận đơn", kind: "readonly" },
  { key: "order_code", label: "Mã đơn", kind: "readonly" },
  { key: "status", label: "Trạng thái", kind: "status" },
  { key: "contact_channel", label: "Kênh liên hệ", kind: "text" },
  { key: "require_carrier_call", label: "YC gọi xác nhận", kind: "bool" },
  { key: "social_nickname", label: "Nick MXH", kind: "text" },
  { key: "send_reason", label: "Lí do gửi", kind: "text" },
  { key: "send_method", label: "Hình thức gửi", kind: "text" },
  { key: "classification", label: "Phân loại", kind: "text" },
  { key: "recall_code", label: "Mã thu hồi", kind: "text" },
  { key: "return_status", label: "Tình trạng đơn hoàn", kind: "text" },
  { key: "accounting_check", label: "Kế toán check", kind: "text" },
  { key: "accounting_note", label: "Kế toán note", kind: "text" },
  { key: "created_by_name", label: "Người tạo", kind: "readonly" },
  { key: "created_at", label: "Ngày tạo", kind: "readonly" },
];

export const DEFAULT_WARRANTY_COLUMNS: WarrantyColumnKey[] = [
  "customer",
  "phone",
  "shipping_code",
  "classification",
  "send_reason",
  "status",
  "created_at",
];

export const STATUS_BADGE_CLASS: Record<1 | 2 | 3 | 4, string> = {
  1: "bg-blue-100 text-blue-700",
  2: "bg-amber-100 text-amber-700",
  3: "bg-green-100 text-green-700",
  4: "bg-gray-200 text-gray-600",
};

const ORDER_KEYS: WarrantyColumnKey[] = [
  "customer",
  "phone",
  "address",
  "product",
  "sku",
  "amount",
  "shipping_code",
  "order_code",
];

function fmtMoney(v: string): string {
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString("vi-VN") + " ₫" : v;
}

function fmtDate(v: string): string {
  if (!v) return "—";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString("vi-VN");
}

/** Read-display value for a cell (used by readonly cells and text-cell display). */
export function columnDisplayValue(row: WarrantyClaim, key: WarrantyColumnKey): string {
  if (key === "status") return row.status_label || "—";
  if (key === "require_carrier_call") return row.require_carrier_call ? "Có" : "Không";
  if (key === "created_at") return fmtDate(row.created_at);
  if (key === "amount") return fmtMoney(row.order.total_after_discount);
  if (ORDER_KEYS.includes(key)) {
    const map: Record<string, string> = {
      customer: row.order.name,
      phone: row.order.phone,
      address: row.order.address,
      product: row.order.product,
      sku: row.order.sku,
      shipping_code: row.order.shipping_code,
      order_code: row.order.order_code,
    };
    return map[key] || "—";
  }
  const v = (row as unknown as Record<string, unknown>)[key];
  return v ? String(v) : "—";
}
```

- [ ] **Step 4: Run test to verify it passes**

Re-run the Step 2 command. Expected: PASS (3 passed).

- [ ] **Step 5: Type-check + commit**

```
cd frontend && npm run typecheck
cd .. && git add frontend/src/features/warranty-claims/warranty-claim-columns.ts frontend/src/features/warranty-claims/__tests__/warranty-claim-columns.test.ts
git commit -m "feat(fe): add warranty table column config"
```

---

### Task 2: Column storage + `useWarrantyColumns` hook

**Files:**
- Create: `frontend/src/features/warranty-claims/warranty-claims-storage.ts`
- Create: `frontend/src/features/warranty-claims/useWarrantyColumns.ts`
- Test: `frontend/src/features/warranty-claims/__tests__/warranty-claims-storage.test.ts`

**Interfaces:**
- Consumes: column config (Task 1).
- Produces:
  - storage: `getStoredColumns(): WarrantyColumnKey[]`, `setStoredColumns(v)`, `getStoredLocked(): WarrantyColumnKey[]`, `setStoredLocked(v)`, `getStoredOrder(): WarrantyColumnKey[]`, `setStoredOrder(v)` — all filter out unknown keys; visible falls back to `DEFAULT_WARRANTY_COLUMNS` when nothing stored.
  - hook `useWarrantyColumns()` → `{ visibleColumns, lockedColumns, orderedColumns, toggleColumn, toggleLock, reorderColumns }` where:
    - `visibleColumns: WarrantyColumnKey[]`, `lockedColumns: WarrantyColumnKey[]`.
    - `orderedColumns: WarrantyColumnKey[]` = visible keys ordered by the stored order, with locked keys first.
    - `toggleColumn(key)`, `toggleLock(key)`, `reorderColumns(activeKey, overKey)` — all persist.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/warranty-claims/__tests__/warranty-claims-storage.test.ts`:

```ts
import { beforeEach, describe, expect, it } from "vitest";

import {
  getStoredColumns,
  getStoredLocked,
  setStoredColumns,
  setStoredLocked,
} from "@/features/warranty-claims/warranty-claims-storage";
import { DEFAULT_WARRANTY_COLUMNS } from "@/features/warranty-claims/warranty-claim-columns";

describe("warranty-claims-storage", () => {
  beforeEach(() => localStorage.clear());

  it("returns defaults when nothing stored", () => {
    expect(getStoredColumns()).toEqual(DEFAULT_WARRANTY_COLUMNS);
    expect(getStoredLocked()).toEqual([]);
  });

  it("persists and reads back, filtering unknown keys", () => {
    setStoredColumns(["customer", "phone", "bogus" as never]);
    expect(getStoredColumns()).toEqual(["customer", "phone"]);
    setStoredLocked(["customer", "nope" as never]);
    expect(getStoredLocked()).toEqual(["customer"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`):
```
npx vitest run src/features/warranty-claims/__tests__/warranty-claims-storage.test.ts
```
Expected: FAIL — cannot resolve `warranty-claims-storage`.

- [ ] **Step 3: Create the storage module**

Create `frontend/src/features/warranty-claims/warranty-claims-storage.ts`:

```ts
import {
  DEFAULT_WARRANTY_COLUMNS,
  WARRANTY_COLUMN_OPTIONS,
  type WarrantyColumnKey,
} from "@/features/warranty-claims/warranty-claim-columns";

const VISIBLE_KEY = "desi_warranty_visible_columns";
const LOCKED_KEY = "desi_warranty_locked_columns";
const ORDER_KEY = "desi_warranty_column_order";

const ALL_KEYS = new Set<string>(WARRANTY_COLUMN_OPTIONS.map((o) => o.key));

function readKeys(storageKey: string): WarrantyColumnKey[] | null {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    return parsed.filter((k): k is WarrantyColumnKey => typeof k === "string" && ALL_KEYS.has(k));
  } catch {
    return null;
  }
}

function writeKeys(storageKey: string, keys: WarrantyColumnKey[]): void {
  try {
    localStorage.setItem(storageKey, JSON.stringify(keys));
  } catch {
    // ignore quota/availability errors
  }
}

export function getStoredColumns(): WarrantyColumnKey[] {
  return readKeys(VISIBLE_KEY) ?? [...DEFAULT_WARRANTY_COLUMNS];
}
export function setStoredColumns(v: WarrantyColumnKey[]): void {
  writeKeys(VISIBLE_KEY, v);
}
export function getStoredLocked(): WarrantyColumnKey[] {
  return readKeys(LOCKED_KEY) ?? [];
}
export function setStoredLocked(v: WarrantyColumnKey[]): void {
  writeKeys(LOCKED_KEY, v);
}
export function getStoredOrder(): WarrantyColumnKey[] {
  return readKeys(ORDER_KEY) ?? [];
}
export function setStoredOrder(v: WarrantyColumnKey[]): void {
  writeKeys(ORDER_KEY, v);
}
```

- [ ] **Step 4: Run test to verify it passes**

Re-run the Step 2 command. Expected: PASS (2 passed).

- [ ] **Step 5: Create the hook**

Create `frontend/src/features/warranty-claims/useWarrantyColumns.ts`:

```ts
import { useCallback, useMemo, useState } from "react";

import {
  WARRANTY_COLUMN_OPTIONS,
  type WarrantyColumnKey,
} from "@/features/warranty-claims/warranty-claim-columns";
import {
  getStoredColumns,
  getStoredLocked,
  getStoredOrder,
  setStoredColumns,
  setStoredLocked,
  setStoredOrder,
} from "@/features/warranty-claims/warranty-claims-storage";

const ALL_ORDER = WARRANTY_COLUMN_OPTIONS.map((o) => o.key);

export function useWarrantyColumns() {
  const [visibleColumns, setVisible] = useState<WarrantyColumnKey[]>(getStoredColumns);
  const [lockedColumns, setLocked] = useState<WarrantyColumnKey[]>(getStoredLocked);
  const [order, setOrder] = useState<WarrantyColumnKey[]>(getStoredOrder);

  // Full order = stored order first (known keys), then any options not yet in it.
  const fullOrder = useMemo(() => {
    const seen = new Set(order);
    return [...order, ...ALL_ORDER.filter((k) => !seen.has(k))];
  }, [order]);

  const orderedColumns = useMemo(() => {
    const vis = new Set(visibleColumns);
    const locked = lockedColumns.filter((k) => vis.has(k));
    const rest = fullOrder.filter((k) => vis.has(k) && !lockedColumns.includes(k));
    return [...locked, ...rest];
  }, [visibleColumns, lockedColumns, fullOrder]);

  const toggleColumn = useCallback((key: WarrantyColumnKey) => {
    setVisible((prev) => {
      const next = prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key];
      setStoredColumns(next);
      return next;
    });
    setLocked((prev) => {
      // a hidden column cannot stay locked
      const next = prev.filter((k) => k !== key);
      if (next.length !== prev.length) setStoredLocked(next);
      return next;
    });
  }, []);

  const toggleLock = useCallback((key: WarrantyColumnKey) => {
    setLocked((prev) => {
      const next = prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key];
      setStoredLocked(next);
      return next;
    });
  }, []);

  const reorderColumns = useCallback(
    (activeKey: WarrantyColumnKey, overKey: WarrantyColumnKey) => {
      setOrder(() => {
        const base = [...fullOrder];
        const from = base.indexOf(activeKey);
        const to = base.indexOf(overKey);
        if (from === -1 || to === -1) return base;
        base.splice(to, 0, base.splice(from, 1)[0]);
        setStoredOrder(base);
        return base;
      });
    },
    [fullOrder],
  );

  return { visibleColumns, lockedColumns, orderedColumns, toggleColumn, toggleLock, reorderColumns };
}
```

- [ ] **Step 6: Type-check + commit**

```
cd frontend && npm run typecheck
cd .. && git add frontend/src/features/warranty-claims/warranty-claims-storage.ts frontend/src/features/warranty-claims/useWarrantyColumns.ts frontend/src/features/warranty-claims/__tests__/warranty-claims-storage.test.ts
git commit -m "feat(fe): add warranty column storage and state hook"
```

---

### Task 3: Column picker (item + dropdown)

**Files:**
- Create: `frontend/src/features/warranty-claims/components/WarrantyColumnItem.tsx`
- Create: `frontend/src/features/warranty-claims/components/WarrantyColumnPicker.tsx`
- Test: `frontend/src/features/warranty-claims/__tests__/WarrantyColumnPicker.test.tsx`

**Interfaces:**
- Consumes: column config (Task 1), `useWarrantyColumns` return shape (Task 2).
- Produces: `WarrantyColumnPicker` with props `{ visibleColumns, lockedColumns, orderedColumns, toggleColumn, toggleLock, reorderColumns }` (the hook's return). Renders a "Cột" button that toggles a portal dropdown listing ALL options (visible ones in their ordered position first, then hidden) with drag-reorder (dnd-kit) over the visible+ordered ones. `WarrantyColumnItem` is a replicated row (checkbox + grip + pin) typed to `WarrantyColumnKey`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/warranty-claims/__tests__/WarrantyColumnPicker.test.tsx`:

```tsx
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WarrantyColumnPicker } from "@/features/warranty-claims/components/WarrantyColumnPicker";
import { DEFAULT_WARRANTY_COLUMNS } from "@/features/warranty-claims/warranty-claim-columns";

afterEach(() => cleanup());

function setup() {
  const toggleColumn = vi.fn();
  render(
    <WarrantyColumnPicker
      visibleColumns={DEFAULT_WARRANTY_COLUMNS}
      lockedColumns={[]}
      orderedColumns={DEFAULT_WARRANTY_COLUMNS}
      toggleColumn={toggleColumn}
      toggleLock={vi.fn()}
      reorderColumns={vi.fn()}
    />,
  );
  return { toggleColumn };
}

describe("WarrantyColumnPicker", () => {
  it("opens the dropdown and lists a known column, toggling it", () => {
    const { toggleColumn } = setup();
    fireEvent.click(screen.getByRole("button", { name: /cột/i }));
    // "Phân loại" is a known column label
    const checkbox = screen.getByLabelText("Phân loại");
    fireEvent.click(checkbox);
    expect(toggleColumn).toHaveBeenCalledWith("classification");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`):
```
npx vitest run src/features/warranty-claims/__tests__/WarrantyColumnPicker.test.tsx
```
Expected: FAIL — cannot resolve `WarrantyColumnPicker`.

- [ ] **Step 3: Create the column item (replica, typed to WarrantyColumnKey)**

Create `frontend/src/features/warranty-claims/components/WarrantyColumnItem.tsx`:

```tsx
import { GripVertical, Pin, PinOff } from "lucide-react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

import { Button } from "@/components/ui/button";
import { type WarrantyColumnKey } from "@/features/warranty-claims/warranty-claim-columns";

type Props = {
  columnOption: { key: WarrantyColumnKey; label: string };
  isVisible: boolean;
  isLocked: boolean;
  onToggle: () => void;
  onToggleLock: () => void;
};

export function WarrantyColumnItem({
  columnOption,
  isVisible,
  isLocked,
  onToggle,
  onToggleLock,
}: Props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: columnOption.key,
    disabled: !isVisible,
  });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };
  return (
    <div
      ref={setNodeRef}
      style={style}
      className="flex items-center gap-1 rounded-md px-1 py-1.5 hover:bg-accent/60"
    >
      {isVisible ? (
        <button
          type="button"
          className="cursor-grab touch-none p-0.5 text-muted-foreground hover:text-foreground"
          {...attributes}
          {...listeners}
        >
          <GripVertical className="h-3.5 w-3.5" />
        </button>
      ) : (
        <span className="inline-block w-[18px]" />
      )}
      <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          className="h-4 w-4 accent-primary"
          checked={isVisible}
          onChange={onToggle}
        />
        <span className="truncate text-sm text-foreground">{columnOption.label}</span>
      </label>
      <Button
        type="button"
        variant="outline"
        size="icon"
        className="h-7 w-7 border-border text-primary hover:bg-accent"
        title={isLocked ? "Bỏ ghim" : "Ghim cột"}
        aria-label={isLocked ? "Bỏ ghim" : "Ghim cột"}
        disabled={!isVisible}
        onClick={onToggleLock}
      >
        {isLocked ? <Pin className="h-3.5 w-3.5" /> : <PinOff className="h-3.5 w-3.5" />}
      </Button>
    </div>
  );
}
```

> Note: the `<label>` wraps the checkbox with the column label text, so `getByLabelText("Phân loại")` resolves to the checkbox.

- [ ] **Step 4: Create the picker**

Create `frontend/src/features/warranty-claims/components/WarrantyColumnPicker.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Columns3 } from "lucide-react";
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";

import { Button } from "@/components/ui/button";
import {
  WARRANTY_COLUMN_OPTIONS,
  type WarrantyColumnKey,
} from "@/features/warranty-claims/warranty-claim-columns";
import { WarrantyColumnItem } from "./WarrantyColumnItem";

type Props = {
  visibleColumns: WarrantyColumnKey[];
  lockedColumns: WarrantyColumnKey[];
  orderedColumns: WarrantyColumnKey[];
  toggleColumn: (key: WarrantyColumnKey) => void;
  toggleLock: (key: WarrantyColumnKey) => void;
  reorderColumns: (activeKey: WarrantyColumnKey, overKey: WarrantyColumnKey) => void;
};

export function WarrantyColumnPicker({
  visibleColumns,
  lockedColumns,
  orderedColumns,
  toggleColumn,
  toggleLock,
  reorderColumns,
}: Props) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));

  useEffect(() => {
    if (!open || !btnRef.current) return;
    const r = btnRef.current.getBoundingClientRect();
    setPos({ top: r.bottom + 4, left: Math.max(8, r.right - 280) });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (
        menuRef.current &&
        !menuRef.current.contains(e.target as Node) &&
        btnRef.current &&
        !btnRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  // Visible columns (in display order) first, then hidden columns.
  const visibleSet = new Set(visibleColumns);
  const hidden = WARRANTY_COLUMN_OPTIONS.map((o) => o.key).filter((k) => !visibleSet.has(k));
  const rows = [...orderedColumns, ...hidden];
  const labelOf = (key: WarrantyColumnKey) =>
    WARRANTY_COLUMN_OPTIONS.find((o) => o.key === key)!.label;

  const onDragEnd = (e: DragEndEvent) => {
    const { active, over } = e;
    if (over && active.id !== over.id) {
      reorderColumns(active.id as WarrantyColumnKey, over.id as WarrantyColumnKey);
    }
  };

  return (
    <>
      <Button
        ref={btnRef}
        type="button"
        variant="outline"
        className="h-9 border-border"
        onClick={() => setOpen((v) => !v)}
      >
        <Columns3 className="mr-2 h-4 w-4" />
        Cột
      </Button>
      {open &&
        createPortal(
          <div
            ref={menuRef}
            style={{ position: "fixed", top: pos.top, left: pos.left, width: 280 }}
            className="z-50 max-h-[70vh] overflow-auto rounded-md border border-border bg-popover p-2 shadow-lg"
          >
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
              <SortableContext items={orderedColumns} strategy={verticalListSortingStrategy}>
                {rows.map((key) => (
                  <WarrantyColumnItem
                    key={key}
                    columnOption={{ key, label: labelOf(key) }}
                    isVisible={visibleSet.has(key)}
                    isLocked={lockedColumns.includes(key)}
                    onToggle={() => toggleColumn(key)}
                    onToggleLock={() => toggleLock(key)}
                  />
                ))}
              </SortableContext>
            </DndContext>
          </div>,
          document.body,
        )}
    </>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

Re-run the Step 2 command. Expected: PASS (1 passed).

- [ ] **Step 6: Type-check + commit**

```
cd frontend && npm run typecheck
cd .. && git add frontend/src/features/warranty-claims/components/WarrantyColumnItem.tsx frontend/src/features/warranty-claims/components/WarrantyColumnPicker.tsx frontend/src/features/warranty-claims/__tests__/WarrantyColumnPicker.test.tsx
git commit -m "feat(fe): add warranty column picker"
```

---

### Task 4: Dynamic table with inline-editable cells

**Files:**
- Create: `frontend/src/features/warranty-claims/components/WarrantyClaimsTable.tsx`
- Test: `frontend/src/features/warranty-claims/__tests__/WarrantyClaimsTable.test.tsx`

**Interfaces:**
- Consumes: column config + `columnDisplayValue` (Task 1); `WarrantyClaim`, `WARRANTY_STATUS_OPTIONS`, `WarrantyClaimPayload` types.
- Produces: `WarrantyClaimsTable` with props:
  - `rows: WarrantyClaim[]`
  - `orderedColumns: WarrantyColumnKey[]`
  - `lockedColumns: WarrantyColumnKey[]`
  - `isLoading: boolean`
  - `onOpenDetail: (id: number) => void`
  - `onPatch: (id: number, patch: Partial<WarrantyClaimPayload>) => void`
  - Inline editors are internal; `text`/`status`/`bool` cells call `onPatch`; readonly cells render `columnDisplayValue`. Editable cells `stopPropagation`. Row click → `onOpenDetail` unless text is selected.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/warranty-claims/__tests__/WarrantyClaimsTable.test.tsx`:

```tsx
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WarrantyClaimsTable } from "@/features/warranty-claims/components/WarrantyClaimsTable";
import type { WarrantyClaim } from "@/features/warranty-claims/warranty-claim-types";

afterEach(() => cleanup());

const row = {
  id: 7,
  order: {
    id: 5,
    name: "Nguyen A",
    phone: "0901",
    address: "Addr",
    product: "Ao",
    sku: "S1",
    quantity: 1,
    total_after_discount: "150000.00",
    shipping_code: "VD1",
    order_code: "OD1",
  },
  status: 1,
  status_label: "Mới",
  contact_channel: "",
  require_carrier_call: false,
  social_nickname: "",
  send_reason: "",
  send_method: "",
  classification: "Đổi",
  recall_code: "",
  return_status: "",
  accounting_check: "",
  accounting_note: "",
  created_by_name: "",
  created_at: "2026-06-24T10:00:00Z",
  updated_at: "2026-06-24T10:00:00Z",
} as WarrantyClaim;

function setup(extra?: Partial<React.ComponentProps<typeof WarrantyClaimsTable>>) {
  const onOpenDetail = vi.fn();
  const onPatch = vi.fn();
  render(
    <WarrantyClaimsTable
      rows={[row]}
      orderedColumns={["customer", "classification", "status"]}
      lockedColumns={[]}
      isLoading={false}
      onOpenDetail={onOpenDetail}
      onPatch={onPatch}
      {...extra}
    />,
  );
  return { onOpenDetail, onPatch };
}

describe("WarrantyClaimsTable", () => {
  it("renders only the visible columns and the readonly value", () => {
    setup();
    expect(screen.getByText("Khách hàng")).toBeInTheDocument();
    expect(screen.getByText("Nguyen A")).toBeInTheDocument();
    expect(screen.queryByText("SĐT")).not.toBeInTheDocument();
  });

  it("clicking a readonly cell opens detail", () => {
    const { onOpenDetail } = setup();
    fireEvent.click(screen.getByText("Nguyen A"));
    expect(onOpenDetail).toHaveBeenCalledWith(7);
  });

  it("editing a text cell calls onPatch and does not open detail", () => {
    const { onOpenDetail, onPatch } = setup();
    fireEvent.click(screen.getByText("Đổi")); // classification cell → becomes input
    const input = screen.getByDisplayValue("Đổi");
    fireEvent.change(input, { target: { value: "Bảo hành" } });
    fireEvent.blur(input);
    expect(onPatch).toHaveBeenCalledWith(7, { classification: "Bảo hành" });
    expect(onOpenDetail).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`):
```
npx vitest run src/features/warranty-claims/__tests__/WarrantyClaimsTable.test.tsx
```
Expected: FAIL — cannot resolve `WarrantyClaimsTable`.

- [ ] **Step 3: Create the table**

Create `frontend/src/features/warranty-claims/components/WarrantyClaimsTable.tsx`:

```tsx
import { useEffect, useState } from "react";

import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  STATUS_BADGE_CLASS,
  WARRANTY_COLUMN_OPTIONS,
  columnDisplayValue,
  type WarrantyColumnKey,
} from "@/features/warranty-claims/warranty-claim-columns";
import {
  WARRANTY_STATUS_OPTIONS,
  type WarrantyClaim,
  type WarrantyClaimPayload,
  type WarrantyClaimStatus,
} from "@/features/warranty-claims/warranty-claim-types";

type Props = {
  rows: WarrantyClaim[];
  orderedColumns: WarrantyColumnKey[];
  lockedColumns: WarrantyColumnKey[];
  isLoading: boolean;
  onOpenDetail: (id: number) => void;
  onPatch: (id: number, patch: Partial<WarrantyClaimPayload>) => void;
};

const KIND_BY_KEY = new Map(WARRANTY_COLUMN_OPTIONS.map((o) => [o.key, o.kind]));
const LABEL_BY_KEY = new Map(WARRANTY_COLUMN_OPTIONS.map((o) => [o.key, o.label]));

const stop = (e: React.SyntheticEvent) => e.stopPropagation();

function InlineTextCell({
  value,
  onSave,
}: {
  value: string;
  onSave: (v: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);

  if (!editing) {
    return (
      <span
        className="block min-h-[1.25rem] cursor-text truncate"
        onClick={(e) => {
          stop(e);
          setEditing(true);
        }}
      >
        {value || "—"}
      </span>
    );
  }
  const commit = () => {
    setEditing(false);
    if (draft !== value) onSave(draft);
  };
  return (
    <input
      autoFocus
      className="h-7 w-full rounded border border-input bg-background px-2 text-sm"
      value={draft}
      onClick={stop}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        else if (e.key === "Escape") {
          setDraft(value);
          setEditing(false);
        }
      }}
    />
  );
}

export function WarrantyClaimsTable({
  rows,
  orderedColumns,
  lockedColumns,
  isLoading,
  onOpenDetail,
  onPatch,
}: Props) {
  const headerClass =
    "h-11 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground";
  const colCount = orderedColumns.length || 1;

  const renderCell = (row: WarrantyClaim, key: WarrantyColumnKey) => {
    const kind = KIND_BY_KEY.get(key);
    if (kind === "status") {
      return (
        <select
          className="h-7 rounded border border-input bg-background px-1 text-sm"
          value={row.status}
          onClick={stop}
          onChange={(e) => onPatch(row.id, { status: Number(e.target.value) as WarrantyClaimStatus })}
        >
          {WARRANTY_STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      );
    }
    if (kind === "bool") {
      return (
        <span onClick={stop} className="inline-flex">
          <Switch
            checked={row.require_carrier_call}
            onCheckedChange={(v) => onPatch(row.id, { require_carrier_call: v })}
          />
        </span>
      );
    }
    if (kind === "text") {
      return (
        <InlineTextCell
          value={String((row as unknown as Record<string, unknown>)[key] ?? "")}
          onSave={(v) => onPatch(row.id, { [key]: v } as Partial<WarrantyClaimPayload>)}
        />
      );
    }
    // readonly
    if (key === "status") {
      const badge = STATUS_BADGE_CLASS[row.status as 1 | 2 | 3 | 4];
      return (
        <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${badge}`}>
          {row.status_label}
        </span>
      );
    }
    return <span className="truncate text-muted-foreground">{columnDisplayValue(row, key)}</span>;
  };

  const handleRowClick = (id: number) => {
    if ((window.getSelection()?.toString() ?? "") !== "") return;
    onOpenDetail(id);
  };

  return (
    <Table className="min-w-[860px] [&_td]:px-3 [&_th]:px-3">
      <TableHeader>
        <TableRow className="border-b border-border bg-muted/40 hover:bg-muted/40">
          {orderedColumns.map((key) => (
            <TableHead
              key={key}
              className={`${headerClass} ${lockedColumns.includes(key) ? "sticky left-0 z-10 bg-muted/40" : ""}`}
            >
              {LABEL_BY_KEY.get(key)}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {isLoading && rows.length === 0 ? (
          <TableRow>
            <TableCell colSpan={colCount} className="py-8 text-center text-muted-foreground">
              Đang tải...
            </TableCell>
          </TableRow>
        ) : rows.length === 0 ? (
          <TableRow>
            <TableCell colSpan={colCount} className="py-8 text-center text-muted-foreground">
              Chưa có phiếu bảo hành nào.
            </TableCell>
          </TableRow>
        ) : (
          rows.map((row) => (
            <TableRow
              key={row.id}
              className="cursor-pointer border-b border-border/70 hover:bg-muted/30"
              onClick={() => handleRowClick(row.id)}
            >
              {orderedColumns.map((key) => (
                <TableCell
                  key={key}
                  className={`max-w-[240px] ${lockedColumns.includes(key) ? "sticky left-0 z-10 bg-background" : ""}`}
                >
                  {renderCell(row, key)}
                </TableCell>
              ))}
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Re-run the Step 2 command. Expected: PASS (3 passed).

- [ ] **Step 5: Type-check + commit**

```
cd frontend && npm run typecheck
cd .. && git add frontend/src/features/warranty-claims/components/WarrantyClaimsTable.tsx frontend/src/features/warranty-claims/__tests__/WarrantyClaimsTable.test.tsx
git commit -m "feat(fe): add warranty claims table with inline-editable cells"
```

---

### Task 5: Detail page + API/hook + route

**Files:**
- Modify: `frontend/src/lib/api/warrantyClaims.ts`
- Modify: `frontend/src/features/warranty-claims/queries.ts`
- Create: `frontend/src/pages/WarrantyClaimDetailPage.tsx`
- Modify: `frontend/src/app/routed-pages.tsx`
- Modify: `frontend/src/app/routes.tsx`
- Test: `frontend/src/features/warranty-claims/__tests__/WarrantyClaimDetailPage.test.tsx`

**Interfaces:**
- Consumes: existing `WarrantyClaimFormDialog`, `useUpdateWarrantyClaim`, `useDeleteWarrantyClaim`, `WARRANTY_STATUS_OPTIONS`, `columnDisplayValue`.
- Produces:
  - `getWarrantyClaim(id: number): Promise<WarrantyClaim>` (GET `/api/warranty/claims/{id}`).
  - `useWarrantyClaim(id: number)` query hook (key `["warranty-claims","detail",id]`, enabled when id>0).
  - Route `/sales/warranty/:claimId` guarded by section `warranty`; `WarrantyClaimDetailRoute`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/warranty-claims/__tests__/WarrantyClaimDetailPage.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import WarrantyClaimDetailPage from "@/pages/WarrantyClaimDetailPage";

vi.mock("@/lib/api/warrantyClaims", () => ({
  getWarrantyClaim: vi.fn().mockResolvedValue({
    id: 7,
    order: {
      id: 5,
      name: "Nguyen A",
      phone: "0901",
      address: "Addr",
      product: "Ao",
      sku: "S1",
      quantity: 1,
      total_after_discount: "150000.00",
      shipping_code: "VD1",
      order_code: "OD1",
    },
    status: 1,
    status_label: "Mới",
    contact_channel: "Shopee",
    require_carrier_call: false,
    social_nickname: "",
    send_reason: "Lỗi",
    send_method: "",
    classification: "Đổi",
    recall_code: "",
    return_status: "",
    accounting_check: "",
    accounting_note: "",
    created_by_name: "",
    created_at: "2026-06-24T10:00:00Z",
    updated_at: "2026-06-24T10:00:00Z",
  }),
  updateWarrantyClaim: vi.fn(),
  deleteWarrantyClaim: vi.fn(),
}));

afterEach(() => cleanup());

describe("WarrantyClaimDetailPage", () => {
  it("renders claim + order info from the fetched claim", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/sales/warranty/7"]}>
          <Routes>
            <Route path="/sales/warranty/:claimId" element={<WarrantyClaimDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Nguyen A")).toBeInTheDocument();
    expect(screen.getByText("Shopee")).toBeInTheDocument();
    expect(screen.getByText("Lỗi")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`):
```
npx vitest run src/features/warranty-claims/__tests__/WarrantyClaimDetailPage.test.tsx
```
Expected: FAIL — cannot resolve `WarrantyClaimDetailPage`.

- [ ] **Step 3: Add API + hook**

In `frontend/src/lib/api/warrantyClaims.ts`, add (the file already imports `fetchJson` and the types):

```ts
export async function getWarrantyClaim(id: number): Promise<WarrantyClaim> {
  return fetchJson(`${BASE}/${id}`);
}
```
(`WarrantyClaim` is already imported in this file; `BASE` is the existing `/api/warranty/claims` constant.)

In `frontend/src/features/warranty-claims/queries.ts`, add `getWarrantyClaim` to the import from `@/lib/api/warrantyClaims`, add a key, and add the hook:

```ts
// in warrantyClaimKeys:
  detail: (id: number) => ["warranty-claims", "detail", id] as const,

export function useWarrantyClaim(id: number) {
  return useQuery({
    queryKey: warrantyClaimKeys.detail(id),
    queryFn: () => getWarrantyClaim(id),
    enabled: id > 0,
  });
}
```

- [ ] **Step 4: Create the detail page**

Create `frontend/src/pages/WarrantyClaimDetailPage.tsx`:

```tsx
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { WarrantyClaimFormDialog } from "@/features/warranty-claims/components/WarrantyClaimFormDialog";
import {
  useDeleteWarrantyClaim,
  useUpdateWarrantyClaim,
  useWarrantyClaim,
} from "@/features/warranty-claims/queries";
import {
  STATUS_BADGE_CLASS,
  columnDisplayValue,
} from "@/features/warranty-claims/warranty-claim-columns";
import type { WarrantyClaimPayload } from "@/features/warranty-claims/warranty-claim-types";

const FIELDS: { key: Parameters<typeof columnDisplayValue>[1]; label: string }[] = [
  { key: "contact_channel", label: "Kênh liên hệ" },
  { key: "require_carrier_call", label: "YC gọi xác nhận" },
  { key: "social_nickname", label: "Nick MXH" },
  { key: "send_reason", label: "Lí do gửi" },
  { key: "send_method", label: "Hình thức gửi" },
  { key: "classification", label: "Phân loại" },
  { key: "recall_code", label: "Mã thu hồi" },
  { key: "return_status", label: "Tình trạng đơn hoàn" },
  { key: "accounting_check", label: "Kế toán check" },
  { key: "accounting_note", label: "Kế toán note" },
  { key: "created_by_name", label: "Người tạo" },
  { key: "created_at", label: "Ngày tạo" },
];

export default function WarrantyClaimDetailPage() {
  const { claimId } = useParams();
  const id = Number(claimId);
  const navigate = useNavigate();
  const { data: claim, isLoading } = useWarrantyClaim(id);
  const updateMutation = useUpdateWarrantyClaim();
  const deleteMutation = useDeleteWarrantyClaim();
  const [editOpen, setEditOpen] = useState(false);

  const back = () => navigate("/sales/warranty");

  if (isLoading) return <div className="p-6 text-muted-foreground">Đang tải...</div>;
  if (!claim) return <div className="p-6 text-muted-foreground">Không tìm thấy phiếu.</div>;

  const handleSubmit = (payload: WarrantyClaimPayload) => {
    updateMutation.mutate(
      { id, payload },
      {
        onSuccess: () => {
          toast.success("Đã cập nhật phiếu bảo hành.");
          setEditOpen(false);
        },
        onError: (err) => toast.error(err instanceof Error ? err.message : "Thao tác thất bại."),
      },
    );
  };

  const handleDelete = () => {
    if (!window.confirm("Xóa phiếu bảo hành này?")) return;
    deleteMutation.mutate(id, {
      onSuccess: () => {
        toast.success("Đã xóa phiếu bảo hành.");
        back();
      },
      onError: (err) => toast.error(err instanceof Error ? err.message : "Thao tác thất bại."),
    });
  };

  const o = claim.order;
  return (
    <div className="flex h-full min-h-0 flex-col gap-4 p-4">
      <div className="flex items-center gap-2">
        <Button variant="outline" size="icon-sm" onClick={back} aria-label="Quay lại">
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h1 className="text-lg font-semibold">Phiếu bảo hành #{claim.id}</h1>
        <span
          className={`ml-2 inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_BADGE_CLASS[claim.status as 1 | 2 | 3 | 4]}`}
        >
          {claim.status_label}
        </span>
        <div className="ml-auto flex gap-2">
          <Button variant="outline" onClick={() => setEditOpen(true)}>
            <Pencil className="mr-2 h-4 w-4" />
            Sửa
          </Button>
          <Button
            variant="outline"
            className="text-destructive"
            disabled={deleteMutation.isPending}
            onClick={handleDelete}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            Xóa
          </Button>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-muted/30 p-4 text-sm">
        <h2 className="mb-2 font-medium">Thông tin đơn</h2>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <div><span className="text-muted-foreground">Khách: </span>{o.name}</div>
          <div><span className="text-muted-foreground">SĐT: </span>{o.phone}</div>
          <div className="sm:col-span-2"><span className="text-muted-foreground">Địa chỉ: </span>{o.address}</div>
          <div><span className="text-muted-foreground">SP/SKU: </span>{o.product} ({o.sku})</div>
          <div><span className="text-muted-foreground">Số tiền: </span>{columnDisplayValue(claim, "amount")}</div>
          <div><span className="text-muted-foreground">Mã vận đơn: </span>{o.shipping_code || "—"}</div>
          <div><span className="text-muted-foreground">Mã đơn: </span>{o.order_code || "—"}</div>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-card p-4 text-sm">
        <h2 className="mb-2 font-medium">Thông tin bảo hành</h2>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {FIELDS.map((f) => (
            <div key={f.key}>
              <span className="text-muted-foreground">{f.label}: </span>
              {columnDisplayValue(claim, f.key)}
            </div>
          ))}
        </div>
      </div>

      <WarrantyClaimFormDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        claim={claim}
        isPending={updateMutation.isPending}
        onSubmit={handleSubmit}
      />
    </div>
  );
}
```

- [ ] **Step 5: Wire route + lazy component**

In `frontend/src/app/routed-pages.tsx`, add near the other lazy imports:
```ts
const WarrantyClaimDetailPage = lazy(() => import("@/pages/WarrantyClaimDetailPage"));
```
and add the route function near `WarrantyClaimsRoute`:
```tsx
export function WarrantyClaimDetailRoute() {
  return <WarrantyClaimDetailPage />;
}
```

In `frontend/src/app/routes.tsx`, add `WarrantyClaimDetailRoute` to the import list from `@/app/routed-pages`, and add the route right after the existing `sales/warranty` route:
```tsx
      {
        path: "sales/warranty/:claimId",
        element: (
          <Guard section="warranty">
            <WarrantyClaimDetailRoute />
          </Guard>
        ),
      },
```

- [ ] **Step 6: Run test to verify it passes**

Re-run the Step 2 command. Expected: PASS (1 passed).

- [ ] **Step 7: Type-check + commit**

```
cd frontend && npm run typecheck
cd .. && git add frontend/src/lib/api/warrantyClaims.ts frontend/src/features/warranty-claims/queries.ts frontend/src/pages/WarrantyClaimDetailPage.tsx frontend/src/app/routed-pages.tsx frontend/src/app/routes.tsx frontend/src/features/warranty-claims/__tests__/WarrantyClaimDetailPage.test.tsx
git commit -m "feat(fe): add warranty claim detail page and route"
```

---

### Task 6: Wire the list page (toolbar + table + navigation)

**Files:**
- Modify: `frontend/src/pages/WarrantyClaimsPage.tsx`
- Test: update `frontend/src/features/warranty-claims/__tests__/WarrantyClaimsPage.test.tsx` (existing smoke test)

**Interfaces:**
- Consumes: `useWarrantyColumns` (Task 2), `WarrantyColumnPicker` (Task 3), `WarrantyClaimsTable` (Task 4), `useWarrantyClaims`/`useCreateWarrantyClaim`/`useUpdateWarrantyClaim` (existing), `useNavigate`.

- [ ] **Step 1: Rewrite the page**

Replace `frontend/src/pages/WarrantyClaimsPage.tsx` with:

```tsx
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { DataTableShell } from "@/components/ui/data-table-shell";
import { Input } from "@/components/ui/input";
import { ResponsiveList } from "@/components/ui/responsive-list";

import { WarrantyClaimFormDialog } from "@/features/warranty-claims/components/WarrantyClaimFormDialog";
import { WarrantyColumnPicker } from "@/features/warranty-claims/components/WarrantyColumnPicker";
import { WarrantyClaimsTable } from "@/features/warranty-claims/components/WarrantyClaimsTable";
import { useWarrantyColumns } from "@/features/warranty-claims/useWarrantyColumns";
import { STATUS_BADGE_CLASS } from "@/features/warranty-claims/warranty-claim-columns";
import {
  useCreateWarrantyClaim,
  useUpdateWarrantyClaim,
  useWarrantyClaims,
} from "@/features/warranty-claims/queries";
import {
  WARRANTY_STATUS_OPTIONS,
  type WarrantyClaim,
  type WarrantyClaimPayload,
  type WarrantyClaimStatus,
} from "@/features/warranty-claims/warranty-claim-types";

const PAGE_SIZE = 50;

export default function WarrantyClaimsPage() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<WarrantyClaimStatus | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  const cols = useWarrantyColumns();

  const params = useMemo(
    () => ({ q: q.trim() || undefined, status, limit: PAGE_SIZE, offset: 0 }),
    [q, status],
  );
  const { data, isLoading } = useWarrantyClaims(params);
  const items = data?.results ?? [];

  const createMutation = useCreateWarrantyClaim();
  const updateMutation = useUpdateWarrantyClaim();

  const openDetail = (id: number) => navigate(`/sales/warranty/${id}`);

  const handlePatch = (id: number, patch: Partial<WarrantyClaimPayload>) => {
    updateMutation.mutate(
      { id, payload: patch },
      { onError: (err) => toast.error(err instanceof Error ? err.message : "Lưu thất bại.") },
    );
  };

  const handleCreate = (payload: WarrantyClaimPayload) => {
    createMutation.mutate(payload, {
      onSuccess: () => {
        toast.success("Đã tạo phiếu bảo hành.");
        setDialogOpen(false);
      },
      onError: (err) => toast.error(err instanceof Error ? err.message : "Thao tác thất bại."),
    });
  };

  const toolbar = (
    <div className="flex w-full flex-wrap items-center gap-2">
      <Input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Tìm theo SĐT / mã vận đơn / tên..."
        className="h-9 w-full max-w-xs"
      />
      <select
        className="h-9 rounded-lg border border-input bg-background px-3 text-sm"
        value={status ?? ""}
        onChange={(e) =>
          setStatus(e.target.value ? (Number(e.target.value) as WarrantyClaimStatus) : null)
        }
      >
        <option value="">Tất cả trạng thái</option>
        {WARRANTY_STATUS_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <div className="ml-auto flex items-center gap-2">
        <WarrantyColumnPicker {...cols} />
        <Button
          className="bg-primary text-primary-foreground hover:bg-primary/90"
          onClick={() => setDialogOpen(true)}
        >
          <Plus className="mr-2 h-4 w-4" />
          Thêm bảo hành
        </Button>
      </div>
    </div>
  );

  const table = (
    <WarrantyClaimsTable
      rows={items}
      orderedColumns={cols.orderedColumns}
      lockedColumns={cols.lockedColumns}
      isLoading={isLoading}
      onOpenDetail={openDetail}
      onPatch={handlePatch}
    />
  );

  return (
    <>
      <DataTableShell toolbar={toolbar}>
        <ResponsiveList
          items={items}
          table={table}
          getKey={(it: WarrantyClaim) => it.id}
          emptyText="Chưa có phiếu bảo hành nào."
          renderCard={(row: WarrantyClaim) => (
            <button
              type="button"
              className="w-full rounded-lg border border-border bg-card p-3 text-left text-sm shadow-sm"
              onClick={() => openDetail(row.id)}
            >
              <div className="flex items-center justify-between">
                <span className="font-medium text-foreground">{row.order.name}</span>
                <span
                  className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_BADGE_CLASS[row.status as 1 | 2 | 3 | 4]}`}
                >
                  {row.status_label}
                </span>
              </div>
              <div className="mt-1 text-muted-foreground">{row.order.phone}</div>
              <div className="text-muted-foreground">VĐ: {row.order.shipping_code || "—"}</div>
            </button>
          )}
        />
      </DataTableShell>

      <WarrantyClaimFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        claim={null}
        isPending={createMutation.isPending}
        onSubmit={handleCreate}
      />
    </>
  );
}
```

- [ ] **Step 2: Update the existing smoke test**

The existing `frontend/src/features/warranty-claims/__tests__/WarrantyClaimsPage.test.tsx` renders the page; it now uses `useNavigate`, so it must render inside a router. Replace its render helper to wrap with `MemoryRouter`. Update the file's `renderPage` to:

```tsx
import { MemoryRouter } from "react-router-dom";
// ...
function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        {/* I18nProvider wrapper retained as in the existing test */}
        <WarrantyClaimsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
```
Keep the existing I18nProvider wrapper that the current test already uses (DataTableShell needs it). Keep the existing assertions ("Thêm bảo hành" button renders; empty state text). Read the current file first and make the minimal change (add MemoryRouter) so it still passes.

- [ ] **Step 3: Run the page smoke test + typecheck + build**

Run (from `frontend/`):
```
npx vitest run src/features/warranty-claims
npm run typecheck
npm run build
```
Expected: all warranty-claims tests pass; typecheck exit 0; build succeeds.

- [ ] **Step 4: Commit**

```
git add frontend/src/pages/WarrantyClaimsPage.tsx frontend/src/features/warranty-claims/__tests__/WarrantyClaimsPage.test.tsx
git commit -m "feat(fe): wire warranty list to dynamic table, column picker and detail nav"
```

---

## Self-Review notes

- **Spec coverage:** column config (T1) ✓; storage+hook visible/locked/order (T2) ✓; picker with dnd-kit reorder+pin, replicated item (T3) ✓; dynamic table, locked sticky, inline text/status/bool editors, stopPropagation, row-click-to-detail with selection guard (T4) ✓; detail page + GET hook + route + edit(form)+delete (T5) ✓; toolbar (search+status+Cột+Thêm), remove row actions, navigate, mobile card → detail (T6) ✓; tests each task + final build (T1–T6) ✓.
- **Backend unchanged** — uses existing GET/PATCH/DELETE. ✓
- **Type consistency:** `WarrantyColumnKey` / `WarrantyColumnKind` / `columnDisplayValue` / `useWarrantyColumns` return shape (`visibleColumns,lockedColumns,orderedColumns,toggleColumn,toggleLock,reorderColumns`) consistent across T2→T3→T4→T6; table props (`rows,orderedColumns,lockedColumns,isLoading,onOpenDetail,onPatch`) consistent T4→T6; `onPatch(id, Partial<WarrantyClaimPayload>)` matches `useUpdateWarrantyClaim` `{id,payload}`.
- **Replication decision:** `WarrantyColumnItem` is a replica of `SortableColumnItem` (typed to `WarrantyColumnKey`, Vietnamese pin labels, no `t`) instead of importing — avoids `OrderColumnKey` type coupling and keeps the warranty feature independent (per CLAUDE.md). Documented in T3.
