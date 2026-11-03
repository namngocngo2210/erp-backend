# Trang Người dùng: phân trang + tìm kiếm server-side

**Ngày:** 2026-07-07
**Branch:** `feat/users-page-pagination-search`

## Vấn đề

Trang Người dùng (`/users`, component `UserManagement`) hiện tải toàn bộ danh sách
từ `GET /api/users/admin` (trả mảng đầy đủ) và render hết một lượt: không có ô
tìm kiếm, không có phân trang. Khi số tài khoản tăng (công ty 50 → 100-150 nhân
sự, cộng tài khoản đã vô hiệu), trang chậm dần và khó tìm đúng người.

## Quyết định (đã chốt với user)

- **Server-side pagination** theo `limit/offset` (pattern blocklist/HR), không
  lọc client-side.
- Tìm kiếm khớp **cả `username` lẫn `display_name`**, không phân biệt hoa thường.

## Thiết kế

### 1. Backend — `GET /api/users/admin` nhận `q`, `limit`, `offset`

`backend/apps/users/services.py`:

- `UserService.admin_list_users(q: str | None = None)` — thêm lọc
  `Q(username__icontains=q) | Q(display_name__icontains=q)` khi `q` truthy;
  thêm `.order_by("id")` để phân trang ổn định. Giữ nguyên
  `select_related`/`prefetch_related` chống N+1 (audit 2026-07 mục 3.5).

`backend/apps/users/views.py` (`admin_users_collection`, nhánh GET):

- Đọc query param: `q` (mặc định rỗng), `limit` (mặc định 20, trần 100, sàn 1),
  `offset` (mặc định 0, sàn 0). Giá trị không parse được → dùng mặc định.
- `total = qs.count()` trước khi slice `qs[offset : offset + limit]`.
- **Response đổi shape**: `[...]` → `{"items": [...], "total": n}`.

`backend/apps/users/serializers.py`:

- Thêm `AdminUserListResponseSerializer` (`items = UserSerializer(many=True)`,
  `total = IntegerField`) chỉ để khai OpenAPI.
- Khai `OpenApiParameter` cho `q`, `limit`, `offset` trong `extend_schema` của
  `admin_users_list`.

Breaking change có chủ đích: FE là consumer duy nhất của endpoint này và được
cập nhật trong cùng MR — không giữ nhánh tương thích trả mảng.

### 2. Hợp đồng OpenAPI

Regen `frontend/src/api/schema.d.ts` bằng `gen:api` sau khi sửa BE.

### 3. Frontend — chuyển query từ shell về trang

Hiện `App.tsx` fetch users ở shell (`useAdminUsersQuery`) rồi truyền qua
`AppShellContext` → `routed-pages` → `UserManagement`. Với server-side
pagination, state `q`/`page` sống ở trang, nên:

- `lib/api/users.ts`: `listAdminUsers(params: {q?, limit, offset})` → trả
  `AdminUserListResponse` typed từ `schema.d.ts`.
- `features/users/queries.ts`: `useAdminUsersQuery(params, enabled)` với key
  `["users", "admin", params]` + `placeholderData: keepPreviousData`. Key gốc
  `["users", "admin"]` giữ làm prefix nên các `invalidateQueries` hiện có ở
  `App.tsx` (sau create/update/delete) vẫn hoạt động.
- `UserManagement.tsx` tự fetch qua hook trên; state cục bộ: `q` (debounce
  ~300ms, đổi từ khóa reset về trang 1), `page` (size 20).
- Gỡ `adminUsers` khỏi `App.tsx` / `AppShellContext` / props `routed-pages`.
  Các handler `onCreate`/`onUpdate`/`onDelete` ở `App.tsx` giữ nguyên.
- `UsersTable.tsx`: thêm ô tìm kiếm (Input + icon Search, shadcn/ui) bên trái
  toolbar của `DataTableShell`; footer phân trang "Trang x/y (n người dùng)" +
  nút Trước/Sau theo pattern các trang hiện có.
- i18n: thêm key placeholder tìm kiếm + label phân trang vào
  `i18n/messages/users.ts` (hoặc dùng key `common.*` sẵn có nếu đủ).

Hành vi selection khi phân trang: `selectedUserIds` là mảng id độc lập với
trang — tick ở trang nào giữ nguyên khi chuyển trang, xóa hàng loạt xóa đúng
mọi id đã tick. Riêng checkbox "chọn tất cả" chỉ áp cho các dòng của trang
hiện tại (chuẩn cho bảng server-side).

### 4. Tests

Backend (`backend/apps/users/tests/`):

- Lọc `q` khớp username, khớp display_name, không phân biệt hoa thường.
- Phân trang: `total` đúng, slice đúng theo `limit/offset`, ordering ổn định.
- `limit` vượt trần bị kẹp về 100; param rác không 500.
- Cập nhật test hiện có đụng shape response (nếu có) + giữ
  `test_users_list_queries.py` (gọi service trực tiếp — thêm case gọi với `q`).

Frontend: cập nhật test hiện có của trang users nếu có; smoke test hook/param
không bắt buộc (theo baseline test hiện tại của repo).

## Phạm vi ảnh hưởng

`views.py`, `services.py`, `serializers.py`, tests BE, `schema.d.ts` (regen),
`lib/api/users.ts`, `features/users/queries.ts`, `UserManagement.tsx`,
`UsersTable.tsx`, `App.tsx`, `AppShellContext.tsx`, `routed-pages.tsx`,
`i18n/messages/users.ts`.

## Ngoài phạm vi

- Không đổi các endpoint users khác (`/departments`, `/profile`, …).
- Không thêm filter theo vai trò/phòng ban/trạng thái (có thể làm sau).
- Không đổi RBAC (vẫn superuser + section `users`).

## Tiến độ

- 2026-07-07: Hoàn thành BE (q/limit/offset + {items,total}), regen contract,
  FE chuyển query về trang + ô tìm kiếm debounce + footer phân trang. Đã làm:
  toàn bộ spec. Chưa làm: không.
