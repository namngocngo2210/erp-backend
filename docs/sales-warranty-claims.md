# Bảo hành (Sales) — Thiết kế

Ngày: 2026-06-24
Branch: `feat/sales-warranty-claims`

## 1. Bối cảnh & mục tiêu

Bổ sung một trang **Bảo hành** nội bộ trong nhóm **Sale** cho nhân viên CSKH/sale tạo và
theo dõi phiếu bảo hành gắn với một đơn hàng đã có.

Lưu ý: app `warranty` hiện tại (`WarrantyRegistration`) phục vụ **form đăng ký bảo hành công
khai** của khách hàng cuối (`POST /api/warranty/`, không auth, throttled). Tính năng này là
nghiệp vụ **khác hẳn** — quản lý nội bộ — nên dùng một model riêng, không sửa model công khai.

## 2. Quyết định thiết kế (đã chốt)

- Model mới riêng `WarrantyClaim` trong app `warranty`, **OneToOne** với `Order` (1 phiếu / đơn).
- Thông tin đơn (tên, sđt, địa chỉ, SKU, số tiền, mã vận đơn) **không lưu trùng** — đọc live qua FK.
- Có field `status` enum (Mới / Đang xử lý / Hoàn tất / Hủy) để badge + filter.
- Section RBAC mới `warranty` trong nhóm Sale với bộ quyền `view/create/edit/delete/export`.
- Tìm đơn để chọn qua endpoint search mới `GET /api/orders/search` (auth) theo sđt + mã vận đơn.

## 3. Backend

### 3.1 Model `WarrantyClaim` (`backend/apps/warranty/models.py`)

| Field | Kiểu | Ghi chú |
|---|---|---|
| `order` | `OneToOneField(Order, on_delete=PROTECT, related_name="warranty_claim")` | 1 phiếu / đơn |
| `status` | `PositiveSmallIntegerField(choices)` | NEW=1, PROCESSING=2, DONE=3, CANCELLED=4; default NEW |
| `contact_channel` | `CharField(max_length=100, blank=True, default="")` | Kênh liên hệ |
| `require_carrier_call` | `BooleanField(default=False)` | YC vận đơn gọi xác nhận |
| `social_nickname` | `CharField(max_length=255, blank=True, default="")` | Nick Shopee/TikTok/FB |
| `send_reason` | `TextField(blank=True, default="")` | Lí do gửi |
| `send_method` | `CharField(max_length=100, blank=True, default="")` | Hình thức gửi |
| `classification` | `CharField(max_length=100, blank=True, default="")` | Phân loại |
| `recall_code` | `CharField(max_length=100, blank=True, default="")` | Mã thu hồi |
| `return_status` | `CharField(max_length=100, blank=True, default="")` | Tình trạng đơn hoàn |
| `accounting_check` | `CharField(max_length=100, blank=True, default="")` | Kế toán check |
| `accounting_note` | `TextField(blank=True, default="")` | Kế toán note |
| `created_by` | `ForeignKey(User, SET_NULL, null=True)` | Người tạo |
| `created_at` | `DateTimeField(auto_now_add=True)` | |
| `updated_at` | `DateTimeField(auto_now=True)` | |
| `deleted_at` | `DateTimeField(null=True, blank=True)` | Soft delete |

- `db_table = "warranty_claims"`.
- Index: `order`, `status`, `created_at`, `deleted_at`.

### 3.2 Serializers (`backend/apps/warranty/serializers.py`)

- `WarrantyClaimOrderSerializer` (read-only): trích từ `Order` các field hiển thị —
  `id, name, phone, address, product, sku, quantity, total_after_discount, shipping_code, order_code`.
- `WarrantyClaimSerializer` (read): toàn bộ field phiếu + `order` lồng (live).
- `WarrantyClaimWriteSerializer` (create/update): nhận `order_id` + các field nghiệp vụ;
  validate đơn tồn tại (chưa xóa) và chưa có phiếu (OneToOne) khi tạo.

### 3.3 Views / URLs

- App `warranty` thêm router cho `claims` (DRF `ModelViewSet` hoặc các function view theo
  pattern hiện tại). Tất cả gate bằng `require_section("warranty")` + `IsAuthenticated`.
  - `GET  /api/warranty/claims` — list, filter `status`, search `q` (sđt/mã vận đơn/tên), phân trang.
  - `POST /api/warranty/claims` — create.
  - `GET  /api/warranty/claims/{id}` — detail.
  - `PATCH /api/warranty/claims/{id}` — update.
  - `DELETE /api/warranty/claims/{id}` — soft delete (set `deleted_at`).
- Endpoint công khai `POST /api/warranty/` giữ nguyên, không đổi.
- **Order search** (`backend/apps/orders/...`): `GET /api/orders/search?q=...&limit=20`
  - Auth + `require_section("warranty")` (hoặc reuse section đơn — chốt lúc impl: dùng `warranty`).
  - Tìm `phone_normalized` / `shipping_code` chứa `q`, `deleted_at__isnull=True`, trả gọn
    `id, name, phone, shipping_code, order_code` cho combobox. Giới hạn 20 dòng.

### 3.4 RBAC

Thêm vào `backend/apps/users/rbac_catalog.py`:

```python
"warranty": ("view", "create", "edit", "delete", "export"),
```

## 4. Frontend

- Route `/sales/warranty` (`frontend/src/app/routes.tsx`) bọc `Guard section="warranty"`.
- Thêm section vào nhóm `sales` trong `frontend/src/app/modules.ts` (icon `ShieldCheck`),
  i18n label "Bảo hành".
- `frontend/src/pages/WarrantyClaimsPage.tsx`:
  - Danh sách dùng `ResponsiveList` (table → card mobile), cột chính + badge `status`.
  - Thanh filter: select `status`, ô search `q`.
  - Nút **"Thêm bảo hành"** mở dialog tạo.
- Dialog thêm/sửa (`FormDialogShell`, size landscape):
  - **Order picker**: combobox search debounce gọi `searchOrders(q)` (gõ sđt/mã vận đơn).
  - Chọn đơn → card read-only hiển thị tên, sđt, địa chỉ, SKU, số tiền, mã vận đơn.
  - Các field nghiệp vụ: kênh liên hệ, YC gọi xác nhận (switch), nick MXH, lí do gửi,
    hình thức gửi, phân loại, mã thu hồi, tình trạng đơn hoàn, kế toán check, kế toán note, status.
- API layer:
  - `frontend/src/lib/api/warrantyClaims.ts`: `listClaims`, `getClaim`, `createClaim`, `updateClaim`, `deleteClaim`.
  - `frontend/src/lib/api/orders.ts`: thêm `searchOrders(q)`.
  - react-query hooks trong `frontend/src/features/warranty-claims/queries.ts`.

## 5. Test

- BE unit test (`backend/apps/warranty/tests/`): create (gắn order hợp lệ / chặn trùng OneToOne /
  chặn order đã xóa), list + filter status + search q, permission (thiếu section → 403), soft delete.
- BE test order search: tìm theo phone & shipping_code, giới hạn 20.
- FE: smoke test page render + dialog mở (tối thiểu).

## 6. Migration

`python manage.py makemigrations warranty` tạo bảng `warranty_claims` + index.

## 7. Sprint

### Sprint 1 — Backend nền tảng
- [ ] Model `WarrantyClaim` + migration.
- [ ] RBAC section `warranty`.
- [ ] Serializers + viewset/urls CRUD + soft delete.
- [ ] Endpoint `GET /api/orders/search`.
- [ ] Unit test BE.

### Sprint 2 — Frontend
- [ ] API layer + react-query hooks.
- [ ] Route + menu (modules.ts) + Guard + i18n.
- [ ] Trang danh sách + filter.
- [ ] Dialog thêm/sửa + order picker combobox.
- [ ] FE smoke test.

### Sprint 3 — Hoàn thiện
- [ ] Kiểm tra phân quyền end-to-end, QA thị giác light theme.
- [ ] Cập nhật doc: đã làm / chưa làm.

## 8. Ghi chú tiến độ

### Đã hoàn thành (branch `feat/sales-warranty-claims`)

**Backend**
- Model `WarrantyClaim` + migration tạo bảng `warranty_claims` với đầy đủ index (`order`, `status`, `created_at`, `deleted_at`).
- RBAC section `warranty` thêm vào `rbac_catalog.py` với bộ quyền `view/create/edit/delete/export`.
- Serializers (`WarrantyClaimOrderSerializer`, `WarrantyClaimSerializer`, `WarrantyClaimWriteSerializer`) + ViewSet CRUD + soft delete tại `/api/warranty/claims`.
- Endpoint `GET /api/orders/search` (auth + section `warranty`) cho order picker combobox.
- Unit test BE: tạo claim hợp lệ, chặn trùng OneToOne, chặn đơn đã xóa, list/filter/search, permission 403, soft delete, order search.

**Frontend**
- API layer: `frontend/src/lib/api/warrantyClaims.ts` + `searchOrders` trong `frontend/src/lib/api/orders.ts`.
- React-query hooks: `frontend/src/features/warranty-claims/queries.ts`.
- Route `/sales/warranty` + Guard `section="warranty"` trong `frontend/src/app/routes.tsx`.
- Menu item "Bảo hành" (icon `ShieldCheck`) trong nhóm Sales tại `frontend/src/app/modules.ts`.
- Trang danh sách `frontend/src/pages/WarrantyClaimsPage.tsx`: `ResponsiveList` (table/card), filter status + search q, badge trạng thái.
- Dialog thêm/sửa `WarrantyClaimFormDialog` + `OrderPicker` combobox debounce.
- FE smoke test: `frontend/src/features/warranty-claims/__tests__/WarrantyClaimsPage.test.tsx` — vitest PASS, typecheck exit 0.

### Còn lại / Chưa làm

- **Manual visual QA (DEFERRED):** Chạy app thật, đăng nhập user có quyền `warranty`, kiểm tra:
  - Sidebar hiển thị "Bảo hành" dưới Sales, route `/sales/warranty` mở đúng trang.
  - Dialog "Thêm bảo hành" mở; order picker trả kết quả khi gõ SĐT/mã vận đơn; chọn đơn điền card read-only.
  - Lưu tạo row mới; badge trạng thái hiển thị đúng; sửa pre-fills form; xóa xoá row.
  - User không có quyền `warranty` không thấy tab và bị chặn tại `/sales/warranty`.
  - Giao diện light theme (tông xanh da trời) nhất quán trên cả desktop và mobile.
