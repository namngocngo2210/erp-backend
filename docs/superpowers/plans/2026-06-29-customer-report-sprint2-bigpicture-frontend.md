# Customer Report — Sprint 2 (Bức tranh toàn cảnh) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Phụ thuộc:** Sprint 1 phải xong (data contract đã có `funnel`, `cohort_retention`, `data_coverage`, `insights` trong response).

**Goal:** Biến trang báo cáo từ "atlas of distributions" thành trang có **bức tranh toàn cảnh**: khối Tổng quan điều hành (phễu vòng đời hợp nhất + thẻ insight + badge độ phủ), cohort retention, và drill-down từ báo cáo sang danh sách khách hàng đã lọc.

**Architecture:** Thêm các component trình bày thuần (presentational) trong `frontend/src/components/report/`, lắp vào `CustomerReportPage`. Drill-down = mở `/crm/customers?segment=&channel=` qua URL param; `useCustomersData` seed filter ban đầu từ URL.

**Tech Stack:** React + TypeScript, shadcn/ui, recharts (đã có), lucide-react, react-router-dom, vitest.

## Global Constraints

- Branch cố định: `feat/customer-report-redesign`. KHÔNG tạo branch mới. Commit mỗi task.
- Component thuần trình bày, không nhét business logic; gọi API qua `@/lib/api/customers`.
- Light theme, tông xanh da trời (`#3b82f6` ...), khớp `CHART_COLORS` hiện có.
- Tách rõ chỉ số RFM (tin được) vs assessment (badge độ phủ) — không để lẫn gây hiểu nhầm.
- Tất cả type lấy từ contract đã đóng băng ở Sprint 1 Task 10 (`CustomerReportSummary.funnel|insights|cohort_retention|data_coverage`).
- FE test bằng vitest (`npm run test`), build type-check `npm run build` phải xanh.

## File Structure

| File | Trách nhiệm |
|---|---|
| `frontend/src/components/report/LifecycleFunnel.tsx` | **mới** — phễu vòng đời hợp nhất (bar-funnel + % chuyển đổi) |
| `frontend/src/components/report/InsightCards.tsx` | **mới** — thẻ insight (warn/info/ok) + nút CTA drill-down |
| `frontend/src/components/report/DataCoverageBadge.tsx` | **mới** — badge "% đơn có đánh giá" |
| `frontend/src/components/report/CohortHeatmap.tsx` | **mới** — heatmap retention theo tháng |
| `frontend/src/pages/CustomerReportPage.tsx` | lắp khối Tổng quan điều hành + tab Cohort |
| `frontend/src/features/customers/use-customers-data.ts` | seed `segment`/`channel` ban đầu từ URL search param |
| `frontend/src/lib/reportLinks.ts` | **mới** — helper build URL drill-down từ `cta.params` |

---

### Task 1: `DataCoverageBadge` — phân lớp độ tin dữ liệu

**Files:**
- Create: `frontend/src/components/report/DataCoverageBadge.tsx`
- Test: `frontend/src/components/report/__tests__/DataCoverageBadge.test.tsx`

**Interfaces:**
- Produces: `DataCoverageBadge({ coverage }: { coverage?: { assessment_orders: number; total_orders: number; pct: number } })`
  — render "Dựa trên N/M đơn có đánh giá (X%)"; null coverage → render null.

- [ ] **Step 1: Viết test thất bại**

```tsx
import { render, screen } from "@testing-library/react";
import { DataCoverageBadge } from "../DataCoverageBadge";

it("renders coverage text", () => {
  render(<DataCoverageBadge coverage={{ assessment_orders: 11292, total_orders: 513514, pct: 2.2 }} />);
  expect(screen.getByText(/2\.2%/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd frontend && npm run test -- DataCoverageBadge`
Expected: FAIL (module không tồn tại).

- [ ] **Step 3: Cài đặt component**

```tsx
interface Props {
  coverage?: { assessment_orders: number; total_orders: number; pct: number };
}

export function DataCoverageBadge({ coverage }: Props) {
  if (!coverage) return null;
  const fmt = (n: number) => n.toLocaleString("vi-VN");
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2.5 py-1 text-xs text-amber-700">
      Dựa trên {fmt(coverage.assessment_orders)}/{fmt(coverage.total_orders)} đơn có đánh giá ({coverage.pct}%)
    </span>
  );
}
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `cd frontend && npm run test -- DataCoverageBadge`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/report/DataCoverageBadge.tsx frontend/src/components/report/__tests__/DataCoverageBadge.test.tsx
git commit -m "feat(report): data coverage badge"
```

---

### Task 2: `LifecycleFunnel` — phễu vòng đời hợp nhất

**Files:**
- Create: `frontend/src/components/report/LifecycleFunnel.tsx`
- Test: `frontend/src/components/report/__tests__/LifecycleFunnel.test.tsx`

**Interfaces:**
- Consumes: `ReportFunnelStage[]` (từ contract): `{ key, label, count, conv_from_prev_pct }`.
- Produces: `LifecycleFunnel({ stages }: { stages: ReportFunnelStage[] })` — mỗi stage 1 thanh ngang co theo `count` so với stage đầu, hiển thị `count` + `conv_from_prev_pct` khi có. stages rỗng → "Không có dữ liệu".

- [ ] **Step 1: Viết test thất bại**

```tsx
import { render, screen } from "@testing-library/react";
import { LifecycleFunnel } from "../LifecycleFunnel";

it("renders stage labels and conversion", () => {
  render(<LifecycleFunnel stages={[
    { key: "total_customers", label: "Tổng KH", count: 1000, conv_from_prev_pct: null },
    { key: "buyers", label: "Người mua", count: 400, conv_from_prev_pct: 40 },
  ]} />);
  expect(screen.getByText("Người mua")).toBeInTheDocument();
  expect(screen.getByText(/40%/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd frontend && npm run test -- LifecycleFunnel`

- [ ] **Step 3: Cài đặt component** (bar-funnel thuần CSS, không cần recharts)

```tsx
import type { ReportFunnelStage } from "@/lib/api/customers";

const fmt = (n: number) => n.toLocaleString("vi-VN");

export function LifecycleFunnel({ stages }: { stages: ReportFunnelStage[] }) {
  if (!stages.length) {
    return <p className="py-12 text-center text-sm text-muted-foreground">Không có dữ liệu</p>;
  }
  const max = Math.max(...stages.map((s) => s.count), 1);
  return (
    <div className="space-y-2">
      {stages.map((s) => {
        const width = Math.max((s.count / max) * 100, 2);
        return (
          <div key={s.key} className="flex items-center gap-3">
            <div className="w-32 shrink-0 text-sm text-muted-foreground">{s.label}</div>
            <div className="relative h-9 flex-1 rounded-md bg-muted">
              <div
                className="flex h-9 items-center rounded-md bg-primary/80 px-2 text-sm font-medium text-primary-foreground"
                style={{ width: `${width}%` }}
              >
                {fmt(s.count)}
              </div>
            </div>
            <div className="w-16 shrink-0 text-right text-xs text-muted-foreground">
              {s.conv_from_prev_pct != null ? `${s.conv_from_prev_pct}%` : ""}
            </div>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `cd frontend && npm run test -- LifecycleFunnel`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/report/LifecycleFunnel.tsx frontend/src/components/report/__tests__/LifecycleFunnel.test.tsx
git commit -m "feat(report): unified lifecycle funnel component"
```

---

### Task 3: `reportLinks` helper + `InsightCards`

**Files:**
- Create: `frontend/src/lib/reportLinks.ts`
- Create: `frontend/src/components/report/InsightCards.tsx`
- Test: `frontend/src/lib/__tests__/reportLinks.test.ts`, `frontend/src/components/report/__tests__/InsightCards.test.tsx`

**Interfaces:**
- Produces:
  - `buildCustomerListHref(params: Record<string, string>): string` → `/crm/customers?segment=...&channel=...` (chỉ giữ key được phép: `segment`, `channel`).
  - `InsightCards({ insights }: { insights: ReportInsight[] })` — thẻ màu theo level; nếu `cta?.type === "customer_list"` render `<Link>` tới `buildCustomerListHref(cta.params)`.

- [ ] **Step 1: Viết test thất bại cho helper**

```ts
import { buildCustomerListHref } from "../reportLinks";

it("builds whitelisted customer list href", () => {
  expect(buildCustomerListHref({ segment: "S3", channel: "5", evil: "x" }))
    .toBe("/crm/customers?segment=S3&channel=5");
});
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd frontend && npm run test -- reportLinks`

- [ ] **Step 3: Cài đặt helper**

```ts
const ALLOWED = ["segment", "channel"] as const;

export function buildCustomerListHref(params: Record<string, string>): string {
  const sp = new URLSearchParams();
  for (const key of ALLOWED) {
    if (params[key]) sp.set(key, params[key]);
  }
  const qs = sp.toString();
  return `/crm/customers${qs ? `?${qs}` : ""}`;
}
```

- [ ] **Step 4: Cài đặt `InsightCards`**

```tsx
import { Link } from "react-router-dom";
import type { ReportInsight } from "@/lib/api/customers";
import { buildCustomerListHref } from "@/lib/reportLinks";

const TONE: Record<ReportInsight["level"], string> = {
  warn: "border-red-200 bg-red-50 text-red-800",
  info: "border-blue-200 bg-blue-50 text-blue-800",
  ok: "border-green-200 bg-green-50 text-green-800",
};

export function InsightCards({ insights }: { insights: ReportInsight[] }) {
  if (!insights.length) return null;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {insights.map((it, i) => (
        <div key={i} className={`rounded-lg border p-3 ${TONE[it.level]}`}>
          <div className="text-sm font-semibold">{it.title}</div>
          <div className="mt-1 text-xs opacity-80">{it.metric}</div>
          {it.cta?.type === "customer_list" && (
            <Link to={buildCustomerListHref(it.cta.params)} className="mt-2 inline-block text-xs underline">
              → Xem danh sách
            </Link>
          )}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 5: Viết + chạy test `InsightCards`** (render warn + link)

```tsx
import { MemoryRouter } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import { InsightCards } from "../InsightCards";

it("renders insight with drill-down link", () => {
  render(<MemoryRouter><InsightCards insights={[
    { level: "warn", title: "Ngủ đông cao", metric: "92%", cta: { type: "customer_list", params: { segment: "S6" } } },
  ]} /></MemoryRouter>);
  expect(screen.getByText("Ngủ đông cao")).toBeInTheDocument();
  expect(screen.getByRole("link")).toHaveAttribute("href", "/crm/customers?segment=S6");
});
```

Run: `cd frontend && npm run test -- reportLinks InsightCards`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/reportLinks.ts frontend/src/components/report/InsightCards.tsx frontend/src/lib/__tests__/reportLinks.test.ts frontend/src/components/report/__tests__/InsightCards.test.tsx
git commit -m "feat(report): insight cards + drill-down link helper"
```

---

### Task 4: `CohortHeatmap`

**Files:**
- Create: `frontend/src/components/report/CohortHeatmap.tsx`
- Test: `frontend/src/components/report/__tests__/CohortHeatmap.test.tsx`

**Interfaces:**
- Consumes: `CohortRow[]`: `{ cohort: string; size: number; retained: number[] }`.
- Produces: `CohortHeatmap({ rows }: { rows: CohortRow[] })` — bảng: cột trái cohort + size, các cột M+1.. tô màu theo tỉ lệ `retained[k]/size`. rows rỗng → "Không có dữ liệu".

- [ ] **Step 1: Viết test thất bại**

```tsx
import { render, screen } from "@testing-library/react";
import { CohortHeatmap } from "../CohortHeatmap";

it("renders cohort row with retention pct", () => {
  render(<CohortHeatmap rows={[{ cohort: "2026-01", size: 100, retained: [20, 10] }]} />);
  expect(screen.getByText("2026-01")).toBeInTheDocument();
  expect(screen.getByText(/20%/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Chạy test, xác nhận FAIL**

Run: `cd frontend && npm run test -- CohortHeatmap`

- [ ] **Step 3: Cài đặt component**

```tsx
import type { CohortRow } from "@/lib/api/customers";

function cell(retained: number, size: number) {
  const pct = size ? Math.round((retained / size) * 100) : 0;
  const bg = pct === 0 ? "transparent" : `rgba(59,130,246,${Math.min(pct / 100 + 0.1, 1)})`;
  return { pct, bg };
}

export function CohortHeatmap({ rows }: { rows: CohortRow[] }) {
  if (!rows.length) {
    return <p className="py-12 text-center text-sm text-muted-foreground">Không có dữ liệu</p>;
  }
  const maxLen = Math.max(...rows.map((r) => r.retained.length));
  return (
    <div className="overflow-x-auto">
      <table className="text-xs">
        <thead>
          <tr className="text-muted-foreground">
            <th className="px-2 py-1 text-left">Cohort</th>
            <th className="px-2 py-1 text-right">Size</th>
            {Array.from({ length: maxLen }, (_, k) => (
              <th key={k} className="px-2 py-1 text-center">M+{k + 1}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.cohort}>
              <td className="px-2 py-1">{r.cohort}</td>
              <td className="px-2 py-1 text-right">{r.size.toLocaleString("vi-VN")}</td>
              {r.retained.map((v, k) => {
                const c = cell(v, r.size);
                return (
                  <td key={k} className="px-2 py-1 text-center" style={{ background: c.bg }}>
                    {c.pct ? `${c.pct}%` : ""}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 4: Chạy test, xác nhận PASS**

Run: `cd frontend && npm run test -- CohortHeatmap`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/report/CohortHeatmap.tsx frontend/src/components/report/__tests__/CohortHeatmap.test.tsx
git commit -m "feat(report): cohort retention heatmap"
```

---

### Task 5: Lắp khối "Tổng quan điều hành" + tab Cohort vào trang

**Files:**
- Modify: `frontend/src/pages/CustomerReportPage.tsx`

**Interfaces:**
- Consumes: `summary.funnel`, `summary.insights`, `summary.data_coverage`, `summary.cohort_retention` (đã có trong type từ Sprint 1).

- [ ] **Step 1: Import các component mới** (đầu file)

```tsx
import { LifecycleFunnel } from "@/components/report/LifecycleFunnel";
import { InsightCards } from "@/components/report/InsightCards";
import { DataCoverageBadge } from "@/components/report/DataCoverageBadge";
import { CohortHeatmap } from "@/components/report/CohortHeatmap";
```

- [ ] **Step 2: Đưa khối điều hành lên ĐẦU tab "overview"** (trước `OverviewKpis`)

```tsx
      content: (
        <div className="space-y-6 py-4">
          {summary?.insights && <InsightCards insights={summary.insights} />}
          <ReportSection title="Phễu vòng đời khách hàng">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between">
                <CardTitle className="text-base">Lead → Mua → Mua lại → Trung thành</CardTitle>
                <DataCoverageBadge coverage={summary?.data_coverage} />
              </CardHeader>
              <CardContent>
                <LifecycleFunnel stages={summary?.funnel?.stages ?? []} />
              </CardContent>
            </Card>
          </ReportSection>
          {summary?.overview && <OverviewKpis ov={summary.overview} />}
          {/* ... giữ nguyên 2 thẻ totals cũ ... */}
        </div>
      ),
```

- [ ] **Step 3: Thêm tab "cohort"** vào `tabItems` (sau "overview")

```tsx
    {
      key: "cohort",
      label: "Giữ chân (Cohort)",
      content: (
        <div className="space-y-6 py-4">
          <ReportSection title="Tỷ lệ quay lại theo tháng mua đầu">
            <Card>
              <CardContent className="pt-6">
                <CohortHeatmap rows={summary?.cohort_retention?.rows ?? []} />
              </CardContent>
            </Card>
          </ReportSection>
        </div>
      ),
    },
```

- [ ] **Step 4: Build + type-check**

Run: `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/CustomerReportPage.tsx
git commit -m "feat(report): executive overview block + cohort tab"
```

---

### Task 6: Drill-down — `useCustomersData` seed filter từ URL

**Files:**
- Modify: `frontend/src/features/customers/use-customers-data.ts` (đọc trước file để biết vị trí `useState` của `segment`/`channel`)
- Modify: `frontend/src/pages/CustomerReportPage.tsx` (đổi CTA cũ "Mở danh sách khách hàng" sang link có param theo `segment`/`channel` đang chọn)
- Test: `frontend/src/features/customers/__tests__/use-customers-data-url.test.tsx` (nếu hook test khả thi) hoặc test helper.

**Interfaces:**
- Behaviour: khi mount, nếu URL có `?segment=` / `?channel=`, hook khởi tạo state filter tương ứng (đọc 1 lần bằng `useSearchParams` / `URLSearchParams(window.location.search)`).

- [ ] **Step 1: Đọc `use-customers-data.ts`**, tìm `const [segment, setSegment] = useState(...)` và `channel`.

Run: mở file, xác định giá trị khởi tạo hiện tại.

- [ ] **Step 2: Seed từ URL** — đổi khởi tạo state sang đọc search param một lần

```tsx
// đầu hook
const initialParams = new URLSearchParams(window.location.search);
// ...
const [segment, setSegment] = useState(initialParams.get("segment") ?? "");
const [channel, setChannel] = useState(initialParams.get("channel") ?? "");
```

- [ ] **Step 3: Đổi CTA trong CustomerReportPage** (vùng comment "list page does not support URL-param")

```tsx
          <div className="flex justify-end">
            <Link
              to={`/crm/customers${
                segment || channel
                  ? `?${new URLSearchParams({ ...(segment && { segment }), ...(channel && { channel }) }).toString()}`
                  : ""
              }`}
              className="text-sm text-primary underline"
            >
              → Mở danh sách khách hàng (theo bộ lọc hiện tại)
            </Link>
          </div>
```
(Gỡ comment TODO cũ.)

- [ ] **Step 4: Viết test** — render `CustomersPage` trong `MemoryRouter` với `initialEntries={["/crm/customers?segment=S3"]}` và assert toolbar nhận `segment="S3"` (nếu khó test hook trực tiếp, test `buildCustomerListHref` đã phủ phần build link; tối thiểu test seed bằng cách mock `window.location.search`).

```tsx
it("seeds segment filter from url", () => {
  // tuỳ khả năng test của hook; nếu hook đọc window.location.search, set trước khi render
  const original = window.location.search;
  Object.defineProperty(window, "location", { value: { search: "?segment=S3" }, writable: true });
  // render component dùng hook, assert giá trị segment hiển thị trên toolbar...
  Object.defineProperty(window, "location", { value: { search: original }, writable: true });
});
```

> Nếu hook khó test cô lập, chấp nhận test ở mức helper + manual verify; ghi rõ trong commit.

- [ ] **Step 5: Build + test**

Run: `cd frontend && npm run build && npm run test -- use-customers-data-url reportLinks`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/customers/use-customers-data.ts frontend/src/pages/CustomerReportPage.tsx frontend/src/features/customers/__tests__/
git commit -m "feat(customers): drill-down via url params from report"
```

---

### Task 7: Phân lớp độ tin — gắn badge vào các block assessment

**Files:**
- Modify: `frontend/src/pages/CustomerReportPage.tsx` (tab "post"/"pre": thêm `DataCoverageBadge` hoặc note ở đầu các ReportSection dựa trên assessment)

**Interfaces:**
- Behaviour: các ReportSection persona/sức khỏe/nguồn (dữ liệu assessment) hiển thị badge độ phủ để người đọc không nhầm là toàn cảnh.

- [ ] **Step 1: Thêm badge vào đầu tab "post"** (và "pre") — ngay dưới tiêu đề tab

```tsx
        <div className="space-y-6 py-4">
          <div className="flex justify-end"><DataCoverageBadge coverage={summary?.data_coverage} /></div>
          {/* ... nội dung tab giữ nguyên ... */}
```

- [ ] **Step 2: Build + type-check**

Run: `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/CustomerReportPage.tsx
git commit -m "feat(report): data-coverage badge on assessment-based tabs"
```

---

## Self-Review (Sprint 2)

- Coverage: B1→Task2+5, B2→Task4+5, B3→Task3+6, B4→Task1+7. Tất cả khớp doc thiết kế mục 4.
- Type consistency: `ReportFunnelStage`, `ReportInsight`, `CohortRow` dùng từ contract Sprint 1 Task 10; `buildCustomerListHref` whitelist `segment`/`channel` khớp filter `useCustomersData`.
- Lifecycle drill-down: hiện `CustomersPage` chỉ có filter `segment`/`channel` → CTA dùng 2 param này. Insight có `cta.params.lifecycle` (từ BE) sẽ bị whitelist bỏ qua một cách an toàn; nếu cần drill-down theo lifecycle, mở task follow-up thêm filter lifecycle (ngoài phạm vi Sprint 2).
- Placeholder: mọi step có code/lệnh cụ thể; Task 6 step test ghi rõ phương án dự phòng.

## Verify thủ công (cuối Sprint 2)
- Mở `/crm/customers/report`: thấy insight + phễu ở đầu, badge độ phủ, tab Cohort có heatmap.
- Click "→ Xem danh sách" trên 1 insight → mở `/crm/customers` đã lọc đúng segment.
- Screenshot trước/sau đính kèm MR.
