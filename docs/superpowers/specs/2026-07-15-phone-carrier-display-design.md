# Hiển thị nhà mạng theo SĐT + bảng "Data theo nhà mạng" trong Báo cáo Sale

**Ngày:** 2026-07-15 · **Branch:** `feat/phone-carrier-display`

## Mục tiêu

1. Cột SĐT trên các bảng đơn hàng hiển thị nhà mạng (Viettel, MobiFone, Vinaphone…)
   dưới dạng nhãn in hoa nhỏ, tô màu, nằm ngay dưới số (theo mẫu CRM tham chiếu).
2. Trang Báo cáo Sale thêm bảng thống kê số lượng data theo nhà mạng.

## Cách nhận diện

Nhà mạng suy ra từ **3 số đầu** sau khi normalize SĐT (bỏ ký tự lạ, `+84/84` → `0`,
số 9 chữ số mất `0` đầu kiểu Excel → thêm lại). Bảng đầu số ổn định từ đợt chuyển
11→10 số (2018): Viettel 032–039/086/096–098; Vinaphone 081–085/088/091/094;
MobiFone 070/076–079/089/090/093; Vietnamobile 052/056/058/092; Gmobile 059/099;
iTel 087; Wintel 055.

**Giới hạn chấp nhận (đã chốt):** số "chuyển mạng giữ số" (MNP, ~2-3%) hiện theo
nhà mạng gốc của đầu số. Muốn chính xác 100% phải tra API bên thứ ba — không làm.

## Thiết kế

### Frontend (badge)
- `src/lib/phone-carrier.ts` — hàm thuần `normalizePhoneDigits` + `getCarrier`
  (trả `{key, label, shortLabel}` hoặc `null`), map màu `CARRIER_TEXT_CLASS`.
- `src/components/ui/carrier-badge.tsx` — `CarrierBadge`: nhãn in hoa 9px tô màu
  theo nhà mạng, không render gì khi không nhận diện được.
- Gắn vào `PhoneWithCall` (`OrdersTable/OrderCell.tsx`) — 1 điểm sửa ăn cả cột
  SĐT lẫn cột Tên KH dạng gộp trên mọi trang dùng OrdersTable.

### Backend (báo cáo)
- `apps/orders/phone_carrier.py` — bảng đầu số mirror với FE (test đối chiếu 2 bên).
- `OrderReportsMixin.summary_data_by_carrier` — cùng scope các block sale khác
  (`_sales_report_orders_queryset` = kênh có NV Ads gán + staff phòng Sales, filter
  ngày theo `created_at`). Normalize phone NGAY TRONG SQL (regexp_replace + Case)
  rồi group theo 3 số đầu — 1 query aggregate; map đầu số → nhà mạng ở Python.
  Zero-fill mọi nhà mạng + dòng `unknown` ("Không xác định"), sort total giảm dần.
- `dashboard_summary` trả thêm key `data_by_carrier: [{carrier, label, total}]`.

### Frontend (Báo cáo Sale)
- `CarrierDataSection` — donut `DistributionDonut` (tổng data ở tâm, legend tên +
  số + %), đặt CUỐI trang (Nhóm 8, sau Tỷ lệ hoàn theo SP). Đổi từ bảng sang
  chart theo yêu cầu user 15/07.
- Màu gán CỐ ĐỊNH theo nhà mạng (`CARRIER_CHART_COLORS` trong dashboard-constants,
  màu theo entity không theo thứ hạng, gợi nhận diện thương hiệu; unknown xám
  trung tính chủ đích). Bộ màu nhóm nhà mạng thực tế pass validator CVD/normal
  của skill dataviz; WARN contrast được bù bằng legend + tooltip có nhãn.
- Lát 0 data không vẽ; cả kỳ 0 data → EmptyCard. Derived rows trong
  `use-dashboard-summary`.
- Regen `backend/schema.yaml` + `schema.d.ts` (kèm prettier).

## Test
- FE: `src/lib/__tests__/phone-carrier.test.ts` — 17 test (normalize +84/84/mất 0,
  mapping từng nhà mạng, số bàn/rác → null, shortLabel).
- BE: `apps/orders/tests/test_data_by_carrier_report.py` — 5 test (mapping prefix,
  đếm theo nhà mạng qua các dạng số, zero-fill, sort, filter ngày, loại kênh
  không gán NV Ads).

## Tiến độ
- [x] FE badge + util + test
- [x] BE block báo cáo + test (5/5 pass) + hồi quy dashboard (18/18 pass)
- [x] FE bảng báo cáo + types + regen schema (tsc, eslint, ruff sạch; 152 test FE khu vực orders/dashboard/components pass)
