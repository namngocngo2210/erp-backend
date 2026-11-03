# Catalog sản phẩm — payload slim cho ô lọc/ô chọn

**Trạng thái:** đã triển khai (2026-08-20). Không chia sprint — 1 lô, phạm vi nhỏ.

## Bối cảnh

Sự cố 20/08: ô lọc "Tất cả sản phẩm" ở trang *Lịch sử nhập xuất* và *Khách hàng*
nạp đúng 1.000 SP đầu rồi lọc phía client, trong khi prod có 2.131 SP đang bán →
mọi SKU xếp sau (nhóm `TMS*` ở hạng 1.406–1.612 theo thứ tự tên) không bao giờ ra
kết quả. MR !1315 sửa bằng cách nạp trọn catalog qua `useAllProductOptions`.

Hệ quả của cách sửa đó: mỗi phiên tải ~1,5MB cho một dropdown chỉ hiển thị SKU,
vì payload catalog mang theo `packages[]` + giá đã resolve theo bảng giá.

## Thiết kế

### Backend — `GET /api/orders/products?slim=1`

| | Payload đầy đủ | `slim=1` |
|---|---|---|
| Field | id, name, sku, price, weight/length/width/height, is_active, group, packages[], created_at, updated_at | **id, sku, name** |
| Bảng chạm | products, product_groups, product_packages, product_package_prices, price_books | **products** |
| Query | 10 (đo bằng `assertNumQueries`) | 3 |

- Giữ nguyên bộ lọc (`is_active`, chưa xoá mềm), `q`, `group`, sắp xếp `name, id`
  và phân trang → ô lọc thấy đúng tập sản phẩm như trước.
- `price_book` / `include_prices` bị bỏ qua khi `slim=1` (không có gì để áp giá).
- **ETag riêng:** tag slim mang tiền tố `slim` và chỉ ghép từ aggregate bảng
  `products`. Nếu dùng chung tag với payload đầy đủ, client sẽ nhận `304` cho một
  payload khác shape — lỗi im lặng, rất khó lần ra.

### Frontend — mọi ô lọc/ô chọn đi qua `@/lib/api/products`

- `getProductOptions(params)` → `ProductOption[]` (`Pick<Product, "id"|"sku"|"name">`),
  tự gắn `slim=1`.
- `getProducts(params)` giữ nguyên cho form đơn (cần giá + gói).
- `productsCatalogUrl(params)` cho nơi tự cầm `fetchJson` (hook catalog trang Đơn
  hàng inject `fetchJson` để test).
- Đã chuyển sang slim: `useAllProductOptions` (4 trang dùng chung cache),
  `useProductSearch` (combo, bảo hành, mã bảo hành), ô tìm SP của phiếu kho.

### Lưới chắn tái phạm

ESLint `no-restricted-syntax` chặn literal/template bắt đầu bằng
`/api/orders/products?` ở `src/features`, `src/pages`, `src/components` — bắt đúng
kiểu lỗi đã gây sự cố (tự ghép URL kèm `limit` tuỳ hứng). File test được miễn.

## Không làm (có chủ đích)

- **Tìm kiếm phía server cho ô lọc**: BE đã có `q`, nhưng đổi ô lọc sang chế độ
  async đòi `MultiSelect` phải nhớ nhãn của item đã chọn khi danh sách options đổi.
  Với payload slim, chi phí nạp trọn catalog đã giảm ~10 lần nên ngưỡng đau lùi rất
  xa; làm khi catalog vượt ~5k SP đang bán.
- Không đụng `products-grouped` / `catalog/products` — hai endpoint đó phục vụ màn
  quản trị sản phẩm và form đơn, cần payload đầy đủ.
