# Mở rộng hồ sơ nhân sự + Backfill từ file HCNS chuẩn

## Bối cảnh

File `HCNS_DESI.xlsx` (sheet **"DATA NHÂN SỰ CHUẨN"**, 115 nhân sự, 46 cột) là
nguồn dữ liệu nhân sự chuẩn của công ty. Bảng `hr_employees` hiện tại chỉ có ~15
field nên không chứa được phần lớn thông tin (CCCD ngày/nơi cấp, học vấn, ngân
hàng, BHXH/MST, liên hệ khẩn cấp, con cái, hôn nhân, xe...). Mục tiêu:

1. **Mở rộng bảng `employee`** để chứa đầy đủ các cột của file HCNS.
2. **Backfill** dữ liệu đã chuẩn hoá vào DB, tái lập được và idempotent.
3. **Đổi quan hệ Employee ↔ User sang 1-N** (một nhân sự có thể gắn nhiều tài
   khoản đăng nhập) — theo yêu cầu để dọn dữ liệu trùng sau này.

## Quyết định thiết kế

| Vấn đề | Quyết định | Lý do |
|---|---|---|
| Mã NV (Excel `D001…` có **trùng** D047/D071/D100, thiếu D084) | Giữ mã D, khử trùng bằng hậu tố `-2/-3` theo thứ tự dòng | Ổn định, idempotent; `employee_code` là khoá upsert |
| Phạm vi | Import cả 115 (35 ON + 80 OFF) | Giữ lịch sử nhân sự đầy đủ |
| Quan hệ với User | **Employee 1-N User** qua bảng nối `EmployeeAccount` (thuộc module `hr`) | "Mỗi module sở hữu bảng của nó" (`docs/architecture-standard.md` §1.4). FK đặt ở `hr` ⇒ chiều phụ thuộc `hr → users`; KHÔNG đặt FK ở `users.User` (sẽ khiến module nền tảng phụ thuộc ngược) |
| Nhóm field mở rộng | Thêm thẳng ~22 cột vào `hr_employees` | Hầu hết là quan hệ 1-1 |
| Con cái (0-N) | Tách bảng `hr_employee_children` | Quan hệ 1-nhiều |
| Tài khoản cho hồ sơ import | KHÔNG tạo User | Người đã nghỉ/CTV chỉ là hồ sơ HR; gắn account sau khi đối soát |

## Mô hình dữ liệu

- **`Employee`** (`hr_employees`): bỏ field `user` (OneToOne cũ); thêm
  `position_level` (enum), `position_title`, `work_type` (enum), `lark_account`,
  `official_date`, `resign_reason`, `marital_status` (enum), `id_issued_date`,
  `id_issued_place`, `permanent_address`, `education_level`, `major`, `school`,
  `tax_code`, `social_insurance_no`, `bank_account_no`, `bank_name`,
  `emergency_contact_{name,relation,phone,address}`, `facebook_url`,
  `vehicle_info`. Thêm property `primary_user` (tài khoản chính, ưu tiên
  `is_primary` rồi account cũ nhất; `None` nếu chưa gắn).
- **`EmployeeAccount`** (`hr_employee_accounts`): `employee` FK →
  `accounts`; `user` OneToOne → `employee_account`; `is_primary`. Ràng buộc:
  tối đa 1 account `is_primary` mỗi nhân sự.
- **`EmployeeChild`** (`hr_employee_children`): `employee` FK → `children`,
  `name`, `birth_year`.

### Enum mới
- `PositionLevel`: director / team_lead / specialist / staff / collaborator
- `WorkType`: fulltime / parttime / remote
- `MaritalStatus`: single / married / divorced / other

## Chuẩn hoá dữ liệu (generator)

`backend/apps/hr/data/build_seed_from_xlsx.py` đọc xlsx → sinh
`employee_profiles_seed.json`. **Cả xlsx lẫn seed JSON đều KHÔNG commit vào repo**
(chứa PII: CCCD, STK ngân hàng, địa chỉ, SĐT) — đã thêm vào `.gitignore`. Chỉ
commit **generator** + **command** + **schema**. Operator sinh seed tại chỗ lúc
deploy rồi chạy import. Quy tắc chuẩn hoá chính:

- **Giới tính**: "Nam"→male, "Nữ/nu"→female.
- **Trạng thái**: OFF→`resigned`; ON + "Thử việc"→`probation`; ON + "Chính
  thức"→`active`.
- **Hình thức làm việc**: Toàn thời gian→fulltime, Bán thời gian→parttime, Remote.
- **Chức danh**→`position_level` (ánh xạ keyword).
- **Hôn nhân** (14 biến thể bẩn): fuzzy → single/married/divorced; rỗng nếu nhiễu.
- **Học vấn** (30 biến thể): giữ **text thô** (không ép enum) ở `education_level`.
- **Con cái**: tách năm sinh bằng regex `(19|20)\d{2}`, phần còn lại là tên; bỏ
  giá trị nhiễu ("Không/không/.").
- **Ngày**: datetime → ISO date.

Phân bố sau chuẩn hoá: employment_status {resigned 80, active 32, probation 3};
work_type {fulltime 84, parttime 28, remote 3}; 13 nhân sự có con (22 con).

## Backfill

`python manage.py import_hr_profiles [--path ... | --dry-run]`
(`backend/apps/hr/management/commands/import_hr_profiles.py`):

- Idempotent: `update_or_create` theo `employee_code`.
- `get_or_create` Department theo tên (tạo mới nếu chưa có).
- Thay thế toàn bộ children mỗi lần chạy (không nhân đôi).
- KHÔNG tạo User.

## An toàn migration (`0009_employee_profile_expansion`)

Thứ tự thao tác để **không mất liên kết tài khoản hiện có trên prod**:
1. Thêm field mới + tạo bảng `EmployeeAccount`, `EmployeeChild`.
2. `RunPython` copy `Employee.user_id` (các NV employee hiện có) →
   `EmployeeAccount(is_primary=True)`.
3. Mới `RemoveField` `Employee.user`. (Có reverse: gán lại account chính về
   `Employee.user`.)

Migration `0002` đã được **đóng băng inline** (dùng model lịch sử còn field
`user`) để độc lập với code live đã đổi sang model 1-N.

## Ảnh hưởng code

- `EmployeeService.create/delete/resign/update`: tạo/đọc tài khoản qua
  `EmployeeAccount`; `delete/resign` vô hiệu hoá **tất cả** user liên kết; event
  dùng `_primary_user_id`.
- `_employee(request)` ở self-service & checkin: `request.user.employee_account.employee`.
- Serializer: `username` (tài khoản chính) + `usernames` (danh sách); thêm các
  field hồ sơ mới + `children`.
- `migrations_logic.backfill_employees` đổi chữ ký (thêm tham số `EmployeeAccount`).

## Trạng thái

### Đã làm
- [x] Generator + seed JSON (115 bản ghi, đã kiểm tra phân bố/parse).
- [x] Model + enum + 2 bảng mới; migration 0009 (copy an toàn user→account).
- [x] Management command import (idempotent, dry-run).
- [x] Serializer/service/repository/views cập nhật sang 1-N.
- [x] Sửa toàn bộ test HR theo quan hệ mới + test import + test 1-N.
- [x] **110/110 test HR pass** (pytest — runner chuẩn của dự án).

### Chưa làm / lưu ý vận hành
- [ ] **Chạy `migrate` + `import_hr_profiles` trên dev/prod lúc deploy** (chưa
  migrate DB dev vì app dev đang chạy code cũ — sẽ vỡ khi cột `user` bị xoá; chỉ
  migrate sau khi deploy code mới).
- [ ] Đối soát & gộp employee trùng (NV cũ ↔ D mới của cùng một người), gắn
  nhiều account về 1 nhân sự canonical.
- [ ] `EmployeeDepartmentChanged` hiện chỉ sync phòng ban cho **tài khoản chính**;
  nếu cần sync mọi account liên kết thì mở rộng sau.
- [ ] Auto-gen mã NV (form tạo mới) vẫn dùng prefix `NV`; mã `D` chỉ dùng cho dữ
  liệu import. Thống nhất prefix sau nếu cần.
- [ ] FE: bổ sung form hiển thị/sửa các field hồ sơ mới (ngoài phạm vi sprint này).
