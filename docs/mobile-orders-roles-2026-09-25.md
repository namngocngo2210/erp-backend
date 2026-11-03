# Mobile UX — Đơn hàng & Vai trò/Phân quyền — 2026-09-25

Branch: `fix/mobile-orders-roles` (từ master `df42c136`, sau !1493 + !1494).
Nguồn: phản hồi người dùng kèm ảnh chụp mobile (~390px) sau khi deploy audit UI/UX.

## 1. Vấn đề

### M1 — Trang Đơn hàng: phần "khung" ăn hết màn hình
Ở ~390×650, từ trên xuống: TopNav → card Bộ lọc → 7 tab (3 hàng) → toolbar 2 hàng icon (9 nút) → ô tìm kiếm → chip trạng thái → **bảng chỉ còn ~50px** (1 hàng header + nửa dòng) → pagination 3 hàng.
Nguyên nhân: trang dạng `isFullHeight` (`RootLayout.tsx` main `overflow-hidden flex-col`), mọi khối chrome `shrink-0`, chỉ bảng cuộn nội bộ; mobile vẫn render **bảng** 1.600px thay vì thẻ.

### M2 — Trang Vai trò: master–detail xếp chồng
Dưới `lg`, grid 1 cột: header + nút tạo + thẻ danh sách role (search + list) ở trên, chi tiết role ở dưới, cả hai trong 1 vùng cuộn → chi tiết (nội dung chính: quyền, field) bị đẩy xuống dưới màn; tab "Quyền hạn / Ẩn field / Sửa field" bị cắt ngang; nút Lưu nằm trong header chi tiết, cuộn đi là mất.

## 2. Thiết kế

Nguyên tắc mobile (<768, `MOBILE_VIEWPORT_QUERY`) — tablet 768–1023 áp cùng bộ nếu hợp lý:
**nội dung chính chiếm ≥ 70% chiều cao; chrome gọn thành 1 hàng hoặc thu vào sheet; thao tác chính luôn trong tầm ngón cái.**

### 2.1 Đơn hàng (M1)
| Khối | Desktop (giữ nguyên) | Mobile |
|---|---|---|
| Card Bộ lọc | card riêng | gộp vào toolbar: 1 nút "Bộ lọc" (badge số filter đang bật) mở `Sheet` từ dưới chứa toàn bộ filter |
| Tab (Tổng hợp, Landing…, Blocklist) | hàng tab | 1 hàng **cuộn ngang** (`overflow-x-auto`, không wrap, `snap-x`), tab đang chọn tự `scrollIntoView` |
| Toolbar 9 nút | đủ nút | giữ tối đa 2 nút chính (Tạo đơn `+`, Bộ lọc) + ô tìm kiếm cùng hàng; phần còn lại (Gọi, Nhập, Xuất, In, KiotViet, Chuyển, Xoá, Cột, Sắp xếp) vào `DropdownMenu` "⋯" |
| Chip trạng thái | wrap | 1 hàng cuộn ngang |
| Bảng | bảng đầy đủ | **danh sách thẻ** (`OrderMobileCard`): tên KH + SĐT (bấm gọi), mã kênh, trạng thái sale (đổi được nếu có quyền — dùng `NativeCellSelect` full width), tổng tiền, trạng thái vận chuyển, ngày tạo; chạm thẻ = hành vi như click dòng (mở chi tiết/drawer); checkbox chọn nếu đang ở chế độ chọn |
| Cuộn | chỉ bảng cuộn | **cả trang cuộn** trên mobile (bỏ `isFullHeight` cho <768 hoặc cho vùng list `flex-1 overflow-y-auto` chiếm phần còn lại); toolbar sticky top |
| Pagination | 3 hàng | 1 hàng gọn: `‹  1/48  ›` + chọn số dòng trong menu ⋯ |

Không đổi logic dữ liệu, query, quyền; chỉ đổi trình bày. Desktop ≥1024 giữ nguyên pixel.

### 2.2 Vai trò (M2)
- Mobile: **master → detail** kiểu điều hướng: mặc định hiện danh sách role full màn; chọn role → hiện chi tiết full màn với nút "‹ Vai trò" quay lại (đồng bộ `?role=<id>` trên URL để back của trình duyệt/điện thoại hoạt động).
- Header trang gọn: tiêu đề + nút `+` (icon) cùng hàng; mô tả ẩn ở mobile.
- Chi tiết: tab 3 mục cuộn ngang được, không cắt; nội dung panel chiếm phần còn lại.
- **Thanh hành động dính đáy** (sticky bottom, safe-area) chứa "Lưu thay đổi" (+ số thay đổi) và Xoá (vào menu ⋯) — luôn thấy khi cuộn quyền.
- Desktop ≥1024 giữ layout 2 cột hiện tại.

### 2.3 Phủ bằng ui-check
- Fixture user có **quyền sửa** (để OrdersTable/card render select thay vì badge — bài học !1494).
- Thêm kiểm "nội dung chính": tỉ lệ chiều cao vùng nội dung chính (`[data-main-content]`) / viewport ở 375×812 — mục tiêu ≥ 60% cho /sales/orders và /settings/roles.

## 3. Sprint
| Sprint | Nội dung | Trạng thái |
|---|---|---|
| M-S1 | Đơn hàng mobile (2.1) | ✅ |
| M-S2 | Vai trò mobile (2.2) | ✅ |
| M-S3 | ui-check fixture quyền sửa + chỉ số nội dung chính, rà các trang full-height khác cùng pattern (contacts, pancake leads…) ghi lại | ✅ (rà xong, 4 trang < 0,5 ghi "cần làm tiếp") |

## 4. Nhật ký
_(cập nhật sau mỗi sprint)_

### M-S1 — Đơn hàng mobile (✅) + phần fixture M-S3

Commit: `d2ac92ad` (ui-check), `fa159341` (orders mobile).

**ui-check (phần M-S3)**
- `fixtures.mjs`: user mock có `editable_order_fields` (trước là `[]` → mọi ô bị khoá, cột render badge) → ô Mã kênh / Trạng thái sale render **select**; `channel_code` của đơn mẫu khớp danh mục `/api/channels`.
- `run.mjs`: chỉ số **`mainRatio`** = chiều cao hiển thị của `[data-main-content]` trong viewport / chiều cao viewport (mobile 812px), in ra console + bảng riêng trong `report.md`. README cập nhật.
- `data-main-content` gắn vào vùng cuộn bảng (desktop) và danh sách thẻ (mobile) của OrdersTable.

**Đơn hàng — chỉ áp `<768` (`MOBILE_VIEWPORT_QUERY`), ≥768 giữ nguyên**
- Thanh lọc riêng → nút icon "Bộ lọc" (badge đếm đủ điều kiện, cùng `buildOrdersFilterChips`) trong toolbar, mở `OrdersFilterSheet` **side bottom** (thêm variant `bottom` cho `Sheet`: max-h 85dvh, bo góc trên, safe-area). Tái dùng nguyên các nhóm filter.
- Tab: 1 hàng cuộn ngang (`max-md:flex-nowrap overflow-x-auto snap-x`), tab đang chọn `scrollIntoView`.
- Toolbar 1 hàng: `[tìm kiếm][Bộ lọc][+ Tạo đơn][⋯]`. Menu "⋯" (DropdownMenu, nhãn chữ + icon): Chọn tất cả, Quay số, Nhập đơn, Sắp xếp (4 mục, dùng chung `OrdersSortMenuItems` với menu desktop), Xuất Excel, In hóa đơn / phiếu giao, KiotViet (Tạo nhanh, Đẩy đơn, KShip, Hủy đơn, Log — admin), Khôi phục / Chuyển (nếu trang có), Xóa đã chọn. Badge số đơn đang chọn trên nút ⋯.
  Handler Hủy KiotViet / Khôi phục / Chuyển landing tách thành `useCallback` dùng chung desktop + menu (logic giữ nguyên từng dòng), disabled/quyền như nút desktop.
- Chip trạng thái (đơn + sale): 1 hàng cuộn ngang; nút mở rộng chip sale ẩn ở mobile.
- **`OrderMobileCard`** (`OrdersTable/OrderMobileCard.tsx`): checkbox chọn (cùng `toggleRowSelection`), tên, SĐT `tel:`, tổng sau giảm, Mã kênh + Trạng thái sale (select full-width nửa thẻ), trạng thái vận chuyển, ngày tạo. Các ô render qua **đúng `OrderCell`** của bảng → cùng quyền (`canEditField`, `can_edit`), cùng `onPatch`, cùng dialog giờ nhắc Lark. Màu thẻ theo `resolveRowTint` như dòng bảng. Chạm thẻ = `onOpenDetails` (click dòng).
- Cuộn: giữ `isFullHeight`, chrome gọn còn ~29% → danh sách thẻ `flex-1 overflow-y-auto` chiếm phần còn lại.
- Phân trang mobile 1 hàng: `[50▾] 96 giao dịch  ‹ 1/2 ›` (`NativeCellSelect` nhỏ cho số dòng).
- i18n: 6 key `orders.mobile.*` (vi/en).

**Số liệu ui-check** (`/sales/orders`, `m-baseline` → `m-s1`)

| | 375 | 768 | 1024 | 1280 |
|---|---|---|---|---|
| tràn/cắt | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 |
| mainRatio | **0,32 → 0,71** | 0,49 → 0,49 | 0,54 → 0,54 | 0,62 → 0,62 |

Ảnh 768/1024/1280 **giống byte-by-byte** m-baseline (`cmp`). 375: thấy ~4 thẻ đơn/màn (trước: 1 hàng header + nửa dòng bảng).

**Kiểm tra**: tsc, eslint 0 warning, prettier, knip xanh. Test: mới `OrderMobileCard.test.tsx` (5), `orders-table-mobile.test.tsx` (5: thẻ thay bảng, menu ⋯ Tạo nhanh với đơn chọn trên thẻ, disabled khi chưa chọn, 1280 giữ bảng, phân trang `‹ 1/3 ›`), `orders-filters.test.tsx` +2 (compact). Chạy 19 file liên quan (quick-sale-deferred, orders-sort-menu, OpenDialpadButton, field-grant-gate, sale-assignment-gate, ShipmentsPage.patch-error, i18n…) — 140 test xanh.

**Chưa làm**
- "Cột" (tuỳ chỉnh cột) không đưa vào menu ⋯ ở mobile: thẻ không dùng cấu hình cột, chỉ bảng (≥768) mới cần.
- Tablet 768–1023 vẫn layout desktop (bảng, mainRatio 0,49) — ngoài phạm vi yêu cầu "<768"; cân nhắc ở sprint sau (cần quyết định thẻ hay bảng cho tablet).
- Rà các trang full-height khác (M-S3) — chưa.

**Rủi ro / cần QA trên máy thật**
- Chọn nhiều bằng Shift không có trên cảm ứng (checkbox vẫn chọn từng đơn; "Chọn tất cả" trong ⋯).
- Menu ⋯ dài (≈18 mục) — cuộn trong `max-h-70dvh`; kiểm tra trên iOS Safari.
- Dialog giờ nhắc Lark mở từ thẻ (trạng thái Hẹn gọi lại…) — đã chặn nổi bọt, cần thử tay.


### M-S2 — Vai trò mobile (✅)

Commit: `4c17d1e5` (fixture roles + trang `?role=1` trong ui-check), `bcda27c3` (master→detail), `96f97860` (test + ui-check so URL kèm query), `c85877e2` (min-w-0 chi tiết desktop, badge 1 dòng), `9fc61c27` (test sạch lint).

**Áp dụng dưới `lg` (<1024, `TABLE_VIEWPORT_QUERY` false) — ≥1024 giữ 2 cột**
- **URL là nguồn sự thật**: role đang chọn đọc/ghi `?role=<id>` (`useSearchParams`) ở mọi độ rộng. Chọn role = push (kèm `state.roleFromList`) → nút back điện thoại về danh sách. Role bị xoá / id không tồn tại → replace bỏ param. Tạo role mới → push sang chi tiết.
- **Master→detail**: không có `?role` → header gọn + danh sách full chiều cao; có `?role` → chi tiết full (ẩn header trang + danh sách), chưa tải xong roles thì spinner.
- Header gọn: tiêu đề + nút `+` icon (`aria-label="Tạo vai trò"`) cùng hàng, ẩn mô tả.
- `RoleDetailHeader` thêm prop `onBack`: nút "‹ Vai trò" + tên/badge/mô tả gọn; ẩn Xoá/Lưu trong header (tránh trùng). Badge `shrink-0` (trước bị bẻ 2 dòng khi tên dài).
- Tab 3 mục: 1 hàng cuộn ngang (Tabs vốn `overflow-x-auto`, không bị `overflow-hidden` cha cắt nữa); panel `flex-1` cuộn nội bộ.
- **`RoleMobileActionBar`** (mới) dính đáy khung chi tiết, `pb-[max(0.5rem,env(safe-area-inset-bottom))]`: "Lưu thay đổi" full width (disabled khi không dirty/đang lưu) + menu ⋯ "Xóa vai trò" (mở `RoleDeleteDialog` như nút thùng rác desktop; ẩn với role hệ thống). Role hệ thống (read-only) không hiện thanh. Hook không có "số thay đổi" nên không hiện số (dấu "Chưa lưu" vẫn ở header).
- **Dirty guard**: trước đây đổi role khi còn thay đổi thì **mất bản nháp im lặng**. Nay đổi role (mọi độ rộng) và "‹ Vai trò" đều qua `useUnsavedGuard` (ConfirmDialog "Bỏ thay đổi / Ở lại") — nhất quán giữa desktop và mobile.
- `DropdownMenuContent`: tự **lật lên trên** khi trigger sát đáy màn (menu ⋯ thanh đáy) — trước chỉ mở xuống, sẽ ra ngoài viewport.
- `data-main-content`: danh sách role (chế độ list mobile), panel tab (chi tiết) — prop `mainContent`/`mainContentPanel` mới của `RoleListSidebar`/`Tabs`.
- Sửa kèm (desktop): khung chi tiết thiếu `min-w-0` trong grid → mô tả role dài (truncate) đẩy section rộng 1144px, tràn khỏi `<main>` 247px ở 1280. Trước đây không lộ vì trang không đọc `?role`.

**Số liệu ui-check** (`m2-baseline` → `m2`)

| Trang | 375 | 768 | 1280 |
|---|---|---|---|
| `/settings/roles` tràn/cắt | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 (ảnh **giống byte-by-byte**) |
| `/settings/roles?role=1` tràn/cắt | 0/0 → 0/0 | 0/0 → 0/0 | 0/0 → 0/0 (sau fix `min-w-0`; trước fix 1/1) |
| mainRatio list | — → **0,76** | — → 0,76 | — |
| mainRatio chi tiết | — → **0,66** | — → 0,66 | — → 0,69 |

Trước (375): danh sách ~5 role chiếm nửa trên, chi tiết là thẻ "Chọn một vai trò" ở dưới, `?role` bị bỏ qua. Sau: list thấy 10 role/màn; chi tiết thấy 3 nhóm quyền + thanh Lưu đáy.
Ảnh: `frontend/.ui-check/m2-baseline/settings_roles_375.png`, `…/settings_roles_role_1_375.png` → `frontend/.ui-check/m2/settings_roles_375.png`, `…/settings_roles_role_1_375.png`, `…/settings_roles_role_1_1280.png`.
ui-check: cột URL giờ so cả `location.search` (trước `?role=1` luôn bị gắn ↪ nhầm).

**Kiểm tra**: tsc, eslint 0 warning, prettier, knip xanh. Test mới `src/pages/__tests__/RolesPage.test.tsx` (6): list → chọn → chi tiết `?role=2` → "‹ Vai trò" về list; header gọn; mở thẳng `?role=1` + Lưu đáy disabled → toggle quyền → gọi `updateRole(1)`; back khi dirty → hỏi, "Ở lại" giữ chi tiết; menu ⋯ → hộp thoại xoá; desktop 1280 hiện 2 cột + ghi `?role`. Chạy cùng 10 file liên quan (roles, orders-table-mobile, orders-sort-menu, use-unsaved-guard, data-table-shell, contacts-table-layout, PancakeLeadsPage, DashboardPage.tabs…) — 54 test xanh.

**Chưa làm**
- Back **của trình duyệt/điện thoại** khi còn thay đổi chưa lưu không hỏi (bỏ bản nháp như đổi role cũ) — cần `useBlocker` (data router) và điều chỉnh test router; để sprint sau nếu cần.
- Số thay đổi trên nút Lưu: `useRoleEditor` chỉ có `isDirty`, không đếm diff — không thêm logic nghiệp vụ.
- Tablet 768–1023 dùng cùng master→detail (mainRatio 0,76/0,66) — hợp lý vì 2 cột 19rem + chi tiết quá chật ở 768.

**Rủi ro / cần QA trên máy thật**
- iOS Safari: safe-area của thanh Lưu, menu ⋯ lật lên.
- Link cũ `/settings/roles` không đổi; link có `?role=` giờ mở thẳng chi tiết (cả desktop).

### M-S3 — Rà nội dung chính các trang danh sách (✅)

Commit: `d2ac92ad` (fixture quyền sửa + `mainRatio`, cùng M-S1), `108b69c7` (gắn `data-main-content`), `9fc61c27` (fixture `/api/pancake/leads` — trước thiếu nên trang rơi ErrorBoundary trong ui-check).

Chỉ gắn attribute, **không đổi layout**: `DataTableShell` (thân bảng — dùng chung customers/loyal/warranty…), `ContactsTable`, wrapper `ResaleTrackingList`, card bảng Pancake leads, panel Tổng quan Dashboard; `/inventory/shipments` dùng sẵn `OrdersTable`.

`m3-scan` @375 (`frontend/.ui-check/m3-scan/`), tràn/cắt đều 0/0:

| Trang | mainRatio | Ghi chú |
|---|---|---|
| /sales/contacts | **0,23** — cần làm tiếp | ~70% màn là bộ lọc (7 control) + 2 hàng chip wrap (15 chip); bảng 1.560px cuộn ngang, chưa có thẻ mobile → áp pattern M-S1 (sheet lọc, chip 1 hàng, thẻ) |
| /crm/customers | 0,65 | |
| /crm/loyal-customers | 0,61 | |
| /sales/resale-tracking | **0,19** — cần làm tiếp | bộ lọc 4 select + 4 thẻ KPI xếp dọc (~400px) đẩy danh sách xuống; gợi ý KPI 2×2 hoặc cuộn ngang, lọc vào sheet |
| /aftersales/warranty | 0,70 | |
| /inventory/shipments | **0,43** — cần làm tiếp | card bộ lọc 4 trường mở sẵn (~45% màn) trước danh sách thẻ → nút "Bộ lọc" + sheet như Đơn hàng |
| /sales/pancake-leads | 0,50 | sát ngưỡng: card lọc + bảng thường (chưa thẻ mobile) |
| /reports/dashboard | 0,52 | trang báo cáo cuộn cả trang, chỉ số tham khảo |

Các trang contacts/resale/dashboard cuộn cả trang (không `isFullHeight`) nên vẫn thao tác được bằng cuộn; tỉ lệ thấp là ở màn đầu tiên.
