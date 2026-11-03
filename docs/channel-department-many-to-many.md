# Kênh bán hàng ↔ Phòng ban: chuyển sang quan hệ nhiều-nhiều (M2M)

## 1. Bối cảnh & vấn đề

Trang **Phòng ban** (`DepartmentManager`) cho phép admin gán danh sách kênh bán hàng
cho từng phòng ban. Khi thêm một kênh vào phòng ban B trong khi kênh đó đang thuộc
phòng ban A, kênh **biến mất khỏi phòng ban A**.

### Nguyên nhân gốc

`Channel.department` là một **ForeignKey** (quan hệ một-nhiều): mỗi kênh chỉ thuộc
**duy nhất một** phòng ban, hoặc `NULL` = dùng chung cho tất cả. Hàm
`_sync_department_channels` chạy `Channel.objects.filter(id__in=...).update(department=dept)`,
nên gán kênh cho phòng ban B sẽ kéo kênh ra khỏi phòng ban A.

Đây không phải lỗi logic — mô hình một-nhiều đang chạy đúng. Vấn đề là **yêu cầu
nghiệp vụ (một kênh dùng chung cho nhiều phòng ban) không khớp với mô hình dữ liệu**.

## 2. Quyết định

Chuyển `Channel.department` (FK) → `Channel.departments` (**ManyToMany** tới
`users.Department`, `related_name="channels"`).

- Một kênh có thể gán cho **nhiều phòng ban** cùng lúc.
- **Không gán phòng ban nào** (M2M rỗng) = "dùng chung", hiển thị cho tất cả phòng ban
  (giữ nguyên ngữ nghĩa `NULL` cũ).

## 3. Phạm vi thay đổi

### Backend
- `apps/channels_core/models.py`: FK `department` → M2M `departments`; bỏ index
  `channel_department_idx` (M2M tự tạo bảng nối + index).
- Migration `0018`: AddField M2M → RunPython copy FK→M2M (forward + reverse) →
  RemoveIndex → RemoveField FK.
- `apps/users/views.py::_sync_department_channels`: dùng `dept.channels.set(...)` —
  chỉ thêm/bớt liên kết của **riêng phòng ban này**, không đụng phòng ban khác.
- `apps/users/serializers.py::DepartmentSerializer.get_channel_ids`: giữ nguyên
  (`obj.channels` reverse M2M vẫn hoạt động).
- `channel_serializers_impl.py`:
  - `ChannelSerializer`: `department_id`/`department_name` → `department_ids`/`department_names` (list).
  - `ChannelCreate/UpdateSerializer`: input `department_id` → `department_ids` (list, optional).
- `channel_repositories_impl.py`: tham số `department` → `departments` (list); set qua
  M2M sau khi lưu; đổi `select_related("department")` → `prefetch_related("departments")`.

### Frontend
- `features/orders/channel-visibility.ts`: `department_id` → `department_ids: number[]`;
  "dùng chung" = mảng rỗng; lọc bằng `department_ids.includes(currentDepartmentId)`.
- `features/orders/OrderFormDialog.tsx`: `ChannelOption.department_id` → `department_ids`.
- `features/channels/types.ts`: `ChannelRow` đồng bộ field mới.
- `App.tsx` / `AppShellContext.tsx`: payload create/update channel `department_id` →
  `department_ids` (optional; UI hiện chưa dùng).

### Tests
- `apps/users/tests/test_department_channels.py`: cập nhật sang M2M + thêm test
  **không cướp kênh giữa các phòng ban** (regression cho bug này).
- `features/orders/channel-visibility.test.ts`: cập nhật sang `department_ids`.

## 4. Tiến độ

- [ ] Sprint 1: Model + migration + backfill
- [ ] Sprint 2: Backend serializer/repository/sync
- [ ] Sprint 3: Frontend types + visibility
- [ ] Sprint 4: Tests xanh (backend + frontend)
