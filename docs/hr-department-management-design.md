# Thiết kế: Quản lý phòng ban (trưởng phòng + team)

**Ngày:** 2026-07-15
**Trạng thái:** Sprint 1 & 2 — đang thực hiện
**Branch:** `feat/hr-department-management`

## 1. Bối cảnh & mục tiêu

Trang "Quản lý phòng ban" hiện có (khu **Quản trị**, section `departments` — `DepartmentManager.tsx`) chỉ tập trung vào **phân quyền**: `name`, `is_active`, `allowed_sections`, kênh bán hàng. Model `Department` (`apps.users`) tối giản, không có thông tin tổ chức nhân sự.

Yêu cầu bổ sung (giữ nguyên vị trí trang ở Quản trị):
1. **Gán trưởng phòng** cho mỗi phòng ban — chọn từ danh sách nhân sự (`hr.Employee`).
2. **Tạo team nhỏ trong phòng ban** — mỗi team gồm: tên, team lead, thành viên.
3. Trưởng phòng / team lead **gắn nghiệp vụ** (duyệt nghỉ phép) — tách Sprint 2.

Quyết định chốt với người dùng:
- Team lead & thành viên **chỉ lấy từ nhân sự thuộc phòng ban đó**.
- Trưởng phòng/lead **có ý nghĩa nghiệp vụ** (duyệt) → Sprint 2.

## 2. Ràng buộc kiến trúc

`Department` ở `apps.users`, `Employee` ở `apps.hr`. Chiều phụ thuộc bắt buộc: **hr → users** (hr được biết users, không ngược lại). `Employee` đã FK tới `users.Department`, nên mọi model mới tham chiếu `Employee` **đặt trong app `hr`** và trỏ về `users.Department` — không phá layering.

UI vẫn nằm ở trang Quản trị; trang này gọi thêm endpoint HR để đọc/ghi head + team. Model `users.Department` **không đổi**.

## 3. Data model (app `hr`)

```
DepartmentHead                     # 1 trưởng phòng / 1 phòng ban
  department  OneToOne(users.Department, related_name="hr_head")
  employee    FK(hr.Employee, PROTECT)
  assigned_at DateTime

Team                               # nhóm nhỏ trong phòng ban
  department  FK(users.Department, related_name="teams")
  name        CharField
  lead        FK(hr.Employee, null=True, SET_NULL)
  is_active   Bool = True
  deleted_at  DateTime null        # soft-delete chuẩn hr
  created_at / updated_at
  unique(department, name) WHERE deleted_at IS NULL

TeamMember
  team        FK(Team, related_name="members")
  employee    FK(hr.Employee)
  created_at
  unique(team, employee)
```

**Validate (service):** `DepartmentHead.employee`, `Team.lead`, mọi `TeamMember.employee` phải có `employee.department_id == department.id` tương ứng → `ValidationError`/400 nếu sai. Team lead khi lưu tự được thêm vào `members` (idempotent).

Index: `Team(department)`, `Team(deleted_at)`, `TeamMember(team)`.

## 4. API (app `hr`)

Gate **cùng luật với trang phòng ban ở Quản trị**: `require_section("users")` + admin (`_is_admin`). Đọc bulk cũng theo gate này (dữ liệu quản trị).

| Method | Endpoint | Body / Ghi chú |
|---|---|---|
| `GET` | `/api/hr/department-org` | `{items:[{department_id, head, teams:[{id,name,lead,members}]}]}`. FE merge theo `department_id`. Tránh N+1 (prefetch). |
| `PUT` | `/api/hr/departments/{id}/head` | `{employee_id: number\|null}` — gán/gỡ. |
| `POST` | `/api/hr/departments/{id}/teams` | `{name, lead_employee_id?, member_employee_ids:[]}` → 201. |
| `PATCH` | `/api/hr/teams/{team_id}` | `{name?, lead_employee_id?, member_employee_ids?}` (members = replace-set). |
| `DELETE` | `/api/hr/teams/{team_id}` | Soft-delete (204). |

Picker nhân sự **tái dùng** `/api/hr/employees?department={id}&state=working` — không thêm API.

Serializer: `EmployeeSummary` gọn `{id, employee_code, full_name, position_title}` (không lộ PII).

## 5. Frontend

Mở rộng trang Quản trị > Phòng ban. `DepartmentManager.tsx` đã lớn (~470 dòng) + trong allowlist `react-select` → **code mới tách component riêng** dùng `components/ui` (cấm `react-select`):

- `features/hr/components/DepartmentHeadField.tsx` — `searchable-select` chọn trưởng phòng (nhân sự đang làm việc trong phòng).
- `features/hr/components/DepartmentTeamsManager.tsx` — list/thêm/sửa/xoá team; mỗi team: tên + lead (`searchable-select`) + thành viên (`multi-select`), giới hạn nhân sự trong phòng.
- Mount vào dialog Sửa phòng ban; card hiển thị "Trưởng phòng · N team".
- `lib/api/hr-departments.ts` typed từ `schema.d.ts`; React Query trong `features/hr/queries.ts` (`useDepartmentOrg` + mutations, invalidate sau ghi).

## 6. Sprint 2 — duyệt nghỉ phép theo trưởng phòng (additive)

Hiện `leave_approve`/`leave_reject` chỉ gate `employees:approve` (bất kỳ ai có quyền). Bổ sung **cộng dồn, không phá vỡ**: cho phép duyệt/từ chối nếu **người dùng là trưởng phòng** của phòng ban chứa nhân sự xin nghỉ (dò qua `DepartmentHead` → `EmployeeAccount.user`), **hoặc** vẫn có `employees:approve`. Không thu hẹp quyền hiện tại.

Luật tối thiểu Sprint 2: chỉ trưởng phòng (không xét team lead ở bước này để tránh mơ hồ nhiều tầng duyệt); mở rộng team-lead-duyệt để lần sau nếu cần.

## 7. Phân rã & tiến độ

### Sprint 1 — Quản lý cấu trúc (làm ngay)
- [ ] Models + migration
- [ ] Repository + service (validate) + serializers
- [ ] 5 endpoint + wire urls
- [ ] Unit test (validate) + endpoint test
- [ ] Regen schema.d.ts
- [ ] FE api typed + React Query
- [ ] UI: head field + teams manager mount vào trang Quản trị
- [ ] FE test + typecheck + eslint

### Sprint 2 — Duyệt nghỉ phép theo trưởng phòng (additive)
- [ ] Helper `_can_approve_leave` (head OR employees:approve)
- [ ] Sửa `leave_approve`/`leave_reject`
- [ ] Test: head duyệt được đơn phòng mình, không duyệt được phòng khác

### Đã làm / chưa làm (cập nhật sau mỗi sprint)

**Sprint 1 — HOÀN THÀNH:**
- Models `DepartmentHead` / `Team` / `TeamMember` + migration `0013`.
- `DepartmentOrgService` (validate nhân sự thuộc phòng ban, lead tự thành member, replace-set members) + `TeamRepository`/`DepartmentHeadRepository`.
- 5 endpoint: `GET /api/hr/department-org`, `PUT .../head` (204), `POST .../teams`, `PATCH /teams/{id}`, `DELETE /teams/{id}`. Gate = admin/superuser (như trang phòng ban).
- Serializers typed (members = `EmployeeSummary[]`, lead nullable) → schema.d.ts regen.
- FE: `lib/api/hr-departments.ts` + React Query (`useDepartmentOrg` + mutations); component `DepartmentHeadField` + `DepartmentTeamsManager` (dùng `components/ui`, không react-select) mount vào dialog Sửa phòng ban ở Quản trị; card hiện "Trưởng phòng · N team".
- Test: 11 endpoint/service test BE (pass), 3 component test FE (pass).

**Sprint 2 — HOÀN THÀNH:**
- `leave_approve`/`leave_reject` cho phép duyệt nếu là trưởng phòng của nhân sự xin nghỉ (cộng dồn với `employees:approve`, không thu hẹp quyền cũ). Test: head duyệt được đơn phòng mình, 403 với phòng khác.

**Chưa làm / follow-up:**
- Team lead duyệt nghỉ phép (hiện chỉ trưởng phòng) — mở rộng sau nếu cần.
- Migration `0013` cần chạy trên prod khi deploy.
- QA thị giác trang Quản trị > Phòng ban (head field + teams manager trong dialog).
