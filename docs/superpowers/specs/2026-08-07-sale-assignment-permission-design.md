# Phân quyền gán sale phụ trách

Ngày: 2026-08-07 · Nhánh: `feat/sale-assignment-permission`

## Vấn đề

Trường "sale phụ trách" quyết định ai được tính doanh số. Hiện bất kỳ ai sửa được
đơn (`orders:edit`) đều đổi được trường này, trên cả ô select inline ở bảng đơn lẫn
form đơn. Sale có thể tự kéo đơn về mình hoặc đẩy đơn sang người khác mà không ai
chặn. Trường tương ứng trên khách thân thiết (`LoyalCustomerNote.assigned_sale`)
còn lỏng hơn: endpoint ghi chỉ gate bằng `loyal-customers:view`.

Rà cả codebase có **bốn** đường ghi trường này, không phải một:

| Đường | Serializer | Field policy |
|---|---|---|
| `POST /api/orders` (form tạo đơn) | `OrderCreateSerializer` | không |
| `PATCH /api/orders/{id}` (inline + form sửa) | `OrderInlineUpdateSerializer` | có `FieldPolicyWriteMixin` |
| Import Excel đơn / landing (cột "Nhân viên") | `OrderCreateSerializer` | không |
| `PATCH` note khách thân thiết | `LoyalCustomerNoteUpdateSerializer` | không |

Bịt mỗi ô select trên UI là vô nghĩa khi ba đường còn lại vẫn mở.

## Quyết định thiết kế

**Mặc định cấm, cấp mới được.** Quyền dương (`sale-assignment:edit`) thay vì rule
âm trên `RoleFieldRule`. `RoleFieldRule` fail-open: role mới tạo mà quên cấu hình
là lại đổi được sale. Với trường quyết định doanh số thì fail-closed đáng giá hơn
sự tiện lợi của việc chỉnh trên UI.

**Một section mới thay vì một action mới.** `PermissionAction` là `TextChoices` cố
định (view/create/edit/delete/approve/export); thêm action `assign` kéo theo
migration `alter_field` và phải sửa `ACTION_ORDER`/`ACTION_LABELS` ở
`RolesPage.tsx`. Thêm section `sale-assignment` với action `edit` sẵn có thì không
đụng gì tới enum.

Quyền là **một**, dùng chung cho cả bốn trang đơn lẫn khách thân thiết. "Được đổi
người ăn doanh số" là một chính sách công ty, không phải thuộc tính của từng trang;
tách thành năm code chỉ tạo ra năm ô tick luôn được tick cùng nhau. Khi thật sự cần
phân theo kênh, `RoleSectionScope` (scope `channel`) đã có sẵn để siết tiếp.

**Lúc tạo đơn: ép về chính mình, không chặn.** Chặn cứng sẽ làm sale không tạo nổi
đơn cho bản thân. Ép `staff = request.user` giữ luồng nhập đơn nguyên vẹn mà vẫn
đóng đường chuyển đơn trá hình qua bước tạo.

## Phạm vi

### 1. Tầng RBAC

- `apps/users/models.py` — `VALID_SECTIONS` thêm `"sale-assignment"`.
- `apps/users/rbac_catalog.py` — `"sale-assignment": ("edit",)`.
  Cố ý **không khai `view`**: đây là quyền hành vi, không phải trang. Khai `view`
  chỉ mời gọi người sau gate nhầm bằng `require_section("sale-assignment")`.
- `apps/users/rbac_catalog.py` — `"loyal-customers"` đổi từ `("view", "export")`
  thành `("view", "edit", "export")` (xem mục 3).
- Data migration trong `apps/users/migrations/` gọi `sync_permissions()` để prod có
  row `Permission` mới. Tiền lệ: migration 0028.
- `apps/users/permissions.py` — thêm:

  ```python
  SALE_ASSIGNMENT_PERMISSION = "sale-assignment:edit"

  def can_assign_sale(user) -> bool:
      from .auth_core import user_has_any_permission
      return user_has_any_permission(user, SALE_ASSIGNMENT_PERMISSION)
  ```

  Đặt cạnh `require_permission` vì cùng một nguồn sự thật
  (`resolve_effective_permissions`, có cache). Administrator (`is_system`)
  short-circuit full quyền nên không bị ảnh hưởng.

### 2. Bịt bốn đường ghi

**`PATCH /api/orders/{id}`** (`order_action_view.py`): trước khi dựng
`OrderInlineUpdateSerializer`, nếu payload có `staff_id` **và** giá trị khác
`order.staff_id` hiện tại **và** `not can_assign_sale(request.user)` → trả 403 với
message "Bạn không có quyền đổi sale phụ trách đơn."

Cố ý trả 403 thay vì strip im lặng như `FieldPolicyWriteMixin`: người dùng vừa chủ
động bấm đổi trên dropdown, nuốt lặng sẽ khiến họ tưởng đã đổi xong và chỉ phát
hiện ra khi đối soát doanh số. So sánh với giá trị hiện tại để payload gửi lại
đúng giá trị cũ (form gửi full object) không bị chặn oan.

**`POST /api/orders`** (`order_crud_view.py`): nếu không có quyền thì
- payload không có `staff_id`, hoặc `staff_id == request.user.id` → ép
  `validated_data["staff"] = request.user`;
- `staff_id` trỏ người khác → 403 "Bạn chỉ được tạo đơn cho chính mình."

**Import Excel** (`import_excel.py`, `import_landing_excel.py`): người import không
có quyền thì bỏ qua cột "Nhân viên", ép staff = người import, và **ghi một dòng
cảnh báo vào kết quả import** ("Cột Nhân viên bị bỏ qua: bạn không có quyền gán sale
— toàn bộ đơn đã gán cho bạn"). Không im lặng: import 500 dòng rồi mới phát hiện
sai chủ đơn là mất cả buổi đối soát.

**`PATCH` note khách thân thiết** (`loyalty_view.py`): nếu `sale_staff_id` có trong
payload và không có quyền → 403. Các field còn lại của note (`sale_action`,
`special_note`, `last_care_call_at`) vẫn ghi bình thường.

### 3. Vá lỗ `loyal-customers`

`update_loyal_customer_note` đang gate bằng `require_section("loyal-customers")` =
`loyal-customers:view` — ai xem được là ghi được toàn bộ ghi chú khách. Đổi sang
`require_permission("loyal-customers:edit")`, kèm việc thêm `edit` vào catalog ở
mục 1.

Đây là **thay đổi hành vi**: role đang sửa được note sẽ mất quyền cho tới khi được
cấp `loyal-customers:edit`. Phải cấp trong cùng cửa sổ deploy (xem mục 6).

### 4. Frontend

Dùng `usePermission` sẵn có; không thêm hook mới.

- `OrdersTable/OrderCell.tsx` — case `"staff"`: `canEdit` thêm điều kiện
  `canAssignSale`. Nhánh render text tĩnh khi `!canEdit` đã có sẵn.
- `OrderFormDialog.tsx` — ô chọn sale: `isDisabled` thêm `|| !canAssignSale`. Với
  đơn tạo mới và không có quyền, preset `staff_id = user.id` khi mở form.
- `LoyalCustomersPage.tsx` — ô chọn sale disable tương tự.

UI chỉ là lớp trải nghiệm; chặn thật nằm ở backend. Tránh đọc ref trong thân render
(React Compiler đóng băng giá trị — đã dính một lần ở nút Xuất Excel); giá trị quyền
lấy từ hook context nên không vướng.

### 5. Test

Theo cấu trúc suite hiện có, không rải test lẻ cho từng chức năng:

- `apps/users/tests/test_rbac_sections.py` — thêm case cho `sale-assignment` và
  `loyal-customers` (file này đã là nơi chuẩn cho việc đăng ký section).
- Một file test hành vi phủ bốn đường ghi × hai trạng thái (có quyền / không quyền),
  đặt cùng chỗ với test phân quyền đơn hiện có.
- Frontend: `OrderCell` render text khi thiếu quyền; form preset chính mình khi tạo
  đơn mới không có quyền.

### 6. Rollout

Sau deploy, **không role nào có `sale-assignment:edit`** ngoài Administrator
(`is_system`). Cả công ty mất quyền đổi sale ngay lập tức, và role HR/CSKH mất quyền
ghi note khách thân thiết. Cấp `sale-assignment:edit` và `loyal-customers:edit` cho
các role tương ứng qua trang Phân quyền **ngay trong cùng cửa sổ deploy** — cấp tay
thay vì data migration để kiểm soát được chính xác ai được, nhưng không để sang hôm
sau.

Cache RBAC có TTL 30 phút nhưng khoá theo version, và mọi đường ghi role trong
`apps/users/services.py` đều đã gọi `bump_rbac_cache_version()` — cấp quyền qua UI
là user thấy hiệu lực ngay, không phải chờ hết TTL.

## Ngoài phạm vi

- Lịch sử ai đổi sale phụ trách **khách thân thiết**: hiện chỉ có `updated_by`
  (người sửa cuối). Đơn hàng đã có `log_staff_change` ghi journey từ ai → ai; note
  khách chưa đáng đẻ bảng lịch sử riêng ở v1.
- Phân quyền theo kênh (trưởng nhóm KOC chỉ đổi sale đơn KOC): `RoleSectionScope`
  đã đủ cơ chế, làm khi có nhu cầu thật.
- Các đường gán sale tự động (`lead-assignment`, `floating-pool`) — đã có section
  quyền riêng, không đụng tới.
