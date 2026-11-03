# Bảng bảo hành: cột động + inline edit + trang chi tiết — Thiết kế

Ngày: 2026-06-24
Branch: `feat/warranty-table-inline-edit`

## 1. Mục tiêu

Nâng cấp trang Bảo hành (`/sales/warranty`) cho giống trang đơn hàng:
- Bảng với **ẩn/hiện + sắp xếp + ghim cột** (đầy đủ như orders), lưu localStorage.
- **Sửa inline ngay trên ô** cho mọi trường nghiệp vụ của phiếu (PATCH ngay).
- Bỏ nút edit/cột "Hành động"; **click dòng → mở trang chi tiết** `/sales/warranty/:id`.
- Trang chi tiết xem đầy đủ + vẫn **sửa được** (mở lại form) + xóa.

Backend không đổi (đã có `GET/PATCH/DELETE /api/warranty/claims/:id`).

## 2. Quyết định đã chốt

- Trang chi tiết riêng `/sales/warranty/:id` (full page, deep-link); giữ và vẫn sửa được.
- Hệ thống cột **đầy đủ như orders**: visible + locked (ghim trái) + thứ tự kéo-thả, lưu localStorage.
- Inline edit cho **tất cả trường nghiệp vụ** của phiếu; cột lấy từ đơn (tên/sđt/địa chỉ/SP/SKU/số tiền/mã vận đơn/mã đơn) **read-only**.
- Editor **theo kiểu dữ liệu**: text → input (lưu khi blur/Enter, Esc hủy); `status` → dropdown; `require_carrier_call` (bool) → switch. Mỗi thay đổi PATCH ngay.
- Click dòng (ngoài ô đang sửa / khi không bôi đen text) → điều hướng chi tiết.

## 3. Cấu trúc cột

`frontend/src/features/warranty-claims/warranty-claim-columns.ts`:
- `WarrantyColumnKey` (union các key).
- `WARRANTY_COLUMN_OPTIONS: { key: WarrantyColumnKey; label: string; editable: boolean; kind: "text"|"status"|"bool"|"readonly" }[]`.
- `DEFAULT_WARRANTY_COLUMNS: WarrantyColumnKey[]` = `["customer","phone","shipping_code","classification","send_reason","status","created_at"]`.

| key | label | nguồn | kind |
|---|---|---|---|
| customer | Khách hàng | order.name | readonly |
| phone | SĐT | order.phone | readonly |
| address | Địa chỉ | order.address | readonly |
| product | Sản phẩm | order.product | readonly |
| sku | SKU | order.sku | readonly |
| amount | Số tiền | order.total_after_discount | readonly |
| shipping_code | Mã vận đơn | order.shipping_code | readonly |
| order_code | Mã đơn | order.order_code | readonly |
| status | Trạng thái | claim.status | status |
| contact_channel | Kênh liên hệ | claim | text |
| require_carrier_call | YC gọi xác nhận | claim | bool |
| social_nickname | Nick MXH | claim | text |
| send_reason | Lí do gửi | claim | text |
| send_method | Hình thức gửi | claim | text |
| classification | Phân loại | claim | text |
| recall_code | Mã thu hồi | claim | text |
| return_status | Tình trạng đơn hoàn | claim | text |
| accounting_check | Kế toán check | claim | text |
| accounting_note | Kế toán note | claim | text |
| created_by_name | Người tạo | claim | readonly |
| created_at | Ngày tạo | claim | readonly |

## 4. State cột + lưu trữ

`frontend/src/features/warranty-claims/warranty-claims-storage.ts`:
- localStorage keys: `desi_warranty_visible_columns`, `desi_warranty_locked_columns`, `desi_warranty_column_order`.
- `getStoredVisible() / setStoredVisible(keys)`; tương tự locked & order. Lọc bỏ key lạ (migration nhẹ: key mới chưa lưu → dùng default).

`frontend/src/features/warranty-claims/useWarrantyColumns.ts` (hook):
- Trả `{ visibleColumns, lockedColumns, orderedColumns, toggleColumn, toggleLock, reorderColumns }`.
- `orderedColumns` = thứ tự đã lưu (locked đứng trước), chỉ gồm cột visible.
- Mọi thay đổi ghi localStorage.

## 5. Column picker

`frontend/src/features/warranty-claims/components/WarrantyColumnPicker.tsx`:
- Nút "Cột" (icon `Columns3`) mở dropdown qua `createPortal`.
- Danh sách dùng `@dnd-kit` (`DndContext` + `SortableContext`) + **tái dùng `SortableColumnItem`** (`@/features/orders/SortableColumnItem`) cho mỗi dòng (checkbox hiện/ẩn + tay kéo + nút ghim).
- Gọi `toggleColumn / toggleLock / reorderColumns` từ hook.

## 6. Bảng

`frontend/src/features/warranty-claims/components/WarrantyClaimsTable.tsx`:
- Props: `rows: WarrantyClaim[]`, `orderedColumns`, `lockedColumns`, `onOpenDetail(id)`, `onPatch(id, patch)`.
- Header render theo `orderedColumns`; cột trong `lockedColumns` sticky-left.
- Cell render theo `WARRANTY_COLUMN_OPTIONS[key].kind`:
  - `readonly` → text thuần (giá trị từ order/claim; `amount` format tiền; `created_at` format ngày; bool/`status` readonly nếu cần).
  - `text` → `<InlineTextCell>`: hiện text; click → input; lưu khi blur/Enter (gọi `onPatch(id,{key:value})` nếu đổi); Esc hủy.
  - `status` → `<InlineStatusCell>`: dropdown `WARRANTY_STATUS_OPTIONS`; onChange → `onPatch(id,{status})`.
  - `bool` → `<InlineBoolCell>`: `Switch`; onChange → `onPatch(id,{require_carrier_call})`.
  - Ô editable `stopPropagation` để không kích hoạt điều hướng dòng.
- `<TableRow cursor-pointer>`; click (khi `window.getSelection().toString()` rỗng) → `onOpenDetail(row.id)`.
- Giữ `ResponsiveList`: mobile render card (vài trường chính, read-only) tap → `onOpenDetail`.

## 7. Page

`frontend/src/pages/WarrantyClaimsPage.tsx` (sửa):
- Toolbar: search + lọc trạng thái + `WarrantyColumnPicker` + nút "Thêm bảo hành" (giữ dialog tạo).
- Dùng `useWarrantyColumns()` cho state cột; render `<WarrantyClaimsTable rows ... onOpenDetail={navigate} onPatch={mutate}>`.
- `onPatch` dùng `useUpdateWarrantyClaim` (PATCH `/api/warranty/claims/:id`), invalidate list khi thành công; toast lỗi.
- Bỏ nút edit/delete khỏi bảng; click dòng → `navigate("/sales/warranty/" + id)`.

## 8. Trang chi tiết

- API: `getWarrantyClaim(id)` trong `lib/api/warrantyClaims.ts` (`GET /api/warranty/claims/:id`); hook `useWarrantyClaim(id)`.
- Route `/sales/warranty/:claimId` + `Guard section="warranty"` (routes.tsx) + `WarrantyClaimDetailRoute` (routed-pages.tsx).
- `frontend/src/pages/WarrantyClaimDetailPage.tsx`: card thông tin đơn + toàn bộ trường phiếu (read-only, badge status). Header: nút Quay lại, **Sửa** (mở `WarrantyClaimFormDialog` edit), **Xóa** (confirm → `deleteWarrantyClaim` → quay về `/sales/warranty`).

## 9. Test (FE)

- Unit `useWarrantyColumns`: default khi chưa lưu; toggle ẩn/hiện; toggleLock; reorder; persist + đọc lại từ localStorage.
- Smoke `WarrantyClaimsTable`: render đúng các cột visible; click ô editable không điều hướng; click dòng gọi `onOpenDetail`.
- Smoke `WarrantyClaimDetailPage`: render trường từ claim mock.
- `npm run typecheck` + `npm run build` xanh.

## 10. Không làm (YAGNI)

Không refactor `OrdersTable`; không đổi backend; không sửa inline cho cột lấy từ đơn (chỉ trường phiếu); không tách hook column-visibility dùng chung toàn app (chỉ làm cho warranty, tái dùng `SortableColumnItem`).

## 11. Ghi chú tiến độ

- (cập nhật sau khi triển khai)
