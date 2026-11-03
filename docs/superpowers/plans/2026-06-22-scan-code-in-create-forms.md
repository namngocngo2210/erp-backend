# "Quét mã" in Create-Document Forms — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Embed a "Quét mã" (scan waybill) input inside both create-document forms — Phiếu kho (`CreateDocumentDialog`) and Phiếu hoàn (`CreateReturnDialog`) — that looks up the order by shipping code and pulls its products into the form's line items; remove the standalone "Quét hàng hoàn" button.

**Architecture:** Frontend-only. Reuse `ScanShipmentInput`, `lookupByShippingCode`/`isMultiple` (scan-queries), and `beepSuccess`/`beepError` (scan-sound). A small pure helper merges order items into line rows. Stock form accumulates (sum by product, dedup by order); return form fills source_order/customer/lines for a single order (replace on re-scan). No backend changes.

**Tech Stack:** React + Vite + TS, shadcn/ui, TanStack Query, sonner, vitest.

## Global Constraints

- Branch `feat/scan-code-in-create-forms` (already checked out). No new branches.
- Commit messages English, short, no `Co-Authored-By`.
- Run FE checks from `frontend/`: `npx tsc --noEmit` (exit 0), `npx eslint <files> --max-warnings=9999` (no NEW errors — a pre-existing `react-hooks/exhaustive-deps` warning on the dialogs' `useEffect([open])` is acceptable; add none), `npx vitest run <path>`.
- Reuse (do not reinvent): `ScanShipmentInput` (`@/features/inventory/scan/ScanShipmentInput`), `lookupByShippingCode` + `isMultiple` (`@/features/inventory/scan/scan-queries`), `beepSuccess`/`beepError` (`@/features/inventory/scan/scan-sound`), `LookupItem`/`LookupOrder` types (`@/features/inventory/scan/scan-types`).
- Backend endpoint `GET /api/orders/lookup-by-shipping-code` already exists; do not modify backend.
- Label the scan UI exactly **"Quét mã"**.
- Known types: `LookupItem = { product_id: number; sku: string; name: string; quantity: number }`. `LookupOrder` has `order_id, order_code, customer_name, customer_phone, items`. StockDocumentsPage `LineForm = { product_id: string; sku: string; name: string; quantity: string }`. CreateReturnDialog `LineForm` adds `condition_note: string`.

---

## File Structure

- `frontend/src/features/inventory/scan/merge-order-lines.ts` — create: pure helpers `mergeOrderItemsIntoLines`, `orderItemsAsLines` + `ScanLine` type.
- `frontend/src/features/inventory/scan/__tests__/merge-order-lines.test.ts` — create.
- `frontend/src/features/inventory/pages/StockDocumentsPage.tsx` — modify `CreateDocumentDialog`: add "Quét mã" (accumulate+dedup); and remove the standalone "Quét hàng hoàn" button + ScanReturnDialog usage.
- `frontend/src/features/inventory/pages/CreateReturnDialog.tsx` — modify: add "Quét mã" (fill single order).

---

### Task 1: Line-merge helper + test

**Files:**
- Create: `frontend/src/features/inventory/scan/merge-order-lines.ts`
- Test: `frontend/src/features/inventory/scan/__tests__/merge-order-lines.test.ts`

**Interfaces:**
- Produces:
  - `type ScanLine = { product_id: string; sku: string; name: string; quantity: string }`
  - `mergeOrderItemsIntoLines(existing: ScanLine[], items: LookupItem[]): ScanLine[]` — for each item: if a line with the same `product_id` (as string) exists, add quantities; else append a new line. Returns a NEW array; never mutates `existing`.
  - `orderItemsAsLines(items: LookupItem[]): ScanLine[]` — map items to fresh ScanLine[] (used for replace).

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/inventory/scan/__tests__/merge-order-lines.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { mergeOrderItemsIntoLines, orderItemsAsLines } from "../merge-order-lines";
import type { LookupItem } from "../scan-types";

const items: LookupItem[] = [
  { product_id: 1, sku: "A", name: "SP A", quantity: 2 },
  { product_id: 2, sku: "B", name: "SP B", quantity: 3 },
];

describe("orderItemsAsLines", () => {
  it("maps items to string-typed line rows", () => {
    expect(orderItemsAsLines(items)).toEqual([
      { product_id: "1", sku: "A", name: "SP A", quantity: "2" },
      { product_id: "2", sku: "B", name: "SP B", quantity: "3" },
    ]);
  });
});

describe("mergeOrderItemsIntoLines", () => {
  it("appends new products", () => {
    const out = mergeOrderItemsIntoLines([], items);
    expect(out).toHaveLength(2);
    expect(out[0]).toEqual({ product_id: "1", sku: "A", name: "SP A", quantity: "2" });
  });

  it("sums quantity for an existing product", () => {
    const existing = [{ product_id: "1", sku: "A", name: "SP A", quantity: "5" }];
    const out = mergeOrderItemsIntoLines(existing, [items[0]]);
    expect(out).toHaveLength(1);
    expect(out[0].quantity).toBe("7");
  });

  it("does not mutate the input array", () => {
    const existing = [{ product_id: "1", sku: "A", name: "SP A", quantity: "5" }];
    mergeOrderItemsIntoLines(existing, items);
    expect(existing).toEqual([{ product_id: "1", sku: "A", name: "SP A", quantity: "5" }]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/features/inventory/scan/__tests__/merge-order-lines.test.ts`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the helper**

Create `frontend/src/features/inventory/scan/merge-order-lines.ts`:

```ts
import type { LookupItem } from "./scan-types";

export type ScanLine = {
  product_id: string;
  sku: string;
  name: string;
  quantity: string;
};

export function orderItemsAsLines(items: LookupItem[]): ScanLine[] {
  return items.map((it) => ({
    product_id: String(it.product_id),
    sku: it.sku,
    name: it.name,
    quantity: String(it.quantity),
  }));
}

export function mergeOrderItemsIntoLines(existing: ScanLine[], items: LookupItem[]): ScanLine[] {
  const next = existing.map((l) => ({ ...l }));
  for (const it of items) {
    const pid = String(it.product_id);
    const found = next.find((l) => l.product_id === pid);
    if (found) {
      found.quantity = String(Number(found.quantity) + it.quantity);
    } else {
      next.push({ product_id: pid, sku: it.sku, name: it.name, quantity: String(it.quantity) });
    }
  }
  return next;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/features/inventory/scan/__tests__/merge-order-lines.test.ts` → PASS (4).
Also: `npx tsc --noEmit` → 0; `npx eslint src/features/inventory/scan/merge-order-lines.ts --max-warnings=9999` → no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/scan/merge-order-lines.ts frontend/src/features/inventory/scan/__tests__/merge-order-lines.test.ts
git commit -m "feat(inventory): order-items-to-lines merge helper"
```

---

### Task 2: "Quét mã" in CreateDocumentDialog (phiếu kho)

**Files:**
- Modify: `frontend/src/features/inventory/pages/StockDocumentsPage.tsx` (the `CreateDocumentDialog` component only)

**Interfaces:**
- Consumes: `ScanShipmentInput`, `lookupByShippingCode`, `isMultiple`, `beepSuccess`/`beepError`, `mergeOrderItemsIntoLines`. The dialog's existing `lines`/`setLines` state (LineForm === ScanLine shape).

- [ ] **Step 1: Add imports + scanned-order tracking state**

In `StockDocumentsPage.tsx`, add imports near the other inventory imports:
```tsx
import { ScanShipmentInput } from "@/features/inventory/scan/ScanShipmentInput";
import { lookupByShippingCode, isMultiple } from "@/features/inventory/scan/scan-queries";
import { beepSuccess, beepError } from "@/features/inventory/scan/scan-sound";
import { mergeOrderItemsIntoLines } from "@/features/inventory/scan/merge-order-lines";
```
Inside `CreateDocumentDialog`, add state next to `lines`:
```tsx
const [scannedOrderIds, setScannedOrderIds] = useState<number[]>([]);
```
In `resetForm` (where `setLines([])` is called), also `setScannedOrderIds([]);`.

- [ ] **Step 2: Add the scan handler**

Inside `CreateDocumentDialog`, add:
```tsx
async function handleScanCode(code: string) {
  try {
    const res = await lookupByShippingCode(code);
    const order = isMultiple(res) ? res.orders[0] : res;
    if (isMultiple(res)) {
      toast.message(`Mã ${code} khớp ${res.orders.length} đơn`, {
        description: res.orders.map((o) => o.order_code).join(", "),
      });
    }
    if (scannedOrderIds.includes(order.order_id)) {
      beepError();
      toast.warning(`Đơn ${order.order_code} đã quét rồi.`);
      return;
    }
    setLines((prev) => mergeOrderItemsIntoLines(prev, order.items));
    setScannedOrderIds((prev) => [...prev, order.order_id]);
    beepSuccess();
  } catch {
    beepError();
    toast.error(`Không tìm thấy đơn cho mã: ${code}`);
  }
}
```

- [ ] **Step 3: Render the "Quét mã" block**

In the dialog body, immediately ABOVE the existing product-search input (the "Tìm sản phẩm (SKU / tên)" field), add:
```tsx
<div>
  <label className="mb-1 block text-sm text-muted-foreground">Quét mã</label>
  <ScanShipmentInput onScan={handleScanCode} placeholder="Quét mã vận đơn để thêm sản phẩm…" />
</div>
```
(Read the component to place this inside the same form section that holds the lines; keep the manual product search beneath it.)

- [ ] **Step 4: Typecheck + lint**

Run (from `frontend/`):
- `npx tsc --noEmit` → 0.
- `npx eslint src/features/inventory/pages/StockDocumentsPage.tsx --max-warnings=9999` → no new errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/pages/StockDocumentsPage.tsx
git commit -m "feat(inventory): scan waybill to add products in stock-document form"
```

---

### Task 3: "Quét mã" in CreateReturnDialog (phiếu hoàn)

**Files:**
- Modify: `frontend/src/features/inventory/pages/CreateReturnDialog.tsx`

**Interfaces:**
- Consumes: `ScanShipmentInput`, `lookupByShippingCode`, `isMultiple`, `beepSuccess`/`beepError`, `orderItemsAsLines`. The dialog's `setSourceOrderId`/`setCustomerName`/`setCustomerPhone`/`setLines`. Its `LineForm` has `condition_note`, so map helper output adding `condition_note: ""`.

- [ ] **Step 1: Add imports**

In `CreateReturnDialog.tsx`, add:
```tsx
import { ScanShipmentInput } from "@/features/inventory/scan/ScanShipmentInput";
import { lookupByShippingCode, isMultiple } from "@/features/inventory/scan/scan-queries";
import { beepSuccess, beepError } from "@/features/inventory/scan/scan-sound";
import { orderItemsAsLines } from "@/features/inventory/scan/merge-order-lines";
```

- [ ] **Step 2: Add the scan handler**

Inside `CreateReturnDialog`, add:
```tsx
async function handleScanCode(code: string) {
  try {
    const res = await lookupByShippingCode(code);
    const order = isMultiple(res) ? res.orders[0] : res;
    if (isMultiple(res)) {
      toast.message(`Mã ${code} khớp ${res.orders.length} đơn`, {
        description: res.orders.map((o) => o.order_code).join(", "),
      });
    }
    const hadOrder = sourceOrderId.trim() !== "";
    setSourceOrderId(String(order.order_id));
    setCustomerName(order.customer_name);
    setCustomerPhone(order.customer_phone);
    setLines(orderItemsAsLines(order.items).map((l) => ({ ...l, condition_note: "" })));
    beepSuccess();
    toast.success(hadOrder ? `Thay bằng đơn ${order.order_code}` : `Đã nạp đơn ${order.order_code}`);
  } catch {
    beepError();
    toast.error(`Không tìm thấy đơn cho mã: ${code}`);
  }
}
```

- [ ] **Step 3: Render the "Quét mã" block**

In the dialog body, ABOVE the existing "Đơn gốc" (source_order) / product-search section, add:
```tsx
<div>
  <label className="mb-1 block text-sm text-muted-foreground">Quét mã</label>
  <ScanShipmentInput onScan={handleScanCode} placeholder="Quét mã vận đơn để nạp đơn hoàn…" />
</div>
```
(Read the component to place it sensibly within the form grid; the manual fields remain editable below.)

- [ ] **Step 4: Typecheck + lint**

Run (from `frontend/`):
- `npx tsc --noEmit` → 0.
- `npx eslint src/features/inventory/pages/CreateReturnDialog.tsx --max-warnings=9999` → no new errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/inventory/pages/CreateReturnDialog.tsx
git commit -m "feat(inventory): scan waybill to load return order in return form"
```

---

### Task 4: Remove the standalone "Quét hàng hoàn" button

**Files:**
- Modify: `frontend/src/features/inventory/pages/StockDocumentsPage.tsx`

**Interfaces:** none produced.

- [ ] **Step 1: Remove the button, dialog mount, state, and import**

In `StockDocumentsPage.tsx`:
- Remove the "Quét hàng hoàn" `<Button …>` block (the one calling `setScanReturnOpen(true)`).
- Remove the `<ScanReturnDialog open={scanReturnOpen} onOpenChange={setScanReturnOpen} />` mount.
- Remove `const [scanReturnOpen, setScanReturnOpen] = useState(false);`.
- Remove `import { ScanReturnDialog } from "@/features/inventory/scan/ScanReturnDialog";`.
Keep the "Tạo ▼" dropdown (Phiếu kho / Phiếu trả hàng) and the Import Excel button. Do NOT touch the Shipments page or its "Quét xuất kho".

- [ ] **Step 2: Typecheck + lint**

Run (from `frontend/`):
- `npx tsc --noEmit` → 0.
- `npx eslint src/features/inventory/pages/StockDocumentsPage.tsx --max-warnings=9999` → no new errors (and no unused-import error for ScanReturnDialog).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/inventory/pages/StockDocumentsPage.tsx
git commit -m "feat(inventory): remove standalone scan-return button (scan now in forms)"
```

---

### Task 5: Verification

**Files:** none.

- [ ] **Step 1: Full FE checks**

Run (from `frontend/`):
- `npx tsc --noEmit` → 0.
- `npx eslint src/features/inventory --max-warnings=9999` → no errors.
- `npx vitest run src/features/inventory` → pass (incl. new merge-order-lines test).

- [ ] **Step 2: Push**

```bash
git push origin feat/scan-code-in-create-forms
```

---

## Self-Review

**Spec coverage:** C1 reuse lookup + embed in both forms → Tasks 2/3. C2 stock accumulate-by-product + dedup-by-order → Task 2 (`mergeOrderItemsIntoLines` + `scannedOrderIds`). C3 return fill single order + replace → Task 3. C4 beep + multiple-handling → Tasks 2/3 handlers. C5 remove "Quét hàng hoàn", keep "Quét xuất kho" → Task 4 (shipments untouched). C6 optional cleanup of ScanReturnDialog/returns-batch → intentionally deferred (not in this plan; noted in spec as optional).

**Placeholder scan:** Render steps say "read the component to place sensibly" — concrete (exact JSX given; placement described relative to named existing fields). No TBD.

**Type consistency:** `ScanLine` (Task 1) matches StockDocumentsPage `LineForm` exactly; return dialog maps with `condition_note: ""` (Task 3) to satisfy its wider `LineForm`. `mergeOrderItemsIntoLines`/`orderItemsAsLines` names consistent across Tasks 1–3. `handleScanCode` consistent within each dialog. `LookupItem`/`isMultiple`/`lookupByShippingCode` names match the existing scan module.
