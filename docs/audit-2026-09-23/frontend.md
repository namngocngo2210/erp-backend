# Audit Frontend — 2026-09-23

Chạy thật trong container node:22-alpine trên bản copy: `npm ci` OK; `typecheck` 0 lỗi; `lint` 0 error/0 warning; `vite build` OK, 136 chunk trùng hash byte-for-byte với image prod `desi-order-frontend:latest`; `vitest run` 1791/1795 pass (4 đỏ dưới tải: 1 do môi trường copy thiếu backend, 3 timeout 5s do tranh CPU — chạy cô lập 17/17 pass, 20s), 0 skip/todo, 710s wall, 120 test >1s.

## Bundle (image prod)
| Chunk | Raw | Gzip | Ghi chú |
|---|---|---|---|
| entry `index-*.js` | 600 KB | 173 KB | chứa data-layer orders (use-orders-data 833, use-crm-websocket 605), i18n vi+en |
| vendor-react | 193 KB | 60 KB | |
| vendor-ui | 74 KB | 22 KB | |
| css | 136 KB | 22 KB | |
| Tải lần đầu | ~1,0 MB | ~277 KB | |
| `dashboard-constants-*.js` | 371 KB | 107 KB | = recharts + hằng dashboard, lazy |
| `index-ByvYMspt.js` | 449 KB | 115 KB | = @zxing, chỉ khi mở camera |
| Tổng JS | 4,29 MB | 1,25 MB | 136 chunk; 0 `.map` |

## Đối chiếu audit 08-19
| Mục | Trạng thái |
|---|---|
| OrderFormDialog 1407 / App.tsx 1133 / use-orders-table 1112 / OrderCell 827 | ĐÓNG (321 / 453 / 122 / 65) |
| ProductsPage 1525 | Nửa (1294, vẫn 1 hàm component) |
| ResaleTrackingPage 1280 | Nửa (1120) |
| OrdersPage 1086 | Mở (1051, Props 220 dòng :62-281) |
| RolesPage/OrderDetailPage/LoyalCustomersPage/BlockCrmRating | Mở 983/982(tăng)/868/853 |
| Fb/Google Ads song sinh | Nửa (hook chung `useAdsPlatformReport.ts`, page vẫn ~65% giống) |
| inventory import ruột orders | Mở (`ShipmentsPage.tsx:5-11`) |
| PAGE_SIZE_OPTIONS ×3 | Tệ hơn: 8 khai báo / 6 bộ giá trị |
| hai thế hệ data-fetching | Mở: 57 file useQuery vs 6 hook thủ công ~2.700 dòng |
| ref-write-in-render ×5 | ĐÓNG |
| virtualization | Mở (0) |
| leaflet/xyflow/zxing lazy | ĐÓNG |
| typed client bỏ không | Nửa: 46 call typed vs 229 `fetchJson` trong lib/api (+101 ngoài); 4 bản `Promise<any>` |
| `any` | 9 ngoài test; 0 ts-ignore; 33 eslint-disable có rule |
| lint max-warnings 9999 | ĐÓNG (=0, CI gate) |

## Phát hiện mới
### P1
- **P1-1. Service worker PWA precache toàn app (146 entry, 4,36 MB) cho MỌI user desktop mỗi deploy.** `vite.config.ts:31-64` VitePWA scope `/m` nhưng `registerSW.js` inject mọi route; `sw.js` precache cả OrdersPage/FlowDiagramTab/leaflet/zxing. Sửa: `injectRegister: null`, gọi `registerSW()` chỉ trong `features/ess/MobileApp.tsx`; thu hẹp `workbox.globPatterns`.
- **P1-2. Typed OpenAPI client mới phủ ~17% call** (46/275 trong lib/api; 14 module 0 typed: media.ts, call-quality.ts, pancake.ts, customer-profile.ts…; 7 `.tsx` gọi `fetchJson` thẳng). Sửa: `no-restricted-imports` cấm `@/services/http` trong `src/lib/api/**` với allowlist tạm; migrate `orders.ts` trước.
- **P1-3. ~1.900 dòng code chết**: `features/orders/OrderDetailDialog.tsx` 485 (0 import); `features/hr/components/DepartmentTeamsManager.tsx` 222 (bản sao của users/, có test 93 dòng đang test code chết); `app/AppLauncher.tsx` 135; `i18n/add_en_translations.mjs/.py` 358+355 (script one-off lỗi thời); `check_keys.cjs` 27 (luôn in "Missing 0 keys"); `components/Can.tsx`, `hooks/useFieldPolicy.ts`, `hooks/use-polling.ts`, `ui/managed-dialog-content.tsx`; ~33 export lib/api/queries không ai gọi; `public/logo.pdf` 530 KB. Sửa: 1 MR xoá + `knip` trong CI.
- **P1-4. God component `src/pages` chưa đổi**: 39 file >500 dòng, 66 tsx >300; 21 page ở `src/pages` song song 35 page trong `features/*/pages`.

### P2
- P2-1. Không virtualization; page size tới 200; `order.created` setState mảng mới → 200 row so props (đã memo row). Đo Profiler trước.
- P2-2. Trùng helper: formatCurrency ×9 + 95 `toLocaleString("vi")` inline; formatDate ×27; normalizePhone ×3; PAGE_SIZE ×8; pagination component ×4; `<table>` thô 48 file vs `data-table-shell` 19. Sửa: format sweep về `lib/format.ts` + lint cấm.
- P2-3. `manualChunks` (`vite.config.ts:107-119`) chỉ tách react + lucide; recharts trộn vào chunk `dashboard-constants` → sửa 1 hằng làm mất cache 371 KB. Sửa: vendor-charts/select/zxing/flow/map/core.
- P2-4. Entry 600 KB chứa data-layer orders (AppShellContext 122 field cấp `orders` cho mọi route) + i18n 2 ngôn ngữ tĩnh. Sửa: lazy `en`; orders sang query cache.
- P2-5. `refetchOnWindowFocus: "always"` trên report nặng (`ads/queries.ts:94,297,412`, `overview-report/queries.ts:28`, `sale-report/queries.ts:61,118`) → alt-tab gọi lại aggregate. Sửa: `true` + staleTime 60s.
- P2-6. Gate quyền bằng `is_superuser` thay permission code ở 12 chỗ (`app/routed-pages.tsx:204,398,475,…`, `RootLayout.tsx:73`, `route-guards.tsx:91`, `use-auth-bootstrap.ts:77` hard-code DEPARTMENT_ID.ADS).
- P2-7. inventory→orders cross-feature (`ShipmentsPage.tsx:5-11`).
- P2-8. 34 `catch {}` rỗng + 336 `toast.error` rải; `swallowError` mới 11 chỗ.

### P3
Test cho code chết (hr DepartmentTeamsManager); `key={index}` 19 chỗ; favicon.png 110 KB khai 32x32, delax.png 189 KB hiển thị h-10; index.html có BOM; tsconfig thiếu noUnusedLocals/noUncheckedIndexedAccess; `@zxing/library@0.22` đòi Node ≥24 vs engines <24; 3 provider value không memo (ui); cấu trúc feature lệch (orders 57 file phẳng, có cả `orders-table/` lẫn `OrdersTable/`); 95 chuỗi "Đang tải…" ngoài i18n; test env 598s (jsdom 243 lần) — cân nhắc `isolate: false` cho test thuần, testTimeout 10s cho 3 file sát ngưỡng.

## Test (243 file, 1.795 it, 32.283 dòng)
0 skip/todo/only, 0 snapshot. Nhiều file test cùng đối tượng (OrderFormDialog ×5, OrderDetailPage ×6, use-orders-data ×4) tách theo kịch bản, không trùng. Mock nặng: `contacts-stage-change`, `contacts-row-selection` (9 vi.mock/file), `contacts-export` (8) — kiểm wiring, giá trị thấp, nên gộp. **Ước tính cắt ~10–15 it** (3 it code chết + gộp 3 file contacts). 3 test sát ngưỡng timeout 5s (1,1–1,8s cô lập) → flaky trên runner yếu.

## Tóm tắt
LOC nguồn 114.461 (751 file, schema.d.ts 20.855 riêng); test 32.283; file >500: 39; bundle first-load 277 KB gz; SW precache 4,36 MB; `any` 9; typed 46/275; code chết ~1.900 dòng; test thừa ~10–15 it.

## Top 5
1. Giới hạn SW về `/m` (P1-1).
2. Xoá code chết 1 MR + knip CI (P1-3).
3. Ép typed client trong lib/api, migrate orders.ts (P1-2).
4. manualChunks vendor nặng + lazy `en` (P2-3/P2-4).
5. Format sweep + gom PAGE_SIZE + tách ProductsPage/OrdersPage props (P2-2/P1-4).

Đã tốt: 52 route lazy + 64 lazy component; sourcemap hidden + Sentry + xoá .map 3 lớp; nginx immutable/no-store đúng; CI gate lint/format/typecheck/vitest/drift-check; query key factory tập trung (0 inline); WS hardening; console strip prod.
