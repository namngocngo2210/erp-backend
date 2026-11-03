# FE-04: Refactor God Components Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tách 5 file khổng lồ (`useOrdersData` 2304, `OrdersPage` 2508, `OrderDetailPage` 2187, `OrdersTable` 1977, `App.tsx` 1872) thành các đơn vị nhỏ, một-trách-nhiệm; đồng thời giảm re-render rộng bằng tách state theo phạm vi + memo hóa hợp lý.

**Architecture:** Áp dụng "container/presentational" + "custom hooks theo trách nhiệm". `useOrdersData` (god hook, 35 useState/50 useCallback) tách thành `useOrdersFilters` (state filter + đồng bộ URL), `useOrdersQuery` (data, dựa TanStack Query của FE-02), `useOrderMutations` (create/update/delete), `useOrderColumnsState` (cấu hình cột hiển thị). Mỗi tách kèm test cho hành vi thuần (reducer/helper) để chống hồi quy. Làm tăng dần, mỗi lần tách một mảnh + giữ app xanh.

**Tech Stack:** React 18, TanStack Query (FE-02), Vitest + Testing Library.

Giải quyết: P1 (god components), P2 (re-render rộng / thiếu memo), P3 (arrow inline).

**Phụ thuộc:** Làm SAU FE-02 (data) và FE-03 (routing) — vì phần lớn khối lượng của `useOrdersData`/`OrdersPage`/`App.tsx` sẽ tự tiêu biến khi data chuyển sang Query và routing tách khỏi App. Chỉ refactor phần CÒN LẠI.

---

> **Nguyên tắc chống hồi quy:** đây là refactor file rất lớn. Trước khi tách, thêm "characterization test" cho các hàm logic thuần (đã export sẵn như `getBranchIdsForChannel`, `getBranchOptionsForChannel` trong `use-orders-data.ts:106-118`) để khóa hành vi. Mỗi task tách giữ test xanh.

### Task 1: Khóa hành vi helper thuần trước khi tách

**Files:**
- Test: `frontend/src/features/orders/__tests__/branch-helpers.test.ts`

- [ ] **Step 1: Viết test cho helper thuần đã export**

```ts
import { describe, it, expect } from "vitest";
import { getBranchIdsForChannel, getBranchOptionsForChannel } from "@/features/orders/use-orders-data";

describe("branch helpers", () => {
  it("đưa branch_id mặc định lên đầu, loại trùng", () => {
    expect(getBranchIdsForChannel({ id: 1, name: "A", branch_id: "10", branch_ids: ["10", "20"] }))
      .toEqual(["10", "20"]);
  });
  it("trả [] khi channel null", () => {
    expect(getBranchIdsForChannel(null)).toEqual([]);
  });
  it("options là {value,label} string", () => {
    expect(getBranchOptionsForChannel({ id: 1, name: "A", branch_id: "10", branch_ids: [] }))
      .toEqual([{ value: "10", label: "10" }]);
  });
});
```

- [ ] **Step 2: Chạy test → PASS (đang khóa hành vi hiện tại)**

Run: `cd frontend && npx vitest run src/features/orders/__tests__/branch-helpers.test.ts`
Expected: PASS. Nếu FAIL, sửa test cho khớp hành vi thật trước khi đi tiếp.

- [ ] **Step 3: Commit**

```bash
cd frontend && git add -A && git commit -m "test: characterize order branch helpers"
```

---

### Task 2: Tách module helper thuần ra khỏi god hook

**Files:**
- Create: `frontend/src/features/orders/order-branch.ts`
- Modify: `frontend/src/features/orders/use-orders-data.ts`
- Modify: `frontend/src/features/orders/__tests__/branch-helpers.test.ts` (đổi import)

- [ ] **Step 1: Chuyển `getBranchIdsForChannel`/`getBranchOptionsForChannel` (+ JSDoc) sang `order-branch.ts`**

```ts
import type { KiotVietOption, BranchSelectOption } from "@/features/orders/order-types";

export function getBranchIdsForChannel(channel?: KiotVietOption | null): string[] {
  if (!channel) return [];
  const ids = [...(channel.branch_ids ?? [])];
  if (channel.branch_id && !ids.includes(channel.branch_id)) {
    ids.unshift(channel.branch_id);
  }
  return ids;
}

export function getBranchOptionsForChannel(channel?: KiotVietOption | null): BranchSelectOption[] {
  return getBranchIdsForChannel(channel).map((id) => ({ value: String(id), label: String(id) }));
}
```

> Di chuyển luôn type `KiotVietOption`/`BranchSelectOption` vào `order-types.ts` nếu chúng đang khai báo trong `use-orders-data.ts`.

- [ ] **Step 2: Trong `use-orders-data.ts` re-export để không vỡ import cũ**

```ts
export { getBranchIdsForChannel, getBranchOptionsForChannel } from "@/features/orders/order-branch";
```

- [ ] **Step 3: Đổi import trong test sang `order-branch`, chạy lại → PASS**

Run: `cd frontend && npx vitest run src/features/orders/__tests__/branch-helpers.test.ts`
Expected: PASS.

- [ ] **Step 4: Verify build**

Run: `cd frontend && npm run build`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add -A && git commit -m "refactor: extract order branch helpers module"
```

---

### Task 3: Tách `useOrdersFilters` (state filter + đồng bộ URL)

**Files:**
- Create: `frontend/src/features/orders/use-orders-filters.ts`
- Test: `frontend/src/features/orders/__tests__/use-orders-filters.test.tsx`
- Modify: `frontend/src/features/orders/use-orders-data.ts`

> Phần filter là cụm `useState` lớn nhất trong god hook (q, dateFilter, status, channel, product, staff, kiotvietChannel, branchId, invoiceStatus, sort, …). Gom vào một hook + (sau FE-03) đồng bộ với `useSearchParams`.

- [ ] **Step 1: Viết test hành vi cho hook filter**

```tsx
import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useOrdersFilters } from "@/features/orders/use-orders-filters";

describe("useOrdersFilters", () => {
  it("set rồi reset trả về default", () => {
    const { result } = renderHook(() => useOrdersFilters());
    act(() => result.current.setStatus(["3"]));
    expect(result.current.filters.status).toEqual(["3"]);
    act(() => result.current.reset());
    expect(result.current.filters.status).toEqual([]);
  });
});
```

- [ ] **Step 2: Chạy test → FAIL**

Run: `cd frontend && npx vitest run src/features/orders/__tests__/use-orders-filters.test.tsx`
Expected: FAIL — hook chưa tồn tại.

- [ ] **Step 3: Tạo `use-orders-filters.ts` gom toàn bộ state filter từ god hook**

Tạo `OrdersFilters` type (gom từ `LoadOrdersOverrides` trong `use-orders-data.ts:69-89`), một `useReducer` hoặc nhóm `useState` với các setter (`setStatus`, `setChannel`, …) + `reset()`. Bê nguyên giá trị default hiện tại từ god hook để không đổi hành vi.

- [ ] **Step 4: Chạy test → PASS**

Run: `cd frontend && npx vitest run src/features/orders/__tests__/use-orders-filters.test.tsx`
Expected: PASS.

- [ ] **Step 5: Thay phần filter trong `use-orders-data.ts` bằng `useOrdersFilters()`**

Xóa các `useState` filter trong god hook, thay bằng `const filters = useOrdersFilters()`, expose qua return object cũ để `OrdersPage` không phải đổi (giữ tương thích API).

- [ ] **Step 6: Verify build + smoke test trang Orders (lọc hoạt động)**

Run: `cd frontend && npm run build`
Expected: pass; lọc đơn theo trạng thái/kênh hoạt động như cũ.

- [ ] **Step 7: Commit**

```bash
cd frontend && git add -A && git commit -m "refactor: extract useOrdersFilters from orders god hook"
```

---

### Task 4: Tách `useOrderMutations` (create/update/delete)

**Files:**
- Create: `frontend/src/features/orders/use-order-mutations.ts`
- Modify: `frontend/src/features/orders/use-orders-data.ts`

> Dựa trên `lib/api/orders.ts` (tạo ở FE-02). Gom toàn bộ logic tạo/sửa/xóa đơn + invalidate query.

- [ ] **Step 1: Tạo `use-order-mutations.ts`**

```ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createOrder, updateOrder, deleteOrder } from "@/lib/api/orders";
import { orderKeys } from "@/features/orders/queries";
import type { CreateOrderPayload } from "@/features/orders/use-orders-data";

export function useOrderMutations() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: orderKeys.lists() });

  const create = useMutation({ mutationFn: (p: CreateOrderPayload) => createOrder(p), onSuccess: invalidate });
  const update = useMutation({ mutationFn: (args: { id: number; patch: Partial<CreateOrderPayload> }) => updateOrder(args.id, args.patch), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: (id: number) => deleteOrder(id), onSuccess: invalidate });

  return { create, update, remove };
}
```

> `CreateOrderPayload` đã có sẵn trong `use-orders-data.ts:11-56`; cân nhắc chuyển sang `order-types.ts` để tránh import vòng.

- [ ] **Step 2: Thay logic mutation thủ công trong god hook bằng `useOrderMutations()`**

Xóa các `useCallback` gọi `fetchJson` POST/PATCH/DELETE trong `use-orders-data.ts`, thay bằng hook trên; giữ tên field trong return object cho tương thích.

- [ ] **Step 3: Verify build + smoke test (tạo/sửa/xóa đơn)**

Run: `cd frontend && npm run build`
Expected: pass; thao tác đơn hoạt động, list tự refetch.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add -A && git commit -m "refactor: extract useOrderMutations"
```

---

### Task 5: Co `OrdersPage` thành container + presentational + giảm re-render

**Files:**
- Modify: `frontend/src/pages/OrdersPage.tsx`
- Create: `frontend/src/features/orders/OrdersToolbar.tsx`
- Create: `frontend/src/features/orders/OrdersPageContainer.tsx` (nếu cần)

- [ ] **Step 1: Tách thanh filter/toolbar (cụm 261 arrow inline) ra `OrdersToolbar.tsx`**

Đưa JSX filter + handler ra component con nhận props từ `useOrdersFilters`. Bọc bằng `React.memo`. Handler truyền xuống dùng `useCallback` (đã sẵn nhiều ở hook) để memo có tác dụng.

- [ ] **Step 2: Bọc các row/cell component nặng bằng `React.memo`**

Xác định component render trong vòng lặp list (cell renderer trong `order-grid-columns.tsx` đã memo; kiểm tra `OrdersTable.tsx` các card/row chưa memo) và bọc `memo`.

- [ ] **Step 3: Verify build + đo cảm quan re-render**

Run: `cd frontend && npm run build`
Expected: pass. Dùng React DevTools Profiler: gõ vào ô search filter không còn làm cả bảng đơn re-render.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add -A && git commit -m "refactor: split OrdersPage toolbar and memoize rows"
```

---

### Task 6: Thu nhỏ `App.tsx` (sau khi FE-03 đã chuyển layout/routing đi)

**Files:**
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/app/useAuth.ts`

- [ ] **Step 1: Tách auth state (`user`, `authChecking`, login/logout, token bootstrap) ra `app/useAuth.ts`**

Gom ~10 useState liên quan auth + `useEffect` kiểm tra token + handler login/logout vào một hook `useAuth()` trả `{ user, authChecking, login, logout }`.

- [ ] **Step 2: App.tsx (hoặc RootLayout) chỉ còn gọi `useAuth()` + render layout**

Xóa các state đã chuyển đi; phần channels/staff/zalo/department nếu là dữ liệu server → chuyển sang query hooks (FE-02) thay vì state trong App.

- [ ] **Step 3: Verify build + smoke test đăng nhập/đăng xuất**

Run: `cd frontend && npm run build`
Expected: pass; login/logout + bootstrap session hoạt động.

- [ ] **Step 4: Kiểm tra kích thước file mục tiêu**

Run: `cd frontend && wc -l src/App.tsx src/pages/OrdersPage.tsx src/features/orders/use-orders-data.ts`
Expected: mỗi file giảm rõ rệt (mục tiêu < 800 dòng).

- [ ] **Step 5: Commit**

```bash
cd frontend && git add -A && git commit -m "refactor: extract useAuth and slim App"
```

---

### Task 7: Lặp cho `OrderDetailPage` và `OrdersTable`

> Áp dụng cùng pattern (tách helper thuần → characterization test → tách hook/sub-component → memo). Mỗi file một chuỗi commit riêng.

- [ ] `OrderDetailPage.tsx` (2187): tách section hiển thị (thông tin KH, sản phẩm, lịch sử) thành sub-component; data qua `features/orders/queries.ts`.
- [ ] `OrdersTable.tsx` (1977): tách cấu hình cột/cell renderer ra file riêng; memo row; data qua props/hook.

Mỗi file thực hiện: (1) test khóa helper thuần → (2) tách sub-component/hook → (3) memo → (4) build + smoke test → (5) commit.

---

## Self-review checklist
- [ ] Mọi helper thuần được tách đều có characterization test xanh.
- [ ] `use-orders-data.ts`, `OrdersPage.tsx`, `App.tsx` giảm đáng kể số dòng (mục tiêu <800).
- [ ] Re-render: gõ filter không làm toàn bộ list re-render (kiểm bằng Profiler).
- [ ] API return object của `useOrdersData` giữ tương thích trong suốt quá trình (không vỡ `OrdersPage`).
- [ ] `npm run build` + `npm run test` xanh sau mỗi task.

## Tiến độ (cập nhật khi thực hiện)
- Hook đã tách khỏi useOrdersData: ___
- LOC App.tsx / OrdersPage / useOrdersData: ___ / ___ / ___
