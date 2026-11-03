# Audit toàn hệ thống — 2026-08-19

> Phạm vi: kiến trúc backend, frontend, database production, security, performance,
> cấu trúc code, design pattern, hạ tầng CI/CD.
> Baseline: master `07f1865a`. Phương pháp: 5 luồng audit độc lập (đọc code trực tiếp,
> đo bằng script, số liệu prod qua user read-only) + đối chiếu chéo với code trước khi
> kết luận. Kế thừa `audit-2026-07-23` / `audit-2026-07-26`: **không lặp lại** các mục
> đã đóng; các mục đó đã verify còn trong code.

## Đánh giá tổng quan

So với đợt 07-26, chất lượng runtime tiếp tục đi lên: WS fanout đã có 10 lớp phòng thủ
kèm test hồi quy, transaction/atomic sạch tuyệt đối (0 HTTP/`.delay()` trong atomic),
webhook ký đúng chuẩn, secrets có guard fail-fast, container non-root, DB không bloat.

Nhưng đợt này lộ ra ba vấn đề **cấu trúc** nghiêm trọng hơn mọi lỗi runtime:

1. **Toàn bộ CI/CD đã bị xoá** (commit `eb15fe28`, "chore: remove ci/cd pipelines") —
   mọi lưới chặn dựng ở các đợt audit trước (lint-imports, test record-scope, guard
   queue Celery, OpenAPI drift-check) hiện chỉ chạy khi ai đó nhớ chạy tay.
2. **Contract phân tầng `.importlinter` đang tự lừa dối**: dấu `:` trong khai báo layer
   là delimiter *non-independent* của import-linter — các app cùng dòng được import
   nhau tự do. ~118 import ngang hàng đang vô hình với contract, tức nợ phân tầng thật
   lớn hơn con số theo dõi ("18 khoản") gần một bậc độ lớn.
3. **Lỗ hổng phân quyền action-level lặp lại có hệ thống**: hàng chục endpoint ghi
   (PATCH/DELETE) trên 13 nhóm view gác bằng quyền `:view`. Cùng lớp lỗi với N1 đợt
   07-26, đã fix cục bộ ≥2 lần nhưng chưa bao giờ sweep toàn cục.

| Mảng | Trạng thái |
|---|---|
| CI/CD | **Không tồn tại — đỏ nhất hệ thống** |
| Security (authN, secrets, webhook, infra) | Tốt |
| Security (authZ action-level) | **Hở diện rộng: ghi bằng quyền đọc** |
| Backend (kiến trúc/phân tầng) | Contract xanh giả; SCC 17 app; orders = 45% LOC |
| Backend (runtime/transaction) | Tốt |
| Performance | Khá — còn 3 điểm P0 ngoài hot path đã vá |
| Database prod | Khá — sạch bloat; còn seq-scan search khách + ~147MB index chết |
| Frontend | Tốt về nền tảng — nợ file lớn + typed client bị bỏ không |

---

## 🔴 NGHIÊM TRỌNG

### N1. CI/CD đã bị xoá toàn bộ — mọi hàng rào chất lượng đang tắt

`git show eb15fe28`: xoá `.gitlab-ci.yml` (282 dòng, 10 job) + 2 GitHub workflow (464
dòng tổng). Hiện tại trên master **không có pipeline nào**.

Hệ quả dây chuyền đã đo được:
- Vi phạm phân tầng lọt vào master ở MR !1166 vì dev quên chạy tay
  (`docs/import-layering-debt-paydown-2026-08.md:425` tự ghi nhận).
- Hàng rào backend duy nhất còn lại là `scripts/check-backend-arch.sh` nhưng chỉ được
  gọi từ `frontend/.husky/pre-push:9` — phụ thuộc dev có cài husky ở... thư mục
  frontend. `.githooks/pre-commit` chỉ check encoding (và `core.hooksPath` trỏ husky,
  `.githooks/` là code chết — đã biết từ trước).
- `lint-imports` crash trên Windows (`'charmap' codec`) vì box-drawing chars trong
  `.importlinter` — cần `PYTHONUTF8=1`, script chưa set.

**Sửa:** khôi phục `.gitlab-ci.yml` từ `eb15fe28^` (file cũ đã chạy tốt, image test
cần bổ sung import-linter — nợ đã ghi nhận 10/08), thêm `PYTHONUTF8=1` vào
`check-backend-arch.sh`, đưa script này vào pre-commit thật. **Không có mục nào trong
tài liệu này đáng làm trước mục này** — mọi fix khác sẽ lại thoái hoá nếu không có gate.

### N2. Endpoint ghi gác bằng quyền đọc — diện rộng, 13 nhóm view

`require_section(x)` = `x:view` (`apps/users/permissions.py:26-33`). Catalog RBAC **có
đủ** `:create/:edit/:delete` nhưng các endpoint sau không dùng (đã xác minh từng chỗ
không có check bổ sung bên trong):

| Vị trí | Hệ quả với user chỉ có quyền xem |
|---|---|
| `warehouses/.../warehouse_views.py:113-125` | xoá mềm kho |
| `orders/.../product_catalog_views.py:298,378,441` | xoá sản phẩm/danh mục |
| `orders/.../price_book_views.py:91` | sửa bảng giá |
| `orders/.../phone_blocklist_view.py:143` | gỡ chặn SĐT |
| `orders/.../order_action_view.py:86-124` | DELETE đơn (PATCH đã có field-policy !1247, DELETE chưa) |
| `inventory/.../flow_history_views.py:464,910,929` + `attachment_views.py:48,118` + `document_views.py:209` + `return_views.py:46` + `stock_views.py:178` | ghi sổ kho, upload/xoá file phiếu |
| `warranty/journey_views.py:23,43` + `claim_views.py:114` | sửa/xoá journey — trong khi `bulk_delete` cùng file (`:139`) gác đúng `:delete` |
| `customers/.../customer_crud_view.py:67` + `resale_tracking_view.py:134,186,232,268` | xoá khách hàng |
| `contact_flow/.../config_views.py:80-157` | sửa cấu hình tác nghiệp |
| `facebook_ads/.../app_config_views.py:60`, `google_ads/.../config_views.py:61` | sửa config token ads |
| `orders/.../kship_note_preset_view.py:104`, `kship_sender_address_view.py:127` | sửa preset KShip |

Đây là lớp lỗi đã fix cục bộ ít nhất 2 lần (`loyalty_view.py:96-97`, foxia
`api_views.py:117`) và đã cắn 2 lần theo chiều 403-oan (MR !1247). Mẫu đúng có sẵn:
`best_express/transport/api_views.py:249-312`.

**Sửa:** một MR sweep theo quy ước `GET=:view / POST=:create / PATCH=:edit /
DELETE=:delete` + **một test kiến trúc** (cùng kỹ thuật AST với
`test_record_scope_coverage.py`) quét mọi view có method ghi mà permission chỉ có
`require_section` → đỏ. Không có test này thì fix từng chỗ sẽ tái diễn lần thứ tư.
⚠️ Trước khi deploy phải rà role prod đang "vô tình" dựa vào lỗ hổng để làm việc
(bài học MR !1247), cấp `:edit`/`:delete` cho đúng role trước, tránh 403-oan hàng loạt.

### N3. `.importlinter` xanh nhưng không chặn gì — lỗi 1 ký tự

`backend/.importlinter:27-30` dùng `:` nối các app cùng tầng; comment dòng 8 nói *"các
app cùng tầng độc lập với nhau"*. Nhưng trong import-linter, `:` là delimiter
**non-independent** (`|` mới là independent) — đã xác minh trực tiếp source
`importlinter/contracts/layers.py`. Nghĩa là `customers ↔ orders ↔ inventory ↔ hr`
được import nhau **tự do** mà contract vẫn KEPT.

Đếm thật: ~118 import ngang hàng đang vô hình (inventory→orders 35, customers→orders
28, orders→customers 20, channels_core→users 15, orders→inventory 7...). Kèm theo:
- **7/21 app không nằm trong contract** (audit, callcenter, contact_flow, dispatch,
  warehouses, warranty, warranty_codes) và không có `exhaustive = true` → import của
  chúng không bao giờ bị soi.
- Đồ thị phụ thuộc thật là **một SCC 17 app** (16 cặp cycle 2 chiều). Chỉ
  `cnv_loyalty`, `foxia`, `warranty_codes`, `common` đứng ngoài.

**Sửa:** đổi `:` → `|` + `exhaustive = true` + khai 7 app thiếu, ghi toàn bộ vi phạm
mới lộ vào KHỐI B (nợ phải trả). Chấp nhận con số nợ thật to hơn 6 lần con số cũ —
thà biết còn hơn contract xanh giả. Làm cùng MR khôi phục CI (N1) để có gate ngay.

### N4. Engine sync KiotViet bị fork 2835 dòng — nhân bản có chủ đích

`apps/kiotviet/management/commands/sync_invoices_between_channels.py` (2835 dòng,
`handle()` 822 dòng) là **bản revert về snapshot 12/05** của
`SyncInvoicesService` (1205 dòng + 3 mixin ~1550 dòng) — docstring `:18-21` tự thú
*"hai luồng KHÔNG còn dùng chung engine"*. Trùng lặp đã xác minh: `_web_list_invoices`
(2 bản), `_normalize_dt_arg` (2 bản), `resolve_target_customer` (2 bản trong cùng
file). Mọi bugfix sync từ 12/05 đến nay chỉ vá một trong hai nhánh.

Bản service cũng không khá hơn về hình thức: `run()` dài **832 dòng**
(`sync_invoices_service.py:84-916`), mixin gọi `self._xxx` chéo nhau — chia file cho
đẹp `wc -l`, không phải composition.

**Sửa:** xoá command monolith, cho CLI gọi lại `SyncInvoicesService` (−2835 dòng,
−4% backend); sau đó tách `run()` thành pipeline steps có test riêng. Cần chạy so
sánh output 2 nhánh trên staging trước khi xoá (đã từng có lý do revert — phải hiểu
lý do đó trước).

---

## 🟠 CAO

### C1. `orders` là god module — 45% backend, 6 bounded context trong 1 file model

- 33.796 LOC non-test (app thứ hai `kiotviet` 17.927), 228 file, fan-in/out 12 app,
  82 điểm import `apps.orders.models` từ 11 app khác, 76 route.
- `apps/orders/models.py` (1623 dòng, 47 class) trộn 6 context: Product Catalog
  (9 model), Order core, Assessment/CRM, KiotViet job queue, KShip, Lead/Sales ops,
  Ads Lark.
- Coupling tầng DB (thứ import-linter mù hoàn toàn): inventory→orders 8 FK,
  inventory→warehouses 8 FK; 2 FK **đi ngược tầng** qua string-ref:
  `channels_core/models.py:21` FK lên `orders.PriceBook`, `users/models.py:86` FK lên
  `kiotviet.KiotVietChannel`.

**Bước đi đúng cho định hướng microservices** (thứ tự sẵn sàng, đã chấm theo fan-in +
FK): tách nội bộ **Product Catalog** ra khỏi `orders` trước (điều kiện tiên quyết cho
inventory/warranty); ứng viên tách service thật đầu tiên là `cnv_loyalty` (fan-in 0,
1 FK, đã có contracts/dtos + client riêng), rồi `foxia`, `warranty*`, `hr`,
`callcenter`. **Chưa tách gì trước khi có CI (N1) và contract thật (N3)** — tách
service trên nền contract giả là chuyển nợ sang network boundary.

Rào cản cụ thể cần gỡ trước: `EncryptedCharField` định nghĩa trong
`kiotviet/models.py:21` nhưng 5 app khác import ở tầng model **và migration đã đóng
băng đường dẫn** (5 file `0001_initial.py`) → move sang `apps/common/fields.py` +
`AlterField` cho 6 app.

### C2. django-ninja là stack tuyên bố, DRF là stack thật

`django-ninja==1.6.2` trong requirements nhưng chỉ 3 file chạm tới, toàn scaffold demo
(`config/api_v2.py:29` — comment "gỡ khi module thật đầu tiên xuất hiện"). 142 file
non-test chạy DRF. Chiến lược strangler chưa migrate được module nào từ khi dựng.

**Quyết định cần chốt** (một trong hai, không treo): hoặc migrate 1 module thật trong
sprint tới (đề xuất: `contact_flow` — nhỏ nhất, sạch nhất), hoặc gỡ django-ninja khỏi
requirements và xoá scaffold. Giữ 2 framework auth/permission/schema song song vô hạn
là chi phí không có ngày thu.

### C3. Business logic + transaction trong DRF serializer

`orders/transport/serializers/order_serializers_impl/product_item_crm_serializers.py`
(681 dòng): 3 cặp `create/update` có `@transaction.atomic`, query `PriceBook`,
`bulk_create` `ProductPackage`/`ProductPackagePrice` — tất cả trong serializer. Toàn
repo 35 điểm `.objects.`/`.save()`/`atomic` trong module serializer. Hệ quả đo được:
35% test suite phải đi qua HTTP client (integration test đội lốt unit test — một phần
lý do suite 19 phút).

**Sửa:** đưa transaction boundary xuống `application/services`; serializer chỉ còn
validate + shape. Làm dần theo file khi chạm, nhưng chặn nợ mới bằng ruff rule hoặc
test kiến trúc quét `atomic` trong `transport/serializers`.

### C4. Ba code path performance P0 (ngoài các hot path đã vá)

1. **`transfer_landing_orders` N+1 ×8** (`order_action_view.py:347-409`):
   `build_order_created_row` serialize từng đơn không có context batch → ~8 query ×
   50 đơn ≈ **400 query + 50 group_send trong một POST**. `build_duplicate_map` được
   gọi 1-phần-tử dù thiết kế cho cả trang. Fix: dựng context batch một lần cho cả lô
   (hạ tầng `_build_order_list_context` có sẵn).
2. **Contact list phân trang bằng Python** (`contact_list_service.py:156-233`):
   materialize toàn bộ DISTINCT ON cửa sổ 92 ngày (~500k orders) vào RAM để trả 20
   dòng; sort + slice Python; export chạy pipeline này **2 lần** (estimate + write).
   Endpoint `GET /api/sale/contacts` — trang Sale mở suốt ca — **không cache gì**.
   Fix tối thiểu: cache `scoped_rows` theo khuôn `_filters_cache_key` + đẩy
   `operation_bucket` xuống SQL (index `op_state_due_idx` đã có sẵn và đang không
   được query nào dùng).
3. **`dispatch_orders_batch`** (`order_service_commands.py:803-854`): load từng Order
   trong vòng lặp (100 round-trip/lô 50) + HTTP KiotViet **tuần tự** trong request
   (50 × 3s timeout = tới 150s/POST khi cache verdict miss). Fix: 1 query `id__in` +
   verify verdict song song bounded (giữ fail-closed).

### C5. WS fanout phía server còn O(clients) toàn cục

FE đã có 10 lớp phòng thủ (xong từ !1205-!1210), nhưng phía server vẫn **một group
`crm_updates` duy nhất** (`channels_core/consumers.py:32`): mọi `order.created` mang
nguyên row serialize đẩy tới mọi client, sale kênh A vẫn nhận toàn bộ traffic kênh
B/C/D rồi vứt ở `_is_authorized`. Payload đã tự chứa `channel_code` → chuyển sang
group theo kênh (`crm_updates:<channel>`) là thay đổi cục bộ, cắt fanout tại nguồn.
Làm cùng nhịp với C4.1 vì `transfer_landing_orders` đang bắn 50 `group_send` rời.

### C6. Search khách hàng seq-scan 756k dòng — index có sẵn nhưng sai dạng

`customers/repositories.py:13-15` dùng `full_name__icontains | code__icontains` →
Postgres sinh `UPPER(col) LIKE` → **không dùng được** 2 GIN trigram bare-column
(`cust_fullname_trgm_idx` 18MB + `cust_code_trgm_idx` 37MB, cả hai 0 scans trên prod).
Giải pháp đã có trong chính repo: functional index
`GinIndex(OpClass(Upper(...), gin_trgm_ops))` như `order_name_upper_trgm_idx`
(115k scans). Cùng lớp lỗi C2 đợt 07-26, tái sinh ở app khác. Đường
`contact_list_service.py:285-287` cũng dùng `icontains` tương tự.

---

## 🟡 TRUNG BÌNH

### T1. Database prod — việc còn lại sau đối chiếu code

Sức khoẻ chung tốt (connections 26-28/100, không bloat, không wraparound, toàn bigint).
Các việc chốt được (mỗi mục đã đối chiếu call site, tránh vết xe "idx_scan=0 → drop"):

| Việc | Chi tiết |
|---|---|
| `GRANT pg_read_all_stats TO claude_ro` | 1 phút, chạy tay prod. Không có nó thì mục top-query của mọi audit sau đều mù (`pg_stat_statements` toàn `<insufficient privilege>`) |
| Drop 4 index chết thật (~147MB) | `ord_delivered_cust_cr_idx` 29MB (bị `ord_gtc_customer_idx` 249M-scans thay 100%, chỉ tồn tại qua RunSQL — an toàn nhất), `order_old_id_idx` 22MB, `customer_code_phone_idx` 63MB, `customer_phone_raw_idx` 33MB. Theo mẫu 0109 CONCURRENTLY |
| **KHÔNG drop** nhóm 0-scan còn call site | `order_product_upper_trgm_idx`, `order_phone_trgm_idx`, `order_ext_order_id_trgm_idx`, item trgm, 5 index audit_logs — nhánh search hiếm chạy nhưng sống |
| Đổi 2 trgm customers sang functional `Upper()` | = fix C6 |
| Dọn 6 index trùng tuyệt đối | fb/gads daily spend (bảng ghi nóng), hr_att_evt, stockitem ×2. 112 cặp "covered-by" FK-auto: **bỏ qua**, planner đang chọn đúng bản nhỏ |
| `autovacuum_vacuum_scale_factor=0.05` cho `order_assessments` | 8.1% dead, **chưa từng được autovacuum** (ngưỡng mặc định 0.2 quá xa) |
| Nợ !1236 còn treo | repack `customers` (idx 432MB/heap 128MB) — VACUUM FULL + lock_timeout, giờ thấp điểm; snapshot có vẻ đã xong (523→62MB), xác nhận với người chạy |
| Cache hit table 87% (orders 79%) | đo lại sau khi cắt index; nếu vẫn thấp cân nhắc shared_buffers 1→2GB (xét RAM chung ads-crm) |
| Migration hygiene | orders có **3 cặp trùng số** (0001, 0112, 0121) — đánh số lại lần chạm tới; 90 file pre-squash nằm cạnh bản squash — dọn khi mọi env đã qua; ⚠️ KHÔNG đề xuất squash lần 3 (xem runbook 2026-07-26) |

### T2. Token ingest dùng chung 3 cửa, phơi qua CORS `*`

`ORDERS_INGEST_API_TOKEN` dùng chung cho landing ingest, Lark ingest, warranty claim
ingest; nginx mở CORS `*` + header `X-Api-Token` trên `/api/orders/ingest` → token nằm
trong JS landing công khai. So sánh `compare_digest` + fail-closed đã đúng. Sửa: tách
token theo nguồn; coi token lộ ở FE là chống-spam (giữ rate zone), không phải auth.

### T3. Frontend — nợ chính

1. **Typed OpenAPI client bị bỏ không**: `api/typed.ts` + `schema.d.ts` 18k dòng +
   drift-check đã có, nhưng 36 module `lib/api/*` gọi thẳng `fetchJson` tự khai type
   tay → "đổi field BE → tsc đỏ FE" không đến được 90% code; gốc rễ của 5 bản
   `Promise<any>` khai lại. Migrate dần theo module khi chạm.
2. **File lớn**: ProductsPage 1525 (tách `PackagesEditor` trước — rẻ nhất),
   OrderFormDialog 1407, ResaleTrackingPage 1280 (190 dòng helper thuần tách được
   ngay), App.tsx 1133, use-orders-table 1112. Mẫu chung: 6/15 file to nhất nằm ở
   `src/pages` — đúng 17/29 feature không có `features/<x>/pages/`. Chốt quy ước:
   feature mới bắt buộc self-contained, page cũ di cư khi chạm.
3. **5 chỗ ghi ref trong thân render** làm React Compiler bail out (App.tsx:887 —
   component lớn nhất app, WebphoneProvider:174, LocationMapPicker:40,
   flow-shared:202, CameraScanner:42). Cùng họ với bug đã cắn 06/08; mẫu đúng có sẵn
   ở `use-crm-websocket.ts:143-157`.
4. Import chéo feature: inventory dùng nguyên `OrdersTable` của orders; `Pager` generic
   nằm trong inventory → thăng cấp lên `components/`. 144 chỗ nuốt lỗi im lặng dù có
   Sentry. `PAGE_SIZE_OPTIONS` khai 3 lần 2 giá trị khác nhau. Script `lint` mặc định
   `--max-warnings=9999`.

### T4. Backend — nợ cấu trúc còn lại

- **Star-import facade 3 tầng** ở `kiotviet/infrastructure/services/`: cùng tập tên
  qua 3 facade, module star-import lẫn nhau → namespace vẫn là khối phẳng 4700 dòng;
  phải ignore F403 (27) + F405 (**576**). Gỡ chuỗi này là trả được 603 vi phạm ruff.
- **Cross-app cross-layer**: `orders/application/.../contact_list_operation.py:10`
  import transport serializer của `contact_flow` (vô hình với contract vì app đó
  ngoài phạm vi — N3 sửa xong sẽ lộ).
- `_repositories_reports.py` 1936 dòng trộn 4 miền báo cáo → tách package `reporting/`;
  `SALE_FB_CHANNEL_IDS = (24,)` hard-code (mìn đã biết) phải ra settings.
- KiotViet — tích hợp hay hỏng nhất — **không dùng** `common/http_client.py` (retry/
  backoff 429/5xx): `common_auth_customer.py:146,585,696`,
  `gateway_kship_customer_webhook.py:50,230,910` gọi `requests` trần.
  `best_express/best_client.py:28` không set default timeout.
- Type hints 68% không có mypy → là tài liệu, không phải ràng buộc. Bật mypy trên
  `apps/*/contracts/` + `apps/common/` trước (đã type đầy đủ), mở rộng dần.
- Không có pytest-cov → 297 file test không nói được vùng nào chưa phủ (`kiotviet`
  17.9k LOC chỉ 21 file test).
- Beat `customers-rebuild-segment-snapshot` (`hour="*/2"` — workload nặng thứ 2 của
  DB) nổ vào 8-16h, đi ngược nguyên tắc lệch-pha của chính file → giãn khỏi giờ làm việc.
- Test N+1 export đỏ trên master: nguyên nhân là RBAC `_compute()` thêm query
  `RoleFieldGrant` (commit `9ba895d5`), **không phải** export tái phát N+1. Sửa đúng:
  warm RBAC cache ngoài block đo — không bump 9→10.
- N+1 nhỏ còn sót: `item.product` thiếu `select_related` ở resale
  (`customer_resale_service.py:116,256,273`) và KiotViet push
  (`invoice_mutations_push.py:471`); contact-flow sweep query `OperationStage` lặp
  mỗi khách (`operation_engine.py:41`) + chưa có trần rows/tick; `rbac.py:132-149`
  query `RolePermission` 2 lần.

### T5. Điểm nhỏ

- `/admin` đăng ký trần (`config/urls.py:7`), không IP-restrict ở nginx → thu hẹp.
- `GET journey-events` không phân trang + purge mặc định TẮT → bảng chỉ tăng;
  audit-logs API hardcode `[:100]` không lật trang được.
- `ORDERS_LIST_COUNT_CACHE_TTL` default 0, chỉ prod bật qua compose → staging/local
  mất prod-parity khi đo.
- Portal bảo hành: throttle là lớp duy nhất chống dò mã 10 ký tự — xác nhận rate đủ chặt.

---

## Những gì đã XANH — xác nhận để không ai "sửa" nhầm

- Transaction/atomic: 158 điểm, **0** HTTP/`.delay()` trong atomic, 84 `select_for_update`,
  `EventBus.publish_on_commit` đúng nguyên tắc.
- Ports & Adapters: 6 port + adapter đăng ký ở `ready()`, có chiều ngược
  (`channels_core` khai port, `orders` cắm) — hexagonal đúng sách, điểm mạnh nhất codebase.
- Signal: 13 điểm, toàn cache-invalidation, có `dispatch_uid`, không signal-gọi-signal.
- Security nền: JWT + token_version, refresh cookie httpOnly, webhook HMAC
  `compare_digest` fail-closed cả 3 tích hợp, secrets guard fail-fast, non-root
  container, ports bind 127.0.0.1, login brute-force 2 lớp, không injection, Excel
  chống formula-injection tập trung.
- FE: WS hardening 10 lớp có test; 30/30 route lazy; strict TS, 0 `@ts-ignore`,
  12 `any` đều có lý do; không lodash/moment/xlsx; `features/exports` mẫu mực.
- DB: không bloat, retention audit_logs đang co đúng dự kiến; quyết định
  "không repack audit_logs" của !1236 được số liệu hôm nay xác nhận đúng.
- `contact_flow` là app mẫu về cấu trúc (mọi file production < 200 dòng) — dùng làm
  chuẩn cho app mới.

---

## Kế hoạch thực hiện

### Sprint 1 — Dựng lại hàng rào (không có nó mọi thứ khác thoái hoá)
| # | Việc | Ước lượng |
|---|---|---|
| 1 | N1: khôi phục `.gitlab-ci.yml` từ `eb15fe28^`, bổ sung import-linter vào image test, `PYTHONUTF8=1` vào check-backend-arch.sh, gắn vào pre-commit | 1 ngày + xác nhận Runner |
| 2 | N3: `.importlinter` `:`→`|` + `exhaustive` + 7 app thiếu; ghi nợ mới lộ vào KHỐI B | 0,5 ngày |
| 3 | N2: sweep quyền ghi + test kiến trúc AST chặn tái phát; rà role prod trước deploy | 2-3 ngày |
| 4 | T1: GRANT pg_read_all_stats; MR drop `ord_delivered_cust_cr_idx` | 0,5 ngày |

### Sprint 2 — Cắt nợ hiệu năng và trùng lặp lớn
| # | Việc | Ước lượng |
|---|---|---|
| 5 | C4.1 + C5: batch context cho transfer + group WS theo channel | 2 ngày |
| 6 | C4.2: cache + đẩy bucket xuống SQL cho contact list | 2 ngày |
| 7 | C6 + T1: MR index đợt 2 (functional trgm customers + drop 3 index chết + 6 trùng) | 1 ngày + đo EXPLAIN |
| 8 | N4: staging-diff 2 nhánh sync KiotViet → xoá command fork | 2 ngày |
| 9 | C2: chốt số phận django-ninja | quyết định + 0-2 ngày |

### Sprint 3+ — Trả nợ cấu trúc theo lộ trình microservices
Tách Product Catalog khỏi `orders` (mở khoá inventory/warranty) → move
`EncryptedCharField` → tách `cnv_loyalty` làm service thí điểm → `foxia` →
đánh giá lại. Song song: C3 (logic khỏi serializer) + T4 (gỡ star-import, mypy
contracts) làm dần khi chạm file, có gate chặn nợ mới từ Sprint 1.

> Nguyên tắc xuyên suốt: **chưa tách microservice nào trước khi CI và contract thật
> hoạt động**. Tách service trên nền contract giả là chuyển nợ import thành nợ
> network — đắt hơn nhiều lần.

---

## Ghi chú phương pháp & hạn chế

- Số liệu prod: counter Postgres tích luỹ từ đầu đời DB (stats chưa reset) — index mới
  (nhóm 0109, 26/07) có idx_scan thấp tự nhiên; đã bù bằng đối chiếu call site.
- `pg_stat_statements` bị chặn quyền → mục top-query chưa làm được (mở khoá bằng
  GRANT ở Sprint 1).
- Mọi đề xuất drop index đều đã grep call site; nhóm "0-scan nhưng code sống" giữ nguyên.
