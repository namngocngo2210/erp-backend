# Báo cáo Ads dạng Chart — Thiết kế

- **Ngày**: 2026-06-24
- **Branch**: feat/sales-warranty-claims
- **Trạng thái**: Đã duyệt thiết kế, chờ review spec → lập plan

## 1. Mục tiêu

Bổ sung biểu đồ (chart) cho trang Báo cáo Ads để trực quan hóa số liệu thay vì
chỉ xem bảng. Trục thời gian tự thay đổi theo khoảng lọc:

- Chọn preset theo **tháng** → trục hiển thị các **ngày** trong tháng.
- Chọn preset theo **năm/quý** → trục gộp và hiển thị theo **tháng**.

## 2. Quyết định đã chốt (brainstorming)

1. **Hiển thị**: Thêm chart nhưng **giữ bảng** — mỗi card có toggle `Chart ⇄ Bảng`,
   mặc định hiển thị Chart.
2. **Mức gộp theo preset**:
   - preset ngày/tuần/tháng → trục **ngày**.
   - preset quý/năm → trục **tháng**.
   - custom range (không có preset) → theo độ dài: `> 62 ngày` → tháng, ngược lại → ngày.
3. **Phạm vi**: Cả 4 phần đều có chart.

## 3. Nguyên tắc & ràng buộc

- **Không thêm thư viện mới**: tái sử dụng `recharts` đã có trong dự án, theo pattern
  tại `frontend/src/features/dashboard/components/DashboardCharts.tsx` và
  `frontend/src/components/report/StackedBarChart.tsx`.
- **Không đổi backend**: việc gộp ngày → tháng thực hiện ở frontend. Dữ liệu tối đa
  ~365 dòng/kỳ nên gộp client-side dư sức, tránh rủi ro sửa API + RBAC.
- **Tách file**: `frontend/src/pages/AdsReportPage.tsx` hiện ~957 dòng, vượt mốc 300
  dòng trong CLAUDE.md. Refactor tách logic ra `features/ads-report/`; page chỉ điều phối.
- Light theme, tông xanh da trời; dùng màu/format tiền tệ sẵn có
  (`formatRevenueShort`, `formatCurrency`).

## 4. Cấu trúc thư mục mới

```
frontend/src/features/ads-report/
  hooks/useAdsReport.ts          # tách fetch + polling + visibilitychange + state khỏi page
  lib/granularity.ts             # resolveGranularity() + groupDailyByMonth()
  components/
    ReportSectionCard.tsx        # Card + toggle Chart<->Bảng (presentational, tái dùng)
    RevenueCostTimeChart.tsx     # ComposedChart: bar DT + bar CP + line %CP/DT
    RevenueByChannelChart.tsx    # Bar ngang doanh thu theo kênh
    TopProductsChart.tsx         # Bar ngang top SP theo số đơn
    ChannelRevenueCostChart.tsx  # (admin) grouped bar DT vs CP theo kênh
    tables/                      # các bảng tách ra từ page, giữ nguyên logic expand
      RevenueByChannelTable.tsx
      DailyBreakdownTable.tsx
      ChannelDailyTable.tsx      # (admin)
      TopProductsTable.tsx
```

`AdsReportPage.tsx` sau refactor: chỉ giữ bộ lọc (DateFilter + channel select), gọi
`useAdsReport`, và render 4 `ReportSectionCard` truyền chart + bảng tương ứng.

## 5. Logic mức gộp — `lib/granularity.ts`

```
type Granularity = "day" | "month";

resolveGranularity(option, from, to): Granularity
  - option in {this_quarter, last_quarter, this_year, last_year}  -> "month"
  - option in {today, yesterday, this_week, last_week, last_7_days,
               this_month, last_month, last_30_days}              -> "day"
  - option === "custom":
      span = số ngày giữa from..to (bao gồm 2 đầu)
      span > 62  -> "month"
      else       -> "day"

groupDailyByMonth(dailyRows): DailyBreakdownRow-like[]
  - cộng dồn revenue, cost, closed_orders theo khóa "YYYY-MM"
  - ratio_pct tính lại = revenue > 0 ? cost / revenue * 100 : null
  - giữ thứ tự tháng tăng dần
```

Khi `granularity === "day"` giữ nguyên `daily_breakdown` theo ngày; khi `"month"`
dùng `groupDailyByMonth`. Nhãn trục X: ngày = `YYYY-MM-DD`, tháng = `YYYY-MM` (hoặc
`MM/YYYY` cho dễ đọc, chốt khi implement).

## 6. Mapping chart

| Phần | Loại chart | Trục X | Series |
|---|---|---|---|
| DT/CP theo thời gian | ComposedChart | ngày *hoặc* tháng | Bar Doanh thu, Bar Chi phí, Line %CP/DT (trục Y phải) |
| Doanh thu theo kênh | Bar ngang | — | Doanh thu (sort giảm dần); đơn đã chốt trong tooltip |
| Sản phẩm bán chạy | Bar ngang | — | Số đơn, top 15 (phần còn lại vẫn xem ở bảng) |
| DT/CP theo kênh (admin) | Grouped Bar | kênh | Doanh thu vs Chi phí |

- Phần admin chỉ render khi `isAdmin === true` (giữ nguyên điều kiện hiện tại).
- Empty state: mỗi chart hiển thị "Không có dữ liệu" như pattern hiện có.

## 7. Toggle & luồng dữ liệu

- `ReportSectionCard` nhận `title`, `chart` (ReactNode), `table` (ReactNode); giữ
  state cục bộ `view: "chart" | "table"` (mặc định `"chart"`). Header có segmented
  toggle nhỏ gồm 2 `Button` shadcn.
- `useAdsReport` trả `{ data, granularity, error }` (giữ nguyên hành vi fetch:
  effect theo dateFilter/channel, polling 30 phút, reload khi tab visible). Một
  nguồn dữ liệu duy nhất — đổi view không gọi lại API.
- `granularity` được tính từ `dateFilter` trong hook và truyền xuống chart thời gian.

## 8. Testing

- `lib/__tests__/granularity.test.ts`:
  - mapping từng preset → granularity đúng.
  - custom range: 30 ngày → day, 90 ngày → month, đúng biên 62.
  - `groupDailyByMonth`: cộng đúng revenue/cost/closed_orders, tính lại ratio_pct,
    xử lý revenue = 0 → ratio null.
- Smoke test mỗi chart: render với data mẫu (không lỗi) + render empty state.
- `ReportSectionCard`: toggle đổi giữa chart và table.

## 9. Phạm vi loại trừ (YAGNI)

- Không export ảnh/PDF/CSV của chart.
- Không drill-down trên chart (drill-down tiếp tục nằm ở bảng expand).
- Không sửa backend / API / RBAC.
- Không thêm bộ lọc mới ngoài cái đang có.
- Không đổi logic nghiệp vụ tính doanh thu/chi phí.

## 10. Rủi ro & lưu ý

- Gộp client-side phụ thuộc backend luôn trả `daily_breakdown` theo ngày: đúng với
  hành vi hiện tại. Nếu sau này backend đổi sang trả sẵn theo tháng thì bỏ bước gộp.
- Custom range rất dài (vd nhiều năm) vẫn gộp theo tháng — chấp nhận được vì backend
  đã có giới hạn khoảng ngày ở tầng query; không xử lý thêm ở phạm vi này.
- Refactor tách bảng phải giữ **nguyên** logic expand/pagination hiện có để không đổi
  hành vi người dùng đang quen.
