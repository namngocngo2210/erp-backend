# Thiết kế: Phân quyền sâu (RBAC + ABAC)

- **Ngày:** 2026-06-14
- **Trạng thái:** Đã duyệt thiết kế tổng thể, chờ review spec
- **Branch:** `feat/deep-rbac-permissions`
- **Liên quan:** `docs/architecture-standard.md` (luật ranh giới module), commit `727f0b3` (channel-level write authz)

## 1. Bối cảnh & vấn đề

Hệ phân quyền hiện tại là **coarse-grained**, chỉ làm được 2 việc:

1. **Page-level access** — `Department.allowed_sections` (JSONField list), kiểm bằng `require_section()` decorator (`backend/apps/users/permissions.py`) + `User.has_section_access()` (`backend/apps/users/models.py`). Frontend ẩn/hiện menu qua `RequireAuth` + `modules.ts`.
2. **Channel scope** — `UserChannel` M2M (`backend/apps/channels_core/models.py`), nhưng **hardcode chỉ cho phòng ADS** (`DepartmentID.ADS` trong `order_service_helpers.py`). Write authz theo kênh qua `UserService.can_edit_order()` (`backend/apps/users/services.py`) — True/False cho cả đơn.

**Giới hạn:**
- Role cứng trong code (`UserRole`: ADMIN/STAFF), không tạo được role tùy biến.
- Quyền chỉ là "thấy/không thấy page" — chưa tách hành động (view ≠ create ≠ edit ≠ delete ≠ approve ≠ export).
- Chưa có field-level (vd: giá vốn/lợi nhuận/SĐT chỉ một số role thấy).
- Data scope bị hardcode cho ADS, không tổng quát (own/department/channel/all).
- 1 user chỉ thuộc 1 phòng ban (`User.department` FK).

**Mục tiêu:** Nâng cấp lên engine **RBAC (role-based) + ABAC (attribute/scope-based)** đầy đủ 4 chiều: action-level, field-level, record/data-scope, custom role + multi-department. Triển khai theo **strangler pattern**, zero-regression.

## 2. Quyết định kiến trúc

| # | Quyết định | Lý do |
|---|---|---|
| D1 | **Role là entity bậc 1, tách khỏi Department** | Chuẩn RBAC, tạo role tùy biến trong UI, gán chéo phòng ban. |
| D2 | **Permission là catalog tĩnh định nghĩa trong code**, sync xuống DB | Permission gắn với code (endpoint/feature) nên không để user tự bịa; sync DB để có FK + hiển thị UI + audit. |
| D3 | **Bảng chuẩn hóa (M2M thật), không nhét JSON** | Cần UI quản trị + audit + index chuẩn. |
| D4 | **Strangler, giữ tương thích ngược** | `User.role` + `allowed_sections` giữ lại, derived từ hệ mới, xóa ở sprint cuối. Không big-bang. |
| D5 | **Quyền user = hợp (union) của các role** | Nhiều role cộng dồn quyền; quy tắc union rõ ràng (mục 4). |

## 3. Mô hình dữ liệu

```
Permission(id, code unique, section, action, label, created_at)
   code dạng "orders:edit"
   action ∈ {view, create, edit, delete, approve, export}
   → seed từ catalog code (management command / migration), không cho user tạo

Role(id, name unique, description, is_system, scope_default, is_active, created_at)
   is_system=true → role hệ thống (Administrator), khóa không cho sửa/xóa
   scope_default ∈ {all, department, channel, own}  (mặc định nếu không có RoleSectionScope)

RolePermission(id, role_fk, permission_fk)
   unique_together (role, permission)

RoleSectionScope(id, role_fk, section, scope)        ← ABAC override theo section
   scope ∈ {all, department, channel, own}
   unique_together (role, section)

RoleFieldRule(id, role_fk, section, field, rule)      ← field-level
   rule ∈ {hidden, readonly}
   unique_together (role, section, field)

UserRole(id, user_fk, role_fk)
   unique_together (user, role)

UserDepartment(id, user_fk, department_fk, is_primary)
   unique_together (user, department)
```

**Index:** FK đều index mặc định; thêm composite index `RolePermission(role, permission)`, `UserRole(user)`, `RoleSectionScope(role, section)` để resolution nhanh. `Permission.code` unique index.

**Tương thích ngược:**
- `User.role` giữ nguyên đến S5. Data migration: ADMIN → gán role `Administrator` (is_system); STAFF → gán role auto-gen từ `Department.allowed_sections`.
- `Department.allowed_sections` giữ làm derived/read-only đến S5.
- `UserChannel` giữ nguyên — trở thành nguồn dữ liệu cho scope `channel` (không còn hardcode ADS).

## 4. Quy tắc hợp quyền (union semantics)

Khi user có nhiều role, quyền hiệu dụng tính như sau — **luôn theo hướng cộng quyền**:

- **Permission:** union tập hợp. Có ở bất kỳ role nào ⇒ có.
- **Data scope (theo section):** lấy **rộng nhất**. Thứ tự `all > department > channel > own`. Nếu role A cho `own`, role B cho `all` trên cùng section ⇒ hiệu dụng `all`.
- **Field rule:** lấy **ít hạn chế nhất**. `hidden > readonly > (không rule = full)`. Nếu một role cho thấy/sửa field ⇒ field hiện/sửa được, bất kể role khác cấm.

> Lưu ý thiết kế: union "rộng nhất thắng" nghĩa là thêm role chỉ nới quyền, không siết. Đây là hành vi RBAC chuẩn, dễ suy luận cho admin. Nếu sau này cần "deny override" (cấm thắng cho phép) thì là thay đổi lớn, ngoài phạm vi spec này.

## 5. Backend — Resolution & enforcement (Django/DRF)

### 5.1 PermissionResolver (service)

```
EffectivePermissions(user):
    permission_codes: set[str]                # {"orders:view", "orders:edit", ...}
    scope_by_section:  dict[str, str]          # {"orders": "channel", ...}
    field_policy:      dict[str, dict[str,str]]# {"orders": {"cost_price": "hidden"}}
```

- Tính bằng cách gom tất cả role của user → union theo quy tắc mục 4.
- **Cache Redis** theo `user_id`, kèm cache-version (tái dùng pattern `bump_channels_cache_version`). Bust khi: sửa role, gán/bỏ role, sửa RolePermission/RoleSectionScope/RoleFieldRule, đổi UserChannel/UserDepartment.
- Admin (role `is_system` Administrator) short-circuit = full quyền (giữ hành vi cũ).

### 5.2 Ba chốt chặn

1. **Action-level** — DRF permission class `require_permission("orders:edit")`. Giữ `require_section(*s)` làm shim = `s:view` để view hiện tại không vỡ.
2. **Record-level scope** — hàm trung tâm `scope_queryset(user, section, qs)`:
   - `all` → không lọc
   - `department` → lọc theo phòng ban của user (qua UserDepartment)
   - `channel` → lọc theo `UserChannel` (tổng quát hóa logic ADS cũ)
   - `own` → lọc theo creator/owner field của model
   - Thay thế `order_channel_codes_scope()` đang hardcode `DepartmentID.ADS`.
3. **Field-level** — `field_policy` tính 1 lần/request, đưa vào serializer context:
   - Output: drop field `hidden`.
   - Write: deny/strip field `hidden|readonly` (trả 403 hoặc bỏ qua tùy field, mặc định strip + log).
   - Không dùng `SerializerMethodField` per-field (tránh N+1).

### 5.3 Module boundary

Code RBAC đặt trong `apps/users` (chủ quyền model Role/Permission). Các app khác (orders, channels_core, …) chỉ **gọi qua service/permission class công khai** của `apps/users`, không truy cập trực tiếp bảng RBAC — tuân luật ranh giới module (`docs/architecture-standard.md`).

## 6. Frontend (React SPA)

- `/api/users/me` trả thêm: `permissions: string[]`, `scopes: dict`, `field_policy: dict`. Giữ `allowed_sections` (derived = section có bất kỳ action) để code cũ không vỡ.
- Hook `usePermission()` → `can("orders:edit")`.
- Component `<Can permission="orders:edit">…</Can>` ẩn/khóa button & action (không chỉ menu).
- Field-level FE: dựa `field_policy` để ẩn cột/disable input (BE vẫn là nguồn enforcement thật, FE chỉ UX).
- **Trang quản trị Role** (trong section `users`):
  - CRUD role.
  - Ma trận Role × (section, action) checkbox.
  - Cấu hình `scope` theo section, `field_rule` theo field.
  - Gán nhiều role + nhiều phòng ban cho user.

## 7. Lộ trình (sprint)

| Sprint | Nội dung | Giá trị | Rủi ro |
|---|---|---|---|
| **S1** | Migration bảng Permission/Role/RolePermission/UserRole/UserDepartment + catalog seed + data migration ánh xạ trạng thái cũ + `PermissionResolver` + cache. **Chưa đổi hành vi runtime.** | Nền tảng | Thấp (chỉ thêm) |
| **S2** | `require_permission` + `<Can>` + `/me` mở rộng + trang quản trị Role + ma trận **action-level** | Phân quyền CRUD/duyệt/export theo role tùy biến | Trung bình |
| **S3** | `scope_queryset` tổng quát + migrate hardcode ADS → `RoleSectionScope` | Data scope own/dept/channel/all | Trung bình (đụng query đơn) |
| **S4** | `RoleFieldRule` + serializer enforcement cho field nhạy cảm (giá vốn, lợi nhuận, SĐT) | Field-level | Trung bình |
| **S5** | Bật multi-department thực sự + dọn field cũ (`User.role`, `allowed_sections`) | Hoàn tất, gỡ nợ kỹ thuật | Cao (xóa field) → làm cuối |

Mỗi sprint: có migration + unit test (mở rộng `backend/apps/orders/tests/test_channel_scope_mutation_authz.py` + test mới cho resolver/scope/field). Bật dần qua chính hệ phân quyền mới.

## 8. Catalog Permission khởi tạo (S1)

Sinh từ `VALID_SECTIONS` (34 section) × action phù hợp. Mặc định mỗi section có `view`; section nghiệp vụ (orders, customers, stock, …) thêm `create/edit/delete`; section có nghiệp vụ duyệt/xuất (orders, stock-documents, audit, …) thêm `approve`/`export` theo nhu cầu thực tế. Danh sách action cho từng section chốt khi làm S1 (rà từng view hiện có để map đúng endpoint).

## 9. Rủi ro & giảm thiểu

- **Hiệu năng resolution mỗi request** → cache Redis + tính 1 lần/request.
- **Quên enforce ở endpoint mới** → permission class là default trên ViewSet base, view phải khai báo permission rõ ràng; thêm test smoke quét endpoint thiếu permission.
- **Data migration sai ánh xạ** → migration idempotent, có backup, đối chiếu số user/section trước-sau, dry-run trên staging.
- **Field-level rò rỉ** → BE là nguồn enforcement; test snapshot serializer theo từng role.

## 10. Ngoài phạm vi (YAGNI)

- Deny-override (cấm thắng cho phép) — hiện dùng union "cho phép thắng".
- Time-based / conditional permission (vd lock sau N ngày).
- Phân quyền theo từng bản ghi cụ thể (per-record ACL) — chỉ làm scope theo nhóm (own/dept/channel/all).
- Đồng bộ phân quyền với Lark.
