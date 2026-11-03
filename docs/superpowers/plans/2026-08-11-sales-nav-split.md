# Tách sidebar Sale thành Đơn hàng / CSKH — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tách module sidebar `sales` (7 mục) thành hai module — "Đơn hàng" và "CSKH" — mà không đổi URL và không đụng RBAC.

**Architecture:** `APP_MODULES` (`frontend/src/app/modules.ts`) là nguồn sự thật duy nhất cho nhóm sidebar + App Launcher; `SIDEBAR_ZONES` (`frontend/src/app/nav-layout.ts`) quyết định thứ tự hiển thị. URL nằm ở `SECTION_TO_PATH` (`frontend/src/lib/routes.ts`), khai báo tường minh và độc lập với `module.basePath`, nên tách module không đụng tới route. Thay đổi thuần dữ liệu cấu hình + i18n, không có logic mới.

**Tech Stack:** React 19 + TypeScript, Vite, Vitest, lucide-react, Tailwind.

## Global Constraints

- Worktree: `c:/Users/MYPC/Desktop/workspace/desi-order-wt-nav-split`, branch `feat/sales-nav-split`.
- Module key nhóm Đơn hàng **phải giữ nguyên chuỗi `"sales"`** — `RootLayout` ghi `localStorage["desi.lastModule"]` và `DefaultRedirect` đọc lại; đổi key làm giá trị đã lưu thành rác.
- Module key nhóm CSKH là chuỗi `"cskh"`.
- Section `outside-orders` **giữ nguyên** trong module `sales` với `hidden: true` ở plan này. Việc xóa nó thuộc plan `2026-08-11-remove-outside-orders.md`, làm sau.
- Không sửa `frontend/src/lib/routes.ts` trong plan này. URL không đổi.
- Không sửa `backend/` trong plan này.
- Repo không có CI (`.gitlab-ci.yml` đã bị xóa ở `eb15fe28`) — mọi lệnh test/lint chạy tay.
- Trước khi chạy test frontend lần đầu trong worktree, tạo junction `node_modules` (worktree này là worktree anh em nên junction dùng được):

  ```powershell
  New-Item -ItemType Junction -Path "c:\Users\MYPC\Desktop\workspace\desi-order-wt-nav-split\frontend\node_modules" -Target "c:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
  ```

- Baseline test đỏ SẴN trên master, không phải do plan này gây ra: `src/app/__tests__/RequireAuth.test.tsx` (2 test), `src/features/orders/__tests__/use-orders-data.realtime-upsert-filters.test.tsx` (5 test).
- Hook husky sẽ chạy prettier khi commit. Repo không có `.prettierrc` và master không sạch format — nếu hook format lan sang file ngoài phạm vi, `git checkout --` những file đó trước khi commit lại.

---

## File Structure

| File | Trách nhiệm | Thay đổi |
| --- | --- | --- |
| `frontend/src/i18n/messages/sidebar.ts` | Nhãn nhóm sidebar | Thêm `sidebar.group.orders`, `sidebar.group.cskh`; bỏ `sidebar.group.sale` |
| `frontend/src/i18n/messages/app.ts` | Nhãn ngắn module (App Launcher) | Thêm `module.cskh` |
| `frontend/src/i18n/messages/topnav.ts` | Nhãn từng section | Đổi `topnav.tabs.orders` thành "Đơn hàng chung" / "General Orders" |
| `frontend/src/app/modules.ts` | Khai báo module + section | Tách `sales` thành `sales` + `cskh` |
| `frontend/src/app/nav-layout.ts` | Thứ tự zone sidebar | Chèn `"cskh"` vào zone `ops` |
| `frontend/src/app/__tests__/modules.test.ts` | Khóa cấu trúc module | Cập nhật kỳ vọng |
| `frontend/src/app/__tests__/nav-layout.test.ts` | Khóa cấu trúc nav | Cập nhật kỳ vọng |

---

### Task 1: Tách `APP_MODULES` thành hai module Đơn hàng / CSKH

**Files:**
- Modify: `frontend/src/i18n/messages/sidebar.ts`
- Modify: `frontend/src/i18n/messages/app.ts:54` (vi) và `:117` (en)
- Modify: `frontend/src/i18n/messages/topnav.ts:22` (vi) và `:64` (en)
- Modify: `frontend/src/app/modules.ts:77-94`
- Test: `frontend/src/app/__tests__/modules.test.ts`

**Interfaces:**
- Consumes: không có (task đầu tiên).
- Produces: module key `"cskh"` với `labelKey: "sidebar.group.cskh"`, `shortLabelKey: "module.cskh"`, `basePath: "/sales"`, sections theo thứ tự `["contacts", "floating-pool", "resale-tracking"]`. Module `"sales"` còn lại sections theo thứ tự `["orders", "landing-orders", "koc-orders", "outside-orders"]` (`outside-orders` vẫn `hidden: true`). Task 2 dùng key `"cskh"`.

- [x] **Step 1: Cập nhật test khóa cấu trúc module cho khớp thiết kế mới**

Trong `frontend/src/app/__tests__/modules.test.ts`, sửa 3 chỗ.

Chỗ 1 — danh sách module kỳ vọng (`describe("APP_MODULES integrity")` → `it("has the expected modules")`), thay khối `expect(...).toEqual([...])`:

```ts
    expect(APP_MODULES.map((m) => m.key)).toEqual([
      "sales",
      "cskh",
      "crm",
      "inventory",
      "catalog",
      "marketing",
      "aftersales",
      "reports",
      "hr",
      "settings",
    ]);
```

Chỗ 2 — trong `describe("derived helpers")` → `it("SECTION_TO_MODULE maps every section to the right module")`, thêm vào cuối thân hàm:

```ts
    expect(SECTION_TO_MODULE["orders"].key).toBe("sales");
    expect(SECTION_TO_MODULE["landing-orders"].key).toBe("sales");
    expect(SECTION_TO_MODULE["koc-orders"].key).toBe("sales");
    expect(SECTION_TO_MODULE["contacts"].key).toBe("cskh");
    expect(SECTION_TO_MODULE["floating-pool"].key).toBe("cskh");
    expect(SECTION_TO_MODULE["resale-tracking"].key).toBe("cskh");
```

Chỗ 3 — thêm một `it` mới vào cuối `describe("derived helpers")`, ngay trước dấu `});` đóng describe:

```ts
  it("module cskh mở được bằng quyền lẻ, section mặc định là contacts", () => {
    const cskh = moduleByKey("cskh");
    expect(cskh).toBeDefined();
    expect(defaultSectionOfModule(cskh!)).toBe("contacts");
    expect(accessibleModules(["floating-pool"]).map((m) => m.key)).toEqual(["cskh"]);
    // Quyền của nhóm Đơn hàng KHÔNG kéo nhóm CSKH hiện ra và ngược lại.
    expect(accessibleModules(["orders"]).map((m) => m.key)).toEqual(["sales"]);
  });
```

Lưu ý: mảng `ALL_SECTIONS` ở đầu file **không đổi** — vẫn đủ 38 section, chỉ là phân bổ lại giữa hai module. Test `each section belongs to exactly one module` tự phủ điều đó.

- [x] **Step 2: Chạy test để xác nhận nó đỏ**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-nav-split/frontend && npx vitest run src/app/__tests__/modules.test.ts
```

Kỳ vọng: FAIL. `has the expected modules` báo mảng nhận được thiếu `"cskh"`; `module cskh mở được bằng quyền lẻ` báo `moduleByKey("cskh")` là `undefined`.

- [x] **Step 3: Thêm nhãn i18n**

Trong `frontend/src/i18n/messages/sidebar.ts`, ở khối `vi`, thay dòng `"sidebar.group.sale": "Sale",` bằng hai dòng:

```ts
    "sidebar.group.orders": "Đơn hàng",
    "sidebar.group.cskh": "CSKH",
```

Ở khối `en`, thay dòng `"sidebar.group.sale": "Sale",` bằng:

```ts
    "sidebar.group.orders": "Orders",
    "sidebar.group.cskh": "Customer Care",
```

Trong `frontend/src/i18n/messages/app.ts`, ngay sau dòng `"module.sales": "Bán hàng",` (khối `vi`) thêm:

```ts
    "module.cskh": "CSKH",
```

và ngay sau dòng `"module.sales": "Sales",` (khối `en`) thêm:

```ts
    "module.cskh": "Customer Care",
```

Trong `frontend/src/i18n/messages/topnav.ts`, đổi giá trị hai dòng sau (giữ nguyên khóa):

```ts
    "topnav.tabs.orders": "Đơn hàng chung",
```

```ts
    "topnav.tabs.orders": "General Orders",
```

- [x] **Step 4: Tách module trong `modules.ts`**

Trong `frontend/src/app/modules.ts`, thay toàn bộ phần tử module `sales` (từ dòng `key: "sales",` tới dấu `},` đóng object đó — hiện là dòng 78-94) bằng hai object:

```ts
  {
    key: "sales",
    labelKey: "sidebar.group.orders",
    shortLabelKey: "module.sales",
    icon: ShoppingCart,
    accent: { chip: "bg-sky-400", tile: "bg-sky-500/15 text-sky-600 dark:text-sky-400" },
    basePath: "/sales",
    sections: [
      { key: "orders", labelKey: "topnav.tabs.orders", icon: ShoppingBag },
      { key: "landing-orders", labelKey: "topnav.tabs.landingOrders", icon: RadioTower },
      { key: "koc-orders", labelKey: "topnav.tabs.kocOrders", icon: Sparkles },
      { key: "outside-orders", labelKey: "topnav.tabs.outsideOrders", icon: Send, hidden: true },
    ],
  },
  {
    // CSKH dùng chung basePath "/sales" vì SECTION_TO_PATH khai báo URL tường minh
    // (/sales/contacts, ...) — tách module KHÔNG đổi URL, không cần redirect.
    key: "cskh",
    labelKey: "sidebar.group.cskh",
    shortLabelKey: "module.cskh",
    icon: Headphones,
    accent: { chip: "bg-cyan-400", tile: "bg-cyan-500/15 text-cyan-600 dark:text-cyan-400" },
    basePath: "/sales",
    sections: [
      { key: "contacts", labelKey: "topnav.tabs.contacts", icon: Contact },
      { key: "floating-pool", labelKey: "topnav.tabs.floatingPool", icon: Waves },
      { key: "resale-tracking", labelKey: "topnav.tabs.resaleTracking", icon: PhoneCall },
    ],
  },
```

`Headphones` đã có sẵn trong khối import `lucide-react` ở đầu file (module `reports` dùng cho `call-quality`) — không cần thêm import.

- [x] **Step 5: Chạy test để xác nhận nó xanh**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-nav-split/frontend && npx vitest run src/app/__tests__/modules.test.ts
```

Kỳ vọng: PASS, toàn bộ file.

- [x] **Step 6: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-nav-split
git add frontend/src/app/modules.ts frontend/src/app/__tests__/modules.test.ts frontend/src/i18n/messages/sidebar.ts frontend/src/i18n/messages/app.ts frontend/src/i18n/messages/topnav.ts
git commit -m "feat(nav): split sales module into orders and customer care"
```

---

### Task 2: Chèn nhóm CSKH vào zone sidebar

**Files:**
- Modify: `frontend/src/app/nav-layout.ts:21-30`
- Test: `frontend/src/app/__tests__/nav-layout.test.ts`

**Interfaces:**
- Consumes: module key `"cskh"` từ Task 1.
- Produces: `SIDEBAR_ZONES[0].moduleKeys` = `["sales", "cskh", "crm", "inventory", "catalog", "marketing", "aftersales"]`. Không có export mới.

- [x] **Step 1: Cập nhật test nav-layout cho khớp thứ tự nhóm mới**

Trong `frontend/src/app/__tests__/nav-layout.test.ts`, sửa 2 test và thêm 1 test.

Sửa 1 — trong `it("full quyền: đủ 5 item phẳng, 2 zone, reports không thành group")`, thay khối `expect(groupKeys).toEqual([...])`:

```ts
    expect(groupKeys).toEqual([
      "sales",
      "cskh",
      "crm",
      "inventory",
      "catalog",
      "marketing",
      "aftersales",
      "hr",
      "settings",
    ]);
```

Sửa 2 — trong `it("không có quyền báo cáo → block phẳng rỗng")`, dòng cuối vẫn đúng nguyên văn (`["sales"]`) vì quyền chỉ có `"orders"`. Không đổi.

Thêm — một `it` mới vào cuối `describe("buildNavModel")`, ngay trước dấu `});` đóng describe:

```ts
  it("nhóm CSKH đứng ngay sau nhóm Đơn hàng và lọc độc lập theo quyền", () => {
    const onlyCskh = buildNavModel("contacts", ["contacts", "resale-tracking"]);
    expect(onlyCskh.zones[0].groups.map((g) => g.key)).toEqual(["cskh"]);
    expect(onlyCskh.zones[0].groups[0].items.map((i) => i.key)).toEqual([
      "contacts",
      "resale-tracking",
    ]);
    expect(onlyCskh.activeGroupKey).toBe("cskh");

    const onlyOrders = buildNavModel("orders", ["orders", "landing-orders"]);
    expect(onlyOrders.zones[0].groups.map((g) => g.key)).toEqual(["sales"]);
    expect(onlyOrders.activeGroupKey).toBe("sales");
  });
```

- [x] **Step 2: Chạy test để xác nhận nó đỏ**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-nav-split/frontend && npx vitest run src/app/__tests__/nav-layout.test.ts
```

Kỳ vọng: FAIL. `full quyền…` báo `groupKeys` nhận được thiếu `"cskh"`; `nhóm CSKH đứng ngay sau…` báo `zones[0].groups` rỗng vì module `cskh` chưa nằm trong zone nào.

- [x] **Step 3: Chèn `cskh` vào zone ops**

Trong `frontend/src/app/nav-layout.ts`, thay khối `moduleKeys` của zone `sidebar.zone.ops`:

```ts
    moduleKeys: ["sales", "cskh", "crm", "inventory", "catalog", "marketing", "aftersales"],
```

- [x] **Step 4: Chạy test để xác nhận nó xanh**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-nav-split/frontend && npx vitest run src/app/__tests__/nav-layout.test.ts
```

Kỳ vọng: PASS, toàn bộ file. Riêng test `mọi section đều reachable: pinned hoặc thuộc module nằm trong zone` là chốt chặn quan trọng — nó đỏ nếu quên chèn `cskh` vào zone.

- [x] **Step 5: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-nav-split
git add frontend/src/app/nav-layout.ts frontend/src/app/__tests__/nav-layout.test.ts
git commit -m "feat(nav): show customer care group in sidebar ops zone"
```

---

### Task 3: Kiểm chứng toàn cục và đẩy MR

**Files:**
- Không sửa file nguồn. Chỉ chạy kiểm chứng; sửa nếu có hồi quy.

**Interfaces:**
- Consumes: kết quả Task 1 và Task 2.
- Produces: nhánh đã push, link MR.

- [x] **Step 1: Chạy typecheck**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-nav-split/frontend && npx tsc --noEmit
```

Kỳ vọng: không có lỗi. Nếu có lỗi kiểu `Property 'sidebar.group.sale' is missing`, nghĩa là còn chỗ nào đó tham chiếu khóa i18n cũ — tìm bằng:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-nav-split && grep -rn "sidebar.group.sale" frontend/src
```

và đổi sang `sidebar.group.orders`.

- [x] **Step 2: Chạy lint**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-nav-split/frontend && npm run lint:strict
```

Kỳ vọng: 0 warning, 0 error.

- [x] **Step 3: Chạy toàn bộ suite frontend**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-nav-split/frontend && npm run test:ci
```

Kỳ vọng lúc viết plan này: **đúng 2 file đỏ / 7 test đỏ** — `RequireAuth.test.tsx` (2 test) và `use-orders-data.realtime-upsert-filters.test.tsx` (5 test), baseline đỏ sẵn trên master lúc đó. **Cập nhật 2026-08-11 (sau khi thực thi xong):** baseline đó đã lỗi thời — sau đợt tái cấu trúc test suite (MR !1154) và các fix tiếp theo, kết quả thực tế khi chạy plan này là **140/140 file xanh, 1052/1052 test xanh**, không còn 2 file đỏ nói trên.

Bất kỳ file đỏ nào ngoài baseline hiện hành lúc chạy là hồi quy do plan này — phải sửa trước khi đi tiếp. Nghi ngờ đầu tiên: test nào hard-code danh sách module/nhóm. Tìm bằng:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-nav-split && grep -rn '"sales"' frontend/src --include=*.test.ts --include=*.test.tsx
```

- [x] **Step 4: Kiểm tra bằng mắt trên app đang chạy**

Mở stack dev local (port 8080), đăng nhập `admin` / `admin123`, xác nhận:

1. Sidebar có hai nhóm liền nhau: **Đơn hàng** (Đơn hàng chung, Đơn hàng Landing Page, Đơn hàng KOC/KOL) và **CSKH** (Danh sách contact, Kho số thả nổi, Theo dõi resale).
2. Bấm từng mục CSKH — URL vẫn là `/sales/contacts`, `/sales/floating-pool`, `/sales/resale-tracking`, trang mở đúng.
3. Nhóm chứa trang đang mở tự bung (accordion) — vào `/sales/contacts` thì nhóm CSKH mở, nhóm Đơn hàng đóng.
4. App Launcher (icon lưới trên đầu) hiện tile "CSKH" màu cyan, bấm vào ra `/sales/contacts`.
5. Không còn mục "Đơn gửi ngoài" ở sidebar (vẫn ẩn như trước plan này).

- [x] **Step 5: Push và tạo MR**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-nav-split
git push -u origin feat/sales-nav-split \
  -o merge_request.create \
  -o merge_request.title="feat(nav): split sales sidebar into orders and customer care" \
  -o merge_request.remove_source_branch
```

Repo không có `glab` và không có token — `git push -o merge_request.create` là cách duy nhất tạo MR. Lấy link MR từ output của lệnh push và gửi cho người yêu cầu.

**Sau khi đã gửi link MR, ĐÓNG BĂNG nhánh này.** Plan 2 và plan 3 phải tạo nhánh mới từ master (hoặc từ nhánh này sau khi merge) — một nhánh chỉ đi với một MR.
