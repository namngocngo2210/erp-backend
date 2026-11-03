# Scan Shipment — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Warehouse staff scan a Bluetooth (HID keyboard-wedge) waybill in the ERP web UI to (A) mark scanned orders dispatched (xuất giao) on the Shipments page, and (B) create return notes (nhập hoàn) on the Returns page.

**Architecture:** A shared `ScanShipmentInput` (auto-focus input that fires on Enter) + a beep utility + a thin data layer (`lookupByShippingCode`, `useDispatchBatch`, `useCreateReturnsBatch`) drive two dialogs (`ScanDispatchDialog`, `ScanReturnDialog`) mounted from the existing Shipments and Returns pages. Backend endpoints already exist and are merged on this branch.

**Tech Stack:** React + Vite + TypeScript, shadcn/ui, TanStack Query, sonner toast, vitest + @testing-library/react.

## Global Constraints

- Work on branch `feat/scan-shipment-stock-document` (already checked out). Do NOT create new branches.
- Commit messages English, short, **no** `Co-Authored-By` line.
- TypeScript strict; run `npx tsc --noEmit` (exit 0) and `npx eslint <files> --max-warnings=9999` (no new errors) from `frontend/` before each commit. A pre-existing `react-hooks/exhaustive-deps` warning elsewhere is acceptable; introduce none.
- Reuse existing patterns: `fetchJson` from `@/services/http`; warehouse list via `fetchJson("/api/warehouses?limit=200&offset=0")` → `WarehouseListResponse.items` (`@/lib/api/warehouses`); dialogs via `FormDialogShell`/`FormGrid` from `@/components/ui/form-dialog-content`; `Dialog` from `@/components/ui/dialog`; `Button`/`Input`/`Select*` from shadcn; `toast` from `sonner`.
- Backend contracts (already merged):
  - `GET /api/orders/lookup-by-shipping-code?code=<str>` → 200 `{order_id, order_code, shipping_code, status, status_label, customer_name, customer_phone, fulfillment_warehouse_id, items:[{product_id, sku, name, quantity}]}`; 200 `{multiple:true, orders:[...]}`; 404 on none; 400 on blank.
  - `POST /api/orders/dispatch-batch` body `{fulfillment_warehouse_id:int, order_ids:int[]}` → `{results:[{order_id, ok, warnings?, stock_warnings?, error?}]}`.
  - `POST /api/inventory/returns/batch` body `{warehouse_id:int, items:[{order_id:int, return_type:str}]}` → `{results:[{order_id, ok, return_note_id?, code?, warnings?, error?}]}`. `return_type` ∈ `delivery_returned|warranty|defect` (UI: bán được / bảo hành / lỗi-chờ kiểm; default `defect`).
- Run frontend tests: `npx vitest run <path>` from `frontend/`.

---

## File Structure

- `frontend/src/features/inventory/scan/scan-sound.ts` — create: `beepSuccess()` / `beepError()` (Web Audio, guarded).
- `frontend/src/features/inventory/scan/ScanShipmentInput.tsx` — create: focused input, fires `onScan(code)` on Enter, refocuses after each scan.
- `frontend/src/features/inventory/scan/scan-types.ts` — create: shared TS types.
- `frontend/src/features/inventory/scan/scan-queries.ts` — create: `lookupByShippingCode()`, `useDispatchBatch()`, `useCreateReturnsBatch()`.
- `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx` — create: xuất giao dialog.
- `frontend/src/features/inventory/scan/ScanReturnDialog.tsx` — create: nhập hoàn dialog.
- `frontend/src/features/inventory/scan/__tests__/ScanShipmentInput.test.tsx` — create.
- `frontend/src/features/inventory/pages/ShipmentsPage.tsx` — modify: add "Quét xuất kho" button + mount `ScanDispatchDialog`.
- `frontend/src/features/inventory/pages/ReturnsPage.tsx` — modify: add "Quét hàng hoàn" button + mount `ScanReturnDialog`.

---

### Task 1: Scan input primitive + beep

**Files:**
- Create: `frontend/src/features/inventory/scan/scan-sound.ts`
- Create: `frontend/src/features/inventory/scan/ScanShipmentInput.tsx`
- Test: `frontend/src/features/inventory/scan/__tests__/ScanShipmentInput.test.tsx`

**Interfaces:**
- Produces: `beepSuccess(): void`, `beepError(): void`.
- Produces: `ScanShipmentInput(props: { onScan: (code: string) => void; disabled?: boolean; placeholder?: string })` — React component. Captures fast keyboard-wedge input; on Enter, trims the value, calls `onScan` if non-empty, clears, and refocuses.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/inventory/scan/__tests__/ScanShipmentInput.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ScanShipmentInput } from "../ScanShipmentInput";

describe("ScanShipmentInput", () => {
  it("fires onScan with trimmed code on Enter and clears the field", async () => {
    const onScan = vi.fn();
    render(<ScanShipmentInput onScan={onScan} />);
    const input = screen.getByРlaceholderText(/quét mã vận đơn/i) as HTMLInputElement;
    await userEvent.type(input, "  VD12345  {Enter}");
    expect(onScan).toHaveBeenCalledWith("VD12345");
    expect(input.value).toBe("");
  });

  it("ignores Enter on an empty field", async () => {
    const onScan = vi.fn();
    render(<ScanShipmentInput onScan={onScan} />);
    const input = screen.getByPlaceholderText(/quét mã vận đơn/i);
    await userEvent.type(input, "{Enter}");
    expect(onScan).not.toHaveBeenCalled();
  });
});
```

NOTE: fix the typo before running — the helper is `getByPlaceholderText` (the first occurrence above contains a deliberate non-ASCII char to force you to retype it correctly).

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/features/inventory/scan/__tests__/ScanShipmentInput.test.tsx`
Expected: FAIL (module `../ScanShipmentInput` not found).

- [ ] **Step 3: Implement the beep utility**

Create `frontend/src/features/inventory/scan/scan-sound.ts`:

```ts
let ctx: AudioContext | null = null;

function getCtx(): AudioContext | null {
  if (typeof window === "undefined") return null;
  const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  if (!ctx) ctx = new Ctor();
  return ctx;
}

function tone(frequency: number, durationMs: number): void {
  const audio = getCtx();
  if (!audio) return;
  try {
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.frequency.value = frequency;
    osc.type = "sine";
    gain.gain.value = 0.05;
    osc.connect(gain);
    gain.connect(audio.destination);
    const now = audio.currentTime;
    osc.start(now);
    osc.stop(now + durationMs / 1000);
  } catch {
    // ignore — audio is best-effort feedback
  }
}

export function beepSuccess(): void {
  tone(880, 90);
}

export function beepError(): void {
  tone(220, 220);
}
```

- [ ] **Step 4: Implement the component**

Create `frontend/src/features/inventory/scan/ScanShipmentInput.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";

import { Input } from "@/components/ui/input";

type Props = {
  onScan: (code: string) => void;
  disabled?: boolean;
  placeholder?: string;
};

export function ScanShipmentInput({ onScan, disabled, placeholder }: Props) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    ref.current?.focus();
  }, []);

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const code = value.trim();
    setValue("");
    if (code) onScan(code);
    // keep capturing the next scan
    requestAnimationFrame(() => ref.current?.focus());
  }

  return (
    <Input
      ref={ref}
      value={value}
      disabled={disabled}
      autoFocus
      placeholder={placeholder ?? "Quét mã vận đơn…"}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={handleKeyDown}
    />
  );
}
```

- [ ] **Step 5: Run test + typecheck**

Run (from `frontend/`):
- `npx vitest run src/features/inventory/scan/__tests__/ScanShipmentInput.test.tsx` → PASS (2).
- `npx tsc --noEmit` → exit 0.
- `npx eslint src/features/inventory/scan/ScanShipmentInput.tsx src/features/inventory/scan/scan-sound.ts --max-warnings=9999` → no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/inventory/scan/scan-sound.ts frontend/src/features/inventory/scan/ScanShipmentInput.tsx frontend/src/features/inventory/scan/__tests__/ScanShipmentInput.test.tsx
git commit -m "feat(inventory): scan input primitive and beep feedback"
```

---

### Task 2: Scan data layer (types + queries)

**Files:**
- Create: `frontend/src/features/inventory/scan/scan-types.ts`
- Create: `frontend/src/features/inventory/scan/scan-queries.ts`

**Interfaces:**
- Consumes: `fetchJson` from `@/services/http`; `useMutation`, `useQueryClient` from `@tanstack/react-query`.
- Produces:
  - Types `LookupItem`, `LookupOrder`, `LookupResponse`, `DispatchResult`, `ReturnBatchResult`, `ReturnTypeValue`.
  - `lookupByShippingCode(code: string): Promise<LookupResponse>` — throws on 404/400 (fetchJson behavior).
  - `useDispatchBatch()` → mutation over `{fulfillment_warehouse_id:number; order_ids:number[]}` returning `{results: DispatchResult[]}`.
  - `useCreateReturnsBatch()` → mutation over `{warehouse_id:number; items:{order_id:number; return_type:ReturnTypeValue}[]}` returning `{results: ReturnBatchResult[]}`.

- [ ] **Step 1: Create the types**

Create `frontend/src/features/inventory/scan/scan-types.ts`:

```ts
export type LookupItem = {
  product_id: number;
  sku: string;
  name: string;
  quantity: number;
};

export type LookupOrder = {
  order_id: number;
  order_code: string;
  shipping_code: string;
  status: number;
  status_label: string;
  customer_name: string;
  customer_phone: string;
  fulfillment_warehouse_id: number | null;
  items: LookupItem[];
};

// Single match → LookupOrder; multiple matches → {multiple:true, orders:[...]}
export type LookupResponse = LookupOrder | { multiple: true; orders: LookupOrder[] };

export type DispatchResult = {
  order_id: number;
  ok: boolean;
  warnings?: string[];
  stock_warnings?: { product_id: number; balance_after: number }[];
  error?: string;
};

export type ReturnTypeValue = "delivery_returned" | "warranty" | "defect";

export type ReturnBatchResult = {
  order_id: number;
  ok: boolean;
  return_note_id?: number;
  code?: string;
  warnings?: string[];
  error?: string;
};

export const RETURN_TYPE_LABELS: Record<ReturnTypeValue, string> = {
  defect: "Lỗi / chờ kiểm",
  delivery_returned: "Bán được",
  warranty: "Bảo hành",
};
```

- [ ] **Step 2: Create the query layer**

Create `frontend/src/features/inventory/scan/scan-queries.ts`:

```ts
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { fetchJson } from "@/services/http";

import type {
  DispatchResult,
  LookupResponse,
  ReturnBatchResult,
  ReturnTypeValue,
} from "./scan-types";

export async function lookupByShippingCode(code: string): Promise<LookupResponse> {
  return fetchJson(
    `/api/orders/lookup-by-shipping-code?code=${encodeURIComponent(code)}`,
  ) as Promise<LookupResponse>;
}

export function isMultiple(
  r: LookupResponse,
): r is { multiple: true; orders: import("./scan-types").LookupOrder[] } {
  return (r as { multiple?: boolean }).multiple === true;
}

export function useDispatchBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: { fulfillment_warehouse_id: number; order_ids: number[] }) =>
      fetchJson("/api/orders/dispatch-batch", {
        method: "POST",
        body: JSON.stringify(payload),
      }) as Promise<{ results: DispatchResult[] }>,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["inventory", "stock"] });
    },
  });
}

export function useCreateReturnsBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      warehouse_id: number;
      items: { order_id: number; return_type: ReturnTypeValue }[];
    }) =>
      fetchJson("/api/inventory/returns/batch", {
        method: "POST",
        body: JSON.stringify(payload),
      }) as Promise<{ results: ReturnBatchResult[] }>,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["inventory", "returns"] });
      void qc.invalidateQueries({ queryKey: ["inventory", "stock"] });
    },
  });
}
```

- [ ] **Step 3: Typecheck**

Run (from `frontend/`):
- `npx tsc --noEmit` → exit 0.
- `npx eslint src/features/inventory/scan/scan-types.ts src/features/inventory/scan/scan-queries.ts --max-warnings=9999` → no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/inventory/scan/scan-types.ts frontend/src/features/inventory/scan/scan-queries.ts
git commit -m "feat(inventory): scan data layer (lookup + dispatch/return batch hooks)"
```

---

### Task 3: Xuất giao dialog on Shipments page

**Files:**
- Create: `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx`
- Modify: `frontend/src/features/inventory/pages/ShipmentsPage.tsx`

**Interfaces:**
- Consumes: `ScanShipmentInput`, `beepSuccess/beepError`, `lookupByShippingCode`, `isMultiple`, `useDispatchBatch`, types from Task 1/2; warehouse list via `fetchJson("/api/warehouses?limit=200&offset=0")` → `WarehouseListResponse.items`.
- Produces: `ScanDispatchDialog(props: { open: boolean; onOpenChange: (o: boolean) => void; onDone?: () => void })`.

- [ ] **Step 1: Create the dialog**

Create `frontend/src/features/inventory/scan/ScanDispatchDialog.tsx`:

```tsx
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormDialogShell } from "@/components/ui/form-dialog-content";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fetchJson } from "@/services/http";
import type { WarehouseListResponse } from "@/lib/api/warehouses";

import { ScanShipmentInput } from "./ScanShipmentInput";
import { beepError, beepSuccess } from "./scan-sound";
import { isMultiple, lookupByShippingCode, useDispatchBatch } from "./scan-queries";
import type { DispatchResult, LookupOrder } from "./scan-types";

const DISPATCH_OK_STATUSES = new Set([2, 11]); // IN_DELIVERY, AWAITING_SHIPMENT

type Props = {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onDone?: () => void;
};

export function ScanDispatchDialog({ open, onOpenChange, onDone }: Props) {
  const [warehouseId, setWarehouseId] = useState("");
  const [scanned, setScanned] = useState<LookupOrder[]>([]);
  const [results, setResults] = useState<DispatchResult[] | null>(null);

  const { data: whData } = useQuery({
    queryKey: ["warehouses", "list"],
    queryFn: () =>
      fetchJson("/api/warehouses?limit=200&offset=0") as Promise<WarehouseListResponse>,
    staleTime: 60_000,
  });
  const warehouses = whData?.items ?? [];
  const dispatchBatch = useDispatchBatch();

  function addOrder(o: LookupOrder) {
    if (scanned.some((s) => s.order_id === o.order_id)) {
      toast.warning(`Đơn ${o.order_code} đã quét rồi.`);
      beepError();
      return;
    }
    setScanned((prev) => [o, ...prev]);
    beepSuccess();
    if (!DISPATCH_OK_STATUSES.has(o.status)) {
      toast.warning(`Đơn ${o.order_code}: trạng thái "${o.status_label}" không phải sẵn sàng xuất.`);
    }
  }

  async function handleScan(code: string) {
    try {
      const res = await lookupByShippingCode(code);
      if (isMultiple(res)) {
        toast.message(`Mã ${code} khớp ${res.orders.length} đơn — chọn 1:`, {
          description: res.orders.map((o) => o.order_code).join(", "),
        });
        // simplest UX: add the first; staff can remove if wrong
        addOrder(res.orders[0]);
        return;
      }
      addOrder(res);
    } catch {
      beepError();
      toast.error(`Không tìm thấy đơn cho mã: ${code}`);
    }
  }

  function removeOrder(id: number) {
    setScanned((prev) => prev.filter((o) => o.order_id !== id));
  }

  async function handleConfirm() {
    if (!warehouseId) {
      toast.error("Vui lòng chọn kho xuất.");
      return;
    }
    if (scanned.length === 0) {
      toast.error("Chưa quét đơn nào.");
      return;
    }
    const resp = await dispatchBatch.mutateAsync({
      fulfillment_warehouse_id: Number(warehouseId),
      order_ids: scanned.map((o) => o.order_id),
    });
    setResults(resp.results);
    const okCount = resp.results.filter((r) => r.ok).length;
    toast.success(`Đã xuất kho ${okCount}/${resp.results.length} đơn.`);
    onDone?.();
  }

  function reset() {
    setWarehouseId("");
    setScanned([]);
    setResults(null);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
    >
      <FormDialogShell
        title="Quét xuất kho (giao hàng)"
        footer={
          <>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Đóng
            </Button>
            <Button onClick={handleConfirm} disabled={dispatchBatch.isPending}>
              Xác nhận xuất kho ({scanned.length})
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-sm text-muted-foreground">Kho xuất</label>
            <Select value={warehouseId} onValueChange={setWarehouseId}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Chọn kho xuất" />
              </SelectTrigger>
              <SelectContent>
                {warehouses.map((w) => (
                  <SelectItem key={w.id} value={String(w.id)}>
                    {w.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div>
            <label className="mb-1 block text-sm text-muted-foreground">Quét mã vận đơn</label>
            <ScanShipmentInput onScan={handleScan} />
          </div>

          <div className="rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Mã vận đơn</th>
                  <th className="px-3 py-2">Mã đơn</th>
                  <th className="px-3 py-2">Khách</th>
                  <th className="px-3 py-2">Trạng thái</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {scanned.length === 0 && (
                  <tr>
                    <td className="px-3 py-3 text-muted-foreground" colSpan={5}>
                      Chưa quét đơn nào.
                    </td>
                  </tr>
                )}
                {scanned.map((o) => {
                  const warn = !DISPATCH_OK_STATUSES.has(o.status);
                  const r = results?.find((x) => x.order_id === o.order_id);
                  return (
                    <tr key={o.order_id} className="border-t border-border">
                      <td className="px-3 py-2 font-medium">{o.shipping_code}</td>
                      <td className="px-3 py-2">{o.order_code}</td>
                      <td className="px-3 py-2">{o.customer_name}</td>
                      <td className="px-3 py-2">
                        <span className={warn ? "text-amber-600" : ""}>{o.status_label}</span>
                        {r && (
                          <span className={r.ok ? "ml-2 text-green-600" : "ml-2 text-red-600"}>
                            {r.ok ? "✓ đã xuất" : `✗ ${r.error ?? "lỗi"}`}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2"
                          onClick={() => removeOrder(o.order_id)}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </FormDialogShell>
    </Dialog>
  );
}
```

- [ ] **Step 2: Wire into ShipmentsPage**

In `frontend/src/features/inventory/pages/ShipmentsPage.tsx`: import the dialog + a button, add `const [scanOpen, setScanOpen] = useState(false);`, render a `<Button onClick={() => setScanOpen(true)}>Quét xuất kho</Button>` in the page header/toolbar area (next to existing controls), and mount:

```tsx
<ScanDispatchDialog open={scanOpen} onOpenChange={setScanOpen} onDone={load} />
```

`load` is the refetch function returned by `useShipments` (read the file to confirm its exact name; the explore reported `load`). If the page uses a different refresh function, pass that. Add imports:

```tsx
import { useState } from "react"; // if not already imported
import { ScanDispatchDialog } from "@/features/inventory/scan/ScanDispatchDialog";
```

- [ ] **Step 3: Typecheck + lint**

Run (from `frontend/`):
- `npx tsc --noEmit` → exit 0.
- `npx eslint src/features/inventory/scan/ScanDispatchDialog.tsx src/features/inventory/pages/ShipmentsPage.tsx --max-warnings=9999` → no new errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/inventory/scan/ScanDispatchDialog.tsx frontend/src/features/inventory/pages/ShipmentsPage.tsx
git commit -m "feat(inventory): scan-to-dispatch dialog on shipments page"
```

---

### Task 4: Nhập hoàn dialog on Returns page

**Files:**
- Create: `frontend/src/features/inventory/scan/ScanReturnDialog.tsx`
- Modify: `frontend/src/features/inventory/pages/ReturnsPage.tsx`

**Interfaces:**
- Consumes: `ScanShipmentInput`, `beepSuccess/beepError`, `lookupByShippingCode`, `isMultiple`, `useCreateReturnsBatch`, `RETURN_TYPE_LABELS`, types; warehouse list as in Task 3.
- Produces: `ScanReturnDialog(props: { open: boolean; onOpenChange: (o: boolean) => void; onDone?: () => void })`.

- [ ] **Step 1: Create the dialog**

Create `frontend/src/features/inventory/scan/ScanReturnDialog.tsx`:

```tsx
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormDialogShell } from "@/components/ui/form-dialog-content";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fetchJson } from "@/services/http";
import type { WarehouseListResponse } from "@/lib/api/warehouses";

import { ScanShipmentInput } from "./ScanShipmentInput";
import { beepError, beepSuccess } from "./scan-sound";
import { isMultiple, lookupByShippingCode, useCreateReturnsBatch } from "./scan-queries";
import { RETURN_TYPE_LABELS } from "./scan-types";
import type { LookupOrder, ReturnBatchResult, ReturnTypeValue } from "./scan-types";

const RETURN_OK_STATUSES = new Set([4, 5, 13]); // IN_RETURN, RETURNED, AWAITING_RETURN_CONFIRMATION

type ScannedReturn = { order: LookupOrder; returnType: ReturnTypeValue };

type Props = {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onDone?: () => void;
};

export function ScanReturnDialog({ open, onOpenChange, onDone }: Props) {
  const [warehouseId, setWarehouseId] = useState("");
  const [scanned, setScanned] = useState<ScannedReturn[]>([]);
  const [results, setResults] = useState<ReturnBatchResult[] | null>(null);

  const { data: whData } = useQuery({
    queryKey: ["warehouses", "list"],
    queryFn: () =>
      fetchJson("/api/warehouses?limit=200&offset=0") as Promise<WarehouseListResponse>,
    staleTime: 60_000,
  });
  const warehouses = whData?.items ?? [];
  const createBatch = useCreateReturnsBatch();

  function addOrder(o: LookupOrder) {
    if (scanned.some((s) => s.order.order_id === o.order_id)) {
      toast.warning(`Đơn ${o.order_code} đã quét rồi.`);
      beepError();
      return;
    }
    setScanned((prev) => [{ order: o, returnType: "defect" }, ...prev]);
    beepSuccess();
    if (!RETURN_OK_STATUSES.has(o.status)) {
      toast.warning(`Đơn ${o.order_code}: trạng thái "${o.status_label}" không phải đơn hoàn.`);
    }
  }

  async function handleScan(code: string) {
    try {
      const res = await lookupByShippingCode(code);
      if (isMultiple(res)) {
        toast.message(`Mã ${code} khớp ${res.orders.length} đơn`, {
          description: res.orders.map((o) => o.order_code).join(", "),
        });
        addOrder(res.orders[0]);
        return;
      }
      addOrder(res);
    } catch {
      beepError();
      toast.error(`Không tìm thấy đơn cho mã: ${code}`);
    }
  }

  function setReturnType(id: number, rt: ReturnTypeValue) {
    setScanned((prev) =>
      prev.map((s) => (s.order.order_id === id ? { ...s, returnType: rt } : s)),
    );
  }

  function removeOrder(id: number) {
    setScanned((prev) => prev.filter((s) => s.order.order_id !== id));
  }

  async function handleConfirm() {
    if (!warehouseId) {
      toast.error("Vui lòng chọn kho nhận.");
      return;
    }
    if (scanned.length === 0) {
      toast.error("Chưa quét đơn nào.");
      return;
    }
    const resp = await createBatch.mutateAsync({
      warehouse_id: Number(warehouseId),
      items: scanned.map((s) => ({ order_id: s.order.order_id, return_type: s.returnType })),
    });
    setResults(resp.results);
    const okCount = resp.results.filter((r) => r.ok).length;
    toast.success(`Đã tạo ${okCount}/${resp.results.length} phiếu hoàn.`);
    onDone?.();
  }

  function reset() {
    setWarehouseId("");
    setScanned([]);
    setResults(null);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
    >
      <FormDialogShell
        title="Quét hàng hoàn (nhập kho)"
        footer={
          <>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Đóng
            </Button>
            <Button onClick={handleConfirm} disabled={createBatch.isPending}>
              Tạo phiếu hoàn ({scanned.length})
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-sm text-muted-foreground">Kho nhận</label>
            <Select value={warehouseId} onValueChange={setWarehouseId}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Chọn kho nhận" />
              </SelectTrigger>
              <SelectContent>
                {warehouses.map((w) => (
                  <SelectItem key={w.id} value={String(w.id)}>
                    {w.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div>
            <label className="mb-1 block text-sm text-muted-foreground">Quét mã vận đơn</label>
            <ScanShipmentInput onScan={handleScan} />
          </div>

          <div className="rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Mã vận đơn</th>
                  <th className="px-3 py-2">Mã đơn</th>
                  <th className="px-3 py-2">Loại trả</th>
                  <th className="px-3 py-2">Trạng thái</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {scanned.length === 0 && (
                  <tr>
                    <td className="px-3 py-3 text-muted-foreground" colSpan={5}>
                      Chưa quét đơn nào.
                    </td>
                  </tr>
                )}
                {scanned.map(({ order: o, returnType }) => {
                  const warn = !RETURN_OK_STATUSES.has(o.status);
                  const r = results?.find((x) => x.order_id === o.order_id);
                  return (
                    <tr key={o.order_id} className="border-t border-border">
                      <td className="px-3 py-2 font-medium">{o.shipping_code}</td>
                      <td className="px-3 py-2">{o.order_code}</td>
                      <td className="px-3 py-2">
                        <Select
                          value={returnType}
                          onValueChange={(v) => setReturnType(o.order_id, v as ReturnTypeValue)}
                        >
                          <SelectTrigger className="h-8 w-40">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {(Object.keys(RETURN_TYPE_LABELS) as ReturnTypeValue[]).map((rt) => (
                              <SelectItem key={rt} value={rt}>
                                {RETURN_TYPE_LABELS[rt]}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </td>
                      <td className="px-3 py-2">
                        <span className={warn ? "text-amber-600" : ""}>{o.status_label}</span>
                        {r && (
                          <span className={r.ok ? "ml-2 text-green-600" : "ml-2 text-red-600"}>
                            {r.ok ? `✓ ${r.code ?? "đã tạo"}` : `✗ ${r.error ?? "lỗi"}`}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2"
                          onClick={() => removeOrder(o.order_id)}
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </FormDialogShell>
    </Dialog>
  );
}
```

- [ ] **Step 2: Wire into ReturnsPage**

In `frontend/src/features/inventory/pages/ReturnsPage.tsx`: add `const [scanOpen, setScanOpen] = useState(false);`, a `<Button onClick={() => setScanOpen(true)}>Quét hàng hoàn</Button>` near the existing "Tạo phiếu trả hàng" button, and mount:

```tsx
<ScanReturnDialog open={scanOpen} onOpenChange={setScanOpen} />
```

(The hook `useCreateReturnsBatch` already invalidates `["inventory","returns"]`, so the list refreshes; no `onDone` needed unless the page uses a non-query refresh — read the file and pass `onDone` if so.) Add imports:

```tsx
import { useState } from "react"; // if not already imported
import { ScanReturnDialog } from "@/features/inventory/scan/ScanReturnDialog";
```

- [ ] **Step 3: Typecheck + lint**

Run (from `frontend/`):
- `npx tsc --noEmit` → exit 0.
- `npx eslint src/features/inventory/scan/ScanReturnDialog.tsx src/features/inventory/pages/ReturnsPage.tsx --max-warnings=9999` → no new errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/inventory/scan/ScanReturnDialog.tsx frontend/src/features/inventory/pages/ReturnsPage.tsx
git commit -m "feat(inventory): scan-to-return dialog on returns page"
```

---

### Task 5: Final frontend verification

**Files:** none (verification only)

- [ ] **Step 1: Full typecheck + targeted tests + build**

Run (from `frontend/`):
- `npx tsc --noEmit` → exit 0.
- `npx vitest run src/features/inventory/scan` → PASS.
- `npx eslint src/features/inventory/scan --max-warnings=9999` → no errors.

- [ ] **Step 2: Push**

```bash
git push origin feat/scan-shipment-stock-document
```

---

## Self-Review

**Spec coverage (spec §4):** ScanShipmentInput shared component → Task 1. Beep feedback → Task 1. Lookup + batch hooks → Task 2. Xuất giao UI on shipments page (warehouse select + scan + scanned list + confirm → dispatch-batch + results) → Task 3. Nhập hoàn UI on returns page (warehouse + scan + per-order return_type default defect + confirm → returns/batch) → Task 4. Status warnings (amber) and dedup-on-scan are in both dialogs. Beep on success/error in both. Default `defect` → Task 4 `addOrder`.

**Placeholder scan:** Wiring steps (Task 3 Step 2, Task 4 Step 2) instruct reading the page file to confirm the exact refresh function / button placement — concrete instruction, not open-ended. Deliberate test typo in Task 1 Step 1 is flagged with a fix note.

**Type consistency:** `LookupOrder`, `DispatchResult`, `ReturnBatchResult`, `ReturnTypeValue` defined in Task 2 `scan-types.ts` and consumed unchanged in Tasks 3/4. Hook names `useDispatchBatch`/`useCreateReturnsBatch` and `lookupByShippingCode`/`isMultiple` consistent between definition (Task 2) and use (Tasks 3/4). Status sets `{2,11}` (dispatch) and `{4,5,13}` (return) match backend `_EXPECTED`.

**Known limitation (documented, acceptable):** the "multiple matches" case auto-adds the first order rather than showing a picker (waybills are effectively unique; staff can remove a wrong one). A full picker is a follow-up if duplicates prove common.
