# Báo cáo rà soát Frontend — desi-order

> Ngày rà soát: 2026-05-27 · Phạm vi: toàn bộ `frontend/src` (109 file TS/TSX, ~36k LOC) · Trọng tâm: **hiệu năng** & **cấu trúc code**

**Stack thực tế:** Vite 5 + React 18 SPA, TypeScript, Tailwind 3, ag-grid + recharts + react-select, `fetch` thuần.

> ⚠️ **Lưu ý chiến lược:** stack hiện tại lệch hẳn so với chuẩn trong `.claude/CLAUDE.md` (Next.js App Router + RSC + shadcn). Đây là SPA client-rendered. Không sai, nhưng cần là một **quyết định có chủ đích** vì ảnh hưởng SEO, TTFB và cách scale team.

## Điểm tổng quan

| Hạng mục | Điểm | Vấn đề chính |
|---|---|---|
| Cấu trúc code | 1.5/4 | God components/files >2000 dòng, không có lint, routing tự chế |
| Hiệu năng | 2/4 | Không có tầng caching/data-fetching; fetch-in-effect thủ công khắp nơi |
| Type safety | 2.5/4 | 39 chỗ `any`, hook trả `Promise<any>` |
| Tooling/chuẩn | 1/4 | Không có ESLint/Prettier, BOM trong tsconfig |

---

## A. Cấu trúc code

### 🔴 P1 — "God components" / file khổng lồ
Vi phạm SRP nghiêm trọng, là rủi ro bảo trì lớn nhất khi team lên 100-150 người:

| File | LOC | Dấu hiệu |
|---|---|---|
| `src/pages/OrdersPage.tsx` | 2508 | 32 useState, 261 arrow inline |
| `src/features/orders/use-orders-data.ts` | 2304 | **35 useState, 50 useCallback**, return object khổng lồ |
| `src/pages/OrderDetailPage.tsx` | 2187 | 214 arrow inline |
| `src/components/OrdersTable.tsx` | 1977 | |
| `src/App.tsx` | 1872 | ~40 useState, kiêm auth + routing + data + layout |

**Tác động:** không thể unit test, conflict merge liên miên, onboarding dev mới rất chậm. `useOrdersData` ôm 35 state rồi trả 1 object lớn drill thẳng vào `OrdersPage` → coupling chặt.
**Khuyến nghị:** tách theo domain — `useOrdersFilters`, `useOrdersQuery`, `useOrdersMutations`; tách `OrdersPage` thành container + presentational; rút logic ra custom hooks/sub-components.

### 🔴 P1 — Không có ESLint/Prettier
Không tồn tại bất kỳ config nào (`.eslintrc`, `eslint.config.*`, `.prettierrc`). Với 36k LOC và team đang mở rộng, không có hàng rào chất lượng tự động là rủi ro hệ thống. Không CI lint = style/bug lọt tự do.
**Khuyến nghị:** thêm ESLint (typescript-eslint, react-hooks, jsx-a11y) + Prettier, chạy trong pre-commit (husky/lint-staged) và CI.

### 🔴 P1 — Routing tự chế bằng `window.location`
Không dùng react-router. `App.tsx` parse `window.location.pathname` + `useState` + `history.pushState` (22 chỗ tham chiếu `window.location`/`history`). Fragile, không có nested routes, không guard tập trung, dễ vỡ khi thêm trang.
**Khuyến nghị:** chuyển sang `react-router-dom` (hoặc nếu cân nhắc lại stack thì Next.js App Router).

### 🟡 P2 — Hai tầng HTTP, một cái là dead code
`src/lib/fetch-json.ts` (fetch trần, **không auth, không refresh token**) có **0 import** — code chết, nguy hiểm nếu ai đó vô tình dùng. Tầng thật là `src/services/http.ts` (xử lý refresh token tốt).
**Khuyến nghị:** xóa `lib/fetch-json.ts`.

### 🟡 P2 — Logic fetch lặp rải rác, thiếu API layer
Chỉ có `customers`, `products`, `facebook_ads` có module API riêng. Còn lại fetch inline trong component ở **11 file** (orders, sync, audit, warranty, order-detail…).
**Khuyến nghị:** chuẩn hóa mỗi resource một module trong `lib/api/`.

### 🟢 P3
- **39** chỗ `: any`/`as any`; `useOrdersData(fetchJson)` typed `Promise<any>` → mất lợi ích TS dù `strict: true`.
- **BOM** (`ef bb bf`) đầu `tsconfig.json` → hazard tooling (đúng pattern lỗi BOM đã biết trong repo này).
- **67** `console.*` còn sót trong code production.
- `src/i18n/messages.ts` 1322 dòng một file — nên tách theo feature.

---

## B. Hiệu năng

### 🔴 P1 — Không có tầng data-fetching/caching (đòn bẩy lớn nhất)
Không dùng react-query/SWR/TanStack. Pattern phổ biến: `fetch` trong `useEffect` + nhồi vào `useState` (**442 useState, 135 useEffect, 18 fetch**).
**Hệ quả:** không cache → refetch mỗi lần mount, không dedup request, dễ race condition, waterfall, làm nặng backend. Vừa là vấn đề perf vừa là correctness.
**Khuyến nghị:** đưa **TanStack Query** vào — giải quyết cache, dedupe, stale-while-revalidate, retry, loading/error state một lượt; đồng thời cắt phần lớn `useState/useEffect` thủ công.

### 🟡 P2 — Re-render rộng
State khổng lồ tập trung ở `App.tsx` + `useOrdersData` → đổi 1 field re-render cả cây lớn. Chỉ **6 `React.memo`** trên 109 file. 152 `useMemo`/150 `useCallback` nhưng khi component cha quá lớn re-render thì memo ở con hiệu quả thấp.
**Khuyến nghị:** tách state theo phạm vi, memo hóa các bảng/row, cân nhắc context tách nhỏ hoặc state lib nhẹ (zustand) cho global state thay vì dồn vào `App.tsx`.

### 🟡 P2 — Polling cần kiểm soát
18 `setInterval/setTimeout`. Kết hợp với việc không có caching, polling có thể dồn tải backend. Cần verify đều được cleanup và dừng khi tab ẩn.

### 🟡 P2 — Bundle vendor nặng
Cùng lúc: ag-grid-community, recharts, react-select, **và cả** `@base-ui` lẫn `shadcn` (chỉ 1 file dùng `@base-ui` → khả năng thừa).
**Khuyến nghị:** chạy `npm run build:analyze` (đã cấu hình visualizer sẵn) để xác nhận; cân nhắc bỏ `@base-ui` nếu chỉ 1 chỗ dùng.

### 🟢 P3
- 261 arrow function inline trong `OrdersPage` — vô hiệu hóa memo ở component con.

---

## ✅ Điểm tốt cần giữ
- **Code splitting đầy đủ:** tất cả page dùng `React.lazy` + Suspense.
- **Vite `manualChunks`** tách vendor (react/grid/ui) và tách riêng `orders-grid-desktop`, `orders-grid-columns`.
- **Bundle visualizer** đã cấu hình sẵn (`build:analyze`).
- **Token refresh** trong `http.ts` dedupe đúng bằng `refreshInFlight` (single-flight) — xử lý chuẩn.
- ag-grid cell renderer đã `memo` hóa.

---

## Thứ tự ưu tiên đề xuất
1. **P1** Thêm ESLint/Prettier + pre-commit + CI — chặn nợ kỹ thuật mới ngay.
2. **P1** Đưa TanStack Query vào — gỡ fetch-in-effect, được cache + bớt state.
3. **P1** Thay routing tự chế bằng react-router.
4. **P1** Tách nhỏ 5 file >1800 dòng (ưu tiên `useOrdersData` + `OrdersPage`).
5. **P2** Xóa `lib/fetch-json.ts`, gom API layer, sửa BOM `tsconfig`, dọn `console.*`.
