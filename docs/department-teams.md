# Đội nhóm trong phòng ban (`teams`) — thiết kế & tiến độ

**Ngày:** 2026-08-23 · **Branch:** `feat/department-teams`

## 1. Vấn đề

Trang Phòng ban đã có phần đội nhóm từ trước (`hr_teams` + `DepartmentTeamsManager`),
nhưng **không dùng được**. Đo prod ngày 23/08:

```
departments: 17 · hr_teams: 0 · hr_team_members: 0 · hr_department_heads: 0
```

Không một dòng nào sau cả tháng. Nguyên nhân không phải người dùng lười — ô chọn
thành viên **gần như không ra ai**:

| Phòng ban | Tài khoản đang hoạt động | Không có hồ sơ HR | Hồ sơ HR ở phòng khác | **Chọn được** |
| --- | ---: | ---: | ---: | ---: |
| Sales | 14 | 5 | 8 | **1** |
| Booking | 7 | 3 | 4 | **0** |
| Ads | 7 | 2 | 4 | **1** |
| Kho | 4 | 3 | 1 | **0** |
| Kế toán | 1 | 1 | 0 | **0** |
| Hành chính nhân sự | 1 | 0 | 0 | **1** |

Toàn hệ thống **3/38 tài khoản** gán được vào đội nhóm. Phòng Sales có 14 người,
ô chọn hiện đúng 1 — nhìn như tính năng hỏng.

Hai nguyên nhân chồng lên nhau:

1. **Ô chọn lấy từ `hr.Employee`, không phải `users.User`.** 15/38 tài khoản
   không có hồ sơ nhân sự nào nên vĩnh viễn không xuất hiện, sửa bộ lọc kiểu gì
   cũng vô ích.
2. **Hai hệ phòng ban song song.** Phòng ban vận hành là id 1–5 (Sales, Ads,
   Booking, Kho, Kế toán) — nơi mọi tài khoản đang nằm. Đợt nhập file HCNS ngày
   17/06 tạo thêm bộ id 18–29 (Vận hành sàn TMĐT, Kho vận, Sáng tạo truyền
   thông…) và gắn 137 hồ sơ nhân sự vào đó. Hai bộ không map với nhau: cùng một
   người, tài khoản ở "Sales" còn hồ sơ ở "Vận hành sàn TMĐT", nên lọc
   `department=Sales` trả về rỗng.

Lý do phụ khiến tính năng vô hình: endpoint gác `is_superuser` cứng
(`apps/hr/.../department_org_views.py::_is_dept_admin`), admin HCNS bấm vào là
403 câm lặng. Và giao diện chỉ nằm trong dialog Sửa, bảng phòng ban không có dấu
hiệu nào cho biết đội nhóm tồn tại.

## 2. Thiết kế

Đội nhóm là khái niệm **vận hành**, gắn với **tài khoản đăng nhập** (`users.User`),
không gắn hồ sơ nhân sự. Vì vậy model do module `users` sở hữu — `users` là module
nền tảng, `hr` được phép đọc ngược lên sau này để dựng sơ đồ tổ chức.

### 2.1 Bảng `teams`

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `id` | bigserial | PK |
| `department_id` | FK → `departments` | `CASCADE` — xoá phòng ban thì nhóm đi theo |
| `name` | varchar(100) | Tên nhóm |
| `lead_id` | FK → `users`, null | **Trưởng nhóm**. `SET_NULL` |
| `is_active` | bool | |
| `deleted_at` | timestamptz, null | Xoá mềm |
| `created_at` / `updated_at` | timestamptz | |

Ràng buộc: `UNIQUE(department_id, name) WHERE deleted_at IS NULL` — trùng tên
trong cùng phòng ban là lỗi, nhưng nhóm đã xoá không chiếm chỗ tên.

Index: `department_id`, `deleted_at`.

`lead_id` dùng `SET_NULL` chứ không `PROTECT`: vô hiệu hoá tài khoản trưởng nhóm
là việc thường ngày của HCNS, không được để nó nổ giữa chừng. Nhóm mất lead vẫn
là nhóm hợp lệ, admin gán lại sau.

### 2.2 Bảng `team_members`

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `id` | bigserial | PK |
| `team_id` | FK → `teams` | `CASCADE` |
| `user_id` | FK → `users` | `CASCADE` |
| `created_at` | timestamptz | |

Ràng buộc **duy nhất**: `UNIQUE(team_id, user_id)`.

**Một người được ở nhiều nhóm**, kể cả nhiều nhóm trong cùng một phòng ban. Đây
là quyết định nghiệp vụ đã chốt — không thêm ràng buộc nào khác. Ràng buộc trên
chỉ chặn việc gán trùng cùng một người vào cùng một nhóm hai lần.

Index: `team_id`, `user_id`.

### 2.3 Ai được vào nhóm — chỗ sửa lỗi cốt lõi

Ứng viên của nhóm thuộc phòng ban X = **tài khoản `User` đang hoạt động thuộc
phòng ban X**, xác định bằng `apps.users.department_membership.department_member_q`:

```python
Q(department_id=X) | Q(department_links__department_id=X)
```

Tức là **phòng chính (`User.department` FK) HOẶC phòng kiêm nhiệm
(`UserDepartment` M2M)**. Dùng helper sẵn có chứ không so sánh cứng
`user.department_id == X`, vì người kiêm nhiệm (NV Sale kiêm chạy ads) phải xuất
hiện ở cả hai phòng — đúng lý do helper này ra đời.

Nhớ `.distinct()`: join M2M nhân dòng.

Riêng thay đổi này đưa phòng Sales từ 1 lên 14 ứng viên, Booking từ 0 lên 7.

### 2.4 Bất biến: đổi phòng ban thì dọn vai trò phòng cũ

Khi `User.department` đổi hoặc `UserDepartment` bị gỡ, phải dọn ngay trong cùng
transaction:

- gỡ `TeamMember` của những nhóm thuộc phòng user **không còn** thuộc về;
- `Team.lead` trỏ tới user đó ở phòng cũ → set `NULL`.

Không dọn thì thành viên mồ côi: người đã chuyển sang Booking vẫn là trưởng nhóm
của một nhóm bên Sales. Luật này bê nguyên từ `apps/hr/.../employee_service.py`
(audit 2026-07-15b) — bài học đã trả giá một lần, không lặp lại.

Điểm móc: `apps/users/subscribers.py` và `RoleService.set_user_departments`.

### 2.5 `hr` giữ nguyên tuyệt đối

`hr_teams`, `hr_team_members`, `hr_department_heads`, `department_org_service`,
`department_org_views`, 5 route `/api/hr/...` và bộ test của chúng **không bị
đụng tới**. Chúng trở thành nhánh không còn dùng ở frontend nhưng vẫn xanh, vẫn
chạy được. Dọn dẹp là việc của lần sau.

**Trưởng phòng nằm ngoài phạm vi.** Trưởng phòng (`hr_department_heads`) và
trưởng nhóm (`teams.lead_id`) là hai khái niệm khác nhau. Ô "Trưởng phòng" trong
dialog Sửa phòng ban giữ nguyên như hiện tại, vẫn đọc/ghi qua API `hr`, kể cả khi
nó đang mắc đúng lỗi ô chọn mô tả ở §1. Hàm duyệt đơn nghỉ phép
`_is_department_head_of_leave` cũng giữ nguyên.

## 3. API

Tất cả nằm dưới `/api/users` — đúng module sở hữu model.

| Method | Đường dẫn | Việc |
| --- | --- | --- |
| GET | `/api/users/departments` | Bổ sung `team_count` vào payload sẵn có |
| GET | `/api/users/departments/{id}/teams` | Danh sách nhóm của phòng, kèm lead + thành viên |
| POST | `/api/users/departments/{id}/teams` | Tạo nhóm |
| PATCH | `/api/users/teams/{id}` | Sửa tên / lead / thành viên |
| DELETE | `/api/users/teams/{id}` | Xoá mềm |
| GET | `/api/users/departments/{id}/assignable-users` | Ứng viên gán được, payload gọn |

`member_user_ids` trong PATCH là **replace-set**: gửi lên danh sách nào thì nhóm
còn đúng danh sách đó. Giữ nguyên ngữ nghĩa của API `hr` cũ để giao diện không
phải đổi cách nghĩ.

`assignable-users` là endpoint riêng chứ không tái dùng `/api/users/admin`: danh
sách admin trả kèm roles, kênh, mapping KiotViet — thừa cho một ô select — và nó
không có bộ lọc phòng ban. Payload gọn `{id, display_name, username}`.

**Quyền:** GET → `users:view`, POST/PATCH/DELETE → `users:edit`. Tách bạch đọc và
ghi ngay từ đầu, không lặp lại lỗi gác quyền ghi bằng `:view` (audit 2026-08-19
mục 2). Bỏ `is_superuser` cứng của API `hr` cũ để admin HCNS dùng được.

## 4. Frontend

- Component quản lý nhóm mới đặt tại `features/users` — đúng module sở hữu, trang
  Phòng ban thôi phụ thuộc ngược vào `features/hr`.
- Nó **thay chỗ** `DepartmentTeamsManager` trong dialog Sửa phòng ban. Component
  cũ trong `features/hr` để nguyên tại chỗ cùng test của nó (§2.5).
- Ô chọn người đổi nguồn: `useEmployees` → `useAssignableUsers(departmentId)`.
- Bảng phòng ban thêm cột **Số nhóm**. Đây là phần trả lời trực tiếp câu "phòng
  ban đang chưa có đội nhóm": hôm nay phải bấm Sửa mới thấy, nên coi như không có.

## 5. Sprint

| Sprint | Nội dung | Trạng thái |
| --- | --- | --- |
| 1 | Model + migration + service (gồm luật dọn ở §2.4) + unit test tầng service | ✅ |
| 2 | 6 endpoint + quyền + test API + regen `schema.yaml` | ✅ |
| 3 | Frontend: component mới, đổi nguồn ô chọn, cột Số nhóm + test | ✅ |

### Sprint 1 — đã xong

Migration `users/0042`. `apps/users/team_services.py` với `assignable_users`,
`create_team`, `update_team`, `delete_team`, `teams_for_department`,
`sync_after_department_change`. 28 test ở `apps/users/tests/test_team_services.py`.
Hồi quy `apps/users` + `apps/hr`: 288 passed. `ruff check .` sạch.

Hai việc phát sinh ngoài dự kiến:

- **`related_name` phải là `staff_teams`, không phải `teams`.** `hr.Team` đã chiếm
  `Department.teams`; giữ nguyên `hr` nghĩa là chấp nhận tên xấu hơn cho model mới.
  Django báo `fields.E304` chứ không im lặng, nên không có rủi ro ngầm.
- **Luật dọn ở §2.4 viết kiểu ĐỐI SOÁT, không phải bắt sự kiện.**
  `sync_after_department_change(user)` nhìn trạng thái phòng ban hiện tại rồi gỡ
  phần thừa, nên idempotent và gọi được ở cả hai đường ghi
  (`RoleService.set_user_departments` và subscriber `hr.employee.department_changed`)
  mà không sợ gọi trùng. Nó cũng tự sửa được dữ liệu đã lệch từ trước.

### Sprint 2 — đã xong

`apps/users/transport/views/users_views_impl/team_views.py` với 3 view, 4 route mới,
4 serializer. 19 test ở `apps/users/tests/test_team_api.py`. Hồi quy `apps/users` +
`apps/hr`: 307 passed. `ruff` sạch, `import-linter` KEPT.

Ba việc phát sinh:

- **Tên component `Team` đã bị `hr` chiếm trong `schema.yaml`.** Đặt serializer là
  `TeamSerializer` thì drf-spectacular ÂM THẦM cho endpoint mới trỏ vào schema của
  `hr` — frontend sinh ra `lead: EmployeeSummary` và payload `lead_employee_id`,
  sai hoàn toàn, không một lỗi nào báo. Chỉ lộ ra khi đọc kỹ file schema sinh lại.
  Đã đổi tên thành `DepartmentTeam*`. Đây là hệ quả trực tiếp của việc giữ `hr`.
- **`schema.yaml` được ghép TAY, không regen toàn bộ.** Regen đầy đủ đổi 1141 dòng
  trong đó 1120 dòng là nhiễu của tính năng khác đã tồn đọng từ trước. Chỉ ghép 241
  dòng thuộc phần này; nợ regen để một MR chore riêng.
- **`team_count` sinh N+1 mới trên danh sách user admin**, bị
  `DepartmentChannelsNPlusOneTests` bắt tại chỗ. Đã thêm prefetch
  `department__staff_teams` cạnh `department__channels` trong `UserService`.

### Sprint 3 — đã xong

`src/lib/api/teams.ts`, 5 hook trong `features/users/queries.ts`,
`features/users/components/DepartmentTeamsManager.tsx` thay chỗ component `hr` trong
dialog Sửa phòng ban. 7 test mới (2 file). Suite frontend: 184 file / 1393 test xanh
(baseline đo lúc bắt đầu là 182/1386 — đúng bằng phần thêm vào). `tsc --noEmit`,
`eslint`, `prettier` sạch.

Hai việc phát sinh:

- **Số đội nhóm trên thẻ phòng ban vốn ĐÃ CÓ nhưng đọc nhầm nguồn.** Nó lấy
  `department-org` của `hr` nên luôn hiện 0. Nay đọc `team_count` của chính phòng ban.
- **`tsc` bắt được một lỗi hợp đồng mà backend không hề báo.** `lead` sinh ra type
  non-null trong khi nhóm chưa có trưởng nhóm là `null`, và `members` sinh ra
  `unknown[]`. Phải khai `allow_null=True` cho nested serializer và
  `@extend_schema_field(TeamUserSerializer(many=True))` cho `SerializerMethodField`.
  Đây là lý do bước regen type frontend phải nằm TRONG sprint, không để sau.

### Merge master 24/08

Master đi tiếp 11 commit trong lúc làm. Hai điểm phải xử lý tay:

- **Trùng số migration.** Master thêm `users/0041_drop_customer_explorer_export`, nhánh
  này cũng đang là `0041` → hai leaf node trong app `users`, Django từ chối chạy. Đã đổi
  tên thành `0042_team_teammember_...` và trỏ `dependencies` sang `0041` của master.
  `makemigrations --check` trả `No changes detected`.
- **`schema.yaml` và `schema.d.ts` conflict** — cả hai là file sinh tự động nên lấy bản
  master rồi sinh lại từ code nhánh này, không merge tay. Kết quả: **0 dòng bị xoá khỏi
  bản master**, chỉ thêm 241 dòng (schema) và 272 dòng (type). Tiện thể khoản nợ 1120
  dòng schema tồn đọng đã được master trả ở commit `bffe32a8`, nên nhánh này không còn
  gánh nữa.

Hồi quy sau merge: backend `apps/users` + `apps/hr` + `apps/analytics` **386 passed**;
frontend **183 file / 1381 test xanh** (số khác lần trước vì master thay
`customer-explorer` bằng `customer-360`); `ruff`, `import-linter`, `tsc` sạch.

## 6. Nợ kỹ thuật ghi nhận

- **Hai hệ phòng ban song song** (id 1–5 vận hành vs 18–29 HR) vẫn còn nguyên.
  Thiết kế này đi vòng qua nó bằng cách không phụ thuộc hồ sơ HR, chứ không giải
  quyết nó. Hợp nhất hai bộ là một việc riêng, cần HCNS đối chiếu tay.
- **105/141 hồ sơ nhân sự mang trạng thái `resigned`**, trong đó 66 hồ sơ không có
  ngày nghỉ việc, tất cả nhập ngày 17/06. Không hồ sơ nào còn tài khoản hoạt động
  nên nhiều khả năng đúng là danh sách nhân sự cũ. Cần HCNS xác nhận.
- **`hr` còn nguyên nhánh đội nhóm/trưởng phòng không dùng tới** ở frontend (§2.5).
- **Ô "Trưởng phòng" vẫn mắc lỗi ô chọn của §1** — cố ý để ngoài phạm vi.
