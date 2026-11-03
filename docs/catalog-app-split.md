# Tách Product Catalog khỏi `orders` — app mới `apps/catalog`

> Audit 2026-08-19 §C1/§1.7 hạng 6 — điều kiện tiên quyết để tách `inventory`/`warranty`
> theo lộ trình microservices. Nhánh: `refactor/catalog-app-split`.

## 1. Mục tiêu & phạm vi

**Mục tiêu:** move 9 model Product Catalog từ `apps/orders/models.py` sang app mới
`apps/catalog`, **KHÔNG đổi schema DB** (mọi `db_table` giữ nguyên, không một câu SQL
nào được sinh ra), **KHÔNG downtime** (deploy như một lần roll code bình thường,
`migrate` chỉ ghi vào `django_migrations`).

**Trong phạm vi (chỉ model + migration state + import site):**

| Model | db_table | Ghi chú |
|---|---|---|
| `Product` | `products` | M2M `categories` → bảng ngầm `products_categories` (tên suy từ db_table + field, không đổi sau move) |
| `ProductGroup` | `product_groups` | |
| `ProductCategory` | `product_categories` | |
| `ProductPackage` | `product_packages` | FK → Product |
| `ProductComponent` | `product_components` | 2 FK → Product |
| `PriceBook` | `price_books` | |
| `ProductPackagePrice` | `product_package_prices` | FK → ProductPackage, PriceBook |
| `ProductProfile` | `product_profiles` | O2O → Product, FK → `users.User` (string ref) |
| `ProductProfileImage` | `product_profile_images` | FK → ProductProfile |

Kèm theo: hằng `PROFILE_SECTIONS` (gắn chặt ProductProfile) move cùng.

Cả 9 model đã khai `db_table` tường minh trong Meta (kiểm 2026-08-20,
`apps/orders/models.py:124-343,1566-1623`) — không model nào dùng tên bảng mặc định,
không cần khai bổ sung.

**Ngoài phạm vi (cố ý):** views/serializers/services của catalog vẫn nằm trong
`apps/orders` (product_catalog_views, price_book_service, crm_catalog…) — move code
nghiệp vụ là bước sau, MR riêng, để MR này giữ đúng một bản chất: *state-only model
move*, diff review được.

## 2. Kỹ thuật: state-only move (SeparateDatabaseAndState)

Chuẩn Django cho việc đổi app sở hữu model mà không đụng DB: mọi thay đổi bọc trong
`migrations.SeparateDatabaseAndState(state_operations=[...], database_operations=[])`.

### 2.1 Bản đồ migration mới

| # | File | state_operations | Phụ thuộc |
|---|---|---|---|
| 1 | `catalog/0001_initial` | `CreateModel` × 9 (đầy đủ index/constraint/db_table như state hiện tại) | `users.__first__` (swappable), `orders/0133` (neo sau head hiện tại) |
| 2 | `channels_core/0023` | `AlterField Channel.price_book → catalog.pricebook` | `catalog/0001`, `channels_core/0022` |
| 3 | `inventory/0040` | `AlterField` product × 5 (StockMovement, StockItem, StockDocumentLine, StockReclassLog, ReturnNoteLine) | `catalog/0001`, `inventory/0039` |
| 4 | `warranty/0020` | `AlterField WarrantyJourney.product` | `catalog/0001`, `warranty/0019` |
| 5 | `warranty_codes/0006` | `AlterField` product × 2 (WarrantyCodeBatch, WarrantyCode) | `catalog/0001`, `warranty_codes/0005` |
| 6 | `orders/0134` | `AlterField OrderItem.product → catalog.product` + `RemoveField/DeleteModel` × 9 | `catalog/0001`, `orders/0133`, **và cả 4 migration AlterField ở trên** — DeleteModel state chỉ hợp lệ khi không còn FK nào trong state trỏ về `orders.Product`/`orders.PriceBook` |

Thứ tự áp: catalog tạo state → mọi FK ngoài trỏ sang label mới → orders xoá state cũ.
`database_operations=[]` ở TẤT CẢ các file trên.

### 2.2 Kiểm chứng vàng (bắt buộc trước khi merge)

1. `python manage.py makemigrations --check` → `No changes detected`.
2. `sqlmigrate` từng migration mới → **không một câu SQL nào** ngoài `BEGIN/COMMIT`.
3. Migrate từ DB rỗng OK (chính là đường test suite đi — `--create-db`).
4. **Migrate trên DB dữ liệu cũ OK** (mô phỏng prod): tạo DB mới, migrate tới
   `master`, checkout nhánh này, `migrate` tiếp → chỉ 6 dòng mới trong
   `django_migrations`, schema diff = rỗng.
5. Full backend suite xanh.

### 2.3 ContentTypes

Đã grep toàn bộ `apps/` (loại migrations): **không có `GenericForeignKey`, không có FK
tới `ContentType`** ở bất kỳ đâu; RBAC là hệ tự viết (`apps/users/rbac.py`,
`require_section`), không dùng `django.contrib.auth` model-permission theo
ContentType. ⇒ **không cần data migration đổi `django_content_type.app_label`**.
Hàng `orders.product…` cũ trong `django_content_type` thành mồ côi vô hại; dọn (tuỳ
chọn, sau deploy, không gấp) bằng `manage.py remove_stale_contenttypes`.

### 2.4 Migration lịch sử của app khác tham chiếu `orders.product`

Các file migration cũ (vd `inventory/0001` FK `to='orders.product'`,
`channels_core/0022`…) **giữ nguyên** — graph lịch sử tự nhất quán: tại thời điểm đó
model còn ở orders, các AlterField mới chuyển state về sau. Chỉ code sống mới đổi.

## 3. Bản đồ import site (đo 2026-08-20 trên master `ea017286`)

`from apps.orders.models import <9 tên>` xuất hiện ở **~110 file** thuộc 13 app.
Chiến lược:

- **App ngoài orders — ĐỔI THẲNG** sang `from apps.catalog.models import …` (mục tiêu
  chính là gỡ phụ thuộc vào orders): `inventory` (~50 file, đa số test), `warranty`,
  `warranty_codes`, `cnv_loyalty`, `kiotviet`, `channels_core`, `customers`,
  `callcenter`, `foxia`, `facebook_ads`, `users` (seed command).
- **Trong orders** cũng đổi thẳng về catalog (import xuống tầng dưới là hợp lệ).
- **`apps/orders/models.py` re-export 9 tên + `PROFILE_SECTIONS`** (import từ catalog,
  đánh dấu deprecated) — giảm bán kính nổ cho mọi chỗ sót/nhánh đang mở; sẽ gỡ ở MR
  dọn sau.
- Import hỗn hợp (`import Order, Product`) tách làm 2 câu import.
- **3 site `get_model("orders", "Product"/"ProductCategory"/"PriceBook")`** phải đổi
  label sang `"catalog"` — sau DeleteModel state-only, label cũ raise `LookupError`:
  `cnv_loyalty/subscribers.py:113-114`, `channels_core/.../channel_serializers_impl.py:27`.
  Test `orders/tests/test_migrations_scratch_db.py` cũng get_model theo label orders → đổi.
- String ref `"orders.Product"`/`"orders.PriceBook"` trong **models.py** của
  inventory/warranty/warranty_codes/channels_core → `"catalog.Product"`/`"catalog.PriceBook"`.
- `db_table` không đổi ⇒ mọi raw SQL/export cứ dùng `products`… như cũ, không đụng.

## 4. Hàng rào kiến trúc

### 4.1 `.importlinter` (exhaustive đang bật — app mới không khai tầng là FAIL)

Thêm tầng `catalog` **dưới** `customers | orders | inventory | hr` và **trên**
`warehouses`: mọi app nghiệp vụ được import catalog; catalog chỉ được nhìn xuống
(users/channels_core/common — thực tế catalog/models.py không import app nào,
FK users.User bằng string ref).

Hệ quả entry:
- Entry nợ B-Lô6 `X -> apps.orders.models` mà file X sau rewrite **không còn** import
  orders.models (chỉ dùng Product/PriceBook) → unmatched → **xoá** (nợ GIẢM thật).
- Import từ app tầng DƯỚI lên catalog (channels_core tests, users seed/staging
  command) → entry ngoại lệ khối A mới, cùng bản chất entry A cũ của chúng.
- Chạy `lint-imports` lặp tới KEPT; tổng nợ khối B chỉ được giảm.

### 4.2 FK guard (`apps/common/tests/test_fk_layering_guard.py`)

- `("inventory", "orders.Product")` → FK giờ trỏ `catalog.Product` (hợp tầng, xuống
  dưới) → **xoá entry** (stale-check tự đòi). Nợ trả thật.
- `("channels_core", "orders.PriceBook")` → FK vẫn NGƯỢC tầng nhưng đích đổi thành
  `catalog.PriceBook` → **đổi key + lý do trung thực**: move sang catalog thu hẹp
  khoảng vượt (không còn xuyên lên orders god-module) nhưng chưa trả hết; trả nốt
  khi đảo quan hệ (catalog không nên biết channel, mà channel giữ config giá).
- `("inventory", "orders.Order")` và các entry khác: giữ nguyên (Order vẫn ở orders).

## 5. Rủi ro & rollback

| Rủi ro | Xác suất | Giảm thiểu |
|---|---|---|
| Migration sinh SQL ngoài ý muốn (index/constraint bị autodetector diff lại) | Thấp | Kiểm chứng vàng #2: sqlmigrate từng file phải rỗng SQL; CreateModel copy đúng state hiện tại |
| Sót site `get_model("orders", …)`/string-ref → LookupError runtime | Trung bình | grep 3 dạng (`get_model`, `"orders.Product"`, import) + full suite; re-export trong orders.models đỡ cho dạng import |
| Bảng M2M ngầm đổi tên | ~0 | Tên suy từ `db_table` của Product (`products_categories`) — db_table không đổi nên tên giữ nguyên; suite migrate-từ-rỗng + so schema DB cũ xác nhận |
| Code bên ngoài (celery beat args, export def) tham chiếu label app cũ | Thấp | grep `"orders", "Product"` mọi biến thể; audit log dùng CharField tự do, không dính label |
| Nhánh đang mở khác merge sau, còn import từ orders.models | Chắc chắn có | Re-export deprecated giữ tương thích; gỡ ở MR dọn sau khi các nhánh lớn merge |
| Rollback | — | Chưa deploy: revert MR là xong (không có SQL nào đã chạy). Đã migrate trên prod: `migrate <app> <số-cũ>` từng app theo thứ tự ngược (cũng no-op DB) rồi roll image cũ — nhưng vì DB không đổi, roll image cũ TRƯỚC un-migrate cũng an toàn |

Điểm không-downtime: vì mọi migration là state-only, container cũ (code cũ) chạy song
song container mới trong lúc rolling deploy đều đúng — hai bên nhìn cùng một schema.

## 6. Checklist deploy

1. Merge MR (sau khi: makemigrations --check sạch, sqlmigrate no-op, migrate-DB-cũ OK,
   full suite xanh, lint-imports KEPT, ruff sạch).
2. Deploy như bình thường: `migrate` (chỉ ghi django_migrations, xem #2.2) → roll code.
   Không cần cửa sổ bảo trì, không cần khoá ghi.
3. Sau deploy: xác nhận `python manage.py makemigrations --check` trên container prod
   sạch; spot-check 1 trang catalog (danh sách sản phẩm) + 1 luồng tạo đơn.
4. (Tuỳ chọn, tuần sau) `remove_stale_contenttypes` dọn hàng `orders.product…` mồ côi.
5. MR dọn tiếp theo (không gấp): gỡ re-export trong `orders/models.py`, move
   views/serializers catalog sang app catalog.

## 7. Tiến độ

- [x] Khảo sát: vị trí 9 model, db_table, FK ngoài (5 inventory + 1 warranty +
      2 warranty_codes + 1 channels_core + OrderItem trong orders), 3 site get_model,
      không GFK/ContentType. (2026-08-20)
- [x] App `apps/catalog` + move model + INSTALLED_APPS + re-export. (2026-08-20)
- [x] 6 migration state-only + kiểm chứng vàng #1-#4 — bằng chứng ở §8. (2026-08-20)
- [x] Rewrite import sites: 114 file / 13 app + 3 site get_model + string-ref models.
- [x] `.importlinter` (tầng catalog, nợ B-Lô6 106→104) + FK guard (xoá
      inventory→orders.Product; channels_core→PriceBook đổi đích catalog) — KEPT.
- [x] ruff toàn backend: All checks passed.
- [x] Full backend suite: **4365 passed + 39 subtests, 2 failed** — cả 2 là bom
      lịch callcenter có sẵn, đã fix ở MR !1321 (merge sau lúc chạy suite);
      rerun trên nhánh sau rebase: xanh (xem lịch sử MR). (2026-08-20)

## 8. Bằng chứng kiểm chứng vàng (2026-08-20, image desi-backend-test)

1. `makemigrations --check --dry-run` → **No changes detected**.
2. `sqlmigrate` cả 6 migration mới → mỗi cái chỉ `BEGIN; -- (no-op) COMMIT;`.
3. Migrate DB rỗng (code nhánh): áp đủ catalog.0001 + 5 migration state-only, exit 0.
4. Mô phỏng prod: DB `desi_catalog_old` migrate bằng code master (59c6cb16) → pg_dump
   schema → migrate bằng code nhánh → pg_dump lại: **diff schema = rỗng** (chỉ khác
   nonce `\restrict` của pg_dump), `django_migrations` thêm đúng **6 dòng**.
