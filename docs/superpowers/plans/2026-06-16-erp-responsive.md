# ERP Responsive (Mobile) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** ERP dùng tốt trên điện thoại (~390px) + hoàn thiện `/m`, không phá density desktop.

**Architecture:** Mobile-first Tailwind. Primitive dùng chung `ResponsiveList` (table↔card theo `md`). Form dialog full-screen `< sm`. Sửa các grid không co. Card-view cho bảng list chính. Tham chiếu `docs/responsive-standard.md`, spec `docs/superpowers/specs/2026-06-16-erp-responsive-design.md`.

**Tech:** React + Vite + Tailwind + shadcn/ui; vitest + RTL. Branch `feature/erp-responsive`. Commit message English, no Co-Authored-By.

**Verify mỗi phase:** `cd frontend && npx tsc -p tsconfig.json --noEmit && npm run build && npx vitest run <relevant>`. Baseline có test FE fail sẵn (memory `known-failing-baseline-tests`) — không nhầm regression.

---

## PHASE 0 — Primitive + chuẩn dialog

### Task 0.1: `ResponsiveList` primitive
**Files:** Create `frontend/src/components/ui/responsive-list.tsx`; Test `frontend/src/components/ui/__tests__/responsive-list.test.tsx`.

- [ ] Implement: render `table` khi `useMediaQuery("(min-width: 768px)")` true, ngược lại map `items` qua `renderCard`. API:
```tsx
import { ReactNode } from "react";
import { useMediaQuery } from "@/hooks/use-media-query";

type Props<T> = {
  items: T[];
  table: ReactNode;
  renderCard: (item: T, index: number) => ReactNode;
  /** key cho mỗi card; mặc định index. */
  getKey?: (item: T, index: number) => string | number;
  emptyText?: string;
};

export function ResponsiveList<T>({ items, table, renderCard, getKey, emptyText }: Props<T>) {
  const isDesktop = useMediaQuery("(min-width: 768px)");
  if (isDesktop) return <>{table}</>;
  if (items.length === 0)
    return <p className="py-8 text-center text-sm text-muted-foreground">{emptyText ?? "Không có dữ liệu."}</p>;
  return (
    <div className="flex flex-col gap-2">
      {items.map((it, i) => (
        <div key={getKey ? getKey(it, i) : i}>{renderCard(it, i)}</div>
      ))}
    </div>
  );
}
```
- [ ] Confirm `frontend/src/hooks/use-media-query.ts` exists and exports `useMediaQuery` (audit said yes). If signature differs, adapt.
- [ ] Test: mock `useMediaQuery` → desktop renders `table` node; mobile renders cards from items; empty shows emptyText. Run vitest → PASS.
- [ ] Commit: `feat(ui): ResponsiveList primitive (table↔card by breakpoint)`

### Task 0.2: Form dialog full-screen on mobile
**Files:** Modify `frontend/src/components/ui/dialog.tsx` (the `form` size variant, audit line ~97-108).

- [ ] Read the `form` variant class. Add mobile-first full-screen: on `< sm` use `h-[100dvh] w-screen max-w-none rounded-none`, on `sm+` keep existing `sm:h-[90dvh] sm:max-h-[920px] sm:w-[95vw] sm:max-w-[1480px] sm:rounded-lg`. Net change: base = full-screen, `sm:` = current desktop sizing. Keep flex column + sticky header/footer behavior intact (check `form-dialog-content.tsx`).
- [ ] Verify: open any form dialog at 390px → fills screen, no rounded overflow; at desktop unchanged. Typecheck + build.
- [ ] Run existing dialog/form tests: `npx vitest run src/features/orders/__tests__/OrderFormDialog.test.tsx` (note: OrderFormDialog is a KNOWN baseline failure — confirm not newly broken by comparing to baseline behavior).
- [ ] Commit: `feat(ui): form dialogs go full-screen on mobile`

---

## PHASE 1 — Sửa grid không co (mechanical)

> Mỗi sửa: thêm base `grid-cols-1` và chuyển cấu hình nhiều cột hiện tại sang prefix `sm:`/`md:`. Sau mỗi file kiểm tra DevTools 390px. Có thể gộp commit cuối phase.

### Task 1.1: CustomerFormDialog (BLOCKER)
**Files:** `frontend/src/features/customers/components/CustomerFormDialog.tsx` (lines ~66, 87, 104).
- [ ] Read each `grid-cols-4 items-center gap-4` row (label + field layout). Đổi sang mobile: nhãn trên, field dưới. Pattern: `grid grid-cols-1 gap-1.5 sm:grid-cols-4 sm:items-center sm:gap-4`. Đảm bảo label + input đều chiếm full width ở mobile (bỏ co cụm). Nếu field con có `col-span-3` thì thêm base `col-span-1 sm:col-span-3`.
- [ ] Verify 390px: form 1 cột, không tràn. tsc OK.

### Task 1.2: date-filter (BLOCKER/MAJOR)
**Files:** `frontend/src/components/ui/date-filter.tsx` (lines ~262, ~293).
- [ ] `:262` preset `grid grid-cols-5 gap-1` → `grid grid-cols-2 gap-1 sm:grid-cols-3 md:grid-cols-5` (nút preset xuống 2 cột ở phone, đủ chỗ chữ).
- [ ] `:293` `grid grid-cols-2 gap-2` (2 input ngày from/to) → `grid grid-cols-1 gap-2 sm:grid-cols-2`.
- [ ] Verify 390px.

### Task 1.3: Các grid-cols-2 thiếu base (MAJOR)
**Files:**
- `frontend/src/pages/ResaleTrackingPage.tsx:1117` — `grid grid-cols-2 gap-3` → `grid grid-cols-1 gap-3 sm:grid-cols-2`.
- `frontend/src/components/orders/Sheet3Section.tsx:173` — `grid grid-cols-2 gap-3` → `grid grid-cols-1 gap-3 sm:grid-cols-2`.
- `frontend/src/pages/SyncPage.tsx:492` — `grid grid-cols-2 gap-3 sm:grid-cols-4` → `grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4`.
- [ ] Sửa từng dòng, đọc context quanh để chắc đúng grid field (không phá layout cố ý).
- [ ] Verify 390px mỗi màn. tsc + build.
- [ ] Commit (gộp phase): `fix(responsive): collapse non-responsive field grids on mobile`

---

## PHASE 2 — Dialog polish + dropdown width

### Task 2.1: Cap width TopNav dropdowns
**Files:** `frontend/src/components/TopNav.tsx` (notifications `w-80` ~line 145; user menu `w-44` ~237 OK).
- [ ] `w-80` → `w-[min(20rem,calc(100vw-1.5rem))]`. (Đang ẩn `< md` nhưng cứ cap cho chắc.)
- [ ] Rà thêm dropdown/popover khác có `w-[2-9]\d` cố định trong shell → cap tương tự nếu có nguy cơ tràn.

### Task 2.2: Rà dialog lẻ
**Files:** `frontend/src/features/orders/KshipDeliveryDialog.tsx:247` (bảng `min-w-[1220px]`).
- [ ] Xác nhận bảng nằm trong `overflow-x-auto` (audit nói có). Nếu chưa, bọc `DataTableShell`/`overflow-x-auto`. Không bỏ min-w (bảng giao hàng nhiều cột — chấp nhận scroll). Đảm bảo header dialog/nút đóng vẫn truy cập được ở 390px.
- [ ] Verify build/tsc.
- [ ] Commit: `fix(responsive): cap dropdown width + ensure wide dialog tables scroll on mobile`

---

## PHASE 3 — Card-view cho bảng list (theo module)

> Mỗi task: dùng `ResponsiveList` bọc bảng hiện có + viết `*Card` component cho mobile (field chính + actions). Bảng desktop GIỮ NGUYÊN. Viết 1 test card render từ mock.

### Task 3.1: HR Employees
**Files:** `frontend/src/features/hr/components/EmployeesTable.tsx` (6 cột: code, name, department, job_title, status, actions). Create `EmployeeCard` (cùng file hoặc `EmployeeCard.tsx`). Test.
- [ ] Bọc nơi render `EmployeesTable` (tìm trong `EmployeesPage`) bằng `ResponsiveList` với `renderCard={(e)=><EmployeeCard …/>}`. Card hiển thị: full_name (đậm) + employee_code, department, job_title, status badge, actions. Reuse các handler/labels sẵn có.
- [ ] Test: mobile (mock useMediaQuery=false) render card có tên + mã NV; desktop render bảng. Run vitest.
- [ ] Commit: `feat(responsive): mobile card view for HR employees list`

### Task 3.2: Customers
**Files:** `frontend/src/features/customers/components/CustomersTable.tsx` (dynamic 8-12 cột). Create `CustomerCard`. Test.
- [ ] Card hiển thị field chính: tên KH, sđt, kênh/nhóm, tổng đơn/giá trị nếu có. Bọc `ResponsiveList`. Giữ chọn (checkbox) nếu cần ở mobile (card có checkbox góc).
- [ ] Test + commit: `feat(responsive): mobile card view for customers list`

### Task 3.3: Inventory Stock + Returns
**Files:** `frontend/src/features/inventory/pages/StockPage.tsx` (8 cột) + returns table. Create cards. Test.
- [ ] Card Stock: kho, SKU/sản phẩm, sellable/warranty/defect/total. ResponsiveList. Returns tương tự.
- [ ] Test + commit: `feat(responsive): mobile card view for inventory stock & returns`

### Task 3.4: Google/Facebook Ads
**Files:** `frontend/src/pages/GoogleAdsPage.tsx`, `frontend/src/pages/FacebookAdsPage.tsx` (15+ cột). Create card với các metric chính (tên campaign, spend, kết quả). Test.
- [ ] ResponsiveList + card hiển thị 4-6 metric quan trọng nhất; còn lại ẩn ở mobile.
- [ ] Test + commit: `feat(responsive): mobile card view for ads reports`

### Task 3.5: AuditLogs
**Files:** `frontend/src/pages/AuditLogsPage.tsx` (hoặc feature audit). Card: thời gian, user, hành động, đối tượng.
- [ ] ResponsiveList + card. Test + commit: `feat(responsive): mobile card view for audit logs`

---

## PHASE 4 — Hoàn thiện `/m` ESS

**Files:** `frontend/src/features/ess/*` (MobileLayout, BottomTabNav, pages, MobileLoginView).
- [ ] MobileLayout: bottom nav cộng `pb-[env(safe-area-inset-bottom)]`; main `pb` đủ tránh che bởi nav; header top cộng safe-area-top nếu cần.
- [ ] Nút chạm chính (login, gửi đơn, check-in) cao tối thiểu `h-11`.
- [ ] Rà trạng thái loading/empty/error nhất quán trên 4 page (đã có phần lớn — bổ sung empty/error còn thiếu).
- [ ] MyLeavePage form: field full width, nút lớn; MyAttendancePage: input month dễ chạm.
- [ ] Verify 390px toàn luồng; build + tsc + `npx vitest run src/features/ess`.
- [ ] Commit: `feat(ess): mobile polish — safe-area, touch targets, states`

---

## Hoàn tất
- [ ] Build + tsc + full relevant vitest xanh.
- [ ] Cập nhật spec mục Phase: đánh dấu hoàn thành + note.
- [ ] Push branch `feature/erp-responsive`, gửi link MR. KHÔNG tự merge.
