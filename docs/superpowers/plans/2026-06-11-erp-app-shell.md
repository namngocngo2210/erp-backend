# ERP App-Shell (Odoo-style) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chuyển frontend từ shell phẳng (21 section trong 1 sidebar) sang mô hình 2 tầng kiểu Odoo (App Launcher → Module → Section) với URL nested, không đổi backend/quyền.

**Architecture:** Một registry module (`app/modules.ts`) là nguồn chân lý duy nhất ánh xạ module ↔ section ↔ icon. `lib/routes.ts` đổi sang path nested `/<module>/<section>` + bảng redirect tương thích path cũ. Router thêm route `/apps` (App Launcher) và các route redirect cũ→mới. Sidebar/MobileNav trở thành contextual (chỉ hiện section của module đang mở); TopNav thêm nút ⊞ mở launcher; trang `/` nhớ module dùng gần nhất.

**Tech Stack:** React 18 + TypeScript + react-router-dom v6 + Vitest + @testing-library/react + lucide-react + shadcn/ui.

**Spec nguồn:** `docs/erp-app-shell.md`. Chuẩn FE: `docs/architecture-standard.md` (≤400 dòng/file, cấm `any`).

**Lưu ý baseline:** Một số test FE đỏ sẵn trên master (OrderFormDialog, `*.mobile`) — KHÔNG phải hồi quy của plan này.

---

## File Structure

| File | Trách nhiệm |
|---|---|
| `frontend/src/lib/routes.ts` | (Sửa) `SECTION_TO_PATH` nested, `sectionFromPath`/`parseOrdersView` viết lại, detail-path helpers, `LEGACY_PATH_REDIRECTS`. Thuần hàm, không phụ thuộc React/registry. |
| `frontend/src/app/modules.ts` | (MỚI) `APP_MODULES` + `SECTION_TO_MODULE`, `moduleByKey`, `defaultSectionOfModule`, `accessibleModules`. Phụ thuộc `lib/routes` (type-only). |
| `frontend/src/app/routes.tsx` | (Sửa) Route nested, route `/apps`, route redirect cũ→mới, `DefaultRedirect` nhớ module gần nhất. |
| `frontend/src/app/AppLauncher.tsx` | (MỚI) Trang lưới icon module, lọc theo quyền. |
| `frontend/src/components/Sidebar.tsx` | (Sửa) Bỏ `NAV_GROUPS`, đọc `APP_MODULES`, render contextual (Sidebar + MobileNavDrawer). |
| `frontend/src/components/TopNav.tsx` | (Sửa) Nút ⊞ → `/apps` + nhãn module hiện tại. |
| `frontend/src/app/RootLayout.tsx` | (Sửa) Lưu `lastModule` vào localStorage, truyền nhãn module cho TopNav, bỏ effect redirect `/orders` cũ. |
| `frontend/src/i18n/messages/common.ts` | (Sửa) Thêm key `launcher.*`, `topnav.apps`. |
| `frontend/src/lib/routes.test.ts` | (MỚI) Test round-trip path + legacy redirect. |
| `frontend/src/app/__tests__/modules.test.ts` | (MỚI) Test tính toàn vẹn registry. |
| `frontend/src/app/__tests__/AppLauncher.test.tsx` | (MỚI) Test lọc module theo quyền. |

**Mọi lệnh chạy từ `frontend/`.** Commit message tiếng Anh, ngắn (theo CLAUDE.md).

---

## Task 1: Nested URLs trong `lib/routes.ts`

**Files:**
- Modify: `frontend/src/lib/routes.ts`
- Test: `frontend/src/lib/routes.test.ts`

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/lib/routes.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  pathFromSection,
  sectionFromPath,
  parseOrdersView,
  orderDetailPath,
  landingOrderDetailPath,
  kocOrderDetailPath,
  LEGACY_PATH_REDIRECTS,
  type AppSection,
} from "@/lib/routes";

const ALL_SECTIONS: AppSection[] = [
  "orders", "landing-orders", "koc-orders", "customers", "customer-report",
  "loyal-customers", "resale-tracking", "dashboard", "ads-report", "channels",
  "kiotviet", "facebook-ads", "google-ads", "users", "departments", "products",
  "product-categories", "warehouses", "audit", "cron-jobs", "sync",
];

describe("nested section paths", () => {
  it("every section path is /<module>/<section>", () => {
    for (const s of ALL_SECTIONS) {
      expect(pathFromSection(s)).toMatch(/^\/(sales|crm|inventory|marketing|reports|settings)\/[a-z-]+$/);
    }
  });

  it("round-trips pathFromSection -> sectionFromPath for all sections", () => {
    for (const s of ALL_SECTIONS) {
      expect(sectionFromPath(pathFromSection(s))).toBe(s);
    }
  });

  it("maps known nested paths", () => {
    expect(pathFromSection("warehouses")).toBe("/inventory/warehouses");
    expect(pathFromSection("customer-report")).toBe("/crm/customer-report");
    expect(pathFromSection("kiotviet")).toBe("/inventory/kiotviet");
    expect(pathFromSection("ads-report")).toBe("/marketing/ads-report");
  });
});

describe("order detail paths (under /sales)", () => {
  it("builds nested detail paths", () => {
    expect(orderDetailPath(12)).toBe("/sales/orders/12");
    expect(landingOrderDetailPath(12)).toBe("/sales/landing-orders/12");
    expect(kocOrderDetailPath(12)).toBe("/sales/koc-orders/12");
  });

  it("parseOrdersView reads id from nested sales detail path", () => {
    expect(parseOrdersView("/sales/orders/99")).toEqual({ type: "detail", orderId: 99 });
    expect(parseOrdersView("/sales/landing-orders/7")).toEqual({ type: "detail", orderId: 7 });
    expect(parseOrdersView("/sales/orders")).toEqual({ type: "list" });
  });

  it("sectionFromPath keeps section for nested detail paths", () => {
    expect(sectionFromPath("/sales/orders/99")).toBe("orders");
    expect(sectionFromPath("/sales/landing-orders/7")).toBe("landing-orders");
    expect(sectionFromPath("/sales/koc-orders/7")).toBe("koc-orders");
  });
});

describe("legacy redirects", () => {
  it("covers all 21 old flat paths", () => {
    expect(LEGACY_PATH_REDIRECTS).toHaveLength(21);
  });
  it("maps old order detail base correctly", () => {
    const orders = LEGACY_PATH_REDIRECTS.find((r) => r.from === "/orders");
    expect(orders?.to).toBe("/sales/orders");
    const report = LEGACY_PATH_REDIRECTS.find((r) => r.from === "/customers/report");
    expect(report?.to).toBe("/crm/customer-report");
    const audit = LEGACY_PATH_REDIRECTS.find((r) => r.from === "/audit-logs");
    expect(audit?.to).toBe("/settings/audit");
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `npx vitest run src/lib/routes.test.ts`
Expected: FAIL (`LEGACY_PATH_REDIRECTS` chưa tồn tại; path còn phẳng).

- [ ] **Step 3: Sửa `lib/routes.ts`**

Thay khối `SECTION_TO_PATH`, các detail-path helper, `parseOrdersView`, `sectionFromPath`, và thêm `LEGACY_PATH_REDIRECTS`. Giữ nguyên `AppSection`, `OrdersView`, `defaultSectionForUser`, `SECTION_PERMISSION`, `requiredPermissionFor`.

```ts
const SECTION_TO_PATH: Record<AppSection, string> = {
  orders: "/sales/orders",
  "landing-orders": "/sales/landing-orders",
  "koc-orders": "/sales/koc-orders",
  customers: "/crm/customers",
  "loyal-customers": "/crm/loyal-customers",
  "resale-tracking": "/crm/resale-tracking",
  "customer-report": "/crm/customer-report",
  products: "/inventory/products",
  "product-categories": "/inventory/product-categories",
  warehouses: "/inventory/warehouses",
  kiotviet: "/inventory/kiotviet",
  "facebook-ads": "/marketing/facebook-ads",
  "google-ads": "/marketing/google-ads",
  channels: "/marketing/channels",
  "ads-report": "/marketing/ads-report",
  dashboard: "/reports/dashboard",
  users: "/settings/users",
  departments: "/settings/departments",
  "cron-jobs": "/settings/cron-jobs",
  sync: "/settings/sync",
  audit: "/settings/audit",
};

export function pathFromSection(section: AppSection): string {
  return SECTION_TO_PATH[section];
}

export function parseOrdersView(pathname: string): OrdersView {
  const match = pathname.match(/^\/sales\/(?:orders|landing-orders|koc-orders)\/(\d+)\/?$/i);
  if (match) {
    const orderId = parseInt(match[1], 10);
    if (Number.isFinite(orderId) && orderId > 0) {
      return { type: "detail", orderId };
    }
  }
  return { type: "list" };
}

export function orderDetailPath(orderId: number): string {
  return `/sales/orders/${orderId}`;
}
export function landingOrderDetailPath(orderId: number): string {
  return `/sales/landing-orders/${orderId}`;
}
export function kocOrderDetailPath(orderId: number): string {
  return `/sales/koc-orders/${orderId}`;
}

export function sectionFromPath(pathname: string): AppSection {
  const normalized = (pathname.toLowerCase().replace(/\/+$/, "") || "/");
  // Detail routes giữ section gốc: /sales/orders/123, /sales/landing-orders/7...
  if (/^\/sales\/landing-orders(\/|$)/.test(normalized)) return "landing-orders";
  if (/^\/sales\/koc-orders(\/|$)/.test(normalized)) return "koc-orders";
  if (/^\/sales\/orders(\/|$)/.test(normalized)) return "orders";
  const match = Object.entries(SECTION_TO_PATH).find(([, path]) => path === normalized);
  if (match) return match[0] as AppSection;
  return "orders";
}

/** Path phẳng cũ → path nested mới (redirect tương thích bookmark / deep-link). */
export const LEGACY_PATH_REDIRECTS: Array<{ from: string; to: string }> = [
  { from: "/orders", to: "/sales/orders" },
  { from: "/landing-orders", to: "/sales/landing-orders" },
  { from: "/koc-orders", to: "/sales/koc-orders" },
  { from: "/customers", to: "/crm/customers" },
  { from: "/customers/report", to: "/crm/customer-report" },
  { from: "/loyal-customers", to: "/crm/loyal-customers" },
  { from: "/resale-tracking", to: "/crm/resale-tracking" },
  { from: "/products", to: "/inventory/products" },
  { from: "/product-categories", to: "/inventory/product-categories" },
  { from: "/warehouses", to: "/inventory/warehouses" },
  { from: "/kiotviet", to: "/inventory/kiotviet" },
  { from: "/facebook-ads", to: "/marketing/facebook-ads" },
  { from: "/google-ads", to: "/marketing/google-ads" },
  { from: "/channels", to: "/marketing/channels" },
  { from: "/ads-report", to: "/marketing/ads-report" },
  { from: "/dashboard", to: "/reports/dashboard" },
  { from: "/users", to: "/settings/users" },
  { from: "/departments", to: "/settings/departments" },
  { from: "/cron-jobs", to: "/settings/cron-jobs" },
  { from: "/sync", to: "/settings/sync" },
  { from: "/audit-logs", to: "/settings/audit" },
];
```

> Lưu ý: `sectionFromPath` không còn map `"/"` (route `/` do `DefaultRedirect` xử lý).

- [ ] **Step 4: Chạy test để xác nhận xanh**

Run: `npx vitest run src/lib/routes.test.ts`
Expected: PASS toàn bộ.

- [ ] **Step 5: Commit**

```bash
git add src/lib/routes.ts src/lib/routes.test.ts
git commit -m "feat(routes): nested module paths and legacy redirect map"
```

---

## Task 2: Module registry `app/modules.ts`

**Files:**
- Create: `frontend/src/app/modules.ts`
- Test: `frontend/src/app/__tests__/modules.test.ts`

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/app/__tests__/modules.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  APP_MODULES,
  SECTION_TO_MODULE,
  moduleByKey,
  defaultSectionOfModule,
  accessibleModules,
} from "@/app/modules";

const ALL_SECTIONS = [
  "orders", "landing-orders", "koc-orders", "customers", "customer-report",
  "loyal-customers", "resale-tracking", "dashboard", "ads-report", "channels",
  "kiotviet", "facebook-ads", "google-ads", "users", "departments", "products",
  "product-categories", "warehouses", "audit", "cron-jobs", "sync",
];

describe("APP_MODULES integrity", () => {
  it("has the 6 expected modules", () => {
    expect(APP_MODULES.map((m) => m.key)).toEqual([
      "sales", "crm", "inventory", "marketing", "reports", "settings",
    ]);
  });

  it("each section belongs to exactly one module", () => {
    const seen = APP_MODULES.flatMap((m) => m.sections.map((s) => s.key));
    expect(seen.sort()).toEqual([...ALL_SECTIONS].sort());
    expect(new Set(seen).size).toBe(seen.length); // không trùng
  });

  it("basePath khớp tiền tố path của section đầu tiên", () => {
    for (const m of APP_MODULES) {
      expect(m.sections.length).toBeGreaterThan(0);
    }
  });
});

describe("derived helpers", () => {
  it("SECTION_TO_MODULE map mọi section về đúng module", () => {
    expect(SECTION_TO_MODULE["warehouses"].key).toBe("inventory");
    expect(SECTION_TO_MODULE["kiotviet"].key).toBe("inventory");
    expect(SECTION_TO_MODULE["ads-report"].key).toBe("marketing");
    expect(SECTION_TO_MODULE["customer-report"].key).toBe("crm");
  });

  it("moduleByKey / defaultSectionOfModule", () => {
    const sales = moduleByKey("sales");
    expect(sales).toBeDefined();
    expect(defaultSectionOfModule(sales!)).toBe("orders");
    expect(moduleByKey("nope")).toBeUndefined();
  });

  it("accessibleModules lọc theo allowed_sections (kèm requires)", () => {
    const mods = accessibleModules(["warehouses"]);
    expect(mods.map((m) => m.key)).toEqual(["inventory"]);
    // customer-report yêu cầu quyền 'dashboard'
    const crm = accessibleModules(["dashboard"]).map((m) => m.key);
    expect(crm).toContain("crm");
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `npx vitest run src/app/__tests__/modules.test.ts`
Expected: FAIL (`@/app/modules` chưa tồn tại).

- [ ] **Step 3: Tạo `app/modules.ts`**

```ts
import {
  BadgeDollarSign, BarChart3, Building2, Contact, Database, FileText, FolderTree,
  Medal, Megaphone, Package, PhoneCall, RadioTower, RefreshCw, ShieldCheck,
  ShoppingBag, ShoppingCart, Sparkles, Store, Timer, Users, UsersRound, Warehouse,
  type LucideIcon,
} from "lucide-react";

import { type AppSection } from "@/lib/routes";

export type ModuleSection = {
  key: AppSection;
  labelKey: string;
  icon: LucideIcon;
  /** Section dùng chung quyền của section khác (mirror SECTION_PERMISSION). */
  requires?: AppSection;
};

export type AppModule = {
  key: string;
  labelKey: string;
  icon: LucideIcon;
  basePath: string;
  sections: ModuleSection[];
};

export const APP_MODULES: AppModule[] = [
  {
    key: "sales", labelKey: "sidebar.group.sale", icon: ShoppingCart, basePath: "/sales",
    sections: [
      { key: "orders", labelKey: "topnav.tabs.orders", icon: ShoppingBag },
      { key: "landing-orders", labelKey: "topnav.tabs.landingOrders", icon: RadioTower },
      { key: "koc-orders", labelKey: "topnav.tabs.kocOrders", icon: Sparkles },
    ],
  },
  {
    key: "crm", labelKey: "sidebar.group.customers", icon: Users, basePath: "/crm",
    sections: [
      { key: "customers", labelKey: "topnav.tabs.customers", icon: Contact },
      { key: "loyal-customers", labelKey: "topnav.tabs.loyalCustomers", icon: Medal },
      { key: "resale-tracking", labelKey: "topnav.tabs.resaleTracking", icon: PhoneCall },
      { key: "customer-report", labelKey: "topnav.tabs.customerReport", icon: Users, requires: "dashboard" },
    ],
  },
  {
    key: "inventory", labelKey: "sidebar.group.warehouse", icon: Package, basePath: "/inventory",
    sections: [
      { key: "products", labelKey: "topnav.tabs.products", icon: Package },
      { key: "product-categories", labelKey: "topnav.tabs.productCategories", icon: FolderTree },
      { key: "warehouses", labelKey: "topnav.tabs.warehouses", icon: Warehouse },
      { key: "kiotviet", labelKey: "topnav.tabs.kiotviet", icon: Database },
    ],
  },
  {
    key: "marketing", labelKey: "sidebar.group.marketing", icon: Megaphone, basePath: "/marketing",
    sections: [
      { key: "facebook-ads", labelKey: "topnav.tabs.facebookAds", icon: Megaphone },
      { key: "google-ads", labelKey: "topnav.tabs.googleAds", icon: BadgeDollarSign },
      { key: "channels", labelKey: "topnav.tabs.channels", icon: Store },
      { key: "ads-report", labelKey: "topnav.tabs.adsReport", icon: Megaphone },
    ],
  },
  {
    key: "reports", labelKey: "sidebar.group.reports", icon: BarChart3, basePath: "/reports",
    sections: [
      { key: "dashboard", labelKey: "topnav.tabs.dashboard", icon: BarChart3 },
    ],
  },
  {
    key: "settings", labelKey: "sidebar.group.admin", icon: ShieldCheck, basePath: "/settings",
    sections: [
      { key: "users", labelKey: "topnav.tabs.users", icon: UsersRound },
      { key: "departments", labelKey: "topnav.tabs.departments", icon: Building2, requires: "users" },
      { key: "cron-jobs", labelKey: "topnav.tabs.cronJobs", icon: Timer },
      { key: "sync", labelKey: "topnav.tabs.sync", icon: RefreshCw },
      { key: "audit", labelKey: "topnav.tabs.audit", icon: FileText },
    ],
  },
];

export const SECTION_TO_MODULE: Record<AppSection, AppModule> = Object.fromEntries(
  APP_MODULES.flatMap((m) => m.sections.map((s) => [s.key, m] as const)),
) as Record<AppSection, AppModule>;

export function moduleByKey(key: string): AppModule | undefined {
  return APP_MODULES.find((m) => m.key === key);
}

export function defaultSectionOfModule(m: AppModule): AppSection {
  return m.sections[0].key;
}

/** Module user được phép thấy: có ≥1 section mà allowed_sections cho phép. */
export function accessibleModules(allowedSections: string[]): AppModule[] {
  const allowed = new Set(allowedSections);
  return APP_MODULES.filter((m) =>
    m.sections.some((s) => allowed.has(s.requires ?? s.key)),
  );
}
```

- [ ] **Step 4: Chạy test để xác nhận xanh**

Run: `npx vitest run src/app/__tests__/modules.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/modules.ts src/app/__tests__/modules.test.ts
git commit -m "feat(shell): add app module registry"
```

---

## Task 3: Router — nested routes, `/apps`, redirect cũ→mới, nhớ module

**Files:**
- Modify: `frontend/src/app/routes.tsx`

> Không TDD ở đây (cấu hình route); kiểm chứng qua `tsc` + chạy app ở Task 7. Mỗi
> route nghiệp vụ giữ nguyên element/Guard, chỉ đổi `path` sang dạng nested và bổ
> sung route redirect.

- [ ] **Step 1: Thêm import + 2 helper redirect ở đầu file (sau import hiện có)**

```tsx
import { Navigate, createBrowserRouter, useLocation, useParams } from "react-router-dom";
import { LEGACY_PATH_REDIRECTS } from "@/lib/routes";
import { moduleByKey, defaultSectionOfModule } from "@/app/modules";
import AppLauncher from "@/app/AppLauncher";

/** Redirect path tĩnh cũ → path mới, giữ query/hash. */
function RedirectTo({ to }: { to: string }) {
  const { search, hash } = useLocation();
  return <Navigate to={{ pathname: to, search, hash }} replace />;
}

/** Redirect path chi tiết đơn cũ (/orders/:id) → base mới (/sales/orders/:id). */
function LegacyDetailRedirect({ base }: { base: string }) {
  const { orderId } = useParams();
  const { search, hash } = useLocation();
  return <Navigate to={{ pathname: `${base}/${orderId}`, search, hash }} replace />;
}
```

- [ ] **Step 2: Đổi `DefaultRedirect` sang nhớ module gần nhất**

Thay hàm `DefaultRedirect` hiện có bằng:

```tsx
function readLastModuleKey(): string | null {
  try {
    return window.localStorage.getItem("desi.lastModule");
  } catch {
    return null;
  }
}

/** "/" → module dùng gần nhất (nếu còn quyền), nếu không → mặc định theo phòng ban. */
function DefaultRedirect() {
  const { user } = useAppShell();
  const stored = readLastModuleKey();
  const m = stored ? moduleByKey(stored) : undefined;
  const allowed = user.allowed_sections;
  const canUse =
    m && (!allowed || m.sections.some((s) => allowed.includes(s.requires ?? s.key)));
  const target = canUse ? defaultSectionOfModule(m) : defaultSectionForUser(user);
  return <Navigate to={pathFromSection(target)} replace />;
}
```

- [ ] **Step 3: Đổi `path` các route con sang nested + thêm `/apps` + route redirect**

Trong `createBrowserRouter`, cập nhật cây route. Route `/apps` đặt **top-level** (ngoài
`RootLayout`) để launcher full-screen; các route nghiệp vụ đổi path nested; thêm cụm
redirect ở cuối children của `RootLayout`. Element/Guard giữ nguyên.

```tsx
export const router = createBrowserRouter([
  // App Launcher full-screen (đã sau auth vì App.tsx chỉ render router khi đăng nhập).
  { path: "/apps", element: <AppLauncher /> },
  {
    path: "/",
    element: <RootLayout />,
    children: [
      { index: true, element: <DefaultRedirect /> },

      { path: "sales/orders", element: <OrdersRoute /> },
      { path: "sales/orders/:orderId", element: <OrderDetailRoute /> },
      {
        path: "sales/landing-orders",
        element: <Guard section="landing-orders"><LandingOrdersRoute /></Guard>,
        children: [{ path: ":orderId", element: <LandingOrderDetailRoute /> }],
      },
      { path: "sales/koc-orders", element: <Guard section="koc-orders"><KocOrdersRoute /></Guard> },
      { path: "sales/koc-orders/:orderId", element: <Guard section="koc-orders"><OrderDetailRoute /></Guard> },

      { path: "crm/customers", element: <Guard section="customers"><CustomersRoute /></Guard> },
      { path: "crm/customer-report", element: <Guard section="customer-report"><CustomerReportRoute /></Guard> },
      { path: "crm/loyal-customers", element: <Guard section="loyal-customers"><LoyalCustomersRoute /></Guard> },
      { path: "crm/resale-tracking", element: <Guard section="resale-tracking"><ResaleTrackingRoute /></Guard> },

      { path: "inventory/products", element: <Guard section="products"><ProductsRoute /></Guard> },
      { path: "inventory/product-categories", element: <Guard section="product-categories"><ProductCategoriesRoute /></Guard> },
      { path: "inventory/warehouses", element: <Guard section="warehouses"><WarehousesRoute /></Guard> },
      { path: "inventory/kiotviet", element: <Guard section="kiotviet"><KiotVietRoute /></Guard> },

      { path: "marketing/facebook-ads", element: <Guard section="facebook-ads"><FacebookAdsRoute /></Guard> },
      { path: "marketing/google-ads", element: <Guard section="google-ads"><GoogleAdsRoute /></Guard> },
      { path: "marketing/channels", element: <Guard section="channels"><ChannelsRoute /></Guard> },
      { path: "marketing/ads-report", element: <Guard section="ads-report"><AdsReportRoute /></Guard> },

      { path: "reports/dashboard", element: <Guard section="dashboard"><DashboardRoute /></Guard> },

      { path: "settings/users", element: <Guard section="users"><UsersRoute /></Guard> },
      { path: "settings/departments", element: <Guard section="departments"><DepartmentsRoute /></Guard> },
      { path: "settings/cron-jobs", element: <Guard section="cron-jobs"><CronJobsRoute /></Guard> },
      { path: "settings/sync", element: <Guard section="sync"><SyncRoute /></Guard> },
      { path: "settings/audit", element: <Guard section="audit"><AuditLogsRoute /></Guard> },

      // Redirect tương thích: chi tiết đơn cũ (giữ deep-link notification/websocket).
      { path: "orders/:orderId", element: <LegacyDetailRedirect base="/sales/orders" /> },
      { path: "landing-orders/:orderId", element: <LegacyDetailRedirect base="/sales/landing-orders" /> },
      { path: "koc-orders/:orderId", element: <LegacyDetailRedirect base="/sales/koc-orders" /> },

      // Redirect tương thích: path phẳng cũ → nested.
      ...LEGACY_PATH_REDIRECTS.map((r) => ({
        path: r.from.replace(/^\//, ""),
        element: <RedirectTo to={r.to} />,
      })),

      { path: "*", element: <DefaultRedirect /> },
    ],
  },
]);
```

> Cập nhật phần comment đầu file `routes.tsx` cho khớp (path nested, `/apps`, redirect).

- [ ] **Step 4: Kiểm tra biên dịch**

Run: `npx tsc --noEmit`
Expected: Không lỗi liên quan `routes.tsx` (AppLauncher tạo ở Task 4 — nếu chạy Task 3 trước Task 4, tạm import sẽ lỗi; thực thi Task 4 ngay sau rồi mới `tsc`). Nếu làm tuần tự, hoãn `tsc` tới sau Task 4.

- [ ] **Step 5: Commit**

```bash
git add src/app/routes.tsx
git commit -m "feat(shell): nested module routes, /apps launcher route, legacy redirects"
```

---

## Task 4: Trang `AppLauncher`

**Files:**
- Create: `frontend/src/app/AppLauncher.tsx`
- Test: `frontend/src/app/__tests__/AppLauncher.test.tsx`

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/app/__tests__/AppLauncher.test.tsx`:

```tsx
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

vi.mock("@/i18n", () => ({
  useI18n: () => ({ t: (k: string) => k, language: "vi", setLanguage: () => {} }),
}));

vi.mock("@/app/AppShellContext", () => ({
  useAppShell: () => ({ allowedSections: ["warehouses", "products"] }),
}));

import AppLauncher from "@/app/AppLauncher";

afterEach(cleanup);

function renderLauncher() {
  const router = createMemoryRouter([{ path: "/apps", element: <AppLauncher /> }], {
    initialEntries: ["/apps"],
  });
  render(<RouterProvider router={router} />);
}

describe("AppLauncher", () => {
  it("chỉ hiện module mà user có quyền", () => {
    renderLauncher();
    // user có quyền warehouses/products → chỉ module inventory hiển thị.
    expect(screen.getByText("sidebar.group.warehouse")).toBeInTheDocument();
    expect(screen.queryByText("sidebar.group.sale")).not.toBeInTheDocument();
    expect(screen.queryByText("sidebar.group.admin")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận đỏ**

Run: `npx vitest run src/app/__tests__/AppLauncher.test.tsx`
Expected: FAIL (`@/app/AppLauncher` chưa tồn tại).

- [ ] **Step 3: Tạo `app/AppLauncher.tsx`**

```tsx
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import { useI18n } from "@/i18n";
import { useAppShell } from "@/app/AppShellContext";
import { accessibleModules, defaultSectionOfModule } from "@/app/modules";
import { pathFromSection } from "@/lib/routes";

export default function AppLauncher() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { allowedSections } = useAppShell();
  const modules = useMemo(() => accessibleModules(allowedSections), [allowedSections]);

  return (
    <div className="min-h-[100dvh] bg-background">
      <div className="mx-auto w-full max-w-4xl px-6 py-10">
        <div className="mb-8 flex items-center gap-3">
          <img
            src="/logo.jpg"
            alt="DESI logo"
            className="h-10 w-10 rounded-lg border border-border bg-white object-contain p-0.5"
          />
          <h1 className="display-face text-2xl font-semibold tracking-tight text-foreground">
            {t("launcher.title")}
          </h1>
        </div>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {modules.map((m) => {
            const Icon = m.icon;
            return (
              <button
                key={m.key}
                type="button"
                onClick={() => navigate(pathFromSection(defaultSectionOfModule(m)))}
                className="flex flex-col items-center gap-3 rounded-xl border border-border bg-white p-6 text-center shadow-sm transition-colors hover:border-primary hover:bg-primary/5"
              >
                <span className="grid h-14 w-14 place-items-center rounded-2xl bg-primary/10 text-primary">
                  <Icon className="h-7 w-7" />
                </span>
                <span className="text-sm font-medium text-foreground">{t(m.labelKey)}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Chạy test để xác nhận xanh**

Run: `npx vitest run src/app/__tests__/AppLauncher.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/AppLauncher.tsx src/app/__tests__/AppLauncher.test.tsx
git commit -m "feat(shell): add Odoo-style app launcher page"
```

---

## Task 5: Sidebar + MobileNavDrawer contextual

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`

> Đổi từ "tất cả group + flyout" sang "chỉ section của module hiện tại". Bỏ
> `NAV_GROUPS` nội bộ, dùng `SECTION_TO_MODULE` từ registry. Giữ props hiện có để
> không phải sửa RootLayout call-site (`active`, `allowedSections`, `collapsed`,
> `onChange`). Logic flyout/portal được loại bỏ → file ngắn lại đáng kể (hợp DoD R3).

- [ ] **Step 1: Thay phần import + bỏ `NAV_GROUPS`/`NavGroup`/`NavItem`**

Đầu file, thay khối import lucide + `NAV_GROUPS` bằng:

```tsx
import { useMemo } from "react";
import { createPortal } from "react-dom";
import { LayoutGrid, X, type LucideIcon } from "lucide-react";

import { useI18n } from "@/i18n";
import { pathFromSection, type AppSection } from "@/lib/routes";
import { SECTION_TO_MODULE, type ModuleSection } from "@/app/modules";
```

Giữ helper `shouldHandleNavClick` và 2 type `Props`, `MobileNavDrawerProps` như cũ.

- [ ] **Step 2: Thêm hook lọc section của module hiện tại (đặt trên `export function Sidebar`)**

```tsx
/** Section (đã lọc quyền) của module chứa `active`. */
function useModuleSections(active: AppSection, allowedSections: string[]): {
  moduleLabelKey: string;
  ModuleIcon: LucideIcon;
  items: ModuleSection[];
} {
  const allowedSet = useMemo(() => new Set(allowedSections), [allowedSections]);
  const mod = SECTION_TO_MODULE[active];
  const items = useMemo(
    () => mod.sections.filter((s) => allowedSet.has(s.requires ?? s.key)),
    [mod, allowedSet],
  );
  return { moduleLabelKey: mod.labelKey, ModuleIcon: mod.icon, items };
}
```

- [ ] **Step 3: Viết lại thân `Sidebar` (desktop)**

```tsx
export function Sidebar({ active, allowedSections, collapsed, onChange }: Props) {
  const { t } = useI18n();
  const { moduleLabelKey, ModuleIcon, items } = useModuleSections(active, allowedSections);
  const sidebarWidth = collapsed ? "w-16" : "w-56";

  return (
    <aside
      className={`sticky top-0 z-30 hidden h-[100dvh] shrink-0 flex-col border-r border-primary-foreground/20 bg-primary text-primary-foreground transition-[width] duration-200 lg:flex ${sidebarWidth}`}
    >
      <div
        className={`flex h-14 items-center border-b border-primary-foreground/20 ${
          collapsed ? "justify-center px-2" : "gap-2 px-3"
        }`}
      >
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-primary-foreground/15">
          <ModuleIcon className="h-5 w-5" />
        </span>
        {!collapsed && (
          <p className="display-face truncate text-base font-semibold tracking-tight text-primary-foreground">
            {t(moduleLabelKey)}
          </p>
        )}
      </div>

      <nav className={`flex-1 overflow-y-auto overflow-x-hidden ${collapsed ? "px-1.5 py-2" : "px-2 py-2"}`}>
        <ul className="space-y-1">
          {items.map((item) => {
            const ItemIcon = item.icon;
            const isActive = active === item.key;
            return (
              <li key={item.key}>
                <a
                  href={pathFromSection(item.key)}
                  onClick={(event) => {
                    if (!shouldHandleNavClick(event)) return;
                    event.preventDefault();
                    onChange(item.key);
                  }}
                  className={`flex w-full items-center rounded-lg text-left transition-colors ${
                    collapsed ? "justify-center px-2 py-2" : "gap-2.5 px-2.5 py-2"
                  } ${
                    isActive
                      ? "bg-primary-foreground/15 text-white"
                      : "text-primary-foreground/85 hover:bg-primary-foreground/10 hover:text-white"
                  }`}
                  title={collapsed ? t(item.labelKey) : undefined}
                >
                  <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-md ${isActive ? "bg-white/15" : ""}`}>
                    <ItemIcon className="h-[18px] w-[18px]" />
                  </span>
                  {!collapsed && <span className="flex-1 truncate text-sm font-medium">{t(item.labelKey)}</span>}
                </a>
              </li>
            );
          })}
        </ul>
      </nav>
    </aside>
  );
}
```

- [ ] **Step 4: Viết lại `MobileNavDrawer` (chỉ module hiện tại + lối về launcher)**

```tsx
export function MobileNavDrawer({ active, allowedSections, open, onChange, onClose }: MobileNavDrawerProps) {
  const { t } = useI18n();
  const { moduleLabelKey, ModuleIcon, items } = useModuleSections(active, allowedSections);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true">
      <button
        type="button"
        aria-label={t("common.close", "Đóng")}
        className="absolute inset-0 bg-black/50"
        onClick={onClose}
      />
      <div className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col bg-primary text-primary-foreground shadow-2xl">
        <div className="flex h-14 items-center justify-between border-b border-primary-foreground/20 px-3">
          <div className="flex items-center gap-2">
            <span className="grid h-9 w-9 place-items-center rounded-lg bg-primary-foreground/15">
              <ModuleIcon className="h-5 w-5" />
            </span>
            <p className="display-face truncate text-base font-semibold">{t(moduleLabelKey)}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("common.close", "Đóng")}
            className="grid h-9 w-9 place-items-center rounded-md text-primary-foreground/80 hover:bg-primary-foreground/10 hover:text-white"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 py-2">
          <ul className="space-y-1">
            {items.map((item) => {
              const ItemIcon = item.icon;
              const isActive = active === item.key;
              return (
                <li key={item.key}>
                  <a
                    href={pathFromSection(item.key)}
                    onClick={(event) => {
                      if (!shouldHandleNavClick(event)) return;
                      event.preventDefault();
                      onChange(item.key);
                      onClose();
                    }}
                    className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors ${
                      isActive ? "bg-white/15 font-medium text-white" : "text-primary-foreground/80 hover:bg-primary-foreground/10 hover:text-white"
                    }`}
                  >
                    <span className={`grid h-7 w-7 shrink-0 place-items-center rounded-md ${isActive ? "bg-white/20" : "bg-primary-foreground/10"}`}>
                      <ItemIcon className="h-4 w-4" />
                    </span>
                    <span className="flex-1 truncate">{t(item.labelKey)}</span>
                  </a>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="border-t border-primary-foreground/20 p-2">
          <a
            href="/apps"
            onClick={(event) => {
              if (!shouldHandleNavClick(event)) return;
              onClose();
            }}
            className="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-primary-foreground/85 hover:bg-primary-foreground/10 hover:text-white"
          >
            <LayoutGrid className="h-4 w-4" />
            <span>{t("topnav.apps")}</span>
          </a>
        </div>
      </div>
    </div>,
    document.body,
  );
}
```

- [ ] **Step 5: Kiểm tra biên dịch + commit**

Run: `npx tsc --noEmit`
Expected: Không lỗi ở `Sidebar.tsx`.

```bash
git add src/components/Sidebar.tsx
git commit -m "feat(shell): make sidebar and mobile nav module-contextual"
```

---

## Task 6: TopNav nút ⊞ + RootLayout nhớ module

**Files:**
- Modify: `frontend/src/components/TopNav.tsx`
- Modify: `frontend/src/app/RootLayout.tsx`

- [ ] **Step 1: TopNav — thêm prop + nút ⊞**

Trong `TopNav.tsx`: thêm import `useNavigate`, `LayoutGrid`; thêm 2 prop; render nút ⊞
và nhãn module bên trái (cạnh nút toggle sidebar).

Thêm vào `type Props`:
```tsx
  moduleLabel?: string;
  onOpenLauncher?: () => void;
```

Trong `import { Bell, ... } from "lucide-react";` thêm `LayoutGrid`. Thêm
`import { useNavigate } from "react-router-dom";`. Trong thân component, sau
`const { language, ... } = useI18n();` thêm `const navigate = useNavigate();`.

Ngay sau nút toggle-sidebar desktop (nút có `aria-pressed={sidebarCollapsed}`), chèn:
```tsx
        <button
          type="button"
          onClick={() => (onOpenLauncher ? onOpenLauncher() : navigate("/apps"))}
          className="grid h-10 w-10 place-items-center rounded-md text-muted-foreground hover:bg-accent/60 hover:text-foreground"
          title={t("topnav.apps")}
          aria-label={t("topnav.apps")}
        >
          <LayoutGrid className="h-5 w-5" />
        </button>
        {moduleLabel && (
          <span className="hidden text-sm font-medium text-foreground md:inline">{moduleLabel}</span>
        )}
```

- [ ] **Step 2: RootLayout — truyền moduleLabel + lưu lastModule**

Trong `RootLayout.tsx`:

Thêm import:
```tsx
import { SECTION_TO_MODULE } from "@/app/modules";
```

Trong thân `RootLayout`, sau khi có `section`, thêm effect lưu module gần nhất:
```tsx
  // Ghi nhớ module đang mở để "/" lần sau quay lại đúng chỗ (spec A1).
  useEffect(() => {
    try {
      window.localStorage.setItem("desi.lastModule", SECTION_TO_MODULE[section].key);
    } catch {
      /* localStorage có thể bị chặn — bỏ qua */
    }
  }, [section]);
```

Truyền nhãn module xuống `TopNav` (thêm 2 prop vào JSX `<TopNav .../>`):
```tsx
            moduleLabel={t(SECTION_TO_MODULE[section].labelKey)}
            onOpenLauncher={() => navigate("/apps")}
```

- [ ] **Step 3: RootLayout — bỏ effect redirect `/orders` cũ**

Xóa block `const didInitialRedirect = useRef(false);` cùng `useEffect` ngay dưới nó
(đoạn xử lý `location.pathname === "/orders"`). Lý do: `/orders` giờ là route redirect
sang `/sales/orders`; điểm vào mặc định do `DefaultRedirect` (nhớ module) xử lý. Xóa
import `useRef` nếu không còn dùng chỗ khác trong file.

- [ ] **Step 4: Kiểm tra biên dịch**

Run: `npx tsc --noEmit`
Expected: Không lỗi.

- [ ] **Step 5: Commit**

```bash
git add src/components/TopNav.tsx src/app/RootLayout.tsx
git commit -m "feat(shell): app-switcher button, module title, remember last module"
```

---

## Task 7: i18n + kiểm thử toàn bộ

**Files:**
- Modify: `frontend/src/i18n/messages/common.ts` (hoặc file chứa namespace `launcher`/`topnav` — xác định bằng grep)

- [ ] **Step 1: Tìm file i18n chứa key `topnav.tabs`**

Run: `grep -rl "topnav" src/i18n/messages | head`
Ghi nhận file `vi` và `en` tương ứng (cấu trúc khóa lồng `topnav: { ... }`).

- [ ] **Step 2: Thêm key mới (cả vi & en)**

Trong namespace `topnav`, thêm khóa `apps`. Thêm namespace `launcher` với khóa `title`.

vi:
```ts
    apps: "Ứng dụng",
```
```ts
  launcher: {
    title: "Ứng dụng",
  },
```

en:
```ts
    apps: "Apps",
```
```ts
  launcher: {
    title: "Apps",
  },
```

> `sidebar.group.*` và `topnav.tabs.*` đã tồn tại sẵn (registry tái dùng) — không thêm lại.

- [ ] **Step 3: Chạy toàn bộ test suite của plan**

Run:
```bash
npx vitest run src/lib/routes.test.ts src/app/__tests__/modules.test.ts src/app/__tests__/AppLauncher.test.tsx
```
Expected: PASS toàn bộ.

- [ ] **Step 4: Type-check + lint toàn dự án**

Run: `npx tsc --noEmit`
Expected: Không lỗi.

Run: `npm run test:ci`
Expected: Xanh trừ các test đỏ baseline đã biết (OrderFormDialog, `*.mobile`) — không có test mới đỏ do plan này.

- [ ] **Step 5: Kiểm thử thủ công nhanh (dev server)**

Run: `npm run dev` rồi kiểm tra:
- `/` → redirect về `/sales/orders` (hoặc module đã mở gần nhất).
- Mở `/orders/123` (deep-link cũ) → tự nhảy `/sales/orders/123`, hiện chi tiết đơn.
- Nút ⊞ → `/apps` hiện lưới module theo quyền; click module vào đúng section.
- Sidebar chỉ hiện section của module hiện tại; đổi module qua ⊞ thì sidebar đổi theo.
- `/warehouses` (cũ) → `/inventory/warehouses`.

- [ ] **Step 6: Commit**

```bash
git add src/i18n/messages
git commit -m "feat(shell): i18n keys for launcher and app switcher"
```

---

## Self-Review (đã thực hiện khi viết plan)

- **Spec coverage:** registry (§3 spec ↔ Task 2), nested URL + redirect (§4 ↔ Task 1+3),
  AppLauncher (§5.1 ↔ Task 4), Sidebar/MobileNav contextual (§5.3 ↔ Task 5), TopNav ⊞ +
  RootLayout lastModule (§5.2/5.4 ↔ Task 6), quyền không đổi (§6 — dùng lại
  `requires ?? key` & `requiredPermissionFor`), i18n (§7 ↔ Task 7). Đủ.
- **Type consistency:** `AppModule`/`ModuleSection`/`SECTION_TO_MODULE`/`accessibleModules`/
  `defaultSectionOfModule`/`moduleByKey` dùng nhất quán giữa Task 2,3,4,5,6. `pathFromSection`/
  `LEGACY_PATH_REDIRECTS` từ Task 1 dùng ở Task 3.
- **Rủi ro chính (R1 deep-link):** phủ bởi route redirect chi tiết đơn (Task 3 Step 3) +
  test legacy (Task 1) + kiểm thử thủ công (Task 7 Step 5).
```
