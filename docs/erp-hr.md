# Module Nhân sự (HR / HCNS) — Thiết kế

> Trạng thái: **Draft thiết kế** · Ngày: 2026-06-16 · Phase 3 (theo `erp-master-plan.md`)
> Chuẩn ràng buộc: `architecture-standard.md` (luật ranh giới module), `events/README.md` (event bus).

## 1. Mục tiêu & phạm vi

Thêm năng lực quản lý hồ sơ nhân sự cho phòng HCNS, **tách bạch danh tính/đăng nhập (`User`) khỏi hồ sơ nhân sự (`Employee`)**, nối nhau bằng quan hệ 1-1. Module `apps/hr` sở hữu toàn bộ dữ liệu nghiệp vụ nhân sự; `apps/users` giữ nguyên vai trò auth + RBAC.

### Quyết định nền tảng (đã chốt)

| Mã | Quyết định | Lý do |
|----|------------|-------|
| H1 | **Mapping = `Employee.user` OneToOne, bắt buộc** | "Mọi NV đều có User" → mapping đơn giản, không xử lý nullable. "User" mang nghĩa *danh tính hệ thống*, không bắt buộc là tài khoản login. |
| H2 | **HR sở hữu `Employee`** (Cách A), không nhồi field vào `User`, không đặt profile trong `apps/users` | Tuân luật ranh giới module; `User` không phình; dễ tách service sau. |
| H3 | **Build native, nhập tay trong ERP** (không sync Lark ở giai đoạn này) | Chủ động dữ liệu; tránh phụ thuộc tầng sync ngay từ đầu. Có thể bổ sung connector Lark sau. |
| H4 | **Department dùng lại model có sẵn** trong `apps/users` (master data tổ chức dùng chung) | Tránh 2 nguồn sự thật về phòng ban; RBAC đang phụ thuộc Department. |
| H5 | **`Employee.department` là nguồn chính, đồng bộ về RBAC qua domain event** | `apps/hr` không import ORM của `apps/users`; ranh giới sạch. |
| H6 | **Chia 3 sprint**, không gộp 1 lần | Phạm vi gồm 4 subsystem độc lập; chấm công là module lớn, để sau. |

### Phân rã sprint

- **Sprint 1 — Hồ sơ NV + Phòng ban + Sơ đồ tổ chức + mapping User.** (nền tảng, chi tiết bên dưới)
- **Sprint 2 — Hợp đồng & lương cơ bản** (lịch sử immutable theo chuẩn tài chính).
- **Sprint 3 — Chấm công / nghỉ phép** (cân nhắc kỹ build vs để Lark đảm nhiệm).

Tài liệu này đặc tả đầy đủ **Sprint 1**; Sprint 2–3 chỉ phác thảo, sẽ chi tiết hóa sau.

---

## 2. Sprint 1 — Đặc tả chi tiết

### 2.1 Data model

```python
# apps/hr/models.py  — HR sở hữu bảng
class Employee(models.Model):
    user            = OneToOneField(settings.AUTH_USER_MODEL, on_delete=PROTECT,
                                    related_name="employee")
    employee_code   = CharField(max_length=20, unique=True)        # tự sinh: NV0001...
    full_name       = CharField(max_length=150)                    # tên hồ sơ HR
    department      = ForeignKey("users.Department", on_delete=PROTECT,
                                 related_name="employees")
    manager         = ForeignKey("self", null=True, blank=True, on_delete=SET_NULL,
                                 related_name="reports")
    job_title       = CharField(max_length=150, blank=True)
    employment_status = CharField(choices=EmploymentStatus, default=PROBATION)
    hire_date       = DateField(null=True, blank=True)
    resign_date     = DateField(null=True, blank=True)
    # liên hệ cá nhân — HR-only, KHÔNG để ở User
    personal_email  = EmailField(blank=True)
    phone           = CharField(max_length=20, blank=True)
    dob             = DateField(null=True, blank=True)
    gender          = CharField(choices=Gender, blank=True)
    id_number       = CharField(max_length=20, blank=True)         # CCCD
    address         = CharField(max_length=255, blank=True)
    deleted_at      = DateTimeField(null=True, blank=True)         # soft delete
    created_at      = DateTimeField(auto_now_add=True)
    updated_at      = DateTimeField(auto_now=True)

    class Meta:
        db_table = "hr_employees"
        indexes = [Index(["department"]), Index(["manager"]),
                   Index(["employment_status"]), Index(["deleted_at"])]
```

**EmploymentStatus**: `probation` (thử việc), `active` (chính thức), `on_leave` (tạm nghỉ), `resigned` (đã nghỉ).

- `manager` self-FK = sơ đồ tổ chức đệ quy; validate chống cycle ở application service.
- `Department` **không tạo mới** — dùng `users.Department` (master data dùng chung).
- `on_delete=PROTECT` cho `user`: `User` dùng soft-delete (`deleted_at`); không xóa cứng làm mất hồ sơ NV.

### 2.2 Cấu trúc module (clean architecture)

```
apps/hr/
├── domain/                  # thuần Python, không import django
│   ├── employee_code.py     # sinh mã NV
│   └── org_chart.py         # validate cycle manager
├── application/
│   └── services/employee_service.py   # orchestrate: create/update + tạo User + publish event
├── contracts/
│   ├── employee_service.py  # Protocol/interface cho module khác gọi
│   └── hr_events.py         # EmployeeDepartmentChanged, EmployeeCreated, EmployeeResigned
├── infrastructure/          # (rỗng ở Sprint 1)
├── transport/
│   ├── views/employee_views.py
│   └── serializers/employee_serializers.py
├── repositories.py          # EmployeeRepository(BaseRepository[Employee])
├── models.py
├── migrations/
└── tests/
```

Luồng phụ thuộc: `transport → application → domain`; truy cập `User` chỉ qua **service tạo user** (không import ORM users trong hr).

### 2.3 Mapping & lifecycle (phần cốt lõi)

- **Tạo Employee → tạo User** trong cùng `transaction.atomic()`. Service nhận cờ `create_login: bool`:
  - `create_login=False`: `User.set_unusable_password()`, `is_active=False` (NV không cần đăng nhập: kho, tài xế…).
  - `create_login=True`: tạo username/email + mật khẩu tạm, `is_active=True`.
- **Đổi phòng ban**: `EmployeeService.change_department()` → `event_bus.publish_on_commit(EmployeeDepartmentChanged(user_id, department_id))`.
- **Nghỉ việc**: `employment_status=resigned` + `User.is_active=False` (không xóa — giữ lịch sử `Order.staff`).

### 2.4 Đồng bộ Department → RBAC qua event (H5)

```python
# apps/users/<app>.ready():  handler không nằm trong apps/hr
@subscribe("hr.employee.department_changed")
def sync_user_department(event: EmployeeDepartmentChanged) -> None:
    # cập nhật User.department + UserDepartment (is_primary) + invalidate cache RBAC
    ...
```
→ `apps/hr` chỉ phát event; `apps/users` tự đồng bộ. Không module nào import ORM chéo. RBAC scoping (channel/department) tiếp tục đọc từ `User`/`UserDepartment` như hiện tại.

### 2.5 RBAC

- Thêm section `"employees"` (và nhóm module `"hr"`) vào `apps/users/rbac_catalog.py`:
  `"employees": ("view", "create", "edit", "delete", "export")`.
- Migration seed permission mới + gán cho role `Administrator` (system role tự full access nên chủ yếu để các role khác cấp được).
- View dùng `HasPermission`/scope hiện có; mặc định scope `own` cho section chưa cấp (xem gotcha RBAC).

### 2.6 API (DRF, prefix `/api/hr/`)

| Method | Path | Mô tả |
|--------|------|-------|
| GET | `/api/hr/employees` | List + filter (`department`, `status`, `q`=tên/mã/phone), phân trang |
| POST | `/api/hr/employees` | Tạo NV (kèm tạo User theo `create_login`) |
| GET | `/api/hr/employees/{id}` | Chi tiết |
| PATCH | `/api/hr/employees/{id}` | Cập nhật (đổi dept → phát event) |
| DELETE | `/api/hr/employees/{id}` | Soft delete |
| GET | `/api/hr/employees/{id}/org-chart` | Cây `reports` của NV |

Tránh N+1: `select_related("user", "department", "manager")`.

### 2.7 Frontend (feature-slice + app-shell)

```
src/features/hr/
├── pages/EmployeeListPage.tsx · EmployeeDetailPage.tsx · OrgChartPage.tsx
├── components/EmployeeFormDialog.tsx   # dùng FormDialogShell (size=form)
├── queries.ts    # TanStack Query: useEmployees, useEmployee, mutations
└── types.ts      # Employee, EmploymentStatus...
```
- Đăng ký module `hr` vào `src/app/modules.ts` (icon `Users`, basePath `/hr`, sections: `employees`, `departments`).
- Tái dùng `src/lib/format.ts`; shadcn/ui + lucide-react; không `any`.

### 2.8 Migration dữ liệu hiện có (H1 chiều ngược)

Data migration backfill **1 `Employee` cho mỗi `User` đang `is_active`**, trừ tài khoản bot/system (vd `lark`, tài khoản kỹ thuật superuser thuần). Mỗi bản ghi:
- sinh `employee_code` tuần tự,
- `full_name` từ `display_name`/`first_last_name`,
- `department` map từ `User.department` (nếu null → để trống, HCNS bổ sung sau),
- các field HR còn thiếu để rỗng cho HCNS nhập dần,
- `employment_status=active`.

Backup trước khi chạy trên prod (theo quy trình hiện có).

### 2.9 Testing

- **Unit (domain/service):** sinh mã NV duy nhất; chống cycle org chart; `create_employee` tạo User atomic (rollback khi lỗi); phát đúng event khi đổi dept.
- **Integration (API):** CRUD + filter; scope RBAC theo section `employees`; handler `sync_user_department` cập nhật `User.department` + invalidate cache.
- **Migration:** backfill đúng số lượng, bỏ qua bot/system, idempotent.

### 2.10 Definition of Done (PR)

- [ ] Không `import` model/ORM của `apps/users` trong `apps/hr` (chỉ qua service `create_user`/contracts).
- [ ] Side-effect đồng bộ department đi qua event bus, không gọi internal của module kia.
- [ ] Migration backfill chạy được, có backup, idempotent.
- [ ] Test unit + integration xanh; ruff sạch.
- [ ] Frontend module `hr` xuất hiện trong app-launcher; CRUD hoạt động.

---

## 3. Phác thảo Sprint 2–3 (chưa chi tiết)

### Sprint 2 — Hợp đồng & lương cơ bản
- `EmploymentContract`, `SalaryRecord` là bảng con của `Employee`.
- Lịch sử thay đổi **append-only/immutable** (chuẩn tài chính); mọi thay đổi tạo bản ghi mới.
- Có thể phát event cho module Kế toán (Phase 2) tiêu thụ sau.

### Sprint 3 — Chấm công / nghỉ phép
- Cân nhắc kỹ build vs tích hợp Lark (Lark đang làm tốt mảng này).
- Nếu build: `Attendance`, `LeaveRequest` + workflow duyệt; nếu tích hợp: connector đồng bộ từ Lark API.

---

## 3b. Sprint 1 — Tiến độ (cập nhật 2026-06-16)

**Đã làm (branch `feature/hr-employee-module`):**
- Model `Employee` (OneToOne→User bắt buộc, FK `users.Department`, self-FK `manager`, soft-delete) + migration `0001`.
- Domain: sinh mã NV (`employee_code.py`), chống cycle org chart (`org_chart.py`).
- Event contracts `EmployeeCreated/EmployeeDepartmentChanged/EmployeeResigned`.
- `EmployeeService` create/update/resign/**delete** — tạo User+Employee atomic, publish event on-commit.
- `EmployeeRepository`; serializers (read + create + **update với alias `department`→`department_id`**); API `/api/hr/employees` (CRUD + `/org-chart`).
- RBAC: thêm section `employees` vào `PERMISSION_CATALOG` **và `VALID_SECTIONS`**; seed migration `users/0018`.
- Đồng bộ phòng ban→RBAC qua event: `apps/users/subscribers.py` (đăng ký trong `ready()`).
- Backfill `Employee` cho User active (trừ bot `lark`/inactive) — migration `0002`.
- FE: feature-slice `src/features/hr` (types, api, queries, EmployeesPage + table + form dialog); đăng ký module `hr` + route `/hr/employees` + i18n.
- Test: backend 30 pass (`apps/hr`), FE 2 pass (`src/features/hr`); ruff + tsc + build sạch.

**Sửa từ review:** (1) update dùng raw `request.data` làm rớt đổi phòng ban + không bắn event → thêm `EmployeeUpdateSerializer` validate + alias; (2) DELETE không vô hiệu hóa User → `EmployeeService.delete` deactivate User + event.

**Chưa làm / nợ kỹ thuật (chuyển follow-up hoặc Sprint sau):**
- FE form dùng `Input` số cho `department_id` (chưa có API list phòng ban) → cần endpoint + dept-picker (`Select`).
- `FormDialogShell.onSubmit` có lỗi type tiềm ẩn (submit qua button onClick) — lỗi của component dùng chung, ngoài phạm vi.
- `manager` có thể trỏ tới NV đã nghỉ/xóa mềm — chưa validate, để follow-up.
- Mã NV pad `:04d` (giới hạn NV9999) — đủ cho quy mô 100-150, nâng `:05d` nếu cần.
- Test baseline đỏ không liên quan: `test_sync_creates_all_catalog_codes`, `test_db_settings`, `modules.test.ts` (fixture thiếu `lead-assignment`/`roles`).

## 3c. Sprint 2 — Tiến độ (cập nhật 2026-06-16)

**Đã làm (cùng branch `feature/hr-employee-module`):**
- Model `EmploymentContract` + `SalaryRecord` (append-only) + enum `ContractType` (migration `0003`).
- Domain `close_previous` (đóng bản lương cũ khi thêm bản mới); `SalaryService.set_salary` (append-only, **lock row + ràng buộc 1 bản current/NV**), `ContractService.sign`.
- Events `SalaryChanged` / `ContractSigned` (publish on-commit, để Kế toán tiêu thụ sau).
- Repositories `ContractRepository` / `SalaryRepository`; API nested: `/api/hr/employees/{id}/salaries` (GET history + POST set), `/api/hr/employees/{id}/contracts` (GET + POST sign).
- FE: `EmployeeDetailPage` với tab Thông tin / Hợp đồng / Lương; types + api + queries; row table điều hướng tới chi tiết.
- Sửa flake test-isolation: handler `_on_department_changed` re-register idempotent + conftest autouse (`EventBus.is_subscribed`).
- Test: backend **45 pass** (chạy `--create-db` để áp ràng buộc partial-unique), FE 4 pass; ruff/tsc/build sạch.

**Review tìm & sửa (Critical+Important):** (C1) `set_salary` thiếu lock → race 2 bản current → thêm `select_for_update` + `UniqueConstraint(employee, is_current=True)` (migration `0004`); (I1) `base_salary` âm → `min_value=0`; (I2) hợp đồng `end_date < start_date` → validate.

**⚠️ Lưu ý deploy:** migration `0004` (partial-unique 1 lương current/NV) sẽ fail nếu prod đang có NV >1 bản `is_current=True` — kiểm tra dữ liệu trước khi chạy (hiện dữ liệu lương là mới nên thường an toàn).

**Chưa làm / nợ:** `created_by_id` là BigInteger không FK (decouple users — chấp nhận); chưa có UI sửa/đóng hợp đồng (append-only, chỉ thêm); dept-picker vẫn nợ từ Sprint 1.

## 3d. Sprint 3 — Tiến độ (cập nhật 2026-06-16)

**Quyết định:** build native nghỉ phép + chấm công tối thiểu (không sync Lark giai đoạn này).

**Đã làm (cùng branch):**
- Model `LeaveRequest` (workflow trạng thái) + `AttendanceRecord` (unique `employee+work_date`) + enum `LeaveType/LeaveStatus` (migration `0005`).
- Domain `count_leave_days` / `assert_can_decide`; `LeaveService.request/approve/reject` (lock row khi duyệt), `AttendanceService.record` (upsert theo ngày, chịu được race).
- Event `LeaveApproved` (on-commit). Repositories `LeaveRepository` / `AttendanceRepository`.
- RBAC: thêm action `employees:approve` (catalog + seed migration `users/0019`).
- API: `/api/hr/leave-requests` (list/create), `/leave-requests/{id}/approve|reject`, `/employees/{id}/attendance` (list/record).
- FE: trang `/hr/leave` (danh sách + duyệt/từ chối theo quyền `employees:approve` + dialog tạo đơn); section `leave` map quyền về `employees`.
- Test: backend **61 pass** (`--create-db`), FE 5 pass; ruff/tsc/build sạch.

**Review tìm & sửa (Important):** (1) chấm công insert đồng thời cùng ngày → 500 → bắt `IntegrityError` + re-get (savepoint); (2) tạo đơn với `employee_id` không tồn tại → 500 → guard 404.

**Chưa làm / nợ (follow-up):** chưa chặn đơn nghỉ trùng ngày (`assert_no_overlap` để sau); attendance POST luôn trả 201 kể cả update (cosmetic); form dùng `employee_id` thô (cần employee-picker); chưa tích hợp Lark.

## 3e. Tổng kết HR Phase 1 (Sprint 1–3)

Module `apps/hr` (modular monolith, clean architecture) đã có: hồ sơ NV ↔ User (OneToOne), phòng ban + org chart, hợp đồng & lương (immutable), nghỉ phép (workflow duyệt) + chấm công cơ bản. Ranh giới module sạch (không import ORM chéo; đồng bộ qua event bus). RBAC section `employees` (view/create/edit/delete/approve/export). FE feature-slice `src/features/hr` + module app-shell `/hr/*`.

**Tổng test:** backend `apps/hr` 61 pass; FE `src/features/hr` 5 pass. Branch `feature/hr-employee-module` (1 MR).
**Lưu ý deploy:** chạy migration theo thứ tự; migration `0004` (1 lương current/NV) cần dữ liệu lương sạch (mới nên an toàn); migration backfill `0002` tạo Employee cho User active (trừ bot) — backup trước khi chạy prod.
**Nợ kỹ thuật gom lại:** manager picker (Select) cho form FE; `assert_no_overlap` nghỉ phép; lỗi type `FormDialogShell.onSubmit` (component dùng chung); cân nhắc connector Lark khi cần.

## 3f. Cải tiến form NV (cập nhật 2026-06-16, branch `feature/hr-employee-edit-fields`)

- **Mã NV cho sửa được:** tạo mới tự sinh `NV…` nếu để trống, hoặc nhập tay (trùng → 400); edit đổi được mã (trùng → 400). `latest_code()` chỉ xét mã prefix `NV` để chuỗi auto-gen không bị mã tùy chỉnh làm hỏng.
- **Phòng ban = dropdown:** thêm endpoint `GET /api/hr/departments` (quyền `employees:view`, trả phòng ban active) vì `/api/users/departments` bị chặn bởi quyền `users`+admin. Form dùng `<Select>`.
- **Chức vụ = dropdown 3 mức:** `job_title` thành choices `staff`/`manager`/`intern` (Nhân viên/Trưởng phòng/Thực tập sinh) + `job_title_display`; FE Select; giá trị free-text cũ coerce về rỗng khi edit để không bị từ chối.
- Test: backend 71 pass, FE 8 pass. → giải quyết nợ "department picker" (manager picker vẫn nợ).

## 4. Liên kết tài liệu
- `erp-master-plan.md` — D2 modular monolith, Phase 3 HR.
- `architecture-standard.md` — luật ranh giới module, clean architecture BE/FE.
- `events/README.md` — cơ chế event bus (`publish_on_commit`/`subscribe`).
