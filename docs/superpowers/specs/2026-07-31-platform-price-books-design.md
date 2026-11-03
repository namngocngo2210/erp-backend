# Giá sản phẩm theo nền tảng (bảng giá) — đơn KOC đi theo giá web

**Ngày:** 2026-07-31
**Trạng thái:** User đã chốt design
**Liên quan:** `ProductPackage` (migration `0045_productpackage.py`), `Channel.is_koc`

## Bối cảnh

Giá sản phẩm hiện chỉ có một chiều: **gói theo số lượng**.

- `Product.price` — giá cơ sở (đơn giá 1 sản phẩm).
- `ProductPackage(product, quantity, price, weight, length, width, height)` —
  `price` là **tổng tiền của cả gói**, FE chia ra đơn giá (`BlockProducts.tsx:113`).
- `ProductSerializer` bắt buộc phải có gói `quantity=1` và **sync ngược** giá gói 1
  về `Product.price` (`product_item_crm_serializers.py:111`).

Toàn bộ dải giá đang lưu là **giá Facebook**. Nghiệp vụ hiện tại cần bán trên nhiều
nền tảng với giá khác nhau (facebook, web, …), và **đơn KOC phải tính theo giá web**,
không phải giá facebook như hiện nay.

## Quyết định đã chốt

1. **Nền tảng là một entity mới** (bảng giá), không tái sử dụng `Channel` hay
   `OrderSource`. Nhiều kênh dùng chung một bảng giá; đổi bảng giá của KOC là thao
   tác dữ liệu, không phải sửa code.
2. **Kênh bán (`Channel`) quyết định bảng giá.** Không có dropdown chọn bảng giá trên
   form đơn — sale không cần biết khái niệm này.
3. **Chỉ giá khác nhau giữa các nền tảng.** Bộ gói (1sp, 2sp, …) và cân nặng/kích
   thước dùng chung.
4. **Thiếu giá → fallback về bảng giá mặc định** (facebook). Không chặn nghiệp vụ.
5. **UI**: tab nền tảng ngay trong form sản phẩm, không dựng trang quản trị riêng.

### Đặt tên: `PriceBook`, không phải `PriceList`

`price_list` **đã bị chiếm** trong luồng import Excel/KOC với nghĩa hoàn toàn khác —
"danh sách giá của từng dòng SKU trong một đơn" (`_import_excel_constants.py:72,99,144-151`).
Dùng lại tên này sẽ tạo ra hai khái niệm trùng tên trong cùng một app. Entity mới tên
`PriceBook`, UI hiển thị "Bảng giá".

## Mô hình dữ liệu

```
price_books
  id, code (unique khi chưa xoá), name, is_default, sort_order,
  is_active, deleted_at, created_at, updated_at

product_packages          ← BỎ cột `price` ở pha 3
  id, product_id, quantity, weight, length, width, height

product_package_prices    ← BẢNG MỚI
  id, package_id → product_packages, price_book_id → price_books,
  price, created_at, updated_at
  UNIQUE (package_id, price_book_id)
  INDEX  (price_book_id, package_id)

channels
  + price_book_id → price_books (null = dùng bảng mặc định)
```

`is_default` bảo đảm duy nhất bằng **partial unique constraint** trên tập chưa xoá —
đúng pattern `ProductCategory.is_loyal_default` đang dùng (`orders/models.py:186-192`),
kèm hàm hạ cờ các book khác khi set default mới (pattern `_demote_other_defaults`).

### Vì sao tách bảng con thay vì thêm `price_book_id` vào `product_packages`

Đã cân nhắc 3 phương án:

| | Ưu | Nhược |
|---|---|---|
| **A. Bảng con `product_package_prices`** *(chọn)* | Gói + cân nặng/kích thước khai một lần; thêm nền tảng chỉ thêm row giá; fallback là một `COALESCE` tự nhiên | Thêm 1 bảng + 1 join; cần deploy 2 pha vì `ProductPackage.price` đang được đọc nhiều nơi |
| B. `price_book_id` vào `product_packages` | Ít bảng, 1 query | Nhân bản weight/length/width/height theo từng nền tảng → mâu thuẫn quyết định #3, dữ liệu vận chuyển dễ lệch giữa các nền tảng |
| C. JSONB `prices` trên `product_packages` | Migration nhẹ nhất | Mất toàn vẹn tham chiếu; `products_grouped` đang `Min/Max(price)` sẽ phải bới JSON; không index/aggregate được cho report giá sau này |

### `Product.price` được giữ lại

Là **mirror của giá gói `quantity=1` ở bảng giá mặc định**. Không bỏ vì đang được đọc
ở `products_grouped` (`Min/Max(price)` dựng price range), `_serialize_products_fast`,
luồng KiotViet và các report. `_sync_base_from_qty1` chỉ sync từ bảng mặc định.

Ngược lại, `ProductPackage.price` **bị bỏ** ở pha 3: đã có mirror ở tầng product, giữ
thêm một mirror nữa chỉ tạo chỗ cho dữ liệu lệch.

## Luồng resolve giá

Điểm chân lý duy nhất — `apps/orders/application/services/price_book_service.py`:

```python
resolve_price_book(channel) -> PriceBook
    # channel.price_book nếu còn active & chưa xoá, ngược lại → book mặc định

package_prices(product_ids, book) -> dict[(product_id, quantity), Decimal]
    # đã COALESCE(giá của book, giá của book mặc định)

unit_price_for(product, book) -> Decimal
    # giá gói quantity=1 của book (đã fallback) — dùng cho các luồng ingest
```

KOC **không hardcode ở bất kỳ đâu**: kênh `code="koc"` được gán `price_book = web`
bằng dữ liệu. `Channel.is_koc` giữ nguyên vai trò cũ (khoá scope trang KOC, loại khỏi
report ads) và không liên quan đến giá.

### Các call site phải sửa

| Nơi | Hiện tại | Sau |
|---|---|---|
| `list_create_products`, `products_catalog_grouped` | trả `price` + `packages[].price` | nhận `?price_book=`, trả giá đã resolve |
| `order_service_helpers.py:352` (`_replace_order_items`) | fallback `product.price` | `unit_price_for(product, resolve_price_book(order.channel))` |
| `order_service_commands.py:58,228` (ingest landing/Lark) | `p.price * qty` | như trên |
| Import Excel/KOC (`import_excel.py`) | cột giá trong file | giữ ưu tiên file; ô giá trống mới resolve theo kênh |
| `BlockProducts.tsx:113` | `pkg.price / qty` | giữ nguyên, chỉ khác nguồn giá |

### ETag — điểm dễ hỏng nhất

`_products_catalog_etag` (`product_catalog_views.py:138`) hiện ghép từ count/max
`updated_at` của product, package và group. Phải cộng thêm:

- `price_book` đang chọn (khác book → khác etag),
- `Max(updated_at)` của `product_package_prices`,
- count + `Max(updated_at)` của `price_books`.

Bỏ sót: sửa giá web xong client vẫn nhận `304` và tiếp tục bán giá cũ — lỗi im lặng,
không có log, rất khó truy. Có test riêng cho case này.

### Đổi kênh giữa chừng trên form đơn

Đổi kênh → nạp lại catalog theo book của kênh mới → các dòng sản phẩm **chưa bị sale
sửa giá tay** được tính lại; dòng đã sửa tay giữ nguyên.

Mở lại **đơn cũ không bao giờ reprice** — `OrderItem.unit_price` là snapshot tại thời
điểm chốt đơn, đúng hành vi hiện tại.

## API contract

### CRUD bảng giá

RBAC dùng lại section `products` (`require_section("products", *ORDER_SECTIONS)`),
không thêm section mới vào `rbac_catalog.py`.

```
GET    /api/orders/price-books       → [{id, code, name, is_default, sort_order, is_active}]
POST   /api/orders/price-books
PATCH  /api/orders/price-books/{id}  → set is_default sẽ hạ cờ ở book khác
DELETE /api/orders/price-books/{id}  → soft-delete; chặn xoá book đang là default
```

### Hai contract giá tách bạch theo mục đích đọc

**1. Trang quản trị sản phẩm — dữ liệu thô, KHÔNG fallback:**

```jsonc
"packages": [
  { "id": 7, "quantity": 1, "weight": "0.250", "length": "10.00", ...,
    "prices": { "facebook": "250000", "web": "280000" } }
]
```

Book chưa khai giá thì **vắng key**. FE render placeholder *"kế thừa 250.000₫ từ
Facebook"* thay vì số 0. Nếu API fallback sẵn ở đây, admin không thể phân biệt "đã
khai đúng bằng giá FB" với "chưa khai bao giờ".

**2. Catalog cho form đơn — đã resolve, giữ nguyên shape cũ:**

```
GET /api/orders/products?price_book=web&limit=…
→ packages: [{ id, quantity, price: "280000", weight, length, width, height }]
```

`price` là scalar đã fallback → `BlockProducts.tsx` không phải đổi gì.

Tham số `price_book` nhận **`code`** (`facebook`, `web`), không nhận id — code ổn định
giữa các môi trường, id thì không. Thiếu tham số hoặc code không tồn tại → dùng book
mặc định (lenient, không raise — cùng triết lý `OrderSource.normalize`), nên client cũ
không vỡ.

### Channel

`ChannelSerializer` thêm `price_book` (ref `{id, code, name}`, read-only) và
`price_book_id` (write-only, nullable).

## UI

### Form sản phẩm (`ProductsPage.tsx`)

Segmented control `Facebook | Web | +` phía trên bảng gói; bảng gói bên dưới giữ
nguyên bố cục hiện tại.

- Cột **số lượng** và **cân nặng/kích thước** dùng chung, **chỉ sửa được ở tab mặc
  định** — biểu hiện trực tiếp của quyết định #3.
- Ô giá ở tab không phải mặc định, khi để trống, hiện giá kế thừa dạng chữ mờ.
- Validate hiện tại (`packages.${idx}.price` bắt buộc, bắt buộc có `quantity=1` —
  `ProductsPage.tsx:336-356`) **chỉ áp cho tab mặc định**; tab khác được phép để trống
  toàn bộ.

### Form đơn hàng

Sale không thấy khái niệm bảng giá. Đổi kênh → `use-orders-catalogs.ts` nạp lại catalog
theo book của kênh đó.

**Bẫy:** `productsCacheRef` hiện là **một ref duy nhất** (`use-orders-catalogs.ts:128`)
với TTL, không phải react-query. Phải đổi thành Map keyed theo `price_book`, nếu không
đổi kênh sẽ ăn cache của kênh cũ và bán sai giá.

### Quản lý kênh

`ChannelManageDialog` thêm select "Bảng giá", mặc định "(Theo mặc định — Facebook)".

### Không làm trang "Bảng giá" riêng

Chỉ có 2-4 nền tảng. Dựng trang + RBAC riêng cho vài dòng dữ liệu là lãng phí; CRUD
nhét vào tab trong trang Sản phẩm là đủ.

## Migration — 3 pha, không downtime

### Pha 1 — expand (chỉ DB, hành vi không đổi)

- Tạo `price_books`, `product_package_prices`; thêm `channels.price_book_id` (null).
- Seed `facebook` (`is_default=True`, `sort_order=0`) và `web` (`sort_order=1`).
- Backfill: mỗi row `product_packages` → 1 row giá vào book `facebook`, `bulk_create`
  theo batch.
- Deploy độc lập được, rollback = drop bảng.

### Pha 2 — dual-write + đọc nguồn mới

- `ProductSerializer` ghi giá vào `product_package_prices` **và** vẫn ghi
  `product_packages.price` = giá của **book mặc định** (để rollback về pha 1 an toàn).
- API đọc từ bảng mới. FE lên tab bảng giá + select bảng giá ở `ChannelManageDialog`.
- Gán **kênh KOC → book `web`** (thao tác dữ liệu).
- Pha rủi ro cao nhất → smoke test tạo đơn ở cả kênh FB lẫn kênh KOC trước khi mở cho
  sale.

### Pha 3 — contract (deploy sau, khi pha 2 chạy ổn ≥ 1 tuần)

- Drop `product_packages.price`.

## Kế hoạch test

### Backend (pytest, chạy trong Docker)

- `price_book_service`: resolve theo channel; channel null / book soft-deleted / book
  inactive → về default; fallback giá khi thiếu row.
- Serializer: dual-write đúng cả hai nơi ở pha 2; validate chỉ bắt buộc ở book mặc
  định; `_sync_base_from_qty1` chỉ chạy với book mặc định.
- Catalog: `?price_book=web` trả giá web; sản phẩm thiếu giá web trả giá facebook;
  **ETag đổi khi sửa giá web** (chốt chặn cho lỗi im lặng nguy hiểm nhất).
- Ingest: đơn landing/Lark vào kênh KOC → `unit_price` = giá web; vào kênh FB → giá
  facebook.
- Import Excel/KOC: file có cột giá → giá file thắng; ô giá trống → resolve theo kênh.
- Query count: thêm assert catalog không phát sinh N+1 khi có nhiều book (pattern sẵn
  ở `test_orders_list_query_count.py`).
- Migration: dựng vài product + package, chạy migrator, assert số row và giá khớp.

### Frontend (vitest, chạy trong worktree)

- `BlockProducts`: đổi kênh → reprice dòng chưa sửa tay, giữ nguyên dòng đã sửa tay.
- `use-orders-catalogs`: cache tách theo `price_book`, không rò giá giữa hai kênh.
- `ProductsPage`: payload gửi lên đúng shape `prices` theo tab; tab trống không gửi key.

### Smoke trên staging trước khi mở pha 2

Tạo tay 1 đơn kênh FB + 1 đơn kênh KOC, đối chiếu `unit_price` trong DB.

## Ngoài phạm vi

- Giá theo thời gian (giá khuyến mãi có hiệu lực từ/đến).
- Giá theo khách hàng / nhóm khách hàng.
- Khác biệt **bộ gói** giữa các nền tảng (đã chốt: gói dùng chung).
- Khác biệt cân nặng/kích thước theo nền tảng.
- Đồng bộ bảng giá sang KiotViet.
