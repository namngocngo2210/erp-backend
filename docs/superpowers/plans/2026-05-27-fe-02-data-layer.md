# FE-02: Data Layer (TanStack Query + API modules) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm tầng caching/data-fetching bằng TanStack Query, chuẩn hóa mọi gọi API thành module trong `lib/api/` (theo mẫu `customers.ts`), xóa dead code `lib/fetch-json.ts`, và migrate fetch-in-effect sang query hooks theo từng resource.

**Architecture:** `QueryClientProvider` bọc app tại `main.tsx`. Mỗi resource có 1 file `lib/api/<resource>.ts` (hàm async thuần bọc `fetchJson` từ `services/http.ts`) và 1 file `features/<resource>/queries.ts` (các hook `useXxxQuery`/`useXxxMutation`). Component chỉ gọi hook, không tự `fetch` + `useEffect` + `useState` nữa. Migrate dần, resource đơn giản trước (sync, audit, products) rồi tới orders/customers.

**Tech Stack:** @tanstack/react-query v5, fetchJson (services/http.ts đã có sẵn auth/refresh).

Giải quyết: P1 (không có caching), P2 (dead `fetch-json.ts`, fetch rải rác 11 file).

**Phụ thuộc:** Nên làm sau FE-01 (để code mới được lint).

---

### Task 1: Xóa dead code `lib/fetch-json.ts`

**Files:**
- Delete: `frontend/src/lib/fetch-json.ts`

- [ ] **Step 1: Xác nhận 0 import**

Run: `cd frontend && grep -rn "lib/fetch-json" src | wc -l`
Expected: `0`.

- [ ] **Step 2: Xóa file**

```bash
cd frontend && git rm src/lib/fetch-json.ts
```

- [ ] **Step 3: Verify build vẫn pass**

Run: `cd frontend && npm run build`
Expected: build thành công.

- [ ] **Step 4: Commit**

```bash
cd frontend && git commit -m "chore: remove unused lib/fetch-json.ts"
```

---

### Task 2: Cài & cấu hình TanStack Query

**Files:**
- Create: `frontend/src/lib/query-client.ts`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/package.json`

- [ ] **Step 1: Cài lib**

```bash
cd frontend && npm i @tanstack/react-query@^5
```

- [ ] **Step 2: Tạo `lib/query-client.ts`**

```ts
import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});
```

- [ ] **Step 3: Bọc app trong `main.tsx`**

Sửa `main.tsx` để thêm provider (giữ nguyên `I18nProvider` và nhánh warranty):

```tsx
import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { I18nProvider } from "./i18n";
import { queryClient } from "./lib/query-client";
import "./styles.css";

const WarrantyPage = lazy(() => import("./pages/WarrantyPage"));
const isWarrantyRoute = window.location.pathname.toLowerCase().startsWith("/warranty");

ReactDOM.createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <I18nProvider>
      {isWarrantyRoute ? (
        <Suspense fallback={null}>
          <WarrantyPage />
        </Suspense>
      ) : (
        <App />
      )}
    </I18nProvider>
  </QueryClientProvider>,
);
```

- [ ] **Step 4: Verify build**

Run: `cd frontend && npm run build`
Expected: thành công.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add -A && git commit -m "feat: add tanstack query client provider"
```

---

### Task 3: Tạo API module cho resource đầu tiên (sync) — exemplar

> Đây là mẫu chuẩn để nhân rộng. `SyncPage.tsx` hiện fetch inline (`fetchJson("/api/webhook/sync/channels")` trong useEffect). Tách ra module + query hook.

**Files:**
- Create: `frontend/src/lib/api/sync.ts`
- Test: `frontend/src/lib/api/__tests__/sync.test.ts`

- [ ] **Step 1: Viết test thất bại cho API module**

```ts
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/http", () => ({ fetchJson: vi.fn() }));
import { fetchJson } from "@/services/http";
import { getSyncChannels } from "@/lib/api/sync";

describe("sync api", () => {
  beforeEach(() => vi.clearAllMocks());

  it("getSyncChannels gọi đúng endpoint", async () => {
    (fetchJson as ReturnType<typeof vi.fn>).mockResolvedValue([{ id: 1 }]);
    const res = await getSyncChannels();
    expect(fetchJson).toHaveBeenCalledWith("/api/webhook/sync/channels");
    expect(res).toEqual([{ id: 1 }]);
  });
});
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd frontend && npx vitest run src/lib/api/__tests__/sync.test.ts`
Expected: FAIL — `getSyncChannels` chưa tồn tại.

- [ ] **Step 3: Viết module `lib/api/sync.ts` (theo mẫu customers.ts)**

```ts
import { fetchJson } from "@/services/http";

export interface SyncChannel {
  id: number;
  code: string;
  name: string;
}

export async function getSyncChannels(): Promise<SyncChannel[]> {
  return fetchJson("/api/webhook/sync/channels");
}

export async function runSync(channelId: number): Promise<void> {
  return fetchJson("/api/webhook/sync/run", {
    method: "POST",
    body: JSON.stringify({ channel_id: channelId }),
  });
}
```

> Lưu ý: `runSync` dùng `fetchJson` thay cho `fetch(...)` trần ở `SyncPage:187` — được auth/refresh tự động.

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `cd frontend && npx vitest run src/lib/api/__tests__/sync.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add -A && git commit -m "feat: add sync api module"
```

---

### Task 4: Tạo query hooks cho sync + migrate SyncPage

**Files:**
- Create: `frontend/src/features/sync/queries.ts`
- Modify: `frontend/src/pages/SyncPage.tsx`

- [ ] **Step 1: Tạo `features/sync/queries.ts`**

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getSyncChannels, runSync } from "@/lib/api/sync";

export const syncKeys = {
  channels: ["sync", "channels"] as const,
};

export function useSyncChannels() {
  return useQuery({
    queryKey: syncKeys.channels,
    queryFn: getSyncChannels,
  });
}

export function useRunSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: runSync,
    onSuccess: () => qc.invalidateQueries({ queryKey: syncKeys.channels }),
  });
}
```

- [ ] **Step 2: Thay fetch-in-effect trong SyncPage bằng hook**

Trong `SyncPage.tsx`: xóa `useState` cho channels + `useEffect` gọi `fetchJson("/api/webhook/sync/channels")` (quanh dòng 99-113), thay bằng:

```tsx
const { data: channels = [], isLoading, error } = useSyncChannels();
const runSyncMutation = useRunSync();
```

Và thay handler chạy sync bằng `runSyncMutation.mutate(channelId)`. Loại bỏ state `loading` thủ công nếu chỉ phục vụ fetch này.

- [ ] **Step 3: Verify build + chạy app, mở trang Sync**

Run: `cd frontend && npm run build`
Expected: build pass. Mở app, vào trang Sync, danh sách channel hiển thị, nút sync hoạt động, refetch sau khi sync.

- [ ] **Step 4: Commit**

```bash
cd frontend && git add -A && git commit -m "refactor: migrate SyncPage to tanstack query"
```

---

### Task 5: Nhân rộng pattern theo từng resource (lặp)

> Lặp lại Task 3-4 cho từng resource đang fetch inline. Thứ tự ưu tiên (đơn giản → phức tạp). Mỗi resource = 1 PR/commit độc lập, dùng đúng mẫu trên.

**Danh sách resource cần migrate** (file đang fetch inline — từ báo cáo):

- [ ] `src/pages/AuditLogsPage.tsx` → `lib/api/audit-logs.ts` + `features/audit/queries.ts`
- [ ] `src/pages/WarrantyPage.tsx` → `lib/api/warranty.ts` + `features/warranty/queries.ts`
- [ ] `src/pages/SyncPage.tsx` → (đã làm ở Task 4)
- [ ] `src/pages/CustomersPage.tsx` → dùng lại `lib/api/customers.ts` đã có, chỉ thêm `features/customers/queries.ts` và bỏ fetch-in-effect
- [ ] `src/pages/OrderDetailPage.tsx` → `lib/api/orders.ts` (chi tiết đơn) + `features/orders/queries.ts`
- [ ] `src/features/orders/OrderFormDialog.tsx` → dùng `lib/api/orders.ts`
- [ ] `src/features/orders/OrdersGridDesktop.tsx` → query hooks orders
- [ ] `src/components/OrdersTable.tsx` → query hooks orders
- [ ] `src/features/orders/use-orders-data.ts` → (xử lý kỹ trong FE-04, vì gắn với refactor god hook)

**Mỗi resource thực hiện 6 step:**

- [ ] **Step 1:** Viết test cho `lib/api/<resource>.ts` (mock `@/services/http`, assert endpoint + method — theo mẫu Task 3 Step 1).
- [ ] **Step 2:** Chạy test → FAIL.
- [ ] **Step 3:** Viết module API (hàm async bọc `fetchJson`, có interface type cho payload/response — KHÔNG dùng `any`).
- [ ] **Step 4:** Chạy test → PASS.
- [ ] **Step 5:** Tạo `features/<resource>/queries.ts` (query/mutation hooks + `queryKeys`) và thay fetch-in-effect trong component bằng hook; xóa `useState`/`useEffect` phục vụ fetch.
- [ ] **Step 6:** `npm run build` pass + smoke test trang đó, rồi commit `refactor: migrate <resource> to tanstack query`.

---

### Task 6: Chốt — không còn fetch trần ngoài services/api

**Files:**
- Modify: `frontend/eslint.config.js` (thêm rule chặn `fetch(` ngoài tầng cho phép)

- [ ] **Step 1: Đếm fetch trần còn lại**

Run: `cd frontend && grep -rn "fetch(" src --include=*.tsx --include=*.ts | grep -v "services/http.ts" | grep -v "lib/api/" | grep -v "fetchJson" | wc -l`
Expected: tiến dần về `0` (trừ websocket).

- [ ] **Step 2: Thêm ESLint `no-restricted-syntax`/`no-restricted-globals` chặn `fetch` ngoài `services/`**

Trong `eslint.config.js` thêm override:

```js
{
  files: ["src/**/*.{ts,tsx}"],
  ignores: ["src/services/**", "src/lib/api/**"],
  rules: {
    "no-restricted-globals": ["error", { name: "fetch", message: "Dùng module trong lib/api/ thay vì fetch trực tiếp." }],
  },
},
```

- [ ] **Step 3: Verify lint pass**

Run: `cd frontend && npm run lint`
Expected: exit 0 (sau khi đã migrate hết).

- [ ] **Step 4: Commit**

```bash
cd frontend && git add -A && git commit -m "chore: restrict raw fetch outside api layer"
```

---

## Self-review checklist
- [ ] `lib/fetch-json.ts` đã bị xóa.
- [ ] `QueryClientProvider` bọc app trong `main.tsx`.
- [ ] Mỗi resource migrate có cặp `lib/api/<r>.ts` + `features/<r>/queries.ts`, có test cho module API.
- [ ] Không còn `fetch(` trần ngoài `services/` & `lib/api/` (trừ websocket).
- [ ] Component không còn `useState`+`useEffect` chỉ để fetch.

## Tiến độ (cập nhật khi thực hiện)
- Resource đã migrate: ___
- fetch trần còn lại: ___
