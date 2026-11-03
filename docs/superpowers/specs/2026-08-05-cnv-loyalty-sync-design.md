# CNV Loyalty sync — design spec

Ngày: 2026-08-05. Trạng thái: đã duyệt (chat), triển khai theo plan `docs/superpowers/plans/2026-08-05-cnv-loyalty-sync.md`.

## 1. Mục tiêu

Đồng bộ một chiều **desi-order → CNV Loyalty** (`apis.cnvloyalty.com`, API kiểu Shopify) để khách hàng
tích điểm/xem đơn trên app loyalty:

- Entity: **Products, Custom/Smart Collections, Customers, Orders** (toàn bộ theo doc CNV).
- Đơn hàng: **đẩy ngay khi tạo** (`financial_status=pending`), **cập nhật khi đổi trạng thái**
  (giao thành công → paid/fulfilled, hủy → cancelled, trả hàng → refunded/restocked).
- Chứng thực: **SSO OAuth đầy đủ** theo doc (`id.cnv.vn`) — authorize → callback đổi code lấy
  access/refresh token, tự refresh khi hết hạn.
- **Backfill toàn bộ** dữ liệu lịch sử, idempotent, chạy được nhiều lần.

Nguồn yêu cầu: tài liệu CNV "SSO - How to connect" (Notion, tổng hợp trong chat 2026-08-05).

## 2. Không nằm trong phạm vi

- Chiều ngược CNV → desi-order (webhook/poll từ CNV): chưa có nhu cầu.
- UI quản trị frontend: đợt đầu thao tác qua API endpoint (admin-only); màn hình quản trị làm sau.
- Login người dùng nội bộ bằng CNV ID: chỉ dùng SSO để lấy API token, không thay JWT hiện tại.

## 3. Kiến trúc

App Django mới **`apps/cnv_loyalty`** (tên tránh đụng feature "loyal customers" nội bộ của
`apps/customers`), đúng chuẩn `docs/architecture-standard.md`, xếp **tầng trên cùng** trong
`.importlinter` (được import `orders`/`customers`/`common`, không chiều ngược lại).

```
apps/cnv_loyalty/
  contracts/dtos.py          # TokenBundle, Protocol CnvApi + CnvTokenProvider (DI cho test)
  domain/mappers.py          # pure functions: model → payload CNV (không I/O, không ORM)
  application/services/
    sync_service.py          # CnvSyncService: sync_customer/product/order/collection (+ dependency)
    backfill_service.py      # keyset pagination, orchestrate backfill
  infrastructure/
    sso_client.py            # OAuth id.cnv.vn: authorize URL, đổi code, refresh, verify
    cnv_client.py            # REST apis.cnvloyalty.com (dùng apps.common.http_client.HttpClient)
    token_provider.py        # đọc CnvConnection, tự refresh, an toàn multi-worker
  transport/api_views.py     # /api/cnv/connect, /api/cnv/callback, /api/cnv/status (admin-only)
  management/commands/cnv_backfill.py
  models.py  subscribers.py  tasks.py  exceptions.py  urls.py  migrations/  tests/
```

### 3.1 Luồng dữ liệu

```
Order.post_save / OrderStatusChanged ──┐
Customer.post_save ────────────────────┤ (on_commit, gate CNV_SYNC_ENABLED)
Product.post_save ─────────────────────┤
                                       ▼
                    enqueue Celery task (queue `slow`)
                                       ▼
              CnvSyncService.sync_<entity>(local_id)
                 │  đọc trạng thái MỚI NHẤT từ DB (last-write-wins)
                 │  đảm bảo dependency: order → customer + products đã sync
                 ▼
              CnvLoyaltyClient ──HTTP──▶ apis.cnvloyalty.com
                 ▼
              CnvSyncRecord (outbox/mapping): synced | failed(+error, attempts)
```

Trigger kép (signal + bus event) là chủ đích: `post_save` phủ mọi đường tạo/sửa qua `.save()`
(kể cả cron import KiotViet), `orders.status_changed` phủ các đường `queryset.update()` có phát
event. Trùng trigger vô hại — task đọc state hiện tại từ DB nên idempotent. Lưới cuối:
cron reconcile hằng đêm.

### 3.2 Models

**`CnvConnection`** (`cnv_connections`) — cấu hình + token OAuth, 1 row active:
`name`, `client_id`, `client_secret` (EncryptedCharField — field chung từ `apps.kiotviet.models`,
cùng cách best_express/facebook_ads dùng), `access_token`/`refresh_token` (Encrypted),
`token_type`, `token_expires_at`, `scope`, `sso_server_uri`/`api_base_url` (override, trống =
lấy settings), `is_active`, timestamps.

**`CnvSyncRecord`** (`cnv_sync_records`) — outbox + mapping ID:
`entity_type` (customer|product|order|custom_collection), `local_id`, `cnv_id` (null tới khi
sync xong), `status` (pending|synced|failed), `attempts`, `last_error`, `last_synced_at`,
timestamps. Unique `(entity_type, local_id)`; partial index `(status, updated_at)` cho
status≠synced (retry cron quét); index `cnv_id`.

### 3.3 SSO OAuth (theo doc CNV)

- `GET /api/cnv/connect` (admin): sinh `state` random 40 ký tự lưu **Django cache** TTL 10'
  (không dựa session — SPA/API), trả về authorize URL
  `{SSO_SERVER_URI}/oauth?client_id&redirect_uri&response_type=code&scope&state`;
  scope đủ 6 quyền: `read/write_products, read/write_customers, read/write_orders`.
- `GET /api/cnv/callback?code&state` : verify state trong cache (pop), đổi code lấy token qua
  `GET {SSO_SERVER_URI}/oauth/token` (grant_type=authorization_code — **doc CNV dùng GET**,
  giữ nguyên theo doc), lưu bundle vào `CnvConnection` active (mã hóa).
- Refresh: `grant_type=refresh_token`. `TokenProvider.get_token()`: token còn ≥120s dùng luôn;
  hết hạn → re-read row từ DB (worker khác có thể vừa refresh — pattern `ensure_token`
  best_express), vẫn hết hạn → refresh + save; rate-limit refresh qua Redis
  (`cache.add`/`incr`, ≤5/phút) tránh bão refresh khi CNV trả 401 hàng loạt.
- Client gặp 401 → force refresh đúng 1 lần rồi retry, vẫn 401 → `CnvAuthError`.

### 3.4 REST client

`CnvLoyaltyClient` bọc `HttpClient` (timeout 10s, retry 3 lần 429/5xx sẵn có), header
`Authorization: Token {access_token}` + `Accept: application/json`. Phủ toàn bộ resource doc:
customers (list/get/create), products (list/get/create/update/delete), custom_collections
(list/get/create) + collects (create — gắn product vào collection), smart_collections
(list/get), orders (list/get/create). Path dùng dạng chuẩn `/{resource}/{id}.json`
(doc có chỗ gõ `/custom_collections.json/{id}.json` — coi là typo, đối chiếu Swagger khi có
token thật). Lỗi HTTP ≥400 → `CnvApiError(status, body[:500])`.

**Đơn đã tạo cần đổi trạng thái**: doc CNV không mô tả PUT /orders. Chiến lược: thử
`PUT /orders/{id}.json`; nếu CNV trả 404/405 thì record đánh dấu failed với lỗi rõ ràng để
biết mà chốt lại với CNV (không đoán mò fallback tự động — tránh tạo đơn trùng).

**Kill switch staging**: mọi method mutating raise `RuntimeError` khi `settings.IS_STAGING`
(pattern KiotViet); `CNV_SSO_CLIENT_SECRET` vào `STAGING_FORBIDDEN_OUTBOUND_ENVS`.

### 3.5 Mapping (domain, pure)

- `split_vietnamese_name("HỒ VĂN KHOA")` → `last_name="HỒ VĂN"`, `first_name="KHOA"` (từ cuối
  là tên gọi — khớp ví dụ trong doc CNV). Rỗng → `first_name="Khách"`.
- Customer → `{last_name, first_name, phone: phone_normalized}` (hệ thống không có email — CNV
  không bắt buộc).
- Product → product 1 variant mặc định: `title=name`, variant `{sku, price, barcode:"",
  inventory_management:false, requires_shipping:true, weight}`; giá từ `Product.price`.
- Order → `{name: order_code|#<id>, unique_id: "desi-<id>"` (chống trùng phía CNV)`,
  line_items` từ `OrderItem` (`product_id/variant_id` = cnv_id đã mapping, `sku, quantity,
  price=unit_price, total_discount`), `subtotal_price/total_discounts/total_price`
  (Decimal → int VND), `customer` (id CNV), `shipping_address` từ bộ
  `address/ward/district/province + *_code` của Order, `note`, `created_at` ISO.
- Trạng thái (`OrderStatus` → CNV):

| OrderStatus | financial_status | fulfillment_status | cancelled_status |
|---|---|---|---|
| 0,1,7,9,11 (chưa giao) | pending | null | uncancelled |
| 2,8,10 (đang giao) | pending | null | uncancelled |
| **3 DELIVERED_SUCCESS** | **paid** | **fulfilled** | uncancelled |
| 4,12,13 (đang trả) | pending | null | uncancelled |
| 5 RETURNED | refunded | restocked | uncancelled |
| 6 CANCELLED | pending | null | **cancelled** |

### 3.6 Sync service + dependency

`sync_order(order_id)`: (1) đảm bảo customer có `CnvSyncRecord.synced` — chưa có thì sync
inline; (2) từng product trong items tương tự; (3) build payload, `create` nếu record chưa có
`cnv_id`, ngược lại `update`; (4) ghi record. Lỗi → `status=failed`, `attempts+=1`,
`last_error` cắt 500 ký tự; **không raise** khỏi task (retry do cron đảm nhiệm, có backoff
theo `updated_at`).

Đơn soft-deleted (`deleted_at`) hoặc customer/product không còn → đánh record `failed` với lý
do, không gọi API.

### 3.7 Celery

- Task: `apps.cnv_loyalty.tasks.{sync_customer,sync_product,sync_order,sync_collection,
  retry_failed_records,reconcile_full,run_backfill}` — tất cả route **queue `slow`** (không
  đổi topology worker prod — bài học sự cố 03/08). `soft_time_limit=60/time_limit=90` cho task
  đơn lẻ; backfill/reconcile 1500/1800.
- Beat: `cnv-retry-failed` mỗi 30' (quét record pending/failed, `attempts < CNV_SYNC_MAX_ATTEMPTS`
  =10, backoff: chỉ lấy `updated_at` đủ cũ theo 2^attempts phút, max 6h); `cnv-reconcile` 02:40
  hằng đêm (vớt entity chưa có record — đường nào lọt trigger; giờ nằm giữa dải retention 03:15-03:50,
  tránh chồng). Cả hai `single_flight` lock, no-op khi `CNV_SYNC_ENABLED` tắt hoặc không có
  connection active.

### 3.8 Backfill

`python manage.py cnv_backfill [--entities products,collections,customers,orders] [--since YYYY-MM-DD]
[--batch-size 200] [--dry-run] [--inline]`. Mặc định dispatch Celery task `run_backfill`
(single_flight); `--inline` chạy trong process (chạy tay giờ thấp điểm). Thứ tự: products →
collections → customers → orders (cũ → mới). **Keyset pagination theo `id`** (không OFFSET —
bài học PgBouncer), sleep ngắn giữa batch để không đập CNV. Idempotent: entity đã `synced` skip;
`unique_id` chống trùng đơn phía CNV kể cả khi record mất.

### 3.9 Cấu hình

Env (thêm `base.py` + `.env.example`): `CNV_SYNC_ENABLED` (default 0 — chưa bật thì signal/cron
no-op hoàn toàn), `CNV_SSO_SERVER_URI` (default `https://id.cnv.vn`), `CNV_API_BASE_URL`
(default `https://apis.cnvloyalty.com`), `CNV_SSO_CLIENT_ID`, `CNV_SSO_CLIENT_SECRET`,
`CNV_SSO_REDIRECT_URI`, `CNV_SYNC_MAX_ATTEMPTS` (default 10).
`CNV_SSO_CLIENT_SECRET` vào staging blocklist. `apps.cnv_loyalty` vào `INSTALLED_APPS` +
tầng trên cùng `.importlinter`.

## 4. Error handling

- Handler signal/bus **nuốt mọi lỗi** (chỉ enqueue — lỗi enqueue log exception, không phá
  luồng tạo đơn).
- Task không raise; mọi thất bại nằm ở `CnvSyncRecord.failed` + `last_error` — quan sát được
  qua `/api/cnv/status` (đếm theo status/entity) và retry tự động có backoff, trần 10 lần.
- CNV down kéo dài: record dồn `failed`, cron retry phục hồi dần khi CNV sống lại; không mất
  dữ liệu vì nguồn chân lý là DB nội bộ.

## 5. Testing

- `test_mappers.py`: tách tên VN, mapping trạng thái đủ 14 status, Decimal→int, payload đơn.
- `test_sync_service.py`: FakeClient inject (pattern best_express) — tạo mới/update, dependency
  order kéo customer+product, lỗi ghi failed+attempts, soft-delete skip.
- `test_token_provider.py`: token còn hạn không refresh, hết hạn refresh + save, re-read DB.
- `test_sso_client.py`: build authorize URL (đủ scope, state), parse token response.
- `test_subscribers.py`: post_save Order/Customer enqueue đúng task; `CNV_SYNC_ENABLED=False`
  → không enqueue; autouse fixture re-register (bài học `event_bus.clear()`).
- `test_api_views.py`: connect/callback/status — quyền admin, state sai → 400.
- `test_tasks.py`: retry_failed chọn đúng record theo backoff + attempts.

## 6. Rủi ro & điểm cần verify khi có credentials thật

1. `PUT /orders/{id}.json` CNV có hỗ trợ không (doc không ghi) — xem §3.4.
2. `GET /oauth/token` (doc dùng GET) — nếu CNV thực tế yêu cầu POST, sửa 1 chỗ trong
   `sso_client.py`.
3. Path chi tiết collections (doc có typo) — đối chiếu Swagger.
4. Rate limit phía CNV chưa công bố — backfill có sleep giữa batch, chỉnh được.
