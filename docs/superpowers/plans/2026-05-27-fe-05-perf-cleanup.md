# FE-05: Performance & Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dọn các vấn đề hiệu năng & gọn gàng còn lại: polling chưa kiểm soát (18 timer), bundle vendor nặng (`@base-ui` chỉ dùng 1 chỗ → bỏ), tách file i18n khổng lồ, và thiết lập baseline đo bundle để theo dõi.

**Architecture:** Chuẩn hóa polling thành một hook `usePolling` tự dừng khi tab ẩn (Page Visibility API) và tự cleanup; nếu polling đó là fetch dữ liệu, ưu tiên dùng `refetchInterval` của TanStack Query (FE-02). Loại bỏ phụ thuộc thừa. Tách `i18n/messages.ts` (1322 dòng) theo namespace. Lưu baseline bundle size vào docs để so sánh.

**Tech Stack:** TanStack Query `refetchInterval`, Page Visibility API, rollup-plugin-visualizer (đã có).

Giải quyết: P2 (polling, bundle vendor), P3 (i18n lớn, arrow inline còn lại).

**Phụ thuộc:** Làm sau FE-02 (để chuyển polling-fetch sang `refetchInterval`). Độc lập với FE-03/FE-04.

---

### Task 1: Đo baseline bundle trước khi tối ưu

**Files:**
- Create: `docs/superpowers/plans/fe-05-bundle-baseline.md`

- [ ] **Step 1: Build analyze**

Run: `cd frontend && npm run build:analyze`
Expected: mở `dist/report.html`; ghi lại size (gzip) của các chunk chính: `vendor-react`, `vendor-grid`, `vendor-ui`, `index`, các page chunk.

- [ ] **Step 2: Ghi baseline vào file**

Tạo `fe-05-bundle-baseline.md` ghi bảng: chunk | raw | gzip. Đây là mốc so sánh sau tối ưu.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/fe-05-bundle-baseline.md && git commit -m "docs: record frontend bundle baseline"
```

---

### Task 2: Bỏ phụ thuộc thừa `@base-ui`

**Files:**
- Modify: file duy nhất đang dùng `@base-ui` (xác định ở Step 1)
- Modify: `frontend/package.json`

- [ ] **Step 1: Tìm chỗ dùng `@base-ui`**

Run: `cd frontend && grep -rn "@base-ui" src`
Expected: 1 file. Ghi lại component đang dùng.

- [ ] **Step 2: Thay bằng shadcn/ui component tương đương hoặc HTML thuần**

Sửa file đó dùng component sẵn có trong `src/components/ui/` (đã có 17 component). Nếu là primitive đơn giản (vd Dialog/Popover) → dùng bản shadcn tương ứng.

- [ ] **Step 3: Gỡ dependency**

```bash
cd frontend && npm uninstall @base-ui/react
```

- [ ] **Step 4: Verify build + smoke test màn dùng component đó**

Run: `cd frontend && npm run build && grep -rn "@base-ui" src | wc -l`
Expected: build pass, grep trả `0`.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add -A && git commit -m "chore: drop redundant @base-ui dependency"
```

---

### Task 3: Hook polling tự dừng khi tab ẩn

**Files:**
- Create: `frontend/src/hooks/use-polling.ts`
- Test: `frontend/src/hooks/__tests__/use-polling.test.tsx`

- [ ] **Step 1: Viết test thất bại — không gọi callback khi document.hidden**

```tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { usePolling } from "@/hooks/use-polling";

describe("usePolling", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("không chạy callback khi tab ẩn", () => {
    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    const cb = vi.fn();
    renderHook(() => usePolling(cb, 1000));
    vi.advanceTimersByTime(3000);
    expect(cb).not.toHaveBeenCalled();
  });

  it("chạy callback theo interval khi tab hiện", () => {
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    const cb = vi.fn();
    renderHook(() => usePolling(cb, 1000));
    vi.advanceTimersByTime(3000);
    expect(cb).toHaveBeenCalledTimes(3);
  });
});
```

- [ ] **Step 2: Chạy test → FAIL**

Run: `cd frontend && npx vitest run src/hooks/__tests__/use-polling.test.tsx`
Expected: FAIL — hook chưa tồn tại.

- [ ] **Step 3: Viết `use-polling.ts`**

```tsx
import { useEffect, useRef } from "react";

export function usePolling(callback: () => void, intervalMs: number, enabled = true) {
  const saved = useRef(callback);
  saved.current = callback;

  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => {
      if (!document.hidden) saved.current();
    }, intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, enabled]);
}
```

- [ ] **Step 4: Chạy test → PASS**

Run: `cd frontend && npx vitest run src/hooks/__tests__/use-polling.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add -A && git commit -m "feat: add visibility-aware usePolling hook"
```

---

### Task 4: Thay các timer thủ công bằng usePolling / refetchInterval

**Files:**
- Modify: các file có `setInterval`/`setTimeout` (xác định ở Step 1)

- [ ] **Step 1: Liệt kê timer hiện có**

Run: `cd frontend && grep -rn "setInterval\|setTimeout" src`
Expected: ~18 chỗ. Phân loại: (a) polling fetch dữ liệu, (b) debounce/delay UI.

- [ ] **Step 2: Với polling fetch — chuyển sang `refetchInterval` của query**

Trong query hook tương ứng (FE-02) thêm `refetchInterval: 30_000, refetchIntervalInBackground: false`. Xóa `setInterval` thủ công + state liên quan.

- [ ] **Step 3: Với polling non-query — thay bằng `usePolling`**

Thay `setInterval` trần bằng `usePolling(fn, ms, enabled)`. Đảm bảo có cleanup (hook tự lo).

- [ ] **Step 4: Verify build + kiểm cleanup (không còn interval chạy khi rời trang)**

Run: `cd frontend && npm run build && grep -rn "setInterval" src | grep -v use-polling.ts | wc -l`
Expected: build pass; số `setInterval` trần giảm về tối thiểu (chỉ còn trong `use-polling.ts`).

- [ ] **Step 5: Commit**

```bash
cd frontend && git add -A && git commit -m "refactor: centralize polling via usePolling/refetchInterval"
```

---

### Task 5: Tách i18n messages theo namespace

**Files:**
- Create: `frontend/src/i18n/messages/orders.ts`, `.../common.ts`, `.../customers.ts`, … (theo nhóm)
- Modify: `frontend/src/i18n/messages.ts` (gom lại từ các namespace)

- [ ] **Step 1: Khảo sát nhóm key hiện có**

Run: `cd frontend && grep -oE "^\s+[a-zA-Z_]+:" src/i18n/messages.ts | head -40`
Expected: thấy các nhóm prefix (orders, customers, common, …) để chia file.

- [ ] **Step 2: Tách từng namespace ra file riêng, giữ nguyên key/value**

Cắt từng cụm sang `messages/<ns>.ts` export object con. Không đổi nội dung text (tránh vỡ test i18n hiện có).

- [ ] **Step 3: `messages.ts` gom lại bằng spread**

```ts
import { common } from "./messages/common";
import { orders } from "./messages/orders";
// ... các namespace khác
export const messages = { vi: { ...common.vi, ...orders.vi }, en: { ...common.en, ...orders.en } };
```

> Cấu trúc cuối phải khớp shape mà `I18nProvider`/`useI18n` đang tiêu thụ — kiểm bằng cách giữ test i18n hiện có xanh.

- [ ] **Step 4: Verify test i18n + build**

Run: `cd frontend && npm run test && npm run build`
Expected: test i18n (nếu có) xanh, build pass, UI không mất chữ.

- [ ] **Step 5: Bỏ `messages.ts` khỏi `.prettierignore`/eslint ignore nếu giờ đã nhỏ**

Sửa `.prettierignore` + `eslint.config.js` (Task FE-01) bỏ dòng ignore `src/i18n/messages.ts`.

- [ ] **Step 6: Commit**

```bash
cd frontend && git add -A && git commit -m "refactor: split i18n messages by namespace"
```

---

### Task 6: Đo lại bundle, so với baseline

**Files:**
- Modify: `docs/superpowers/plans/fe-05-bundle-baseline.md` (thêm cột "sau")

- [ ] **Step 1: Build analyze lại**

Run: `cd frontend && npm run build:analyze`
Expected: ghi size mới các chunk.

- [ ] **Step 2: So sánh, ghi chênh lệch vào file baseline**

Cập nhật bảng: chunk | gzip trước | gzip sau | Δ. Kỳ vọng vendor-ui giảm (bỏ @base-ui), không chunk nào vượt `chunkSizeWarningLimit` (1200).

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/fe-05-bundle-baseline.md && git commit -m "docs: compare bundle size after cleanup"
```

---

## Self-review checklist
- [ ] Không còn `setInterval` trần ngoài `use-polling.ts`; polling dừng khi tab ẩn.
- [ ] `@base-ui` đã gỡ; `grep @base-ui src` = 0.
- [ ] `i18n/messages.ts` tách thành nhiều namespace; UI không mất chữ; test i18n xanh.
- [ ] Bundle baseline được ghi lại + so sánh trước/sau.
- [ ] `npm run build` + `npm run test` xanh.

## Tiến độ (cập nhật khi thực hiện)
- Timer còn lại: ___
- vendor-ui gzip trước/sau: ___ / ___
