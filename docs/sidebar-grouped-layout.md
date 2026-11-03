# Sidebar grouped layout — tái cấu trúc điều hướng

Ngày: 2026-07-06 · Branch: `feat/sidebar-grouped-layout`

## Mục tiêu

Tái cấu trúc sidebar theo hướng phân tầng tần suất sử dụng (tham chiếu mẫu sidebar dạng Nhanh.vn):

- Các trang **báo cáo** (dùng để nhìn nhanh) đưa lên **top-level phẳng** — bấm là điều hướng thẳng, không phải mở accordion.
- Các module còn lại chia thành 2 nhóm có **group header chữ hoa**: "VẬN HÀNH, QUẢN LÝ" và "CÀI ĐẶT, THIẾT LẬP".
- **Không đổi**: nền xanh đậm (`bg-primary`), hành vi accordion inline, route, RBAC, i18n label các trang.
- Áp dụng đồng bộ cả 3 biến thể: desktop mở rộng (accordion), desktop thu gọn (rail + flyout), mobile drawer.

## Cấu trúc mới

```
[DESI logo]
 Báo cáo Sale             ← phẳng (section: dashboard)
 Báo cáo Ads              ← phẳng (section: ads-report)
 Báo cáo khách hàng       ← phẳng (section: customer-report, requires dashboard)

 VẬN HÀNH, QUẢN LÝ
 Bán hàng        ▾   orders, landing-orders, koc-orders, resale-tracking, warranty
 Khách hàng      ▾   customers, loyal-customers
 Kho vận         ▾   shipments, stock, dispatch-history, stock-documents, warehouses
 Sản phẩm        ▾   products, product-categories
 Marketing       ▾   facebook-ads, google-ads

 CÀI ĐẶT, THIẾT LẬP
 Nhân sự         ▾   employees, leave, attendance-history
 Hệ thống        ▾   users, lead-assignment, roles, departments, channels,
                     kiotviet, sync, audit, attendance-locations
```

Module `reports` bị rút toàn bộ section lên phẳng nên không còn xuất hiện dưới dạng nhóm accordion.

## Thiết kế kỹ thuật

### 1. Config layout (`frontend/src/app/modules.ts` hoặc `nav-layout.ts` mới)

Thêm cấu trúc layout **tham chiếu key**, không nhân đôi dữ liệu — `APP_MODULES` vẫn là nguồn sự thật về section, icon, label, quyền:

```ts
export type SidebarZone = { labelKey: string; moduleKeys: string[] };
export const SIDEBAR_PINNED: AppSection[] = ["dashboard", "ads-report", "customer-report"];
export const SIDEBAR_ZONES: SidebarZone[] = [
  { labelKey: "sidebar.zone.ops",      moduleKeys: ["sales", "crm", "inventory", "catalog", "marketing"] },
  { labelKey: "sidebar.zone.settings", moduleKeys: ["hr", "settings"] },
];
```

Ràng buộc (được test bảo vệ):

- Mọi section của mọi module đều reachable: hoặc nằm trong `SIDEBAR_PINNED`, hoặc module của nó thuộc một zone.
- Không key trùng, không key mồ côi (module key trong zone phải tồn tại trong `APP_MODULES`).

### 2. Lọc quyền (giữ nguyên logic hiện tại)

- Item phẳng: lọc theo `requires`/`requiresAny` như `useNavGroups` đang làm (dashboard/ads-report theo key; customer-report `requires: dashboard`).
- Module trong zone: tái dùng `useNavGroups`; zone không còn module hiển thị nào thì ẩn cả header.
- User không có quyền báo cáo nào → block phẳng ẩn hoàn toàn.

### 3. Render

- **`SidebarAccordion` (desktop mở rộng)**: thêm block item phẳng đầu nav (tái dùng `SectionLink`), sau đó với mỗi zone render header (`text-[11px] font-semibold uppercase tracking-wider text-primary-foreground/50 px-2.5 pt-4 pb-1`) + các module accordion như cũ.
- **`SidebarRail` (thu gọn)**: item phẳng thành icon button điều hướng thẳng (có `title` tooltip); giữa các zone chèn divider mảnh (`border-t border-primary-foreground/15`); flyout module giữ nguyên.
- **`MobileNavDrawer`**: cùng cấu trúc với accordion desktop.
- Trạng thái active: item phẳng active dùng đúng style `SectionLink` hiện tại; `activeGroupKey` giữ hành vi auto-mở accordion khi đứng trong section thuộc module.

### 4. i18n

Thêm vào `frontend/src/i18n/messages/sidebar.ts`:

- `sidebar.zone.ops` = "Vận hành, quản lý" / "Operations"
- `sidebar.zone.settings` = "Cài đặt, thiết lập" / "Settings"

Label item phẳng tái dùng key `topnav.tabs.*` sẵn có.

### 5. Test

- Unit test config: mọi section reachable, không trùng/sót/mồ côi key.
- Test render Sidebar: item phẳng hiển thị và điều hướng đúng; header zone ẩn khi cả zone không có quyền; block phẳng ẩn khi không có quyền báo cáo; accordion vẫn auto-mở theo section active.
- Chạy lại baseline FE test (lưu ý các test fail sẵn trên master: OrderFormDialog + 2 mobile-viewport — không phải regression).

## Ngoài phạm vi

- Không đổi màu nền / light theme.
- Không thêm nút "+Tạo đơn" hay badge "LIVE".
- Không đổi flyout thành hover-submenu kiểu mới.
- Không đụng route, permission backend.

## Sprint

| # | Việc | Trạng thái |
|---|------|-----------|
| 1 | Config `SIDEBAR_PINNED` + `SIDEBAR_ZONES` + i18n keys + unit test config | ✅ Xong — `frontend/src/app/nav-layout.ts` (`buildNavModel`) + key i18n `sidebar.zone.ops`/`sidebar.zone.settings`; 9 unit test (`nav-layout.test.ts`) bảo vệ reachable/không trùng/không mồ côi |
| 2 | Render accordion desktop + mobile drawer (block phẳng + zone header) | ✅ Xong — `Sidebar.tsx` thêm `PinnedList`/`ZoneLabel`, dùng chung cho accordion desktop và mobile drawer; 4 test render (`Sidebar.test.tsx`) phủ item phẳng, zone header ẩn/hiện theo quyền, block phẳng ẩn khi thiếu quyền báo cáo |
| 3 | Render rail thu gọn (icon phẳng + divider zone) | ✅ Xong — rail thu gọn thêm icon phẳng pinned + divider giữa các zone; kèm fix truyền `pinned` xuống call-site rail còn thiếu; 2 test bổ sung |
| 4 | Test render + chạy full FE test + QA thị giác | ✅ Xong phần tự động — full suite 39 file / 226 test pass (không có fail mới so với baseline), `typecheck`/`lint` 0 lỗi, `format:check` chỉ còn cảnh báo ở 7 file không liên quan (pre-existing, không đụng vào); QA thị giác (desktop mở rộng/thu gọn/mobile) còn chờ user xem trên môi trường dev trước khi merge |
