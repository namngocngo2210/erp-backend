# Quản lý tài khoản đăng nhập trong trang Nhân sự

**Ngày:** 2026-06-17
**Branch:** `feature/hr-employee-account-management`
**Trạng thái:** Design approved → chờ implementation plan

## 1. Bối cảnh & mục tiêu

Trang chi tiết nhân sự (`EmployeeDetailPage` → `EmployeeProfileForm`) hiện chỉ **hiển thị**
các username được gán dưới dạng badge read-only. Không có cách thêm hoặc gỡ tài khoản từ UI,
dù model `EmployeeAccount` đã hỗ trợ sẵn quan hệ Employee 1-N User.

**Mục tiêu:** cho phép, ngay trong trang nhân sự:
- **Thêm tài khoản mới** và gắn vào nhân sự (form đầy đủ như admin Users).
- **Gỡ tài khoản** khỏi nhân sự (chỉ unlink, giữ User độc lập).

Ngoài phạm vi (YAGNI cho lần này): sửa tài khoản đã gắn (đổi mật khẩu/roles), đổi tài khoản
primary thủ công — vẫn làm ở trang admin Users. Việc đặt primary chỉ xảy ra tự động khi gỡ.

## 2. Quyết định thiết kế

| # | Quyết định | Lý do |
|---|-----------|-------|
| D1 | Endpoint mới đặt trong `apps/hr`, gate `employees:edit` | "Ai sửa được hồ sơ nhân sự thì gắn được account"; khác endpoint admin Users (yêu cầu superuser) |
| D2 | Gỡ = chỉ unlink (xóa `EmployeeAccount`), giữ User | User có thể gắn nhân sự khác; tránh xóa nhầm dữ liệu đăng nhập |
| D3 | Form thêm = đầy đủ như admin Users | username, password, is_active, phòng ban, roles RBAC, KiotViet mappings |
| D4 | Tái dụng `AdminUserCreateSerializer` + `UserCreateDialog`, không fork UI | Tránh trùng lặp, đồng nhất hành vi tạo User |
| D5 | Mở rộng `EmployeeSerializer` thêm field `accounts` có cấu trúc, giữ `usernames` | FE cần `account_id` để unlink; giữ `usernames` cho consumer cũ |
| D6 | Gỡ đúng account primary mà còn account khác → tự động promote account cũ nhất còn lại làm primary | Nhân sự luôn có 1 primary hợp lý; tôn trọng ràng buộc DB chỉ 1 primary |

## 3. Backend

### 3.1 Endpoints (gate `employees:edit`)

```
POST   /api/hr/employees/<id>/accounts                 # thêm tài khoản mới + gắn vào nhân sự
DELETE /api/hr/employees/<id>/accounts/<account_id>    # unlink (xóa EmployeeAccount, giữ User)
```

- View mới `apps/hr/transport/views/account_views.py`, đăng ký trong `apps/hr/urls.py`.
- View mỏng: validate quyền + serializer, gọi `EmployeeService`.

### 3.2 Tách logic tạo User dùng chung

Hiện logic tạo admin user nằm inline trong `admin_users_collection` (`apps/users/views.py`).
Tách thành `UserService.create_admin_user(validated_data) -> User` trong `apps/users/services.py`:
tạo User, set_password, gán `department_id`, `kiotviet_mappings`, `role_ids`, `department_ids`.
Cả `admin_users_collection` và endpoint HR mới đều gọi hàm này (tránh copy-paste).

### 3.3 EmployeeService

```python
@transaction.atomic
def add_account(employee_id, validated_data) -> EmployeeAccount:
    # validated_data đã qua AdminUserCreateSerializer
    user = UserService.create_admin_user(validated_data)
    return EmployeeAccount.objects.create(employee_id=employee_id, user=user, is_primary=False)

@transaction.atomic
def unlink_account(employee_id, account_id) -> None:
    acc = EmployeeAccount.objects.get(id=account_id, employee_id=employee_id)  # 404 nếu không có
    was_primary = acc.is_primary
    acc.delete()  # chỉ xóa join, User giữ nguyên
    if was_primary:
        nxt = (EmployeeAccount.objects
               .filter(employee_id=employee_id)
               .order_by("created_at").first())
        if nxt:
            nxt.is_primary = True
            nxt.save(update_fields=["is_primary"])
```

### 3.4 Serializer

Mở rộng `EmployeeSerializer` (`apps/hr/transport/serializers/employee_serializers.py`):
giữ nguyên `usernames`, thêm field `accounts` (đọc từ `obj.accounts.all()` — đã prefetch sẵn,
không N+1):

```python
accounts = [
  {
    "account_id": <EmployeeAccount.id>,
    "user_id":    <User.id>,
    "username":   <User.username>,
    "display_name": <User.display_name>,
    "is_primary": <bool>,
    "is_active":  <User.is_active>,
  },
  ...
]
```

### 3.5 Lỗi & validation

- POST trùng username → `AdminUserCreateSerializer`/`IntegrityError` → 400 với thông báo tiếng Việt.
- DELETE account_id không thuộc employee → 404.
- Không có guard "chặn gỡ primary" (D6 đã xử lý tự động).

### 3.6 Test (backend)

- `add_account`: gate quyền (403 khi thiếu `employees:edit`), tạo User + link `is_primary=False`,
  username trùng → 400.
- `unlink_account`: unlink account thường; unlink primary còn account khác → promote cũ nhất;
  unlink account cuối cùng (primary) → không còn account, không lỗi; account_id sai → 404.
- Serializer trả đúng cấu trúc `accounts`.

## 4. Frontend

### 4.1 Types (`features/hr/hr-types.ts`)

```ts
export interface EmployeeAccount {
  account_id: number;
  user_id: number;
  username: string;
  display_name: string;
  is_primary: boolean;
  is_active: boolean;
}
// thêm vào interface Employee:
accounts: EmployeeAccount[];
```

### 4.2 API layer (`lib/api/hr.ts`)

```ts
addEmployeeAccount(empId: number, payload: CreateAdminUserPayload): Promise<unknown>
unlinkEmployeeAccount(empId: number, accountId: number): Promise<void>
```

### 4.3 UI (`features/hr/components/EmployeeProfileForm.tsx`)

- Thay khối badge read-only bằng danh sách account:
  - Tiêu đề "Tài khoản đăng nhập" + nút **Thêm tài khoản** (chỉ hiện khi có quyền `employees:edit`).
  - Mỗi account: username + badge `primary`/trạng thái `is_active`, nút **gỡ** (icon X) — chỉ hiện khi có quyền.
- **Thêm:** tái dụng `UserCreateDialog`, thêm prop `onSubmit?` (mặc định giữ hành vi POST `/api/users/admin`);
  HR truyền handler POST `/api/hr/employees/<id>/accounts`. Sau khi tạo → refetch employee.
- **Gỡ:** `AlertDialog` xác nhận → `unlinkEmployeeAccount` → refetch employee.

### 4.4 Phân quyền FE

Dùng cùng cờ quyền mà `EmployeeProfileForm` đã dùng để gate các field sửa (`employees:edit`).
Không hiện nút Thêm/Gỡ nếu không có quyền.

## 5. Rủi ro & lưu ý

- `UserCreateDialog` tham chiếu roles/permission toàn cục — tái dụng nguyên trạng, chỉ đổi đích
  submit, không sửa logic RBAC bên trong dialog.
- Endpoint HR mới gate `employees:edit` (không phải superuser) — đây là chủ đích mở rộng quyền
  tạo User cho HR manager; cần nêu rõ khi review để không bị hiểu nhầm là lỗ hổng.
- Giữ `usernames` trong serializer để không vỡ các consumer hiện có (org-chart, list, mobile ESS).
