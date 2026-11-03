# Thiết kế: Mở rộng dashboard chân dung khách hàng

**Ngày:** 2026-05-26
**Branch:** `customer-report-dashboard-20260526`
**Trạng thái:** Spec — chờ duyệt để chuyển sang implementation plan
**Tiền đề:** Mở rộng dashboard `/customers/report` đã có (xem `2026-05-26-customer-report-dashboard-design.md`).

## 1. Mục tiêu

Bổ sung các tiêu chí đánh giá **chân dung khách hàng** dựa trên bảng `order_assessments`, tổ chức lại trang theo section chủ đề, thêm bảng độ phủ dữ liệu và 3 phân tích chéo.

## 2. Phạm vi & quyết định đã chốt

- **Bố cục:** 1 trang cuộn dọc, chia **section có tiêu đề**.
- **Cross-tab:** 3 cái **cố định**, hiển thị **stacked bar**.
- **comparing_brand_text** (free text): **bảng top giá trị** (chuẩn hóa lower/strip rồi đếm).
- **Đơn vị phân tích:** giữ nguyên — mỗi đơn có assessment trong kỳ; phân khúc gán theo khách; trường mảng đếm theo lượt.
- **Quyền:** dùng chung `dashboard`. **Không migration** (mọi field đã có).
- **Phase:** Phase 1 = phân bố 1 chiều + bố cục section + bảng brand + độ phủ; Phase 2 = 3 cross-tab.

## 3. Nguồn dữ liệu (đã xác minh)

Tất cả label map đã tồn tại trong `apps/orders/crm_catalog.py` trừ giới tính (`GENDER_LABELS` ở `apps/customers/serializers.py` `{0:"Nữ",1:"Nam"}` — sẽ khai báo inline trong service để tránh phụ thuộc serializer).

| Tiêu chí | Field | Nguồn | Kiểu | Label map | Biểu đồ |
|---|---|---|---|---|---|
| Giới tính | `Customer.gender` | Customer (join) | 0/1/None | inline `{0:Nữ,1:Nam}` | donut |
| Tỉnh/thành | `Order.province` | Order | text | (raw, top 10 + Khác) | bar |
| Mức tiềm năng | `OrderAssessment.potential_level` | assessment | 1-5/None | `POTENTIAL_LEVEL_LABELS` | bar |
| Mức độ đau | `pain_level_code` | assessment | code | `PAIN_LEVEL_LABELS` | bar |
| Thời gian đau | `pain_duration_code` | assessment | code | `PAIN_DURATION_LABELS` | bar |
| Thói quen sinh hoạt | `lifestyle_habit_codes` | assessment | ArrayField | `LIFESTYLE_HABIT_LABELS` | bar (lượt) |
| Đã từng dùng massage | `used_massage_before` | assessment | Bool/None | `USED_MASSAGE_BEFORE_LABELS` (True→"yes",False→"no") | donut |
| Thời điểm thư giãn | `relax_time_codes` | assessment | ArrayField | `RELAX_TIME_LABELS` | bar (lượt) |
| Nguồn biết đến SP | `delax_source_codes` | assessment | ArrayField | `DELAX_SOURCE_LABELS` | bar (lượt) |
| Lý do không chốt | `not_closed_reason_code` | assessment | code | `NOT_CLOSED_REASON_LABELS` | bar |
| Thời lượng cuộc gọi | `call_duration_code` | assessment | code | `CALL_DURATION_LABELS` | bar |
| Thương hiệu so sánh | `comparing_brand_text` | assessment | text | (raw normalized, top N) | bảng |

Hằng số `CLOSED_SALES_STATUS_CODES` ở `apps/orders/_repositories_reports.py` (cho cross-tab nguồn × tỉ lệ chốt).

## 4. Backend

### 4.1 Refactor cho sạch (config-driven)

Thay vì lặp tay từng counter, định nghĩa cấu hình khai báo:

```python
# (key trả về, field trong .values, label_map, is_array, fixed_order|None)
SIMPLE_DISTRIBUTIONS = [
    ("occupation_distribution", "assessment__occupation_code", OCCUPATION_LABELS, False, None),
    ("classification_distribution", "assessment__customer_classification_code", CUSTOMER_CLASSIFICATION_LABELS, False, None),
    ("pain_level_distribution", "assessment__pain_level_code", PAIN_LEVEL_LABELS, False, PAIN_LEVEL_ORDER),
    ...
    ("lifestyle_distribution", "assessment__lifestyle_habit_codes", LIFESTYLE_HABIT_LABELS, True, None),
    ("delax_source_distribution", "assessment__delax_source_codes", DELAX_SOURCE_LABELS, True, None),
    ...
]
```

`summary()` lấy tất cả field cần thiết trong **một** `base.values(...)`, rồi 1 vòng lặp sinh phân bố: trường thường đếm theo đơn (rỗng/None → `unknown`), trường mảng (`is_array=True`) đếm theo lượt. Tận dụng lại `_distribution(counter, label_map, total, fixed_order)` đã có.

Trường đặc biệt xử lý riêng:
- `gender`: join `customer__gender`, label inline, total = total_records.
- `province`: raw value, gom top 10 theo count, phần còn lại gộp `"Khác"`; rỗng → `unknown`.
- `used_massage_before`: Bool → code `"yes"/"no"`, None → `unknown`.
- `potential_level`: int 1-5 → str key, label từ `POTENTIAL_LEVEL_LABELS`, fixed order 1→5.
- `comparing_brand_top`: chuẩn hóa `text.strip().lower()`, bỏ rỗng, đếm, trả top N `[{value, count}]` (value hiển thị giữ bản gốc đại diện đầu tiên).

Phân khúc S1-S7, độ tuổi, nhu cầu chi tiết, rào cản, feedback rating + samples giữ nguyên như hiện tại.

### 4.2 Độ phủ dữ liệu (Phase 1)

`completeness: [{field, label, filled, total, percentage}]` — `total = total_records`; `filled` = số đơn có giá trị ≠ rỗng/None (với ArrayField: list không rỗng). Danh sách field theo dõi: tuổi, nghề, giới tính, nguồn, mức đau, thời gian đau, nhu cầu, phản hồi SP, tiềm năng, phân loại.

### 4.3 Cross-tab (Phase 2)

3 hàm riêng, mỗi hàm trả ma trận cho stacked bar:
```python
{"rows": [{"code","label"}...], "columns": [{"code","label"}...], "cells": {row_code: {col_code: count}}}
```
- **Nhu cầu × Phân khúc:** rows = need_type, columns = segment. Đếm distinct (đơn, need_type) gán theo phân khúc của khách.
- **Mức đau × Độ tuổi:** rows = pain_level, columns = age bucket. Đếm đơn.
- **Nguồn × Tỉ lệ chốt:** rows = delax_source (lượt), columns = {closed, not_closed} theo `CLOSED_SALES_STATUS_CODES`. Hiển thị stacked + % chốt.

### 4.4 Cấu trúc file
- Nếu `report_service.py` phình to (>~250 dòng): tách `report_distributions.py` (config + builder cho phân bố/độ phủ) và `report_crosstabs.py` (3 cross-tab). `report_service.py` orchestrate + gọi. Quyết định tách khi viết code.
- View `/api/customers/report/summary` giữ nguyên, chỉ trả thêm key. Cross-tab Phase 2 có thể nằm cùng response hoặc tách param `?include=crosstab` — mặc định trả cùng response (đơn giản).

### 4.5 Test
- Mở rộng `test_customer_report_service.py`: seed assessment có các field mới, assert phân bố (kể cả array đếm lượt), used_massage Bool, gender, province top+Khác, potential, brand top, completeness.
- `test_customer_report_api.py`: thêm các key mới vào kiểm tra shape.
- Phase 2: test riêng 3 cross-tab (ma trận đúng số đếm, tỉ lệ chốt theo nguồn).

## 5. Frontend

- Component mới: `<ReportSection title>` (heading + lưới chart), `<DistributionTable>` (brand top, completeness), `<StackedBarChart>` (cross-tab, Phase 2). Đặt trong `pages/` hoặc `components/`.
- `CustomerReportPage` render theo 6-8 section như mục Bố cục. Mỗi chart vẫn dùng `DistributionBar`/`DistributionDonut` sẵn có.
- Type `CustomerReportSummary` mở rộng thêm key mới; cross-tab có type `CrossTabData`.

## 6. Bố cục section (frontend)

1. Nhân khẩu học: Giới tính · Độ tuổi · Nghề nghiệp · Tỉnh/thành
2. Phân khúc & phân loại: Phân khúc S1-S7 · Phân loại KH · Mức tiềm năng
3. Sức khỏe & nhu cầu: Mức đau · Thời gian đau · Thói quen sinh hoạt · Đã dùng massage · Thời điểm thư giãn · Nhu cầu chi tiết
4. Nguồn & marketing: Nguồn biết đến SP
5. Sale & rào cản: Rào cản · Lý do không chốt · Thời lượng cuộc gọi · Thương hiệu so sánh (bảng)
6. Phản hồi sau mua: Phản hồi SP · danh sách feedback
7. Phân tích chéo (Phase 2): 3 stacked bar
8. Độ phủ dữ liệu: bar % đã nhập từng trường

## 7. Đơn vị & ranh giới
- `CustomerReportService.summary` orchestrate; builder phân bố thuần (counter→list); cross-tab tách hàm; completeness tách hàm. Mỗi unit test độc lập qua DB seed.
- Frontend: section/table/stacked-bar là component thuần nhận data đã chuẩn hóa.

## 8. Ngoài phạm vi (YAGNI)
- Không NLP cho text (brand/feedback chỉ đếm/đọc thô).
- Không heatmap linh hoạt chọn chiều (chỉ 3 cross-tab cố định).
- Không export, không trend theo thời gian (có thể sprint sau).
- Không thêm quyền mới, không migration.

## 9. Phase triển khai
- **Phase 1:** mục 4.1 + 4.2 + 5 + 6 (section 1-6, 8). Phần lớn lặp pattern hiện có.
- **Phase 2:** mục 4.3 + section 7 (3 cross-tab + StackedBarChart).

## 10. Đã hoàn thành (2026-05-26)

Triển khai trên branch `customer-portrait-dashboard-20260526` (tách từ master sau khi Phase 0 đã merge).

- **Phase 1:** `report_fields.py` (config), refactor `summary()` config-driven + các trường giới tính/tỉnh/tiềm năng/đau/thói quen/massage/thư giãn/nguồn/lý do không chốt/thời lượng gọi/brand-top + độ phủ dữ liệu; frontend chia section + `ReportSection`/`DistributionTable`.
- **Phase 2:** `report_crosstabs.py` (3 cross-tab) gắn vào response; frontend `StackedBarChart` + section "Phân tích chéo".
- **Tests:** report_fields/service/api/crosstabs đều PASS (full suite customers chỉ còn 1 fail pre-existing `test_customer_order_aggregate_api`).
- **Review:** APPROVED_WITH_MINOR_ISSUES; đã bổ sung `pain_duration` vào độ phủ. "nhu cầu" trong độ phủ bỏ qua (nằm ở bảng OrderNeed riêng, tránh query thừa).

**Sự cố quá trình:** một session song song trong cùng working dir đã đổi branch giữa chừng khiến 4 commit đáp nhầm sang `resale-appointment-lark-reminder-20260526`; đã gỡ: cherry-pick sang branch riêng off master và trả branch kia về đúng commit của họ.
