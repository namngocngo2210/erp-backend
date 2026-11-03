# Ads Report Chart + Drilldown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Ads Report page so each block shows a recharts chart on top with an expandable "Xem bảng chi tiết" drill-down table beneath (the Báo cáo Sale pattern), plus a top KPI tile row.

**Architecture:** Pure, testable selectors compute KPI totals + chart-shaped data from the existing `useAdsReport` payload. Presentational chart components wrap each recharts chart together with a `CollapsibleTable` that holds the current table (moved verbatim). The page is moved to `features/ads/pages/` and slimmed to compose these blocks. No backend/API/RBAC change.

**Tech Stack:** React + TypeScript, recharts `^3.9.1`, React Query (`useAdsReport`), vitest `^3.2.6` + @testing-library/react `^16`, shadcn/ui.

## Global Constraints

- **No backend / API / RBAC changes.** All data comes from the existing `GET /api/orders/reports/ads` payload.
- **No new dependencies.** Reuse `recharts` per the `features/dashboard/components/DashboardCharts.tsx` pattern.
- **No new fetch.** Keep React Query `useAdsReport`; one data source — drill-down never refetches.
- **Reuse existing helpers** from `features/ads/report-grouping.ts`: `formatAdsCurrency`, `formatRatioPct`, `shouldGroupByMonth`, `sumCostByChannel`, `groupDailyRowsByMonth`, `formatMonthLabel`, types `DailyBreakdownRow`/`DailyChannelRow`.
- **Preserve behavior verbatim**: channel-row SKU expand, top-products pagination + expand, monthly grouping (62-day threshold), error/loading handling (error → clear data + banner; loading → keep old data).
- **Light theme, blue tone.** Chart axis/grid use CSS vars (`var(--border)`, `var(--muted-foreground)`, `var(--foreground)`); series colors from `features/dashboard/dashboard-constants.ts` `CHART_COLORS`.
- **File size** target ≤ ~300 lines per CLAUDE.md.
- Test runner: `cd frontend && npx vitest run <path>`. Tests live in `__tests__/` folders beside source.
- Vietnamese UI copy via `useI18n().t(key, fallback)`.

---

### Task 1: Shared `CollapsibleTable` primitive

**Files:**
- Create: `frontend/src/components/report/CollapsibleTable.tsx`
- Test: `frontend/src/components/report/__tests__/CollapsibleTable.test.tsx`

**Interfaces:**
- Consumes: nothing.
- Produces: `CollapsibleTable({ label: string; children: ReactNode }): JSX.Element` — native `<details>` block. Default label handled by caller.

Rationale: `features/dashboard/components/DashboardPrimitives.tsx` already has an identical `CollapsibleTable`, but importing across features couples ads→dashboard. Create a shared copy under `components/report/` (where `ReportSection.tsx`/`StackedBarChart.tsx` already live). Do NOT touch the dashboard copy.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/report/__tests__/CollapsibleTable.test.tsx
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { CollapsibleTable } from "../CollapsibleTable";

afterEach(cleanup);

describe("CollapsibleTable", () => {
  it("renders the label and its children", () => {
    render(
      <CollapsibleTable label="Xem bảng chi tiết">
        <span>inner content</span>
      </CollapsibleTable>,
    );
    expect(screen.getByText("Xem bảng chi tiết")).toBeInTheDocument();
    expect(screen.getByText("inner content")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/report/__tests__/CollapsibleTable.test.tsx`
Expected: FAIL — cannot resolve `../CollapsibleTable`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/src/components/report/CollapsibleTable.tsx
import { type ReactNode } from "react";
import { ChevronRight } from "lucide-react";

/** Khối "Xem bảng chi tiết" bung/thu bằng <details> — chart ở trên, bảng ở đây. */
export function CollapsibleTable({ label, children }: { label: string; children: ReactNode }) {
  return (
    <details className="group mt-4 rounded-lg border border-border/60 bg-muted/20">
      <summary className="flex cursor-pointer select-none items-center gap-1.5 px-3 py-2 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground">
        <ChevronRight className="h-3.5 w-3.5 transition-transform group-open:rotate-90" />
        {label}
      </summary>
      <div className="overflow-x-auto px-3 pb-3">{children}</div>
    </details>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/report/__tests__/CollapsibleTable.test.tsx`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/report/CollapsibleTable.tsx frontend/src/components/report/__tests__/CollapsibleTable.test.tsx
git commit -m "feat(report): shared CollapsibleTable primitive"
```

---

### Task 2: Ads report selectors (pure logic + types)

**Files:**
- Create: `frontend/src/features/ads/ads-report-selectors.ts`
- Test: `frontend/src/features/ads/__tests__/ads-report-selectors.test.ts`

**Interfaces:**
- Consumes: `DailyBreakdownRow`, `sumCostByChannel`, `groupDailyRowsByMonth`, `formatMonthLabel` from `./report-grouping`.
- Produces (imported by Tasks 3–8):
  - Types `ChannelRevenueRow`, `TopProductChannelRow`, `TopProductRow`, `AdsReportPayload` (moved out of the page so components share them).
  - `computeAdsKpis(channelRows: ChannelRevenueRow[], dailyRows: DailyBreakdownRow[]): AdsKpis` where `AdsKpis = { totalRevenue: number; totalCost: number; ratioPct: number | null; closedOrders: number; deliveredOrders: number; deliveredRevenue: number }`. `totalCost` sums `sumCostByChannel` **only over channels present in `channelRows`** so it matches the existing table's "Tổng" row exactly.
  - `buildChannelRevenueChartData(channelRows: ChannelRevenueRow[], dailyRows: DailyBreakdownRow[]): ChannelRevenueDatum[]` where `ChannelRevenueDatum = { name: string; revenue: number; closedOrders: number; ratioPct: number | null }`, sorted by `revenue` desc.
  - `buildDailyTrendChartData(dailyRows: DailyBreakdownRow[], groupByMonth: boolean): TrendDatum[]` where `TrendDatum = { label: string; revenue: number; cost: number; ratioPct: number | null }`. When `groupByMonth`, group via `groupDailyRowsByMonth` and label with `formatMonthLabel`; else per-day, label = `date`.
  - `buildTopProductsChartData(topProducts: TopProductRow[], limit: number): TopProductDatum[]` where `TopProductDatum = { name: string; revenue: number; closedOrders: number }`, first `limit` items (payload already sorted), name = `product__sku || product__name || "—"`.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/features/ads/__tests__/ads-report-selectors.test.ts
import { describe, expect, it } from "vitest";

import type { DailyBreakdownRow } from "../report-grouping";
import {
  buildChannelRevenueChartData,
  buildDailyTrendChartData,
  buildTopProductsChartData,
  computeAdsKpis,
  type AdsReportPayload,
} from "../ads-report-selectors";

const CHANNEL_ROWS: AdsReportPayload["revenue_by_channel"] = [
  { channel_id: 1, channel__code: "fb-1", channel__name: "Kênh A", closed_orders: 2, revenue: "1000", delivered_orders: 1, delivered_revenue: "600" },
  { channel_id: 2, channel__code: "fb-2", channel__name: "Kênh B", closed_orders: 5, revenue: "3000", delivered_orders: 4, delivered_revenue: "2500" },
];

const DAILY_ROWS: DailyBreakdownRow[] = [
  {
    date: "2026-01-10", revenue: "1000", cost: "100", closed_orders: 2, ratio_pct: 10,
    channels: [
      { channel_id: 1, channel__code: "fb-1", channel__name: "Kênh A", revenue: "1000", cost: "100", closed_orders: 2, ratio_pct: 10 },
      { channel_id: 99, channel__code: "x", channel__name: "Ngoài", revenue: "0", cost: "500", closed_orders: 0, ratio_pct: null },
    ],
  },
  {
    date: "2026-02-05", revenue: "3000", cost: "600", closed_orders: 5, ratio_pct: 20,
    channels: [
      { channel_id: 2, channel__code: "fb-2", channel__name: "Kênh B", revenue: "3000", cost: "600", closed_orders: 5, ratio_pct: 20 },
    ],
  },
];

describe("computeAdsKpis", () => {
  it("totals only over channels present in channelRows and matches tfoot", () => {
    const kpis = computeAdsKpis(CHANNEL_ROWS, DAILY_ROWS);
    expect(kpis.totalRevenue).toBe(4000);
    // cost for channel 1 (100) + channel 2 (600); channel 99 excluded
    expect(kpis.totalCost).toBe(700);
    expect(kpis.ratioPct).toBeCloseTo(17.5);
    expect(kpis.closedOrders).toBe(7);
    expect(kpis.deliveredOrders).toBe(5);
    expect(kpis.deliveredRevenue).toBe(3100);
  });

  it("ratioPct is null when revenue is 0", () => {
    expect(computeAdsKpis([], []).ratioPct).toBeNull();
  });
});

describe("buildChannelRevenueChartData", () => {
  it("sorts by revenue desc and carries closed orders + ratio", () => {
    const data = buildChannelRevenueChartData(CHANNEL_ROWS, DAILY_ROWS);
    expect(data.map((d) => d.name)).toEqual(["Kênh B", "Kênh A"]);
    expect(data[0].revenue).toBe(3000);
    expect(data[1].ratioPct).toBeCloseTo(10); // 100/1000
  });
});

describe("buildDailyTrendChartData", () => {
  it("returns per-day rows when not grouping", () => {
    const data = buildDailyTrendChartData(DAILY_ROWS, false);
    expect(data.map((d) => d.label)).toEqual(["2026-01-10", "2026-02-05"]);
    expect(data[0]).toMatchObject({ revenue: 1000, cost: 100 });
  });

  it("groups by month with MM/YYYY labels when grouping", () => {
    const data = buildDailyTrendChartData(DAILY_ROWS, true);
    expect(data.map((d) => d.label)).toEqual(["01/2026", "02/2026"]);
    expect(data[0]).toMatchObject({ revenue: 1000, cost: 100, ratioPct: 10 });
  });
});

describe("buildTopProductsChartData", () => {
  it("takes first N and derives name/revenue/closedOrders", () => {
    const products: AdsReportPayload["top_products"] = [
      { product_id: 1, product__name: "P1", product__sku: "SKU1", closed_orders: 9, revenue: "900", channels: [] },
      { product_id: 2, product__name: "P2", product__sku: null, closed_orders: 4, revenue: "400", channels: [] },
    ];
    const data = buildTopProductsChartData(products, 1);
    expect(data).toEqual([{ name: "SKU1", revenue: 900, closedOrders: 9 }]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/ads/__tests__/ads-report-selectors.test.ts`
Expected: FAIL — cannot resolve `../ads-report-selectors`.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/src/features/ads/ads-report-selectors.ts
import {
  type DailyBreakdownRow,
  formatMonthLabel,
  groupDailyRowsByMonth,
  sumCostByChannel,
} from "./report-grouping";

export type ChannelRevenueRow = {
  channel_id: number | null;
  channel__code: string | null;
  channel__name: string | null;
  closed_orders: number;
  revenue: string;
  delivered_orders: number;
  delivered_revenue: string;
};

export type TopProductChannelRow = {
  channel_id: number | null;
  channel__code: string | null;
  channel__name: string | null;
  closed_orders: number;
  revenue: string;
};

export type TopProductRow = {
  product_id: number | null;
  product__name: string | null;
  product__sku: string | null;
  closed_orders: number;
  revenue: string;
  channels: TopProductChannelRow[];
};

export type AdsReportPayload = {
  revenue_by_channel: ChannelRevenueRow[];
  top_products: TopProductRow[];
  daily_breakdown: DailyBreakdownRow[];
};

export type AdsKpis = {
  totalRevenue: number;
  totalCost: number;
  ratioPct: number | null;
  closedOrders: number;
  deliveredOrders: number;
  deliveredRevenue: number;
};

export type ChannelRevenueDatum = {
  name: string;
  revenue: number;
  closedOrders: number;
  ratioPct: number | null;
};

export type TrendDatum = {
  label: string;
  revenue: number;
  cost: number;
  ratioPct: number | null;
};

export type TopProductDatum = {
  name: string;
  revenue: number;
  closedOrders: number;
};

function ratioPct(revenue: number, cost: number): number | null {
  return revenue > 0 ? (cost / revenue) * 100 : null;
}

function channelName(row: { channel__name: string | null; channel__code: string | null }): string {
  return row.channel__name || row.channel__code || "—";
}

/**
 * Tổng KPI đầu trang. `totalCost` chỉ cộng chi phí của các kênh CÓ trong
 * `channelRows` (kênh có đơn chốt) để khớp đúng dòng "Tổng" của bảng chi tiết.
 */
export function computeAdsKpis(
  channelRows: readonly ChannelRevenueRow[],
  dailyRows: readonly DailyBreakdownRow[],
): AdsKpis {
  const costByChannel = sumCostByChannel(dailyRows);
  let totalRevenue = 0;
  let totalCost = 0;
  let closedOrders = 0;
  let deliveredOrders = 0;
  let deliveredRevenue = 0;
  for (const row of channelRows) {
    totalRevenue += Number(row.revenue || 0);
    totalCost += row.channel_id != null ? (costByChannel.get(row.channel_id) ?? 0) : 0;
    closedOrders += row.closed_orders || 0;
    deliveredOrders += row.delivered_orders || 0;
    deliveredRevenue += Number(row.delivered_revenue || 0);
  }
  return {
    totalRevenue,
    totalCost,
    ratioPct: ratioPct(totalRevenue, totalCost),
    closedOrders,
    deliveredOrders,
    deliveredRevenue,
  };
}

export function buildChannelRevenueChartData(
  channelRows: readonly ChannelRevenueRow[],
  dailyRows: readonly DailyBreakdownRow[],
): ChannelRevenueDatum[] {
  const costByChannel = sumCostByChannel(dailyRows);
  return channelRows
    .map((row) => {
      const revenue = Number(row.revenue || 0);
      const cost = row.channel_id != null ? (costByChannel.get(row.channel_id) ?? 0) : 0;
      return {
        name: channelName(row),
        revenue,
        closedOrders: row.closed_orders || 0,
        ratioPct: ratioPct(revenue, cost),
      };
    })
    .sort((a, b) => b.revenue - a.revenue);
}

export function buildDailyTrendChartData(
  dailyRows: readonly DailyBreakdownRow[],
  groupByMonth: boolean,
): TrendDatum[] {
  if (groupByMonth) {
    return groupDailyRowsByMonth(dailyRows).map((m) => ({
      label: formatMonthLabel(m.month),
      revenue: m.revenue,
      cost: m.cost,
      ratioPct: m.ratio_pct,
    }));
  }
  return dailyRows.map((row) => ({
    label: row.date,
    revenue: Number(row.revenue || 0),
    cost: Number(row.cost || 0),
    ratioPct: row.ratio_pct,
  }));
}

export function buildTopProductsChartData(
  topProducts: readonly TopProductRow[],
  limit: number,
): TopProductDatum[] {
  return topProducts.slice(0, limit).map((p) => ({
    name: p.product__sku || p.product__name || "—",
    revenue: Number(p.revenue || 0),
    closedOrders: p.closed_orders || 0,
  }));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/ads/__tests__/ads-report-selectors.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/ads/ads-report-selectors.ts frontend/src/features/ads/__tests__/ads-report-selectors.test.ts
git commit -m "feat(ads): pure selectors for KPI + chart data"
```

---

### Task 3: `AdsKpiRow` component

**Files:**
- Create: `frontend/src/features/ads/components/AdsKpiRow.tsx`
- Test: `frontend/src/features/ads/components/__tests__/AdsKpiRow.test.tsx`

**Interfaces:**
- Consumes: `AdsKpis` (Task 2), `formatAdsCurrency`, `formatRatioPct` from `../report-grouping`, `useI18n`.
- Produces: `AdsKpiRow({ kpis: AdsKpis }): JSX.Element` — a responsive grid of 6 tiles.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/features/ads/components/__tests__/AdsKpiRow.test.tsx
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { I18nProvider } from "@/i18n";
import { AdsKpiRow } from "../AdsKpiRow";
import type { AdsKpis } from "../../ads-report-selectors";

afterEach(cleanup);

const KPIS: AdsKpis = {
  totalRevenue: 4000, totalCost: 700, ratioPct: 17.5,
  closedOrders: 7, deliveredOrders: 5, deliveredRevenue: 3100,
};

describe("AdsKpiRow", () => {
  it("renders all six tile values", () => {
    render(
      <I18nProvider>
        <AdsKpiRow kpis={KPIS} />
      </I18nProvider>,
    );
    expect(screen.getByText("17.5%")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/ads/components/__tests__/AdsKpiRow.test.tsx`
Expected: FAIL — cannot resolve `../AdsKpiRow`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/src/features/ads/components/AdsKpiRow.tsx
import { useI18n } from "@/i18n";
import { formatAdsCurrency, formatRatioPct } from "../report-grouping";
import type { AdsKpis } from "../ads-report-selectors";

type Tile = { label: string; value: string; tone?: "default" | "danger" };

export function AdsKpiRow({ kpis }: { kpis: AdsKpis }) {
  const { t } = useI18n();
  const tiles: Tile[] = [
    { label: t("adsReport.kpi.revenue", "Tổng doanh thu"), value: formatAdsCurrency(kpis.totalRevenue) },
    { label: t("adsReport.kpi.cost", "Tổng chi phí"), value: formatAdsCurrency(kpis.totalCost) },
    { label: t("adsReport.kpi.ratio", "% chi phí / doanh thu"), value: formatRatioPct(kpis.ratioPct), tone: "danger" },
    { label: t("adsReport.kpi.closed", "Đơn đã chốt"), value: String(kpis.closedOrders) },
    { label: t("adsReport.kpi.delivered", "Đơn giao thành công"), value: String(kpis.deliveredOrders) },
    { label: t("adsReport.kpi.deliveredRevenue", "Doanh thu giao thành công"), value: formatAdsCurrency(kpis.deliveredRevenue) },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {tiles.map((tile) => (
        <div key={tile.label} className="rounded-xl border border-border bg-card p-4">
          <div
            className={`text-xl font-semibold tabular-nums ${
              tile.tone === "danger" ? "text-rose-500" : "text-foreground"
            }`}
          >
            {tile.value}
          </div>
          <div className="mt-1 text-xs uppercase tracking-wide text-muted-foreground">{tile.label}</div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/ads/components/__tests__/AdsKpiRow.test.tsx`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/ads/components/AdsKpiRow.tsx frontend/src/features/ads/components/__tests__/AdsKpiRow.test.tsx
git commit -m "feat(ads): KPI tile row"
```

---

### Task 4: `RevenueByChannelChart` (bar + detail table)

**Files:**
- Create: `frontend/src/features/ads/components/RevenueByChannelChart.tsx`
- Test: `frontend/src/features/ads/components/__tests__/RevenueByChannelChart.test.tsx`

**Interfaces:**
- Consumes: `ChannelRevenueRow`, `TopProductRow`, `buildChannelRevenueChartData` (Task 2); `DailyBreakdownRow`, `sumCostByChannel`, `formatAdsCurrency`, `formatRatioPct` from `../report-grouping`; `CollapsibleTable` from `@/components/report/CollapsibleTable`; recharts; `useI18n`; `Card*` from `@/components/ui/card`.
- Produces: `RevenueByChannelChart({ channelRows: ChannelRevenueRow[]; topProducts: TopProductRow[]; dailyRows: DailyBreakdownRow[] }): JSX.Element`.

Card layout: title "Đơn đã chốt & doanh thu theo kênh"; empty state when `channelRows.length === 0`; otherwise a horizontal `BarChart` (layout `vertical`, `revenue` bar, `CHART_COLORS.blue`) sized `Math.max(240, data.length * 44 + 60)`, tooltip showing revenue + closed orders + ratio; below it a `CollapsibleTable` labelled `t("dashboard.showDetailTable", "Xem bảng chi tiết")` containing the existing channel table.

**Move the detail table verbatim.** Into the `CollapsibleTable`, move the existing channel `<table>` markup from `frontend/src/pages/AdsReportPage.tsx` lines 261–416 (the `<div className="overflow-x-auto"><table>…</table></div>`). Along with it, move these hooks/derivations currently in the page into this component so they travel with the table:
- `expandedChannels` state (page line 87) + its toggle usage.
- `productsByChannel` memo + local `ChannelProductRow` type (page lines 131–161).
- `costByChannel` memo (page line 182).
- totals: `totalChannelRevenue`, `totalChannelClosed`, `totalChannelDelivered`, `totalChannelDeliveredRevenue`, `totalChannelCost` (page lines 163–190).
Keep their bodies byte-for-byte; only their inputs change to this component's props (`channelRows`, `topProducts`, `dailyRows`).

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/features/ads/components/__tests__/RevenueByChannelChart.test.tsx
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { I18nProvider } from "@/i18n";
import { RevenueByChannelChart } from "../RevenueByChannelChart";
import type { ChannelRevenueRow, TopProductRow } from "../../ads-report-selectors";
import type { DailyBreakdownRow } from "../../report-grouping";

afterEach(cleanup);

const CHANNELS: ChannelRevenueRow[] = [
  { channel_id: 1, channel__code: "fb-1", channel__name: "Kênh A", closed_orders: 2, revenue: "1000", delivered_orders: 1, delivered_revenue: "600" },
];
const PRODUCTS: TopProductRow[] = [];
const DAILY: DailyBreakdownRow[] = [];

function renderChart(channels: ChannelRevenueRow[]) {
  return render(
    <I18nProvider>
      <RevenueByChannelChart channelRows={channels} topProducts={PRODUCTS} dailyRows={DAILY} />
    </I18nProvider>,
  );
}

describe("RevenueByChannelChart", () => {
  it("renders the detail table with the channel name", () => {
    renderChart(CHANNELS);
    expect(screen.getByText("Kênh A")).toBeInTheDocument();
    expect(screen.getByText("Xem bảng chi tiết")).toBeInTheDocument();
  });

  it("shows the empty state when there are no channels", () => {
    renderChart([]);
    expect(screen.getByText("Chưa có dữ liệu.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/ads/components/__tests__/RevenueByChannelChart.test.tsx`
Expected: FAIL — cannot resolve `../RevenueByChannelChart`.

- [ ] **Step 3: Write minimal implementation**

Create the component scaffold below, then paste the moved table markup + hooks into the marked regions (see "Move the detail table verbatim" above). `ResponsiveContainer` renders with 0 height in jsdom — that's fine; the test asserts on the detail table + labels, not chart pixels.

```tsx
// frontend/src/features/ads/components/RevenueByChannelChart.tsx
import { Fragment, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CollapsibleTable } from "@/components/report/CollapsibleTable";
import { ChartTooltip } from "@/features/dashboard/components/DashboardPrimitives";
import { CHART_COLORS } from "@/features/dashboard/dashboard-constants";
import { useI18n } from "@/i18n";
import {
  type ChannelRevenueRow,
  type TopProductRow,
  buildChannelRevenueChartData,
} from "../ads-report-selectors";
import {
  type DailyBreakdownRow,
  formatAdsCurrency as formatCurrency,
  formatRatioPct,
  sumCostByChannel,
} from "../report-grouping";

type Props = {
  channelRows: ChannelRevenueRow[];
  topProducts: TopProductRow[];
  dailyRows: DailyBreakdownRow[];
};

export function RevenueByChannelChart({ channelRows, topProducts, dailyRows }: Props) {
  const { t } = useI18n();
  const chartData = useMemo(
    () => buildChannelRevenueChartData(channelRows, dailyRows),
    [channelRows, dailyRows],
  );

  // ── MOVED FROM AdsReportPage: expandedChannels, ChannelProductRow, productsByChannel,
  //    costByChannel, totalChannel* memos (page lines 87, 131–190) — bodies unchanged.
  const [expandedChannels, setExpandedChannels] = useState<Set<string>>(new Set());
  // …paste productsByChannel / costByChannel / totals memos here…

  return (
    <Card className="rounded-xl border-border bg-card">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">
          {t("adsReport.revenueByChannelTitle", "Đơn đã chốt & doanh thu theo kênh")}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {channelRows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("adsReport.empty", "Chưa có dữ liệu.")}</p>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={Math.max(240, chartData.length * 44 + 60)}>
              <BarChart layout="vertical" data={chartData} margin={{ top: 8, right: 24, left: 8, bottom: 8 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" strokeOpacity={0.55} horizontal={false} />
                <XAxis
                  type="number"
                  tickFormatter={(v) => formatCurrency(Number(v))}
                  tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                  stroke="var(--border)" tickLine={false} axisLine={false}
                />
                <YAxis
                  type="category" dataKey="name" width={120}
                  tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                  stroke="var(--border)" tickLine={false} axisLine={false}
                />
                <Tooltip
                  content={
                    <ChartTooltip
                      formatter={(entry) => {
                        const p = entry.payload as { closedOrders?: number; ratioPct?: number | null } | undefined;
                        return `${formatCurrency(Number(entry.value))} · ${p?.closedOrders ?? 0} đơn · ${formatRatioPct(p?.ratioPct ?? null)}`;
                      }}
                    />
                  }
                />
                <Bar dataKey="revenue" name={t("adsReport.revenueHeader", "Doanh thu")} radius={[0, 4, 4, 0]}>
                  {chartData.map((row) => (
                    <Cell key={row.name} fill={CHART_COLORS.blue} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>

            <CollapsibleTable label={t("dashboard.showDetailTable", "Xem bảng chi tiết")}>
              {/* …paste channel <table> markup moved from AdsReportPage lines 261–416… */}
            </CollapsibleTable>
          </>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/ads/components/__tests__/RevenueByChannelChart.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/ads/components/RevenueByChannelChart.tsx frontend/src/features/ads/components/__tests__/RevenueByChannelChart.test.tsx
git commit -m "feat(ads): revenue-by-channel chart + detail table"
```

---

### Task 5: `DailyTrendChart` (composed chart + detail table)

**Files:**
- Create: `frontend/src/features/ads/components/DailyTrendChart.tsx`
- Test: `frontend/src/features/ads/components/__tests__/DailyTrendChart.test.tsx`
- Modify: `frontend/src/features/ads/components/DailyBreakdownTable.tsx` (add optional `bare` prop)

**Interfaces:**
- Consumes: `buildDailyTrendChartData` (Task 2); `DailyBreakdownRow`, `formatAdsCurrency`, `formatRatioPct` from `../report-grouping`; `DailyBreakdownTable` from `./DailyBreakdownTable`; `CollapsibleTable`; recharts `ComposedChart`; `CHART_COLORS`, `PERCENT_AXIS_DOMAIN`; `Card*`; `useI18n`.
- Produces: `DailyTrendChart({ rows: DailyBreakdownRow[]; groupByMonth: boolean }): JSX.Element`.

**Prerequisite — `bare` prop.** `DailyBreakdownTable` currently renders its own `<Card><CardHeader>…</CardHeader><CardContent>…</CardContent></Card>` (verified at `DailyBreakdownTable.tsx:81-90`). Nesting it inside the chart Card + CollapsibleTable would double the Card + title. Add an optional `bare?: boolean` (default `false`) to `DailyBreakdownTableProps`: when `true`, return the inner content only (the `rows.length === 0 ? …empty… : <div className="overflow-x-auto">…table…</div>` currently inside `<CardContent>`) without the surrounding `Card`/`CardHeader`/`CardContent`. Default `false` preserves today's rendering, so the existing `DailyBreakdownTable.test.tsx` stays green. `DailyTrendChart` renders `<DailyBreakdownTable rows={rows} groupByMonth={groupByMonth} bare />` inside the `CollapsibleTable`.

Layout: Card title "Diễn biến doanh thu / chi phí"; empty state when `rows.length === 0`; `ComposedChart` with X = `label`, left Y = currency (revenue + cost bars, `CHART_COLORS.blue` / `CHART_COLORS.amber`), right Y = `%` (`PERCENT_AXIS_DOMAIN`, `ratioPct` line, `CHART_COLORS.red`); below it a `CollapsibleTable` wrapping `<DailyBreakdownTable rows={rows} groupByMonth={groupByMonth} />`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/features/ads/components/__tests__/DailyTrendChart.test.tsx
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { I18nProvider } from "@/i18n";
import { DailyTrendChart } from "../DailyTrendChart";
import type { DailyBreakdownRow } from "../../report-grouping";

afterEach(cleanup);

const ROWS: DailyBreakdownRow[] = [
  { date: "2026-01-10", revenue: "1000", cost: "100", closed_orders: 2, ratio_pct: 10, channels: [] },
];

describe("DailyTrendChart", () => {
  it("renders the title and the detail table toggle", () => {
    render(
      <I18nProvider>
        <DailyTrendChart rows={ROWS} groupByMonth={false} />
      </I18nProvider>,
    );
    expect(screen.getByText("Diễn biến doanh thu / chi phí")).toBeInTheDocument();
    expect(screen.getByText("Xem bảng chi tiết")).toBeInTheDocument();
  });

  it("shows the empty state without rows", () => {
    render(
      <I18nProvider>
        <DailyTrendChart rows={[]} groupByMonth={false} />
      </I18nProvider>,
    );
    expect(screen.getByText("Chưa có dữ liệu.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/ads/components/__tests__/DailyTrendChart.test.tsx`
Expected: FAIL — cannot resolve `../DailyTrendChart`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/src/features/ads/components/DailyTrendChart.tsx
import { useMemo } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CollapsibleTable } from "@/components/report/CollapsibleTable";
import { ChartTooltip } from "@/features/dashboard/components/DashboardPrimitives";
import { CHART_COLORS, PERCENT_AXIS_DOMAIN } from "@/features/dashboard/dashboard-constants";
import { useI18n } from "@/i18n";
import { buildDailyTrendChartData } from "../ads-report-selectors";
import {
  type DailyBreakdownRow,
  formatAdsCurrency as formatCurrency,
  formatRatioPct,
} from "../report-grouping";
import { DailyBreakdownTable } from "./DailyBreakdownTable";

export function DailyTrendChart({
  rows,
  groupByMonth,
}: {
  rows: DailyBreakdownRow[];
  groupByMonth: boolean;
}) {
  const { t } = useI18n();
  const data = useMemo(() => buildDailyTrendChartData(rows, groupByMonth), [rows, groupByMonth]);
  return (
    <Card className="rounded-xl border-border bg-card">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">
          {t("adsReport.trendTitle", "Diễn biến doanh thu / chi phí")}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("adsReport.empty", "Chưa có dữ liệu.")}</p>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={320}>
              <ComposedChart data={data} margin={{ top: 16, right: 16, left: 8, bottom: 8 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" strokeOpacity={0.55} vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} stroke="var(--border)" tickLine={false} axisLine={false} />
                <YAxis yAxisId="money" tickFormatter={(v) => formatCurrency(Number(v))} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} stroke="var(--border)" tickLine={false} axisLine={false} width={70} />
                <YAxis yAxisId="ratio" orientation="right" domain={PERCENT_AXIS_DOMAIN} unit="%" tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} stroke="var(--border)" tickLine={false} axisLine={false} />
                <Tooltip
                  content={
                    <ChartTooltip
                      formatter={(entry) =>
                        entry.dataKey === "ratioPct"
                          ? formatRatioPct(entry.value == null ? null : Number(entry.value))
                          : formatCurrency(Number(entry.value))
                      }
                    />
                  }
                />
                <Bar yAxisId="money" dataKey="revenue" name={t("adsReport.revenueHeader", "Doanh thu")} fill={CHART_COLORS.blue} radius={[4, 4, 0, 0]} />
                <Bar yAxisId="money" dataKey="cost" name={t("adsReport.costHeader", "Chi phí")} fill={CHART_COLORS.amber} radius={[4, 4, 0, 0]} />
                <Line yAxisId="ratio" type="monotone" dataKey="ratioPct" name={t("adsReport.ratioHeader", "% chi phí / doanh thu")} stroke={CHART_COLORS.red} strokeWidth={2} dot={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
            <CollapsibleTable label={t("dashboard.showDetailTable", "Xem bảng chi tiết")}>
              <DailyBreakdownTable rows={rows} groupByMonth={groupByMonth} bare />
            </CollapsibleTable>
          </>
        )}
      </CardContent>
    </Card>
  );
}
```

Also add the `bare` prop to `DailyBreakdownTable` per the Prerequisite above, e.g.:

```tsx
// DailyBreakdownTable.tsx — signature + early bare return
type DailyBreakdownTableProps = { rows: DailyBreakdownRow[]; groupByMonth: boolean; bare?: boolean };

export function DailyBreakdownTable({ rows, groupByMonth, bare = false }: DailyBreakdownTableProps) {
  // …existing hooks/memos unchanged…
  const body =
    rows.length === 0 ? (
      <p className="text-sm text-muted-foreground">{t("adsReport.empty", "Chưa có dữ liệu.")}</p>
    ) : (
      <div className="overflow-x-auto">{/* …existing table markup unchanged… */}</div>
    );
  if (bare) return body;
  return (
    <Card className="rounded-xl border-border bg-card">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{/* …existing title… */}</CardTitle>
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/ads/components/__tests__/DailyTrendChart.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/ads/components/DailyTrendChart.tsx frontend/src/features/ads/components/__tests__/DailyTrendChart.test.tsx
git commit -m "feat(ads): daily revenue/cost trend chart + detail table"
```

---

### Task 6: `TopProductsChart` (bar + detail table)

**Files:**
- Create: `frontend/src/features/ads/components/TopProductsChart.tsx`
- Test: `frontend/src/features/ads/components/__tests__/TopProductsChart.test.tsx`

**Interfaces:**
- Consumes: `TopProductRow`, `buildTopProductsChartData` (Task 2); `formatAdsCurrency` from `../report-grouping`; `CollapsibleTable`; recharts; `CHART_COLORS`; `Card*`, `Button`, `Select*`; `useI18n`.
- Produces: `TopProductsChart({ topProducts: TopProductRow[] }): JSX.Element`.

Const `TOP_PRODUCTS_CHART_LIMIT = 15`. Chart: horizontal `BarChart` of `buildTopProductsChartData(topProducts, 15)` by `revenue` (`CHART_COLORS.green`). Detail: `CollapsibleTable` wrapping the existing top-products `<table>` + pagination markup moved from `frontend/src/pages/AdsReportPage.tsx` lines 444–561, together with its state moved from the page: `expandedProducts` (line 86), `topProductsPage`/`topProductsPageSize` (lines 88–91), the page-clamp `useEffect` (lines 199–203), `topProductsTotalPages`/`topProductsPageStart`/`pagedTopProducts` (lines 198–208), and constants `TOP_PRODUCTS_PAGE_SIZE_OPTIONS`/`TOP_PRODUCTS_DEFAULT_PAGE_SIZE` (lines 74–75). Bodies unchanged; input becomes the `topProducts` prop.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/features/ads/components/__tests__/TopProductsChart.test.tsx
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { I18nProvider } from "@/i18n";
import { TopProductsChart } from "../TopProductsChart";
import type { TopProductRow } from "../../ads-report-selectors";

afterEach(cleanup);

const PRODUCTS: TopProductRow[] = [
  { product_id: 1, product__name: "P1", product__sku: "SKU1", closed_orders: 9, revenue: "900", channels: [] },
];

describe("TopProductsChart", () => {
  it("renders the SKU in the detail table", () => {
    render(
      <I18nProvider>
        <TopProductsChart topProducts={PRODUCTS} />
      </I18nProvider>,
    );
    expect(screen.getByText("SKU1")).toBeInTheDocument();
    expect(screen.getByText("Xem bảng chi tiết")).toBeInTheDocument();
  });

  it("shows the empty state without products", () => {
    render(
      <I18nProvider>
        <TopProductsChart topProducts={[]} />
      </I18nProvider>,
    );
    expect(screen.getByText("Chưa có dữ liệu.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/ads/components/__tests__/TopProductsChart.test.tsx`
Expected: FAIL — cannot resolve `../TopProductsChart`.

- [ ] **Step 3: Write minimal implementation**

Scaffold (chart + empty state + Card title "Sản phẩm bán chạy") mirroring Task 4's structure; move the top-products table + pagination + state as listed above into the `CollapsibleTable`. Chart:

```tsx
// key parts of frontend/src/features/ads/components/TopProductsChart.tsx
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CHART_COLORS } from "@/features/dashboard/dashboard-constants";
import { ChartTooltip } from "@/features/dashboard/components/DashboardPrimitives";
import { buildTopProductsChartData, type TopProductRow } from "../ads-report-selectors";
import { formatAdsCurrency as formatCurrency } from "../report-grouping";

const TOP_PRODUCTS_CHART_LIMIT = 15;
// …chartData = buildTopProductsChartData(topProducts, TOP_PRODUCTS_CHART_LIMIT)…
// horizontal BarChart on dataKey="revenue", Cell fill={CHART_COLORS.green},
// YAxis type="category" dataKey="name" width={140}, XAxis tickFormatter formatCurrency,
// Tooltip formatter -> `${formatCurrency(value)} · ${closedOrders} đơn`.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/ads/components/__tests__/TopProductsChart.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/ads/components/TopProductsChart.tsx frontend/src/features/ads/components/__tests__/TopProductsChart.test.tsx
git commit -m "feat(ads): top-products chart + detail table"
```

---

### Task 7: `ChannelDailyChart` (admin stacked bar + detail table)

**Files:**
- Create: `frontend/src/features/ads/components/ChannelDailyChart.tsx`
- Test: `frontend/src/features/ads/components/__tests__/ChannelDailyChart.test.tsx`
- Modify: `frontend/src/features/ads/ads-report-selectors.ts` (add `buildChannelStackChartData`)
- Modify test: `frontend/src/features/ads/__tests__/ads-report-selectors.test.ts` (cover the new selector)
- Modify: `frontend/src/features/ads/components/ChannelDailyTable.tsx` (add optional `bare` prop, same pattern as Task 5's `DailyBreakdownTable`: `bare?: boolean` default `false`; when `true` skip the outer `Card`/`CardHeader`/`CardContent` and return the inner content only, keeping existing tests green)

**Interfaces:**
- Add to selectors: `buildChannelStackChartData(dailyRows: DailyBreakdownRow[], groupByMonth: boolean): { data: Array<Record<string, string | number>>; series: { key: string; name: string }[] }`. Each `data` entry has `label` plus one numeric key per channel (`ch_<channelId>` = revenue); `series` lists those keys with display names. When `groupByMonth`, aggregate per month by summing each channel's daily `revenue`; label via `formatMonthLabel`.
- Produces: `ChannelDailyChart({ dailyRows: DailyBreakdownRow[]; dateFrom?: string; dateTo?: string; groupByMonth: boolean }): JSX.Element`. Renders a stacked `BarChart` (one `<Bar stackId="rev">` per series, colors cycled from `ENTITY_PALETTE`) + `CollapsibleTable` wrapping `<ChannelDailyTable dailyRows dateFrom dateTo groupByMonth />`.

- [ ] **Step 1: Write the failing selector test (append to existing file)**

```ts
// append to frontend/src/features/ads/__tests__/ads-report-selectors.test.ts
import { buildChannelStackChartData } from "../ads-report-selectors";

describe("buildChannelStackChartData", () => {
  it("emits one numeric key per channel and per-day labels", () => {
    const rows = [
      { date: "2026-01-10", revenue: "1000", cost: "100", closed_orders: 2, ratio_pct: 10,
        channels: [{ channel_id: 1, channel__code: "fb-1", channel__name: "Kênh A", revenue: "1000", cost: "100", closed_orders: 2, ratio_pct: 10 }] },
    ];
    const { data, series } = buildChannelStackChartData(rows, false);
    expect(series).toEqual([{ key: "ch_1", name: "Kênh A" }]);
    expect(data[0]).toMatchObject({ label: "2026-01-10", ch_1: 1000 });
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/features/ads/__tests__/ads-report-selectors.test.ts`
Expected: FAIL — `buildChannelStackChartData` not exported.

- [ ] **Step 3: Implement the selector**

```ts
// append to frontend/src/features/ads/ads-report-selectors.ts
export type ChannelStackChart = {
  data: Array<Record<string, string | number>>;
  series: { key: string; name: string }[];
};

export function buildChannelStackChartData(
  dailyRows: readonly DailyBreakdownRow[],
  groupByMonth: boolean,
): ChannelStackChart {
  const names = new Map<number, string>();
  const buckets = new Map<string, Map<number, number>>();
  for (const row of dailyRows) {
    const label = groupByMonth ? formatMonthLabel(row.date.slice(0, 7)) : row.date;
    const bucket = buckets.get(label) ?? new Map<number, number>();
    for (const ch of row.channels) {
      if (ch.channel_id == null) continue;
      names.set(ch.channel_id, ch.channel__name || ch.channel__code || `#${ch.channel_id}`);
      bucket.set(ch.channel_id, (bucket.get(ch.channel_id) ?? 0) + Number(ch.revenue || 0));
    }
    buckets.set(label, bucket);
  }
  const series = Array.from(names, ([id, name]) => ({ key: `ch_${id}`, name }));
  const data = Array.from(buckets, ([label, bucket]) => {
    const entry: Record<string, string | number> = { label };
    for (const [id, revenue] of bucket) entry[`ch_${id}`] = revenue;
    for (const s of series) if (!(s.key in entry)) entry[s.key] = 0;
    return entry;
  });
  return { data, series };
}
```

- [ ] **Step 4: Write the component test**

```tsx
// frontend/src/features/ads/components/__tests__/ChannelDailyChart.test.tsx
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { I18nProvider } from "@/i18n";
import { ChannelDailyChart } from "../ChannelDailyChart";
import type { DailyBreakdownRow } from "../../report-grouping";

afterEach(cleanup);

const ROWS: DailyBreakdownRow[] = [
  { date: "2026-01-10", revenue: "1000", cost: "100", closed_orders: 2, ratio_pct: 10,
    channels: [{ channel_id: 1, channel__code: "fb-1", channel__name: "Kênh A", revenue: "1000", cost: "100", closed_orders: 2, ratio_pct: 10 }] },
];

describe("ChannelDailyChart", () => {
  it("renders the detail toggle for admin pivot", () => {
    render(
      <I18nProvider>
        <ChannelDailyChart dailyRows={ROWS} dateFrom="2026-01-01" dateTo="2026-01-31" groupByMonth={false} />
      </I18nProvider>,
    );
    expect(screen.getByText("Xem bảng chi tiết")).toBeInTheDocument();
  });
});
```

- [ ] **Step 5: Run to verify it fails**

Run: `cd frontend && npx vitest run src/features/ads/components/__tests__/ChannelDailyChart.test.tsx`
Expected: FAIL — cannot resolve `../ChannelDailyChart`.

- [ ] **Step 6: Implement the component**

```tsx
// frontend/src/features/ads/components/ChannelDailyChart.tsx
import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CollapsibleTable } from "@/components/report/CollapsibleTable";
import { ChartTooltip } from "@/features/dashboard/components/DashboardPrimitives";
import { ENTITY_PALETTE } from "@/features/dashboard/dashboard-constants";
import { useI18n } from "@/i18n";
import { buildChannelStackChartData } from "../ads-report-selectors";
import { type DailyBreakdownRow, formatAdsCurrency as formatCurrency } from "../report-grouping";
import { ChannelDailyTable } from "./ChannelDailyTable";

export function ChannelDailyChart({
  dailyRows,
  dateFrom,
  dateTo,
  groupByMonth,
}: {
  dailyRows: DailyBreakdownRow[];
  dateFrom?: string;
  dateTo?: string;
  groupByMonth: boolean;
}) {
  const { t } = useI18n();
  const { data, series } = useMemo(
    () => buildChannelStackChartData(dailyRows, groupByMonth),
    [dailyRows, groupByMonth],
  );
  return (
    <Card className="rounded-xl border-border bg-card">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">
          {t("adsReport.channelDailyTitle", "Doanh thu theo kênh & thời gian")}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {dailyRows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("adsReport.empty", "Chưa có dữ liệu.")}</p>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={320}>
              <BarChart data={data} margin={{ top: 16, right: 16, left: 8, bottom: 8 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" strokeOpacity={0.55} vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} stroke="var(--border)" tickLine={false} axisLine={false} />
                <YAxis tickFormatter={(v) => formatCurrency(Number(v))} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} stroke="var(--border)" tickLine={false} axisLine={false} width={70} />
                <Tooltip content={<ChartTooltip formatter={(entry) => formatCurrency(Number(entry.value))} />} />
                {series.map((s, idx) => (
                  <Bar key={s.key} dataKey={s.key} name={s.name} stackId="rev" fill={ENTITY_PALETTE[idx % ENTITY_PALETTE.length]} />
                ))}
              </BarChart>
            </ResponsiveContainer>
            <CollapsibleTable label={t("dashboard.showDetailTable", "Xem bảng chi tiết")}>
              <ChannelDailyTable dailyRows={dailyRows} dateFrom={dateFrom} dateTo={dateTo} groupByMonth={groupByMonth} bare />
            </CollapsibleTable>
          </>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 7: Run both tests to verify they pass**

Run: `cd frontend && npx vitest run src/features/ads/__tests__/ads-report-selectors.test.ts src/features/ads/components/__tests__/ChannelDailyChart.test.tsx`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/ads/ads-report-selectors.ts frontend/src/features/ads/__tests__/ads-report-selectors.test.ts frontend/src/features/ads/components/ChannelDailyChart.tsx frontend/src/features/ads/components/__tests__/ChannelDailyChart.test.tsx
git commit -m "feat(ads): admin channel-by-day stacked chart + detail table"
```

---

### Task 8: Move + slim the page, rewire routing

**Files:**
- Create: `frontend/src/features/ads/pages/AdsReportPage.tsx`
- Delete: `frontend/src/pages/AdsReportPage.tsx`
- Modify: `frontend/src/app/routed-pages.tsx:30` (import path)

**Interfaces:**
- Consumes: `AdsKpiRow`, `RevenueByChannelChart`, `DailyTrendChart`, `TopProductsChart`, `ChannelDailyChart` (Tasks 3–7); `AdsReportPayload`, `computeAdsKpis` (Task 2); `useAdsReport`; `shouldGroupByMonth`; filter UI (`DateFilter`, `MultiSelect`, `Card*`, `PageHeader`).
- Produces: default export `AdsReportPage({ channels: ChannelRow[]; isAdmin?: boolean })` — same props/signature as today, so `AdsReportRoute` (routed-pages.tsx:446–449) needs no change beyond the import.

The new page keeps: filter card (DateFilter + channel MultiSelect + `channelOptions` memo, lines 110–123, 220–241), `useAdsReport` wiring + error/data handling (lines 93–108), `channelRows`/`topProducts`/`dailyRows` memos (lines 127–129), `groupByMonth` memo (lines 193–196). It drops every table/pagination/expand block now living in the chart components. Body:

```tsx
return (
  <div className="space-y-4">
    <PageHeader title={t("topnav.tabs.adsReport")} description={t("adsReport.hint", "…")} />
    <Card className="rounded-xl border-border bg-card">
      <CardContent className="space-y-3 pt-6">
        {/* DateFilter + channel MultiSelect (unchanged) */}
      </CardContent>
    </Card>
    {error && (/* error banner unchanged */)}
    <AdsKpiRow kpis={computeAdsKpis(channelRows, dailyRows)} />
    <RevenueByChannelChart channelRows={channelRows} topProducts={topProducts} dailyRows={dailyRows} />
    <DailyTrendChart rows={dailyRows} groupByMonth={groupByMonth} />
    <TopProductsChart topProducts={topProducts} />
    {isAdmin && (
      <ChannelDailyChart dailyRows={dailyRows} dateFrom={dateFilter.from} dateTo={dateFilter.to} groupByMonth={groupByMonth} />
    )}
  </div>
);
```

`AdsReportPayload` and the row types now live in `ads-report-selectors.ts`; import them from there (the page's local type declarations at lines 34–65 are deleted).

- [ ] **Step 1: Create the new page** at `frontend/src/features/ads/pages/AdsReportPage.tsx` per the composition above, importing types/selectors from `../ads-report-selectors` and the five block components from `../components/*`. Keep `default export`.

- [ ] **Step 2: Update the route import**

Change `frontend/src/app/routed-pages.tsx:30` from:
```ts
const AdsReportPage = lazy(() => import("@/pages/AdsReportPage"));
```
to:
```ts
const AdsReportPage = lazy(() => import("@/features/ads/pages/AdsReportPage"));
```

- [ ] **Step 3: Delete the old page**

```bash
git rm frontend/src/pages/AdsReportPage.tsx
```

- [ ] **Step 4: Typecheck + full ads test run + build**

Run: `cd frontend && npx tsc --noEmit && npx vitest run src/features/ads src/components/report && npm run build`
Expected: no type errors; all ads + report tests PASS; build succeeds. (If `npm run lint` is part of CI, also run `npm run lint` and fix any `no-restricted-imports`/unused-import issues.)

- [ ] **Step 5: Manual verification (per superpowers:verify)**

Run the app (`npm run dev` in `frontend/`, backend up), open the Ads Report page, and confirm: KPI row shows, each block shows a chart, "Xem bảng chi tiết" expands the original table with identical numbers, top-products pagination still works, admin sees the channel×day block, and a long (quarter/year) range switches charts + tables to monthly grouping.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/ads/pages/AdsReportPage.tsx frontend/src/app/routed-pages.tsx
git commit -m "feat(ads): rebuild ads report as charts with drilldown detail"
```

---

## Self-Review

**Spec coverage:**
- Chart-on-top + "Xem bảng chi tiết" drill-down (spec §1, §6) → Tasks 4–7 (chart + `CollapsibleTable`).
- KPI tile row (spec §5) → Task 3 + `computeAdsKpis` (Task 2).
- 4 chart mappings (spec §6): bar by channel → Task 4; composed daily trend → Task 5; top-products bar → Task 6; admin stacked channel×day → Task 7.
- No backend/API/RBAC change (spec §3, §8) → all tasks read the existing payload only.
- Keep React Query `useAdsReport` (spec §3) → Task 8 reuses it.
- Reuse monthly grouping (spec §3) → `groupByMonth` threaded to Tasks 5 & 7; selectors reuse `groupDailyRowsByMonth`/`formatMonthLabel`.
- File move to `features/ads/pages/` (spec §2, §4) → Task 8.
- Lift `CollapsibleTable`, don't refactor Dashboard (spec §4, §9) → Task 1 (new shared copy; dashboard untouched).
- Preserve expand/pagination behavior (spec §9) → Tasks 4 & 6 move the exact markup/state verbatim.
- Tests (spec §7) → selectors (Task 2/7), each component smoke test, CollapsibleTable (Task 1).

**Placeholder scan:** Task 4 and Task 6 reference "move verbatim" of existing markup with exact source line ranges rather than re-pasting ~150 lines — this is a mechanical move of code that already exists in the repo, with the exact source location and wrapping specified, not an unspecified "implement later". All new-from-scratch code is shown in full.

**Type consistency:** `AdsKpis`, `ChannelRevenueRow`, `TopProductRow`, `AdsReportPayload`, `ChannelRevenueDatum`, `TrendDatum`, `TopProductDatum`, `ChannelStackChart` are defined once in Task 2/7 and imported unchanged by Tasks 3–8. Component prop names (`channelRows`, `topProducts`, `dailyRows`, `rows`, `groupByMonth`, `dateFrom`, `dateTo`) match between producer interface blocks and the Task 8 call sites. `ChannelDailyTable`/`DailyBreakdownTable` props match their current signatures (`dailyRows/dateFrom/dateTo/groupByMonth` and `rows/groupByMonth`).
