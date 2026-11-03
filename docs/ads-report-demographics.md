# Báo cáo Ads — phân bổ theo vị trí địa lý và giới tính

Ngày: 2026-07-18 · Branch: `feat/ads-report-demographics`

## Yêu cầu (đã chốt với user)

Thêm ở **cuối** trang Báo cáo Ads 2 khối phân bổ:

1. **Theo vị trí địa lý** — mức tỉnh/thành (không cần gộp miền).
2. **Theo giới tính** — Nam / Nữ / Không rõ.

Số liệu chính theo **đơn đã chốt** (đồng nhất các khối hiện tại), kèm cột đơn
giao thành công + doanh thu giao thành công. **Không** đưa vào Lark ads report
sync — chỉ hiển thị trên web.

## Nguồn dữ liệu

- Địa lý: `Order.province` (TextField, đổ từ KiotViet/landing). Rỗng → bucket
  "Không rõ". Hiển thị nguyên văn tên tỉnh; chưa chuẩn hóa tên (làm sau nếu
  thấy nhiễu).
- Giới tính: `Customer.gender` (0 = Nữ, 1 = Nam, null = Không rõ) join qua
  `order.customer`.

## Thiết kế

### Backend

Mở rộng endpoint hiện có `GET /api/orders/reports/ads` (không tạo endpoint
mới) — thêm 2 key vào response:

- `revenue_by_province`: `[{province: string|null, closed_orders, revenue,
  delivered_orders, delivered_revenue}]` — group theo `province` đã trim,
  rỗng → `null`; sort revenue giảm dần.
- `revenue_by_gender`: `[{gender: 0|1|null, closed_orders, revenue,
  delivered_orders, delivered_revenue}]`.

Hai hàm repo mới trong `_repositories_reports.py`
(`summary_ads_revenue_by_province`, `summary_ads_revenue_by_gender`) tái dùng
đúng `_ads_landing_orders_queryset` + filter đơn chốt
(`assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES`) — scope kênh
RBAC / loại kênh KOC / loại đơn hủy tự đồng nhất với các khối trên. Không
zero-fill (khác kênh: không có danh mục tỉnh/giới cố định cần hiện dòng 0).

BE trả `province`/`gender` dạng raw (null cho không rõ) — nhãn hiển thị do FE
quyết ("Không rõ", "Nam", "Nữ") để giữ i18n một chỗ.

### Frontend

- 1 component dùng chung `RevenueBreakdownCard`
  (`features/ads/components/`): Card = DonutChart (doanh thu, tối đa 8 lát,
  đuôi gộp "Khác") + CollapsibleTable 5 cột (Tên / Đơn đã chốt / Đơn giao
  thành công / Doanh thu / Doanh thu giao thành công) + dòng Tổng. Không có
  cột chi phí (chi phí ads không tách được theo tỉnh/giới).
- `AdsReportPage`: cuối trang thêm grid 2 cột (`lg:grid-cols-2`) chứa 2 card
  "Doanh thu theo tỉnh/thành" và "Doanh thu theo giới tính", map rows từ
  payload sang shape chung qua selector.
- Selectors mới trong `ads-report-selectors.ts`: types `ProvinceRevenueRow`,
  `GenderRevenueRow`, mở rộng `AdsReportPayload`, helper map row → breakdown
  datum (nhãn null → "Không rõ", gender 0/1 → Nữ/Nam).

### Test

- BE: `tests/test_ads_report_demographics.py` — group đúng theo
  tỉnh/giới, bucket Không rõ (province rỗng / gender null), tôn trọng scope
  kênh (kênh chưa gán NV Ads không tính), loại đơn hủy.
- FE: test selector (map/label/sort) + test render `RevenueBreakdownCard`.

## Ngoài phạm vi

- Chuẩn hóa tên tỉnh (Hà Nội vs Thành phố Hà Nội) — theo dõi sau khi có số
  liệu thật.
- Breakdown chi phí ads theo region/gender từ FB Insights (demographics người
  xem quảng cáo) — bài toán khác, giai đoạn sau nếu cần.
- Lark ads report sync — user chốt không đưa vào.

## Tiến độ

- [x] Design doc
- [x] BE: repo + view + schema + test
- [x] FE: selectors + component + wiring + test
- [x] Test BE/FE pass (BE 33/33 gồm suite ads cũ, FE 46/46) — branch `feat/ads-report-demographics` đã push, chờ MR
