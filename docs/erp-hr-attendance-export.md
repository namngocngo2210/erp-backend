# HR — Export bảng chấm công tháng & Bảng lương (thiết kế)

Ngày: 2026-07-13 · Trạng thái: Sprint 1 đang làm · Branch: `feat/hr-attendance-export`

## Bối cảnh

Kế toán/HCNS đang chấm lương bằng file `BẢNG LƯƠNG 2026.xlsx` (mỗi tháng 1 sheet
`T0X.2026`, ~27 cột, công thức TỔNG `=G/26*D*0.85 + G/26*E + H+I+K+M+N−O−P`).
Nguồn số công/số bữa trưa/đi muộn hiện tổng hợp tay. Hệ thống đã có dữ liệu chấm
công chính xác từng giây (`AttendanceEvent`/`AttendanceRecord` + ảnh + GPS), cần:

1. **Sprint 1 (doc này)**: export Excel bảng chấm công tháng — mỗi nhân sự đủ
   ngày 1→cuối tháng, cột giờ làm thực tế, phút đi muộn, phút về sớm.
2. **Sprint 2 (kế tiếp)**: bảng "Cơ chế lương" per nhân sự (công chuẩn, tỉ lệ
   KPI, phụ cấp trách nhiệm/ăn trưa/khác — HCNS chỉ điền 3 cột thủ công) + lệnh
   sinh sheet lương tháng đúng format `T0X.2026`.

## Quy tắc nghiệp vụ đã chốt với CEO (2026-07-13)

- Giờ làm chuẩn: **08:00–17:30**, nghỉ trưa **12:00–13:30** → ngày công = 8h
  (2 buổi: sáng 08:00–12:00 = 4h, chiều 13:30–17:30 = 4h).
- Công chuẩn 26 ⇒ làm **T2–T7**; Chủ nhật là ngày nghỉ (vẫn hiện dữ liệu nếu có
  chấm công, ghi chú "CN").
- **Tính công ngày** (rule nửa công): xét từng buổi — khoảng [giờ vào, giờ ra]
  phủ **đủ** buổi nào thì buổi đó auto **0.5 công**; buổi bị **thiếu** thì tính
  phần giờ làm thực tế trong buổi đó **/ 8h**. Công ngày = tổng 2 buổi (làm tròn
  2 chữ số).
- **Đi muộn** = max(0, giờ vào − 08:00) phút; **Về sớm** = max(0, 17:30 − giờ ra)
  phút. (Người nghỉ phép nửa ngày sẽ có số phút lớn — đối chiếu cột ghi chú Phép.)
- Quy đổi phạt đi muộn ra tiền: **ngoài phạm vi** (HCNS tự xử lý, cột O bảng lương).
- Giờ chuẩn cấu hình qua env (`HR_WORK_*`), không hardcode.

## Thiết kế Sprint 1

### Backend (module `hr`, clean architecture)

- `apps/hr/domain/workday.py` — pure function, không phụ thuộc Django ORM:
  `WorkSchedule` (4 mốc giờ, parse từ settings) + `evaluate_workday(check_in,
  check_out, schedule) -> WorkdayResult(late_minutes, early_minutes,
  worked_minutes, workday_credit)` theo rule trên.
- `apps/hr/application/services/attendance_export_service.py` —
  `build_month_export(month: "YYYY-MM")`: nhân sự đang làm việc trong tháng
  (loại người `resign_date` trước đầu tháng / `hire_date` sau cuối tháng),
  `AttendanceRecord` của tháng, `LeaveRequest` APPROVED giao tháng → trả cấu
  trúc dữ liệu thuần (rows chi tiết + rows tổng hợp) cho tầng render.
- `apps/hr/infrastructure/attendance_export_excel.py` — render openpyxl 2 sheet:
  - **Chi tiết**: Mã NV | Tên | Phòng ban | Ngày | Thứ | Giờ vào | Giờ ra |
    Đi muộn (phút) | Về sớm (phút) | Giờ làm thực tế | Công | Ghi chú
    (Phép / Quên chấm công / CN). Mỗi NV đủ mọi ngày trong tháng.
  - **Tổng hợp**: Mã NV | Tên | Phòng ban | Tổng công | Ngày có chấm công |
    Tổng đi muộn (phút) | Tổng về sớm (phút) | Số lần quên chấm công | Số ngày
    phép — input trực tiếp cho cột E (số công), L (số bữa trưa), O (căn cứ phạt)
    của bảng lương.
- Endpoint `GET /api/hr/attendance/export?month=YYYY-MM` → file
  `cham-cong-YYYY-MM.xlsx`; gate RBAC **`employees:export`** (action có sẵn
  trong catalog).
- Settings env: `HR_WORK_MORNING_START=08:00`, `HR_WORK_MORNING_END=12:00`,
  `HR_WORK_AFTERNOON_START=13:30`, `HR_WORK_AFTERNOON_END=17:30`.

### Frontend

- Trang **Lịch sử chấm công** (`AttendanceHistoryPage`): nút "Xuất Excel" cạnh
  bộ chọn tháng hiện có, gọi endpoint download blob; ẩn khi thiếu quyền
  `employees:export`.

### Ngoài phạm vi Sprint 1

- Tự tính tiền phạt đi muộn (cột O) — HCNS tự quy đổi.
- Tăng ca/OT, ca đặc thù theo phòng ban.
- Ghi sheet trực tiếp vào file `BẢNG LƯƠNG 2026.xlsx` gốc.

## Sprint 2 — Cơ chế lương + sinh bảng lương tháng (ĐÃ LÀM, branch `feat/hr-payroll-workbook`)

Quyết định chốt khi triển khai:
- Số bữa trưa (cột L) = **số ngày có chấm công đủ vào+ra** (khớp file gốc: L ≈ số ngày làm).
- RBAC: xem/sửa cơ chế lương gate **`employees:edit`** (chặt hơn `employees:view`
  vì dữ liệu lương); export bảng lương gate **`employees:export`**. Chưa tách
  section `payroll` riêng — cân nhắc khi phân quyền mịn hơn.
- Tách công thử việc (cột D) / chính thức (E) theo `Employee.official_date`
  (chưa có mốc + đang thử việc → toàn bộ vào D).

Đã triển khai:
1. Model `EmployeeCompensation` (OneToOne Employee, bảng
   `hr_employee_compensations`, migration 0012): công chuẩn (26), chuyên cần
   (300k khi công ≥ 24, 0 = tắt), PC trách nhiệm, tỉ lệ KPI (nullable), ăn
   trưa/bữa (30k), PC khác, BHXH (nullable = ô trống), ghi chú. Lương cơ bản
   vẫn ở `SalaryRecord.is_current`.
2. API: `GET /api/hr/compensations` (mọi NV đang làm việc, default khi chưa có
   bản ghi) + `PUT /api/hr/compensations/<employee_id>` upsert.
3. `GET /api/hr/payroll/export?month=YYYY-MM` → `bang-luong-YYYY-MM.xlsx`
   format sheet T0X.2026: 27 cột đúng nhãn/thứ tự, đổ sẵn team/tên/công D-E/
   công chuẩn/lương cơ bản/số bữa trưa/STK+ngân hàng; công thức sống H
   (`IF(E>=24,mức,0)`), M (`L*mức`), Q (TỔNG, chia theo F thay vì hardcode 26),
   W (chuyển ngoài), dòng Tổng; khối doanh thu (nhãn + SUM) để trống chờ điền.
   Cột K (lương KPI), O (phạt), S–V (thanh toán) để trống cho HCNS.
4. FE: trang **Cơ chế lương** (`/hr/compensation`, tab module HR, quyền
   employees) — bảng inline-edit per NV (lương cơ bản readonly), nút lưu từng
   dòng, chọn tháng + **Xuất bảng lương**.

## Tiến độ

- [x] Chốt rule nghiệp vụ với CEO (4 câu hỏi)
- [x] Sprint 1: domain + service + excel + endpoint + FE + tests (merged? — MR feat/hr-attendance-export)
- [x] Sprint 2: cơ chế lương + sinh bảng lương tháng (branch feat/hr-payroll-workbook)
- [ ] HCNS đối chiếu file sinh ra với bảng tính tay 1 tháng trước khi dùng chính thức

## Ghi chú bảo mật

`BẢNG LƯƠNG 2026.xlsx` (PII + lương) nằm ở repo root nhưng KHÔNG được commit —
đã thêm pattern vào `.gitignore` (cùng chính sách với `HCNS_DESI.xlsx` của module
hồ sơ nhân sự).
