# Audit chi tiết — Performance backend (2026-08-19)

> Báo cáo gốc của luồng audit performance. Bản tổng hợp: `../audit-2026-08-19-full-stack.md`.
> Mức: **P0** = đang/sắp gây sự cố · **P1** = chi phí lớn ở hot path · **P2** = đáng sửa · **P3** = ghi nhận.
> Nhận xét chung: codebase đã được tối ưu rất kỹ (batch map, defer, Exists, trigram, WS
> payload self-contained, audit batching). Phát hiện dưới đây là các đường CÒN SÓT.

## 1. N+1 queries

### P0 — `transfer_landing_orders`: N+1 nhân 8 trên đường REST-fanout
`orders/transport/views/order_views_impl/order_action_view.py:347-409` — vòng lặp tới
50 đơn, mỗi vòng `build_order_created_row(order)` (`order_event_publisher.py:107-136`)
chạy `OrderListSerializer(order)` **không có context batch** → mỗi đơn: customer, staff,
items, assessment (fallback get_crm), recall, recall_entries.all(),
fulfillment_warehouse, và `build_duplicate_map([order])` (1 query OrderItem/đơn — hàm
thiết kế cho CẢ TRANG bị gọi 1-phần-tử). ⇒ ~8 query × 50 ≈ **400 query đồng bộ trong
một POST** + 50 `group_send`. Hạ tầng `_build_order_list_context` tồn tại để tránh đúng
điều này — đường này không dùng. *(→ ĐANG LÀM: nhánh perf/transfer-batch-row-context.)*

### P1 — Resale tracking: `item.product` chưa select_related
`customers/customer_resale_service.py:273` — items nạp qua `prefetch_related("items")`
không kèm product ở cả `:116` và `:256`. OrderItem có sku rỗng → 1 query/dòng hàng.
Kèm: `_resale_order_history_by_customer` (:249-262) nạp **toàn bộ** đơn của khách,
không LIMIT.

### P1 — KiotViet push payload: N+1 OrderItem.product
`kiotviet/infrastructure/services/invoice_mutations_push.py:471-478` —
`OrderItem.objects.filter(order=order)` không select_related("product"), chạm
item.product 2 lần/dòng. Chạy trong worker soft_time_limit=180.

### P1 — `dispatch_orders_batch`: load từng Order trong loop
`orders/application/services/order_service_commands.py:803-811` —
`.filter(id=oid).first()` + prefetch items MỖI đơn. Lô 50 kiện = 100 round-trip.

### P1 — contact-flow entry sweep: transaction + query danh mục lặp theo khách
`contact_flow/application/services/entry_service.py:39-42` → `operation_engine.py:32-59`:
mỗi khách 1 atomic + select_for_update + `OperationStage.objects.filter(is_start=True,
is_active=True).first()` (hằng số hệ thống tra lại mỗi vòng, :41) + 2 INSERT ≈ 5
query/khách, beat mỗi 5'. Không có trần rows/tick (khác floating pool
SWEEP_MAX_ROWS_PER_TICK=2000).

### P3 — RBAC resolver query RolePermission 2 lần
`users/rbac.py:132-136` và `:147-149` — gộp 1 values_list là bỏ 1 query mỗi cache-miss.

### Ghi chú test N+1 export đang đỏ (assert 9 / thực tế 10)
`orders/tests/test_orders_export_definition.py:585-625` — con số 9 hard-code **gồm cả**
query RBAC resolver. Phần chống N+1 thật vẫn đúng, không phụ thuộc N. Cái đổi là
`_compute()` trong rbac.py giờ 6 query (thêm **RoleFieldGrant** :172, commit `9ba895d5`).
**Kết luận: test đỏ vì assertion đếm chi phí RBAC hằng số, không phải export tái phát
N+1. Sửa đúng: warm RBAC cache trước block đo — KHÔNG bump 9→10** (bump chỉ dời bẫy).

## 2. Caching (Redis)

### Hiện trạng key/TTL/invalidation (rút gọn)
- Orders list count/chips/sheet-stats: `orders:*:<md5(filters)>`, TTL
  ORDERS_LIST_COUNT_CACHE_TTL (prod 120s qua compose; **code default 0**), TTL-only.
- RBAC eff perms: versioned bust qua post_save/post_delete — thiết kế tốt nhất repo.
- Channels list: versioned bust 30s. Customer report: 600s + warm task. Resale
  facets/count 300/60s. Loyal ranking 300s. KiotViet cancel verdict 300s. Token
  Lark/KV/CNV theo expires_in.
- Hạ tầng: Redis cache riêng, `InstrumentedRedisCache` fail-soft socket_timeout 0.5s —
  Redis chết chỉ chậm, không sập. Rất tốt.

### Thiếu cache — ưu tiên
- **P0 — `GET /api/sale/contacts` không cache gì** (`contact_list_view.py:102-120` →
  `contact_list_service.py:95-241`): mỗi request chạy lại toàn pipeline DISTINCT ON cửa
  sổ 92 ngày + 3 map + enrich Python. Trang Sale mở suốt ca. Cache `scoped_rows` theo
  khuôn `_filters_cache_key`.
- P1 — ORDERS_LIST_COUNT_CACHE_TTL default 0: staging/local không bật → mọi đo đạc
  trước prod lệch, mất prod-parity.
- P2 — Danh mục contact-flow stage/result không cache (`config_views.py:75,96`) — dùng
  khuôn versioned-bust channels_core.
- P2 — Sheet 3 temperature_counts cache-miss duyệt toàn tập Python
  (`sheet3_view.py:181-201`), nguy cơ stampede (không single-flight).

## 3. Celery / công việc đồng bộ trong request

### Đã làm đúng (không sửa)
Lark async + idempotency marker; KiotViet qua job + lock terminal-state + rate_limit +
soft_time_limit; audit batch bulk_create + autoretry; export queue riêng + rescue;
single-flight cho mọi sweep.

### P1 — HTTP KiotViet TUẦN TỰ trong request path (2 chỗ)
1. `order_service_commands.py:854` trong vòng lặp :803 — lô 50, cache miss → 50 lượt
   HTTP × timeout 3s = tới 150s/POST. Fail-closed có chủ đích nên không đẩy background —
   prefetch verdict theo lô hoặc song song bounded.
2. `shipping_lookup_view.py:207,247-255` — 1 HTTP/lần quét mã (hot path kho).

### P2 — Beat: task nặng nhất DB chạy giờ cao điểm
`celery.py:304-320` — `customers-rebuild-segment-snapshot` + `warm-report-cache`
`hour="*/2"` nổ 08/10/12/14/16h. Docstring task tự ghi: workload nặng thứ hai của DB
(~23% total_exec_time, 1 câu đọc 100,5 triệu dòng). `*/N` cũng đi ngược nguyên tắc
lệch-pha của chính file (:135-144). → giãn sang 1,3,5,7,9,13,17,21 hoặc tương tự.

### P2 — Task thiếu autoretry_for/max_retries
floating_pool_tasks, lead_assignment_tasks, lead_close_tasks, contact_flow/tasks,
foxia/tasks:47,90,98, cnv_loyalty/tasks:56-93. Idempotency nhóm này ổn sẵn → thêm
`autoretry_for=(OperationalError,)` an toàn.

### P3 — run_export.progress mỗi 1000 dòng = 2 query + 1 WS — chấp nhận được, đừng hạ
_PROGRESS_EVERY.

## 4. Pagination / export nạp vào RAM

### P0 — Contact list: phân trang bằng Python sau khi materialize TOÀN BỘ
`contact_list_service.py:156, 207, 232-233` — `_latest_order_rows` = list(DISTINCT ON,
KHÔNG LIMIT) cửa sổ 92 ngày (~500k orders) → nạp toàn bộ contact của quý vào Python để
trả 20 dòng; sort + slice Python. Export chạy pipeline **2 lần** (estimate:244-247 +
write:250-261); trần 20k chỉ chặn số dòng ghi file.
Ràng buộc thật: action_needed/interaction/customer_type/operation_bucket lọc Python
(:399-418, contact_list_operation.py:67-71) nên không đẩy LIMIT xuống SQL nếu không đổi
thiết kế. Tối thiểu: (a) cache scoped_rows, (b) đẩy operation_bucket xuống SQL — index
`op_state_due_idx` (contact_flow/models.py:96) đang không được query nào dùng.

### P1 — Export tồn kho: `list(qs[:20_001])` vào RAM worker
`inventory/exports_definitions.py:206` (comment tự thừa nhận); stock_views.py:143 chế độ
as_of tương tự (có guard warehouse_id/search :128-132).

### Đã ổn: export orders streaming iterator chunk 2000 + trần 50k; flow-history export
limit 5000 + cắt cảnh báo; audit export defer + trần 10k.

### P2 — List endpoint không phân trang
| Endpoint | Vị trí | Ghi chú |
|---|---|---|
| GET /api/orders/{id}/journey-events | contact_log_view.py:129-130 | toàn bộ timeline; purge mặc định **0 = tắt** (celery.py:379-388) → bảng chỉ tăng |
| GET /api/audit-logs/ | audit_views_impl.py:34,67 | ✅ ĐÃ XỬ LÝ (feat/audit-logs-pagination): limit/offset chuẩn repo, response {items,total,limit,offset}, default 100/max 500; kích hoạt luôn timestamp__gte/lte |
| milestone-snapshots, contact-flow config, RBAC catalog | | bounded, P3 |

## 5. WebSocket / Channels

Hiện trạng: **một group toàn cục `crm_updates`** (consumers.py:32); phân quyền không
chạm DB — payload tự chứa staff_id + channel_code, consumer so in-memory (:106-139);
field ẩn strip per-connection từ field_policy nạp lúc handshake. (Bản vá sự cố
2026-07-29, làm rất chuẩn.)

### P1 — Fanout O(clients) payload row đầy đủ, lọc ở phía nhận
Mỗi đơn ingest = 1 message × N connection; sale scope own kênh A vẫn nhận toàn bộ
traffic kênh B/C/D rồi vứt ở `_is_authorized`. Chưa có: group theo channel_code
(`crm_updates:<channel>` — hạ tầng sẵn, thay đổi cục bộ), gom lô publisher (transfer bắn
50 group_send rời), server-side visible-only (đang dồn hết cho FE).

### P3 — Tên group export ghép tay 2 nơi (consumers.py:37-43 ↔
exports/event_publisher:18-19) — đã comment, ghi nhận.

## 6. Query patterns xấu (bảng tổng)

| # | Mức | Vị trí | Vấn đề |
|---|---|---|---|
| 1 | P1 | order_service_commands.py:803-811 | filter(id=oid).first() trong for |
| 2 | P1 | entry_service.py:39-42 + operation_engine.py:41 | danh mục lặp mỗi vòng; 1 transaction/khách |
| 3 | P1 | order_action_view.py:395-409 | serialize + duplicate_map từng đơn |
| 4 | P1 | contact_list_service.py:232-233 | len() + slice Python thay COUNT/LIMIT |
| 5 | P2 | customer_resale_service.py:249-262 | lịch sử đơn toàn bộ, không LIMIT |
| 6 | P2 | users/rbac.py:132-149 | RolePermission query 2 lần |
| 7 | P2 | sheet3_view.py:184-199 | temperature_counts vòng lặp Python toàn tập |
| 8 | P2 | inventory/exports_definitions.py:206,216 | list(qs[:20001]) + count() 2 lần quét |
| 9 | P2 | floating_pool_service.py:131-139 | count() sau bulk_create (không có cách rẻ hơn) |
| 10 | P3 | customer_behavior_report.py:222-225 | 3 count() full-table — che bởi cache 600s + warm |
| 11 | P3 | return_note_service.py:61 | count()+2 sinh sequence — race + scan prefix |
| 12 | P3 | warranty_codes/batch_service.py:128 | cùng pattern count()+1 |

Bulk operations đã tốt (floating_pool 2 UPDATE hàng loạt, audit bulk_create,
segment_snapshot 5 bulk op, pending_scan budget/tick). `.save()` trong loop còn sót ở
pending_scan_service (6 chỗ), kiotviet upsert mixin, import_excel — đường
từng-bản-ghi-có-transaction-riêng, chấp nhận được nhưng đáng rà.

## 7. Index hints từ code (đối chiếu với audit DB)

- **orders** (`repositories.py:479-900`, models.py:539-671): độ phủ index rất cao.
  2 trục không thấy index: `branch_id` (lọc MỌI query list qua `_exclude_hidden_branches`
  :247-248), `vat` (bộ lọc mặc định 3 trang, cardinality thấp).
- **stock_items**: không có composite (warehouse, product, state); search icontains trên
  products.sku/name không có trigram.
- **audit_logs**: phủ đủ (6 composite theo -timestamp).
- **customers**: `contact_list_service.py:285-287` dùng full_name/phone `__icontains`
  trong khi resale đã cố tình đổi `__contains` + ghi tên index trigram cần thêm — đường
  contact list chưa theo → nhiều khả năng seq-scan (khớp phát hiện audit DB: functional
  Upper() trgm).
- **customer_operation_states**: `op_state_due_idx(due_at)` không query nào dùng —
  bucket tính Python → dấu hiệu nên đẩy bucket xuống SQL (mục 4).

## Tóm tắt ưu tiên

| # | Mức | Việc |
|---|---|---|
| 1 | P0 | transfer_landing_orders — context batch cả lô *(đang làm)* |
| 2 | P0 | Contact list — đẩy phân trang/bucket xuống SQL hoặc cache scoped_rows |
| 3 | P1 | dispatch_orders_batch — 1 query id__in + prefetch verdict theo lô |
| 4 | P1 | select_related("product") ở resale + KiotViet push |
| 5 | P1 | Group WS theo channel_code |
| 6 | P1 | contact-flow sweep — nhấc danh mục khỏi vòng lặp + trần rows/tick |
| 7 | P2 | Test N+1 export: warm RBAC ngoài block đo, không bump 9→10 |
| 8 | P2 | Dời rebuild-segment-snapshot khỏi 08–17h |
| 9 | P2 | Trigram GIN customers.full_name hoặc __contains như resale |
| 10 | P2 | Bật ORDERS_LIST_COUNT_CACHE_TTL ở staging/local (prod-parity) |
