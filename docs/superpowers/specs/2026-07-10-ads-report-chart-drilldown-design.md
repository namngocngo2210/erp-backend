# Báo cáo Ads dạng Chart + Drill-down — Thiết kế

- **Ngày**: 2026-07-10
- **Branch**: feat/ads-report-charts (worktree, base `origin/master`)
- **Trạng thái**: Đã duyệt thiết kế (brainstorming) → chờ review spec → lập plan
- **Quan hệ với bản cũ**: Thay thế phần *chart* của
  `2026-06-24-ads-report-charts-design.md`. Bản cũ đề xuất **toggle Chart⇄Bảng**;
  bản này chọn UX **chart luôn hiển thị + "Xem bảng chi tiết" (drill-down)** giống
  trang Báo cáo Sale. Phần *monthly grouping* của bản cũ đã ship (07/06,
  `frontend/src/features/ads/report-grouping.ts`) — tái sử dụng, không làm lại.

## 1. Mục tiêu

Làm lại trang Báo cáo Ads (`AdsReportPage.tsx`, hiện toàn bảng) theo mô hình trực
quan của Báo cáo Sale (Dashboard): mỗi khối hiển thị **chart tổng quan** ở trên,
kèm **"Xem bảng chi tiết"** (thẻ `<details>` bung ra) render đúng bảng hiện tại +
dòng tổng. Không mất thông tin nào so với bản bảng.

## 2. Quyết định đã chốt (brainstorming session 2026-07-10)

1. **Phạm vi**: Chart lên trên, **giữ nguyên 4 bảng hiện tại** nhưng bọc trong
   `CollapsibleTable` "Xem bảng chi tiết". Đúng pattern Báo cáo Sale.
2. **Loại chart** (theo đề xuất): xem mục 6.
3. **Đầu trang có hàng KPI tile** tổng quan (như `KpiHero` của Dashboard).
4. **Chuyển file page** sang `features/ads/pages/` theo convention CLAUDE.md
   (đã xác nhận OK).

## 3. Nguyên tắc & ràng buộc

- **Không đổi backend.** Toàn bộ dữ liệu cho 4 chart + KPI đã nằm trong payload
  `GET /api/orders/reports/ads` (`revenue_by_channel`, `daily_breakdown` có
  `revenue`/`cost`/`ratio_pct`/`channels`, `top_products`). Chi phí theo kênh vẫn
  dựng client qua `sumCostByChannel` như hiện tại. Zero risk tầng server/RBAC.
- **Không đổi data-fetching.** Ads đã dùng React Query (`useAdsReport`) — giữ
  nguyên. Một nguồn dữ liệu duy nhất; bung/thu chi tiết không gọi lại API.
- **Không thêm thư viện.** Dùng `recharts` (`^3.9.1`) theo pattern
  `frontend/src/features/dashboard/components/DashboardCharts.tsx`. Light theme,
  tông xanh; dùng lại helper format tiền tệ sẵn có (`formatAdsCurrency`,
  `formatRatioPct` trong `report-grouping.ts`).
- **Tách file.** `AdsReportPage.tsx` hiện 569 dòng > mốc 300 của CLAUDE.md → tách
  thành các component theo khối trong `features/ads/`.
- **Gộp ngày→tháng** tái dùng logic sẵn có trong `report-grouping.ts` (ngưỡng 62
  ngày). Chart trục thời gian dùng đúng dữ liệu mà `DailyBreakdownTable` đang gộp,
  để chart và bảng chi tiết luôn khớp nhau.

## 4. Cấu trúc file

**Mới**
```
frontend/src/components/report/
  CollapsibleTable.tsx          # lift primitive dùng chung (<details><summary>Xem bảng chi tiết</summary>...)
                                # tránh import chéo từ features/dashboard

frontend/src/features/ads/
  pages/AdsReportPage.tsx       # CHUYỂN từ src/pages/AdsReportPage.tsx; slim lại thành khung compose
  ads-report-selectors.ts      # hàm thuần: tính KPI tổng + map dữ liệu cho từng chart (unit-test được)
  components/
    AdsKpiRow.tsx               # hàng KPI tile tổng quan
    RevenueByChannelChart.tsx   # bar ngang DT/kênh + CollapsibleTable(bảng DT theo kênh cũ)
    DailyTrendChart.tsx         # ComposedChart DT/CP/%CP + CollapsibleTable(DailyBreakdownTable cũ)
    TopProductsChart.tsx        # bar ngang top N + CollapsibleTable(bảng top SP cũ, giữ phân trang)
    ChannelDailyChart.tsx       # (admin) stacked bar theo kênh + CollapsibleTable(ChannelDailyTable cũ)
```

**Giữ nguyên** (không sửa logic): `features/ads/queries.ts`,
`features/ads/report-grouping.ts`, `features/ads/components/DailyBreakdownTable.tsx`,
`features/ads/components/ChannelDailyTable.tsx`.

**Sửa 1 dòng import**: `frontend/src/app/routed-pages.tsx` trỏ page về vị trí mới.

`AdsReportPage.tsx` sau refactor: giữ filter card (DateFilter + MultiSelect kênh),
gọi `useAdsReport`, render `AdsKpiRow` + 4 khối chart. Logic lọc kênh/scope hiện có
giữ nguyên.

## 5. Hàng KPI tile — `AdsKpiRow`

Tính trong `ads-report-selectors.ts` từ payload:

| Tile | Nguồn |
|---|---|
| Tổng doanh thu | Σ `revenue_by_channel[].revenue` |
| Tổng chi phí | Σ `daily_breakdown[].cost` |
| % chi phí/doanh thu | tổng chi phí / tổng doanh thu × 100 (null khi DT = 0) |
| Đơn đã chốt | Σ `revenue_by_channel[].closed_orders` |
| Đơn giao thành công | Σ `revenue_by_channel[].delivered_orders` |
| Doanh thu giao thành công | Σ `revenue_by_channel[].delivered_revenue` |

## 6. Mapping chart

| Khối | Loại chart | Trục X | Series |
|---|---|---|---|
| Doanh thu theo kênh | Bar ngang | — | Doanh thu (sort giảm dần); nhãn %CP, đơn chốt trong tooltip |
| Diễn biến theo ngày | ComposedChart | ngày *hoặc* tháng (theo `report-grouping`) | Bar Doanh thu, Bar Chi phí, Line %CP/DT (trục Y phải) |
| Top sản phẩm bán chạy | Bar ngang | — | Doanh thu, top N; phần còn lại xem ở bảng chi tiết |
| Kênh × Ngày (admin) | Stacked bar | ngày/tháng | Doanh thu theo từng kênh (stack) |

- Khối admin chỉ render khi `isAdmin === true` (giữ nguyên điều kiện hiện tại).
- Empty state mỗi chart: hiển thị "Không có dữ liệu" theo pattern hiện có.
- Mỗi khối bọc trong Card; chart trên, `CollapsibleTable` "Xem bảng chi tiết" dưới.

## 7. Testing

- `ads-report-selectors.test.ts`:
  - Tính KPI tổng đúng (doanh thu, chi phí, %CP null khi DT=0, các đếm đơn).
  - Map dữ liệu chart theo kênh (sort giảm dần) + top-N sản phẩm.
- Smoke test mỗi chart component: render với data mẫu (không lỗi) + render empty state.
- `CollapsibleTable`: render summary + bung nội dung.
- Không phá test hiện có của `DailyBreakdownTable`/`ChannelDailyTable`.

## 8. Phạm vi loại trừ (YAGNI)

- Không export ảnh/PDF/CSV của chart.
- Không drill-down click-trên-chart (chi tiết nằm ở bảng bung `<details>`).
- Không sửa backend / API / RBAC / logic tính doanh thu-chi phí.
- Không thêm bộ lọc mới ngoài cái đang có.
- Không đổi granularity logic (dùng lại ngưỡng 62 ngày của `report-grouping.ts`).

## 9. Rủi ro & lưu ý

- Chi phí theo kênh dựng client (`sumCostByChannel`) — giữ nguyên hành vi hiện tại,
  không đổi cách tính để tránh lệch số so với bản đang chạy.
- Chart trục thời gian phải dùng **cùng** dữ liệu đã gộp mà bảng chi tiết dùng, nếu
  không chart và bảng sẽ lệch khi range > 62 ngày.
- Refactor tách bảng phải giữ **nguyên** logic expand SKU + phân trang top-products
  để không đổi hành vi người dùng đang quen.
- `CollapsibleTable`, `KpiHero` hiện nằm trong `features/dashboard`. Lift primitive
  cần dùng sang `components/report/`; không refactor Dashboard ở phạm vi này.
