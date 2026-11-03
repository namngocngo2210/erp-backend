# Thiết kế: Nhập đơn KOC theo layout 19 cột mới

- **Ngày:** 2026-06-02
- **Phạm vi:** Chỉ trang đơn hàng KOC (`/koc-orders`). Trang đơn thường & landing **giữ nguyên** layout 17 cột hiện tại.
- **Branch:** `koc-import-19col-20260602`

## 1. Bối cảnh

Import "dán văn bản" hiện tại (`POST /api/orders/import-text`) parse **positional**: map cứng theo thứ tự
`IMPORT_PAYLOAD_FIELDS` (17 cột) và **bỏ qua tên header**. Endpoint này dùng **chung** giữa trang KOC và
trang đơn thường (chỉ Landing có endpoint riêng `import-landing-text`).

Đội KOC cần dán đơn theo một layout **19 cột** khác (xuất từ sheet nội bộ/KiotViet), khác cũ ở:

| Khác biệt | Cũ (17 cột) | Mới KOC (19 cột) |
|---|---|---|
| Thêm cột | — | **Mã KH** (vd `KOC790`) |
| Thêm cột | — | **Khu vực** + **Phường/Xã** (địa chỉ tách sẵn) |
| Đảo thứ tự | Giảm Giá → Giảm Giá Phiếu Đặt | **Giảm Giá Phiếu Đặt → Giảm Giá** |
| Bỏ cột | Khách TT Trước | (không có) |

## 2. Quyết định thiết kế (đã chốt với người dùng)

1. **Phạm vi: KOC-only.** Tách path riêng cho KOC, không đụng trang đơn thường/landing.
2. **Vẫn dán giá trị thuần (positional)**, không có dòng header. Header chỉ là minh hoạ tên cột.
3. **Mã KH:** nếu `Customer.code` tồn tại → gắn đơn vào KH đó; không có thì fallback tạo/khớp theo SĐT như hiện tại.
4. **Khu vực + Phường/Xã:** vẫn parse địa chỉ từ cột "Địa chỉ" (raw_address). Hai cột này **chiếm vị trí nhưng bị bỏ qua**.
5. **Trọng lượng:** lưu nguyên giá trị (vd `4500` → `4500`), không quy đổi đơn vị.

## 3. Layout 19 cột KOC (positional)

| # | Cột | Map vào payload | Ghi chú |
|---|---|---|---|
| 1 | Kênh Kiot | `kiotviet_channel_id` | `old_kiot`→5, `new_kiot`→4 (alias sẵn có) |
| 2 | Mã KH | `customer_code` | 🆕 match KH theo code |
| 3 | Người tạo | `staff_username` | lookup `User.username` |
| 4 | Kênh Bán | `channel_code` | `Hàng mẫu KOC`→`koc` (alias sẵn có) |
| 5 | Tên KH | `name` | |
| 6 | Giới Tính | `customer_gender` | |
| 7 | SĐT | `phone` | |
| 8 | Địa chỉ | `raw_address` | nguồn parse địa chỉ |
| 9 | Khu vực | _(placeholder, drop)_ | 🆕 chiếm vị trí |
| 10 | Phường/Xã | _(placeholder, drop)_ | 🆕 chiếm vị trí |
| 11 | Mã SP | `sku_list` | |
| 12 | SL | `quantity_list` | |
| 13 | Giá | `price_list` | |
| 14 | Giảm Giá Phiếu Đặt | `booking_voucher_discount` | ⚠️ đảo thứ tự so với cũ |
| 15 | Giảm Giá | `discount_amount` | |
| 16 | Trọng Lượng | `weight` | giữ nguyên giá trị |
| 17 | Kích Thước | `dimensions` | |
| 18 | BRANCH_ID | `branch_id` | lưu chuỗi as-is |
| 19 | Loại Đơn | `order_type` | `đơn có trả`→`has_return` (alias sẵn có) |

Không có cột `customer_prepaid`; với `has_return`, prepaid được suy ra như logic hiện tại
(`_build_row_payload`: prepaid = total_after_discount nếu > 0, ngược lại = total).

## 4. Thay đổi chi tiết

### 4.1 Backend — `apps/orders/import_excel.py`
- Thêm hằng `KOC_IMPORT_PAYLOAD_FIELDS` (19 phần tử) gồm `customer_code` và 2 placeholder
  (vd `"_ignore_area"`, `"_ignore_ward"`) sẽ bị drop trong `_build_row_payload`.
- Tham số hoá `import_orders_from_text(..., field_layout: list[str] | None = None)` (mặc định
  `IMPORT_PAYLOAD_FIELDS`) để tái dùng toàn bộ logic parse/validate/permission/create.
- Thêm `import_orders_from_koc_text(*, text_data, actor)` gọi `import_orders_from_text` với
  `field_layout=KOC_IMPORT_PAYLOAD_FIELDS`.
- `_build_row_payload`: nhận `customer_code` (passthrough vào payload), drop các key placeholder.

### 4.2 Backend — serializer & service
- `OrderCreateSerializer` (`transport/serializers/.../order_action_serializers.py`):
  thêm `customer_code = serializers.CharField(required=False, allow_blank=True, default="")`.
- `OrderServiceCommands.create_order_manual`: nếu `payload.get("customer_code")` không rỗng và
  `CustomerService.get_by_code(code)` trả về KH → dùng KH đó (vẫn cập nhật name/phone/gender như
  logic hiện tại); ngược lại fallback `get_or_create_by_phone`. Chỉ kích hoạt khi có `customer_code`
  nên các luồng tạo đơn khác không bị ảnh hưởng.

### 4.3 Backend — transport/urls
- View mới `import_orders_koc_text` (mirror `import_orders_text`) gọi `import_orders_from_koc_text`.
- Đăng ký route `POST /api/orders/import-koc-text`.

### 4.4 Frontend — `pages/OrdersPage.tsx`
- Thêm `KOC_IMPORT_HEADERS` (19 cột) dùng cho: preview table, `formatImportText`
  (`targetHeaderCount`), và chọn endpoint.
- Khi `isKoc`: `activeImportHeaders = KOC_IMPORT_HEADERS`, endpoint = `/api/orders/import-koc-text`,
  payload `{ text }`.
- `formatImportText` cần nhận đúng `targetHeaderCount` theo `isKoc` để gộp dòng chính xác.

## 5. Kiểm thử

**Backend** (`apps/orders/tests/`): test mới dán 1 dòng 19 cột, assert:
- `booking_voucher_discount` và `discount_amount` đúng (không bị đảo).
- KH khớp theo `customer_code` khi tồn tại; fallback theo SĐT khi không có.
- `order_source == "koc"`.
- `weight` giữ nguyên giá trị.
- địa chỉ parse từ `raw_address` (cột Khu vực/Phường-Xã bị bỏ qua).
- `order_type == has_return` cho "Đơn có trả".

**Frontend:** kiểm tra `isKoc` dùng đúng bộ header 19 cột & endpoint mới (nếu có test phù hợp).

## 6. Sprint

- **Sprint 1 — Backend parse + service:** `KOC_IMPORT_PAYLOAD_FIELDS`, parametrize import, customer_code
  ở serializer + `create_order_manual`, endpoint + route. Viết test backend.
- **Sprint 2 — Frontend:** `KOC_IMPORT_HEADERS`, nhánh `isKoc` cho preview/format/endpoint.
- **Sprint 3 — Kiểm thử & hoàn thiện:** chạy test backend/frontend, rà soát, cập nhật tiến độ.

## 7. Rủi ro / lưu ý

- `create_order_manual` dùng chung nhiều luồng → thay đổi customer_code phải **opt-in** (chỉ chạy khi có code).
- `formatImportText` gộp dòng theo số cột; sai `targetHeaderCount` sẽ gộp/sai dòng → cần truyền đúng theo `isKoc`.
- Khi match KH theo code, vẫn cập nhật name/phone từ dòng import (chấp nhận, vì cùng nguồn dữ liệu).

## 8. Tiến độ (cập nhật 2026-06-02)

**Đã làm (tất cả sprint):**
- Backend: `KOC_IMPORT_PAYLOAD_FIELDS`, parametrize `import_orders_from_text(field_layout=...)`,
  `import_orders_from_koc_text`, đọc `customer_code` trong `_build_row_payload`.
- Backend: `customer_code` ở `OrderCreateSerializer`; `create_order_manual` match KH theo code rồi fallback SĐT.
- Backend: view `import_koc_orders_text` + route `POST /api/orders/import-koc-text` (qua facade views/urls).
- Backend: căn lookup "Người tạo" theo đúng ràng buộc serializer (role=STAFF, active) → người tạo không
  phải staff (vd kế toán) sẽ fallback về người thao tác thay vì fail cả dòng. **(phát sinh khi test)**
- Frontend: `KOC_IMPORT_HEADERS` (19 cột) + nhánh `isKoc` cho preview / `formatImportText` / endpoint /
  placeholder.
- Test: `test_koc_orders_import_text_api` (2 case) — xanh. Đã verify không phá vỡ
  `test_landing_import_source_api`, `test_koc_orders_api`, `test_order_create_api`; frontend `tsc` xanh.

**Lưu ý phát sinh — chưa làm (chờ quyết định):** "Người tạo" (vd Lanhketoan/kế toán) nếu không phải
user role STAFF sẽ KHÔNG được ghi vào `order.staff` (đơn gắn người thao tác import). Nếu muốn ghi đúng
người tạo bất kể role, cần nới ràng buộc `staff_id` trong serializer — chưa thực hiện vì ngoài phạm vi.
