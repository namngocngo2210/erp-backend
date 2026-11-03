# Spec — App-Shell kiểu Odoo (Phase 0)

> Spec triển khai cho phần app-shell trong `docs/erp-master-plan.md` (Phase 0).
> Mục tiêu: chuyển frontend từ shell phẳng (21 section trong 1 sidebar) sang mô
> hình 2 tầng kiểu Odoo (App Launcher → Module → Section), URL nested theo module.
>
> - Ngày tạo: 2026-06-11
> - Chuẩn FE: `docs/architecture-standard.md` (feature-slice, ≤400 dòng/file, không `any`)
> - Phụ thuộc: không đụng backend; thuần frontend.

---

## 1. Mục tiêu & Phi mục tiêu

**Mục tiêu**
- App Launcher (lưới icon module) làm điểm điều hướng tổng, mở qua nút ⊞.
- Sidebar **contextual**: chỉ hiển thị section của module đang mở.
- URL **nested** theo module: `/<module>/<section>` (vd `/inventory/warehouses`).
- Một **registry module** (`app/modules.ts`) làm nguồn chân lý duy nhất cho mapping
  module ↔ section ↔ icon ↔ path — thêm module ERP mới = thêm một entry.
- Tương thích ngược: mọi URL phẳng cũ (bookmark, link trong notification/websocket,
  hệ thống ngoài) tự redirect sang URL mới.

**Phi mục tiêu (không làm trong spec này)**
- Không đổi backend, không đổi model quyền (vẫn dùng `allowed_sections`).
- Không thêm module nghiệp vụ mới (inventory/accounting... ở phase sau).
- Không đổi nội dung bên trong từng page.

---

## 2. Quyết định đã chốt
| # | Quyết định |
|---|---|
| A1 | Trang `/` **nhớ module dùng gần nhất** (localStorage); lần đầu → module mặc định theo phòng ban (`defaultSectionForUser` hiện có). |
| A2 | App Launcher có route riêng `/apps`, mở bằng nút ⊞ trên topnav. |
| A3 | Triển khai **big-bang trong 1 PR** + bảng redirect tương thích path cũ. |
| A4 | Quyền **không đổi**: module hiện trong launcher nếu user có ≥1 section được phép trong module đó. |
| A5 | `NAV_GROUPS` (đang nằm trong `Sidebar.tsx`) được **nâng thành registry dùng chung** `app/modules.ts`; Sidebar/MobileNav/AppLauncher cùng đọc từ đó. |

---

## 3. Bản đồ Module (registry)

| Module key | i18n label (tái dùng) | Icon | basePath | Sections (theo thứ tự) |
|---|---|---|---|---|
| `sales` | `sidebar.group.sale` | ShoppingCart | `/sales` | orders, landing-orders, koc-orders |
| `crm` | `sidebar.group.customers` | Users | `/crm` | customers, loyal-customers, resale-tracking |
| `inventory` | `sidebar.group.warehouse` | Warehouse | `/inventory` | stock, stock-documents, returns, warehouses |
| `catalog` | `sidebar.group.catalog` | Package | `/catalog` | products, product-categories |
| `marketing` | `sidebar.group.marketing` | Megaphone | `/marketing` | facebook-ads, google-ads |
| `reports` | `sidebar.group.reports` | BarChart3 | `/reports` | dashboard, ads-report, customer-report |
| `settings` | `sidebar.group.admin` | ShieldCheck | `/settings` | users, departments, channels, kiotviet, cron-jobs, sync, audit |

> Thay đổi so với NAV_GROUPS hiện tại: `kiotviet` chuyển từ marketing → **inventory**
> (đúng bản chất sync tồn/POS). **Cả 3 báo cáo (dashboard/sale, ads-report, customer-report)
> gom chung module `reports`** (`feat/reports-group`, 2026-06-11) — `/reports/dashboard`,
> `/reports/ads-report`, `/reports/customer-report`. (`feat/module-reorg-catalog-settings`, 2026-06-11):
> tách module **Sản phẩm** `catalog` (products + product-categories, `/catalog/...`); chuyển
> **channels** (từ marketing) và **kiotviet** (từ inventory) vào **Cài đặt** (`/settings/...`).
> Icon từng section giữ nguyên như NAV_GROUPS.
> Chỗ cho module ERP mới (inventory đã có, thêm `purchasing`/`accounting`/`hr` sau)
> chỉ là thêm entry — không đụng module cũ.

### 3.1 Hình dạng registry (`frontend/src/app/modules.ts`, MỚI)
```ts
import { type LucideIcon } from "lucide-react";
import { type AppSection } from "@/lib/routes";

export type ModuleSection = {
  key: AppSection;
  labelKey: string;
  icon: LucideIcon;
  /** Section dùng chung quyền của section khác (vd customer-report ⊂ dashboard). */
  requires?: AppSection;
};

export type AppModule = {
  key: string;            // "sales" | "crm" | ...
  labelKey: string;       // tái dùng sidebar.group.*
  icon: LucideIcon;
  basePath: string;       // "/sales"
  sections: ModuleSection[];
};

export const APP_MODULES: AppModule[] = [ /* theo bảng §3 */ ];

// Index dẫn xuất (tính 1 lần):
export const SECTION_TO_MODULE: Record<AppSection, AppModule>;  // section → module
export function moduleByKey(key: string): AppModule | undefined;
export function defaultSectionOfModule(m: AppModule): AppSection; // section đầu tiên
```

`Sidebar.tsx` bỏ `NAV_GROUPS` nội bộ, import `APP_MODULES`. Không trùng nguồn dữ liệu.

---

## 4. URL Scheme & Tương thích ngược

### 4.1 `SECTION_TO_PATH` mới (nested) — `lib/routes.ts`
| Section | Path mới | Path cũ (redirect từ) |
|---|---|---|
| orders | `/sales/orders` | `/orders` |
| landing-orders | `/sales/landing-orders` | `/landing-orders` |
| koc-orders | `/sales/koc-orders` | `/koc-orders` |
| customers | `/crm/customers` | `/customers` |
| loyal-customers | `/crm/loyal-customers` | `/loyal-customers` |
| resale-tracking | `/crm/resale-tracking` | `/resale-tracking` |
| customer-report | `/crm/customer-report` | `/customers/report` |
| products | `/inventory/products` | `/products` |
| product-categories | `/inventory/product-categories` | `/product-categories` |
| warehouses | `/inventory/warehouses` | `/warehouses` |
| kiotviet | `/inventory/kiotviet` | `/kiotviet` |
| facebook-ads | `/marketing/facebook-ads` | `/facebook-ads` |
| google-ads | `/marketing/google-ads` | `/google-ads` |
| channels | `/marketing/channels` | `/channels` |
| ads-report | `/marketing/ads-report` | `/ads-report` |
| dashboard | `/reports/dashboard` | `/dashboard` |
| users | `/settings/users` | `/users` |
| departments | `/settings/departments` | `/departments` |
| cron-jobs | `/settings/cron-jobs` | `/cron-jobs` |
| sync | `/settings/sync` | `/sync` |
| audit | `/settings/audit` | `/audit-logs` |

### 4.2 Chi tiết đơn (giữ hành vi drawer/full-page hiện có)
- `orderDetailPath` → `/sales/orders/:id`
- `landingOrderDetailPath` → `/sales/landing-orders/:id`
- `kocOrderDetailPath` → `/sales/koc-orders/:id`
- `parseOrdersView` regex đổi sang `^/sales/(?:orders|landing-orders|koc-orders)/(\d+)/?$`.
- Redirect cũ phải bao cả dạng có `:id`: `/orders/123` → `/sales/orders/123`.

### 4.3 `sectionFromPath` (viết lại)
Suy section từ path nested bằng cách tra `APP_MODULES` (so khớp `basePath` + section
cuối), thay cho chuỗi `startsWith` thủ công. Vẫn ưu tiên `landing-orders`/`koc-orders`
trước `orders`. Trả `defaultSectionForUser` khi không khớp.

### 4.4 Redirect tương thích (router)
Trong `app/routes.tsx`, mỗi path cũ thêm một route `<Navigate to={...} replace />`
sang path mới (giữ nguyên `:id`, query string). Bảng redirect lấy từ `lib/routes.ts`
(`LEGACY_PATH_REDIRECTS`). **Quan trọng:** notification/websocket deep-link `/orders/:id`
phải tiếp tục mở đúng — đây là lý do redirect là bắt buộc, không chỉ "đẹp".

---

## 5. Thành phần

### 5.1 AppLauncher — `app/AppLauncher.tsx` (MỚI), route `/apps`
- Lưới icon các module mà user có quyền: `APP_MODULES.filter(m => m.sections.some(s =>
  allowedSet.has(requiredPermissionFor(s.requires ?? s.key))))`.
- Click module → `navigate(pathFromSection(defaultSectionOfModule(m)))` + lưu lastModule.
- Có ô tìm app (lọc theo label) — nhẹ, không bắt buộc cho bản đầu.
- Theme light xanh da trời, icon lucide, dùng shadcn card. ≤400 dòng.

### 5.2 RootLayout — sửa
- **Last-module:** khi section đổi (effect đồng bộ từ URL), ghi
  `localStorage["desi.lastModule"] = SECTION_TO_MODULE[section].key`.
- **`/` index:** thay `DefaultRedirect` bằng logic: đọc lastModule → nếu hợp lệ & còn
  quyền, redirect về `defaultSectionOfModule`; nếu không → `defaultSectionForUser(user)`.
- Truyền `activeModule` (suy từ section) xuống Sidebar/TopNav.
- `navigateToSection` giữ nguyên (đã đi qua `pathFromSection`, tự đúng path mới).

### 5.3 Sidebar + MobileNavDrawer — sửa thành contextual
- Bỏ render **mọi** group; chỉ render **section của `activeModule`** (flat list, không
  còn flyout group vì trong 1 module ít mục).
- Header sidebar: hiện tên + icon module hiện tại (thay logo-only), logo về `/apps`.
- Lọc section theo quyền như cũ (`allowedSet.has(item.requires ?? item.key)`).
- Mobile drawer: liệt kê section của module hiện tại + một lối về App Launcher.

### 5.4 TopNav — thêm nút ⊞ App Switcher
- Nút ⊞ (icon `LayoutGrid`) bên trái cạnh nút toggle sidebar → `navigate("/apps")`.
- Hiển thị **tên module hiện tại** (breadcrumb mỏng) cạnh ⊞ để định vị.
- Giữ nguyên notification/profile/lang.

---

## 6. Quyền (không đổi)
- Vẫn dựa `user.allowed_sections` + `requiredPermissionFor`.
- Module ẩn khỏi launcher nếu user không có section nào trong đó.
- Guard route giữ nguyên (`RequireAuth` theo section). Path đổi nhưng section-key không đổi.

---

## 7. i18n
- Label module: tái dùng `sidebar.group.*` (sale/customers/warehouse/marketing/reports/admin).
- Thêm key cho launcher: `launcher.title`, `launcher.search`, `topnav.apps` (tooltip ⊞),
  `topnav.module.<key>` nếu cần tên ngắn. Thêm cả `vi` lẫn `en` trong `i18n/messages`.

---

## 8. Danh sách file đụng tới
| File | Thay đổi |
|---|---|
| `frontend/src/app/modules.ts` | **MỚI** — registry `APP_MODULES` + index dẫn xuất. |
| `frontend/src/app/AppLauncher.tsx` | **MỚI** — trang lưới icon module. |
| `frontend/src/lib/routes.ts` | `SECTION_TO_PATH` nested, `sectionFromPath`/`parseOrdersView` viết lại, detail-path helpers, `LEGACY_PATH_REDIRECTS`, helper module. |
| `frontend/src/app/routes.tsx` | Path route nested, route `/apps`, `/` remember-last, các route redirect cũ→mới. |
| `frontend/src/app/RootLayout.tsx` | Lưu/đọc lastModule, truyền activeModule, sửa index redirect. |
| `frontend/src/components/Sidebar.tsx` | Bỏ `NAV_GROUPS`, đọc `APP_MODULES`, render contextual (Sidebar + MobileNavDrawer). |
| `frontend/src/components/TopNav.tsx` | Nút ⊞ + tên module hiện tại. |
| `frontend/src/i18n/messages/*` | Key launcher/topnav. |
| `frontend/src/**/__tests__` | Test routing + sidebar contextual + redirect. |

---

## 9. Kiểm thử
- **Unit:** `pathFromSection`/`sectionFromPath` khứ hồi cho cả 21 section; `parseOrdersView`
  với path nested; `LEGACY_PATH_REDIRECTS` map đủ 21 path cũ (+ dạng `:id`).
- **Component:** Sidebar chỉ render section của module active; AppLauncher lọc theo quyền;
  ⊞ điều hướng `/apps`.
- **Hồi quy:** deep-link `/orders/123` → `/sales/orders/123` mở đúng chi tiết; quyền theo
  phòng ban (Booking → koc) vẫn đúng; mobile drawer hoạt động.
- `tsc --noEmit` xanh; không thêm `any`/eslint error (DoD trong architecture-standard.md).
- Lưu ý: baseline có sẵn vài test FE đỏ (OrderFormDialog, *.mobile) — không phải hồi quy
  của spec này.

---

## 10. Sprint & Tiến độ
| Bước | Nội dung | Trạng thái |
|---|---|---|
| 1 | `routes.ts` nested `SECTION_TO_PATH` + helpers + `LEGACY_PATH_REDIRECTS` | ✅ Xong (`ac40380`) |
| 2 | `modules.ts` registry (`APP_MODULES` + helpers) | ✅ Xong (`6b78bfd`) |
| 3 | `AppLauncher.tsx` | ✅ Xong (`c1e85ac`) |
| 4 | `routes.tsx`: route nested + `/apps` + redirect cũ→mới + `/` remember-last | ✅ Xong (`69cf0c6`) |
| 5 | Sidebar/MobileNav contextual (536→202 dòng) | ✅ Xong (`668e5d0`) |
| 6 | TopNav ⊞ + RootLayout lastModule + nhãn module | ✅ Xong (`bffa43f`) |
| 7 | i18n (`launcher.title`, `topnav.apps`) + cập nhật test path nested | ✅ Xong (`55db6a6`) |

> **Đã hoàn thành (2026-06-11).** Branch `feat/erp-app-shell`. 169/169 test xanh,
> `tsc --noEmit` sạch. Final review: APPROVE (không có lỗi Critical/Important).
> Phát sinh ngoài plan: 2 file test cũ (`routes.koc.test.ts`, `routes.detail.test.ts`)
> đã được cập nhật path phẳng→nested; comment cũ nhắc `NAV_GROUPS` đã sửa thành `APP_MODULES`.
> **Bổ sung sau (2026-06-11, `29271c4`):** ô tìm app trong launcher (`launcher.search`)
> và tên module rút gọn cho topnav + launcher (`module.*`, field `shortLabelKey` trong
> registry) đã hoàn thành.

---

## 11. Rủi ro
- **R1 — Sót deep-link path cũ** (đặc biệt `/orders/:id` từ notification/websocket): phủ
  bằng test map đầy đủ §9. *Rủi ro cao nhất.*
- **R2 — Quyền lệch khi đổi nhóm** (kiotviet/ads-report/customer-report đổi module): giữ
  nguyên section-key & `requiredPermissionFor`, chỉ đổi vị trí trình bày → không đổi quyền.
- **R3 — File phình >400 dòng:** Sidebar.tsx hiện đã lớn; nhân dịp này tách phần
  contextual-nav ra hàm/component con nếu vượt trần (theo DoD).
