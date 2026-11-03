# Tên ngắn sản phẩm (`products.short_name`) cho Pancake POS

## 1. Lý do

`products.name` là tên hoá đơn/khai báo (rất dài) và **bị import hoá đơn KiotViet ghi đè**
(`apps/kiotviet/infrastructure/services/_import_service_upsert_mixin.py`,
`_upsert_products_and_items`: tên dòng hàng khác tên hiện tại thì `save(update_fields=["name", ...])`).
Sửa `name` cho gọn trên Pancake POS là vô ích — lần import sau đè lại. Cần một tên ngắn riêng do
người dùng CRM quản lý.

## 2. Phạm vi

- **DB**: `catalog.Product.short_name` `CharField(120, blank, default="", db_default="")`,
  migration `catalog/0006_product_short_name`. `ADD COLUMN ... NOT NULL DEFAULT ''` hằng → PG ≥ 11
  không rewrite bảng (~2,7k dòng). Không index (không lọc/tìm theo cột này).
- **API** `GET/POST /api/orders/products`, `PATCH /api/orders/products/{id}`: nhận/trả `short_name`
  (DRF trim khoảng trắng hai đầu, > 120 ký tự → 400). List nhanh (`_serialize_products_fast`) trả thêm.
- **KiotViet import**: không đụng `short_name` (test `kiotviet/tests/test_import_keeps_short_name.py`).
- **Pancake POS** (`apps/pancake_pos`): `CatalogReader` đọc thêm cột; `CatalogProduct.display_name`
  = `short_name` (đã strip) nếu có, không thì `name`. Mapping dùng `display_name` cho **cả** tên sản phẩm
  Pancake (tiền tố chung trong nhóm / tên SKU lẻ) **và** giá trị "Mẫu" của mẫu mã. Payload đổi → hash đổi →
  đồng bộ tăng dần tự PUT lại (sửa qua form: `catalog_write_service.update_product` → `save()` phát
  `ProductSaved` → hàng đợi dirty; đã có test).
  Nhóm trộn SKU có/không có tên ngắn: tiền tố chung tính trên tên hiển thị của từng SKU — nên đặt tên ngắn
  cho **cả nhóm** theo mẫu `<tên chung> <nhãn mẫu>` (vd. `Ghế OHI01 Đen`, `Ghế OHI01 Trắng`).
- **FE**: form sản phẩm có ô "Tên ngắn (bán hàng / Pancake)" (dưới Tên/SKU, gợi ý ≤ 60 ký tự, bộ đếm
  chuyển đỏ khi vượt, chặn cứng 120); bảng sản phẩm hiện tên ngắn thành dòng phụ dưới tên.
- **Excel sản phẩm**: repo **chưa có** luồng import/export Excel sản phẩm → không làm; cập nhật hàng loạt
  bằng lệnh CSV bên dưới.

## 3. Cách dùng

- Sửa từng SKU: form sản phẩm.
- Hàng loạt (CSV UTF-8, Excel "CSV UTF-8" có BOM cũng được, tiêu đề `sku,short_name`):

  ```
  manage.py set_product_short_names --file short_names.csv --dry-run   # xem trước: đổi gì, SKU nào không có
  manage.py set_product_short_names --file short_names.csv             # ghi thật
  ```

  SKU so khớp không phân biệt hoa/thường, chỉ sản phẩm chưa xoá mềm; ô `short_name` trống = xoá tên ngắn;
  khoảng trắng thừa được gộp. Ghi từng dòng qua `save(update_fields=["short_name", "updated_at"])` trong một
  transaction → chỉ SKU thực sự đổi mới phát `ProductSaved` (Pancake tự đẩy lại), lỗi giữa chừng không ghi gì.
  Dòng > 120 ký tự hoặc thiếu cột → dừng trước khi ghi.

## 4. Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| S1 | Cột + migration, API, mapping Pancake, form/bảng FE, lệnh CSV, test | ✅ Xong (`ecff6fe`) |

## 5. Nhật ký

- **2026-09-30 — S1 xong.**
  - Đã làm: như §2–§3. Test: `pancake_pos` (display_name, nhóm/SKU lẻ/trộn, hash đổi, reader, dirty khi
    sửa tên ngắn), `orders/tests/test_product_short_name_api.py`, `kiotviet/tests/test_import_keeps_short_name.py`,
    `catalog/tests/test_set_product_short_names_command.py`, vitest `product-short-name.test.tsx`.
    Chạy full `catalog + pancake_pos + orders + kiotviet`: 2446 passed, 1 skipped; tsc/eslint/knip sạch.
  - Chưa làm: import/export Excel sản phẩm (chưa có luồng sẵn). Tìm kiếm sản phẩm theo tên ngắn chưa hỗ trợ
    (không index; thêm khi có nhu cầu).
