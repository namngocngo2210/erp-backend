# Audit hiệu năng backend — 2026-09-23 (đối chiếu audit 2026-08-19)

Phạm vi: `backend/apps` (HEAD `db0899a8`). Chỉ đọc + đo, không sửa code.
Mức: **P0** = đang/sắp gây sự cố · **P1** = chi phí lớn ở hot path · **P2** = đáng sửa · **P3** = ghi nhận.

## 0. Cách đo (đo THẬT, không đoán)

Container `desi-web` (image build 00:40 23/09, `apps/orders/serializers.py` trùng worktree) có pytest 9 + pytest-django, nhưng **image không chứa thư mục `tests/`**. Cách chạy: copy 2 file test vào `/tmp/perfaudit` trong container, chạy pytest với `-c /app/pytest.ini --rootdir /app`, và **override env để trỏ DB/Redis sang `desi-test-pg`/`desi-test-redis`** (desi-web ở network `desi-order_default`, tới test-pg qua gateway `172.19.0.1:55432`, test-redis `172.19.0.1:56379`). Dry-run settings xác nhận: `DATABASES.HOST=172.19.0.1:55432 NAME=desi_order`, `CACHES=redis://172.19.0.1:56379/0`, `CHANNEL_LAYERS`/`CELERY_BROKER_URL` cũng trỏ test-redis — không chạm prod.

```sh
# scratchpad/run_in_web.sh
docker exec -i -w /app -e APP_ENV=development -e DJANGO_DEBUG=0 \
  -e POSTGRES_HOST=172.19.0.1 -e POSTGRES_PORT=55432 -e POSTGRES_USER=desi_order \
  -e POSTGRES_PASSWORD=testpass -e POSTGRES_DB=desi_order \
  -e REDIS_HOST=172.19.0.1 -e REDIS_PORT=56379 -e REDIS_PASSWORD= \
  -e REDIS_CACHE_HOST=172.19.0.1 -e REDIS_CACHE_PORT=56379 -e REDIS_CACHE_PASSWORD= \
  -e CELERY_BROKER_URL=redis://172.19.0.1:56379/3 -e CELERY_RESULT_BACKEND=redis://172.19.0.1:56379/4 \
  -e DJANGO_CONN_MAX_AGE=0 -e DB_PGBOUNCER=0 desi-web "$@"
docker cp scratchpad/perfaudit/test_perf_audit_endpoints.py desi-web:/tmp/perfaudit/
./run_in_web.sh python -m pytest /tmp/perfaudit/test_perf_audit_endpoints.py \
   -c /app/pytest.ini --rootdir /app -q -s -p no:cacheprovider --reuse-db
```

Script: `scratchpad/perfaudit/test_perf_audit_endpoints.py` (11 endpoint, `CaptureQueriesContext`, seed 20 rồi thêm 20 → so số query) và `test_perf_audit_services.py` (tầng service/serializer). Tạo test DB ~3' (migrations), 11 test HTTP 224s, 5 test service 23s. Env container có `ORDERS_LIST_COUNT_CACHE_TTL=120` (prod-parity) nên lần gọi 2 của mỗi list ăn cache count/chips → số query giảm; **cột N=20 là số cold**. Đã xoá `/tmp/perfaudit` trong container sau khi đo.

### Kết quả endpoint (số query / thời gian server)

| Endpoint | N=20 | N=40 | Δ query | Kết luận |
|---|---|---|---|---|
| POST /api/orders (limit 50) | 11 q / 104ms | 9 q / 145ms | −2 (cache count) | hằng số, không N+1 |
| POST /api/orders/landing | 11 q / 224ms | 9 q / 466ms | −2 | hằng số; serialize 47→249ms (CPU, §3.6) |
| POST /api/orders/sheet3 | 12 q / 172ms | 9 q / 230ms | −3 | hằng số |
| POST /api/orders/shipments | 3 q / 74ms | 2 q / 45ms | −1 | hằng số |
| GET /api/customers | 1 q / 9ms | 1 q / 16ms | 0 | tốt |
| GET /api/customers/profiles | 8 q / 125ms | 8 q / 160ms | 0 | tốt |
| GET /api/customers/resale | 4 q / 75ms | 1 q / 44ms | −3 (facet cache) | tốt |
| GET /api/customers/loyal | 1 q / 34ms | 1 q / 10ms | 0 | tốt |
| GET /api/sale/contacts | 7 q / 96ms | 3 q / 36ms | −4 (rows cache) | tốt |
| GET /api/orders/dashboard/summary | 3 q / 63ms | 3 q / 51ms | 0 | **không đại diện** — test không có kênh sale nên 8/11 summary short-circuit |
| GET /api/orders/{id} | 9 q / 49ms | — | — | ổn |
| GET /api/orders/{id}/journey-events | 2 q | — | — | ổn (không phân trang) |
| GET /api/customers/{id}/orders (20 đơn) | 6 q / 62ms | — | — | ổn |
| GET /api/orders/history?phone (20 đơn) | 7 q / 108ms | — | — | ổn |

**Kết luận chính: toàn bộ hot path list/detail đơn – khách – contact đã batch đúng, số query không phụ thuộc N.** Phát hiện mới nằm ở đường phụ (admin, worker, sweep) và ở cache/CPU.

### Kết quả tầng service

| Đo | N=10 | N=20 | Query/bản ghi |
|---|---|---|---|
| `UserSerializer(UserService.admin_list_users())` RBAC cache **lạnh** | 54 q / 201ms | 104 q / 275ms | **5/user** |
| … RBAC cache ấm | 4 q | 4 q | 0 |
| `OperationEntryService.sweep()` (khách mới) | 33 q / 205ms | 63 q / 149ms | **3/khách** (+1 transaction/khách) |
| `ChannelTypeSerializer(many)` 12 loại | 13 q | — | **1 COUNT/loại** |
| `build_order_created_row(order)` đường 1 đơn (ingest) | 8 q / 29-74ms | — | 8/event |

---

## 1. Đã đóng (so với audit 2026-08-19) — chỉ liệt kê

| Mục cũ | Bằng chứng đã fix |
|---|---|
| P0.1 transfer_landing_orders N+1×8 | `order_action_view.py:428-440` select/prefetch cả lô khi `len(ids) ≤ _TRANSFER_ROW_PAYLOAD_MAX`; `:490` `build_order_rows_context(orders)` 1 lần; test `test_transfer_row_batch_context.py` assertNumQueries(0) |
| P0 contact list không cache | `contact_list_service.py:302-321` `_enriched_rows_cached` theo `ORDERS_LIST_COUNT_CACHE_TTL` (đo: 7→3 query khi ấm) |
| P1 resale `item.product` | `customer_resale_service.py:123,269` `Prefetch("items", OrderItem.objects.select_related("product"))` |
| P1 KV push payload N+1 product | `invoice_mutations_push.py:494` `.select_related("product")` |
| P1 dispatch_orders_batch load từng đơn + HTTP tuần tự | `order_service_commands.py:977-1008` 1 query `id__in` + prefetch + `_prefetch_dispatch_verdicts` song song |
| P1 contact-flow sweep danh mục lặp + không trần | `entry_service.py:27` `SWEEP_MAX_ROWS_PER_TICK=2000`, `:36` start_stage tra 1 lần; **còn 1 transaction/khách — §2.3** |
| P3 RBAC RolePermission 2 query | `users/rbac.py:131-134` gộp 1 `values_list` |
| P1 ORDERS_LIST_COUNT_CACHE_TTL default 0 ở staging/local | `docker-compose.staging.yml:56`, `docker-compose.local.yml:57` = 120 |
| P2 contact-flow config không cache | `config_views.py:19-38` versioned cache |
| P2 sheet3 temperature_counts stampede | `sheet3_view.py:213-227` `single_flight` + wait 6×250ms |
| P2 beat rebuild-segment-snapshot giờ chẵn | `settings_parts/celery.py:370` `hour="1-23/2"` |
| C6 customers search icontains | `customers/repositories.py:14-24` `__ilike_contains` (khớp GIN bare-column) |
| C5 WS group theo kênh | `groups.py:35-42`, `consumers.py:43-64` join group kênh; publisher `order_event_publisher.py:245` — **mới ở giai đoạn 1, §2.5** |
| Audit log per-request | `dispatch/api.py:60-86` buffer + `threading.Timer` flush → batch, không `.delay()` mỗi request |
| Purge journey events | beat 03:45 (`celery.py:445`) — retention default 0 = no-op (comment `:440`) |

Còn mở nguyên trạng từ audit cũ (không lặp chi tiết): resale `_resale_order_history_by_customer` không LIMIT (`customer_resale_service.py:261-275`); export tồn kho `list(qs[:20_001])` (`inventory/exports_definitions.py:206,216`); journey-events không phân trang (`contact_log_view.py:129-130`); contact list vẫn phân trang Python sau cache (`contact_list_service.py:190-191` `len(filtered_rows)` + slice); `customer__full_name__icontains` ở contact list (`contact_list_service.py:466-468`, cùng lớp C6, chưa đổi sang `__ilike_contains`); 16 file task không có `autoretry_for`/`max_retries` (best_express, callcenter, inventory, hr, google_ads, facebook_ads, exports, warranty, ai_mart, common, customers, analytics, orders/retention|sale_reminder|lark_notification|restore).

---

## 2. Phát hiện mới

### 2.1 — P1 — Dashboard/report: 11 aggregate trên `orders` mỗi request, không cache, không single-flight
- Vị trí: `apps/orders/transport/views/order_views_impl/dashboard_report_views.py:42-95`; `apps/orders/reporting/sale_reports.py:73-520`, `delivery_quality.py:106-260`.
- Mô tả: `dashboard_summary` gọi 11 hàm `summary_*`, mỗi hàm 1 GROUP BY trên `orders` (join `order_assessments`, `Exists` trên `order_journey_events` — `sale_reports.py:143-158`) theo khoảng ngày người dùng chọn. `grep "cache\." apps/orders/reporting/*.py dashboard_report_views.py` → **0 kết quả**. Nhiều người mở dashboard cùng khoảng ngày đầu ca/đầu tháng → mỗi tab lặp lại 11 aggregate y hệt, trong khi `orders:landing_sheet_stats`/`sheet3_temp_counts` (cùng cỡ) đã có cache + single-flight.
- Bằng chứng: test chỉ ghi 3 query vì không có kênh sale (short-circuit) — chi phí thật theo kích thước bảng, không theo N dòng; audit DB 08-19 đã xếp nhóm report vào workload đọc nặng.
- Sửa: bọc `dashboard_summary` (và `reports/overview`, `reports/ads`) bằng `cache.get/set` key `md5(date_from,date_to,scope)` TTL 60-120s + `single_flight` như `sheet3_view.py:213`; cho `?refresh=1` bỏ cache.

### 2.2 — P2 — Admin users list: N+1 RBAC `_compute` khi cache lạnh (đo 5 query/user)
- Vị trí: `apps/users/serializers.py:196-215` (`get_allowed_sections`, `get_editable_order_fields` → `resolve_effective_permissions(obj)` 2 lần/user); `apps/users/rbac.py:183-195`.
- Bằng chứng: N=10→20 users: **54 → 104 query** (x10/x20 `rbac_roles`, `rbac_role_permissions`, `rbac_role_section_scopes`, `rbac_role_field_rules`, `rbac_role_field_grants`). Cache ấm: 4 query. Cache lạnh xảy ra **cho MỌI user** sau bất kỳ `post_save` role/permission/UserChannel (`rbac.py:207-215` bump version toàn cục) — đúng lúc admin ở trang Phân quyền. ~50-100 user = 250-500 query/request. Test `test_users_list_queries.py` **patch** `resolve_effective_permissions` nên không bắt được (docstring tự nhận).
- Kể cả khi ấm: 2 resolve/user × 2 Redis GET = 4 round-trip Redis/user (fail-soft 0.5s, không sập, nhưng 200-400 GET/request).
- Sửa: `admin_list_users` dựng `eff_map = {user.id: eff}` 1 lần (hoặc `resolve_effective_permissions_bulk(users)`: 1 query/bảng với `role_id__in`), truyền qua `context`; serializer đọc map, fallback resolve lẻ khi thiếu context.

### 2.3 — P2 — Contact-flow entry sweep: 3 query + 1 transaction/khách, trần 2000/tick (5')
- Vị trí: `apps/contact_flow/application/services/entry_service.py:52-55` → `operation_engine.py:37-50` (`transaction.atomic` + `select_for_update` + `CustomerOperationState.objects.create` + `ContactActivity.objects.create` từng khách).
- Bằng chứng đo: N=10→20: **33 → 63 query** (x20 SELECT FOR UPDATE, x20 INSERT state, x20 INSERT activity). Worst case theo trần: ~6000 query + 2000 transaction/tick, mỗi 5'.
- Sửa: `existing = set(CustomerOperationState.objects.filter(customer_id__in=ids).values_list(...))` 1 lần, rồi `bulk_create(states, ignore_conflicts=True)` + `bulk_create(activities)` trong 1 transaction; giữ `enter_start` từng khách cho đường request (đơn mới).

### 2.4 — P2 — KiotViet import invoices: `Product.get_or_create` + lookup Customer từng dòng/từng hoá đơn trong worker
- Vị trí: `apps/kiotviet/infrastructure/services/_import_service_upsert_mixin.py:194-208` (`for sku, row in aggregated.items(): Product.objects.get_or_create(...)` + `product.save()`), `:47-97` (3-5 query Customer/hoá đơn: filter code, filter phone, exists code…), `:173,227-228` (`OrderItem.filter(order).delete()` + `bulk_create` — 2 query, ổn).
- Mô tả: page 100 hoá đơn (`import_invoices_service.py:121 page_size=100`) × (3-5 Customer + 1-3/dòng Product + Order create/save + `_get_or_create_order_channel`) ≈ 10-15 query/hoá đơn → 1000-1500 query/page, tuần tự, `transaction.atomic()` (`:288`) từng hoá đơn. Catalog Product nhỏ nên nạp trước được.
- Sửa: trước vòng lặp page: `products_by_sku = {p.sku: p for p in Product.objects.filter(sku__in=all_skus_in_page)}`, `customers_by_code/phone` tương tự; chỉ `bulk_create` phần thiếu. Bounded, chạy ở worker `kiotviet` có `rate_limit`/`soft_time_limit` nên không P1.

### 2.5 — P2 — WS fanout vẫn ở "giai đoạn 1": mỗi event `group_send` 2 lần, group chung vẫn nhận toàn bộ traffic
- Vị trí: `apps/orders/application/services/order_event_publisher.py:244-246` (`group_send(order_channel_group)` **và** `group_send(_broad_copy_group())`); `_broad_copy_group()` `:62-65` trả `CRM_GROUP` khi `WS_CHANNEL_FANOUT_PHASE < 2`; `docker-compose.prod.yml:115` default `1`; env prod hiện `WS_CHANNEL_FANOUT_PHASE=1` (`docker exec desi-web env`).
- Mô tả: hạ tầng C5 đã xong nhưng chưa bật → tốn **gấp đôi** publish tới Redis channel layer (mỗi đơn ingest = 2 group_send, bản sao vào `crm_updates` toàn cục), consumer khử trùng lặp bằng `dedup_id` (`consumers.py:16-20`), sale scope "channels" vẫn nhận kênh ngoài phạm vi rồi vứt.
- Sửa: xác nhận mọi web đã chạy consumer join `CRM_BROAD_GROUP` → đặt `WS_CHANNEL_FANOUT_PHASE=2` trong `.env` prod + restart worker. Việc vận hành, 0 dòng code.

### 2.6 — P2 — `LeadAssignmentService.transfer`: 1 INSERT journey event/đơn trong vòng lặp, trong lock
- Vị trí: `apps/orders/application/services/lead_assignment_service.py:234-240` `for order in Order.objects.filter(id__in=ids): OrderServiceHelpers.log_staff_change(...)` → `_create_journey_event` `order_service_helpers.py:616-626` `OrderJourneyEvent.objects.create` mỗi đơn, sau khi đã `select_for_update` + `update()` hàng loạt (`:226-233`).
- Mô tả: `count` đơn = `count` INSERT tuần tự trong cùng transaction giữ lock các dòng `orders` → lock kéo dài theo count. (Không đo được: script gọi sai tên sheet — `ValueError('Sheet không hợp lệ: landing')`; kết luận theo đọc code.)
- Sửa: dựng list `OrderJourneyEvent(...)` rồi `bulk_create` 1 lần; vòng `Order.objects.filter(id__in=ids)` bỏ được vì chỉ cần `order_id`.

### 2.7 — P2 — `auto_sync_invoice_full`: 1 HTTP KiotViet + 1 query OrderItem/dòng hoá đơn, tuần tự, quét 7 ngày
- Vị trí: `apps/orders/infrastructure/tasks/order_tasks_impl/auto_sync_invoice_full.py:61-76` (mọi đơn có `invoice_code` cập nhật 7 ngày, `iterator(chunk_size=100)`), `:76-79` `get_invoice_by_code` tuần tự từng đơn, `:242-243` → `_apply_invoice_detail_price` `:335` `OrderItem.objects.filter(order=order, sku=...).first()` **mỗi dòng hoá đơn**, `:246` `order.save()` + `:251` publish WS mỗi đơn đổi.
- Mô tả: vài nghìn đơn/tuần/kênh = vài nghìn HTTP tuần tự + 2-5 query/đơn mỗi lần chạy. `docs/go-offload-plan.md:68` ghi task cron 60s và **đã lên kế hoạch port sang Go** (`kv_auto_sync_full.go`); `task_id="go"` trong code gợi ý Go đang gọi. Không thấy trong beat `settings_parts/celery.py`.
- Sửa: nếu đã port Go → xoá/đánh dấu deprecated path Python; nếu chưa → `prefetch_related("items")` thay filter/dòng, bounded concurrency HTTP (như `_prefetch_dispatch_verdicts`), quét theo watermark `updated_at` từ lần chạy trước thay vì 7 ngày.

### 2.8 — P3 — `ChannelTypeSerializer.get_channel_count`: `.count()` mỗi loại kênh
- `apps/channels_core/transport/serializers/channel_serializers_impl.py:101-110` `obj.channels.filter(deleted_at__isnull=True).count()`. Đo: 12 loại → 13 query. View cache 30s (`channel_views_impl.py:200-206`) nên P3. Sửa: `annotate(channel_count=Count("channels", filter=Q(channels__deleted_at__isnull=True)))`.

### 2.9 — P3 — `build_order_created_row` đường 1 đơn: 8 query/event ingest
- `order_event_publisher.py:192-205`: `build_duplicate_map([order])` + serializer lazy-load `channel`, `kiotviet_channel`, `items`, `recall_entries`, `assessment__needs`… Đo: **8 query/đơn**, 29-74ms. Chấp nhận được cho ingest lẻ; call site tạo đơn đã có instance → truyền `Order` nạp `select_related("channel","kiotviet_channel","customer","staff","assessment")` là về 3-4 query.

### 2.10 — P3 — Audit middleware ghi 1 dòng `audit_logs` cho MỌI request API, kể cả list poll
- `apps/audit/middleware_impl.py:142-143` `SKIP_PATHS` chỉ bỏ health/static/ws; `:381-389` GET → `READ`, POST list `/api/orders` → `CREATE`/`OTHER`. Mỗi lần FE refetch list = 1 INSERT (đã batch qua `dispatch/api.py:60-86`, có purge). Bảng 2.19M dòng (comment `audit_views_impl.py:236`). Sửa: bỏ qua GET/POST-list 2xx (hoặc sample 1/20) — giảm ~80% write vào `audit_logs`, 6 index composite bớt bloat.

### 2.11 — P3 — icontains còn sót ở bảng vừa/lớn
- `apps/inventory/application/services/stock_query_service.py:26` `product__sku/name__icontains`; `dispatch_history_service.py:174,212`; `return_history_service.py:91-92` — `products` nhỏ, `pending_scans.code` không trgm → seq-scan cửa sổ nhỏ, chấp nhận.
- `apps/warranty/claim_repository.py:32-36` `order__phone/name__icontains` join `orders` — claims nhỏ, planner đi từ claims; theo dõi khi claims lớn.
- `apps/audit/transport/views/audit_views_impl.py:229-231` — code tự ghi nhận seq-scan trong cửa sổ ≤31 ngày; trgm trên `entity_id` khi cần.

---

## 3. Các trục kiểm tra khác

### 3.1 Cache
- Key/TTL/invalidation: giữ đánh giá cũ (tốt). `ORDERS_LIST_COUNT_CACHE_TTL` bật ở cả 3 compose (120s); `_enriched_rows_cached` (contact list) dùng đúng khuôn `_filters_cache_key`; **chưa có single-flight** cho contact list rows (`contact_list_service.py:314-321`) — stampede nhẹ khi 10 tab sale cùng mở đầu ca sau khi cache hết hạn (pipeline DISTINCT ON 92 ngày). Đề xuất bọc `single_flight(f"{cache_key}:lock")` như sheet3.
- `cache.get` trong loop: chỉ ở vòng chờ single-flight (`sheet3_view.py:224`, `callcenter/services.py:85`) — đúng mục đích.
- Dashboard/report: **không cache** (§2.1).

### 3.2 Celery
- `.delay()` trong loop: `cnv_loyalty/tasks.py:180` có `cap_per_entity`; `kiotviet/tasks.py:136,516` fan-out theo kênh (bounded). OK.
- Task dài không chunk: `rebuild_segment_snapshot` `_BATCH=2000` (`segment_snapshot_service.py:18,65`); retention/reminder có `[:batch_size]`; `auto_sync_invoice_full` (§2.7) không trần.
- Retry: 88 chỗ `soft_time_limit`; 16/37 file task không có `autoretry_for`/`max_retries` (§1) — nhóm idempotent (inventory ensure partition, hr purge, exports) nên thêm `autoretry_for=(OperationalError,)`.
- Queue: đã tách `kiotviet/orders/lark_noti/slow/exports/foxia/foxia_push` (`settings_parts/celery.py:35-51`), container riêng từng queue. Tốt.

### 3.3 Middleware / mỗi request
- `MIDDLEWARE` (`base.py:89-110`): RequestId, HttpMetrics (in-memory), WhiteNoise, CORS, Session, CSRF, Auth, Messages, **AuditLogMiddleware**, XFrame. Không có debug toolbar/silk. `DJANGO_SQL_LOG_ENABLE` → `SQL_LOG_ENABLED` (`base.py:1044`) default `0`; env prod không bật.
- Query mỗi request: JWT auth 1 query `users` (`jwt.py:46-52`); RBAC 2 Redis GET (`rbac.py:187-190`), 0 SQL khi ấm; audit middleware 0 SQL (buffer). `require_permission`/`require_section` không query thêm. Nền = 1 SQL + 2 Redis/request, sạch.
- GZip chỉ bật khi env (`base.py:128-131`) — nginx nén. Tốt.

### 3.4 Query nặng / index
- `orders` search: `phone/shipping/invoice/order_code__contains` + `name__icontains` có functional trgm `Upper` (`repositories.py:519-537`). OK.
- `.distinct()` (53 chỗ): mẫu ở orders/contact list là `DISTINCT ON` có chủ đích hoặc sau join M2M; không thấy `distinct()` thừa trên hot path.
- `.only/.defer`: list orders dùng `lean_list_columns` (`repositories.py:263`); `list_customer_orders`/`history` nạp full 200 đơn (bounded).

### 3.5 Vòng lặp `.save()` (18 chỗ, đã đọc): còn lại là cập nhật field-by-field trên 1 instance hoặc lô nhỏ (`product_group_service.py:91`, `pancake/views.py:369`) — không đáng sửa.

### 3.6 Quan sát CPU (không phải query)
- `landing-timing rows=40 … serialize=249ms` (6ms/row) so với `list-timing rows=40 serialize=56ms` (1.4ms/row) trên cùng `OrderListSerializer` — lần đo đầu của process, có thể warm-up DRF; đo lại bằng log `landing-timing` trên prod trước khi kết luận. Nếu ổn định ~6ms/row thì `_build_crm_map`/`OrderCRMListSerializer` là chỗ xem.

---

## 4. Nghi ngờ chưa xác minh
1. `process_auto_sync_invoice_full` Python còn được gọi hay đã hoàn toàn thay bằng Go (`docs/go-offload-plan.md:68`) — không thấy trong beat, không thấy repo Go trong worktree.
2. `landing` serialize 6ms/row (§3.6).
3. `warranty/claim_repository.py:32-36` icontains join `orders` — cần EXPLAIN trên prod với số claims thật.
4. Contact list stampede sau hết hạn cache (§3.1) — chưa đo đồng thời.

## 5. Bảng tóm tắt

| # | Mức | Vị trí | Vấn đề | Bằng chứng |
|---|---|---|---|---|
| 1 | P1 | dashboard_report_views.py:42-95 | 11 aggregate/req không cache/single-flight | grep cache = 0 |
| 2 | P2 | users/serializers.py:196-215 | RBAC resolve ×2/user, cold = 5 SQL/user | đo 54→104 q |
| 3 | P2 | entry_service.py:52 → operation_engine.py:37 | 3 q + 1 tx/khách, tới 2000/tick | đo 33→63 q |
| 4 | P2 | _import_service_upsert_mixin.py:47-97,194-208 | Product/Customer lookup từng dòng/hoá đơn | đọc code |
| 5 | P2 | order_event_publisher.py:244-246 + env | WS phase 1: 2 group_send/event, group chung nhận hết | env `WS_CHANNEL_FANOUT_PHASE=1` |
| 6 | P2 | lead_assignment_service.py:234-240 | INSERT journey event/đơn trong lock | đọc code |
| 7 | P2 | auto_sync_invoice_full.py:76-79,335 | HTTP tuần tự + OrderItem filter/dòng, quét 7 ngày | đọc code |
| 8 | P3 | channel_serializers_impl.py:101-110 | count()/loại kênh | đo 13 q/12 loại |
| 9 | P3 | order_event_publisher.py:192-205 | 8 q/event ingest | đo |
| 10 | P3 | audit/middleware_impl.py:142,381 | audit row cho mọi list poll | đọc code |
| 11 | P3 | stock_query_service.py:26, claim_repository.py:32, audit_views_impl.py:229 | icontains seq-scan bảng vừa | đọc code |
| — | mở cũ | resale history no LIMIT; inventory export 20k RAM; journey-events no page; contact list slice Python; contact list icontains; 16 task thiếu retry | §1 |

## 6. Top 5 việc nên làm
1. **Cache + single-flight cho `dashboard_summary` / `reports/*`** (60-120s, key theo ngày+scope) — dùng nguyên khuôn `sheet3_view.py:213`. Rẻ nhất, cắt trực tiếp workload đọc nặng nhất còn lại.
2. **Bật `WS_CHANNEL_FANOUT_PHASE=2` trên prod** (sau khi xác nhận mọi web đã chạy consumer mới) — giảm 50% publish channel-layer và cắt fanout kênh ngoài scope, 0 dòng code.
3. **Bulk hoá contact-flow sweep** (`bulk_create` state + activity, 1 transaction/lô) và **bulk_create journey event** trong `LeadAssignmentService.transfer` — cùng một mẫu sửa.
4. **RBAC resolve theo lô cho admin users list** (map trong context) + sửa `test_users_list_queries.py` để bỏ patch, đo thật cold cache.
5. **Nạp trước Product/Customer theo page trong KiotViet import** (`_import_service_upsert_mixin`) — giảm ~10× query/page ở worker `kiotviet`, sync nhanh hơn trong cửa sổ `soft_time_limit`.
