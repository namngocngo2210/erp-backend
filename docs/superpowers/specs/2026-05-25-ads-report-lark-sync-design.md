# Thiết kế: Cron đồng bộ Báo cáo Ads sang Lark Base

- **Ngày**: 2026-05-25
- **Branch**: fix-landing-orders-source-not-null (sẽ tạo branch mới khi triển khai)
- **Trạng thái**: Đã chốt thiết kế, chờ lập kế hoạch triển khai

## 1. Mục tiêu

Tự động đẩy số liệu của **trang Báo cáo Ads** sang **Lark Base** theo từng ngày, từng kênh, gồm:

- **Doanh thu**: doanh thu của **tất cả đơn đã chốt** (KHÔNG phải doanh thu giao thành công).
- **Chi phí**: chi phí Ads đã nhân hệ số **×1.1**.
- **Số liệu theo từng sản phẩm**: số đơn đã chốt và doanh thu theo sản phẩm.

## 2. Bối cảnh codebase (tiền lệ tái sử dụng)

Migration sang Golang gần như đã hoàn tất. Đã có tiền lệ gần như y hệt:

- Handler `kv.revenue_sync` — `go-services/internal/service/worker/business/handlers/kv_revenue_sync.go`:
  chạy native trong Go, tổng hợp doanh thu theo ngày → đẩy Lark Bitable, **upsert** qua bảng
  mapping `kiotviet_revenue_lark_records` (vì Bitable không có khóa tự nhiên).
- Lark OpenAPI client (tenant token, BatchCreate/BatchUpdate, chunk 500, auto-refresh token):
  `go-services/internal/shared/gateway/lark/openapi.go`.
- Cron: `task-cron` (robfig/cron v3) → dedup `go_cron_runs` → enqueue `jobs` → `task-worker` dispatch
  theo reg map ở `go-services/internal/service/worker/module.go`.

Logic tổng hợp gốc (tham chiếu để dịch sang SQL Go): `backend/apps/orders/_repositories_reports.py`.

## 3. Kiến trúc (100% Golang)

```
task-cron (@every 30m, queue "orders")
   │  RecordFire dedup (go_cron_runs) → Enqueue job "ads.report_lark_sync"
   ▼
task-worker  ──►  AdsReportLarkSyncNative(gormDB, larkOpenC)
                     │  window = 7 ngày gần nhất (gồm hôm nay)
                     ├─ Query A: aggregate ngày×kênh    (doanh thu, số đơn chốt, chi phí ×1.1)
                     ├─ Query B: aggregate ngày×kênh×SP  (số đơn chốt, doanh thu SP)
                     └─ Upsert 2 bảng Lark qua lark.OpenAPIClient
                          dùng 2 bảng mapping record_id
```

Toàn bộ logic tổng hợp viết bằng **SQL thuần trong Go**, không gọi Django.

### Quyết định cấu hình (đã chốt với business)

- Tần suất: **@every 30m** (khớp auto-refresh 30' của trang báo cáo).
- Cửa sổ đồng bộ: **7 ngày gần nhất (gồm hôm nay)** + **upsert** — bắt đơn chốt muộn, chi phí FB chốt lại.
- Cấu trúc Lark: **2 bảng riêng** theo đúng grain.

## 4. Schema 2 bảng Lark Base

### Bảng A — `Ads_Daily_Channel` (grain: ngày × kênh)

| Field Lark | Kiểu | Nguồn |
|---|---|---|
| `record_key` | Text | `YYYY-MM-DD\|channel_id` |
| `Ngày` | Date | `TruncDate(created_at)` |
| `Kênh` | Text | `channel.name` |
| `Mã kênh` | Text | `channel.code` |
| `Doanh thu` | Number | `SUM(total_after_discount)` đơn đã chốt (KHÔNG phải giao thành công) |
| `Số đơn chốt` | Number | `COUNT(id)` đơn đã chốt |
| `Chi phí` | Number | `SUM(spend) × 1.1` |
| `Tỷ lệ CP/DT (%)` | Number | `Chi phí / Doanh thu × 100` (optional, tiện dashboard) |

### Bảng B — `Ads_Daily_Product` (grain: ngày × kênh × sản phẩm)

| Field Lark | Kiểu | Nguồn |
|---|---|---|
| `record_key` | Text | `YYYY-MM-DD\|channel_id\|product_id` |
| `Ngày` | Date | `TruncDate(created_at)` |
| `Kênh` | Text | `channel.name` |
| `Sản phẩm` | Text | `product.name` |
| `SKU` | Text | `product.sku` |
| `Số đơn chốt` | Number | `COUNT(DISTINCT order_id)` |
| `Doanh thu SP` | Number | `SUM(order_items.line_total)` |

Ghi chú:
- Bảng B lưu **toàn bộ** sản phẩm (không giới hạn top 5 như trên web).
- Cột `Ngày` lưu timestamp ms (giống `kv_revenue_sync`: noon để tránh lệch ngày do TZ) để Lark nhận diện Date.

## 5. Logic tổng hợp (dịch từ Django sang SQL Go)

Điều kiện lọc đơn (theo `_ads_landing_orders_queryset` trong `_repositories_reports.py`, bản master mới nhất):

- `deleted_at IS NULL`
- **Mọi đơn có nguồn** (KHÔNG chỉ landing): `order_source IS NOT NULL AND order_source <> ''`
- Loại đơn hủy: `status <> 6`
- Loại đơn hủy KiotViet: `invoice_status IS DISTINCT FROM 2` (NULL vẫn tính — Django `.exclude(invoice_status=2)`)
- Loại kênh sale-fb: `channel_id <> 24` (`SALE_FB_CHANNEL_IDS`)
- Loại branch ẩn: `branch_id NOT IN ('1000000139')` (`EXCLUDED_BRANCH_IDS` — `_exclude_hidden_branches`)
- Đơn **đã chốt**: `order_assessments.sales_status_code IN ('closed','closed_express','closed_scheduled_date','created_order')`

Cron không có user → tương đương admin scope: KHÔNG lọc thêm theo channel khả kiến (lấy mọi kênh non-deleted, trừ sale-fb).

Doanh thu (KHÔNG phải giao thành công): `SUM(orders.total_after_discount)` trên tập đơn đã chốt
(không lọc thêm `status = 3 DELIVERED_SUCCESS`).

Chi phí: `SUM(facebook_ad_account_daily_spends.spend) × 1.1`, map `ad_account → kênh` qua
`channels.fb_ads_accounts` (ArrayField). Nếu 1 ad_account map nhiều kênh thì chia đều spend (giống logic gốc).

Sản phẩm: join `order_items` (`line_total`, `product_id`) với tập đơn đã chốt; `COUNT(DISTINCT order_id)`,
`SUM(line_total)` theo ngày × kênh × sản phẩm.

**Timezone**: Django `TIME_ZONE = "Asia/Bangkok"` (UTC+7), `USE_TZ=True` → `created_at` lưu UTC.
TruncDate quy về Asia/Bangkok. SQL Go dùng explicit `(created_at AT TIME ZONE 'Asia/Bangkok')::date`
để khớp tuyệt đối với trang web. Chi phí dùng cột `date` của `facebook_ad_account_daily_spends` (đã là DateField).

## 6. Cơ chế upsert & bảng mapping

Bitable không có khóa tự nhiên → 2 bảng mapping Postgres (giống `kiotviet_revenue_lark_records`):

```sql
CREATE TABLE ads_daily_channel_lark_records (
    record_key     TEXT PRIMARY KEY,        -- 'YYYY-MM-DD|channel_id'
    lark_record_id TEXT NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE ads_daily_product_lark_records (
    record_key     TEXT PRIMARY KEY,        -- 'YYYY-MM-DD|channel_id|product_id'
    lark_record_id TEXT NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Luồng upsert mỗi lần chạy (cho từng bảng):

1. Aggregate trong cửa sổ 7 ngày → rows kèm `record_key`.
2. Load map `record_key → lark_record_id` từ Postgres cho các key đó.
3. Phân loại: key đã có → `BatchUpdateRecords`; key chưa có → `BatchCreateRecords`
   (Lark trả record_id mới → ghi lại bảng mapping).
4. Chunk 500/req (client hỗ trợ sẵn).
5. **Zero-out**: key tồn tại trong mapping nhưng aggregate hiện tại không còn (đơn hủy hết)
   → update record về 0, **không xóa** record Lark (tránh phá link/dashboard).

## 7. Idempotency, lỗi & vận hành

- Dedup cron: `go_cron_runs` UNIQUE `(cron_name, fired_at)` — sẵn có.
- Idempotent: upsert theo `record_key`, chạy lại nhiều lần ra cùng kết quả.
- Retry: hàng đợi `jobs` có `attempts`/`RequeueStuckJobs` — handler trả error là được retry.
- Ghi mapping `record_key → lark_record_id` **sau khi** Lark trả record_id, trong transaction (tránh mồ côi).
- Lark token hết hạn: client auto-refresh + retry 1 lần (99991663/99991668) — sẵn có.
- Metrics/log: log số record create/update/zero-out mỗi bảng mỗi lần chạy, theo chuẩn metrics hiện có.

## 8. Testing

- Unit test SQL aggregate: seed orders/order_items/assessments/channels/fb_spend → assert doanh thu
  (đơn chốt, không phải giao thành công), chi phí ×1.1, đếm đơn SP. Khẳng định bằng số (vd 50000×1.1=55000).
- Unit test phân loại upsert: mock mapping → đúng create / update / zero-out.
- Unit test Lark client: theo mẫu `openapi_test.go` (httptest).
- Build/test chạy qua **Docker** (máy không có Go local).

## 9. File dự kiến thay đổi / tạo mới (đều trong `go-services`)

| Việc | File |
|---|---|
| Handler mới | `internal/service/worker/business/handlers/ads_report_lark_sync.go` (mới) |
| Đăng ký handler | `internal/service/worker/module.go` (reg map) |
| Cron entry `@every 30m` | `config/task-cron.yaml` |
| Provide 2 table_id Lark (env) | `module.go` — `LARK_BITABLE_ADS_CHANNEL_TABLE_ID`, `LARK_BITABLE_ADS_PRODUCT_TABLE_ID` |
| 2 bảng mapping | migration mới |
| sqlc query / raw SQL aggregate | `db/queries/` hoặc raw trong handler |

## 10. Điểm cần xác minh khi vào triển khai

- GORM model cho bảng `channels` + cột `fb_ads_accounts` (ArrayField) đã có trong `dbmodel` chưa
  (Go cần đọc để map ad_account → kênh); nếu chưa, bổ sung.
- Tên model/bảng chính xác cho `orders`, `order_items`, `order_assessments`, `products`,
  `facebook_ad_account_daily_spends` trong `dbmodel`.
- Cần tạo sẵn 2 table trên Lark Base và lấy `table_id` để cấu hình env.
