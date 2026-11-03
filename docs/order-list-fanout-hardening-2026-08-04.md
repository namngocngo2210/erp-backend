# Dập bão POST /api/orders (list) do fanout `order.created` — 2026-08-04

## Đính chính chẩn đoán bão gate buổi sáng

Mổ bão 09:13 04/08 bằng Loki (`{container="desi-web"}` khung 09:12–09:16) cho
kết quả lật ngược chẩn đoán "burst tạo đơn" của các sự cố 31/07 & 03/08:

- 125 log `POST /api/orders` trong 4 phút là **fetch danh sách** (`path("")` →
  `list_orders`, `@api_view(["POST"])` nhận filter body, trả 200) — KHÔNG phải
  tạo đơn. Tạo tay là `POST /api/orders/create` (201, có create-timing); đơn
  thật vào qua API ingest.
- Cả 3.5h (08:00–11:30) chỉ có 5 dòng create-timing (total 249–906ms, validate
  35–51ms) — **không dòng nào trong bão** → tối ưu `OrderCreateSerializer` là
  bắn nhầm mục tiêu.
- Phân bố bão: **44 POST/10s (09:13:40) rồi 40 POST/10s (09:14:10)**, cách ~30s
  — đúng chu kỳ "gate queue 15s → 503 đồng loạt → retry/đợt event kế tiếp".

Chuỗi nhân quả thật: ingest tạo đơn dồn buổi sáng → WS `order.created` broadcast
→ mỗi tab coalesce trong cửa sổ jitter 0.5–2s rồi `loadOrders` = POST list nặng
(~1.5s: repo+counts 700ms, serialize 460ms) → N tab đồng nhịp đè 1.5 CPU → gate
503 → sóng sau. Jitter chỉ gom event TRONG một cửa sổ; burst kéo dài phát event
liên tục nên **mỗi tab flush mỗi ~1–2s suốt cơn bão**.

## Thay đổi trong MR này

1. **BE — cache `status_counts_for` theo hash bộ lọc** (cùng khuôn + cùng knob
   `ORDERS_LIST_COUNT_CACHE_TTL` với `_count_with_cache`, prod đang đặt 30):
   aggregate quét toàn tập chạy lại y hệt nhau mỗi lần load list; N tab cùng bộ
   lọc giờ chỉ tốn 1 lần quét mỗi TTL. Chip trạng thái trễ tối đa TTL giây —
   cùng mức trễ đã chấp nhận cho nhãn "tổng N đơn". Key gồm scope user
   (owner/channel nằm trong filters) nên không rò dữ liệu giữa scope.
2. **BE — log `list-timing`** trong `_run_orders_list` (cùng họ landing-timing/
   create-timing): bão toàn POST list nhưng view này chưa có số đo. Burst kế
   tiếp sẽ tự chỉ khúc phình (count/page/ctx/serialize/status_counts).
3. **FE — sàn nhịp refresh 10s/tab** (`CREATED_REFRESH_MIN_INTERVAL_MS`): flush
   kế tiếp cách flush trước tối thiểu 10s; event đến trong lúc chờ vẫn gom vào
   pending. Mỗi tab visible tối đa ~6 fetch/phút thay vì ~30–60.
4. **FE — backoff khi fetch fail** (5s nhân đôi tới 60s, kèm jitter): 503 giữa
   bão không còn bị thả trôi (user nhìn list cũ) và không retry dày tự khuếch
   đại bão; pending giữ nguyên để gộp với event mới, thành công thì reset.

Đã có sẵn từ MR !1122 (không làm lại ở đây): tab ẩn hoãn fetch tới khi visible,
gate 12→8, bypass token refresh, batch audit.

## Phạm vi cache theo trang (cập nhật chiều 04/08 — phủ TOÀN BỘ list đơn)

Quan sát sau deploy trưa: `landing-timing` dày hơn `list-timing` — landing/sheet
CSKH mới là list nóng nhất giờ thường. Đã phủ cùng khuôn cache (knob duy nhất
`ORDERS_LIST_COUNT_CACHE_TTL`, key hash bộ lọc gồm scope):

| Trang | Aggregate toàn tập | Cache |
|---|---|---|
| /api/orders (orders) | count + status_counts | ✔ (`_count_with_cache` + `status_counts_for`) |
| /api/orders/outside | count | ✔ |
| /api/orders/koc | count + status_counts | ✔ (dùng chung repo) |
| /api/orders/shipments | count | ✔ |
| /api/orders/landing (+sheet 1/2) | count + sales_status_counts + sheet_stats | ✔ (repo + `_sheet_stats_with_cache`) |
| /api/orders/sheet3 | count + sales_status_counts + temperature_counts (duyệt cả tập bằng Python!) | ✔ (key sau-chip gồm trục sales_status) |

## Đợt 3 — Serializer gọn cho list (chiều 04/08, cùng MR)

Audit FE toàn bộ cell/filter-mirror/realtime-merge/dialog của 6 trang list
(kết quả chi tiết trong phần mô tả MR) → cắt payload + CPU serialize:

- **`OrderListSerializer`** = OrderSerializer pop 22 field chỉ trang chi tiết đọc
  (`LIST_EXCLUDED_ORDER_FIELDS`: 6 field địa chỉ tách phần, financials chi tiết,
  shipping_fee, order_code/invoice_id/url_page/external_order_id, old_id, outside,
  channel_owner, latest_kiotviet_job_action, fulfillment_warehouse_name). Pop trong
  `__init__` vì DRF không cho subclass cắt Meta.fields của field đã declare; field
  pop rồi thoát cả vòng to_representation (CPU) lẫn schema OpenAPI.
- **`OrderCRMListSerializer`** = OrderCRMSerializer pop 11 field
  (`LIST_EXCLUDED_CRM_FIELDS`: events/updated_by/timestamps, 4 label không cell nào
  render, và 3 JSON blob ai_filled_fields/extra_fields/ai_open_insights — phần
  payload to nhất). **Sheet3 giữ crm ĐẦY ĐỦ** (`full_crm=True`) vì panel đánh giá
  dùng chung BlockCrmRating với trang chi tiết.
- **FE đổi kèm (bắt buộc, cùng deploy):** trang chi tiết trước đây ưu tiên row của
  list và CHỈ fetch GET /api/orders/{id} khi đơn không nằm trong list → giờ LUÔN
  fetch bản đầy đủ, ưu tiên standalone theo đúng id (App.tsx + routed-pages.tsx);
  `patchOrder` merge server response vào standaloneOrder để edit trên detail hiện
  ngay (use-orders-data trả `serverData`).
- Hợp đồng: regen `schema.yaml` (component `OrderList` mới) + `npm run gen:api`.
- Test: `test_order_list_serializer_trim.py` khóa 4 mặt (list cắt đúng, list giữ
  field bảng cần, crm gọn/sheet3 đầy đủ, detail không bị cắt).

Kỳ vọng: serialize giảm ~30-40% (70→48 field/row + crm 55→44 và bỏ JSON blob),
payload response nhỏ hơn đáng kể (ai_open_insights từng chiếm phần lớn size).
Đo thật bằng `list-timing`/`landing-timing` sau deploy.

**Đợt 4 — lean columns cho query list (đọc list-timing prod 14h 04/08):** mọi
request `rows=1` mà `page` vẫn 66–160ms lúc vãn → chi phí là SELECT full cột của
7 bảng join, không phải số row. `_with_related_crm(lean_list_columns=True)`
(chỉ 2 đường list_orders / list_orders_with_status_counts): bỏ join
`assessment__updated_by` + defer 3 JSON blob assessment và 10 cột text dài của
Order đã cắt khỏi OrderListSerializer. Sheet3/detail/history giữ nguyên (cần crm
đầy đủ; đụng field defer là N+1). Test khóa bằng bất biến "thêm đơn không tăng
số query một trang" (test_list_query_count_constant_per_page). Số đo trong test
env: page 21–23ms cho 1–4 rows.

## Tiến độ

- [x] Cache status_counts + count (orders/koc/outside/shipments) + test
- [x] Cache landing: sales_status_counts + count + sheet_stats + test
- [x] Cache sheet3: sales_status_counts + temperature_counts + count + test
      (15 test file status_counts pass; 33 test sheet3/lead-assignment hiện có pass)
- [x] list-timing log (orders/outside)
- [x] FE cooldown + backoff + 3 test mới (12/12 pass), tsc + eslint sạch
- [ ] Đọc list-timing/landing-timing sau burst kế tiếp → bước tối ưu kế
      (nghi: serialize 100 rows ~460ms — trang nhỏ hơn/serializer gọn cho list;
      count/badge/stats sẽ gần 0 nhờ cache)

## Số kỳ vọng cho bão sáng 05/08

- Loki đếm `POST /api/orders` khung burst: giảm từ ~44/10s xuống ~N_tab_visible/
  10s lần đầu, sau đó ≤ N/60s nhờ sàn 10s + tab ẩn đã hoãn từ !1122.
- `list-timing`: status_counts ≈ 0ms từ lần load thứ 2 cùng bộ lọc (cache 30s).
- Gate: `desi_http_gate_timeouts_total` không tăng vọt ở path_prefix
  `/api/orders`.
