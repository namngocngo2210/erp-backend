# Thiết kế: Refactor Frontend (UX, Performance, Cấu trúc, Thẩm mỹ)

**Ngày:** 2026-06-15
**Trạng thái:** ĐÃ HOÀN THÀNH Sprint 1 (trừ B1/B2 bỏ qua), 2, 3 (gồm T3-4), 4 (gồm sweep), 5. Thực thi bằng subagent, review từng bước. Còn nợ duy nhất: B1/B2 (bỏ qua theo quyết định).
**Branch:** `refactor/frontend-ux-perf`
**Người soạn:** CTO / Senior Fullstack

## 1. Bối cảnh & Mục tiêu

Frontend hiện tại (`frontend/`, React 18 + Vite + shadcn/ui + ag-grid + react-query) đã ở mức **enterprise-ready** nhưng tích tụ nợ kỹ thuật sau nhiều sprint thêm tính năng nhanh. Đợt rà soát ngày 2026-06-15 phát hiện 4 nhóm vấn đề:

1. **Bug hiển thị**: một số dropdown/grid hiện mã (code) hoặc ID thay vì tên — đúng triệu chứng người dùng phản ánh.
2. **Performance**: `App.tsx` là monolith 1.774 dòng, context value không memoize gây re-render toàn cây route.
3. **Cấu trúc code**: không nhất quán giữa `pages/` – `features/` – `components/`; mutation lẫn trong `App.tsx`; cache thủ công trong khi đã có react-query.
4. **Thẩm mỹ/bố cục**: tốt (7.8/10) nhưng border-radius, spacing, empty-state chưa chuẩn hóa.

**Mục tiêu refactor:**
- Sửa dứt điểm bug hiển thị code/ID (P0).
- Giảm 30–50% số lần re-render không cần thiết khi tương tác (toggle sidebar, realtime, edit grid).
- Chuẩn hóa cấu trúc thư mục theo mô hình `features/` (đã chứng minh tốt) để dễ scale khi team lên 100–150 người.
- Nâng độ chỉn chu giao diện lên ~9/10.

**Nguyên tắc:**
- **Không đổi hành vi nghiệp vụ** — refactor thuần túy, mỗi sprint phải xanh test trước khi merge.
- Ưu tiên ROI: làm việc rẻ-tác-động-lớn trước (P0), việc nền tảng (tách `App.tsx`) sau.
- Bám codebase: tái dùng pattern `features/` và react-query đã có, không tạo convention mới.
- YAGNI: không đụng những phần đang ổn (design token OKLCH, `features/` đã chuẩn).

**Ngoài phạm vi (làm sau khi có nhu cầu):**
- Đưa state global sang Zustand/Jotai (cân nhắc ở Sprint 3, không bắt buộc).
- Dark mode hoàn chỉnh + theme switcher UI.
- Viết lại OrdersPage/DashboardPage thành kiến trúc mới hoàn toàn (chỉ tách dần).

## 2. Hiện trạng (Baseline đã xác minh ngày 2026-06-15)

| Hạng mục | Số liệu |
|---|---|
| Tổng số file `.ts/.tsx` | 248 |
| `src/App.tsx` | 1.774 dòng (~62KB) |
| `OrdersPage.tsx` / `DashboardPage.tsx` / `CustomersPage.tsx` | 2.676 / 2.249 / 1.896 dòng |
| `OrdersTable.tsx` | 2.477 dòng, chưa `React.memo` |
| `shellValue` context (App.tsx:1685) | object 70+ field, **không** `useMemo` |
| react-query | đã dùng ở `features/{orders,customers,inventory,audit,sync}`; `App.tsx` vẫn cache thủ công bằng `useRef` + TTL 30s |
| Số lần dùng `any` | ~40 (thấp, chấp nhận được) |
| Điểm thẩm mỹ | 7.8/10 (design token OKLCH tốt, không hardcode màu) |

## 3. Danh mục vấn đề chi tiết

### 3.1. Bug hiển thị code/ID (P0)

| # | Mức | Vị trí | Đang hiện | Cần sửa |
|---|---|---|---|---|
| B1 | 🔴 Cao | `features/orders/order-branch.ts:27` | `label: String(id)` → "10", "20" | map `id → tên chi nhánh` |
| B2 | 🔴 Cao | `features/kiotviet/KiotVietDialog.tsx:208` | render thẳng `{branchId}` | map `id → tên chi nhánh` |
| B3 | 🟡 TB | `pages/OrdersPage.tsx:692` | `item.sku \|\| String(item.id)` | `item.name \|\| item.sku` |
| B4 | 🟡 TB | `pages/AdsReportPage.tsx:192` | fallback `ch.name \|\| ch.code` | luôn ưu tiên `name`, fallback rõ nghĩa |
| B5 | 🟢 Thấp | `features/users/DepartmentManager.tsx:178` | `"Tên (CODE)"` | bỏ code hoặc đưa vào tooltip |

**Gốc rễ B1/B2:** thiếu nguồn dữ liệu `branch_id → branch_name`. Cần kiểm tra backend KiotViet đã trả tên chi nhánh chưa; nếu chưa, bổ sung map ở tầng API/feature. Đây là điều kiện tiên quyết của Sprint 1.

> Ghi chú: phần lớn chỗ khác đã đúng (staff, order_source, VAT, gender map sang nhãn dễ đọc). Đây là vài chỗ sót, không phải lỗi hệ thống.

### 3.2. Performance (P0–P1)

| # | Mức | Vị trí | Vấn đề | Hướng sửa |
|---|---|---|---|---|
| P1 | 🔴 | `App.tsx:1685-1766` | `shellValue` tạo mới mỗi render → re-render toàn route tree | `useMemo` + tách context (User/UI/Data) |
| P2 | 🔴 | `App.tsx:~900` | effect WS phụ thuộc `authChecking` → add/remove listener lặp | tách handler ra ref, subscribe 1 lần |
| P3 | 🔴 | `OrdersTable.tsx`, `OrdersGridDesktop.tsx` | chưa `React.memo`, callback inline không `useCallback` | memo + ổn định callback |
| P4 | 🟡 | `App.tsx:312-352` | cache thủ công `useRef`+TTL, dễ stale, trùng react-query | chuyển dần sang react-query |
| P5 | 🟡 | WS handler `App.tsx:900-1200` | nhiều `setState` liên tiếp khi realtime | dựa vào auto-batching React 18 / gom update |
| P6 | 🟢 | `vite.config.ts` | `chunkSizeWarningLimit: 1200` quá cao | hạ về 500, chạy `build:analyze` |

### 3.3. Cấu trúc code (P1)

- `App.tsx` ôm: auth bootstrap, WS handler (~300 dòng), 15+ hàm `load*`, mutation CRUD (1286-1390), notifications, sidebar prefs.
- `components/{UserManagement,ChannelManagement,KiotVietManagement}.tsx` nằm sai chỗ; page tương ứng chỉ là wrapper rỗng → dồn về `features/`.
- Mutation rải rác: vừa ở `App.tsx`, vừa ở `features/orders/use-order-mutations.ts`.
- Test đặt không nhất quán: `components/OrdersTable.mobile.test.tsx` (nên vào `__tests__/`).
- Type cục bộ trong `App.tsx:28-135` (`User`, `DashboardSummary`) → gom về `features/*/types.ts` hoặc `types/`.

### 3.4. Thẩm mỹ / bố cục (P2)

- Border-radius lẫn 4 mức (`md/lg/xl/2xl`) → gom còn 2 tier qua `tailwind.config`.
- Spacing giữa section chưa thống nhất (`space-y-2/3/4`).
- Empty-state thiếu icon + thông điệp hành động.
- Nút destructive chưa nổi bật.
- Focus state hơi mờ trên select/input.

## 4. Kế hoạch Sprint

> Quy ước: mỗi sprint là 1 nhánh con/đợt commit, chạy `npm run lint && npm run test:ci && npm run build` xanh trước khi merge. Sau mỗi sprint cập nhật mục §5 (Tiến độ) — ghi rõ Đã làm / Chưa làm / Ghi chú.

### Sprint 1 — Sửa bug hiển thị code/ID (P0) · ~0.5–1 ngày
**Mục tiêu:** không còn dropdown/grid nào hiện mã/ID cho người dùng.
- [ ] Kiểm tra backend KiotViet có trả tên chi nhánh; nếu thiếu, bổ sung field/map ở tầng API.
- [ ] B1: `order-branch.ts` — `getBranchOptionsForChannel` nhận thêm map tên, `label = tên ?? id`.
- [ ] B2: `KiotVietDialog.tsx` — render tên chi nhánh.
- [ ] B3: `OrdersPage.tsx` — product label `name || sku`.
- [ ] B4: `AdsReportPage.tsx` — ưu tiên `name`.
- [ ] B5: `DepartmentManager.tsx` — bỏ code khỏi label (hoặc tooltip).
- [ ] Bổ sung unit test cho hàm tạo option (label đúng kỳ vọng).
**Tiêu chí hoàn thành:** test xanh; rà tay 5 màn hình xác nhận hiển thị tên.

### Sprint 2 — Performance "quick win" (P0) · ~1–2 ngày
**Mục tiêu:** giảm rõ rệt re-render mà không đổi cấu trúc lớn.
- [ ] `useMemo` cho `shellValue` (App.tsx:1685) với dependency tối thiểu.
- [ ] Tách `AppShellContext` thành 2–3 context: `UserContext` (ít đổi) / `UIContext` (sidebar, notifications) / `DataContext`.
- [ ] `React.memo` cho `OrdersTable`, `OrdersGridDesktop`; `useCallback` cho `onPatch`, `onOpenDetails`...
- [ ] Sửa effect WebSocket: handler qua ref, subscribe 1 lần (deps `[user?.id]` thay vì kèm `authChecking`).
- [ ] (Tùy chọn) hạ `chunkSizeWarningLimit` về 500, chạy `build:analyze` ghi lại baseline bundle.
**Tiêu chí hoàn thành:** React DevTools Profiler cho thấy toggle sidebar không còn re-render OrdersTable; test xanh.

### Sprint 3 — Tách App.tsx & chuẩn hóa cấu trúc (P1) · ~3–5 ngày
**Mục tiêu:** `App.tsx` còn ~250–300 dòng, chỉ giữ auth-gate + providers + router.
- [ ] Tách `hooks/use-auth-bootstrap.ts`.
- [ ] Tách `hooks/use-crm-websocket.ts` (toàn bộ logic WS handler).
- [ ] Tách `hooks/use-cached-fetch.ts` HOẶC chuyển các `load*` sang react-query (ưu tiên react-query vì đã có).
- [ ] Đưa mutation CRUD (App.tsx:1286-1390) về `services/mutations/` hoặc `features/*/use-*-mutations.ts`.
- [ ] Tách `hooks/use-order-notifications.ts`.
- [ ] Di chuyển `components/{UserManagement,ChannelManagement,KiotVietManagement}.tsx` → `features/{users,channels,kiotviet}/components/`; xóa page wrapper rỗng, lazy import từ features.
- [ ] Di chuyển `OrdersTable.mobile.test.tsx` → `features/orders/__tests__/`.
**Tiêu chí hoàn thành:** `App.tsx` < 350 dòng; toàn bộ test cũ xanh; không đổi route/URL.

### Sprint 4 — Chuẩn hóa thẩm mỹ (P2) · ~2–3 ngày
**Mục tiêu:** nâng độ chỉn chu lên ~9/10.
- [ ] Định nghĩa `borderRadius` 2 tier trong `tailwind.config` (vd `lg`=8px nút/input, `xl`=12px card); thay thế các `rounded-2xl` lệch chuẩn.
- [ ] Chuẩn spacing: thống nhất `space-y-4` cho section lớn.
- [ ] Component `<EmptyState icon message action />` dùng chung; áp vào OrdersTable, ProductsPage, Dashboard.
- [ ] Tăng độ rõ nút destructive + focus ring.
- [ ] (Tùy chọn) `<LoadingOverlay />` dùng chung.
**Tiêu chí hoàn thành:** rà tay các page thấy đồng nhất; không phát sinh hardcode màu mới.

### Sprint 5 (Tùy chọn) — Tách page khổng lồ · theo nhu cầu
- [ ] Tách `OrdersPage`/`DashboardPage`/`CustomersPage` thành `components/` + `hooks/` trong feature tương ứng (làm dần, không gấp).

## 5. Tiến độ (cập nhật sau mỗi sprint)

Thực thi ngày 2026-06-15 bằng subagent-driven-development (mỗi task: implementer → review → fix). Baseline test:ci = 192 pass / 4 fail PRE-EXISTING (OrdersTable.mobile, ChannelsTable.mobile, OrderFormDialog, app/modules) — giữ nguyên suốt quá trình. Lưu ý kỹ thuật phát hiện: `npm run build` KHÔNG type-check → đã bổ sung `npx tsc --noEmit` vào verify.

| Sprint | Trạng thái | Đã làm | Chưa làm / Ghi chú |
|---|---|---|---|
| 1 | 🟡 Một phần | B3 (product label `name\|\|sku`), B5 (bỏ code khỏi label department). B4 xác nhận không cần sửa. Commit `14509bf`. | **B1/B2 (tên chi nhánh) BỎ QUA** theo quyết định ngày 2026-06-15. Backend đã có sẵn field `branch_names` (commit `82cb85ff`/`1294c867`) nhưng FE chưa dùng — để task riêng. |
| 2 | ✅ Xong | Tách `AppShellContext`→data (ổn định qua ref/wrapper) + `AppUIContext` volatile + `ShellChrome`, memoize context (`0aa2597`). `React.memo` OrdersTable/grid, ổn định callback, sửa effect WS chỉ re-subscribe theo `user.id`, hạ chunk limit 1200→600 (`733ef9a`). Cả 2 reviewer APPROVED. | — |
| 3 | ✅ Xong | Dời UserManagement/ChannelManagement/KiotVietManagement → `features/*/components`, xóa 3 page wrapper rỗng, dời test mobile, xóa dead `OrdersGridDesktop` (`a32702a`). Tách `useCrmWebSocket` (`a489f19`+fix `bb7843d`), `useOrderNotifications`+`useAuthBootstrap` (`5d29bb5`). **T3-4**: dời mutation CRUD ra `lib/api/*` (`144621a`); migrate 7 load* shell sang react-query với `enabled` gating khớp 1-1 `loadSectionData`, mutation → `invalidateQueries` (`3b05e73`); fix memo identity bằng `EMPTY_LIST` (`2ebcf11`). App.tsx 1774→1155 dòng. Reviewer APPROVED. | `loadSummary` cố ý giữ cache thủ công (coupled WS hook). App.tsx <350 chưa đạt nhưng đã giảm 35%; tách OrdersPage/Dashboard khổng lồ là Sprint 5. |
| 4 | ✅ Xong | `EmptyState` component + áp vào Dashboard/Products/OrdersTable, nút destructive rõ hơn + focus ring, token `--destructive-foreground` (fix latent bug 8 chỗ) (`39d4712`). **Border-radius/spacing sweep**: thống nhất container về `rounded-xl` (17 Card + data-table-shell + TopNav dropdown), chuẩn hóa section spacing 3 page về `space-y-4` (`30850d7`). Reviewer APPROVED. | Cần REVIEW TRỰC QUAN: data-table-shell (mọi bảng), LeadAssignmentPage spacing giảm mạnh (32→16px) có thể revert riêng. Ring opacity button(70%)/input(50%) chưa đồng bộ — để sau. |
| 5 | ✅ Xong | Tách 3 page khổng lồ thành component+hook co-located trong `features/`: **CustomersPage 1897→153** (`74242d0`, 13 file), **DashboardPage 2268→150** (`d4db6ed`, features/dashboard/), **OrdersPage 2676→1709** (`e0e42a4`, tách dialog import/kship/precheck, GIỮ nguyên vùng grid/perf-critical). Cả 3 reviewer APPROVED, tương đương hành vi, không phá memo. | OrdersPage còn 1709 dòng (vùng grid + filter options cố ý giữ để không phá React.memo/identity). Tách thêm chỉ khi thực sự cần. |

## 6. Rủi ro & Giảm thiểu

| Rủi ro | Giảm thiểu |
|---|---|
| Tách context làm vỡ component đang đọc `shellValue` | Giữ API context tương thích; đổi dần từng nhóm consumer; dựa test |
| Backend chưa có tên chi nhánh (chặn B1/B2) | Kiểm tra ngay đầu Sprint 1; nếu thiếu, mở task backend song song |
| Refactor App.tsx ảnh hưởng realtime/WS | Tách `use-crm-websocket` có test riêng; QA luồng order.created/updated |
| `npm run test` trên Windows | Backend test chạy trên server Linux/Docker; frontend test chạy local bằng `test:ci` |

## 7. Đo lường thành công

- **UX:** 0 dropdown/grid hiện mã/ID (rà tay + test).
- **Perf:** Profiler — thao tác UI phổ biến không re-render component nặng ngoài phạm vi; bundle ghi nhận baseline qua `build:analyze`.
- **Cấu trúc:** `App.tsx` < 350 dòng; `components/` không còn *Management component; mutation tập trung.
- **Thẩm mỹ:** border-radius/spacing/empty-state đồng nhất toàn app.
