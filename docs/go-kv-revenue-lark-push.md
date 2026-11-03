# Go KiotViet Revenue → Lark Bitable push

## Bối cảnh / Vấn đề

Cron `kv.revenue_sync` (`go-services/config/task-cron.yaml`, `@every 60s`, `time_range: thismonth`)
được offload sang Go worker (`KVRevenueSyncNative`). Handler Go **chỉ upsert vào
`kiotviet_daily_revenues`** và **cố tình bỏ bước push Lark Bitable** (comment trong
`kv_revenue_sync.go`: _"Lark Bitable push intentionally skipped … can be reintroduced
later"_).

Hệ quả thực tế:
- DB có đủ ngày (cron kéo `thismonth` mỗi 60s). ✅
- Lark Bitable không còn được cập nhật từ khi offload → khuyết ngày. ❌

Logic push phía Python (`lark_revenue_sync.py` + `lark_bitable_client.py`) vẫn đúng
nhưng **không còn ai gọi**, vì cron đang chạy là Go.

## Quyết định

- **Port toàn bộ bước push sang Go** (không gọi ngược Django), đúng định hướng Pattern A
  (native Go gateway) của worker.
- **Backfill toàn bộ ngày thiếu**: cơ chế tự lành (self-heal) — mỗi lần chạy, ngoài các
  ngày `thismonth` vừa kéo, handler còn quét mọi `date` trong `kiotviet_daily_revenues`
  **chưa** có bản ghi trong `kiotviet_revenue_lark_records` và push luôn. Lần chạy đầu sau
  khi deploy sẽ lấp đầy quá khứ; các lần sau truy vấn "missing" trả về rỗng (rẻ).

## Thiết kế

### 1. Lark Open API client (Go) — `internal/shared/gateway/lark/openapi.go`
Port `lark_bitable_client.py`:
- `tenant_access_token` (server-to-server) + cache in-process (TTL = expire − 300s),
  mutex bảo vệ, tự refresh.
- `BatchCreateRecords` / `BatchUpdateRecords`, chunk theo `_BATCH_LIMIT = 500`.
- Retry 1 lần khi token hết hạn (code `99991663` / `99991668`) — invalidate cache + thử lại.
- Cấu hình qua env (đồng bộ tên với Python): `LARK_OPEN_BASE_URL`, `LARK_APP_ID`,
  `LARK_APP_SECRET`, `LARK_BITABLE_APP_TOKEN`, `LARK_BITABLE_REVENUE_TABLE_ID`.

### 2. Model — `internal/shared/dbmodel/dbmodel.go`
Thêm `KiotVietRevenueLarkRecord` map bảng `kiotviet_revenue_lark_records`
(`date` unique, `lark_record_id`, timestamps). Không cần migration (Django đã tạo bảng).

### 3. Handler — `kv_revenue_sync.go`
Sau khi upsert xong các target:
1. Gom `touchedDates` (ngày vừa upsert) ∪ `missingDates` (ngày trong DB chưa có lark record).
2. Với mỗi date: aggregate `SUM(total_amount, discount, revenue, total_cost, gross_profit)`
   qua **mọi** channel + branch (`GROUP BY date`) — khớp `_fetch_aggregates_for_dates`.
3. Tra `kiotviet_revenue_lark_records`: có record_id → gom `toUpdate`; chưa có → `toCreate`.
4. `BatchCreate` các record mới → lưu mapping `date → record_id`; `BatchUpdate` phần còn lại.
5. Push là **best-effort**: lỗi Lark không làm fail bước kéo DB (log + đưa vào summary).
   Nếu chưa cấu hình Lark (thiếu app_token/table_id) → skip lặng.

### 4. Wiring — `module.go`
Provider mới cho `*lark.OpenAPIClient` (đọc env), inject vào `KVRevenueSyncNative`.

## Field map (giữ nguyên cột Bitable như Python)
`Date, Total Amount, Discount, Revenue, Total Cost, Gross Profit`.
`Date` = ms-since-epoch tại 12:00 UTC (ổn định qua timezone).

## Test
- `openapi_test.go`: token cache, chunk theo batch limit, retry token-expired (httptest).
- Handler: aggregate-by-date + phân loại create/update (mock client + sqlite/gorm hoặc DI).

## Sprint
- **Sprint 1**: openapi.go + model + unit test client. ✅
  - `lark/openapi.go` (tenant token cache + retry, batch create/update chunk 500).
  - `dbmodel.KiotVietRevenueLarkRecord`.
  - `lark/openapi_test.go`: Configured, token caching, chunking, token-expired retry,
    non-retriable error surface — 5/5 PASS.
- **Sprint 2**: ghép push + self-heal backfill vào handler + wiring. ✅
  - `kv_revenue_sync.go`: `pushRevenueToLark` (touched ∪ missing dates → aggregate by
    date → create/update + lưu mapping), best-effort.
  - `module.go`: provider `*lark.OpenAPIClient` từ env, inject vào handler.
- **Sprint 3**: build + vet + test. ✅
  - `go build ./...`, `go vet ./...`, `go test ./internal/shared/gateway/lark/...`
    chạy trên worktree sạch (golang:1.25 trong Docker) — tất cả xanh.

## Ghi chú vận hành (sau khi merge)
1. Đảm bảo task-worker có các biến môi trường `LARK_APP_ID`, `LARK_APP_SECRET`,
   `LARK_BITABLE_APP_TOKEN`, `LARK_BITABLE_REVENUE_TABLE_ID`, `LARK_OPEN_BASE_URL`
   (dùng chung tên với backend Django). Thiếu → push skip lặng (`reason=not_configured`).
2. Lần chạy cron `kv.revenue_sync` đầu tiên sau deploy sẽ tự **backfill** mọi ngày trong
   `kiotviet_daily_revenues` chưa có trong `kiotviet_revenue_lark_records`.
3. Push là best-effort — lỗi Lark KHÔNG làm fail bước kéo DB; chi tiết nằm ở
   `summary.lark` (created/updated/create_error/update_error).

## ⚠️ Lưu ý ngoài phạm vi (không thuộc thay đổi này)
Khi triển khai phát hiện working tree có một refactor dang dở (CHƯA commit) ở
`handlers/kiotviet_native.go` (~448 dòng) + `handlers/util.go` — chuyển sweep sang
fan-out child job (reenable-kv-auto-sync Sprint 2). Bản WIP này **không build được**
(`undefined: rate`/`fanOutSweep`). Đã **không commit** 2 file này; chúng được giữ
nguyên trong working tree để chủ nhân tự hoàn thiện.
