# Sidebar Grouped Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tái cấu trúc sidebar: 3 trang báo cáo lên top-level phẳng, các module còn lại chia 2 nhóm có header ("VẬN HÀNH, QUẢN LÝ" / "CÀI ĐẶT, THIẾT LẬP"), áp dụng cho cả 3 biến thể (desktop mở rộng, rail thu gọn, mobile drawer).

**Architecture:** Thêm config layout `nav-layout.ts` tham chiếu key vào `APP_MODULES` (nguồn sự thật không đổi) + hàm pure `buildNavModel()` lọc RBAC, trả về `{ pinned, zones, activeGroupKey }`. `Sidebar.tsx` chỉ đổi phần render: block item phẳng + zone header; hành vi accordion/flyout/màu giữ nguyên.

**Tech Stack:** React 19 + TypeScript, Tailwind, vitest + @testing-library/react, lucide-react.

**Spec:** `docs/sidebar-grouped-layout.md`

## Global Constraints

- Làm việc trong worktree `.claude/worktrees/sidebar-grouped-layout`, branch `feat/sidebar-grouped-layout`. KHÔNG tự tạo branch mới.
- Không đổi route, RBAC backend, màu nền (`bg-primary`), hành vi accordion inline.
- Commit message tiếng Anh, ngắn gọn, KHÔNG kèm dòng `Co-Authored-By`.
- Mọi lệnh chạy trong `frontend/` của worktree. Test: `npx vitest run <file>`; full: `npm test`; typecheck: `npm run typecheck`.
- Baseline fail sẵn trên master (KHÔNG phải regression, đừng sửa): OrderFormDialog + 2 test mobile-viewport (`CreateReturnDialog.viewport`, `ScanDispatchDialog.viewport`).
- Label i18n mới: `sidebar.zone.ops` = "Vận hành, quản lý"/"Operations"; `sidebar.zone.settings` = "Cài đặt, thiết lập"/"Settings". Label trang báo cáo tái dùng key `topnav.tabs.*` sẵn có (vi: "Báo cáo Sale", "Báo cáo Ads", "Báo cáo khách hàng").

---

### Task 1: Config layout + buildNavModel + i18n keys

**Files:**
- Create: `frontend/src/app/nav-layout.ts`
- Modify: `frontend/src/i18n/messages/sidebar.ts`
- Test: `frontend/src/app/__tests__/nav-layout.test.ts`

**Interfaces:**
- Consumes: `APP_MODULES`, `ModuleSection` từ `@/app/modules`; `AppSection` từ `@/lib/routes`.
- Produces (Task 2/3 dùng):
  - `SIDEBAR_PINNED: AppSection[]`
  - `SIDEBAR_ZONES: SidebarZone[]` với `SidebarZone = { labelKey: string; moduleKeys: string[] }`
  - `NavGroup = { key: string; labelKey: string; icon: LucideIcon; items: ModuleSection[] }`
  - `NavZone = { labelKey: string; groups: NavGroup[] }`
  - `NavModel = { pinned: ModuleSection[]; zones: NavZone[]; activeGroupKey: string | null }`
  - `buildNavModel(active: AppSection, allowedSections: string[]): NavModel`

- [ ] **Step 1: Viết test fail**

Tạo `frontend/src/app/__tests__/nav-layout.test.ts`:

```ts
import { describe, it, expect } from "vitest";

import { APP_MODULES } from "@/app/modules";
import { SIDEBAR_PINNED, SIDEBAR_ZONES, buildNavModel } from "@/app/nav-layout";

const ALL_SECTION_KEYS = APP_MODULES.flatMap((m) => m.sections.map((s) => s.key));
/** Toàn bộ permission key để mô phỏng user full quyền (gồm cả requires/requiresAny). */
const FULL_PERMISSIONS = [
  ...new Set(
    APP_MODULES.flatMap((m) =>
      m.sections.flatMap((s) => s.requiresAny ?? [s.requires ?? s.key]),
    ),
  ),
];

describe("sidebar layout config", () => {
  it("pinned sections tồn tại trong APP_MODULES và không trùng", () => {
    for (const key of SIDEBAR_PINNED) {
      expect(ALL_SECTION_KEYS).toContain(key);
    }
    expect(new Set(SIDEBAR_PINNED).size).toBe(SIDEBAR_PINNED.length);
  });

  it("moduleKeys trong zone tồn tại trong APP_MODULES và không trùng giữa các zone", () => {
    const allZoneModuleKeys = SIDEBAR_ZONES.flatMap((z) => z.moduleKeys);
    const moduleKeys = APP_MODULES.map((m) => m.key);
    for (const key of allZoneModuleKeys) {
      expect(moduleKeys).toContain(key);
    }
    expect(new Set(allZoneModuleKeys).size).toBe(allZoneModuleKeys.length);
  });

  it("mọi section đều reachable: pinned hoặc thuộc module nằm trong zone", () => {
    const zoneModules = new Set(SIDEBAR_ZONES.flatMap((z) => z.moduleKeys));
    const pinned = new Set<string>(SIDEBAR_PINNED);
    for (const m of APP_MODULES) {
      for (const s of m.sections) {
        const reachable = pinned.has(s.key) || zoneModules.has(m.key);
        expect(reachable, `section ${s.key} không reachable`).toBe(true);
      }
    }
  });

  it("module reports đã rút hết section lên phẳng, không nằm trong zone nào", () => {
    const reports = APP_MODULES.find((m) => m.key === "reports")!;
    for (const s of reports.sections) {
      expect(SIDEBAR_PINNED).toContain(s.key);
    }
    expect(SIDEBAR_ZONES.flatMap((z) => z.moduleKeys)).not.toContain("reports");
  });
});

describe("buildNavModel", () => {
  it("full quyền: đủ 3 item phẳng, 2 zone, reports không thành group", () => {
    const model = buildNavModel("orders", FULL_PERMISSIONS);
    expect(model.pinned.map((s) => s.key)).toEqual([
      "dashboard",
      "ads-report",
      "customer-report",
    ]);
    expect(model.zones.map((z) => z.labelKey)).toEqual([
      "sidebar.zone.ops",
      "sidebar.zone.settings",
    ]);
    const groupKeys = model.zones.flatMap((z) => z.groups.map((g) => g.key));
    expect(groupKeys).toEqual(["sales", "crm", "inventory", "catalog", "marketing", "hr", "settings"]);
    expect(groupKeys).not.toContain("reports");
  });

  it("không có quyền báo cáo → block phẳng rỗng", () => {
    const model = buildNavModel("orders", ["orders"]);
    expect(model.pinned).toEqual([]);
    expect(model.zones).toHaveLength(1);
    expect(model.zones[0].labelKey).toBe("sidebar.zone.ops");
    expect(model.zones[0].groups.map((g) => g.key)).toEqual(["sales"]);
  });

  it("chỉ quyền HR → zone ops ẩn, zone settings chỉ còn hr", () => {
    const model = buildNavModel("employees", ["employees"]);
    expect(model.zones.map((z) => z.labelKey)).toEqual(["sidebar.zone.settings"]);
    // employees kéo theo leave / attendance-history (requires: employees)
    // và attendance-locations (module settings, requires: employees).
    expect(model.zones[0].groups.map((g) => g.key)).toEqual(["hr", "settings"]);
  });

  it("customer-report hiện theo quyền dashboard (requires)", () => {
    const model = buildNavModel("dashboard", ["dashboard"]);
    expect(model.pinned.map((s) => s.key)).toEqual(["dashboard", "customer-report"]);
  });

  it("activeGroupKey = null khi đứng ở trang pinned, = module khi đứng trong accordion", () => {
    expect(buildNavModel("dashboard", FULL_PERMISSIONS).activeGroupKey).toBeNull();
    expect(buildNavModel("stock", FULL_PERMISSIONS).activeGroupKey).toBe("inventory");
    expect(buildNavModel("roles", FULL_PERMISSIONS).activeGroupKey).toBe("settings");
  });
});
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `npx vitest run src/app/__tests__/nav-layout.test.ts`
Expected: FAIL — `Cannot find module '@/app/nav-layout'` (hoặc tương đương).

- [ ] **Step 3: Tạo `frontend/src/app/nav-layout.ts`**

```ts
import { type LucideIcon } from "lucide-react";

import { APP_MODULES, type ModuleSection } from "@/app/modules";
import { type AppSection } from "@/lib/routes";

/** Section phẳng ở đầu sidebar — bấm điều hướng thẳng, không qua accordion. */
export const SIDEBAR_PINNED: AppSection[] = ["dashboard", "ads-report", "customer-report"];

export type SidebarZone = {
  labelKey: string;
  moduleKeys: string[];
};

/** Nhóm module bên dưới block phẳng, theo đúng thứ tự hiển thị. */
export const SIDEBAR_ZONES: SidebarZone[] = [
  {
    labelKey: "sidebar.zone.ops",
    moduleKeys: ["sales", "crm", "inventory", "catalog", "marketing"],
  },
  {
    labelKey: "sidebar.zone.settings",
    moduleKeys: ["hr", "settings"],
  },
];

export type NavGroup = {
  key: string;
  labelKey: string;
  icon: LucideIcon;
  items: ModuleSection[];
};

export type NavZone = {
  labelKey: string;
  groups: NavGroup[];
};

export type NavModel = {
  pinned: ModuleSection[];
  zones: NavZone[];
  /** Module chứa section active (null nếu active là item phẳng). */
  activeGroupKey: string | null;
};

const PINNED_SET = new Set<AppSection>(SIDEBAR_PINNED);

function canSee(section: ModuleSection, allowed: Set<string>): boolean {
  return section.requiresAny
    ? section.requiresAny.some((x) => allowed.has(x))
    : allowed.has(section.requires ?? section.key);
}

/**
 * Model điều hướng sidebar: item phẳng + zone module (đã lọc RBAC).
 * Pure function để unit test không cần render; Sidebar bọc trong useMemo.
 */
export function buildNavModel(active: AppSection, allowedSections: string[]): NavModel {
  const allowed = new Set(allowedSections);
  const sectionByKey = new Map(
    APP_MODULES.flatMap((m) => m.sections.map((s) => [s.key, s] as const)),
  );

  const pinned = SIDEBAR_PINNED.flatMap((key) => {
    const section = sectionByKey.get(key);
    return section && canSee(section, allowed) ? [section] : [];
  });

  const zones = SIDEBAR_ZONES.map((zone) => ({
    labelKey: zone.labelKey,
    groups: zone.moduleKeys.flatMap((moduleKey) => {
      const m = APP_MODULES.find((x) => x.key === moduleKey);
      if (!m) return [];
      const items = m.sections.filter((s) => !PINNED_SET.has(s.key) && canSee(s, allowed));
      return items.length > 0 ? [{ key: m.key, labelKey: m.labelKey, icon: m.icon, items }] : [];
    }),
  })).filter((zone) => zone.groups.length > 0);

  const activeGroupKey =
    zones.flatMap((z) => z.groups).find((g) => g.items.some((i) => i.key === active))?.key ?? null;

  return { pinned, zones, activeGroupKey };
}
```

- [ ] **Step 4: Thêm i18n keys vào `frontend/src/i18n/messages/sidebar.ts`**

Trong object `vi` thêm:

```ts
    "sidebar.zone.ops": "Vận hành, quản lý",
    "sidebar.zone.settings": "Cài đặt, thiết lập",
```

Trong object `en` thêm:

```ts
    "sidebar.zone.ops": "Operations",
    "sidebar.zone.settings": "Settings",
```

- [ ] **Step 5: Chạy test, xác nhận pass**

Run: `npx vitest run src/app/__tests__/nav-layout.test.ts`
Expected: PASS toàn bộ (10 test).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/nav-layout.ts frontend/src/app/__tests__/nav-layout.test.ts frontend/src/i18n/messages/sidebar.ts
git commit -m "feat: add sidebar nav layout config with pinned sections and zones"
```

---

### Task 2: Render accordion desktop + mobile drawer theo model mới

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`
- Test: `frontend/src/components/__tests__/Sidebar.test.tsx` (tạo mới)

**Interfaces:**
- Consumes: `buildNavModel`, `NavGroup`, `NavZone` từ `@/app/nav-layout` (Task 1).
- Produces: `Sidebar` / `MobileNavDrawer` giữ nguyên props hiện tại (`active`, `allowedSections`, `collapsed`, `onChange`, `open`, `onClose`) — KHÔNG đổi call-site. Nội bộ: component `ZoneLabel({ labelKey })`, `PinnedList({ pinned, active, onSelect })` (Task 3 tái dùng model, không tái dùng 2 component này).

**Chi tiết sửa `Sidebar.tsx`:**

- [ ] **Step 1: Viết render test fail**

Tạo `frontend/src/components/__tests__/Sidebar.test.tsx`:

```tsx
import { describe, it, expect, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { I18nProvider } from "@/i18n";
import { Sidebar, MobileNavDrawer } from "@/components/Sidebar";
import { APP_MODULES } from "@/app/modules";

afterEach(cleanup);

const FULL_PERMISSIONS = [
  ...new Set(
    APP_MODULES.flatMap((m) =>
      m.sections.flatMap((s) => s.requiresAny ?? [s.requires ?? s.key]),
    ),
  ),
];

function renderSidebar(allowedSections: string[], collapsed = false) {
  return render(
    <I18nProvider>
      <Sidebar
        active="orders"
        allowedSections={allowedSections}
        collapsed={collapsed}
        onChange={() => {}}
      />
    </I18nProvider>,
  );
}

describe("Sidebar accordion (desktop mở rộng)", () => {
  it("hiện item phẳng báo cáo và header 2 zone khi đủ quyền", () => {
    renderSidebar(FULL_PERMISSIONS);
    expect(screen.getByRole("link", { name: "Báo cáo Sale" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Báo cáo Ads" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Báo cáo khách hàng" })).toBeTruthy();
    expect(screen.getByText("Vận hành, quản lý")).toBeTruthy();
    expect(screen.getByText("Cài đặt, thiết lập")).toBeTruthy();
  });

  it("ẩn block phẳng khi không có quyền báo cáo", () => {
    renderSidebar(["orders"]);
    expect(screen.queryByRole("link", { name: "Báo cáo Sale" })).toBeNull();
    expect(screen.getByText("Vận hành, quản lý")).toBeTruthy();
  });

  it("ẩn header zone khi cả zone không có quyền", () => {
    renderSidebar(["orders"]);
    expect(screen.queryByText("Cài đặt, thiết lập")).toBeNull();
  });
});

describe("MobileNavDrawer", () => {
  it("hiện item phẳng và header zone khi mở", () => {
    render(
      <I18nProvider>
        <MobileNavDrawer
          active="orders"
          allowedSections={FULL_PERMISSIONS}
          open
          onChange={() => {}}
          onClose={() => {}}
        />
      </I18nProvider>,
    );
    expect(screen.getByRole("link", { name: "Báo cáo Sale" })).toBeTruthy();
    expect(screen.getByText("Vận hành, quản lý")).toBeTruthy();
    expect(screen.getByText("Cài đặt, thiết lập")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `npx vitest run src/components/__tests__/Sidebar.test.tsx`
Expected: FAIL — chưa có link "Báo cáo Sale" / text "Vận hành, quản lý".

- [ ] **Step 3: Sửa `Sidebar.tsx` — thay useNavGroups bằng buildNavModel + render mới**

3a. Đổi import và xoá type/hook cũ: xoá `type NavGroup` local (dòng 31-36) và hook `useNavGroups` (dòng 39-63); xoá import `APP_MODULES` nếu không còn dùng, giữ `type ModuleSection`. Thêm:

```ts
import { buildNavModel, type NavGroup, type NavZone } from "@/app/nav-layout";
```

3b. Thêm 2 component nội bộ (đặt cạnh `SectionLink`):

```tsx
/** Header nhóm zone (chữ hoa, mờ) — dùng chung accordion desktop và mobile. */
function ZoneLabel({ labelKey }: { labelKey: string }) {
  const { t } = useI18n();
  return (
    <p className="px-2.5 pt-4 pb-1 text-[11px] font-semibold uppercase tracking-wider text-primary-foreground/50">
      {t(labelKey)}
    </p>
  );
}

/** Block item phẳng đầu sidebar (các trang báo cáo). */
function PinnedList({
  pinned,
  active,
  onSelect,
}: {
  pinned: ModuleSection[];
  active: AppSection;
  onSelect: (key: AppSection) => void;
}) {
  if (pinned.length === 0) return null;
  return (
    <ul className="space-y-0.5">
      {pinned.map((item) => (
        <li key={item.key}>
          <SectionLink item={item} active={active} onSelect={() => onSelect(item.key)} />
        </li>
      ))}
    </ul>
  );
}
```

3c. `Sidebar`: thay `useNavGroups(...)` bằng:

```ts
const { pinned, zones, activeGroupKey } = useMemo(
  () => buildNavModel(active, allowedSections),
  [active, allowedSections],
);
```

Truyền `pinned`, `zones` xuống `SidebarAccordion` / `SidebarRail` (props mới thay cho `groups`). Task này chỉ sửa `SidebarAccordion`; `SidebarRail` tạm nhận `zones` và render `zones.flatMap((z) => z.groups)` như cũ (Task 3 hoàn thiện rail).

3d. `SidebarAccordion` — props mới `{ pinned, zones, active, activeGroupKey, onChange }`, render:

```tsx
function SidebarAccordion({
  pinned,
  zones,
  active,
  activeGroupKey,
  onChange,
}: {
  pinned: ModuleSection[];
  zones: NavZone[];
  active: AppSection;
  activeGroupKey: string | null;
  onChange: (value: AppSection) => void;
}) {
  const { t } = useI18n();
  const [expandedGroup, setExpandedGroup] = useState<string | null>(activeGroupKey);

  useEffect(() => {
    if (activeGroupKey) setExpandedGroup(activeGroupKey);
  }, [activeGroupKey]);

  return (
    <nav className="flex-1 overflow-y-auto overflow-x-hidden px-2 py-2">
      <PinnedList pinned={pinned} active={active} onSelect={onChange} />
      {zones.map((zone) => (
        <div key={zone.labelKey}>
          <ZoneLabel labelKey={zone.labelKey} />
          <ul className="space-y-1">
            {zone.groups.map((group) => {
              /* GIỮ NGUYÊN body render module accordion hiện tại (button + chevron
                 + grid-rows collapse + SectionLink con) — chỉ chuyển từ groups.map
                 sang zone.groups.map, không đổi logic expandedGroup. */
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
```

(Body `zone.groups.map((group) => ...)` copy nguyên khối `groups.map((group) => ...)` cũ dòng 201-252, không sửa gì bên trong.)

3e. `MobileNavDrawer`: thay `useNavGroups` bằng `buildNavModel` (bọc `useMemo` như 3c), render `<PinnedList pinned={pinned} active={active} onSelect={(key) => { onChange(key); onClose(); }} />` ngay đầu `<nav>`, rồi map zones với `ZoneLabel` như 3d (giữ nguyên body accordion mobile hiện tại dòng 480-534).

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `npx vitest run src/components/__tests__/Sidebar.test.tsx src/app/__tests__/nav-layout.test.ts`
Expected: PASS toàn bộ.

- [ ] **Step 5: Typecheck + lint**

Run: `npm run typecheck && npm run lint`
Expected: 0 error (warning sẵn có được phép).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/components/__tests__/Sidebar.test.tsx
git commit -m "feat: render sidebar with pinned reports and zone headers"
```

---

### Task 3: Rail thu gọn — icon phẳng + divider zone

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx` (function `SidebarRail`)
- Test: `frontend/src/components/__tests__/Sidebar.test.tsx` (thêm describe)

**Interfaces:**
- Consumes: props `{ pinned, zones, active, activeGroupKey, onChange }` (Task 2 đã truyền xuống).
- Produces: không có — thay đổi nội bộ render.

- [ ] **Step 1: Thêm test fail vào `Sidebar.test.tsx`**

```tsx
describe("SidebarRail (thu gọn)", () => {
  it("hiện icon phẳng điều hướng thẳng (title = tên trang) và không hiện header text", () => {
    renderSidebar(FULL_PERMISSIONS, true);
    expect(screen.getByTitle("Báo cáo Sale")).toBeTruthy();
    expect(screen.getByTitle("Báo cáo Ads")).toBeTruthy();
    // Rail không render label zone dạng text, chỉ divider.
    expect(screen.queryByText("Vận hành, quản lý")).toBeNull();
  });

  it("ẩn icon phẳng khi không có quyền báo cáo", () => {
    renderSidebar(["orders"], true);
    expect(screen.queryByTitle("Báo cáo Sale")).toBeNull();
  });
});
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `npx vitest run src/components/__tests__/Sidebar.test.tsx`
Expected: FAIL — chưa có element title "Báo cáo Sale" ở rail.

- [ ] **Step 3: Sửa `SidebarRail`**

Props mới `{ pinned, zones, active, activeGroupKey, onChange }`. Giữ toàn bộ logic flyout (openGroup, flyoutPos, timers, portal) — chỉ đổi lookup `openGroupData` sang `zones.flatMap((z) => z.groups).find((g) => g.key === openGroup)` và cấu trúc `<nav>`:

```tsx
<nav className="flex-1 overflow-y-auto overflow-x-hidden px-1.5 py-2">
  {pinned.length > 0 && (
    <ul className="space-y-1">
      {pinned.map((item) => {
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
              title={t(item.labelKey)}
              className={`flex w-full items-center justify-center rounded-lg px-2 py-2 transition-colors ${
                isActive
                  ? "bg-primary-foreground/15 text-white"
                  : "text-primary-foreground/85 hover:bg-primary-foreground/10 hover:text-white"
              }`}
            >
              <span
                className={`grid h-8 w-8 shrink-0 place-items-center rounded-md ${
                  isActive ? "bg-white/15" : ""
                }`}
              >
                <ItemIcon className="h-[18px] w-[18px]" />
              </span>
            </a>
          </li>
        );
      })}
    </ul>
  )}
  {zones.map((zone) => (
    <div key={zone.labelKey}>
      <div aria-hidden className="mx-2 my-2 border-t border-primary-foreground/15" />
      <ul className="space-y-1">
        {zone.groups.map((group) => {
          /* GIỮ NGUYÊN body render icon module + hover/click flyout hiện tại
             (dòng 339-374 cũ) — chỉ chuyển từ groups.map sang zone.groups.map. */
        })}
      </ul>
    </div>
  ))}
</nav>
```

Lưu ý: nếu `pinned.length === 0` thì zone đầu tiên vẫn render divider — chấp nhận được (ngăn cách với logo) hoặc bỏ divider đầu bằng `zones.map((zone, i) => ...)` + `{(i > 0 || pinned.length > 0) && <divider/>}`. Chọn cách có điều kiện `i`.

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `npx vitest run src/components/__tests__/Sidebar.test.tsx`
Expected: PASS toàn bộ.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/components/__tests__/Sidebar.test.tsx
git commit -m "feat: pinned shortcuts and zone dividers in collapsed sidebar rail"
```

---

### Task 4: Full suite + cập nhật docs + push + MR

**Files:**
- Modify: `docs/sidebar-grouped-layout.md` (bảng Sprint)

- [ ] **Step 1: Chạy full FE test**

Run (trong `frontend/`): `npm test`
Expected: pass toàn bộ trừ baseline fail sẵn (OrderFormDialog + 2 mobile-viewport). Nếu có fail MỚI → sửa trước khi tiếp tục.

- [ ] **Step 2: Typecheck + lint + format**

Run: `npm run typecheck && npm run lint && npm run format:check`
Expected: 0 error. Nếu format:check fail → `npm run format` rồi kiểm tra diff.

- [ ] **Step 3: Cập nhật bảng Sprint trong `docs/sidebar-grouped-layout.md`**

Đổi 4 dòng "Chưa làm" thành "✅ Xong" kèm ghi chú ngắn những gì đã làm (theo yêu cầu CLAUDE.md: note lại sau mỗi sprint).

- [ ] **Step 4: Commit + push + MR**

```bash
git add docs/sidebar-grouped-layout.md
git commit -m "docs: mark sidebar grouped layout sprints done"
git push -u origin feat/sidebar-grouped-layout
```

Gửi link MR theo format GitLab của repo (remote origin), ví dụ: `https://<gitlab-host>/<group>/<repo>/-/merge_requests/new?merge_request[source_branch]=feat/sidebar-grouped-layout`.

- [ ] **Step 5: QA thị giác (manual, báo user)**

Nêu rõ trong summary cuối: cần user xem 3 biến thể (desktop mở rộng / thu gọn / mobile) trên môi trường dev trước khi merge.
