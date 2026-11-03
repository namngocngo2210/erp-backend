# Landscape Form-Dialog Standardization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every content-form dialog render landscape (width > height), near-full-screen with a max cap, responsive from 13" to 27" — driven by a shared size variant + a reusable `FormDialogShell`, without touching confirm/error/small dialogs.

**Architecture:** Add a `size` variant (`confirm` | `form`) to the custom `DialogContent` via `cva` (default `confirm` = today's behavior, zero regression). Add `FormDialogShell` (sticky header / scrollable body / sticky footer) + `FormGrid` (1→2→3 responsive columns). Migrate the 16 in-scope forms to use the shell. The dialog portals to `document.body`, so `FormDialogShell` wraps body+footer in a `<form onSubmit>` when given `onSubmit` to keep native submit working.

**Tech Stack:** React + Vite + TypeScript, Tailwind, `class-variance-authority`, custom shadcn-style dialog, Vitest + Testing Library.

**Spec:** `docs/dialog-form-landscape-standard.md`

**Baseline known-fail tests (NOT regressions — verify on master via stash):** `OrderFormDialog.test.tsx`, `OrdersTable.mobile.test.tsx`, `ChannelsTable.mobile.test.tsx`, `app/__tests__/modules.test.ts`.

**Branch:** `feature/dialog-form-landscape` (already created off master). All paths below are relative to `frontend/`. Run all commands from `frontend/`.

---

## Phase 1 — Shared infrastructure

### Task 1: `size` variant on `DialogContent`

**Files:**
- Modify: `src/components/ui/dialog.tsx`
- Test: `src/components/ui/__tests__/dialog.test.tsx`

- [ ] **Step 1: Write the failing tests** — append to `src/components/ui/__tests__/dialog.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Dialog, DialogContent } from "@/components/ui/dialog";

describe("DialogContent size variant", () => {
  it("defaults to confirm size (max-w-sm)", () => {
    render(
      <Dialog open>
        <DialogContent>body</DialogContent>
      </Dialog>,
    );
    const content = screen.getByRole("dialog");
    expect(content.className).toContain("max-w-sm");
  });

  it("applies the landscape form size when size=form", () => {
    render(
      <Dialog open>
        <DialogContent size="form">body</DialogContent>
      </Dialog>,
    );
    const content = screen.getByRole("dialog");
    expect(content.className).toContain("max-w-[1480px]");
    expect(content.className).toContain("h-[90dvh]");
    expect(content.className).not.toContain("max-w-sm");
  });

  it("still lets a caller className override the size", () => {
    render(
      <Dialog open>
        <DialogContent className="max-w-3xl">body</DialogContent>
      </Dialog>,
    );
    const content = screen.getByRole("dialog");
    expect(content.className).toContain("max-w-3xl");
    expect(content.className).not.toContain("max-w-sm");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/ui/__tests__/dialog.test.tsx`
Expected: FAIL — `size="form"` not applied (`max-w-[1480px]` absent); `size` prop unknown to TS.

- [ ] **Step 3: Implement the variant in `src/components/ui/dialog.tsx`**

Add imports near the top (after existing imports):

```tsx
import { cva, type VariantProps } from "class-variance-authority";
```

Add the variant definition above `function DialogContent`:

```tsx
const dialogContentVariants = cva(
  "fixed top-1/2 left-1/2 z-50 -translate-x-1/2 -translate-y-1/2 rounded-xl bg-popover text-sm text-popover-foreground ring-1 ring-border duration-100 outline-none animate-in fade-in-0 zoom-in-95",
  {
    variants: {
      size: {
        // Confirm/error/small forms — identical to the previous base classes.
        confirm: "grid w-[calc(100%-2rem)] max-w-sm gap-4 p-4",
        // Landscape, near-full-screen with a cap; width > height. Body scrolls (see FormDialogShell).
        form: "flex h-[90dvh] max-h-[920px] w-[96vw] max-w-[1480px] flex-col overflow-hidden p-0 sm:w-[95vw]",
      },
    },
    defaultVariants: { size: "confirm" },
  },
);
```

Change the `DialogContent` signature and `className`:

```tsx
function DialogContent({
  className,
  children,
  showCloseButton = true,
  size,
  ...props
}: React.ComponentProps<"div"> & {
  showCloseButton?: boolean;
} & VariantProps<typeof dialogContentVariants>) {
```

Replace the long `className={cn("fixed top-1/2 ... zoom-in-95", className)}` on the content div with:

```tsx
        className={cn(dialogContentVariants({ size }), className)}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/components/ui/__tests__/dialog.test.tsx`
Expected: PASS (all cases, including the pre-existing override test).

- [ ] **Step 5: Typecheck**

Run: `npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/components/ui/dialog.tsx src/components/ui/__tests__/dialog.test.tsx
git commit -m "feat(ui): add size variant (confirm|form) to DialogContent"
```

---

### Task 2: `FormGrid` + `FormDialogShell`

**Files:**
- Create: `src/components/ui/form-dialog-content.tsx`
- Test: `src/components/ui/__tests__/form-dialog-content.test.tsx`

- [ ] **Step 1: Write the failing test** — create `src/components/ui/__tests__/form-dialog-content.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Dialog } from "@/components/ui/dialog";
import { FormDialogShell, FormGrid } from "@/components/ui/form-dialog-content";

function renderShell(props: Partial<React.ComponentProps<typeof FormDialogShell>> = {}) {
  render(
    <Dialog open>
      <FormDialogShell title="Tạo user" description="Mô tả" footer={<button>Lưu</button>} {...props}>
        <FormGrid columns={3}>
          <div>field-1</div>
        </FormGrid>
      </FormDialogShell>
    </Dialog>,
  );
}

describe("FormDialogShell", () => {
  it("renders title, description, body children and footer", () => {
    renderShell();
    expect(screen.getByText("Tạo user")).toBeInTheDocument();
    expect(screen.getByText("Mô tả")).toBeInTheDocument();
    expect(screen.getByText("field-1")).toBeInTheDocument();
    expect(screen.getByText("Lưu")).toBeInTheDocument();
  });

  it("uses the landscape form size", () => {
    renderShell();
    expect(screen.getByRole("dialog").className).toContain("max-w-[1480px]");
  });

  it("wraps body+footer in a form and fires onSubmit", () => {
    const onSubmit = vi.fn((e) => e.preventDefault());
    renderShell({ onSubmit, footer: <button type="submit">Lưu</button> });
    fireEvent.click(screen.getByText("Lưu"));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("FormGrid columns=3 yields a 3-column class at xl", () => {
    renderShell();
    expect(screen.getByText("field-1").parentElement?.className).toContain("xl:grid-cols-3");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/ui/__tests__/form-dialog-content.test.tsx`
Expected: FAIL — module `form-dialog-content` not found.

- [ ] **Step 3: Implement `src/components/ui/form-dialog-content.tsx`**

```tsx
import type { ComponentProps, FormEventHandler, ReactNode } from "react";

import {
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

/** Lưới field responsive cho thân form: 1 cột (mobile) → 2 (lg) → 3 (xl khi columns=3). */
export function FormGrid({
  columns = 2,
  className,
  ...props
}: ComponentProps<"div"> & { columns?: 2 | 3 }) {
  return (
    <div
      className={cn(
        "grid grid-cols-1 gap-x-6 gap-y-4",
        columns === 3 ? "lg:grid-cols-2 xl:grid-cols-3" : "lg:grid-cols-2",
        className,
      )}
      {...props}
    />
  );
}

type FormDialogShellProps = Omit<ComponentProps<typeof DialogContent>, "title" | "size"> & {
  title: ReactNode;
  description?: ReactNode;
  footer?: ReactNode;
  /** Khi có, body+footer được bọc trong <form> để nút submit (portal) vẫn submit được. */
  onSubmit?: FormEventHandler<HTMLFormElement>;
  bodyClassName?: string;
};

/**
 * Khung dialog form landscape: header dính / body cuộn / footer dính.
 * Chỉ vùng body cuộn; header & footer luôn hiển thị.
 */
export function FormDialogShell({
  title,
  description,
  footer,
  onSubmit,
  children,
  className,
  bodyClassName,
  ...props
}: FormDialogShellProps) {
  const body = (
    <div className={cn("min-h-0 flex-1 overflow-y-auto px-6 py-4", bodyClassName)}>{children}</div>
  );
  const footerEl = footer ? (
    <div className="sticky bottom-0 z-10 flex items-center justify-end gap-2 border-t border-border bg-card px-6 py-3">
      {footer}
    </div>
  ) : null;

  return (
    <DialogContent
      size="form"
      className={cn("border border-border/90 bg-card text-card-foreground shadow-xl", className)}
      {...props}
    >
      <DialogHeader className="sticky top-0 z-10 border-b border-border bg-card px-6 py-4 pr-12">
        <DialogTitle>{title}</DialogTitle>
        {description ? <DialogDescription>{description}</DialogDescription> : null}
      </DialogHeader>
      {onSubmit ? (
        <form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col">
          {body}
          {footerEl}
        </form>
      ) : (
        <>
          {body}
          {footerEl}
        </>
      )}
    </DialogContent>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/components/ui/__tests__/form-dialog-content.test.tsx`
Expected: PASS (4 cases).

- [ ] **Step 5: Typecheck + lint**

Run: `npx tsc --noEmit && npx eslint src/components/ui/form-dialog-content.tsx`
Expected: exit 0, no errors.

- [ ] **Step 6: Commit**

```bash
git add src/components/ui/form-dialog-content.tsx src/components/ui/__tests__/form-dialog-content.test.tsx
git commit -m "feat(ui): add FormDialogShell + FormGrid primitives"
```

---

## Migration recipe (apply for every in-scope form in Phases 2–5)

Each form below is a **deterministic transformation**, not free design. For each: **read the current file first**, then apply these steps exactly:

1. **Imports:** import `{ FormDialogShell, FormGrid }` from `@/components/ui/form-dialog-content`. Remove now-unused imports (`ManagedDialogContent`; or `DialogContent`/`DialogFooter` if no longer referenced). Keep `Dialog`.
2. **Replace the container:** swap `<DialogContent className="…sizing…">` (or `<ManagedDialogContent …>`) for `<FormDialogShell title={…} description={…} onSubmit={…} footer={…}>`.
   - `title` = the existing `<DialogTitle>` content; `description` = existing `<DialogDescription>` (omit if none).
   - If the form currently has a `<form onSubmit={handler}>` inside the dialog, move that handler to `FormDialogShell`'s `onSubmit` and delete the inner `<form>` wrapper (the shell now owns it). If submit is via button `onClick` (no `<form>`), do NOT pass `onSubmit`; keep the buttons' `onClick`.
3. **Header/footer markup:** delete the old `<DialogHeader>`/`<DialogTitle>`/`<DialogDescription>` block (now props) and the old footer/action-button row; pass the action buttons (Cancel + Submit/Save) into the `footer` prop instead.
4. **Body layout:** wrap the field block in `<FormGrid columns={N}>` (N per the table in §5.1 of the spec). Give full-width children (section headings, textareas, tables, item lists) `className="col-span-full"` (merge with existing classes).
5. **Remove leftover sizing/scroll classes** (`max-w-*`, `w-[min(...)]`, `max-h-[..]`, `overflow-y-auto`) from inner wrappers — the shell owns size + scroll.
6. **Verify:** `npx tsc --noEmit`; run the form's test if one exists; eyeball that full-width items have `col-span-full`.
7. **Commit** with a scoped message.

**Worked skeleton (before → after):**

```tsx
// BEFORE
<Dialog open={open} onOpenChange={onOpenChange}>
  <DialogContent className="sm:max-w-2xl">
    <DialogHeader><DialogTitle>Tạo user</DialogTitle></DialogHeader>
    <form onSubmit={handleSubmit} className="space-y-4">
      <Input ... /> <Input ... />
      <div className="flex justify-end gap-2"><Button variant="outline" onClick={close}>Hủy</Button><Button type="submit">Lưu</Button></div>
    </form>
  </DialogContent>
</Dialog>

// AFTER
<Dialog open={open} onOpenChange={onOpenChange}>
  <FormDialogShell
    title="Tạo user"
    onSubmit={handleSubmit}
    footer={<><Button variant="outline" onClick={close}>Hủy</Button><Button type="submit">Lưu</Button></>}
  >
    <FormGrid columns={2}>
      <Input ... /> <Input ... />
    </FormGrid>
  </FormDialogShell>
</Dialog>
```

---

## Phase 2 — Orders cluster

> One task per file. Each: read file → apply recipe → verify → commit. Columns/notes per file below.

### Task 3: `OrderFormDialog`
**File:** `src/features/orders/OrderFormDialog.tsx` · **Test:** `src/features/orders/OrderFormDialog.test.tsx`
- [ ] Apply recipe. Columns: group fields by section, `FormGrid columns={3}` for dense field sections; each section heading + the order-items table get `col-span-full`. This file already does near-full-screen — remove its bespoke `w-[min(96vw,1600px)] …` classes; the shell replaces them (cap becomes 1480px).
- [ ] Verify: `npx vitest run src/features/orders/OrderFormDialog.test.tsx` — must match the baseline (the one pre-existing KiotViet-block assertion failure is allowed; no NEW failures). Then `npx tsc --noEmit`.
- [ ] Commit: `git commit -m "refactor(orders): order form dialog uses FormDialogShell"`

### Task 4: `OrderDetailDialog`
**File:** `src/features/orders/OrderDetailDialog.tsx`
- [ ] Apply recipe (read-only; no `onSubmit`, footer = close/print actions if any). Columns: `FormGrid columns={2}`; history/timeline + any table `col-span-full`. Remove bespoke `max-h-[92dvh] w-[min(96vw,1600px)] overflow-y-auto` classes.
- [ ] Verify `npx tsc --noEmit`. Commit: `refactor(orders): order detail dialog uses FormDialogShell`.

### Task 5: `OrdersImportDialog`
**File:** `src/features/orders/OrdersImportDialog.tsx`
- [ ] Apply recipe. Body is a preview table → no FormGrid; wrap the table area so it can scroll inside the body. footer = import/cancel buttons. Remove `sm:max-w-5xl`.
- [ ] Verify `npx tsc --noEmit`. Commit.

### Task 6: `ExportColumnsDialog`
**File:** `src/features/orders/ExportColumnsDialog.tsx`
- [ ] Apply recipe. Checkbox list → `FormGrid columns={3}`. Remove `max-w-4xl max-h-[90vh] flex flex-col`.
- [ ] Verify `npx tsc --noEmit`. Commit.

### Task 7: `KiotVietLogsDialog`
**File:** `src/features/orders/KiotVietLogsDialog.tsx`
- [ ] Apply recipe. Logs table → full-width body, no FormGrid. Remove bespoke `w-[min(96vw,1600px)] …`.
- [ ] Verify `npx tsc --noEmit`. Commit.

### Task 8: `KshipDeliveryDialog`
**File:** `src/features/orders/KshipDeliveryDialog.tsx`
- [ ] Apply recipe. Note current `!block`/`!w-[…]` important overrides — remove them; the shell's `flex` layout replaces `!block`. Body table/form full-width. Remove `!max-w-[1600px]`.
- [ ] Verify `npx tsc --noEmit`. Commit.

---

## Phase 3 — Users / Channels / KiotViet cluster

### Task 9: `UserCreateDialog`
**File:** `src/features/users/UserCreateDialog.tsx`
- [ ] Apply recipe (was `ManagedDialogContent` → `sm:max-w-2xl`). `FormGrid columns={2}`. Long fields (notes/permissions block) `col-span-full`.
- [ ] Verify `npx tsc --noEmit`. Commit.

### Task 10: `UserEditDialog`
**File:** `src/features/users/UserEditDialog.tsx`
- [ ] Apply recipe. Same columns as Task 9.
- [ ] Verify `npx tsc --noEmit`. Commit.

### Task 11: `ChannelCreateDialog`
**File:** `src/features/channels/ChannelCreateDialog.tsx`
- [ ] Apply recipe. `FormGrid columns={2}`. The fb/google ads multi-select block `col-span-full`.
- [ ] Verify `npx tsc --noEmit`. Commit.

### Task 12: `ChannelManageDialog`
**File:** `src/features/channels/ChannelManageDialog.tsx`
- [ ] Apply recipe (was `sm:max-w-5xl`). `FormGrid columns={2}`; assignment section `col-span-full`.
- [ ] Verify `npx tsc --noEmit`. Commit.

### Task 13: `KiotVietDialog`
**File:** `src/features/kiotviet/KiotVietDialog.tsx`
- [ ] Apply recipe (was `ManagedDialogContent` + `max-h-[90dvh] overflow-hidden sm:max-w-5xl`). `FormGrid columns={2}`. Remove the bespoke max-h/overflow — shell owns scroll.
- [ ] Verify `npx tsc --noEmit`. Commit.

### Task 14: `DepartmentManager` (create + edit dialogs)
**File:** `src/features/users/DepartmentManager.tsx`
- [ ] Apply recipe to BOTH the create and edit `<DialogContent>` instances (leave the delete-confirm `DialogContent` untouched). `FormGrid columns={2}`. Remove `max-h-[85vh] overflow-y-auto sm:max-w-3xl`.
- [ ] Verify `npx tsc --noEmit`. Commit.

### Task 15: `ProfileDialog`
**File:** `src/app/profile-dialog.tsx`
- [ ] Apply recipe (was `ManagedDialogContent`). Two logical groups (profile, password) → `FormGrid columns={2}`, each group heading `col-span-full`. Profile has two independent submits (save profile / save password) — keep them as button `onClick`s; do NOT pass a single `onSubmit`. Footer can hold both or keep per-group buttons inside the body and pass `footer={null}` (omit footer).
- [ ] Verify `npx tsc --noEmit`. Commit.

---

## Phase 4 — Catalog / Inventory cluster

### Task 16: `ProductsPage` create + edit dialogs
**File:** `src/pages/ProductsPage.tsx`
- [ ] Apply recipe to the create and edit product `<DialogContent sm:max-w-[720px]>` instances (leave the two delete-confirm dialogs untouched). `FormGrid columns={2}`; description/image fields `col-span-full`.
- [ ] Verify `npx tsc --noEmit`. Commit.

### Task 17: `CreateReturnDialog`
**File:** `src/features/inventory/pages/CreateReturnDialog.tsx`
- [ ] Apply recipe (was `max-w-3xl`). Header fields `FormGrid columns={2}` (`col-span-full` heading); the line-items table `col-span-full` / full-width below the grid.
- [ ] Verify `npx tsc --noEmit`. Commit.

### Task 18: `StockDocumentsPage` detail dialog
**File:** `src/pages/StockDocumentsPage.tsx`
- [ ] Apply recipe to the document-detail `<DialogContent max-w-3xl>`. Read-only → no `onSubmit`. Document-lines table full-width, no FormGrid. Summary fields may use `FormGrid columns={3}`.
- [ ] Verify `npx tsc --noEmit`. Commit.

---

## Phase 5 — Finalize

### Task 19: Full verification + out-of-scope audit
- [ ] Run full suite: `npx vitest run` → confirm the only failures are the 4 baseline known-fails (compare names). No new failures.
- [ ] `npx tsc --noEmit` → exit 0. `npx eslint src` → 0 errors (pre-existing warnings OK).
- [ ] Grep audit that out-of-scope dialogs were not changed: `git diff --name-only master -- 'src/**'` — confirm none of the §5.2 files (CustomerFormDialog, ProductCategoriesPage, WarehousesPage, ResolveStockDialog, ResaleTrackingPage, Kship*Picker, KiotVietPrecheckDialog, inventory/ImportExcelDialog, OrderDetailPage cancel, FacebookAds/GoogleAdsPage, BlocklistPage, LoyalCustomersPage, RolesPage, *Management.tsx error dialogs) appear.
- [ ] Update `docs/dialog-form-landscape-standard.md` §9 "Nhật ký thực hiện" with done/not-done per phase.
- [ ] Commit: `git commit -m "docs: update dialog standard execution log"`.

### Task 20: Push + MR
- [ ] `git push -u origin feature/dialog-form-landscape`
- [ ] Report the MR link to the user.

---

## Self-review notes
- **Spec coverage:** D1 scope (§5.1/§5.2 → Tasks 3–18 only touch in-scope; Task 19 audits §5.2 untouched). D2/D3 cap (Task 1 variant `max-w-[1480px] max-h-[920px]`). D4 columns (FormGrid + per-task columns). D5 single MR, phased commits (Phases 1–5). Testing §6 → Tasks 1, 2, 3, 19.
- **Portal/submit gotcha:** handled by `FormDialogShell.onSubmit` wrapping body+footer in `<form>` (Task 2, recipe step 2).
- **Type consistency:** `FormDialogShell` props (`title`, `description`, `footer`, `onSubmit`, `bodyClassName`) and `FormGrid` (`columns: 2|3`) are referenced identically across all tasks.
- **Manual visual pass:** per-form column choices are best-effort from the survey; the executor must eyeball each migrated dialog (Task 19) and adjust `columns`/`col-span-full` where content looks unbalanced.
