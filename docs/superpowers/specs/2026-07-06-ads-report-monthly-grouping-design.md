# Báo cáo ads: gộp danh sách theo tháng cho khoảng ngày dài

Ngày: 2026-07-06

## Bối cảnh

Trang Báo cáo ads (`frontend/src/pages/AdsReportPage.tsx`) có 2 bảng dựa trên
`daily_breakdown` của `GET /api/orders/reports/ads`:

- **Doanh thu / chi phí theo ngày**: mỗi dòng 1 ngày, click xổ breakdown theo kênh.
- **Doanh thu / chi phí theo kênh** (admin): mỗi kênh xổ ra từng ngày.

Khi chọn khoảng quý/năm, danh sách theo ngày quá dài (90–365 dòng) khó đọc.

## Yêu cầu (đã chốt với user)

1. Khoảng ngày ngắn (tuần/tháng): hiển thị theo ngày như hiện tại.
2. Khoảng dài (quý/năm): gộp theo tháng; click 1 tháng xổ ra các ngày của tháng.
3. Luật gộp theo **độ dài khoảng ngày** (không theo preset): khoảng > 62 ngày
   (~2 tháng) → gộp theo tháng. Áp dụng thống nhất cho preset lẫn custom.
   Nếu custom thiếu from/to thì tính span từ min/max ngày có trong dữ liệu.
4. Áp dụng cho **cả 2 bảng**: bảng theo ngày gộp thành tháng→ngày→kênh (3 cấp,
   dòng ngày vẫn xổ tiếp ra kênh); bảng admin theo kênh thành kênh→tháng→ngày.

## Phương án

**Chọn: gộp hoàn toàn ở frontend.** Backend đã trả `daily_breakdown` đủ từng
ngày kèm breakdown kênh cho cả khoảng lọc (sort tăng dần theo ngày); FE chỉ nhóm
lại. Không sửa backend, không thêm request, dữ liệu ngày có sẵn để xổ tức thì.

Phương án bị loại: thêm `granularity=month` ở backend — thêm param + round-trip
khi drill-down trong khi payload theo ngày cả năm vốn không phải vấn đề.

## Thiết kế

### Helper thuần: `frontend/src/features/ads/report-grouping.ts`

- Chuyển các type `DailyBreakdownRow`, `DailyChannelRow` từ page sang đây.
- `MONTHLY_GROUPING_THRESHOLD_DAYS = 62`.
- `shouldGroupByMonth(from, to, rows)`: span (inclusive) từ bộ lọc nếu đủ 2 mốc,
  ngược lại từ min/max `rows[].date`; trả `true` khi span > 62.
- `groupDailyRowsByMonth(rows)` → `MonthlyBreakdownRow[]`
  (`{ month: "YYYY-MM", revenue, cost, closed_orders, ratio_pct, days }`);
  ratio `null` khi revenue = 0; giữ thứ tự tăng dần.
- `groupChannelDaysByMonth(days)` → tương tự cho pivot theo kênh.
- `enumerateDates(from, to)` (chuyển từ page), `computeDailyTotals(rows)`,
  `formatMonthLabel("YYYY-MM")` → `"MM/YYYY"`.

### Component: `frontend/src/features/ads/components/`

- `DailyBreakdownTable.tsx`: nhận `{ rows, groupByMonth }`, render nguyên Card
  (title động "…theo ngày"/"…theo tháng", header cột "Ngày"/"Tháng").
  Chế độ tháng: tháng → ngày → kênh; chế độ ngày: giữ nguyên behavior cũ.
- `ChannelDailyTable.tsx` (admin): nhận `{ dailyRows, dateFrom, dateTo,
  groupByMonth }`, chứa luôn logic pivot theo kênh (chuyển từ page).
  Chế độ tháng: kênh → tháng → ngày; chế độ ngày: kênh → ngày.
- State expand cục bộ trong từng component. i18n dùng `t(key, fallback)` như
  hiện tại (không cần sửa file messages).

### Page

`AdsReportPage.tsx` gọn lại: tính `groupByMonth` từ bộ lọc + `dailyRows`, render
2 component mới. Không đổi: backend, React Query, bộ lọc kênh, bảng doanh thu
theo kênh, bảng sản phẩm bán chạy.

### Test (vitest)

`features/ads/__tests__/report-grouping.test.ts`: ngưỡng 62 ngày (62 → ngày,
63 → tháng), fallback span từ dữ liệu khi custom thiếu mốc, tổng theo tháng,
ratio null khi doanh thu 0, giữ thứ tự ngày, formatMonthLabel, computeDailyTotals.

## Follow-up cùng branch: scope kênh theo NV Ads gán (2026-07-06)

Yêu cầu bổ sung: báo cáo ads **chỉ tính kênh có NV Ads được gắn vào**.

- Tái sử dụng `sale_report_channel_ids()` (backend, nguồn sự thật sẵn có của
  báo cáo sale: UserChannel active + user active + thuộc phòng Ads kể cả kiêm
  nhiệm M2M + loại admin). Gán NV vào kênh là kênh tự vào báo cáo.
- Áp vào `_ads_landing_orders_queryset` (phủ revenue_by_channel / top_products /
  daily_breakdown phía doanh thu) và `channel_qs` phía chi phí trong
  `summary_ads_daily_breakdown` (spend của ad account map vào kênh chưa gán ads
  không tính).
- FE: dropdown kênh lọc thêm `assigned_user_count > 0`; cập nhật mô tả trang.
- Test BE: `apps/orders/tests/test_ads_report_channel_scope.py` (4 test: doanh
  thu chỉ kênh có gán, daily breakdown chỉ kênh có gán, spend kênh chưa gán = 0,
  gán thêm NV → kênh vào báo cáo).

## Follow-up 2: cột Chi phí + % ở bảng "Đơn đã chốt & doanh thu theo kênh"

- Thêm 2 cột **Chi phí** và **% chi phí / doanh thu** — chi phí theo kênh cộng
  dồn FE từ `daily_breakdown` (helper `sumCostByChannel`), không sửa backend.
- Giữ nguyên danh sách kênh (chỉ kênh có đơn chốt — đã chốt với user); kênh có
  chi phí nhưng 0 đơn KHÔNG hiện ở bảng này → tổng chi phí bảng này có thể nhỏ
  hơn tổng ở bảng theo ngày.
- Các cột số của bảng này căn giữa (header + cell) theo yêu cầu user (trước đó
  bị lệch giữa header và số liệu).
