# Lọc kênh bán hàng theo phòng ban ở form tạo đơn

## Bối cảnh

Ở form tạo/sửa đơn hàng, dropdown "Kênh bán hàng" đang hiển thị **tất cả** kênh.
Yêu cầu: chỉ hiển thị các kênh thuộc **phòng ban (department) của user đang đăng nhập**.

Hiện trạng data model **không có** quan hệ trực tiếp Channel ↔ Department. Quan hệ chỉ
gián tiếp qua `UserChannel` (gán nhân viên vào kênh) → `User.department`.

## Quyết định thiết kế

1. **Quan hệ Kênh–Phòng ban**: thêm FK `department` (nullable) vào `Channel`. Admin gán
   phòng ban cho kênh ở trang Quản lý kênh.
2. **Cách lọc**: form đơn lọc theo `department` của user đang đăng nhập.
   - Nhân viên đã có phòng ban: **chỉ** thấy kênh được gán đúng phòng ban mình. Kênh
     CHƯA gán phòng ban nào (`department = NULL`) sẽ **bị ẩn** (lọc chặt). ⇒ Admin cần
     gán hết kênh cho các phòng ban, nếu không nhân viên sẽ thiếu kênh khi lên đơn.
   - User là admin (không thuộc phòng ban) hoặc user không có phòng ban → thấy tất cả kênh.
   - Khi sửa đơn: luôn giữ kênh đang được chọn trong dropdown dù khác phòng ban (tránh
     mất giá trị đang lưu).

## Phạm vi thay đổi

### Backend (`apps/channels_core`, `apps/users`)
- `models.Channel`: thêm `department = FK("users.Department", null, on_delete=SET_NULL,
  related_name="channels")` + index `channel_department_idx`.
- Migration tương ứng.
- `ChannelSerializer`: thêm `department_id`, `department_name` (read-only).
- `ChannelCreateSerializer` / `ChannelUpdateSerializer`: nhận `department_id`
  (PrimaryKeyRelatedField, allow_null, required=False).
- `ChannelRepository.create_channel` / `update_channel`: lưu `department`.
- `list_for_user`: `select_related("department")` tránh N+1 cho `department_name`.

### Frontend
- `features/channels/types.ts`: `ChannelRow` thêm `department_id`, `department_name`.
- Trang quản lý kênh (`ChannelManagement` + `ChannelCreateDialog` + `ChannelManageDialog`):
  thêm ô chọn "Phòng ban" (Select từ danh sách `departments`), truyền `department_id`
  trong payload create/update. Cập nhật type dọc chuỗi: `App.createChannel/updateChannel`,
  `AppShellContext`, `ChannelsPage`, `ChannelManagement`.
- `OrderFormDialog`: thêm prop `currentDepartmentId`, `isAdmin`; `ChannelOption` thêm
  `department_id`; lọc `channelSelectOptions` theo phòng ban (giữ kênh đang chọn).
- `OrdersPage` + `useOrdersPageProps`: truyền `currentDepartmentId` (từ
  `shell.user.department?.id`) và `isAdmin` xuống form tạo đơn.

## Sprint

- **Sprint 1 (backend)**: model + migration + serializer + repository. ✅
- **Sprint 2 (frontend - quản lý kênh)**: ô chọn phòng ban + threading payload. ✅
- **Sprint 3 (frontend - form đơn)**: lọc dropdown kênh theo phòng ban. ✅

## Cập nhật: quản lý phòng ban ở page riêng, gán kênh từ phía phòng ban

Theo yêu cầu sau, việc gán kênh ↔ phòng ban được **đảo chiều** về phía phòng ban:

- **Tách trang Phòng ban riêng**: thêm section `departments` (route `/departments`,
  dùng chung quyền với `users` → admin). `DepartmentManager` được gỡ khỏi trang Người dùng
  (`UserManagement`) và chuyển sang `pages/DepartmentsPage.tsx`. Thêm mục sidebar trong
  nhóm "Quản trị".
- **Gán kênh khi tạo/sửa phòng ban**: form phòng ban có multi-select "Kênh bán hàng".
  - Backend `DepartmentSerializer` trả `channel_ids`; `DepartmentCreateUpdateSerializer`
    nhận `channel_ids` (optional). View set `Channel.department` đúng theo danh sách
    (bỏ kênh không còn, thêm kênh mới) + bump cache kênh.
  - `channel_ids` chỉ áp dụng khi được gửi lên (partial update không gửi ⇒ giữ nguyên).
- **Bỏ ô chọn phòng ban khỏi dialog sửa/tạo kênh** (`ChannelCreateDialog`,
  `ChannelManageDialog`, `ChannelManagement`). Quan hệ vẫn là `Channel.department` FK; chỉ
  đổi nơi gán. Lọc kênh ở form đơn theo phòng ban của user giữ nguyên.

## Ghi chú / chưa làm
- Lọc chỉ áp dụng cho form đơn (create/edit ở `OrdersPage`). Form sửa nhúng trong
  `OrdersTable`/`OrderDetailPage` giữ nguyên (không truyền prop ⇒ không lọc) để tránh
  rủi ro ẩn kênh của đơn cũ; có thể mở rộng sau nếu cần.
- Lọc thực hiện ở frontend (không scope ở backend `/api/channels`) để không ảnh hưởng
  dropdown bộ lọc danh sách, render tên kênh, và logic scope ADS sẵn có.
