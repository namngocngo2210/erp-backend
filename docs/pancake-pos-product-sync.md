# Đồng bộ sản phẩm CRM → Pancake POS qua Open API

**Ngày:** 2026-09-29 · **Branch:** `feat/pancake-pos-product-sync`

## 1. Mục tiêu

- Đẩy danh mục sản phẩm CRM lên **một shop Pancake POS** bằng Open API. Chiều đồng bộ duy nhất:
  CRM → Pancake. CRM là master của tên, giá, SKU.
- Chạy lại bao nhiêu lần cũng ra cùng kết quả (idempotent), **không tạo trùng**, kể cả khi
  shop Pancake đã có sẵn sản phẩm (từ sàn, tạo tay).
- Sau lần đẩy đầu, sản phẩm tạo hoặc sửa trên CRM tự động lên Pancake.
- Ngoài phạm vi: tồn kho (đồng bộ tồn làm sau, theo phương án B đã chốt), đơn hàng, combo.

## 2. Hợp đồng API (đọc từ `https://docs.pancake.biz/pos/api/openapi.json`, 29/09/2026)

- Base `https://pos.pages.fm/api/v1`. Xác thực bằng query `?api_key=`, lấy ở Cấu hình → Ứng dụng
  → API KEY.
- `GET /shops`: danh sách shop, dùng để kiểm tra key và lấy `SHOP_ID`.
- `GET /shops/{SHOP_ID}/products/variations?page_size=&page_number=&search=`: danh sách
  **mẫu mã** kèm product và tồn theo kho.
- `GET /shops/{SHOP_ID}/products/{PRODUCT_SKU}`: lấy 1 sản phẩm theo SKU hoặc ID.
- `POST /shops/{SHOP_ID}/products` với body `{product:{name, weight*, custom_id, category_ids,
  product_attributes:[{name, values}], variations:[{fields:[{name, value}], retail_price,
  last_imported_price, weight, size:{length,width,height}, barcode, custom_id, is_hidden}]}}`.
  **Response chỉ có `{success}`, không trả id**, nên phải đọc lại bằng SKU để lấy id.
- `PUT /shops/{SHOP_ID}/products/{PRODUCT_ID}`: cùng body, variation có `id` là sửa, không có
  `id` là thêm mới.
- `GET/POST /shops/{SHOP_ID}/categories`: danh mục sản phẩm.
- `POST /shops/{SHOP_ID}/variations/update_quantity` với `is_actual_remain_quantity`: đặt tồn
  tuyệt đối theo kho. Dùng ở giai đoạn tồn kho, không dùng ở đây.
- Giới hạn gọi: có HTTP 429 "You have reached the limit of requests" nhưng tài liệu không ghi
  con số, nên client phải tự điều tốc và backoff.
- Số nguyên: `retail_price` và `weight` là integer (VND, gram).

**Cần kiểm chứng bằng key thật ở S1** (ghi kết quả vào mục 7): trường nào hiển thị là "Mã SP"
trên UI Pancake (`custom_id` hay `barcode`); `GET products/{SKU}` tìm theo `custom_id` của
product hay của variation; POST thiếu `weight` hoặc `weight=0` có bị từ chối không; con số giới
hạn gọi thực tế.

## 3. Quyết định thiết kế

### 3.1 Phạm vi sản phẩm đẩy lên

Số liệu prod 28/09/2026: 2.151 sản phẩm đang dùng, trong đó 89 mã có `{DEL}` và 495 mã có đuôi
`-N`. Các mã đuôi `-N` không mã nào còn tồn, không mã nào bán trong 90 ngày (có vẻ là lô nhập
KiotViet cũ).

**Đủ điều kiện** = `deleted_at IS NULL AND is_active` VÀ SKU không chứa `{DEL}` VÀ SKU không
khớp `-\d+$` VÀ không phải combo (không có `product_components`, không chứa `+`) VÀ
(**có tồn ≠ 0 ở bất kỳ kho nào** HOẶC **có bán trong 180 ngày**). Ước chừng 400–500 SKU.

- Tiêu chí nằm trong một hàm domain thuần (`is_syncable`), có test. Ghi đè được bằng cờ
  `pancake_sync_mode` trên sản phẩm: `auto` (mặc định, theo luật), `always`, `never`.
- ~~Khi đã tích hợp `product_type` (MR !1504), linh kiện sẽ bị loại.~~ Thay bởi two-way-sync
  §6b (A1): linh kiện VẪN lên Pancake, ẩn, danh mục "Linh kiện" (cờ `PANCAKE_POS_INCLUDE_COMPONENTS`).

### 3.2 Ghép khuôn: nhóm CRM → sản phẩm Pancake, SKU → mẫu mã

- Mỗi `ProductGroup` (auto theo đầu mã, ví dụ OHI01) thành **một** sản phẩm Pancake,
  `product.custom_id = group.code`. Mỗi SKU trong nhóm thành một **variation**,
  `variation.custom_id = sku`, `barcode = sku`.
- SKU không có nhóm (317 mã) thì tự thành 1 sản phẩm 1 variation, `product.custom_id = sku`.
- **Tên sản phẩm Pancake**: `group.name` đang chỉ là mã ("MDD01"), nên dùng
  `f"{group.code} - {tiền tố chung dài nhất của tên các SKU}"`. Rỗng hoặc quá ngắn (< 6 ký tự)
  thì lấy tên SKU đầu tiên. Hàm thuần, có test.
- "Tên SKU" ở đây và ở nhãn "Mẫu" bên dưới = `products.short_name` nếu có, không thì `products.name`
  (docs/product-short-name.md).
- **Thuộc tính variation**: `product_attributes = [{name: "Mẫu", values: [...]}]`. Mỗi variation
  có `fields = [{name: "Mẫu", value: phần tên sau tiền tố chung, fallback phần SKU sau group.code}]`.
- Giá: `retail_price = int(price)`. Cân nặng: `int(weight)` gram, **0 thì dùng mặc định cấu hình**
  `PANCAKE_POS_DEFAULT_WEIGHT_G` = 500. Hiện 2.134/2.151 sản phẩm có weight 0.
- Kích thước: `size` từ length/width/height nếu > 0.
- Danh mục: tạm không đẩy; để S3 nếu cần.

### 3.3 Không tạo trùng: đối chiếu trước khi tạo

Trước mỗi lượt đồng bộ đầy đủ, **kéo toàn bộ variations của shop** (phân trang) và dựng chỉ mục
`sku_upper → (product_id, variation_id)` từ `custom_id` và `barcode` của variation. Với mỗi SKU CRM:

1. Đã có mapping trong DB thì **update** nếu hash payload thay đổi.
2. Chưa có mapping nhưng tìm thấy trên Pancake theo SKU thì **liên kết** (lưu mapping), rồi update nếu khác.
3. Không có ở đâu cả thì **tạo**. Nếu product Pancake của nhóm đã có thì PUT thêm variation vào
   product đó; chưa có thì POST product mới, rồi GET lại theo `custom_id` để lấy id.

SKU trùng (một SKU khớp nhiều variation trên Pancake) thì **không tự xử lý**, đánh dấu
`conflict` để người xem.

### 3.4 Dữ liệu (app mới `apps/pancake_pos`, độc lập với `apps/pancake` là Pages/chat)

```
pancake_pos_product_links                          -- 1 dòng / nhóm hoặc SKU lẻ ↔ product Pancake
  id, group_key varchar(100) UNIQUE,               -- group.code hoặc 'sku:<SKU>'
  pancake_product_id varchar(64) UNIQUE NULL,
  payload_hash char(64), synced_at, created_at, updated_at

pancake_pos_variation_links                        -- 1 dòng / SKU CRM ↔ variation Pancake
  id, product_id bigint UNIQUE REFERENCES products(id),
  product_link_id bigint REFERENCES pancake_pos_product_links(id) ON DELETE CASCADE,
  pancake_variation_id varchar(64) UNIQUE NULL,
  status varchar(20)  -- pending | synced | conflict | error | skipped
  last_error text, payload_hash char(64), synced_at, attempts int, updated_at
  INDEX (status) WHERE status IN ('pending','error','conflict')

pancake_pos_sync_runs                              -- nhật ký mỗi lượt
  id, mode (full|incremental|dry_run), started_at, finished_at,
  created, updated, linked, skipped, conflicts, errors, triggered_by_id
```

- `products` thêm `pancake_sync_mode varchar(10) NOT NULL DEFAULT 'auto'` (dùng `db_default`,
  bảng nhỏ).
- Cấu hình (sysconfig, nhóm integrations): `PANCAKE_POS_ENABLED` (bool),
  `PANCAKE_POS_API_KEY` (secret, mã hoá theo cơ chế secret sẵn có), `PANCAKE_POS_SHOP_ID`
  (int), `PANCAKE_POS_DEFAULT_WEIGHT_G` (500), `PANCAKE_POS_RPS` (2, số request mỗi giây).

### 3.5 Kiến trúc

```
apps/pancake_pos/
  domain/        mapping.py (build payload, tên chung, attribute), eligibility.py, hashing.py  — thuần
  contracts/     ports.py: PancakePosGateway (Protocol), CatalogReader (Protocol)
  infrastructure/pancake_pos_client.py  — HttpClient chung, điều tốc token-bucket, retry 429/5xx
                                           (exponential + jitter), không log api_key
  application/services/product_sync_service.py — full_sync(dry_run), sync_products(ids)
  transport/     POST /api/pancake-pos/sync {mode, limit?, skus?} → 202 {run_id};
                 GET sync-runs (phân trang) · sync-runs/{id} (kèm report) · links?status=&q= ·
                 POST links/{id}/retry · GET status (cục bộ) · GET health (GET /shops, không trả key)
                 quyền: section `pancake-pos` (view = xem, edit = chạy/thử lại)
  subscribers.py event catalog → hàng đợi dirty
  tasks.py       celery (queue foxia): run_sync (tay), sync_dirty_products (beat 120s)
  management/commands/pancake_pos_sync.py  --dry-run --limit --sku
```

- `CatalogReader` đọc CRM theo lô, prefetch nhóm, tồn, bán gần đây bằng 2–3 query tổng hợp,
  **không N+1**.
- **Tăng dần**: không dùng signal `post_save` (ngầm, khó kiểm soát bulk update). Dùng **outbox
  nhẹ** (S2): `pancake_pos` nghe domain event của catalog (`catalog.product_saved` phát sau commit
  từ MỌI `Product.save()`, `catalog.product_group_saved`, `catalog.products_changed` cho đường
  bulk/bảng phụ như thành phần combo) → `INSERT … ON CONFLICT DO NOTHING` vào bảng
  `pancake_pos_dirty_products(product_id PK, marked_at)`. Beat mỗi 2 phút (chỉ khi
  `PANCAKE_POS_ENABLED`) lấy tối đa 200 id bằng `DELETE … FOR UPDATE SKIP LOCKED RETURNING` →
  `sync_products(ids)` (kéo cả nhóm, chỉ mục Pancake tối thiểu: tìm theo SKU chưa liên kết);
  lỗi cả lượt thì trả id về hàng đợi.
- **Không xoá trên Pancake**: SKU đã liên kết mà xoá mềm / `never` / hết đủ điều kiện → ẩn mẫu
  mã (`is_hidden=true`) trong lần PUT của nhóm (hoặc PUT chỉ-ẩn nếu cả nhóm hết đủ điều kiện),
  liên kết chuyển `skipped` với lý do. Đủ điều kiện lại → PUT `is_hidden=false`, về `synced`.
  Full sync có `--limit` không ẩn hàng loạt ngoài các nhóm đã chọn.
- **PUT luôn gửi đủ mẫu mã** (S2, phòng rủi ro §7): GET sản phẩm ngay trước mỗi PUT, gửi kèm MỌI
  mẫu mã đang có không thuộc payload CRM với `id` + trường đọc về nguyên trạng (`fields`, giá,
  cân nặng, barcode, custom_id, `is_hidden`, ảnh — KHÔNG gửi `variations_warehouses`); giá trị
  thuộc tính của chúng gộp vào `product_attributes`. GET không thấy sản phẩm → không PUT (lỗi nhóm).
- **Khoá**: một khoá Redis `single_flight` cho mọi lượt (full, dry-run, tăng dần); lượt chạy tay
  qua API chặn sớm bằng kiểm tra "còn lượt full/dry-run chưa xong" trong `pg_advisory_xact_lock`
  (409). Task full đụng khoá với lượt tăng dần → tự thử lại sau 30s.
- **Dry-run** xuất báo cáo (JSON/CSV trong sync_run) cho biết sẽ tạo / liên kết / cập nhật / xung
  đột bao nhiêu, và mẫu 20 payload, **không gọi API ghi**.

## 4. Rủi ro

| Rủi ro | Giảm thiểu |
|---|---|
| Tạo trùng trên shop đã có hàng | Đối chiếu SKU trước (3.3), dry-run bắt buộc trước lần full đầu |
| POST không trả id | GET lại theo `custom_id`; không thấy thì đánh `error`, lượt sau sẽ liên kết |
| Giới hạn gọi chưa rõ | Token bucket cấu hình được + backoff 429 |
| Tên/giá CRM chưa sạch | Dry-run + đồng bộ theo lô nhỏ (`--limit`) trước khi chạy hết |
| Pancake đổi schema | Client parse phòng thủ; test hợp đồng bằng fixture JSON lấy từ openapi |

## 5. Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| S1 | App `pancake_pos`: sysconfig, client (điều tốc/retry), domain mapping + eligibility, models + migration, `CatalogReader`, `full_sync` (dry-run + thật), management command, test (client mock, mapping, idempotency, conflict) | ✅ Xong (`0637474`) |
| S2 | Tăng dần (dirty + beat), API trigger/nhật ký/links, UI tối giản ở trang Cấu hình tích hợp (nút Dry-run / Đồng bộ, bảng lượt chạy, danh sách lỗi/xung đột), `pancake_sync_mode` trên form sản phẩm | ✅ Xong (`ee4d803`) |
| S3 | Chạy thật sau khi MR merge + deploy (vận hành, không code): nhập key, kiểm chứng §2/§7, dry-run trên prod, đồng bộ theo lô, đối soát | ⏳ cần API key + SHOP_ID |

## 6. Nhật ký

- **2026-09-29**: Thiết kế theo Open API chính thức. Người dùng chọn làm bằng API thay vì Excel.

- **2026-09-29 — S1 xong.**
  - Đã làm: app `apps/pancake_pos` (tầng cùng `kiotviet | foxia | ...` trong `.importlinter`,
    không import code app khác ngoài `common`/`sysconfig`); 6 khoá sysconfig nhóm
    integrations (`PANCAKE_POS_API_KEY` dùng `secret=True` → Fernet ở `system_settings.secret_value`,
    `ENABLED`/`API_KEY` `staging_forbidden`); `PancakePosClient` (HttpClient `retries=0` + retry
    tự quản, token bucket, backoff mũ + jitter, tôn trọng `Retry-After`, redact `api_key` ở mọi
    message, `raise ... from None` để traceback không mang URL); domain `eligibility`/`mapping`/
    `hashing`/`pancake_index` thuần; 3 bảng + `products.pancake_sync_mode` (`db_default='auto'`,
    migration `catalog.0005`); `SqlCatalogReader` 1 câu SQL (đo prod: CTE bán 180 ngày ~290 ms,
    518 SKU đủ điều kiện); `ProductSyncService.full_sync`; lệnh `pancake_pos_sync`
    (`--dry-run --limit --sku --show-samples`). 53 test mới (client mock, domain, reader không
    N+1, service: idempotent, liên kết, PUT thêm mẫu mã, xung đột, lỗi cô lập, dry-run).
  - Lệch/bổ sung so với design:
    - `pancake_pos_sync_runs` thêm cột `report jsonb` (mẫu 20 payload, lý do loại, danh sách
      xung đột/lỗi, `fatal`) — design §3.3 nói "báo cáo trong sync_run" nhưng chưa có cột.
    - Cờ `PANCAKE_POS_ENABLED` chỉ chặn lượt GHI; dry-run chạy được khi cờ tắt (chỉ cần key + shop).
    - Retry: GET retry 429/5xx/timeout; POST/PUT **chỉ** retry 429 + ConnectTimeout (5xx/ReadTimeout
      có thể đã ghi → retry = tạo trùng; lượt sau tự liên kết theo SKU).
    - `--sku` kéo **cả nhóm** của SKU; `--limit` tính theo **nhóm** (nhóm là đơn vị một sản phẩm Pancake).
    - SKU lẻ không có thuộc tính "Mẫu" (1 mẫu mã mặc định); nhóm (kể cả 1 SKU) luôn có "Mẫu".
    - Xung đột mở rộng ngoài "SKU khớp > 1 mẫu mã": nhóm rơi vào > 1 sản phẩm Pancake; mẫu mã/sản
      phẩm Pancake đã thuộc SKU/nhóm CRM khác (giữ ràng buộc UNIQUE id Pancake).
    - Lần đầu liên kết luôn PUT một lần (chưa có hash để so với nội dung đang có trên Pancake).
    - "Bán trong 180 ngày" = có dòng `order_items` của đơn không xoá, không mirror (foxia/cnv),
      `created_at` trong cửa sổ — không lọc theo trạng thái đơn.
    - `attempts` = số lần lỗi liên tiếp (thành công thì về 0); xung đột không tăng `attempts`.
  - Chưa làm (đúng phạm vi): incremental/beat, API/UI (S2); gọi API thật (S3).

## 7. Kết quả kiểm chứng API thật

_(điền ở S3)_ — ngoài 4 điểm ở §2, S1 để lại các điểm cần đo:
- Danh sách `products/variations` có trả `custom_id` của mẫu mã và `product.custom_id` không
  (ví dụ trong spec chỉ có `display_id`, `barcode`). Client đang đọc `custom_id`/`barcode`.
- `GET products/{SKU}` có trả `id` sản phẩm ở gốc không (spec không ghi); client lấy `id` →
  `product_id` của mẫu mã → fallback tìm `variations?search=SKU`.
- PUT **thiếu** một mẫu mã đang có trên Pancake có xoá mẫu mã đó không. Từ S2 code KHÔNG còn phụ
  thuộc câu trả lời (luôn gửi đủ), nhưng vẫn cần biết để đánh giá rủi ro sửa tay trên Pancake.
- PUT gửi lại mẫu mã với `fields`/`images`/giá nguyên trạng có bị Pancake coi là "sửa" (đổi
  `updated_at`, ghi log) hay bị từ chối vì thiếu trường nào không.
- PUT chỉ-ẩn (body chỉ có trường sản phẩm đọc về + variations) có giữ nguyên tên/danh mục/ảnh
  sản phẩm không.
- Danh sách `products/variations` có trả mẫu mã đang ẩn (`is_hidden=true`) không — ảnh hưởng
  chỉ mục đối chiếu SKU (GET product trước PUT đã phủ phần gửi đủ mẫu mã).

- **2026-09-30 — S2 xong (`ee4d803`).**
  - Đã làm:
    - **PUT gửi đủ mẫu mã (ưu tiên 1)**: `PancakeVariation/PancakeProduct.raw` giữ trường cần gửi
      lại; service GET sản phẩm ngay trước mọi PUT (∪ chỉ mục), mẫu mã lạ đi nguyên trạng
      (`passthrough_variation`), thuộc tính gộp (`merge_attributes`). Fake Pancake trong test mô
      phỏng trường hợp xấu nhất "PUT thiếu = xoá" — test: mẫu mã lạ còn nguyên, giá không đổi.
    - **Tăng dần**: event `ProductGroupSaved` (phát từ `ProductGroup.save`) + `ProductsChanged`
      (phát từ `ProductComponentService.replace`) trong `catalog.contracts`; `ProductSaved` có sẵn
      phủ create/update/xoá mềm ở `catalog_write_service`, view xoá, import Excel đơn, import
      KiotViet (đều qua `.save()`/`get_or_create`). Rà code: không có đường `queryset.update()` /
      `bulk_update` nào trên `products` ngoài migration. `pancake_pos/subscribers.py` → bảng
      `pancake_pos_dirty_products`; beat `pancake-pos-sync-dirty-products` 120s (queue `foxia`).
    - **Ẩn thay vì xoá** (§3.5), cột `pancake_pos_sync_runs.hidden`.
    - **API** 7 endpoint, quyền mới `pancake-pos:view|edit` (migration `users.0053` seed, CỐ Ý
      không gán role — Administrator có sẵn). Regen `schema.yaml` (chỉ hunk mới) + `schema.d.ts`.
    - **UI** Cài đặt → Pancake POS (`/settings/pancake-pos`, dropdown Cài đặt zone Vận hành cạnh
      Pancake): trạng thái + nút Kiểm tra + chọn shop (superuser, ghi sysconfig
      `PANCAKE_POS_SHOP_ID`), Chạy thử / Đồng bộ (xác nhận), bảng lượt chạy (poll 5s khi đang
      chạy) + dialog báo cáo (lý do loại, xung đột, lỗi, payload mẫu), bảng liên kết (mặc định lọc
      lỗi) + Thử lại. Form sản phẩm: select "Đồng bộ Pancake POS" (Tự động/Luôn/Không); API sản
      phẩm (serializer + danh sách nhanh) nhận/trả `pancake_sync_mode`.
    - **Nhập API key**: Cài đặt → Cấu hình hệ thống → tab "Foxia / Pancake / CNV" (nhóm `integrations`) → `PANCAKE_POS_API_KEY`
      (ô secret sẵn có, superuser; lưu mã hoá, chỉ hiện 4 ký tự cuối), bật `PANCAKE_POS_ENABLED`
      cùng chỗ. Shop chọn ở trang Pancake POS sau khi bấm Kiểm tra (hoặc nhập `PANCAKE_POS_SHOP_ID`
      ở Cấu hình hệ thống).
    - Test BE mới: `test_sync_s2.py` (15), `test_incremental.py` (16: hook sau commit/rollback,
      cờ tắt, nhóm, combo, FIFO, SKIP LOCKED 2 kết nối, beat khoá/lỗi trả hàng đợi, 409 lượt tay,
      task thử lại/bỏ), `test_api.py` (23 ca: quyền, 409, lượt treo quá hạn, broker chết, phân trang,
      lọc, retry, health không lộ key), reader lọc id. Vitest: `sync-logic` + `SyncRunnerCard`.
    - Đã chạy: pytest `pancake_pos catalog users common sysconfig cnv_loyalty orders` xanh trừ lỗi
      có sẵn trên master/môi trường (`test_no_atomic_in_transport` — `order_ingest_views.py`;
      `test_compose_no_registry_keys` — container test chỉ mount `backend/`);
      `makemigrations --check` sạch; pre-push xanh; FE tsc/eslint/knip sạch, vitest 1925/1925.
  - Lệch so với design:
    - Dirty dùng bảng riêng `pancake_pos_dirty_products` thay vì `variation_links.status='pending'`:
      SKU mới chưa có liên kết (FK `product_link` NOT NULL) và hàng đợi cần claim/SKIP LOCKED độc lập
      với trạng thái liên kết. `retry` vẫn đặt liên kết về `pending` để UI thấy.
    - Subscriber chỉ đánh dấu khi `PANCAKE_POS_ENABLED` (trước khi bật bắt buộc dry-run + full).
    - Thêm `GET /api/pancake-pos/status` (trạng thái cục bộ cho UI, không gọi Pancake).
    - Chọn shop không có endpoint riêng: dùng API Cấu hình hệ thống (superuser) — đổi shop là đổi
      nơi ghi, giữ cùng mức quyền với API key.
    - Route task vào queue `foxia` (I/O đối tác, cùng worker poll Pancake), không phải `slow`.
  - Chưa làm / để sau:
    - Liên kết `pending` không có id Pancake mà SKU hết đủ điều kiện sẽ nằm `pending` (không có gì
      để ẩn) — chỉ ảnh hưởng hiển thị.
    - SKU đổi nhóm (mã nhóm đổi) → mẫu mã cũ ở sản phẩm Pancake cũ vẫn hiện; cần xử lý tay/S3.
    - Chưa có beat full định kỳ (sản phẩm có tồn trở lại mà không sửa gì sẽ chỉ lên ở lượt full
      tay). Đề xuất: full hằng đêm sau khi S3 xác nhận ổn.
    - Các điểm §7 (cần key thật).

- **2026-09-30**: Tách phạm vi. MR !1508 chỉ còn **đồng bộ sản phẩm** (S1–S2 + S3 vận hành).
  Đồng bộ hai chiều (tồn, đơn, webhook, hàng hoàn, đối soát) chuyển sang các MR sau, thiết kế ở
  `docs/pancake-pos-two-way-sync.md` trên nhánh `feat/pancake-pos-orders-stock`.
