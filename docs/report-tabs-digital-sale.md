# Báo cáo theo Digital & theo Sale — chuyển 2 trang báo cáo sang dạng tab

> Trạng thái: Sprint 1 + 2 + 3 ✅ hoàn thành
> Ngày tạo: 2026-08-01 · Branch `feat/report-tabs-digital-sale`

## 1. Bối cảnh

Hai trang báo cáo hiện có đang là một trang cuộn dài duy nhất:

| Trang | Section | Component |
|---|---|---|
| Báo cáo Ads | `ads-report` | `frontend/src/features/ads/pages/AdsReportPage.tsx` |
| Báo cáo Sale | `dashboard` | `frontend/src/pages/DashboardPage.tsx` |

Yêu cầu: mỗi trang tách thành **tab**, giữ nội dung cũ ở tab "Tổng quan" và bổ sung
2 tab báo cáo dạng bảng.

## 2. Định nghĩa nghiệp vụ (đã chốt với CTO 2026-08-01)

### 2.1. "Digital" = kênh có NV Ads phụ trách

Hệ thống **không có** entity `digital`. Đơn vị của báo cáo là **kênh bán hàng**
(page Facebook / tài khoản Google Ads) nằm trong phạm vi `sale_report_channel_ids()`
— tức kênh có ít nhất một NV phòng Ads đang được gán qua `UserChannel`.

- Mỗi hàng bảng "Báo cáo theo digital" = 1 kênh.
- Chi phí ad account map vào N kênh vẫn **chia đều 1/N** như logic hiện hữu của
  `summary_ads_daily_breakdown` — tổng theo hàng luôn khớp tổng toàn kỳ.

### 2.2. Cột báo cáo Ads (đúng thứ tự yêu cầu)

| # | Cột | Công thức | Nguồn |
|---|---|---|---|
| 1 | Doanh thu | Σ `total_after_discount` của đơn **chốt** | `CLOSED_SALES_STATUS_CODES` |
| 2 | Chi phí | Σ spend FB×hệ số FB + Google×1.08, chia đều theo kênh | `*AdAccountDailySpend` |
| 3 | ME/RE | `chi phí / doanh thu` (%) | tính ở FE |
| 4 | Số data | Số lead/đơn phát sinh trong kỳ, **mọi trạng thái** (đã loại đơn huỷ) | mới thêm: `leads` |
| 5 | CPL | `chi phí / số data` | tính ở FE |
| 6 | Số đơn hàng | Số đơn **chốt** | `closed_orders` |
| 7 | AOV | `doanh thu / số đơn` | tính ở FE |
| 8 | Tỉ lệ chốt | `số đơn / số data` (%) | tính ở FE |

"Số data" là chỉ số **duy nhất còn thiếu** trong payload hiện tại → chỉ cần bổ sung
`leads` vào `summary_ads_daily_breakdown` (cả mức ngày lẫn mức kênh-trong-ngày).
Mọi cột khác FE tự dẫn xuất ⇒ **không thêm endpoint mới cho Ads**.

### 2.3. Cột báo cáo Sale (đúng thứ tự yêu cầu)

| # | Cột | Công thức |
|---|---|---|
| 1 | Doanh thu tổng | Σ tiền của **toàn bộ** data sale nhận trong kỳ (mọi trạng thái, đã loại huỷ) |
| 2 | Doanh thu chốt đơn | Σ tiền đơn ở `CLOSED_REVENUE_SALES_STATUS_CODES` (đã chốt / hoả tốc / đã lên đơn — **trừ** "đã chốt, hẹn ngày") |
| 3 | Doanh thu xác nhận đơn | Σ tiền đơn ở `CLOSED_SALES_STATUS_CODES` (đủ 4 trạng thái chốt, **có** hẹn ngày) |
| 4 | Tỉ lệ chốt đơn | `số đơn / số data nhận` (%) |
| 5 | Số data nhận | `Count(id)` toàn bộ đơn/lead giao cho sale |
| 6 | Số đơn hàng | `Count(id)` đơn ở `CLOSED_SALES_STATUS_CODES` |
| 7 | AOV | `doanh thu xác nhận đơn / số đơn hàng` |

Chọn cột 6 dùng đủ 4 trạng thái chốt để **khớp mẫu số/tử số** với cột 3 và cột 4 —
cùng quy ước với `CloseRateSection` (Nhóm 1) đang chạy.

Phạm vi đơn: `_sales_report_orders_queryset()` — kênh có NV Ads gán, `staff` thuộc
phòng Sales, đã loại đơn huỷ ở `_base_orders_queryset()`.

### 2.4. Tab "chi tiết"

Chọn **1 kênh** (ads) / **1 nhân viên sale** + **1 tháng** → mỗi hàng là 1 ngày trong
tháng đó, các cột y hệt tab tổng hợp, có hàng "Tổng" ở cuối. Ngày không phát sinh
vẫn hiện (giá trị 0) để nhìn được ngày trống.

## 3. Thiết kế kỹ thuật

### 3.1. Backend

```
apps/orders/_repositories_reports.py
  summary_ads_daily_breakdown()        # + leads (mức ngày + mức kênh)
  summary_sale_daily_by_staff()        # MỚI — 1 hàng / (ngày, nhân viên sale)

apps/orders/transport/views/order_views_impl/dashboard_report_views.py
  sale_staff_report_summary()          # MỚI — GET /api/orders/reports/sale-by-staff

apps/orders/urls.py                    # + route
```

`summary_sale_daily_by_staff` trả **hàng phẳng** `(date, staff)` thay vì lồng
nhau: FE dùng chung một payload cho cả tab tổng hợp (gộp theo staff) lẫn tab chi
tiết (lọc 1 staff), không cần 2 endpoint. Với 30 ngày × ~25 sale = ~750 hàng —
nhỏ, 1 query group-by, không N+1.

Quyền: dùng lại `require_section("dashboard")` — cùng trang Báo cáo Sale.

### 3.2. Frontend

```
features/ads/
  report-grouping.ts                  # + leads vào DailyChannelRow / DailyBreakdownRow
  digital-report-selectors.ts         # MỚI — buildDigitalRows / buildDigitalDailyRows / monthRange
  components/DigitalMetricTable.tsx   # MỚI — bảng chỉ số dùng chung cho cả 2 tab
  components/DigitalReportTab.tsx     # MỚI — tab "Báo cáo theo digital"
  components/DigitalDetailTab.tsx     # MỚI — tab "Chi tiết theo digital"
  pages/AdsReportPage.tsx             # → Tabs

features/sale-report/                 # MỚI (module riêng, không nhét vào dashboard)
  queries.ts                          # useSaleStaffReport
  sale-report-selectors.ts            # buildSaleStaffRows / buildSaleDailyRows
  components/SaleMetricTable.tsx      # bảng chỉ số dùng chung cho cả 2 tab
  components/SaleStaffReportTab.tsx
  components/SaleStaffDetailTab.tsx

pages/DashboardPage.tsx               # → Tabs
components/ui/month-picker.tsx        # MỚI — chọn tháng cho 2 tab chi tiết
```

Dùng `components/ui/tabs.tsx` sẵn có (Tabs nhẹ, không Radix). Tabs chỉ mount
panel đang mở, nên tab nào chưa xem thì không phát sinh request.

Tab chi tiết có bộ lọc tháng **độc lập** với `DateFilter` của trang: query riêng
với `date_from`/`date_to` = mốc đầu/cuối tháng đã chọn.

Nhãn dùng `t("key", "fallback tiếng Việt")` như phần còn lại của trang Báo cáo
ads/sale — 2 trang này không khai báo key trong `i18n/messages/`.

## 4. Sprint

### Sprint 1 — Backend ✅
- [x] `summary_ads_daily_breakdown` trả thêm `leads` (ngày + kênh)
- [x] `summary_sale_daily_by_staff` + view + route `/api/orders/reports/sale-by-staff`
- [x] Unit test: leads đếm mọi trạng thái, không trùng với closed_orders
- [x] Unit test: 3 mốc doanh thu sale tách đúng theo trạng thái; RBAC 403

### Sprint 2 — Frontend Ads ✅
- [x] `MonthPicker` dùng chung
- [x] Selector + bảng "Báo cáo theo digital"
- [x] Selector + bảng "Chi tiết theo digital"
- [x] `AdsReportPage` → Tabs, tab 1 giữ nguyên nội dung cũ
- [x] Unit test selector + component

### Sprint 3 — Frontend Sale ✅
- [x] `features/sale-report` (queries + selectors)
- [x] Bảng "Báo cáo theo sale" + "Chi tiết theo sale"
- [x] `DashboardPage` → Tabs
- [x] Unit test selector + component + wiring tab
- [x] Đồng bộ `backend/schema.yaml` + `frontend/src/api/schema.d.ts` (job drift của CI)

## 5. Ghi chú sau khi thực hiện

**Đã làm**

- Backend: `leads` được tính bằng **một query riêng** trên cùng
  `_ads_landing_orders_queryset` (không lọc trạng thái chốt) rồi merge vào cùng
  `by_date` — giữ nguyên hành vi cũ của mọi field đang có, không đụng vào query
  doanh thu/chi phí. Kênh chỉ có lead mà chưa có đơn chốt vẫn xuất hiện trong
  `channels[]` của ngày đó (trước đây bị rớt).
- Backend: `summary_sale_daily_by_staff` là **1 query group-by duy nhất**
  `(TruncDate(created_at), staff)`; mọi cột là `Count`/`Sum` có `filter=` nên
  không N+1, không đọc lại DB theo hàng.
- FE: cả 4 bảng mới dùng chung một cặp selector thuần (`ads-report-selectors.ts`,
  `sale-report-selectors.ts`) — component chỉ render, logic tính nằm ở hàm pure
  để unit test không cần render.
- FE: tab chi tiết dùng query riêng theo tháng; `keepPreviousData` giữ số liệu cũ
  khi đổi tháng, không nháy bảng trống.
- FE: bỏ ý định thêm prop `enabled` cho query của tab sale — `Tabs` vốn chỉ mount
  panel đang mở nên prop đó là thừa; test đã chốt hành vi "ở tab Tổng quan không
  gọi API báo cáo sale".
- Đã thêm test: 8 test backend (5 ads `leads` / 3 sale) + 34 test frontend
  (10 selector digital, 8 selector sale, 5 + 4 bảng, 4 tab DashboardPage,
  3 tab AdsReportPage). Fixture `daily_breakdown` cũ được bổ sung `leads`.
- Đã chạy: pytest 8/8 mới + 46/46 hồi quy báo cáo, ruff sạch, `tsc --noEmit`
  sạch, `lint:strict` sạch, `format:check` sạch, `test:ci` 783 passed / 7 failed
  — 7 test đỏ này (RequireAuth ×2, use-orders-data.realtime-upsert-filters ×5)
  đã đỏ sẵn trên master, đã kiểm chứng lại trên worktree master sạch.

**Chưa làm / cố ý bỏ qua**

- Không export Excel cho các tab mới (yêu cầu chưa nêu) — nếu cần thì thêm sau,
  dữ liệu đã sẵn ở selector.
- Tab chi tiết chỉ chọn **1** kênh/sale mỗi lần theo đúng yêu cầu; chưa hỗ trợ
  so sánh nhiều đối tượng cạnh nhau.
- Chưa đánh index mới: 2 query đều đi qua `orders.created_at` + `channel_id` /
  `staff_id` vốn đã có index; đo trên dữ liệu thật trước khi thêm.
