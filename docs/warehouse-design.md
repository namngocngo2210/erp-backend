# Thiết kế: Danh mục Kho (Warehouse)

**Ngày:** 2026-06-10
**Trạng thái:** Đã thực hiện xong Sprint 1-2, đang hoàn thiện Sprint 3
**Branch:** `feat/warehouses`

## 1. Mục tiêu & Phạm vi

Thêm bảng danh mục kho vào hệ thống: lưu tên kho, mã kho, trạng thái hoạt động. Kèm API CRUD và trang quản lý trên frontend.

**Trong phạm vi:**
- Bảng `warehouses` trong PostgreSQL (app Django mới `apps.warehouses`)
- API CRUD `/api/warehouses`
- Trang quản lý kho trên frontend (danh sách, thêm, sửa, xóa)
- Phân quyền theo section `warehouses` (cơ chế `require_section` sẵn có)
- Đăng ký Django admin
- Unit test backend + frontend

**Ngoài phạm vi (làm sau khi có nhu cầu cụ thể — YAGNI):**
- Gắn kho vào đơn hàng (FK `orders.warehouse`)
- Tồn kho theo sản phẩm (bảng stock product × warehouse)
- Nhập/xuất/chuyển kho

## 2. Backend

### 2.1. App mới `backend/apps/warehouses/`

Tạo app Django riêng thay vì nhét vào `apps.orders` (đã ôm quá nhiều domain). Cấu trúc theo chuẩn `docs/architecture-standard.md`:

```
backend/apps/warehouses/
├── __init__.py
├── apps.py
├── models.py
├── repositories.py
├── admin.py
├── urls.py
├── migrations/
│   └── 0001_initial.py
├── transport/
│   ├── serializers/warehouse_serializers.py
│   └── views/warehouse_views.py
└── tests/
    └── test_warehouse_api.py
```

### 2.2. Model `Warehouse`

| Trường | Kiểu | Ràng buộc |
|---|---|---|
| `id` | BigAutoField | PK |
| `code` | CharField(50) | unique |
| `name` | CharField(255) | unique |
| `is_active` | BooleanField | default True |
| `deleted_at` | DateTimeField | null, soft-delete theo convention |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | auto_now |

- `db_table = "warehouses"`, `ordering = ["name"]`
- Index: `deleted_at` (lọc soft-delete), `is_active`. `code`/`name` unique đã tự có index.
- Unique trên `code`/`name` áp dụng ở tầng DB; khi soft-delete vẫn giữ unique (tên kho không tái sử dụng sau khi xóa — chấp nhận được với danh mục nhỏ, tránh phức tạp partial unique index).

### 2.3. Repository

`WarehouseRepository(BaseRepository[Warehouse])` — dùng `active()`, `get_or_none()`, `soft_delete()`, `paginate()` sẵn có từ `apps/common/repository.py`.

### 2.4. API

Function-based views theo pattern hiện tại, permission `IsAuthenticated` + `require_section("warehouses")`:

| Method & Path | Mô tả |
|---|---|
| `GET /api/warehouses` | Danh sách. Query: `search` (icontains theo name/code), `is_active`, `limit` (mặc định 50, tối đa 200), `offset`. Trả `{items, total, limit, offset}`. Loại bản ghi đã soft-delete. |
| `POST /api/warehouses` | Tạo kho. Body: `code`, `name`, `is_active` (optional). Trùng code/name → 400 kèm message tiếng Việt. |
| `PATCH /api/warehouses/<id>` | Sửa `code`/`name`/`is_active`. 404 nếu không tồn tại hoặc đã xóa. |
| `DELETE /api/warehouses/<id>` | Soft delete (set `deleted_at`). |

Serializer: `WarehouseSerializer` (ModelSerializer) — validate strip khoảng trắng, không cho rỗng.

### 2.5. Phân quyền

- Thêm `"warehouses"` vào `VALID_SECTIONS` (`apps/users/models.py`)
- Admin mặc định có quyền; phòng ban cần được cấp `"warehouses"` trong `allowed_sections`

### 2.6. Admin & cấu hình

- Đăng ký `WarehouseAdmin`: `list_display` (code, name, is_active, created_at), `search_fields` (code, name)
- Thêm `apps.warehouses` vào `INSTALLED_APPS`, include `apps.warehouses.urls` vào router gốc với prefix `/api/warehouses`

## 3. Frontend

### 3.1. API client `src/lib/api/warehouses.ts`

```ts
interface Warehouse { id; code; name; is_active; created_at; updated_at }
getWarehouses({ search, isActive, limit, offset })
createWarehouse({ code, name, is_active })
updateWarehouse(id, partial)
deleteWarehouse(id)
```

Theo pattern `src/lib/api/products.ts`, gọi qua `fetchJson`.

### 3.2. Trang `src/pages/WarehousesPage.tsx`

- Bảng danh sách: cột Mã kho, Tên kho, Trạng thái (badge), Thao tác
- Ô tìm kiếm (debounce), filter trạng thái, phân trang
- Dialog thêm/sửa kho (shadcn/ui Dialog + Input + Button), validate trống
- Xóa có confirm dialog
- shadcn/ui + lucide-react, light theme đồng bộ các trang hiện có
- Thêm route + mục menu điều hướng, ẩn/hiện theo section `warehouses` (theo cách các trang khác đang gate)

## 4. Kiểm thử

**Backend (`tests/test_warehouse_api.py`):**
- CRUD hạnh phúc: tạo → list → sửa → xóa
- Trùng `code`/`name` → 400
- DELETE là soft-delete: bản ghi còn trong DB với `deleted_at`, không xuất hiện trong list
- User không có section `warehouses` → 403; admin → pass
- Search + filter `is_active` + phân trang

**Frontend:** test API client và component chính theo pattern test FE hiện có.

Lưu ý baseline: 2 test fail sẵn trên master (dashboard return-rate BE, OrderFormDialog FE) — không phải regression.

## 5. Sprint & Tiến độ

### Sprint 1 — Backend (model + API + test)
- [x] Tạo app `warehouses`: model, migration, admin, INSTALLED_APPS
- [x] Thêm section `warehouses` vào `VALID_SECTIONS`
- [x] Repository + serializer + views + urls
- [x] Unit test API (14 tests pass)
- **Ghi chú sau sprint:** Đã làm đủ theo thiết kế. Ngoài kế hoạch: (1) dùng helper phân trang chung `apps/common/pagination.py` (mở rộng thêm tham số `default_limit` backward-compatible) thay vì viết riêng, theo `docs/architecture-standard.md`; (2) bọc `IntegrityError → 400` cho race tạo/sửa trùng code/name; (3) thêm 2 test pin hành vi: PATCH trùng code → 400, tạo lại kho trùng sau soft-delete → 400 (quyết định thiết kế unique giữ cả bản ghi đã xóa). Lưu ý: repo thực tế chạy Django 6.0.3 (doc cũ ghi 5.2).

### Sprint 2 — Frontend (API client + trang quản lý)
- [x] API client `warehouses.ts`
- [x] `WarehousesPage` + route + menu (gate theo section `warehouses`, thêm vào group "Kho hàng" sẵn có trên sidebar) + i18n + DepartmentManager
- [x] Test FE (2 tests pass; tsc + build sạch)
- **Ghi chú sau sprint:** Sau code review đã sửa 5 điểm: loading khởi tạo true + load ngay lần đầu (hết flash "Chưa có kho nào"), clamp offset khi xóa hết item trang cuối, sequence guard chống response cũ đè mới, toast lỗi xóa hiển thị message từ server, disable nút phân trang khi đang loading.

### Sprint 3 — Hoàn thiện
- [x] Full test BE/FE (warehouse 14/14 BE + 2/2 FE pass; các fail khác là baseline môi trường, tái hiện trên master sạch — không phải regression)
- [ ] Chạy migration trên môi trường thật — **để khi deploy sau merge** (migration `warehouses.0001_initial` chỉ CREATE TABLE bảng rỗng, an toàn; chưa apply để tránh prod-schema đi trước prod-code khi MR còn review)
- [x] Push, tạo MR, gửi link
- **Ghi chú sau sprint:** Còn lại làm sau khi có nhu cầu: gắn kho vào đơn hàng, tồn kho theo sản phẩm (ngoài phạm vi đợt này).
