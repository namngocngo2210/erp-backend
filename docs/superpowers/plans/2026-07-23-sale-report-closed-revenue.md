# Nhóm 9 — Data chia & doanh thu chốt theo Sale: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm một khối bảng ở cuối trang Báo cáo Sale hiển thị theo từng nhân viên: số data được chia, số đơn chốt, % chốt và doanh thu chốt được.

**Architecture:** Backend thêm đúng một `annotate` (`closed_revenue`) vào lượt `GROUP BY` sẵn có của `summary_sale_performance_by_staff` — không thêm query, không thêm index. Frontend đọc field mới qua `useDashboardSummary`, render trong một component mới đứng riêng (`ClosedRevenueSection.tsx`) thay vì nối vào `DashboardSections.tsx` (đã 1443 dòng).

**Tech Stack:** Django 5 + DRF + Django ORM aggregate (backend), React 18 + TypeScript + Vite + shadcn/ui + vitest/testing-library (frontend).

**Spec:** `docs/superpowers/specs/2026-07-23-sale-report-closed-revenue-design.md`

## Global Constraints

- Worktree: `c:\Users\MYPC\Desktop\workspace\desi-order-closed-revenue`, nhánh `feat/sale-report-closed-revenue` (base `origin/master`).
- Commit message tiếng Anh, ngắn gọn, **không** kèm trailer `Co-Authored-By`.
- Doanh thu chốt = `SUM(total_after_discount)` với `assessment.sales_status_code ∈ ("closed", "closed_express", "closed_scheduled_date", "created_order")`. **Tính cả đơn đã hoàn. Không thêm bất kỳ điều kiện lọc `status` nào** — đơn huỷ đã bị loại sẵn ở `_base_orders_queryset()`.
- Tiền đi qua JSON dưới dạng **chuỗi** (`str(Decimal)`), không dùng float.
- Không sửa định nghĩa/số liệu của bất kỳ Nhóm 1–8 nào; không refactor `DashboardSections.tsx`.
- Lệnh test backend (Docker, khác docs trong repo):
  ```bash
  cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
    -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=test_closed_revenue \
    -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
    -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
    -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
    -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
    -v "$PWD/backend:/app" -w /app desi-backend-test:latest \
    sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
  ```
- Lệnh test frontend: `cd frontend && npx vitest run <path>`; typecheck: `npx tsc --noEmit`.

## File Structure

| File | Trách nhiệm | Thao tác |
|---|---|---|
| `backend/apps/orders/_repositories_reports.py` | Aggregate `closed_revenue` trong `summary_sale_performance_by_staff` | Sửa (~dòng 326–345) |
| `backend/apps/orders/tests/test_dashboard_summary_api.py` | Test hành vi hoàn/huỷ của `closed_revenue` | Sửa (thêm 1 test vào `DashboardSalePerformanceTests`) |
| `frontend/src/features/dashboard/types.ts` | Kiểu `SalePerformanceRow` | Sửa (thêm 1 field) |
| `frontend/src/features/dashboard/use-dashboard-summary.ts` | Cộng dồn tổng | Sửa (thêm 1 dòng reduce + 1 dòng seed) |
| `frontend/src/features/dashboard/components/ClosedRevenueSection.tsx` | Toàn bộ khối Nhóm 9 | **Tạo mới** |
| `frontend/src/features/dashboard/__tests__/ClosedRevenueSection.test.tsx` | Test render/sort/tổng/empty | **Tạo mới** |
| `frontend/src/i18n/messages/dashboard.ts` | Nhãn vi/en cho nhóm mới | Sửa |
| `frontend/src/pages/DashboardPage.tsx` | Gắn section vào cuối trang | Sửa |

---

### Task 1: Backend — field `closed_revenue`

**Files:**
- Modify: `backend/apps/orders/_repositories_reports.py:326-345`
- Test: `backend/apps/orders/tests/test_dashboard_summary_api.py` (class `DashboardSalePerformanceTests`, bắt đầu dòng 270)

**Interfaces:**
- Consumes: `CLOSED_SALES_STATUS_CODES` (đã có ở `_repositories_reports.py:62`), helper `self._create_order(*, sales_status=None, total=0, status=0)` của class test (dòng 347).
- Produces: mỗi dict trong `sale_performance_by_staff` có thêm key `closed_revenue: str` (chuỗi số thập phân, ví dụ `"350000"`, không bao giờ `None`).

**Bối cảnh cần biết:** `DashboardSalePerformanceTests.setUp` đã tạo sẵn cho `alice-perf` 2 đơn chốt đã giao thành công: `closed` 100.000 (status=3) và `closed_express` 200.000 (status=3). Vì vậy `closed_revenue` từ setUp = 300.000, và test mới cộng thêm trên nền đó.

- [ ] **Step 1: Viết test thất bại**

Thêm method sau vào class `DashboardSalePerformanceTests`, ngay sau `test_sale_performance_counters_match_status_distribution` (kết thúc ở dòng 359):

```python
    def test_closed_revenue_includes_returned_and_excludes_cancelled(self):
        """Doanh thu chốt = tiền các đơn ở trạng thái chốt, tính cả đơn sau đó bị
        hoàn, không tính đơn đã huỷ (đơn huỷ bị loại ở _base_orders_queryset)."""
        # Đơn đã chốt rồi bị hoàn (status=5) -> VẪN tính vào doanh thu chốt.
        self._create_order(sales_status="closed_scheduled_date", total=50000, status=5)
        # Đơn đã chốt nhưng đã huỷ (status=6) -> KHÔNG tính.
        self._create_order(sales_status="created_order", total=70000, status=6)
        # Đơn chưa chốt dù có tiền -> KHÔNG tính.
        self._create_order(sales_status="need_more_consulting", total=90000)

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/orders/dashboard/summary")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        alice = next(
            row
            for row in payload["sale_performance_by_staff"]
            if row["staff__username"] == "alice-perf"
        )

        # setUp: 100.000 (closed) + 200.000 (closed_express) + 50.000 đơn chốt bị hoàn.
        self.assertEqual(Decimal(alice["closed_revenue"]), Decimal("350000"))
        # Tiền đi qua JSON dưới dạng chuỗi để không mất độ chính xác Decimal.
        self.assertIsInstance(alice["closed_revenue"], str)
        # Nhóm 4 (chỉ đơn giao thành công) không bị ảnh hưởng.
        self.assertEqual(Decimal(alice["revenue"]), Decimal("300000"))
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run:
```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=test_closed_revenue \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$PWD/backend:/app" -w /app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_dashboard_summary_api.py::DashboardSalePerformanceTests::test_closed_revenue_includes_returned_and_excludes_cancelled -q --create-db'
```

Expected: FAIL với `KeyError: 'closed_revenue'`.

- [ ] **Step 3: Thêm annotate**

Trong `summary_sale_performance_by_staff`, ngay sau khối `revenue=Sum(...)` / `delivered_count=Count(...)` (dòng 326–333), thêm:

```python
                # Doanh thu CHỐT: tiền của các đơn ở trạng thái chốt, không phụ
                # thuộc kết quả giao hàng — tính cả đơn sau đó bị hoàn. Đơn huỷ
                # đã bị loại ở _base_orders_queryset() nên không lọc thêm.
                # Khác `revenue` phía trên (chỉ đơn giao thành công, Nhóm 4).
                closed_revenue=Sum(
                    "total_after_discount",
                    filter=Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES),
                ),
```

- [ ] **Step 4: Ép chuỗi trong vòng lặp hậu xử lý**

Sửa vòng lặp cuối method (dòng 343–344) thành:

```python
        for row in rows:
            row["revenue"] = str(row["revenue"] or Decimal("0"))
            row["closed_revenue"] = str(row["closed_revenue"] or Decimal("0"))
        return rows
```

- [ ] **Step 5: Chạy cả file test, xác nhận PASS**

Run:
```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=test_closed_revenue \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$PWD/backend:/app" -w /app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_dashboard_summary_api.py -q --create-db'
```

Expected: PASS toàn bộ file (test mới + các test cũ của dashboard summary không đổi kết quả).

- [ ] **Step 6: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue
git add backend/apps/orders/_repositories_reports.py backend/apps/orders/tests/test_dashboard_summary_api.py
git commit -m "feat(dashboard): add closed revenue per sale to summary"
```

---

### Task 2: Frontend — kiểu dữ liệu + tổng

**Files:**
- Modify: `frontend/src/features/dashboard/types.ts:31-47`
- Modify: `frontend/src/features/dashboard/use-dashboard-summary.ts:21-52`

**Interfaces:**
- Consumes: field `closed_revenue: string` từ Task 1.
- Produces: `SalePerformanceRow.closed_revenue: string`; `performanceTotals.closed_revenue: number` (đã `Number()` hoá, dùng cho dòng Tổng ở Task 3).

- [ ] **Step 1: Thêm field vào type**

Trong `types.ts`, thêm dòng vào `SalePerformanceRow` ngay dưới `revenue: string;` (dòng 43):

```ts
  /** Doanh thu các đơn ở trạng thái chốt (gồm đơn bị hoàn, không gồm đơn huỷ). */
  closed_revenue: string;
```

- [ ] **Step 2: Cộng dồn vào performanceTotals**

Trong `use-dashboard-summary.ts`, thêm vào object tích luỹ của `reduce` (ngay dưới dòng `revenue: acc.revenue + Number(r.revenue || 0),`):

```ts
        closed_revenue: acc.closed_revenue + Number(r.closed_revenue || 0),
```

và thêm vào giá trị khởi tạo (ngay dưới `revenue: 0,`):

```ts
        closed_revenue: 0,
```

- [ ] **Step 3: Typecheck**

Run: `cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue/frontend && npx tsc --noEmit`

Expected: không lỗi. (Nếu có test/fixture cũ dựng `SalePerformanceRow` thiếu field mới, TS sẽ báo — bổ sung `closed_revenue: "0"` vào fixture đó.)

- [ ] **Step 4: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue
git add frontend/src/features/dashboard/types.ts frontend/src/features/dashboard/use-dashboard-summary.ts
git commit -m "feat(dashboard): expose closed revenue in summary view model"
```

---

### Task 3: Frontend — component `ClosedRevenueSection`

**Files:**
- Create: `frontend/src/features/dashboard/components/ClosedRevenueSection.tsx`
- Create: `frontend/src/features/dashboard/__tests__/ClosedRevenueSection.test.tsx`
- Modify: `frontend/src/i18n/messages/dashboard.ts`

**Interfaces:**
- Consumes: `SalePerformanceRow` (Task 2), `performanceTotals` (Task 2), helper `formatCurrency(value: string | number): string` và `resolveDisplayName(row, fallback): string` từ `../dashboard-utils`, `formatPercent(numerator: number, denominator: number): string` từ `@/lib/metrics`.
- Produces: named export `ClosedRevenueSection` với props
  `{ t: Translate; performanceRows: readonly SalePerformanceRow[]; performanceTotals: { total: number; closed: number; closed_revenue: number }; unknownStaffLabel: string }`.

- [ ] **Step 1: Thêm nhãn i18n**

Trong `frontend/src/i18n/messages/dashboard.ts`, thêm vào object `vi` (trước dấu `},` đóng của `vi`):

```ts
    "dashboard.closedRevenueTitle": "Nhóm 9 — Data chia & doanh thu chốt theo Sale",
    "dashboard.closedRevenueHint":
      "Doanh thu chốt = tổng tiền các đơn ở trạng thái Đã chốt / Hoả tốc / Hẹn lịch / Đã lên đơn. Tính cả đơn sau đó bị hoàn; không tính đơn đã huỷ. Khác Nhóm 4 — nhóm đó chỉ tính đơn đã giao thành công.",
    "dashboard.assignedDataColumn": "Data được chia",
    "dashboard.closedRevenueColumn": "Doanh thu chốt",
```

và vào object `en`:

```ts
    "dashboard.closedRevenueTitle": "Group 9 — Assigned data & closed revenue by sale",
    "dashboard.closedRevenueHint":
      "Closed revenue = total amount of orders in a closed status (closed / express / scheduled / order created). Includes orders later returned; excludes cancelled orders. Unlike Group 4, which only counts successfully delivered orders.",
    "dashboard.assignedDataColumn": "Assigned data",
    "dashboard.closedRevenueColumn": "Closed revenue",
```

- [ ] **Step 2: Viết test thất bại**

Tạo `frontend/src/features/dashboard/__tests__/ClosedRevenueSection.test.tsx`:

```tsx
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import { I18nProvider, useI18n } from "@/i18n";
import { ClosedRevenueSection } from "../components/ClosedRevenueSection";
import type { SalePerformanceRow } from "../types";

afterEach(cleanup);

function makeRow(overrides: Partial<SalePerformanceRow>): SalePerformanceRow {
  return {
    staff_id: 1,
    staff__username: "sale",
    staff__display_name: null,
    total: 0,
    total_assessed: 0,
    closed: 0,
    not_buying: 0,
    nurture: 0,
    unreached: 0,
    dirty: 0,
    warranty: 0,
    revenue: "0",
    closed_revenue: "0",
    delivered_count: 0,
    nurture_total_ever: 0,
    nurture_followup_closed: 0,
    ...overrides,
  };
}

// Component nhận `t` từ ngoài (giống các section khác), nên test bọc một
// wrapper nhỏ để lấy `t` thật từ I18nProvider thay vì tự chế hàm dịch.
function Harness({ rows }: { rows: SalePerformanceRow[] }) {
  const { t } = useI18n();
  const totals = rows.reduce(
    (acc, r) => ({
      total: acc.total + r.total,
      closed: acc.closed + r.closed,
      closed_revenue: acc.closed_revenue + Number(r.closed_revenue || 0),
    }),
    { total: 0, closed: 0, closed_revenue: 0 },
  );
  return (
    <ClosedRevenueSection
      t={t}
      performanceRows={rows}
      performanceTotals={totals}
      unknownStaffLabel="Không xác định"
    />
  );
}

function renderSection(rows: SalePerformanceRow[]) {
  return render(
    <I18nProvider>
      <Harness rows={rows} />
    </I18nProvider>,
  );
}

const ROWS = [
  makeRow({
    staff_id: 1,
    staff__username: "linh",
    staff__display_name: "Linh",
    total: 200,
    closed: 100,
    closed_revenue: "1000000",
  }),
  makeRow({
    staff_id: 2,
    staff__username: "hanh",
    staff__display_name: "Hạnh",
    total: 300,
    closed: 150,
    closed_revenue: "3000000",
  }),
];

describe("ClosedRevenueSection", () => {
  it("sắp xếp theo doanh thu chốt giảm dần", () => {
    renderSection(ROWS);

    const names = screen.getAllByTestId("closed-revenue-staff").map((el) => el.textContent);
    expect(names).toEqual(["Hạnh", "Linh"]);
  });

  it("dòng Tổng cộng đúng data, đơn chốt, doanh thu và % chốt trên tổng", () => {
    renderSection(ROWS);

    const totalRow = screen.getByTestId("closed-revenue-total-row");
    // 500 data, 250 đơn chốt, 50.0%, 4.000.000 doanh thu
    expect(within(totalRow).getByText("500")).toBeInTheDocument();
    expect(within(totalRow).getByText("250")).toBeInTheDocument();
    expect(within(totalRow).getByText("50.0%")).toBeInTheDocument();
    expect(within(totalRow).getByText("4.000.000")).toBeInTheDocument();
  });

  it("không có dòng nào -> hiện empty state", () => {
    renderSection([]);

    expect(screen.getByText("Không có dữ liệu để hiển thị")).toBeInTheDocument();
    expect(screen.queryByTestId("closed-revenue-total-row")).toBeNull();
  });

  it("thiếu tên hiển thị -> dùng nhãn Không xác định", () => {
    renderSection([
      makeRow({ staff_id: null, staff__username: null, total: 10, closed: 5, closed_revenue: "1" }),
    ]);

    expect(screen.getByTestId("closed-revenue-staff")).toHaveTextContent("Không xác định");
  });
});
```

- [ ] **Step 3: Chạy test, xác nhận FAIL**

Run: `cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue/frontend && npx vitest run src/features/dashboard/__tests__/ClosedRevenueSection.test.tsx`

Expected: FAIL — không resolve được module `../components/ClosedRevenueSection`.

- [ ] **Step 4: Viết component**

Tạo `frontend/src/features/dashboard/components/ClosedRevenueSection.tsx`:

```tsx
import { useMemo } from "react";
import { Inbox } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { formatPercent } from "@/lib/metrics";
import type { useI18n } from "@/i18n";

import { formatCurrency, resolveDisplayName } from "../dashboard-utils";
import type { SalePerformanceRow } from "../types";

type Translate = ReturnType<typeof useI18n>["t"];

/** Tổng đã cộng dồn ở useDashboardSummary — chỉ lấy các field khối này dùng. */
type ClosedRevenueTotals = {
  total: number;
  closed: number;
  closed_revenue: number;
};

/**
 * Nhóm 9 — bảng tổng kết cuối trang Báo cáo Sale: mỗi sale được chia bao nhiêu
 * data, chốt được bao nhiêu đơn và bao nhiêu tiền.
 *
 * `total` / `closed` dùng lại đúng hai field của Nhóm 1 nên hai nhóm luôn khớp
 * số. Cột tiền là `closed_revenue` (đơn ở trạng thái chốt, gồm cả đơn sau đó bị
 * hoàn) — khác `revenue` của Nhóm 4 vốn chỉ tính đơn giao thành công.
 *
 * Bảng hiển thị mở sẵn (không bọc CollapsibleTable) vì đây là bảng tổng kết —
 * người dùng cần liếc là thấy, không phải bấm mở.
 */
export function ClosedRevenueSection({
  t,
  performanceRows,
  performanceTotals,
  unknownStaffLabel,
}: {
  t: Translate;
  performanceRows: readonly SalePerformanceRow[];
  performanceTotals: ClosedRevenueTotals;
  unknownStaffLabel: string;
}) {
  // Sắp xếp doanh thu giảm dần trên bản sao — mảng gốc còn được các nhóm khác
  // dùng chung (thứ tự theo username), không được mutate.
  const rows = useMemo(
    () =>
      [...performanceRows].sort(
        (a, b) => Number(b.closed_revenue || 0) - Number(a.closed_revenue || 0),
      ),
    [performanceRows],
  );

  return (
    <Card className="rounded-xl border-border bg-card">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">
          {t("dashboard.closedRevenueTitle", "Nhóm 9 — Data chia & doanh thu chốt theo Sale")}
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          {t(
            "dashboard.closedRevenueHint",
            "Doanh thu chốt = tổng tiền các đơn ở trạng thái Đã chốt / Hoả tốc / Hẹn lịch / Đã lên đơn. Tính cả đơn sau đó bị hoàn; không tính đơn đã huỷ. Khác Nhóm 4 — nhóm đó chỉ tính đơn đã giao thành công.",
          )}
        </p>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <EmptyState
            icon={Inbox}
            title={t("dashboard.empty", "Không có dữ liệu để hiển thị")}
            className="py-6"
          />
        ) : (
          <div className="overflow-x-auto">
            <table data-slot="table" className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="py-2 pr-4 font-medium">{t("dashboard.staffColumn", "Nhân viên")}</th>
                  <th className="py-2 pr-4 text-right font-medium">
                    {t("dashboard.assignedDataColumn", "Data được chia")}
                  </th>
                  <th className="py-2 pr-4 text-right font-medium">
                    {t("dashboard.closedOrdersColumn", "Số đơn chốt")}
                  </th>
                  <th className="py-2 pr-4 text-right font-medium">
                    {t("dashboard.closeRateColumn", "% chốt")}
                  </th>
                  <th className="py-2 text-right font-medium">
                    {t("dashboard.closedRevenueColumn", "Doanh thu chốt")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={String(row.staff_id ?? "unknown")}
                    className="border-b border-border/60 transition-colors hover:bg-muted/50"
                  >
                    <td className="py-2 pr-4 text-foreground" data-testid="closed-revenue-staff">
                      {resolveDisplayName(row, unknownStaffLabel)}
                    </td>
                    <td className="py-2 pr-4 text-right text-foreground">
                      {row.total.toLocaleString("vi-VN")}
                    </td>
                    <td className="py-2 pr-4 text-right text-foreground">
                      {row.closed.toLocaleString("vi-VN")}
                    </td>
                    <td className="py-2 pr-4 text-right text-foreground">
                      {formatPercent(row.closed, row.total)}
                    </td>
                    <td className="py-2 text-right font-semibold text-foreground">
                      {formatCurrency(row.closed_revenue)}
                    </td>
                  </tr>
                ))}
                <tr data-testid="closed-revenue-total-row">
                  <td className="py-2 pr-4 text-sm font-semibold text-foreground">
                    {t("dashboard.totalRow", "Tổng")}
                  </td>
                  <td className="py-2 pr-4 text-right text-sm font-semibold text-foreground">
                    {performanceTotals.total.toLocaleString("vi-VN")}
                  </td>
                  <td className="py-2 pr-4 text-right text-sm font-semibold text-foreground">
                    {performanceTotals.closed.toLocaleString("vi-VN")}
                  </td>
                  <td className="py-2 pr-4 text-right text-sm font-semibold text-foreground">
                    {formatPercent(performanceTotals.closed, performanceTotals.total)}
                  </td>
                  <td className="py-2 text-right text-sm font-semibold text-foreground">
                    {formatCurrency(performanceTotals.closed_revenue)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default ClosedRevenueSection;
```

- [ ] **Step 5: Chạy test, xác nhận PASS**

Run: `cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue/frontend && npx vitest run src/features/dashboard/__tests__/ClosedRevenueSection.test.tsx`

Expected: PASS 4/4.

Nếu test "dòng Tổng" fail vì `500` xuất hiện ở nhiều ô: kiểm tra lại số liệu fixture — `ROWS` cố ý chọn 200/300 data và 100/150 đơn chốt để các con số trong dòng Tổng (500, 250, 50.0%, 4.000.000) đôi một khác nhau.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue
git add frontend/src/features/dashboard/components/ClosedRevenueSection.tsx frontend/src/features/dashboard/__tests__/ClosedRevenueSection.test.tsx frontend/src/i18n/messages/dashboard.ts
git commit -m "feat(dashboard): add closed revenue by sale section"
```

---

### Task 4: Gắn section vào cuối trang Báo cáo Sale

**Files:**
- Modify: `frontend/src/pages/DashboardPage.tsx:1-165`

**Interfaces:**
- Consumes: `ClosedRevenueSection` (Task 3), `performanceRows` + `performanceTotals` từ `useDashboardSummary` (Task 2).
- Produces: không có API mới.

- [ ] **Step 1: Import component**

Thêm vào `DashboardPage.tsx`, ngay sau khối import `DashboardSections` (kết thúc dòng 23):

```tsx
import { ClosedRevenueSection } from "@/features/dashboard/components/ClosedRevenueSection";
```

- [ ] **Step 2: Render cuối trang**

Ngay sau `<CarrierDataSection ... />` (dòng 160), trước thẻ đóng `</>`, thêm:

```tsx
          {/* Nhóm 9 — Data chia & doanh thu chốt theo Sale (bảng tổng kết cuối trang) */}
          <ClosedRevenueSection
            t={t}
            performanceRows={performanceRows}
            performanceTotals={performanceTotals}
            unknownStaffLabel={unknownStaffLabel}
          />
```

- [ ] **Step 3: Typecheck + toàn bộ test frontend**

Run:
```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue/frontend && npx tsc --noEmit && npx vitest run
```

Expected: typecheck sạch, toàn bộ test suite PASS.

- [ ] **Step 4: Lint file đã sửa**

Run:
```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue/frontend && npx eslint src/features/dashboard src/pages/DashboardPage.tsx --max-warnings=0
```

Expected: không lỗi, không cảnh báo.

- [ ] **Step 5: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue
git add frontend/src/pages/DashboardPage.tsx
git commit -m "feat(dashboard): render closed revenue section on sale report"
```

---

### Task 5: Kiểm chứng end-to-end & đẩy nhánh

**Files:** không sửa file nguồn.

- [ ] **Step 1: Chạy lại toàn bộ test dashboard backend**

Run:
```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=test_closed_revenue \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$PWD/backend:/app" -w /app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_dashboard_summary_api.py apps/orders/tests/test_ads_report_channel_scope.py -q --create-db'
```

Expected: PASS toàn bộ (file thứ hai để chắc annotate mới không ảnh hưởng báo cáo Ads dùng chung repository).

- [ ] **Step 2: Xác nhận không thêm query DB**

Đọc lại `summary_sale_performance_by_staff` và khẳng định `closed_revenue` nằm trong cùng chuỗi `.annotate(...)` với `total`/`closed`/`revenue` — không có `.filter()`/`.values()` mới, không có lời gọi queryset thứ hai. Ghi kết quả kiểm tra vào phần mô tả MR.

- [ ] **Step 3: Đẩy nhánh**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-closed-revenue
git push -u origin feat/sale-report-closed-revenue
```

- [ ] **Step 4: Gửi link MR**

Lấy URL tạo MR từ output của `git push` (GitLab in ra sẵn) và gửi cho user, kèm tóm tắt: 1 field backend mới, 1 component frontend mới, 5 test mới.

---

## Ghi chú cho người triển khai

- **Không** thêm `status__in`/`~Q(status__in=...)` vào filter của `closed_revenue`. Đã có người nhầm ở bản thiết kế đầu: user chốt rõ **tính cả đơn hoàn**.
- Đơn huỷ được loại một lần duy nhất ở `OrderRepository._base_orders_queryset()` (`backend/apps/orders/repositories.py:174-178`) — đừng lọc lại lần nữa ở tầng report.
- `revenue` (Nhóm 4) và `closed_revenue` (Nhóm 9) là **hai con số khác nhau, cùng tồn tại**. Không thay thế, không hợp nhất.
