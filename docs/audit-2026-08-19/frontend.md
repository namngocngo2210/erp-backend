# Audit chi tiết — Frontend (2026-08-19)

> Báo cáo gốc của luồng audit frontend. Bản tổng hợp: `../audit-2026-08-19-full-stack.md`.
> React 19 + Vite 7 + TypeScript 5.6 + TanStack Query 5 + React Router 7. 616 file
> `.ts/.tsx` (không tính test), 29 features, 21 page ở `src/pages`.
> Nhận xét chung: 2 vấn đề nêu sẵn trong đề bài (vitest thiếu React Compiler; WS fanout)
> **đều đã được sửa** có test hồi quy. Phần lớn phát hiện là nợ cấu trúc, không phải lỗi runtime.

## 1. Cấu trúc & ranh giới module

### 1.1 `src/pages` và `src/features/*/pages` cùng tồn tại — quy ước kép — High
17/29 feature **không có** `pages/` riêng (audit, blocklist, callcenter, channels,
customers, dashboard, exports, kiotviet, lead-assignment, orders, products, roles,
sale-report, users, warehouses, warranty, warranty-claims); 12 feature còn lại
self-contained. `src/app/routed-pages.tsx:9-56` trộn hai nguồn. Hệ quả: page cũ không có
"nhà" để tách hook/component → phình 1000+ dòng (xem mục 2).

### 1.2 `src/components/orders` — component đặc thù feature trong thư mục chung — Medium
5 file Sheet3*/AiFilledBadge chỉ được orders dùng (4 import đều thuộc domain orders) →
nên về `features/orders/`.

### 1.3 Ba lớp API chồng nhau — High
- `services/http.ts` — transport thật (token in-memory, refresh cookie, retry 401). ✅
- `api/typed.ts` — typed client sinh từ OpenAPI. ✅ đúng hướng.
- `lib/api/*` — **36 module viết tay** gọi thẳng fetchJson, không qua typed client
  (customers.ts 679 dòng). → xem 6.2.

### 1.4 Import chéo feature — Medium
Bản đồ: contacts→callcenter(7)/orders(5)/exports(4)/customers(4)/channels(4);
inventory→orders(10)/exports(9); warranty-codes→products(6)/inventory(3); ads→dashboard(7);
users⇄hr (chu trình 2 chiều); v.v.
**Đáng lo (b):** `inventory/pages/ShipmentsPage.tsx:5-12` import nguyên
`OrdersTable`/`OrdersPagination`/`OrderRow`/`AddressCatalog` từ orders — sửa bảng orders
có nguy cơ vỡ trang inventory; `warranty-codes` import `Pager` từ
`inventory/components/flow-shared` (component phân trang generic nằm trong feature).
**Đề xuất:** thăng cấp component bị 2+ feature dùng lên `components/`.

### 1.5 Trùng lặp giữa features
- **(a) Bảng — Medium**: `data-table-shell` dùng ở 18 file, còn **47 file** tự dựng
  `<table>` thô; 5 bảng trong `features/ads/components/` gần cùng shape → gộp
  `MetricTable` config cột.
- **(b) Phân trang — Medium**: wrapper "Tổng N · Mỗi trang · pages" chép ≥4 lần
  (OrdersPagination:24-77, CustomersPagination:20-40, flow-shared Pager,
  ContactsPage:469…). `PAGE_SIZE_OPTIONS` khai 3 lần **giá trị khác nhau**:
  [10,20,50,100] (contacts, ads) vs [20,50,100,200] (inventory).
- **(c) Filter panel — Medium**: OrdersFilterPanel (677), ContactsFilterBar,
  ShipmentsFilterBar — 3 bộ tự viết, đã có mầm chia sẻ nhưng chưa tách primitive.
- **(d) Export — điểm sáng**: `features/exports` là hạ tầng job dùng chung tốt (6+
  feature). Chỉ 2 chỗ HR tự tải file ngoài luồng (AttendanceHistoryPage:74-77,
  CompensationPage:157-160) — Low.

## 2. TOP 15 file dài nhất (không tính test, không tính schema.d.ts 18.036 dòng)

| # | File | Dòng | Mức | Hướng tách |
|---|---|---|---|---|
| 1 | `pages/ProductsPage.tsx` | 1525 | Critical | 19 useState. `PackagesEditor` (1324-1508) tách ra trước; `useProductsPage()`; helper `:86,:90` + hằng `:72-74` sang constants. Đích < 200 dòng. |
| 2 | `features/orders/OrderFormDialog.tsx` | 1407 | Critical | Đã có `order-form/` — đẩy tiếp từng section; `useOrderForm()` cho state+validate+payload. |
| 3 | `pages/ResaleTrackingPage.tsx` | 1280 | Critical | 18 useState. Dòng 52-162 = 11 helper thuần + 6 hằng → `resale-format.ts` (−190 dòng, unit-test được); `ContactHistory` (166-242) thành component. Nhiều formatter trùng `lib/format.ts`. |
| 4 | `App.tsx` | 1133 | Critical | 23 useState, root shell kiêm data-layer. Tách `useAppOrders/Channels/Users/Summary`; khối `dataHandlersRef` (887-911) + `stableDataHandlers` (913-960) → `useStableHandlers()` generic. |
| 5 | `features/orders/use-orders-table.ts` | 1112 | Critical | 13 useState. Tách theo trục: cột/hiển thị, selection, inline-edit, export. |
| 6 | `pages/OrdersPage.tsx` | 1086 | High | Props type 60-271 = **211 dòng khai type** → prop-drilling nặng; chuyển sang AppShellContext hoặc gom object. |
| 7 | `pages/RolesPage.tsx` | 1075 | High | 8 bảng label/hằng (58-135) → constants.ts; `groupState/toggleGroup` → permission-logic.ts (xóa được 2 eslint-disable). |
| 8 | `pages/OrderDetailPage.tsx` | 884 | High | Block* memo tốt; tách `useOrderDetail()`. |
| 9 | `pages/LoyalCustomersPage.tsx` | 868 | High | Container/presentational. |
| 10 | `features/orders/BlockCrmRating.tsx` | 853 | High | Đã memo. Tách form CRM + catalog. |
| 11 | `features/orders/OrdersTable/OrderCell.tsx` | 827 | High | Switch khổng lồ theo cột → map `columnKey → renderer` (gộp với cell-renderers.tsx 607). |
| 12 | `features/orders/use-orders-data.ts` | 805 | High | Tiếp tục tách load/realtime-merge. |
| 13-14 | `pages/FacebookAdsPage.tsx` 794 + `pages/GoogleAdsPage.tsx` 780 | | High | **Song sinh** → `useAdsPlatformReport(platform)` + 1 page chung. |
| 15 | `OrdersTable/OrdersTable.tsx` | 764 | High | Đã memo. Xem 5.1 virtualization. |

Kế tiếp (761-600): order-detail-shared 756, lib/api/customers.ts 679, OrdersFilterPanel
677, order-types 674, ProductCategoriesPage 659, ChannelManagement 631, use-customers-data
614, Sidebar 608, cell-renderers 607, use-crm-websocket 605, ScanDispatchDialog 605.

**Mẫu chung:** 6/15 file dài nhất nằm ở `src/pages/` — chính xác các feature không có
`features/<x>/pages/` (1.1).

## 3. State / Data fetching

### 3.1 Hai thế hệ song song — High
TanStack Query v5 (50 file, config tập trung `lib/query-client.ts` hợp lý) vs thế hệ
thủ công useState+useEffect+fetchJson+ref cache TTL (App.tsx, use-orders-data,
use-orders-table, use-orders-catalogs, use-orders-bulk-actions, useOrdersTableData,
use-shipments, use-auth-bootstrap, use-kship-note-presets).
`App.tsx:55` `FRONTEND_CACHE_TTL_MS = 30_000` chép tay đúng bằng staleTime react-query.
Realtime tự mô phỏng invalidation qua 6 handler (use-crm-websocket.ts:78-88).
Không Redux/Zustand — global state là Context; `AppShellContext` khai **24 handler**
(context quá to, đã giảm nhẹ bằng ref-wrapper = chữa triệu chứng).

### 3.2 API client — chuẩn hoá một nửa — Medium
ESLint chặn `fetch` thô — 6 vi phạm đều disable kèm lý do chính đáng (blob, Nominatim,
portal). Chất lượng cao. Điểm trừ: 13 file .tsx gọi fetchJson trực tiếp trong component
(ContactsPage.tsx:131 hardcode URL + as cast + limit=1000).

### 3.3 Xử lý lỗi/loading không nhất quán — Medium
Tên biến loading 5 kiểu (pending 264, loading 237, isLoading 196, isPending 190,
isFetching 24). **144 chỗ nuốt lỗi im lặng** (catch {}) dù Sentry đã cài → đáng
captureException. 300 toast.error rải rác, chưa có handleApiError() tập trung
(lib/errors.ts đã tồn tại). ErrorBoundary 3 tầng đặt đúng chỗ — tốt.

## 4. React Compiler

### 4.1 Vitest ĐÃ bật compiler — Resolved
`vitest.config.ts:8` khớp `vite.config.ts:26-30` (target 19). Commit sửa `6d94aa74`
(2026-08-06) + test hồi quy `useOrdersTableData.export-gate.test.tsx` + post-mortem
`docs/order-export-by-filter.md`. Chất lượng xử lý sự cố rất tốt.

### 4.2 Còn sót: GHI ref trong thân render — 5 chỗ — Medium
Làm component bail out khỏi toàn bộ memoization của Compiler:
1. `App.tsx:887` — `dataHandlersRef.current = {…24 handler…}` (component lớn nhất app)
2. `features/callcenter/WebphoneProvider.tsx:174`
3. `features/hr/components/LocationMapPicker.tsx:40`
4. `features/inventory/components/flow-shared.tsx:202`
5. `features/inventory/scan/CameraScanner.tsx:42`
Cách đúng: useEffect như `use-crm-websocket.ts:143-157` (cùng ý đồ, viết chuẩn).
*(→ ĐANG LÀM: nhánh fix/react-compiler-ref-render-writes.)*

### 4.3 Đọc ref trong render — 2 chỗ lazy-init hợp lệ — Low
`use-orders-data.ts:134-137`, `App.tsx:942`. Được phép (idempotent), nhưng dòng 137
đáng thêm comment cảnh báo — nếu ref thành mutable sẽ tái hiện đúng bug 06/08.

### 4.4 3 chỗ disable react-compiler có chủ đích — Low, chấp nhận được
(warranty-claims JourneyCard/journey-form/useBestOrderForm — deps thu hẹp giữ draft,
comment rõ.)

## 5. Performance

### 5.1 KHÔNG có virtualization — High
0 kết quả grep; page size tối đa 200 × ~30 cột × OrderCell 827 dòng logic/ô. Giảm nhẹ:
pagination server-side + memo OrderTableRow. Đo trước; cần biết là không có lưới an toàn
khi nâng page size.

### 5.2 Lazy route — Tốt. 30/30 route lazy; Suspense 3 tầng; PortalScanButton lazy
component-level.

### 5.3 Thư viện nặng — không lodash/moment/xlsx (Excel sinh server-side). recharts
named-import trong route lazy ✅. **leaflet `import L` default trong 2 dialog HR, không
lazy — Medium**; `@xyflow/react` trong FlowDiagramTab nên lazy theo tab — Low.

### 5.4 manualChunks thô — Low. Chỉ vendor-react/vendor-ui; chunkSizeWarningLimit nâng
600; có `npm run build:analyze` chưa chạy.

### 5.5 memo — 16 component (13 ở orders), dùng đúng. Lưu ý: với Compiler bật, phần lớn
memo thủ công là thừa — sau khi sửa 4.2 có thể gỡ dần, phải đo.

## 6. TypeScript

### 6.1 Type từ OpenAPI — có generate + CI drift-check — Tốt
`gen:api` → schema.d.ts 18.036 dòng; api/typed.ts suy type từ `paths` — thiết kế đẹp.

### 6.2 …nhưng typed client gần như không được dùng — High
36 module lib/api/* gọi thẳng fetchJson tự khai type tay. **Hợp đồng OpenAPI có, cơ chế
sinh có, CI gác drift có — nhưng lợi ích "đổi field BE → tsc đỏ FE" không đến được 90%
codebase.** Phát hiện giá trị cao nhất mục TypeScript.

### 6.3 strict: true, 0 @ts-ignore — Tốt. Gợi ý siết dần: noUncheckedIndexedAccess,
noImplicitOverride, exactOptionalPropertyTypes, noUnusedLocals/Parameters,
verbatimModuleSyntax; vite.config ngoài include nên không được typecheck.

### 6.4 `any` — chỉ 12 lần, đều có disable + lý do — Low
Đáng chú ý: `type FetchJson = (...) => Promise<any>` **khai lại 5 lần** (orders ×4 +
inventory use-shipments) — điểm rò rỉ type lớn nhất, chính là chỗ thay bằng apiGet/apiPost.

### 6.5 Lint gate lỏng — Medium
Script `lint` mặc định `--max-warnings=9999`; `lint:strict` (=0) phải gọi riêng — CI
phải gọi lint:strict (file CI khôi phục đã đúng như vậy).

## 7. Realtime / WebSocket — sự cố fanout ĐÃ xử lý rất kỹ — Resolved

`use-crm-websocket.ts` (605 dòng), 10 lớp phòng thủ cho `order.created`:
(1) section gate ORDER_LIST_SECTIONS; (2) channel scope; (3) jitter 500ms+random 1500ms;
(4) coalescing Set (1 id → GET detail, ≥2 → MỘT loadOrders); (5) cooldown sàn 10s/tab
(comment ghi số đo bão 2026-08-04: sóng 44 rồi 40 POST/10s); (6) visible-only + flush
khi visible lại; (7) backoff 5s→60s khi 503; (8) **zero-REST broadcast** — payload WS
tự mang `row` server đã strip theo field_policy, validate `row.id === orderId`, fallback
đường coalesce; (9) dashboard summary cùng khuôn; (10) cleanup đủ 3 timer + listeners.
`transfer_landing_orders` cũng đã zero-REST (comment đo 2026-08-11).
Transport `services/websocket.ts`: singleton, reconnect backoff mũ jitter 250ms→30s,
3 file test.

**Rủi ro còn lại — Low:** file trộn 3 concern (connection / scheduling / parse payload
`order.updated` :441-580) → tách `parseOrderUpdatedPayload()` pure + scheduler hook;
`disconnectCRM()` trong cleanup cần refcount nếu có consumer thứ 2.

## Tổng hợp ưu tiên

**Critical (tách file, không đổi hành vi):** ProductsPage (PackagesEditor trước),
OrderFormDialog, ResaleTrackingPage (helper thuần :52-162), App.tsx + use-orders-table.
**High:** typed client bỏ không (6.2); không virtualization (5.1); quy ước kép pages
(1.1); 2 thế hệ data-fetching (3.1); inventory import ruột orders (1.4b).
**Medium:** 5 chỗ ghi ref trong render (4.2 — đang fix); 144 catch nuốt lỗi (3.3);
components/orders sai chỗ (1.2); pagination ×4 + PAGE_SIZE khác giá trị (1.5b);
leaflet không lazy (5.3); lint 9999 (6.5).

**Đã tốt, giữ:** compiler đồng bộ vitest↔vite + test hồi quy; WS hardening 10 lớp;
30/30 lazy route; 0 @ts-ignore/strict/12 any có lý do; ESLint chặn fetch thô; exports
infra mẫu mực; comment tiếng Việt gắn ngày + số đo prod — dạng tài liệu hoá hiếm, rất đáng giữ.
