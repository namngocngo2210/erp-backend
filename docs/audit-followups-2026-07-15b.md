# Audit 2026-07-15 đợt 2 (b) — Plan & nhật ký

Scope: 13 nhánh merge sau điểm audit sáng 15/07 (`e2c97b17..07265a9e`, ~11.5k dòng):
sale-contacts-page, hr-department-management (+move-depts, +remove-picker), issue-report-
include-stock-issue-docs, flow-export-matched-notes + dedup-shipping-code, orders-recall-
code-filter, orders-filter-chip-bar, hr-employee-filters (+persist), customer-type follow-up,
category-sku, logo-spinner. Branch: `fix/audit-followups-2026-07-15b`.

**Sạch đáng ghi nhận** (5 agent xác minh, đừng re-flag): recall-code filter parity đủ mọi
endpoint (`~Exists` không seq-scan, cột non-null nên nhánh "không" không sót vế NULL);
chip-bar đủ 21 filter (gotcha hasRecallCode trong memory đã được vá); customer-type đổi
định nghĩa "mới = 0 đơn giao" đồng bộ cả 3 nơi; HR duyệt nghỉ chặn IDOR chéo phòng +
self-approve (test đủ); issue-report disjoint A/B xác minh tận nguồn ghi movement, footer
luôn đúng; contacts không giả mạo được staff, migration ContactActivity chuẩn.

## Đã fix

### CAO-1 — Sale contacts lộ PII toàn công ty với scope CHANNEL/DEPARTMENT
`contact_list_service.py` + `contact_activity_view.py` + `contact_activity_service.py`.
Service chỉ phân biệt OWN vs không-OWN (DataScope có 4 mức) → user `contacts:view` scope
CHANNEL/DEPARTMENT không bị filter: thấy toàn bộ danh bạ + đọc/ghi timeline mọi khách;
user CHANNEL chưa gán kênh (đáng lẽ 0) thấy tất cả. **Fix**: `_apply_contacts_scope` dùng
`resolved_channel_codes` (cùng quy ước `_apply_user_channel_scope` trang Đơn) cho list +
order_count; activity thêm `customer_in_channels` — 404 ngoài kênh. Test mới
`test_contact_scope.py` (CHANNEL thấy đúng kênh / không kênh thấy 0 / activity 404 + 200).

### TRUNG-1 — Contacts: BE không ép cửa sổ ngày (DoS ~7s/request)
Gọi thẳng API bỏ param ngày → DISTINCT ON toàn bảng orders + materialize hết ra Python
(chốt chặn chỉ ở FE). **Fix**: service tự clamp — thiếu `date_from` thì suy `anchor − 92
ngày` (anchor = date_to hoặc hôm nay). `date_from` tường minh giữ nguyên.

### TRUNG-2 — HR: chuyển phòng để lại DepartmentHead/TeamMember mồ côi
Trưởng phòng Sales chuyển sang Booking vẫn duyệt được đơn nghỉ Sales, vẫn lead/member
team phòng cũ. **Fix**: `EmployeeService.update` khi đổi phòng dọn `DepartmentHead` khác
phòng, `TeamMember` khác phòng, unset `Team.lead` khác phòng (cùng transaction); phòng thủ
chiều sâu ở `_is_department_head_of_leave` (head phải CÒN cùng phòng + chưa nghỉ việc).
Test: transfer → 403 + row bị dọn; stale row → 403.

### TRUNG-3 — Issue-report view "Theo SKU" hụt per-row (window)
SKU có phiếu kho mới nhưng lần xuất-theo-đơn cũ rớt khỏi top-window → dòng hiện
`SL theo đơn = 0` trong khi footer aggregate đúng → màn hình tự mâu thuẫn. **Fix**:
`skus_page` truy bù aggregate order cho đúng tập pid thiếu (1 query bounded). Test
window=1 + phiếu mới nhất.

### TRUNG-4 — Export: mất note MỚI khi quét lại cùng mã
`scan_notes_by_code` để dict ghi đè theo Meta.ordering `-created_at` → bản CŨ NHẤT thắng
(docstring nhầm "code+kind duy nhất" — unique chỉ ép trên PENDING). **Fix**: gộp TẤT CẢ
note cũ → mới, "; " ngăn cách, dedupe chuỗi trùng. Test 2 scan cùng mã note khác nhau.

### TRUNG-5 — Return export: dedup nuốt cả dòng lẫn note của scan FAILED
Scan FAILED trùng mã phiếu trả (guard chống trùng chặn quét lại) bị loại khỏi khối chờ
khớp mà không gộp bù — issue_export xử đúng ca này, return thì không. **Fix**:
`return_export` dựng `scan_notes_by_code(kind=RETURN)` và gộp vào dòng phiếu cùng mã
(nối "; " nếu phiếu có note riêng). Test FAILED-dup → 1 dòng, note giữ.

### THẤP kèm theo
- `sheet3_view` parse `has_recall_code` (chặn tái diễn tiền lệ filter-parity) + dọn comment
  stale nhắc OrdersFilterPanel đã xoá.
- `contact_activity` cap `call_meta` (phải là dict, ≤4000 ký tự serialize) — log
  append-only không có đường xoá.
- Dispatch history: gộp 2 query kho+performer thành 1; performer = user NON-NULL mới nhất
  (reversal hệ thống created_by=NULL không xoá trắng tên người thực xuất) — đóng luôn 2
  THẤP treo từ audit `flow-history-performed-by`.
- Org list không hiện head đã nghỉ việc.

## Ghi nhận KHÔNG fix (chờ quyết / theo dõi)
- Contacts `_last_journey_event_map` chỉ tra đơn MỚI NHẤT mỗi khách (ContactActivity thì
  theo khách) — có thể là chủ đích design §5, cần xác nhận nếu thấy `pending` sai ở khách
  nhiều đơn.
- HR self-approve edge tài khoản 1-N (`_employee_id_for_user` dùng `.first()`) — rất hẹp.
- Issue-report: đơn net=0 vẫn hiện ở nhánh order (phiếu net=0 thì ẩn) — bất đối xứng
  hiển thị sẵn có; excel SKU cắt ở window 5000.
- HR localStorage filter dùng chung máy (pattern sẵn có, chỉ là preference).

## Câu hỏi nghiệp vụ treo (tồn từ trước)
1. Hoàn MỘT PHẦN; 2. Nhắc chấm công Chủ nhật; 3. "Tổng data nhà mạng" đếm đơn hay khách.

## Nhật ký
- CAO-1 + TRUNG-1..5 + THẤP: XONG. Test mới: contacts scope (5), HR transfer/stale (2),
  SKU backfill (1), export notes (2).
- Verify: contacts 50 pass, hr 134 pass, inventory export/SKU 53 pass; suite
  orders+inventory+hr+customers chạy nền (xem MR).
