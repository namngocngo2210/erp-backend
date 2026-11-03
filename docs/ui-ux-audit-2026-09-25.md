# UI/UX & Responsive audit — 2026-09-25

Branch: `fix/ui-ux-audit-2026-09-25` (worktree `.claude/worktrees/ui-ux-audit`, từ master `de490161`).
Kế thừa: `docs/audit-2026-09-23/frontend.md` (code quality), `docs/ui-theme-refresh-2026-07-06.md` (token/font).
Công cụ đánh giá: skill ui-ux-pro-max (119 UX guideline) + quét tĩnh toàn bộ `frontend/src` + chụp headless trang login ở 375/768/1024/1280/1920
(trang trong chưa chụp được — không dùng tài khoản prod, xem Sprint 0).

Stack: Vite + React 19 SPA, shadcn (`base-nova`), Tailwind v4, lucide, React Query, i18n vi/en. Light theme xanh da trời.

---

## 1. Đánh giá hiện trạng — 7/10

### Điểm mạnh (giữ nguyên)
- Token oklch tông xanh; 2.926 chỗ dùng token semantic vs 531 màu palette thô.
- `:focus-visible` toàn cục, `prefers-reduced-motion`, 252 `aria-label`, 230 `htmlFor`, 90 file test dùng `getByRole`.
- 53 route lazy; bảng `tabular-nums` + zebra mặc định; shell (Sidebar/TopNav/PageHeader/Dialog size) đã responsive.

### Phát hiện

| ID | Mức | Vấn đề | Bằng chứng |
|---|---|---|---|
| F1 | Cao | Chữ quá nhỏ: root co 14px ở <1280 → `text-xs` = 10,5px (704 chỗ); 71 `text-[9-11px]` | `styles.css:317-327` |
| F2 | Cao | 25 `window.confirm()` cho hành động phá huỷ; guard "chưa lưu" lặp 3 lần | `flow-shared.tsx`, `OrderDetailPage.tsx:706-724` |
| F3 | TB-Cao | Touch target: 62 nút `icon-sm/xs` 24–28px, `icon` 32px, select ô bảng `h-7` | `button.tsx:24-26`, `crm-cells.tsx:39` |
| F4 | TB | 5 hệ select (shadcn 51, Searchable 19, Multi 18, react-select 10, native 42); 46 bảng thô vs 19 `DataTableShell`; `PageHeader` 26/57 trang | |
| F5 | TB | Emoji làm icon (🏭📤🛡️⚙️🛒…) | `warranty-claims/components/journey/*`, `BestOrderFormBody.tsx:230` |
| F6 | TB | Màu palette thô cho trạng thái thay vì token `action-*` | `logistics-cells.tsx` 40, `LeadAssignmentPage` 22, `LoyalCustomersPage` 19 |
| F7 | TB | Loading = spinner (83 file), Skeleton 3 file → CLS | |
| F8 | Thấp | Dark mode dở dang: `.dark` + 49 `dark:` nhưng không có toggle | |
| F9 | Thấp | God page: Resale 1120, Roles 983, OrderDetail 983, Orders 870, LoyalCustomers 866 | |
| F10 | Thấp | 359 arbitrary `[..px]`, 69 `style={{}}` | |
| F11 | Thấp | Login chỉ có placeholder, không label | `app/login-view.tsx` |

### Responsive — nguyên nhân gốc

| ID | Vấn đề |
|---|---|
| R1 | `html, body { overflow-x: hidden }` → phần tràn bị **cắt** thay vì cuộn; bug bị che khỏi DevTools |
| R2 | Root font-size co (rem) nhưng 359 kích thước px không co → tỉ lệ lệch giữa các tier |
| R3 | 4 hệ breakpoint lệch (Tailwind; DESKTOP 768; COMPACT 1536; font 1280/1536). `sm:` 257 vs `md:` 50 / `lg:` 68 → vùng 640–1024 (tablet, cửa sổ nửa màn) nhận layout desktop với bảng `min-w-[760-1600px]` |
| R4 | ~40 hàng toolbar `flex justify-between` không `flex-wrap` |
| R5 | Không có test viewport/visual |

Điểm vỡ cụ thể: `PackagesEditor.tsx:105` (grid-cols-12 cứng), `WebphonePanel.tsx:25`/`WebphoneDock.tsx:68` (w-340 cứng),
`ContactHistoryPopover.tsx:106` (w-320), `RecallEntriesCell.tsx:100` (min-w-430 trong ô bảng), `LeadAssignmentPage.tsx:342,474` + `ProvinceRevenueCard.tsx:118` (bảng không wrapper cuộn),
`order-detail-shared.tsx:145` (grid-cols-12 + react-select), ~25 bảng rộng không sticky cột định danh.

---

## 2. Thiết kế giải pháp

### 2.1 Typography & density
- Bỏ tier 14px (<1280). Giữ 1 tier 15px cho <1536 (mật độ laptop — quyết định cũ có chủ đích).
- Sàn chữ nhỏ: `@theme { --text-xs: max(12px, 0.75rem) }` → `text-xs` ≥ 12px ở mọi tier.
- `text-[9-11px]` → `text-xs`; ngoại lệ duy nhất: badge đếm số (dùng token `text-2xs` = 11px, khai trong `@theme`).

### 2.2 Breakpoint — một nguồn sự thật
`lib/constants/layout.ts` định nghĩa theo thang Tailwind:
| Hằng | Giá trị | Dùng cho |
|---|---|---|
| `MOBILE_MAX` (`md`) | <768 | camera quét kho, bottom-nav ESS |
| `TABLE_MIN` (`lg`) | ≥1024 | `ResponsiveList` bảng ↔ card (trước: 768) |
| `COMPACT_MAX` (`2xl`) | <1536 | auto-thu gọn sidebar + root 15px |
Quy ước: layout nhiều cột dùng `md:`/`lg:`; `sm:` chỉ cho tinh chỉnh nhỏ.

### 2.3 Overflow
- Bỏ `overflow-x: hidden` trên `html, body`; khung app vốn đã `overflow-hidden` (`RootLayout.tsx:176`). Trang login/portal có blob trang trí → `overflow-hidden` trên container của chúng.
- Dev-only `lib/dev/overflow-detector.ts` (chỉ `import.meta.env.DEV`): log `console.warn` phần tử vượt viewport ngoài vùng cuộn hợp lệ.
- Bảng rộng: luôn bọc `overflow-x-auto`; cột định danh đầu `sticky left-0 bg-card z-[1]` cho Orders, Contacts, LoyalCustomers, Resale, Compensation.
- Width cứng → `w-[Npx] max-w-[calc(100vw-2rem)]`; grid 12 cột → `grid-cols-1 sm:grid-cols-12` (hoặc tương đương); toolbar `flex-wrap`.

### 2.4 Touch target (không đổi mật độ desktop)
Tailwind v4 variant `pointer-coarse:` — chỉ tăng trên thiết bị cảm ứng:
`icon` 32→44, `icon-sm`/`icon-xs` → 40, `default`/`sm` h-9→h-11, `Input` h-9→h-11, select ô bảng h-7→h-8 (+ coarse h-10).

### 2.5 Xác nhận thao tác phá huỷ
- `components/ui/confirm-dialog.tsx` (AlertDialog-style trên Dialog size `confirm`) + `ConfirmProvider` + `useConfirm()` trả `Promise<boolean>`; option `{ title, description, confirmText, destructive }`, nút phá huỷ variant `destructive`, focus mặc định nút Huỷ.
- `hooks/use-unsaved-guard.ts` cho form "có thay đổi chưa lưu".
- ESLint `no-restricted-globals`/`no-restricted-syntax` cấm `confirm`/`window.confirm`/`alert`.

### 2.6 Màu trạng thái
- Thêm token `--action-info` (sky). Component `components/ui/status-badge.tsx` với `tone: success|warning|danger|info|neutral` (dùng `bg-action-*/10 text-action-* border-action-*/30`).
- Thay palette thô mang nghĩa trạng thái bằng token/`StatusBadge`. **Giữ** accent module (sidebar/app launcher — `modules.ts`) và trang public warranty-portal (brand riêng), màu chart.

### 2.7 Icon
Emoji → lucide (`Warehouse`, `Send`, `ShieldCheck`, `CircleCheck`, `Mailbox`, `User`, `Settings`, `Truck`, `Inbox`…), icon `aria-hidden`. Ký tự ✓/✗ trong text trạng thái → icon `Check`/`X`.

### 2.8 Select
- Chuẩn: shadcn `Select` (danh sách tĩnh), `SearchableSelect` (tìm kiếm, thêm prop `creatable`), `MultiSelect`.
- Gỡ `react-select` (10 file, gồm `CreatableSelect` ở form đơn) → cần test hồi quy form đơn.
- `<select>` native: giữ trong ô OrdersTable (hàng trăm row, perf) nhưng chuẩn hoá qua 1 component `NativeCellSelect`; ngoài bảng → shadcn `Select`.

### 2.9 Bảng & loading
- `DataTableShell` thêm prop `loading` render skeleton rows (giữ chiều cao) — dùng cho các trang danh sách.
- Mọi trang có `PageHeader`.

### 2.10 Dọn dẹp
- Xoá dark mode chết (`.dark` block, `@custom-variant dark`, 49 `dark:`) — quy định dự án là light theme.
- Login: label `sr-only` + `autoComplete`.
- Tách 5 god page theo section/hook.

### 2.11 Chặn tái phát
- `eslint-plugin-jsx-a11y` (bộ rule chọn lọc, 0 warning).
- `no-restricted-syntax` cấm className chứa `text-[9|10|11px]`.
- Script `frontend/scripts/ui-check/` (puppeteer-core + Chromium, API **mock** bằng request interception, chạy trên `vite preview`) chụp các trang chính ở 375/768/1024/1280/1920 và báo overflow; chạy qua docker, không cần tài khoản prod.

---

## 3. Kế hoạch sprint

| Sprint | Nội dung | Mục | Trạng thái |
|---|---|---|---|
| S0 | Công cụ `ui-check` (mock API + overflow report) — baseline trước khi sửa | R5, 2.11 | ✅ |
| S1 | Nền tảng: typography, breakpoint, overflow, touch target, ESLint guard | F1 F3 R1 R2 R3 | ✅ |
| S2 | Component: ConfirmDialog/useConfirm, unsaved guard, StatusBadge + token, emoji → lucide, login label | F2 F5 F6 F11 | ✅ |
| S3 | Responsive hotspot + toolbar wrap + sticky cột + wrapper bảng + PageHeader + skeleton | R4, hotspot, F4(bảng), F7 | 🟡 |
| S4 | Select hợp nhất (gỡ react-select), dọn dark mode, arbitrary px | F4(select), F8, F10 | ✅ |
| S5 | Tách god page | F9 | ✅ |
| S6 | Hồi quy: full typecheck/lint/knip/vitest/build + ui-check sau sửa, so baseline | — | ✅ |

Quy tắc thực thi: mỗi sprint 1 subagent, chạy tuần tự trên cùng worktree (tránh xung đột + không chạy 2 vitest song song trên host prod);
test trong `node:22-alpine --cpus=2 --memory=3g`. Mỗi sprint commit riêng và ghi "Đã làm / Chưa làm" vào mục 4.

---

## 4. Nhật ký thực hiện

_(cập nhật sau mỗi sprint)_

### Sprint 0 — công cụ `ui-check` + baseline (✅)

**Đã làm**
- `frontend/scripts/ui-check/`: `run.mjs` (static server `dist/` + SPA fallback, puppeteer-core, request interception),
  `fixtures.mjs` (superuser giả + dữ liệu dài), `Dockerfile` (node:22-alpine + chromium + puppeteer-core@23 ở `/app`,
  **không** thêm vào `package.json` frontend), `README.md`. Output `frontend/.ui-check/<label>/` (gitignore + dockerignore).
- Auth mock: localStorage `desi_cached_user` → `/api/users/token/refresh` trả JWT giả (exp 2100) → `/api/users/me` superuser
  với `allowed_sections` đủ mọi section + `salaries`. WebSocket thay bằng stub; mọi request ra ngoài origin bị chặn.
- Fixture có dữ liệu dài cho: orders (list POST + detail + shipments), customers/profiles, sale/contacts, customers/loyal(+segments),
  customers/resale, warranty/claims, products + products-grouped, inventory/stock, warehouses, hr/employees, hr/compensations,
  hr/departments, dashboard/summary, reports/overview, lead-assignment (staff/workload/shares/shift-windows), roles/permissions,
  channels(+staff/types/list-for-kiotviet), crm-catalogs, kiotviet-options, exports. **18/18 trang render, 0 ErrorBoundary.**
- Chỉ số mỗi trang × độ rộng: **tràn** (phần tử ngoài cùng vượt viewport, ngoài vùng cuộn ngang hợp lệ; bỏ blob trang trí
  `pointer-events-none`), **cắt** (container `overflow-x: hidden|clip` giấu nội dung, kèm phần tử gây ra; bỏ `truncate`/`line-clamp`),
  **cuộn** (vùng `overflow-x-auto` đang cuộn — thông tin, ứng viên sticky cột/card view).
- Lệch so với yêu cầu ban đầu: fallback endpoint list trả `[]` thay vì `{count:0,results:[]}` — đa số wrapper FE nhận mảng trần
  (`Array.isArray(data) ? data : []`), trả object làm 6/18 trang crash `.map is not a function`.

**Cách chạy** (xem `frontend/scripts/ui-check/README.md`)
```bash
cd frontend
docker build -t desi-ui-check scripts/ui-check
docker run --rm --cpus=2 --memory=3g -u $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/repo/frontend -w /repo/frontend node:22-alpine sh -c "npx vite build"
docker run --rm --cpus=2 --memory=3g -u $(id -u):$(id -g) -e HOME=/tmp -e LABEL=baseline -v $PWD:/repo/frontend desi-ui-check node /repo/frontend/scripts/ui-check/run.mjs
```
~7 phút cho 18 trang × 5 độ rộng.

**Baseline** (`tràn/cắt/cuộn`, master `de490161`)

| Trang | 375 | 768 | 1024 | 1280 | 1920 |
|---|---|---|---|---|---|
| /login | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| /apps → /sales/orders | 0/0/1 | 0/0/2 | 0/0/1 | 0/0/1 | 0/0/1 |
| /sales/orders | 0/0/1 | 0/0/2 | 0/0/1 | 0/0/1 | 0/0/1 |
| /sales/orders/900001 | 0/0/1 | 0/0/1 | 0/0/0 | 0/0/1 | 0/0/1 |
| /crm/customers | 0/0/0 | 0/0/1 | 0/0/1 | 0/0/1 | 0/0/1 |
| /sales/contacts | **1**/0/1 | 0/0/1 | 0/0/1 | 0/0/1 | 0/0/1 |
| /crm/loyal-customers | 0/0/1 | 0/0/1 | 0/0/1 | 0/0/1 | 0/0/1 |
| /sales/resale-tracking | 0/0/0 | 0/0/1 | 0/0/1 | 0/0/1 | 0/0/0 |
| /aftersales/warranty | **1/1**/0 | 0/0/1 | 0/0/1 | 0/0/1 | 0/0/0 |
| /catalog/products | 0/0/0 | 0/0/1 | 0/0/0 | 0/0/0 | 0/0/0 |
| /inventory/stock | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| /inventory/shipments | 0/0/2 | 0/0/2 | 0/0/2 | 0/0/2 | 0/0/0 |
| /reports/dashboard | 0/0/3 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| /reports/overview-report | 0/0/3 | 0/0/2 | 0/0/2 | 0/0/2 | 0/0/1 |
| /hr/employees | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| /hr/compensation | 0/0/1 | 0/0/1 | 0/0/1 | 0/0/1 | 0/0/1 |
| /settings/lead-assignment | **2**/0/0 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |
| /settings/roles | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 | 0/0/0 |

Tổng: **4 phần tử tràn + 1 bị cắt, tất cả ở 375px**; ≥768 sạch tràn/cắt trên các trang danh sách (shell + wrapper bảng đã đúng).

**Vi phạm cụ thể — đầu vào S1/S3**
| Trang @px | Vi phạm | Nguồn |
|---|---|---|
| contacts @375 | Hàng filter `flex flex-wrap items-center gap-2` rộng 424px (DateFilter inline + MultiSelect `w-[190px] shrink-0`) — ô ngày "09/24/2…" bị cắt mép | `features/contacts/components/ContactsFilterBar.tsx:58` |
| warranty @375 | Toolbar `ml-auto flex items-center gap-2` (Xuất Excel / Cột / Thêm bảo hành) không wrap → nút "Thêm bảo hành" bị card `overflow-hidden` cắt 32px | `pages/WarrantyClaimsPage.tsx:175` |
| lead-assignment @375 | `<table>` không wrapper cuộn, rộng 531px (khớp hotspot R đã biết) | `pages/LeadAssignmentPage.tsx:342` (và `:474`) |
| lead-assignment @375 | Nút "Copy cấu hình ngày gần nhất" tràn 79px (hàng không wrap); ô giờ ca bị xuống dòng lệch | `pages/LeadAssignmentPage.tsx:561` |
| orders @768 | Toolbar bảng dùng `overflow-x-auto` (nội dung 890/735px) → nút thao tác phải cuộn ngang mới thấy — cân nhắc `flex-wrap` | `features/orders/OrdersTable/OrdersTable.tsx:305` |
| shipments @375–1024 | Filter bar `flex-nowrap … overflow-x-auto` (1050px) → filter ẩn sau cuộn ngang ở tablet | `features/inventory/shipments/ShipmentsFilterBar.tsx:63` |

Bảng rộng cuộn ngang (ứng viên sticky cột định danh, S3): orders 1480px, loyal-customers 2432px, customers 2116px (≥768),
contacts 2012px, compensation 1801px, warranty ~1430px, resale 1280px, overview-report bảng con 600–890px (`components/report/CollapsibleTable.tsx`).
Customers/warranty/products/stock/employees ở 375 đã chuyển card view (ResponsiveList, ngưỡng 768).

Quan sát bằng mắt (ảnh baseline): ô chữ phụ `text-[10px]` ("VIETTEL", badge đếm) rất nhỏ ở 375 (F1); cột "Mã kênh" orders
bẻ tên kênh thành 6–7 dòng làm hàng cao ~120px; placeholder ô tìm kiếm bị cắt ở 375 (customers/warranty).

**Chưa làm / giới hạn**
- Endpoint còn fallback (trang vẫn render): `callcenter/orders/:id/call-records`, `contact-flow/results|stages`,
  `orders/kiotviet-location-data`, `orders/kship-note-presets`, `orders/price-books`, `orders/product-categories`.
- Chỉ đo trạng thái mặc định của trang — dialog/drawer/popover (WebphonePanel w-340, ContactHistoryPopover w-320,
  RecallEntriesCell, PackagesEditor…) chưa mở nên chưa đo; S3 có thể thêm bước `ACTIONS` click mở dialog.
- Không đo cỡ chữ/touch target (F1/F3) — chỉ overflow; đánh giá qua ảnh.
- `_full.png` (375) nới khung `h-dvh`/`overflow-y-auto` bằng CSS chèn sau khi đo để chụp hết trang.

**Rủi ro/cần QA**: không đụng mã `src/`; eslint/prettier/knip chỉ quét `src/` nên không ảnh hưởng.

### Sprint 1 — Nền tảng (✅)

**Đã làm**
- **Typography (F1, R2)** — `styles.css`: bỏ tier root 14px (<1280), chỉ còn 15px cho <1536. `@theme`:
  `--text-xs: max(12px, 0.75rem)` (sàn 12px mọi tier; trước: 10,5px ở <1280) và `--text-2xs: 11px`
  (+ line-height 16/11) chỉ cho badge đếm số. **71 → 0** `text-[9|10|11px]` (41 file): 59 → `text-xs`,
  12 badge đếm (TopNav thông báo, chip trạng thái Orders, OrdersFilterBar, ContactActionChips,
  OperationBucketChips, ColumnManagerGroupSection, RolesPage) → `text-2xs`. `RecallEntriesCell` số thứ tự `w-4 → w-5`
  cho vừa "#10". `tailwind-merge` nhận `text-2xs` là font-size (đã kiểm, không nuốt class màu).
- **Breakpoint (R3)** — `lib/constants/layout.ts`: `MOBILE_VIEWPORT_QUERY` (<768, camera quét kho — giữ nguyên),
  `TABLE_VIEWPORT_QUERY` (≥1024 `lg`, thay `DESKTOP_VIEWPORT_QUERY` 768) cho `ResponsiveList`, `IssueTab`, `ReturnTab`;
  `COMPACT_*` giữ nguyên. Comment ghi quy ước `md:`/`lg:` cho layout nhiều cột.
  `test/viewport.ts` thêm `mockViewportWidth(px)` (đánh giá min/max-width thật) — test mới cho ResponsiveList
  (375/768/1023 thẻ, 1024/1280 bảng) và ScanDispatchDialog ở 800px (không camera).
- **Overflow (R1)** — bỏ `html, body { overflow-x: hidden }`. Login vốn đã `overflow-hidden`; warranty-portal thêm
  `overflow-x-clip` ở root. Grep blob trang trí absolute âm: chỉ login (đã clip). `lib/dev/overflow-detector.ts`
  (ResizeObserver + MutationObserver, debounce 500ms, `console.warn` 1 lần/selector, bỏ qua phần tử trong
  `overflow-x auto|scroll` và `pointer-events:none`); `main.tsx` import động trong nhánh `import.meta.env.DEV`
  (đã kiểm: không có trong `dist/`). 6 test cho hàm thuần `findOverflowingElements`/`describeElement`.
- **Touch target (F3)** — Tailwind 4.3.1 có sẵn `pointer-coarse:` (không cần `@custom-variant`). `Button`:
  default/sm/lg → `h-11`, xs → `h-9`, icon/icon-lg → `size-11`, icon-sm/icon-xs → `size-10`; `Input` + `SelectTrigger`
  → `h-11`; 14 `<select>` native trong ô OrdersTable `h-7 → h-8 pointer-coarse:h-10`. Chuột (pointer fine) không đổi.
- **Guard (2.11)** — `eslint-plugin-jsx-a11y@6.10.2` (devDep) với 13 rule chọn lọc ở `error`; `aria-role` đặt
  `ignoreNonDOM` (prop nghiệp vụ `role="staff"` của component). Vi phạm phát sinh: 1 (`SelectTrigger` combobox thiếu
  `aria-controls`) → thêm `useId` cho listbox + `aria-controls`. `no-restricted-syntax` chặn `text-[9|10|11(.5)px]`
  trên Literal/TemplateElement cho toàn `src/` (trừ test) — đã thử file mẫu, bắt đúng.
- **Phát sinh** — root 14→15px ở <1280 làm toolbar OrdersTable (`overflow-x-auto`) giấu nút "Tạo đơn" ở 375:
  đổi sang `flex-wrap` (việc R4 của S3 làm sớm cho riêng toolbar này; hết vùng cuộn ở 375/768/1024).

**ui-check `s1` so baseline** (`tràn/cắt` không đổi: 4 tràn + 1 cắt, đều ở 375 — hotspot thuộc S3)

| Thay đổi | Baseline | S1 |
|---|---|---|
| docScrollWidth mọi trang/độ rộng | = viewport (bị clip che) | = viewport (không còn clip, vẫn không cuộn trang) |
| Orders toolbar cuộn ngang @375/768/1024 | 1/2/1 vùng cuộn | 1/1/1 (chỉ còn bảng) — toolbar wrap |
| @768 customers/loyal/resale/warranty/products | bảng cuộn ngang | thẻ (ResponsiveList ≥1024) — 0 vùng cuộn |
| Bảng orders @1280 | 1480px | 1646px (chữ phụ 10→12px) |

Ảnh kiểm bằng mắt: orders 375/768/1280 (badge đếm, "VIETTEL" đọc được, toolbar đủ nút), customers 768 (thẻ).

**Chưa làm**
- jsx-a11y chưa bật: `label-has-associated-control`, `click-events-have-key-events`, `no-static-element-interactions`,
  `no-noninteractive-element-interactions`, `interactive-supports-focus`, `no-autofocus` — hàng trăm vi phạm cũ, dọn dần.
- Cấm `window.confirm`/`confirm`/`alert` — để S2 (sau khi có `useConfirm`).
- Không chuẩn hoá `text-[12px]`/`text-[13px]` và `DESKTOP_MEDIA_QUERY` riêng trong WarrantyPortalPage (1024, brand riêng) — S4 (arbitrary px).
- Cột "Mã kênh" orders vẫn bẻ tên kênh 6–7 dòng (S3).

**Rủi ro / cần QA**
- Chữ ≥12px làm vài bảng rộng thêm (orders +166px) và hàng cao hơn nhẹ; laptop <1280 to hơn ~7% (14→15px).
- Tablet 768–1023 giờ thấy thẻ thay vì bảng ở các trang dùng ResponsiveList + tab Lịch sử nhập xuất — xác nhận với kho/CSKH.
- `pointer-coarse:` thắng `className` h-*/size-* truyền vào Button/Input trên thiết bị cảm ứng (twMerge không gộp khác variant):
  nút nhỏ trong ô bảng/ô input trên tablet cảm ứng sẽ to lên 40–44px — cần QA iPad/Android tablet.
- Bỏ `overflow-x: hidden` trên body: phần tử tràn ngoài khung app giờ tạo thanh cuộn ngang thay vì bị cắt —
  dev thấy qua `[overflow]` warning; ui-check không phát hiện trang nào cuộn ngang.
- Commit lint (`1ee86553`) đứng trước commit thay chữ — lint chỉ xanh từ `fec43407` (lưu ý khi bisect).

### Sprint 2 — Component (✅)

**Đã làm**
- **Xác nhận (F2, 2.5)** — `components/ui/confirm-dialog.tsx` (`ConfirmDialog` trên Dialog size `confirm`,
  `role="alertdialog"` + `aria-labelledby/describedby`, focus mặc định nút Huỷ, trả focus về chỗ cũ khi đóng,
  Escape bắt ở capture phase + `stopPropagation` để Sheet/Dialog bên dưới KHÔNG đóng theo) + `ConfirmProvider`
  gắn ở `main.tsx` (bọc mọi entry: app, `/warranty`, `/bao-hanh`, `/sp`, `/m`). `hooks/use-confirm.ts`:
  `useConfirm()` → `Promise<boolean>`; gọi mới khi lần trước chưa trả lời → lần trước resolve `false`
  (không Promise treo); thiếu provider → reject lúc GỌI (render vẫn chạy). Nút xác nhận mặc định
  "Xác nhận" / "Xóa" khi `destructive`. i18n thêm `common.confirm`, `common.unsaved.*` (vi/en).
- `hooks/use-unsaved-guard.ts` thay 3 bản lặp: `OrderDetailPage` (quay lại/trước/sau), `Sheet3AssessmentPanel`
  (đóng panel), `SystemSettingsPage` (đổi tab). Không có `beforeunload` nào trong 3 chỗ này nên không đổi;
  nếu sau thêm, `beforeunload` vẫn phải dùng hộp thoại native (bắt buộc đồng bộ) — ghi trong JSDoc hook.
- **25 → 0** `window.confirm`/`confirm()` (19 file); 0 `alert()` từ trước. Handler chuyển async, giữ nguyên
  nghiệp vụ; câu hỏi dài tách `title` + `description`, thêm `confirmText` sát hành động ("Huỷ phiếu",
  "Gỡ khớp", "Đóng case"…). ESLint: `no-restricted-globals` (`confirm`, `alert`) + `no-restricted-properties`
  (`window|globalThis` . `confirm|alert`) mức error cho toàn `src/` (gộp vào khối fetch/history có sẵn).
- Test: `confirm-dialog.test.tsx` (7: role/tên/mô tả, focus Huỷ, xác nhận/huỷ/Escape, destructive, gọi liên tiếp,
  Escape không lan xuống document, thiếu provider), `use-unsaved-guard.test.tsx` (2). 4 test cũ mock
  `window.confirm` (Foxia, JourneyCard, pending-replacement, flow-tabs) chuyển sang bấm nút trong
  `alertdialog`; helper `answerConfirm(user, accept)` ở `test/utils.tsx`.
- **Màu trạng thái (F6, 2.6)** — token `--action-info` (oklch 0.5 0.12 230) + `--color-action-info`.
  4 token `action-*` trước đó **chưa có chỗ nào dùng** → chỉnh độ sáng để chữ đạt ≥ 4.5:1 trên nền card
  VÀ nền tint /10 (tính oklch→sRGB): danger 0.55/0.22/27 (4.7:1 trên tint), success 0.5/0.14/152,
  warning 0.53/0.14/55 (hổ phách đậm ~amber-700; giá trị cũ 0.66 chỉ 3:1), info 0.5/0.12/230.
  `components/ui/status-badge.tsx` (cva, `tone: success|warning|danger|info|neutral`, cỡ theo Badge, `data-tone`) + test;
  dùng ở `LedgerTable` (nháp=neutral, chờ duyệt=warning, ghi sổ=success, huỷ=danger) và `PendingScansDialog`.
  `Badge` variant `success`/`warning`, `RatingBadge` chuyển sang token.
- Đổi palette thô mang nghĩa trạng thái → `action-*` ở ~70 file: lỗi form (`text/border-red-500`), hộp cảnh báo/
  thông tin/lỗi (amber/sky/rose/green-50…), kết quả quét kho, trạng thái mã bảo hành/lô, KPI đỏ, thanh tiêu chí
  call-quality, urgency liên hệ (`contact-actions`, mức đậm nhạt giờ phân biệt bằng font-weight), ring "chưa lưu"
  ở form đơn, nút Khôi phục/Gọi/Lưu đánh giá… Bỏ luôn biến thể `dark:` đi kèm ở các dòng đã đổi.
- **Số liệu** (grep `(bg|text|border|ring|…)-(red|amber|emerald|green|yellow|orange|sky|rose|blue)-\d+`, bỏ test):
  **672 → 348** (bỏ warranty-portal + `modules.ts`: **624 → 300**); lớp `*-action-*`: **0 → 288**.
- **Emoji → lucide (F5, 2.7)** — `SectionTitle` (journey-fields) với icon `aria-hidden size-4`: journey send
  (User/Settings/ShoppingCart), return (Warehouse/Send/ShieldCheck/CircleCheck/Mailbox), Best form
  (`AddressFields` thêm prop `icon`: PackageOpen/Inbox/Undo2; ghi chú: NotebookPen). Header nhóm cột Khách hàng
  (`profile-columns.ts` thêm `icon?: LucideIcon`, nhãn bỏ emoji), 🔔 nhắc CRM → `Bell`, ✓ MultiSelect → `Check`.
  Quét kho: `dispatchResultBadge`/`scanRowResultLabel` chỉ trả câu chữ, `ScanResultLabel` vẽ `Check`/`AlertTriangle`/`X`
  theo tone với màu token (ScanDispatchDialog + ScanReturnDialog). Check-in HR: bỏ ✓/✗ khỏi chuỗi i18n (đã có MapPin/MapPinOff).
- **Login (F11)** — label `sr-only` đã có sẵn; thêm `name`, `autoComplete="username"|"current-password"`,
  `autoCapitalize="none"`, `spellCheck={false}` + test `app/__tests__/login-view.test.tsx`.

**ui-check `s2`** (login, orders, order detail, loyal-customers, lead-assignment, warranty, stock, contacts @375/1280):
tràn/cắt **không đổi** so S1 (lead-assignment 2, warranty 1+1 cắt, contacts 1 — đều ở 375, hotspot S3); 1280 sạch.
Ảnh kiểm mắt: banner info trang chi tiết đơn, hộp giải thích lead-assignment, badge "Đã xuất VAT", loyal 375 — không hồi quy.

**Chưa làm (giữ nguyên có chủ đích)**
- Bảng màu >4 trạng thái / thang nhiệt — gộp vào 4 tone sẽ mất phân biệt: trạng thái giao hàng
  (`logistics-cells` 6 nhóm màu/14 trạng thái), trạng thái hoá đơn KiotViet (`finance-cells`, cam "không giao được"),
  tint hàng theo trạng thái sale (`OrdersTable/constants.ts`, `row-tint.ts` — kèm hex `stickyBg`),
  trạng thái hành trình BH (`journey-enums`), trạng thái phiếu BH (`warranty-claim-columns` 10 trạng thái),
  thang "số ngày từ lần mua cuối" (`LoyalCustomersPage`, `resale-format`) và mã S1–S7.
- Màu phân loại (không mang nghĩa đúng/sai): nguồn đơn, VAT, loại vận chuyển, loại đơn KOC, nền tảng ads,
  nhãn "Khách mới", hành động liên hệ (`contact-actions` badge), huy chương, sao đánh giá, accent tiêu đề
  sky-900 ở LeadAssignment, marker AI.
- ⭐ trong nhãn đánh giá CRM (`<option>` native + chuỗi react-select) — option không chứa được SVG; là nội dung, không phải icon.
- Không thêm `beforeunload` cho form chưa lưu (thay đổi hành vi — cần PO quyết).

**Rủi ro / cần QA**
- Luồng xác nhận giờ bất đồng bộ: kiểm tay các thao tác huỷ phiếu kho/đơn hoàn, gỡ khớp, xoá mã chờ, huỷ/khôi phục
  đơn KiotViet (menu portal), đóng case Sheet3, xoá ngày phân bổ lead, xoá FB App/Google Ads/blocklist/vị trí chấm công,
  đổi tab Cài đặt hệ thống khi có thay đổi, đóng panel đánh giá khi chưa lưu, Quay lại/Trước/Sau ở chi tiết đơn.
- Dialog xác nhận mở từ trong popover/menu: popover đóng ngay khi bấm vào dialog (mousedown outside) — handler vẫn chạy tiếp.
- Token `action-*` đậm hơn giá trị cũ (chưa ai dùng) — màu cảnh báo là hổ phách nâu thay vì vàng tươi.
- Nháp phiếu kho đổi vàng → xám; "Chờ khớp" ở bảng mã chờ đổi vàng → xanh info (tách khỏi cam "đơn đã hủy").

### Sprint 3 — Responsive & bảng (🟡)

**Đã làm**
- **Vi phạm baseline 375px → 0**: `DateFilter inline` wrap (cặp ô ngày co giãn, nhóm riêng) — sửa luôn contacts 424px
  và shipments; toolbar `WarrantyClaimsPage` `flex-wrap justify-end`; `LeadAssignmentPage`: 2 bảng bọc `overflow-x-auto`
  (bảng phân bổ `min-w-[640px]`, select NV `min-w-40`), hàng nút `flex-wrap`, ô giờ ca thành cặp co giãn (không xuống dòng lệch);
  `ShipmentsFilterBar` bỏ `flex-nowrap overflow-x-auto` → `flex-wrap` (ô `w-full sm:w-56`), hàng filter + nút "Quét xuất kho" wrap.
- **Hotspot theo code**: `PackagesEditor` lưới `grid-cols-2 sm:grid-cols-12` (`grid-flow-row-dense`, KT chiếm cả hàng);
  Webphone `max-w-[calc(100vw-2rem)]`; `PopoverContent` (component dùng chung) thêm `max-w-[calc(100vw-1rem)]` — phủ
  ContactHistoryPopover w-320, KshipNotePresetPicker w-280, SystemSettings w-96…; `order-detail-shared` `grid-cols-1 sm:grid-cols-12`;
  badge "Mã kênh" + "Kênh KiotViet" (read-only) 1 dòng `max-w-48 truncate` + `title` (trước: 6–7 dòng ở 375).
- **Bảng không wrapper** (script: file có `<table` mà không có overflow/DataTableShell/ResponsiveList — 20 file): 13 bảng
  report/dashboard (gồm `ProvinceRevenueCard`) đã nằm trong `CollapsibleTable` (`overflow-x-auto`) → false positive, không sửa;
  `OrderFormPurchaseHistory` có `overflow-y-auto` (tự thành cuộn 2 chiều); `StockMovementHistory` là bảng con trong ô bảng cha.
  Thêm `overflow-x-auto` cho 5 bảng trong dialog: PendingScans, ScanDispatch, ScanReturn, AdjustReturn, StockDocumentDetail (`overflow-hidden` → auto).
- **Toolbar wrap (R4)**: quét hàng `flex … justify-between` không wrap trong `pages`/`features/*/pages` (30) + quét heuristic
  hàng ≥2 control toàn `src` → thêm `flex-wrap gap-2` cho 13 hàng thật sự có ≥2 control: Blocklist (phân trang), WarrantyClaimDetail,
  Roles (header nhóm quyền), FacebookAds, GoogleAds, MyLeave, MyAttendance, PancakeSettings (CardHeader), FloatingPool (phân trang),
  Shipments, EmployeeProfileForm (tài khoản, cả nhóm nút), AttachmentsSection. Hàng còn lại chỉ là text + 1 icon/nhóm icon nhỏ → giữ.
- **Sticky cột định danh**: `@utility sticky-col` / `sticky-col-edge` trong `styles.css` — nền đặc (card) + phủ `--sticky-tint`
  (biến đặt ở rule zebra/hover/selected của bảng `data-slot`), header dùng màu thead; `@utility` nên dùng được `md:sticky-col`.
  Áp dụng: Warranty claims (checkbox + cột hiển thị đầu; trước: nền `bg-muted/40` trong suốt nhìn xuyên), Customers (header
  tầng 2 "Mã KH"/"Họ tên" nay dính cùng ô thân — trước chỉ thân bảng dính nên lệch; hàng lịch sử đơn dùng tint muted/20 đặc),
  Contacts (≥768: checkbox + # + Khách hàng; <768 chỉ Khách hàng dính mép trái), LoyalCustomers / Compensation (Họ tên),
  Resale (Khách hàng). OrdersTable: có sẵn cơ chế ghim cột (đo width thật) → chưa ghim cột nào thì tự dính cột tên/SĐT đầu tiên
  đang hiển thị ở ≥768 (`resolveStickyColumns`, không ghi vào cấu hình đã lưu; cột đầu mặc định là "Trạng thái KiotViet" — không
  phải định danh nên chọn tên/SĐT) + 4 test.
- **DataTableShell skeleton (2.9)**: prop `loading` / `skeletonRows` (8) / `skeletonColumns` (6) → `SkeletonTable` thay thân bảng,
  giữ toolbar, `aria-busy`, không hiện EmptyState khi đang tải; `SkeletonTable` nhận `label` (i18n). 4 test. Áp cho 15 nơi (chỉ lần
  tải đầu `isLoading && rows.length === 0`, refetch giữ bảng cũ): Compensation, Warehouses, ProductCategories, LoyalCustomers,
  Customers, WarrantyClaims, Employees, AttendanceLocations, LeaveRequests, AttendanceHistory, warranty-codes (Codes/Batches/ScanLogs),
  Products, CallQuality. Gỡ nhánh spinner trong bảng đã thành dead code (Compensation, Warehouses, ProductCategories, Loyal).
- **PageHeader**: FloatingPoolPage chuyển sang `PageHeader` (tiêu đề + quota + nút cấu hình).
- **ui-check**: thêm `SCROLL_X` — chụp thêm `_scrollx.png` sau khi cuộn ngang mọi vùng cuộn N px (kiểm cột sticky).

**ui-check `s3`** (18 trang × 5 độ rộng): **tràn/cắt = 0 ở mọi trang/độ rộng** (baseline/S1/S2: 4 tràn + 1 cắt ở 375).
| Trang | Baseline 375 | S3 375 | Ghi chú |
|---|---|---|---|
| /sales/contacts | 1/0/1 | 0/0/1 | filter wrap |
| /aftersales/warranty | 1/1/0 | 0/0/0 | toolbar wrap |
| /settings/lead-assignment | 2/0/0 | 0/0/1 | bảng phân bổ cuộn trong wrapper |
| /inventory/shipments 375–1024 | 0/0/2 | 0/0/1 | filter bar hết cuộn ngang, chỉ còn bảng |
Ảnh kiểm mắt (`s3-scroll`, cuộn 600px): orders 1280 (tên dính sau checkbox, nền tint đơn đúng màu), contacts 375/768, customers
1280 (header khớp thân), warranty 1280, loyal 1280, lead-assignment 375 full, shipments 768. Bảng orders 1280: 1646 → 1750px
(badge kênh 1 dòng rộng tối đa 192px thay vì bẻ dòng).

**Kiểm tra**: tsc, eslint 0 warning, prettier, knip xanh; vitest 27 file liên quan (186 test) xanh; `vite build` OK.

**Chưa làm**
- `RecallEntriesCell` `min-w-[430px]`: nằm trong bảng orders đã cuộn ngang (không phải lỗi tràn); cho wrap sẽ làm cột co về
  min-content và hàng cao lên ở MỌI độ rộng — thay đổi mật độ bảng chính của sale, cần PO quyết.
- `ContentFilterBar` (media) giữ `flex-nowrap overflow-x-auto` — comment ghi rõ là yêu cầu chủ shop.
- Warranty: ghim ≥2 cột vẫn cùng `left-10` (đè nhau — lỗi có từ trước, cần đo width như orders).
- Skeleton chưa áp: KiotViet/Channels/Users table (component nhận dữ liệu đã phân trang từ cha, không có cờ loading — cần
  truyền qua 2 tầng), OrdersTable (không dùng DataTableShell, vẫn LogoSpinner). Chưa chuyển 46 bảng thô sang DataTableShell.
- PageHeader: giữ header đặc thù — trang chi tiết có nút Quay lại + badge trạng thái (EmployeeDetail, ProductContent,
  WarrantyClaimDetail, OrderDetail topbar), ESS mobile (h1 riêng), Contacts/Resale (tiêu đề nằm trong hàng filter),
  FB/Google Ads (tiêu đề section có nút thu gọn). Trang danh sách còn lại không có header tự viết (tiêu đề ở TopNav).
- Checkbox sticky của OrdersTable ở hàng chẵn không mặc định vẫn nền card (không zebra) — giữ vì `stickyBg` do `row-tint` quyết.

**Rủi ro / cần QA**
- Orders: người chưa ghim cột nào sẽ thấy cột Tên khách hàng dính khi cuộn ngang (≥768) — cột ghim thủ công vẫn ưu tiên.
- Contacts ở 375: cột Khách hàng (180px) dính chiếm ~½ màn khi cuộn ngang.
- `PopoverContent` có `max-w` mặc định: popover rộng hơn viewport-16px giờ bị giới hạn (nội dung wrap) — kiểm popover nhiều cột.
- Skeleton thay spinner ở lần tải đầu: test e2e/QA nào chờ chữ "Đang tải..." trong bảng của các trang trên sẽ không thấy nữa
  (skeleton có `role=status` + `aria-label`).

### Sprint 4 — Select hợp nhất, dark mode, arbitrary px (✅)

**Đã làm**
- **`SearchableSelect` viết lại** theo WAI-ARIA combobox (ô nhập `role=combobox` + `listbox`, `aria-activedescendant`,
  ↑/↓/Home/End/Enter/Esc/Tab; Esc trong Dialog chỉ đóng menu). Menu **portal ra `document.body`** (`position: fixed`,
  tự lật lên khi thiếu chỗ, bám theo cuộn/resize — hook `components/ui/floating-menu.ts`) nên không bị Dialog/Sheet/ô bảng
  `overflow` cắt. Prop mới: `creatable` (+ `formatCreateLabel`, dòng "Thêm "…"" khi từ khoá không trùng nhãn/giá trị),
  `clearable`, `group` trên option (header nhóm `role=group`), `renderOption`, `emptyText`, `size="sm"`, `controlClassName`,
  `id` (cho `<label htmlFor>`), `onSelectOption` (nhận object option — form đơn cần `product`). Không có aria-label/label thì
  tên a11y = nhãn đang hiện/placeholder (như nút cũ). Trong `<fieldset disabled>` không mở menu. Từ khoá rỗng ở chế độ
  `onSearch` nạp ngay (không debounce).
- **`MultiSelect`**: nút `role=combobox` (nhận `id` → `label htmlFor` trỏ thẳng, bỏ input `sr-only`), listbox
  `aria-multiselectable`, option `aria-selected` (bỏ checkbox native lồng trong option), ↓ mở, ↑/↓ + Enter bật/tắt, Esc đóng
  và trả focus, chip có nút "Bỏ …" có tên; thêm `group`, `size="sm"`, `disabled`, `emptyText`, `aria-label`.
- **`Select` (shadcn)**: thêm bàn phím (↓/↑ mở + tô item, Enter/Space chọn, `aria-activedescendant`, `data-highlighted`) —
  để chuyển `<select>` native sang không làm mất khả năng dùng phím.
- **Gỡ react-select: 10 → 0 file**, `npm uninstall react-select`, xoá `react-select-styles.ts` (`orderGridSelectStyles`…) và
  `channels/select2-styles.ts`; ESLint `no-restricted-imports` react-select nâng `warn → error`, bỏ allowlist.
  | File | Thay bằng | Ghi chú nghiệp vụ |
  |---|---|---|
  | `order-form/OrderFormBasicSection` (7 ô) | SearchableSelect `clearable` (VAT không) | payload giữ nguyên: xoá nhân viên = `null`, VAT rỗng = `"no"`; label `htmlFor` |
  | `order-form/OrderFormCustomerSection` (Khu vực/Phường xã) | SearchableSelect `creatable clearable` | đổi khu vực vẫn xoá phường |
  | `order-form/OrderFormItemsSection` (AsyncSelect) | SearchableSelect `onSearch` | hook `use-order-form` trả `searchProductOptions(q)`: rỗng → gợi ý mặc định (không gọi server), có từ khoá → tìm theo bảng giá + `mergeProducts`, gom nhóm như cũ (header nhóm), in đậm biến thể SKU qua `renderOption`; dòng chỉ có SKU vẫn hiện SKU |
  | `order-detail-shared` AddressFields (4 CreatableSelect) | SearchableSelect `sm creatable clearable` | |
  | `BlockProducts` | SearchableSelect `sm clearable` | chỉ hiện khi product_id có trong danh mục (như cũ) |
  | `BlockCrmRating` (multi ×N, nhu cầu có nhóm, 2 ô lý do bom) | MultiSelect `menuPortal` / SearchableSelect `sm` | nhu cầu giữ `group_code`; mã lạ bị bỏ như trước |
  | `orders-filters/filter-fields` | MultiSelect (`size="sm"` ở thanh ngang) | hợp đồng `value/onChange` mảng option giữ nguyên |
  | `ChannelCreateDialog`/`ChannelManageDialog` | MultiSelect TKQC + SearchableSelect gán user Ads | `resolveAdAccountSelection` thay `resolveSelectedAdAccountOptions`: nhóm đang chọn mang PK **đang lưu** (không ghi đè 172→21) — test hồi quy cũ vẫn xanh |
  | `users/DepartmentManager` | MultiSelect `menuPortal` + label `htmlFor` | |
- **`<select>` native ngoài bảng → shadcn/Searchable**: PancakeLeadsPage (3 lọc), PancakeSettingsPage (kênh theo page, sale theo
  NV — bảng cấu hình vài dòng), ContentFormDialog (nguồn, loại), ProposalFormDialog (trạng thái → Select; sản phẩm → SearchableSelect
  vì danh mục dài), LeadAssignmentPage (NV mỗi dòng → SearchableSelect).
- **`NativeCellSelect`** (`components/ui/native-cell-select.tsx`, `variant outline|filled`, h-8 `pointer-coarse:h-10`, focus ring token):
  thay **17** `<select className=…>` lặp ở cell-renderers (channel 4, crm 5, customer 1, finance 1, logistics 3), MultiSelectCell,
  SalesStatusInlineEditor (h-7 → h-8, sót ở S1) và WarrantyClaimsTable (ô trạng thái, h-7 → h-8). min-w cột giữ px.
- **Dark mode (F8)**: xác nhận không có toggle/`classList.add("dark")`/`prefers-color-scheme`. Xoá `@custom-variant dark` + khối
  `.dark {…}` (40 biến) trong `styles.css` (thay bằng 1 dòng ghi chú light theme), **24 → 0** class `dark:` (S2 đã gỡ 25/49 kèm dòng
  đổi token). Không test nào assert `dark:`.
- **Arbitrary px (F10)**: **297 → 170** `[..px]` (grep `[a-z-]+-[Npx]`, bỏ test). 50 `text-[12px]` → `text-xs` (= đúng 12px ở mọi
  tier nhờ sàn `max(12px, .75rem)`, chỉ thêm line-height 16px); 63 kích thước chia hết 4px → thang spacing (`w-[160px]`→`w-40`,
  `min-h-[112px]`→`min-h-28`, `max-w-[520px]`→`max-w-130`, …) cho control/filter/popover/dialog/placeholder. **Giữ px**: min-w bảng
  ≥500px, width cột bảng (dòng `<th>/<td>/<col>/TableHead/TableCell`, OrdersTable/**, ContactsTable, CustomerGroupCells,
  RecallEntriesCell, WarrantyClaimsTable, KshipDeliveryDialog, CompensationPage), giá trị không chia hết 4 (`w-[18px]`, `w-[155px]`…),
  `text-[13px]`. Script: `scratchpad/px.py` (không commit).
- `style={{}}`: 69 → 68; phần còn lại là động (width %, màu theo data/kênh, vị trí sticky) hoặc tĩnh có lý do (keyframe camera quét,
  font SVG recharts, màu brand portal bảo hành) → giữ.
- Portal bảo hành: `DESKTOP_MEDIA_QUERY` = `TABLE_VIEWPORT_QUERY` (cùng `lg` 1024) từ `lib/constants/layout.ts`.
- Test: mới `searchable-select` (+7: lọc/Enter/onSelectOption, activedescendant, creatable, clearable, group, portal + Esc trong
  Dialog, fieldset disabled), `multi-select` (4), `select` (2). Cập nhật sang role `combobox`/`option`: contacts-page, contact-cells,
  OperationResultDialog, operation-stage-select, call-quality filter, combo-components-editor, Pancake Leads/Settings,
  Content/ProposalFormDialog. Test form đơn (`OrderFormDialog.*`, `OrderDetailPage.*`, orders-filters, channels, DepartmentManager)
  **không phải sửa** — đã dùng role `combobox`/`option`.

**ui-check `s4`** (login, orders, order detail, lead-assignment, contacts, loyal-customers, customers × 5 độ rộng): **tràn/cắt = 0**
mọi ô (như S3). Ảnh kiểm mắt: chi tiết đơn 1280 (ô sản phẩm SearchableSelect khoá do có hoá đơn), 375 full (Khu vực/Phường xã,
select CRM nhiều lựa chọn).

**Kiểm tra**: tsc, eslint 0 warning, prettier, knip xanh; `vite build` OK; vitest 80 + 35 file liên quan (577 + 261 test) xanh.

**Chưa làm**
- `<select>` native ngoài phạm vi danh sách S4: ContentFilterBar, ContentMarketingCard, ContentUploadCard, ContentTagsDialog,
  TaxonomyFormDialog, StarRating (media), CampaignCostTable, FoxiaReportPage, ManualDispatchDialog — để S5/S6.
- `cacheOptions` của AsyncSelect (cache kết quả theo từ khoá) không mang sang: gõ lại từ khoá cũ sẽ gọi server lần nữa (debounce 250ms).
- Giá trị tạo mới (creatable) giờ được `trim()` — react-select giữ nguyên khoảng trắng đầu/cuối.
- 170 `[..px]` còn lại chủ đích (xem trên); `style={{}}` tĩnh còn lại không đổi.

**Rủi ro / cần QA**
- **Form tạo/sửa đơn (luồng lõi sale)**: kiểm tay chọn kênh/nhân viên/kênh KiotViet/kho/nguồn/VAT/kho xuất, gõ tìm sản phẩm theo
  bảng giá + đổi kênh tính lại giá, tạo mới Khu vực/Phường xã, xoá lựa chọn (nút X), dùng bàn phím (Tab/↓/Enter) — Enter trong menu
  KHÔNG submit form.
- Chiều cao ô chọn trong form đơn 40px (`h-10` như Input), trong chi tiết đơn/CRM 32px — như react-select cũ; chip MultiSelect CRM
  hiển thị kiểu badge thay vì chip react-select.
- Menu SearchableSelect/MultiSelect(menuPortal) nằm trong body với `z-60` — nếu đặt trong `Popover` (z 9999) thì menu nằm dưới và
  bấm vào menu bị Popover coi là "bấm ra ngoài" (hiện không có chỗ nào như vậy).
- Kích thước control đổi px → rem: ở <1536px (root 15px) các ô lọc/popover/dialog nhỏ đi ~6% (vd Webphone 340 → 319px) — chủ ý R2.
- Sau khi bỏ `.dark`, class `dark` nếu có extension trình duyệt thêm vào `<html>` không còn tác dụng (đúng ý).

### Sprint 5 — Tách god page (✅)

**Đã làm** — refactor thuần, không đổi props public / route / query key / text / class / thứ tự render.
Page trong `src/pages` giữ nguyên đường dẫn (router `app/routed-pages.tsx` và 6 test `OrderDetailPage.*` import
`@/pages/OrderDetailPage` không phải sửa), phần tách ra đặt vào `src/features/<domain>/…`.

| Page | Trước | Sau | Tách ra (dòng) |
|---|---|---|---|
| `ResaleTrackingPage` | 1127 | 102 | `features/customers/resale/`: `use-resale-tracking-list` (lọc/phân trang/facets/giữ vị trí cuộn, 170), `use-resale-row-actions` (3 dialog + submit, 163), `resale-tracking-state` (params/KPI/form mặc định — thuần, 92), `ResaleFilterBar` 138, `ResaleKpiCards` 35, `ResaleTrackingList` (bảng + card, 224), `ResaleRowParts` (badge trạng thái + 3 nút dùng chung bảng/card, 73), `ResaleContactDialog` 147, `ResaleAppointmentDialog` 127, `ResaleOrderDialog` 171 |
| `RolesPage` | 983 | 215 | `features/roles/`: `use-role-editor` (draft + toggle + lưu, 145), `role-draft` (gom section theo module, lọc role, dirty check, payload — thuần, 133), `components/RolePermissionsPanel` 148, `RoleFieldRulesPanel` 127, `RoleFieldGrantsPanel` 154, `RoleListSidebar` 92, `RoleDetailHeader` 82, `RoleDialogs` (tạo/xoá, 166) |
| `OrderDetailPage` | 970 | 260 | `features/orders/order-detail/`: `use-order-detail-draft` (pending + chặn field khoá hoá đơn + lưu, 93), `order-detail-draft` (gộp pending, deep-merge crm/recall — thuần, 52), `BlockGeneralInfo` 65 → `GeneralInfoOrderSection` 240 / `GeneralInfoCustomerSection` 106 / `GeneralInfoShippingSection` 86, `use-address-autofill` (parse địa chỉ thô / 2 cấp, 63), `OrderDetailTopbar` 141, `OrderDetailBottomTabs` 78 |
| `OrdersPage` | 870 | 331 | `features/orders/orders-page/`: `OrdersTablePanel` (OrdersTable + slot toolbar + phân trang, 230), `OrdersFiltersBar` (nối 70 prop vào `OrdersFilters`, 90), `OrdersPageDialogs` (precheck/log KiotViet, import, Kship, 71), `use-orders-order-dialog` (tạo/sửa đơn + strip field thiếu quyền, 105), `use-orders-crm-panel` (tab Tư vấn mở side panel, 35), `orders-filter-key` (khoá reset selection — thuần, 43) |
| `LoyalCustomersPage` | 864 | 93 | `features/customers/loyal/`: `use-loyal-customers-list` (preset/khoảng ngày/phân khúc/danh mục mặc định/phân trang, 191), `use-loyal-note-editor` (dialog ghi chú + vá cache, 76), `loyal-format` (định dạng + params + payload ghi chú — thuần, 127), `LoyalFilterToolbar` 130, `LoyalCustomersList` (bảng, 186), `LoyalCustomerCard` 142, `LoyalNoteDialog` 155, `RankMedal` 21 |

- Tổng 5 page: **4814 → 1001 dòng**; mọi page ≤ 331, component/hook con lớn nhất 240 (`GeneralInfoOrderSection`).
- Thứ tự hook/effect giữ nguyên (vd Roles: effect bỏ chọn role bị xoá chạy trước effect nạp draft; OrderDetail: state tab cuối
  vẫn ở page để không reset khi đơn đang tải lại). `DaysChip`/`ResaleRowActions` gom JSX lặp giữa bảng và card mà DOM không đổi.
- Test mới cho helper thuần: `customers/__tests__/resale-tracking-state` (5), `customers/__tests__/loyal-format` (6),
  `roles/__tests__/role-draft` (7), `orders/__tests__/order-detail-draft` (5), `orders/__tests__/orders-filter-key` (3).

**ui-check `s5`** (orders, chi tiết đơn, loyal-customers, resale, roles × 375/1280 + `_full` 375): tràn/cắt = 0. So ảnh **byte-by-byte**
(`cmp`): orders / chi tiết đơn / loyal **giống hệt s4**, roles **giống hệt s3**; resale khác s3 do đổi px→rem ở S4 nên build lại
bản trước S5 (`git show 1cd59aed:…/ResaleTrackingPage.tsx`, label `s5-pre`) — **giống hệt s5**. ⇒ không đổi giao diện.

**Kiểm tra**: tsc, eslint `src` 0 warning, prettier check, knip xanh; `vite build` OK; vitest file liên quan xanh — 6 test
`OrderDetailPage.*` + BlockJourneyEvents + invoice-lock (40), orders filter/column/sort/koc/shipping/sale-assignment/field-grant/import
+ i18n messages (89), roles section-labels + roles-field-grants + role-draft (16), resale-format + resale-tracking-state (20),
loyal-format (6), routes + modules (23). Không có test assertion nào phải sửa.

**Chưa làm**
- Không có test render cho ResaleTracking/Roles/LoyalCustomers page (trước S5 cũng không có) — dựa vào ui-check ảnh giống hệt;
  nên bổ sung test tương tác (mở dialog, lưu) ở sprint sau.
- `OrdersPage` vẫn nhận 5 nhóm props từ `useOrdersPageProps` (routed-pages) và truyền nguyên nhóm xuống `OrdersTablePanel` —
  chưa gom tiếp `OrderFormDialog` (≈25 prop) vì chỉ dời chỗ, không giảm độ phức tạp.
- `ordersFilterKey` giờ memo theo object `filters` thay vì từng field: chuỗi kết quả y hệt, chỉ tính lại JSON mỗi render khi
  route tạo object mới (rẻ).

**Rủi ro / cần QA**
- Luồng lõi không đổi logic nhưng file đã chuyển chỗ: QA nhanh chi tiết đơn (sửa địa chỉ thô/2 cấp, đổi kênh/sale/kho, Lưu, rời trang
  khi chưa lưu, Xuất kho), trang đơn (tạo đơn, import, KiotViet precheck/log, Kship, tab Tư vấn mở panel), phân quyền (tạo/xoá role,
  tick quyền/phạm vi/field, Lưu), resale (ghi liên hệ/đặt hẹn/đơn resale), KH thân thiết (lọc thời gian/phân khúc/danh mục, sửa ghi chú).

### Sprint 6 — Hồi quy toàn bộ (✅)

**Đồng bộ master**: `git fetch origin` thất bại (máy host không có SSH key GitLab) → so theo ref local: `origin/master` =
merge-base = `de490161`, không có commit mới cần merge. **Trước khi tạo MR phải fetch lại và merge master nếu có commit mới.**

**Kiểm tra đầy đủ** (container `node:22-alpine --cpus=2 --memory=3g`, chạy tuần tự từng lệnh)
| Lệnh | Kết quả | Thời gian |
|---|---|---|
| `npx tsc --noEmit` | ✅ 0 lỗi | 38s |
| `npx eslint src --max-warnings=0` | ✅ 0 lỗi / 0 warning | 66s |
| `npx prettier --check "src/**/*.{ts,tsx,css}"` | ✅ | 22s |
| `npx knip --no-progress` | ✅ (chỉ 1 hint cấu hình `.css` có sẵn) | 4s |
| `npm run gen:api` + `git diff --exit-code src/api/schema.d.ts` (như job CI `frontend:openapi-types`) | ✅ không drift | 3s |
| **`npx vitest run` (full)** | ✅ **256/256 file, 1880/1880 test**, 0 flaky | 461s (~7,7 phút) |
| `npx vite build` | ✅ | 84s |

`npm ci` không cần (không merge nên `package-lock.json` không đổi từ S4).

**Bundle** (build master `de490161` trong worktree tạm, `npm ci` riêng — đã xoá; kB theo Vite, tổng tính từ `dist/assets`)
| Chunk | master raw / gzip | branch raw / gzip | Δ |
|---|---|---|---|
| entry `index-*.js` | 387,4 / 112,8 kB | 392,8 / 114,9 kB | +5,4 / +2,1 (ConfirmProvider + dialog, key i18n) |
| css `index-*.css` | 135,4 / 22,1 kB | 136,6 / 22,1 kB | +1,2 (utility `sticky-col`, `pointer-coarse:`) |
| `vendor-select` (react-select + emotion) | 82,7 / 29,4 kB | **—** | **−82,7 / −29,4** |
| `vendor-shared` | 11,1 / 4,0 kB | 5,1 / 1,8 kB | −6,0 / −2,1 |
| `OrdersPage` | 226,5 / 67,9 kB | 234,3 / 70,3 kB | +7,8 (SearchableSelect viết lại, NativeCellSelect) |
| `OrderDetailPage` | 47,8 / 15,7 kB | 60,1 / 20,1 kB | +12,3 (sections tách + SearchableSelect) |
| Tải lần đầu (entry + css + vendor-react/core/ui) | 926 / 261 KiB | 934 / 263 KiB | +8 / +2,5 KiB |
| Tổng JS (140 → 141 chunk) | 4.212 / 1.291 KiB | 4.203 / 1.292 KiB | −9 / +0,6 KiB |

- Route **/sales/orders** (OrdersPage + vendor-select + vendor-shared): **320 / 101 kB → 239 / 72 kB (−81 / −29 kB gzip)**;
  chi tiết đơn (OrderDetailPage + vendor-select): 131 / 45 → 60 / 20 kB. Gỡ react-select làm nhỏ đúng các route dùng nó;
  tổng JS gần như không đổi vì code mới (SearchableSelect/MultiSelect viết lại, `floating-menu`, ConfirmDialog, StatusBadge,
  file tách của S5) bù lại. First-load +2,5 KiB gzip chấp nhận được (ConfirmProvider phải nằm ở root).

**ui-check `final`** (18 trang × 5 độ rộng, `SCROLL_X=600`, 5 phút): **tràn = 0, cắt = 0 ở cả 90 ô**, 0 ErrorBoundary.
| Trang | Baseline 375 / 768 / 1024 / 1280 / 1920 | Final 375 / 768 / 1024 / 1280 / 1920 |
|---|---|---|
| /login | 0/0/0 ×5 | 0/0/0 ×5 |
| /sales/orders | 0/0/1 · 0/0/2 · 0/0/1 · 0/0/1 · 0/0/1 | 0/0/1 ×5 (toolbar hết cuộn @768) |
| /sales/orders/900001 | 0/0/1 · 0/0/1 · 0/0/0 · 0/0/1 · 0/0/1 | như baseline |
| /crm/customers | 0/0/0 · 0/0/1 · 0/0/1 · 0/0/1 · 0/0/1 | 0/0/0 · **0/0/0** · 0/0/1 · 0/0/1 · 0/0/1 (768 = thẻ) |
| /sales/contacts | **1**/0/1 · 0/0/1 ×4 | **0**/0/1 ×5 |
| /crm/loyal-customers | 0/0/1 ×5 | 0/0/1 · **0/0/0** · 0/0/1 ×3 |
| /sales/resale-tracking | 0/0/0 · 0/0/1 · 0/0/1 · 0/0/1 · 0/0/0 | 0/0/0 · **0/0/0** · 0/0/1 · 0/0/1 · 0/0/0 |
| /aftersales/warranty | **1/1**/0 · 0/0/1 · 0/0/1 · 0/0/1 · 0/0/0 | **0/0**/0 · **0/0/0** · 0/0/1 · 0/0/1 · 0/0/0 |
| /catalog/products | 0/0/0 · 0/0/1 · 0/0/0 ×3 | 0/0/0 ×5 |
| /inventory/stock, /hr/employees, /settings/roles | 0/0/0 ×5 | 0/0/0 ×5 |
| /inventory/shipments | 0/0/2 ×4 · 0/0/0 | 0/0/1 ×4 · 0/0/0 (filter bar wrap) |
| /reports/dashboard | 0/0/3 · 0/0/0 ×4 | như baseline |
| /reports/overview-report | 0/0/3 · 0/0/2 ×3 · 0/0/1 | 0/0/3 · 0/0/2 · 0/0/3 · 0/0/2 · 0/0/1 (bảng con CollapsibleTable) |
| /hr/compensation | 0/0/1 ×5 | 0/0/1 ×5 |
| /settings/lead-assignment | **2**/0/0 · 0/0/0 ×4 | **0**/0/1 · 0/0/0 ×4 (bảng cuộn trong wrapper) |

Tổng: baseline **4 tràn + 1 cắt** → final **0 / 0**. "Cuộn" chỉ còn ở bảng rộng có chủ đích (đã có cột dính).

**Kiểm tra nghi vấn SCROLL_X** (`s3-scroll/crm_loyal-customers_1280_scrollx.png` "không cuộn"): **script đúng, nghi vấn sai**.
So với ảnh không cuộn, ảnh scrollx đã mất STT/Hạng/SĐT/Số lần mua/Tổng chi tiêu, cột "Sản phẩm đã mua" bị cắt còn đuôi
"…AX-0 (SL: 2)" — tức wrapper `div.relative.w-full.overflow-auto` (nội dung 2619 / khung 1188px) đã cuộn 600px; "HỌ TÊN" vẫn
đứng đầu chính vì nó là cột dính. Không sửa script. Ảnh `final/*_1280_scrollx.png` xác nhận cột dính cho **orders** (checkbox +
Tên khách hàng; Trạng thái KiotViet/Mã kênh/SĐT đã cuộn qua), **contacts** (checkbox/#/Khách hàng), **customers** (mở rộng +
checkbox + Mã + Họ tên, header 2 tầng khớp), **loyal** (Họ tên), **compensation** (Họ tên; cột Chuyên cần bị cắt dưới mép),
**warranty** (checkbox + Khách hàng), **resale** (Khách hàng). Lấy mẫu pixel nền ô dính vs ô thường ở hàng lẻ/chẵn:
warranty/compensation/loyal/customers khớp tuyệt đối; orders lệch 1–2 đơn vị (hex `stickyBg` của row-tint, không thấy bằng mắt).

**Sửa trong S6**
- **Contacts + Resale: ô dính khác màu phần còn lại của hàng** (ô dính `252,255,255` vs ô thường `245,251,255`): wrapper bảng không có
  nền nên ô thường lộ `--background` của trang, còn `sticky-col` sơn `--card`. Thêm `bg-card` cho wrapper
  (`ContactsTable.tsx`, `ResaleTrackingList.tsx`) — nay khớp tuyệt đối, bảng cùng nền card như các bảng `DataTableShell`.
  ui-check `final-fix` (2 trang × 375–1280): tràn/cắt 0.
- Comment lỗi thời nhắc react-select/AsyncSelect: `use-order-form-products.ts`, test `OrderFormDialog.auto-assign` / `.reprice`.
- Rà chéo `components/ui/*`, `styles.css`, `lib/constants/layout.ts`, `main.tsx`: không có import thừa, class trùng (script quét
  token lặp trong chuỗi className ở mọi dòng thêm mới — 0), TODO/FIXME mới (0), comment 14px/dark/window.confirm đều đã đúng
  hành vi mới (chỉ còn nhắc như lịch sử). Comment lịch sử "trước khi sửa… react-select" ở test ChannelManageDialog và
  `floating-menu.ts` giữ vì mô tả lý do/so sánh.
- Kiểm lại: prettier + eslint các file sửa, vitest contacts/customers/OrderFormDialog (15 file, 135 test) xanh, `vite build` OK.

**Chưa làm**: không fetch được origin (xem trên). Không có việc S6 nào khác bị hoãn.

---

## 5. Tổng kết & việc còn lại

### 5.1 Trạng thái phát hiện

| ID | Vấn đề | Trạng thái | Commit chính | Ghi chú |
|---|---|---|---|---|
| F1 | Chữ quá nhỏ | **Đóng** | `fec43407`, `1ee86553` | sàn 12px, 71 → 0 `text-[9-11px]`, ESLint chặn |
| F2 | `window.confirm` | **Đóng** | `692c9f8f` | 25 → 0, `useConfirm` + unsaved guard, ESLint chặn |
| F3 | Touch target | **Đóng** (cần QA thiết bị) | `f3ae6ef2`, `11f76204` | `pointer-coarse:` 40–44px; chuột không đổi |
| F4 | Select/bảng/PageHeader không thống nhất | **Một phần** | `11f76204`, `198d5a9a` | select: 5 → 3 hệ + NativeCellSelect, react-select 0; còn 46 bảng thô, PageHeader có ngoại lệ, 9 `<select>` native ở media/khác |
| F5 | Emoji làm icon | **Đóng** | `bacf5f0e` | ⭐ trong option CRM giữ (nội dung) |
| F6 | Màu palette thô cho trạng thái | **Một phần** | `21814010` | 672 → 348 (phần còn lại là thang >4 trạng thái / màu phân loại — chủ đích) |
| F7 | Spinner thay skeleton | **Một phần** | `198d5a9a` | 15 bảng có skeleton; OrdersTable, KiotViet/Channels/Users còn spinner |
| F8 | Dark mode dở dang | **Đóng** | `0a1676f9` | `.dark` + 49 `dark:` → 0 |
| F9 | God page | **Đóng** | `1238efbe` `ac2b491c` `5e0f3ec8` `bb8f90d3` `7403a789` | 4814 → 1001 dòng |
| F10 | Arbitrary px, `style={{}}` | **Một phần** | `eed5e0dc` | 297 → 170 `[..px]` (còn lại chủ đích: cột bảng, min-w bảng) |
| F11 | Login thiếu label/autocomplete | **Đóng** | `1d3121ed` | |
| R1 | `overflow-x: hidden` che bug | **Đóng** | `fec43407` | + `overflow-detector` dev |
| R2 | rem co / px không co | **Đóng** | `fec43407`, `eed5e0dc` | 1 tier 15px |
| R3 | 4 hệ breakpoint | **Đóng** | `f6b50cc5` | `layout.ts` 1 nguồn; bảng từ `lg` |
| R4 | Toolbar không wrap | **Đóng** | `e8b3fe23` | ContentFilterBar giữ cuộn (yêu cầu chủ shop) |
| R5 | Không có test viewport | **Một phần** | `ad95cc25`, `2cb3dbc1`, `198d5a9a` | ui-check chạy tay qua docker, chưa vào CI; `mockViewportWidth` cho unit test |

Kết quả đo: ui-check **4 tràn + 1 cắt → 0 / 0** (90 ô); full vitest 256 file / 1880 test xanh; route đơn hàng −29 kB gzip.

### 5.2 Việc tiếp theo (gom "Chưa làm" S0–S6, theo ưu tiên)

**P1 — nên làm sprint sau**
1. Fetch + merge `origin/master` trước khi mở MR (S6 không fetch được).
2. Test render/tương tác cho `ResaleTrackingPage`, `RolesPage`, `LoyalCustomersPage` (mở dialog, lưu) — trước/sau S5 đều chưa có (S5).
3. Warranty: ghim ≥2 cột bị đè cùng `left-10` — đo width thật như OrdersTable (S3, lỗi có từ trước).
4. Skeleton cho OrdersTable (bảng chính của sale, còn LogoSpinner) và KiotViet/Channels/Users table (truyền cờ loading qua 2 tầng) (S3).
5. Đưa ui-check vào CI (job thủ công/nightly, image `desi-ui-check`), bổ sung fixture 7 endpoint còn fallback
   (`callcenter/orders/:id/call-records`, `contact-flow/results|stages`, `orders/kiotviet-location-data`, `kship-note-presets`,
   `price-books`, `product-categories`) và bước `ACTIONS` mở dialog/popover (Webphone, ContactHistoryPopover, RecallEntriesCell,
   PackagesEditor chưa được đo) (S0).

**P2 — cần PO/nghiệp vụ quyết**
6. `RecallEntriesCell` `min-w-[430px]` — cho wrap sẽ đổi mật độ bảng đơn ở mọi độ rộng (S3).
7. `beforeunload` cho form có thay đổi chưa lưu (thay đổi hành vi) (S2).
8. Tablet 768–1023 nhận thẻ thay bảng (ResponsiveList, tab nhập xuất) — xác nhận với kho/CSKH (S1).

**P3 — dọn dần**
9. Bật tiếp jsx-a11y: `label-has-associated-control`, `click-events-have-key-events`, `no-static-element-interactions`,
   `no-noninteractive-element-interactions`, `interactive-supports-focus`, `no-autofocus` (hàng trăm vi phạm cũ) (S1).
10. `<select>` native còn lại: ContentFilterBar, ContentMarketingCard, ContentUploadCard, ContentTagsDialog, TaxonomyFormDialog,
    StarRating (media), CampaignCostTable, FoxiaReportPage, ManualDispatchDialog → shadcn Select/Searchable (S4).
11. Chuyển 46 bảng thô sang `DataTableShell`; PageHeader cho trang chi tiết (nút Quay lại + badge) nếu thống nhất được layout (S3).
12. Cache kết quả tìm sản phẩm theo từ khoá (thay `cacheOptions` của AsyncSelect cũ) nếu server bị gọi nhiều (S4).
13. `OrdersPage`: gom tiếp props `OrderFormDialog` (~25 prop) khi refactor data-layer orders (S5).
14. Palette thô còn lại (thang >4 trạng thái: logistics, hoá đơn KiotViet, row-tint, hành trình/phiếu BH, số ngày mua cuối) — cần
    thiết kế thang token riêng nếu muốn gom (S2); 170 `[..px]` còn lại, `text-[13px]` (S4).
15. Checkbox sticky OrdersTable hàng chẵn không zebra (do `row-tint` quyết `stickyBg`) (S3).

### 5.3 Checklist QA tay trước khi deploy (theo luồng nghiệp vụ)

Chuẩn bị: kiểm trên Chrome desktop 1280/1920, laptop <1536 (root 15px), iPad/Android tablet (cảm ứng), điện thoại 375.

**Đơn hàng (sale — ưu tiên cao nhất)**
- [ ] Danh sách đơn: cuộn ngang → cột Tên khách hàng dính (≥768) khi chưa ghim cột; cột ghim thủ công vẫn ưu tiên; toolbar wrap đủ nút ở 375/768.
- [ ] Tạo/sửa đơn: chọn kênh/nhân viên/kênh KiotViet/kho/nguồn/VAT/kho xuất; gõ tìm sản phẩm theo bảng giá, đổi kênh → tính lại giá;
      tạo mới Khu vực/Phường xã; nút X xoá lựa chọn; Tab/↓/Enter trong menu KHÔNG submit form; chia đơn tự động khoá ô nhân viên.
- [ ] Chi tiết đơn: sửa địa chỉ thô/2 cấp, đổi kênh/sale/kho, Lưu, Quay lại/Trước/Sau khi chưa lưu (hộp xác nhận), Xuất kho;
      ô chọn CRM nhiều lựa chọn (chip dạng badge).
- [ ] Trang đơn: import, KiotViet precheck/log, Kship, tab Tư vấn mở side panel; huỷ/khôi phục đơn KiotViet từ menu (dialog xác nhận).

**CSKH / CRM**
- [ ] Contacts: 375 cột Khách hàng dính chiếm ~½ màn khi cuộn; lọc ngày inline wrap; popover lịch sử liên hệ không vượt màn.
- [ ] Resale: ghi liên hệ / đặt hẹn / đơn resale; bảng nền card, cột Khách hàng dính.
- [ ] KH thân thiết: lọc thời gian/phân khúc/danh mục, sửa ghi chú; khách hàng: header 2 tầng dính khớp thân bảng.
- [ ] Đánh giá Sheet3: đóng panel khi chưa lưu, đóng case (xác nhận).

**Kho / bảo hành**
- [ ] Huỷ phiếu kho/đơn hoàn, gỡ khớp, xoá mã chờ (dialog xác nhận, Escape không đóng Sheet bên dưới).
- [ ] Quét xuất/nhập kho: nhãn kết quả icon + màu token; camera chỉ ở <768; tablet 768–1023 thấy thẻ (xác nhận với kho).
- [ ] Bảo hành: toolbar wrap ở 375, ô trạng thái NativeCellSelect, cuộn ngang cột Khách hàng dính.

**Cài đặt / HR**
- [ ] Phân bổ lead: xoá ngày phân bổ (xác nhận), bảng cuộn trong wrapper ở 375, chọn NV từng dòng (SearchableSelect).
- [ ] Phân quyền: tạo/xoá role, tick quyền/phạm vi/field, Lưu.
- [ ] Cài đặt hệ thống: đổi tab khi có thay đổi (xác nhận); xoá FB App/Google Ads/blocklist/vị trí chấm công.
- [ ] Kênh: gán TKQC (PK đang lưu không bị đổi), gán user Ads; phòng ban: MultiSelect.

**Toàn cục**
- [ ] Thiết bị cảm ứng: nút/ô nhập 40–44px, nút nhỏ trong ô bảng to lên — kiểm bảng không vỡ trên iPad.
- [ ] Laptop <1280: chữ to hơn ~7% (14 → 15px), bảng rộng thêm; không có thanh cuộn ngang toàn trang.
- [ ] Màu cảnh báo hổ phách đậm; nháp phiếu kho màu xám; "Chờ khớp" màu xanh info.
- [ ] Lần tải đầu các trang danh sách hiện skeleton (không còn chữ "Đang tải..." trong bảng) — cập nhật kịch bản QA/e2e nếu chờ chữ này.
- [ ] Popover nhiều cột bị giới hạn `100vw-1rem` trên điện thoại.

### Hotfix sau review (25/09)
- **Hồi quy S4**: khi gom `<select>` ô bảng vào `NativeCellSelect`, 14 `className` bị rút thành chuỗi rỗng → mất `min-w-*`, ô "Mã kênh", "Trạng thái sale"… co còn ~55px ("Face…", "Không nghe m…"). Đã khôi phục đúng `min-w` như master cho cả 14 ô; "Mã kênh" và "Trạng thái sale" nâng 140 → 160px theo yêu cầu.
- Form đơn: ô "Số lượng" từ `col-span-1` (≈40px, chỉ hiện 1 chữ số) → cột cố định 6rem; lưới dòng sản phẩm chuyển sang `grid-cols-[4fr_6rem_2fr_2fr_2fr_auto]`, nhãn không xuống dòng.
- Bài học: ui-check dùng mock user nên cột ở chế độ chỉ đọc (badge) — không bắt được lỗi ô select; cần fixture quyền sửa để phủ.
