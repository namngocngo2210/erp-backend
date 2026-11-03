# Ads Report → Lark Base Daily Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mỗi 30 phút, đẩy số liệu báo cáo ads (doanh thu đơn đã chốt, chi phí Ads ×1.1, số liệu theo từng sản phẩm) lên 2 bảng Lark Base, theo từng ngày × kênh, cho cửa sổ 7 ngày gần nhất, dùng upsert idempotent.

**Architecture:** Native Golang trong `go-services`, mượn nguyên pattern handler `kv.revenue_sync`: `task-cron` (robfig/cron) enqueue job `ads.report_lark_sync` → `task-worker` chạy handler `AdsReportLarkSyncNative` → aggregate bằng SQL thuần (GORM `db.Raw`) → upsert lên Lark Bitable qua `lark.OpenAPIClient`, dùng 2 bảng mapping Postgres (`record_key → lark_record_id`) để chọn create vs update.

**Tech Stack:** Go 1.25, GORM (raw SQL), pgx/v5, samber/do, robfig/cron, Lark OpenAPI (tenant token + Bitable batch). Django (model + migration cho 2 bảng mapping). Build/test qua Docker (máy không có Go local — xem [[go-build-via-docker]]).

## Chiến lược test (theo đúng convention codebase — ĐỌC KỸ)

Codebase **không** test tích hợp DB cho handler Go. Tiền lệ `kv_revenue_sync` (handler tương đương) không có test; `docs/go-kv-revenue-lark-push.md` chỉ test package `lark`. Vì vậy:

- **Pure-function unit test (luôn chạy, BẮT BUỘC)**: `classifyUpsert`, field builders (`channelRowToFields`/`productRowToFields`), `adsDateToLarkMs`, key builders, window calc. Không chạm DB.
- **SQL aggregate (`aggregateAdsChannel`/`aggregateAdsProduct`)**: viết test **integration gated bằng env `TEST_DATABASE_URL`** — `if os.Getenv("TEST_DATABASE_URL") == "" { t.Skip(...) }`. Mặc định skip nên `go test ./...` vẫn xanh không cần DB. Chạy tay (verify số) bằng compose postgres đã `migrate`.
- **SQL chính thức được kiểm chứng** qua smoke test Task 11 + đối chiếu trang báo cáo ads (giống cách kv_revenue_sync đã validate).

**Lệnh build/vet/test chuẩn (từ [[go-build-via-docker]], dùng path tuyệt đối + cache volume để build lại nhanh):**
```bash
docker run --rm \
  -v "$(pwd)/go-services:/app" -v desi-gomod:/go/pkg/mod -v desi-gobuild:/root/.cache/go-build \
  -w /app golang:1.25-alpine sh -c "go build ./... && go vet ./... && go test ./..."
```
Chạy 1 test cụ thể: thay phần `go test ./...` bằng `go test ./internal/... -run TenTest -v`. 2 named volume `desi-gomod`/`desi-gobuild` giữ module + build cache giữa các lần chạy (lần đầu vẫn tải module, sau đó nhanh).

**Chạy integration test aggregate (tùy chọn, cần Postgres đã migrate):** set `TEST_DATABASE_URL` trỏ tới postgres của `docker-compose.yml` (service `postgres`) trên cùng network, sau khi `docker compose run --rm backend python manage.py migrate`.

---

## Bối cảnh & tham chiếu (đọc trước khi code)

- Handler mẫu: `go-services/internal/service/worker/business/handlers/kv_revenue_sync.go` — copy cấu trúc (aggregate → lookup mapping → phân loại create/update → save mapping). **Lưu ý**: ads push KHÔNG best-effort như kv_revenue (xem Task 7 quyết định trả error để retry).
- Lark client: `go-services/internal/shared/gateway/lark/openapi.go` — `BatchCreateRecords`/`BatchUpdateRecords` đang hard-code `c.cfg.TableID`. Task 2 thêm biến thể nhận `tableID` tường minh để dùng cho 2 bảng ads.
- GORM models dùng chung: `go-services/internal/shared/dbmodel/dbmodel.go`.
- Logic gốc (nguồn chân lý cho SQL): `backend/apps/orders/_repositories_reports.py` — `_ads_landing_orders_queryset`, `summary_ads_revenue_by_channel`, `summary_ads_top_products`, `summary_ads_daily_breakdown`.
- DI/registration worker: `go-services/internal/service/worker/module.go` (reg map dòng 132-157, provider Lark dòng 71-80).
- Cron config: `go-services/config/task-cron.yaml`.
- Model mapping mẫu (Django): `backend/apps/kiotviet/models.py:191` (`KiotVietRevenueLarkRecord`).

### Hằng số đã xác minh từ codebase

- `CLOSED_SALES_STATUS_CODES = ('closed','closed_express','closed_scheduled_date','created_order')`
- `SALE_FB_CHANNEL_IDS = (24,)`
- `EXCLUDED_BRANCH_IDS = ('1000000139',)`
- `ADS_DAILY_COST_MULTIPLIER = 1.1`
- `OrderStatusCancelled = 6`
- Django `TIME_ZONE = "Asia/Bangkok"`, `USE_TZ = True`
- Bảng & cột: `orders(id, channel_id, branch_id, order_source, status, invoice_status, total_after_discount, deleted_at, created_at)`, `order_assessments(order_id, sales_status_code)`, `order_items(order_id, product_id, line_total)`, `products(id, name, sku)`, `channels(id, code, name, fb_ads_accounts int[], deleted_at)`, `facebook_ad_account_daily_spends(ad_account_id, date, spend)`.

---

## File Structure

| File | Trách nhiệm | Loại |
|---|---|---|
| `backend/apps/orders/models.py` | 2 model mapping `AdsDailyChannelLarkRecord`, `AdsDailyProductLarkRecord` | Modify |
| `backend/apps/orders/migrations/00XX_ads_lark_records.py` | Tạo 2 bảng mapping | Create |
| `go-services/internal/shared/dbmodel/dbmodel.go` | Mở rộng `Order`, `OrderItem`; thêm `Product`, `Channel`, 2 struct mapping ads | Modify |
| `go-services/internal/shared/gateway/lark/openapi.go` | Thêm `BatchCreateRecordsInTable`/`BatchUpdateRecordsInTable` + `ConfiguredAuth()` | Modify |
| `go-services/internal/shared/gateway/lark/openapi_test.go` | Test các method table-explicit | Modify |
| `go-services/internal/service/worker/business/handlers/ads_report_lark_sync.go` | Handler: aggregate + upsert 2 bảng | Create |
| `go-services/internal/service/worker/business/handlers/ads_report_lark_sync_test.go` | Pure-function unit test (classify, builders, key, window) — luôn chạy | Create |
| `go-services/internal/service/worker/business/handlers/ads_report_lark_sync_integration_test.go` | Integration test SQL aggregate — gated `TEST_DATABASE_URL`, skip mặc định | Create |
| `go-services/internal/service/worker/module.go` | Đăng ký handler + đọc 2 env table id | Modify |
| `go-services/config/task-cron.yaml` | Thêm cron `ads.report_lark_sync @every 30m` | Modify |
| `backend/apps/.../seed_cron_jobs.py` (nếu có khai báo) | Khai báo cron tương ứng | Modify (nếu cần) |

---

## Task 1: Django models + migration cho 2 bảng mapping

**Files:**
- Modify: `backend/apps/orders/models.py`
- Create: `backend/apps/orders/migrations/00XX_ads_lark_records.py` (số thứ tự do `makemigrations` sinh)

- [ ] **Step 1: Thêm 2 model vào cuối `backend/apps/orders/models.py`**

```python
class AdsDailyChannelLarkRecord(models.Model):
    """Map (ngày|channel_id) -> Lark Bitable record id cho bảng Ads_Daily_Channel.
    Cho phép upsert (PUT vs POST) khi đẩy lại cùng key trong cửa sổ 7 ngày."""

    record_key = models.CharField(max_length=64, unique=True, verbose_name="Record Key")  # 'YYYY-MM-DD|channel_id'
    lark_record_id = models.CharField(max_length=64, verbose_name="Lark Bitable Record ID")
    last_synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ads_daily_channel_lark_records"
        verbose_name = "Ads Daily Channel Lark Record"

    def __str__(self):
        return f"{self.record_key} -> {self.lark_record_id}"


class AdsDailyProductLarkRecord(models.Model):
    """Map (ngày|channel_id|product_id) -> Lark Bitable record id cho bảng Ads_Daily_Product."""

    record_key = models.CharField(max_length=96, unique=True, verbose_name="Record Key")  # 'YYYY-MM-DD|channel_id|product_id'
    lark_record_id = models.CharField(max_length=64, verbose_name="Lark Bitable Record ID")
    last_synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ads_daily_product_lark_records"
        verbose_name = "Ads Daily Product Lark Record"

    def __str__(self):
        return f"{self.record_key} -> {self.lark_record_id}"
```

- [ ] **Step 2: Sinh migration**

Django service trong compose tên là **`web`** (file `docker-compose.local.yml`), KHÔNG phải `backend`. Repo chỉ có `.env.example` (chưa có `.env`), và `makemigrations` không cần DB thật nhưng cần Django import được settings (có biến bắt buộc, gồm `ENCRYPTION_KEY` là Fernet key 44-byte base64).

Ưu tiên: nếu dựng được stack, chạy:
```bash
cp .env.example .env   # rồi điền các biến bắt buộc; tạo ENCRYPTION_KEY:
docker run --rm python:3.12-alpine sh -c "pip install cryptography -q && python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())'"
docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm web python manage.py makemigrations orders
```
Expected: tạo `backend/apps/orders/migrations/00XX_ads_lark_records.py` với 2 `CreateModel`.

**Fallback (nếu không dựng được stack):** viết tay migration. Tìm migration mới nhất hiện có:
```bash
ls backend/apps/orders/migrations/ | grep -E '^[0-9]' | sort | tail -1
```
Tạo file `backend/apps/orders/migrations/00XX_ads_lark_records.py` (XX = số kế tiếp), `dependencies` trỏ tới migration mới nhất đó:

```python
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "<LATEST_MIGRATION_NAME_WITHOUT_PY>"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdsDailyChannelLarkRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("record_key", models.CharField(max_length=64, unique=True, verbose_name="Record Key")),
                ("lark_record_id", models.CharField(max_length=64, verbose_name="Lark Bitable Record ID")),
                ("last_synced_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Ads Daily Channel Lark Record", "db_table": "ads_daily_channel_lark_records"},
        ),
        migrations.CreateModel(
            name="AdsDailyProductLarkRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("record_key", models.CharField(max_length=96, unique=True, verbose_name="Record Key")),
                ("lark_record_id", models.CharField(max_length=64, verbose_name="Lark Bitable Record ID")),
                ("last_synced_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Ads Daily Product Lark Record", "db_table": "ads_daily_product_lark_records"},
        ),
    ]
```

- [ ] **Step 3: Xác nhận migration nhất quán (nếu dựng được stack)**

Nếu stack chạy được:
```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm web python manage.py makemigrations --check --dry-run orders
```
Expected: "No changes detected" (migration khớp models). Rồi `... run --rm web python manage.py migrate orders` → `OK`. Nếu KHÔNG dựng được stack, ghi DONE_WITH_CONCERNS và để verify ở Task 11.

- [ ] **Step 4: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations/
git commit -m "feat: add ads daily lark record mapping tables"
```

---

## Task 2: Lark client — method ghi vào table tường minh

Hiện `BatchCreateRecords`/`BatchUpdateRecords` chỉ ghi vào `c.cfg.TableID` (bảng revenue). Ads cần 2 bảng khác → thêm biến thể nhận `tableID`, giữ method cũ làm wrapper (DRY). Dùng chung token cache của 1 client.

**Files:**
- Modify: `go-services/internal/shared/gateway/lark/openapi.go`
- Test: `go-services/internal/shared/gateway/lark/openapi_test.go`

- [ ] **Step 1: Viết test thất bại cho method table-explicit**

Thêm vào `openapi_test.go` (đặt cạnh test BatchCreate hiện có; tái dùng helper `httptest` đã có trong file — đọc test hiện tại để khớp cách tạo server & assert path):

```go
func TestBatchCreateRecordsInTable_UsesGivenTable(t *testing.T) {
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/auth/v3/tenant_access_token/internal" {
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`{"code":0,"tenant_access_token":"t","expire":7200}`))
			return
		}
		gotPath = r.URL.Path
		w.WriteHeader(200)
		_, _ = w.Write([]byte(`{"code":0,"data":{"records":[{"record_id":"rec1","fields":{}}]}}`))
	}))
	defer srv.Close()

	c := NewOpenAPI(OpenAPIConfig{BaseURL: srv.URL, AppID: "a", AppSecret: "s", AppToken: "app1", TableID: "revtbl"})
	out, err := c.BatchCreateRecordsInTable(context.Background(), "adstbl", []BitableRecord{{Fields: map[string]any{"x": 1}}})
	if err != nil {
		t.Fatalf("unexpected err: %v", err)
	}
	if len(out) != 1 || out[0].RecordID != "rec1" {
		t.Fatalf("unexpected records: %+v", out)
	}
	if gotPath != "/bitable/v1/apps/app1/tables/adstbl/records/batch_create" {
		t.Fatalf("wrong path: %s", gotPath)
	}
}
```

- [ ] **Step 2: Chạy test, xác nhận fail (chưa có method)**

Run:
```bash
docker compose -f go-services/docker-compose.yml run --rm gotest go test ./internal/shared/gateway/lark/ -run TestBatchCreateRecordsInTable -v
```
(Nếu repo chưa có service `gotest`, dùng one-off: `docker run --rm -v "$PWD/go-services":/app -w /app golang:1.25 go test ./internal/shared/gateway/lark/ -run TestBatchCreateRecordsInTable -v`.)
Expected: FAIL — `c.BatchCreateRecordsInTable undefined`.

- [ ] **Step 3: Thêm implementation table-explicit + refactor wrapper**

Trong `openapi.go`, thay thân 2 method cũ và thêm biến thể + helper auth:

```go
// ConfiguredAuth reports whether tenant credentials + AppToken are present
// (không yêu cầu TableID — dùng cho caller truyền tableID tường minh).
func (c *OpenAPIClient) ConfiguredAuth() bool {
	return c.cfg.AppID != "" && c.cfg.AppSecret != "" && c.cfg.AppToken != ""
}

// BatchCreateRecordsInTable creates records in an explicit table, returning them
// (with record_id) in input order. Chunks at batchLimit per request.
func (c *OpenAPIClient) BatchCreateRecordsInTable(ctx context.Context, tableID string, records []BitableRecord) ([]BitableRecord, error) {
	if len(records) == 0 {
		return nil, nil
	}
	path := fmt.Sprintf("/bitable/v1/apps/%s/tables/%s/records/batch_create", c.cfg.AppToken, tableID)
	created := make([]BitableRecord, 0, len(records))
	for _, chunk := range chunkRecords(records, batchLimit) {
		data, err := c.bitableRequest(ctx, path, map[string]any{"records": chunk})
		if err != nil {
			return created, err
		}
		created = append(created, decodeRecords(data)...)
	}
	return created, nil
}

// BatchUpdateRecordsInTable updates records in an explicit table (each must carry RecordID + Fields).
func (c *OpenAPIClient) BatchUpdateRecordsInTable(ctx context.Context, tableID string, records []BitableRecord) ([]BitableRecord, error) {
	if len(records) == 0 {
		return nil, nil
	}
	path := fmt.Sprintf("/bitable/v1/apps/%s/tables/%s/records/batch_update", c.cfg.AppToken, tableID)
	updated := make([]BitableRecord, 0, len(records))
	for _, chunk := range chunkRecords(records, batchLimit) {
		data, err := c.bitableRequest(ctx, path, map[string]any{"records": chunk})
		if err != nil {
			return updated, err
		}
		updated = append(updated, decodeRecords(data)...)
	}
	return updated, nil
}
```

Rồi rút gọn 2 method cũ thành wrapper (giữ tương thích `kv_revenue_sync`):

```go
func (c *OpenAPIClient) BatchCreateRecords(ctx context.Context, records []BitableRecord) ([]BitableRecord, error) {
	return c.BatchCreateRecordsInTable(ctx, c.cfg.TableID, records)
}

func (c *OpenAPIClient) BatchUpdateRecords(ctx context.Context, records []BitableRecord) ([]BitableRecord, error) {
	return c.BatchUpdateRecordsInTable(ctx, c.cfg.TableID, records)
}
```

- [ ] **Step 4: Chạy test, xác nhận pass + cả package lark vẫn xanh**

Run:
```bash
docker run --rm -v "$PWD/go-services":/app -w /app golang:1.25 go test ./internal/shared/gateway/lark/ -v
```
Expected: PASS toàn bộ (gồm test cũ của BatchCreate/Update — chứng minh wrapper không vỡ).

- [ ] **Step 5: Commit**

```bash
git add go-services/internal/shared/gateway/lark/openapi.go go-services/internal/shared/gateway/lark/openapi_test.go
git commit -m "feat: lark client supports explicit bitable table id"
```

---

## Task 3: Mở rộng dbmodel (Order, OrderItem) + thêm Product, Channel, 2 struct mapping

**Files:**
- Modify: `go-services/internal/shared/dbmodel/dbmodel.go`

- [ ] **Step 1: Thêm cột vào struct `Order` hiện có**

Thêm 3 field vào struct `Order` (sau `Status`/trước hoặc sau các field hiện có; không đổi field cũ):

```go
	ChannelID   int64   `gorm:"column:channel_id"`
	BranchID    string  `gorm:"column:branch_id"`
	OrderSource *string `gorm:"column:order_source"`
```

(Lưu ý: `Order` đã có `Status int16`, `InvoiceStatus *int16`, `TotalAfterDiscount float64`, `DeletedAt`, `CreatedAt` — dùng lại, không khai trùng.)

- [ ] **Step 2: Thêm cột vào struct `OrderItem` hiện có**

Struct `OrderItem` hiện chỉ có ID/OrderID/SKU. Thêm:

```go
	ProductID int64   `gorm:"column:product_id"`
	LineTotal float64 `gorm:"column:line_total"`
```

- [ ] **Step 3: Thêm struct `Product` và `Channel`**

```go
// ── Product (apps.orders.Product) ──────────────────────────────────────────
type Product struct {
	ID   int64  `gorm:"primaryKey;column:id"`
	Name string `gorm:"column:name"`
	SKU  string `gorm:"column:sku"`
}

func (Product) TableName() string { return "products" }

// ── Channel (apps.channels_core.Channel) ───────────────────────────────────
// SUBSET. fb_ads_accounts là int[] Postgres — chỉ đọc qua raw SQL (unnest),
// không map array vào struct để tránh phụ thuộc driver array type.
type Channel struct {
	ID        int64      `gorm:"primaryKey;column:id"`
	Code      string     `gorm:"column:code"`
	Name      string     `gorm:"column:name"`
	DeletedAt *time.Time `gorm:"column:deleted_at"`
}

func (Channel) TableName() string { return "channels" }
```

- [ ] **Step 4: Thêm 2 struct mapping ads**

```go
// ── AdsDailyChannelLarkRecord (apps.orders.AdsDailyChannelLarkRecord) ──────
type AdsDailyChannelLarkRecord struct {
	ID           int64     `gorm:"primaryKey;column:id"`
	RecordKey    string    `gorm:"column:record_key;uniqueIndex"`
	LarkRecordID string    `gorm:"column:lark_record_id"`
	LastSyncedAt time.Time `gorm:"column:last_synced_at;autoUpdateTime"`
	CreatedAt    time.Time `gorm:"column:created_at;autoCreateTime"`
	UpdatedAt    time.Time `gorm:"column:updated_at;autoUpdateTime"`
}

func (AdsDailyChannelLarkRecord) TableName() string { return "ads_daily_channel_lark_records" }

// ── AdsDailyProductLarkRecord (apps.orders.AdsDailyProductLarkRecord) ──────
type AdsDailyProductLarkRecord struct {
	ID           int64     `gorm:"primaryKey;column:id"`
	RecordKey    string    `gorm:"column:record_key;uniqueIndex"`
	LarkRecordID string    `gorm:"column:lark_record_id"`
	LastSyncedAt time.Time `gorm:"column:last_synced_at;autoUpdateTime"`
	CreatedAt    time.Time `gorm:"column:created_at;autoCreateTime"`
	UpdatedAt    time.Time `gorm:"column:updated_at;autoUpdateTime"`
}

func (AdsDailyProductLarkRecord) TableName() string { return "ads_daily_product_lark_records" }
```

- [ ] **Step 5: Build để xác nhận compile**

Run:
```bash
docker run --rm -v "$PWD/go-services":/app -w /app golang:1.25 go build ./...
```
Expected: build thành công, không lỗi.

- [ ] **Step 6: Commit**

```bash
git add go-services/internal/shared/dbmodel/dbmodel.go
git commit -m "feat: extend dbmodel for ads report lark sync"
```

---

## Task 4: Handler — SQL aggregate (bảng A: ngày×kênh) + test

Bắt đầu handler mới bằng phần aggregate. Theo convention codebase (xem "Chiến lược test" đầu plan): SQL aggregate KHÔNG test bằng unit thường (cần Postgres thật vì `AT TIME ZONE`/`unnest`). Viết hàm trước, build/vet, rồi thêm 1 test integration **gated `TEST_DATABASE_URL`** (skip mặc định) để verify số khi chạy tay.

**Files:**
- Create: `go-services/internal/service/worker/business/handlers/ads_report_lark_sync.go`
- Create: `go-services/internal/service/worker/business/handlers/ads_report_lark_sync_integration_test.go`

- [ ] **Step 1: Implement `aggregateAdsChannel` trong `ads_report_lark_sync.go`**

```go
package handlers

import (
	"context"
	"fmt"
	"time"

	"gorm.io/gorm"
)

const adsTimeZone = "Asia/Bangkok"

// adsClosedSalesStatusSQL — danh sách sales_status_code coi là "đã chốt".
var adsClosedSalesStatus = []string{"closed", "closed_express", "closed_scheduled_date", "created_order"}

const (
	adsSaleFBChannelID  = 24
	adsExcludedBranchID = "1000000139"
	adsCostMultiplier   = 1.1
)

// adsChannelRow là một dòng grain ngày×kênh.
type adsChannelRow struct {
	Date         time.Time `gorm:"column:d"`
	ChannelID    int64     `gorm:"column:channel_id"`
	ChannelCode  string    `gorm:"column:code"`
	ChannelName  string    `gorm:"column:name"`
	ClosedOrders int64     `gorm:"column:closed_orders"`
	Revenue      float64   `gorm:"column:revenue"`
	Cost         float64   `gorm:"column:cost"`
}

// aggregateAdsChannel tính doanh thu (đơn đã chốt, KHÔNG phải giao thành công)
// + chi phí Ads (×1.1, chia đều ad_account cho N kênh) theo ngày×kênh trong [from,to].
func aggregateAdsChannel(ctx context.Context, db *gorm.DB, from, to time.Time) ([]adsChannelRow, error) {
	// Doanh thu + số đơn chốt theo ngày×kênh.
	revSQL := `
SELECT (o.created_at AT TIME ZONE ?)::date AS d,
       o.channel_id AS channel_id, ch.code AS code, ch.name AS name,
       COUNT(o.id) AS closed_orders,
       COALESCE(SUM(o.total_after_discount), 0) AS revenue
FROM orders o
JOIN order_assessments oa ON oa.order_id = o.id
JOIN channels ch ON ch.id = o.channel_id
WHERE o.deleted_at IS NULL
  AND o.order_source IS NOT NULL AND o.order_source <> ''
  AND o.status <> 6
  AND (o.invoice_status IS NULL OR o.invoice_status <> 2)
  AND o.channel_id <> ?
  AND o.branch_id <> ?
  AND oa.sales_status_code IN ?
  AND (o.created_at AT TIME ZONE ?)::date BETWEEN ? AND ?
GROUP BY d, o.channel_id, ch.code, ch.name`

	type revRow struct {
		Date         time.Time `gorm:"column:d"`
		ChannelID    int64     `gorm:"column:channel_id"`
		Code         string    `gorm:"column:code"`
		Name         string    `gorm:"column:name"`
		ClosedOrders int64     `gorm:"column:closed_orders"`
		Revenue      float64   `gorm:"column:revenue"`
	}
	var revRows []revRow
	if err := db.WithContext(ctx).Raw(revSQL,
		adsTimeZone, adsSaleFBChannelID, adsExcludedBranchID, adsClosedSalesStatus,
		adsTimeZone, from, to,
	).Scan(&revRows).Error; err != nil {
		return nil, fmt.Errorf("ads revenue agg: %w", err)
	}

	// Chi phí theo ngày×kênh: unnest fb_ads_accounts, chia đều spend cho số kênh map cùng ad_account.
	costSQL := `
WITH acc_chan AS (
    SELECT ch.id AS channel_id, ch.code, ch.name, acc.account_id AS ad_account_id
    FROM channels ch
    CROSS JOIN LATERAL unnest(ch.fb_ads_accounts) AS acc(account_id)
    WHERE ch.deleted_at IS NULL AND ch.id <> ?
),
acc_count AS (
    SELECT ad_account_id, COUNT(*) AS n FROM acc_chan GROUP BY ad_account_id
)
SELECT s.date AS d,
       ac.channel_id AS channel_id, ac.code AS code, ac.name AS name,
       COALESCE(SUM(s.spend / acc_count.n) * ?, 0) AS cost
FROM facebook_ad_account_daily_spends s
JOIN acc_chan ac ON ac.ad_account_id = s.ad_account_id
JOIN acc_count ON acc_count.ad_account_id = s.ad_account_id
WHERE s.date BETWEEN ? AND ?
GROUP BY s.date, ac.channel_id, ac.code, ac.name`

	type costRow struct {
		Date      time.Time `gorm:"column:d"`
		ChannelID int64     `gorm:"column:channel_id"`
		Code      string    `gorm:"column:code"`
		Name      string    `gorm:"column:name"`
		Cost      float64   `gorm:"column:cost"`
	}
	var costRows []costRow
	if err := db.WithContext(ctx).Raw(costSQL,
		adsSaleFBChannelID, adsCostMultiplier, from, to,
	).Scan(&costRows).Error; err != nil {
		return nil, fmt.Errorf("ads cost agg: %w", err)
	}

	// Merge revenue + cost theo key ngày|channel.
	merged := map[string]*adsChannelRow{}
	keyOf := func(d time.Time, ch int64) string {
		return d.Format("2006-01-02") + "|" + fmt.Sprint(ch)
	}
	for _, r := range revRows {
		k := keyOf(r.Date, r.ChannelID)
		merged[k] = &adsChannelRow{
			Date: r.Date, ChannelID: r.ChannelID, ChannelCode: r.Code, ChannelName: r.Name,
			ClosedOrders: r.ClosedOrders, Revenue: r.Revenue,
		}
	}
	for _, c := range costRows {
		k := keyOf(c.Date, c.ChannelID)
		if m, ok := merged[k]; ok {
			m.Cost = c.Cost
		} else {
			merged[k] = &adsChannelRow{
				Date: c.Date, ChannelID: c.ChannelID, ChannelCode: c.Code, ChannelName: c.Name,
				Cost: c.Cost,
			}
		}
	}
	out := make([]adsChannelRow, 0, len(merged))
	for _, m := range merged {
		out = append(out, *m)
	}
	return out, nil
}
```

Lưu ý: GORM Raw với `IN ?` cho slice string sẽ expand đúng. `s.date BETWEEN ? AND ?` — truyền `from`/`to` là `time.Time` ở 00:00 UTC; vì `date` là kiểu DATE, so sánh theo ngày. Đảm bảo `from`/`to` được chuẩn hóa về ngày (Task 7 tạo window bằng ngày địa phương).

- [ ] **Step 2: Viết integration test gated `TEST_DATABASE_URL`**

Tạo `ads_report_lark_sync_integration_test.go`. Skip nếu không có env. Helper `openTestDB(t)` mở GORM tới `TEST_DATABASE_URL` (Postgres đã `migrate` đủ schema Django). Seed bằng raw INSERT (KHÔNG tạo schema). Fixture: channel id=10 (code 'lp-a', name 'LP A', fb_ads_accounts `{777}`), channel id=24 (sale-fb, phải bị loại); 2 đơn ngày 2026-05-20 channel 10 cùng có assessment closed (đơn A total=100000 status=1; đơn B total=50000 status=3 delivered); 1 đơn channel 24 (loại); 1 đơn branch_id='1000000139' (loại); 1 fb_ad_account account_id=777 + 1 spend ngày 2026-05-20 spend=50000.

```go
//go:build integration_optional

package handlers

import (
	"context"
	"os"
	"testing"
	"time"

	"gorm.io/driver/postgres"
	"gorm.io/gorm"
)

func openTestDB(t *testing.T) *gorm.DB {
	url := os.Getenv("TEST_DATABASE_URL")
	if url == "" {
		t.Skip("TEST_DATABASE_URL not set; skipping DB integration test")
	}
	db, err := gorm.Open(postgres.Open(url), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test db: %v", err)
	}
	return db
}

func TestAggregateAdsChannel_Integration(t *testing.T) {
	db := openTestDB(t)
	cleanupAndSeedAdsFixture(t, db) // raw INSERT theo fixture mô tả ở trên; truncate trước

	from := time.Date(2026, 5, 20, 0, 0, 0, 0, time.UTC)
	to := time.Date(2026, 5, 20, 0, 0, 0, 0, time.UTC)
	rows, err := aggregateAdsChannel(context.Background(), db, from, to)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0].ChannelID != 10 {
		t.Fatalf("want 1 row channel 10, got %+v", rows)
	}
	r := rows[0]
	if r.ClosedOrders != 2 {
		t.Fatalf("want 2 closed orders, got %d", r.ClosedOrders)
	}
	if r.Revenue != 150000 { // 100000 + 50000 — KHÔNG phải chỉ delivered
		t.Fatalf("want revenue 150000, got %v", r.Revenue)
	}
	if r.Cost != 55000 { // 50000 * 1.1
		t.Fatalf("want cost 55000, got %v", r.Cost)
	}
}
```

`cleanupAndSeedAdsFixture`: viết trong cùng file, dùng `db.Exec` truncate (`orders, order_items, order_assessments, channels, products, facebook_ad_accounts, facebook_ad_account_daily_spends` — `TRUNCATE ... RESTART IDENTITY CASCADE`) rồi INSERT các dòng fixture. Build tag `integration_optional` để test này KHÔNG nằm trong `go test ./...` mặc định.

- [ ] **Step 3: Build + vet (BẮT BUỘC pass)**

Run:
```bash
docker run --rm -v "$(pwd)/go-services:/app" -w /app golang:1.25-alpine sh -c "go build ./... && go vet ./..."
```
Expected: build + vet OK (file integration test bị loại khỏi build thường nhờ build tag, không gây lỗi).

- [ ] **Step 4: (Tùy chọn) chạy integration test với Postgres đã migrate**

Nếu có Postgres test sẵn (compose `postgres` sau khi `migrate`):
```bash
docker run --rm --network <compose_network> -e TEST_DATABASE_URL="postgres://...@postgres:5432/<db>?sslmode=disable" -v "$(pwd)/go-services:/app" -w /app golang:1.25-alpine sh -c "go test -tags integration_optional ./internal/service/worker/business/handlers/ -run TestAggregateAdsChannel_Integration -v"
```
Expected: PASS (revenue 150000, cost 55000). Nếu không có DB, bỏ qua — sẽ verify ở Task 11.

- [ ] **Step 5: Commit**

```bash
git add go-services/internal/service/worker/business/handlers/ads_report_lark_sync.go go-services/internal/service/worker/business/handlers/ads_report_lark_sync_integration_test.go
git commit -m "feat: ads channel-day aggregation query"
```

---

## Task 5: Handler — SQL aggregate (bảng B: ngày×kênh×sản phẩm) + test

**Files:**
- Modify: `go-services/internal/service/worker/business/handlers/ads_report_lark_sync.go`
- Modify: `go-services/internal/service/worker/business/handlers/ads_report_lark_sync_integration_test.go`

- [ ] **Step 1: Implement `aggregateAdsProduct`**

Thêm vào `ads_report_lark_sync.go`:

```go
// adsProductRow là một dòng grain ngày×kênh×sản phẩm.
type adsProductRow struct {
	Date         time.Time `gorm:"column:d"`
	ChannelID    int64     `gorm:"column:channel_id"`
	ChannelName  string    `gorm:"column:channel_name"`
	ProductID    int64     `gorm:"column:product_id"`
	ProductName  string    `gorm:"column:product_name"`
	SKU          string    `gorm:"column:sku"`
	ClosedOrders int64     `gorm:"column:closed_orders"`
	Revenue      float64   `gorm:"column:revenue"`
}

// aggregateAdsProduct tính số đơn đã chốt (COUNT DISTINCT order) + doanh thu SP
// theo ngày×kênh×sản phẩm trong [from,to], trên tập đơn đã chốt (cùng filter Task 4).
func aggregateAdsProduct(ctx context.Context, db *gorm.DB, from, to time.Time) ([]adsProductRow, error) {
	sql := `
SELECT (o.created_at AT TIME ZONE ?)::date AS d,
       o.channel_id AS channel_id, ch.name AS channel_name,
       oi.product_id AS product_id, p.name AS product_name, p.sku AS sku,
       COUNT(DISTINCT oi.order_id) AS closed_orders,
       COALESCE(SUM(oi.line_total), 0) AS revenue
FROM order_items oi
JOIN orders o ON o.id = oi.order_id
JOIN order_assessments oa ON oa.order_id = o.id
JOIN channels ch ON ch.id = o.channel_id
JOIN products p ON p.id = oi.product_id
WHERE o.deleted_at IS NULL
  AND o.order_source IS NOT NULL AND o.order_source <> ''
  AND o.status <> 6
  AND (o.invoice_status IS NULL OR o.invoice_status <> 2)
  AND o.channel_id <> ?
  AND o.branch_id <> ?
  AND oa.sales_status_code IN ?
  AND (o.created_at AT TIME ZONE ?)::date BETWEEN ? AND ?
GROUP BY d, o.channel_id, ch.name, oi.product_id, p.name, p.sku`

	var rows []adsProductRow
	if err := db.WithContext(ctx).Raw(sql,
		adsTimeZone, adsSaleFBChannelID, adsExcludedBranchID, adsClosedSalesStatus,
		adsTimeZone, from, to,
	).Scan(&rows).Error; err != nil {
		return nil, fmt.Errorf("ads product agg: %w", err)
	}
	return rows, nil
}
```

- [ ] **Step 2: Thêm integration test gated cho product (mở rộng file ở Task 4)**

Thêm vào `ads_report_lark_sync_integration_test.go`. Mở rộng fixture: thêm product id=100 (name 'Ghế Massage', sku 'GM01') + 2 order_items cho đơn A và B của channel 10 (đều product 100): đơn A line_total 80000, đơn B line_total 70000.

```go
func TestAggregateAdsProduct_Integration(t *testing.T) {
	db := openTestDB(t)
	cleanupAndSeedAdsFixture(t, db) // cùng fixture đã có order_items product 100

	from := time.Date(2026, 5, 20, 0, 0, 0, 0, time.UTC)
	to := time.Date(2026, 5, 20, 0, 0, 0, 0, time.UTC)
	rows, err := aggregateAdsProduct(context.Background(), db, from, to)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 || rows[0].ProductID != 100 || rows[0].SKU != "GM01" {
		t.Fatalf("unexpected rows: %+v", rows)
	}
	if rows[0].ClosedOrders != 2 { // COUNT(DISTINCT order_id)
		t.Fatalf("want 2 closed orders, got %d", rows[0].ClosedOrders)
	}
	if rows[0].Revenue != 150000 { // 80000 + 70000
		t.Fatalf("want revenue 150000, got %v", rows[0].Revenue)
	}
}
```

- [ ] **Step 3: Build + vet (BẮT BUỘC pass)**

Run:
```bash
docker run --rm -v "$(pwd)/go-services:/app" -w /app golang:1.25-alpine sh -c "go build ./... && go vet ./..."
```
Expected: OK.

- [ ] **Step 4: Commit**

```bash
git add go-services/internal/service/worker/business/handlers/ads_report_lark_sync.go go-services/internal/service/worker/business/handlers/ads_report_lark_sync_integration_test.go
git commit -m "feat: ads product-channel-day aggregation query"
```

---

## Task 6: Handler — upsert một bảng (generic) + test phân loại create/update/zero-out

Tách logic upsert dùng chung cho cả 2 bảng: nhận danh sách `(record_key, fields)` + tên bảng mapping GORM + tableID Lark, làm: lookup mapping → phân loại → create/update → save mapping mới → zero-out các key trong mapping (thuộc cửa sổ ngày) mà aggregate không còn.

**Files:**
- Modify: `go-services/internal/service/worker/business/handlers/ads_report_lark_sync.go`
- Modify: `go-services/internal/service/worker/business/handlers/ads_report_lark_sync_test.go`

- [ ] **Step 1: Định nghĩa kiểu trung gian + viết test phân loại (không gọi Lark thật)**

Test logic phân loại bằng cách tách hàm thuần `classifyUpsert` không phụ thuộc IO:

```go
func TestClassifyUpsert(t *testing.T) {
	desired := []adsLarkRow{
		{Key: "2026-05-20|10", Fields: map[string]any{"Doanh thu": 150000.0}},
		{Key: "2026-05-20|11", Fields: map[string]any{"Doanh thu": 5000.0}},
	}
	existing := map[string]string{
		"2026-05-20|11": "recB", // đã có -> update
		"2026-05-20|99": "recZ", // còn trong mapping (cửa sổ) nhưng không còn trong desired -> zero-out
	}
	windowKeys := map[string]struct{}{
		"2026-05-20|10": {}, "2026-05-20|11": {}, "2026-05-20|99": {},
	}
	create, update, zero := classifyUpsert(desired, existing, windowKeys, zeroFieldsChannel)

	if len(create) != 1 || create[0].Key != "2026-05-20|10" {
		t.Fatalf("create wrong: %+v", create)
	}
	if len(update) != 1 || update[0].RecordID != "recB" {
		t.Fatalf("update wrong: %+v", update)
	}
	if len(zero) != 1 || zero[0].RecordID != "recZ" {
		t.Fatalf("zero wrong: %+v", zero)
	}
}
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run:
```bash
docker run --rm -v "$PWD/go-services":/app -w /app golang:1.25 go test ./internal/service/worker/business/handlers/ -run TestClassifyUpsert -v
```
Expected: FAIL — `classifyUpsert undefined`.

- [ ] **Step 3: Implement kiểu + `classifyUpsert` + zero field builders**

```go
import "desi/go-services/internal/shared/gateway/lark"

// adsLarkRow là một dòng đã sẵn sàng đẩy Lark (key duy nhất + fields).
type adsLarkRow struct {
	Key    string
	Fields map[string]any
}

// zeroFieldsChannel/Product trả về fields đưa số liệu về 0 nhưng giữ nhãn (Ngày/Kênh/SP)
// — dùng khi key cũ trong cửa sổ không còn dữ liệu (đơn hủy hết). Không xóa record Lark.
func zeroFieldsChannel(prev map[string]any) map[string]any {
	out := map[string]any{}
	for _, k := range []string{larkAdsDate, larkAdsChannel, larkAdsChannelCode} {
		if v, ok := prev[k]; ok {
			out[k] = v
		}
	}
	out[larkAdsRevenue] = 0
	out[larkAdsClosedOrders] = 0
	out[larkAdsCost] = 0
	out[larkAdsRatio] = 0
	return out
}

func zeroFieldsProduct(prev map[string]any) map[string]any {
	out := map[string]any{}
	for _, k := range []string{larkAdsDate, larkAdsPChannel, larkAdsProduct, larkAdsPSKU} {
		if v, ok := prev[k]; ok {
			out[k] = v
		}
	}
	out[larkAdsPClosedOrders] = 0
	out[larkAdsPRevenue] = 0
	return out
}

// classifyUpsert phân loại desired rows thành create/update, và xác định zero-out:
// các key thuộc windowKeys, có trong existing mapping, nhưng KHÔNG có trong desired.
// zeroFields(nil) tạo fields-0 (không có prev vì zero-out chỉ cần ghi 0 + giữ nhãn từ key nếu cần).
func classifyUpsert(
	desired []adsLarkRow,
	existing map[string]string, // key -> record_id
	windowKeys map[string]struct{},
	zeroFields func(prev map[string]any) map[string]any,
) (create []adsLarkRow, update []lark.BitableRecord, zero []lark.BitableRecord) {
	desiredKeys := make(map[string]struct{}, len(desired))
	for _, d := range desired {
		desiredKeys[d.Key] = struct{}{}
		if recID := existing[d.Key]; recID != "" {
			update = append(update, lark.BitableRecord{RecordID: recID, Fields: d.Fields})
		} else {
			create = append(create, d)
		}
	}
	for key := range windowKeys {
		if _, stillThere := desiredKeys[key]; stillThere {
			continue
		}
		if recID := existing[key]; recID != "" {
			zero = append(zero, lark.BitableRecord{RecordID: recID, Fields: zeroFields(nil)})
		}
	}
	return create, update, zero
}
```

Và khai báo hằng tên field Lark (đặt đầu file, khớp Phần 4 spec):

```go
const (
	larkAdsDate         = "Ngày"
	larkAdsChannel      = "Kênh"
	larkAdsChannelCode  = "Mã kênh"
	larkAdsRevenue      = "Doanh thu"
	larkAdsClosedOrders = "Số đơn chốt"
	larkAdsCost         = "Chi phí"
	larkAdsRatio        = "Tỷ lệ CP/DT (%)"

	larkAdsPChannel      = "Kênh"
	larkAdsProduct       = "Sản phẩm"
	larkAdsPSKU          = "SKU"
	larkAdsPClosedOrders = "Số đơn chốt"
	larkAdsPRevenue      = "Doanh thu SP"
)
```

- [ ] **Step 4: Chạy test, xác nhận pass**

Run:
```bash
docker run --rm -v "$PWD/go-services":/app -w /app golang:1.25 go test ./internal/service/worker/business/handlers/ -run TestClassifyUpsert -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add go-services/internal/service/worker/business/handlers/ads_report_lark_sync.go go-services/internal/service/worker/business/handlers/ads_report_lark_sync_test.go
git commit -m "feat: ads lark upsert classification with zero-out"
```

---

## Task 7: Handler — ghép luồng đầy đủ `AdsReportLarkSyncNative`

Ghép: tính window 7 ngày (theo Asia/Bangkok) → aggregate A & B → build fields → load mapping → classify → Lark create/update/zero-out → lưu mapping mới (transaction). Trả error nếu Lark fail để job retry (khác kv_revenue best-effort — đây là yêu cầu idempotency của spec §7).

**Files:**
- Modify: `go-services/internal/service/worker/business/handlers/ads_report_lark_sync.go`

- [ ] **Step 1: Thêm builders fields + helpers window/mapping + handler chính**

```go
import (
	"errors"
	"time"

	"gorm.io/gorm"
	"gorm.io/gorm/clause"

	"desi/go-services/internal/service/worker/entity"
	"desi/go-services/internal/shared/dbmodel"
)

const adsWindowDays = 7

// dateToLarkMs: noon UTC để ổn định ngày qua TZ (giống kv_revenue_sync.dateToLarkMs).
func adsDateToLarkMs(d time.Time) int64 {
	noon := time.Date(d.Year(), d.Month(), d.Day(), 12, 0, 0, 0, time.UTC)
	return noon.UnixMilli()
}

func channelRowToFields(r adsChannelRow) map[string]any {
	ratio := 0.0
	if r.Revenue > 0 {
		ratio = r.Cost / r.Revenue * 100
	}
	return map[string]any{
		larkAdsDate:         adsDateToLarkMs(r.Date),
		larkAdsChannel:      r.ChannelName,
		larkAdsChannelCode:  r.ChannelCode,
		larkAdsRevenue:      r.Revenue,
		larkAdsClosedOrders: r.ClosedOrders,
		larkAdsCost:         r.Cost,
		larkAdsRatio:        ratio,
	}
}

func productRowToFields(r adsProductRow) map[string]any {
	return map[string]any{
		larkAdsDate:          adsDateToLarkMs(r.Date),
		larkAdsPChannel:      r.ChannelName,
		larkAdsProduct:       r.ProductName,
		larkAdsPSKU:          r.SKU,
		larkAdsPClosedOrders: r.ClosedOrders,
		larkAdsPRevenue:      r.Revenue,
	}
}

func channelKey(r adsChannelRow) string {
	return r.Date.Format("2006-01-02") + "|" + fmt.Sprint(r.ChannelID)
}
func productKey(r adsProductRow) string {
	return r.Date.Format("2006-01-02") + "|" + fmt.Sprint(r.ChannelID) + "|" + fmt.Sprint(r.ProductID)
}

// AdsReportLarkSyncNative đẩy báo cáo ads (cửa sổ 7 ngày) lên 2 bảng Lark Base.
// channelTableID / productTableID đến từ env (module.go). Trả error nếu Lark lỗi
// để job được retry (idempotent qua record_key).
func AdsReportLarkSyncNative(db *gorm.DB, larkC *lark.OpenAPIClient, channelTableID, productTableID string) TaskHandler {
	return HandlerFunc(func(ctx context.Context, job entity.Job) (any, error) {
		if db == nil || larkC == nil {
			return nil, errors.New("gorm + lark client required")
		}
		if !larkC.ConfiguredAuth() || channelTableID == "" || productTableID == "" {
			return map[string]any{"skipped": true, "reason": "lark_not_configured"}, nil
		}

		// Window 7 ngày theo Asia/Bangkok (gồm hôm nay).
		loc, err := time.LoadLocation(adsTimeZone)
		if err != nil {
			return nil, fmt.Errorf("load tz: %w", err)
		}
		nowLocal := time.Now().In(loc)
		toDay := time.Date(nowLocal.Year(), nowLocal.Month(), nowLocal.Day(), 0, 0, 0, 0, time.UTC)
		fromDay := toDay.AddDate(0, 0, -(adsWindowDays - 1))

		summary := map[string]any{"from": fromDay.Format("2006-01-02"), "to": toDay.Format("2006-01-02")}

		// ── Bảng A: ngày×kênh ──
		chRows, err := aggregateAdsChannel(ctx, db, fromDay, toDay)
		if err != nil {
			return nil, err
		}
		chDesired := make([]adsLarkRow, 0, len(chRows))
		chWindow := map[string]struct{}{}
		for _, r := range chRows {
			k := channelKey(r)
			chDesired = append(chDesired, adsLarkRow{Key: k, Fields: channelRowToFields(r)})
			chWindow[k] = struct{}{}
		}
		chRes, err := upsertAdsTable(ctx, db, larkC, channelTableID, chDesired, chWindow,
			&dbmodel.AdsDailyChannelLarkRecord{}, zeroFieldsChannel)
		if err != nil {
			return nil, fmt.Errorf("channel table: %w", err)
		}
		summary["channel"] = chRes

		// ── Bảng B: ngày×kênh×SP ──
		pRows, err := aggregateAdsProduct(ctx, db, fromDay, toDay)
		if err != nil {
			return nil, err
		}
		pDesired := make([]adsLarkRow, 0, len(pRows))
		pWindow := map[string]struct{}{}
		for _, r := range pRows {
			k := productKey(r)
			pDesired = append(pDesired, adsLarkRow{Key: k, Fields: productRowToFields(r)})
			pWindow[k] = struct{}{}
		}
		pRes, err := upsertAdsTable(ctx, db, larkC, productTableID, pDesired, pWindow,
			&dbmodel.AdsDailyProductLarkRecord{}, zeroFieldsProduct)
		if err != nil {
			return nil, fmt.Errorf("product table: %w", err)
		}
		summary["product"] = pRes

		return summary, nil
	})
}
```

- [ ] **Step 2: Implement `upsertAdsTable` (load mapping → classify → Lark → save mapping)**

`mappingModel` là con trỏ tới struct GORM mapping (có cột `record_key`, `lark_record_id`); dùng để load + ghi. Vì 2 struct mapping cùng shape, dùng GORM dynamic table qua model interface:

```go
// adsLarkMappingRow đọc record_key + lark_record_id từ bất kỳ bảng mapping ads nào.
type adsLarkMappingRow struct {
	RecordKey    string `gorm:"column:record_key"`
	LarkRecordID string `gorm:"column:lark_record_id"`
}

func upsertAdsTable(
	ctx context.Context,
	db *gorm.DB,
	larkC *lark.OpenAPIClient,
	tableID string,
	desired []adsLarkRow,
	windowKeys map[string]struct{},
	mappingModel any, // &dbmodel.AdsDaily{Channel,Product}LarkRecord{}
	zeroFields func(prev map[string]any) map[string]any,
) (map[string]any, error) {
	// 1. Load mapping cho mọi key trong cửa sổ.
	keys := make([]string, 0, len(windowKeys))
	for k := range windowKeys {
		keys = append(keys, k)
	}
	existing := map[string]string{}
	if len(keys) > 0 {
		var rows []adsLarkMappingRow
		if err := db.WithContext(ctx).Model(mappingModel).
			Where("record_key IN ?", keys).Find(&rows).Error; err != nil {
			return nil, fmt.Errorf("mapping lookup: %w", err)
		}
		for _, r := range rows {
			existing[r.RecordKey] = r.LarkRecordID
		}
	}

	// 2. Phân loại.
	create, update, zero := classifyUpsert(desired, existing, windowKeys, zeroFields)

	// 3. Update + zero-out (đều là BatchUpdate).
	res := map[string]any{"created": 0, "updated": 0, "zeroed": 0}
	if len(update) > 0 {
		if _, err := larkC.BatchUpdateRecordsInTable(ctx, tableID, update); err != nil {
			return res, fmt.Errorf("lark update: %w", err)
		}
		res["updated"] = len(update)
	}
	if len(zero) > 0 {
		if _, err := larkC.BatchUpdateRecordsInTable(ctx, tableID, zero); err != nil {
			return res, fmt.Errorf("lark zero-out: %w", err)
		}
		res["zeroed"] = len(zero)
	}

	// 4. Create + lưu mapping mới (record_id trả về theo thứ tự input).
	if len(create) > 0 {
		recs := make([]lark.BitableRecord, len(create))
		for i, d := range create {
			recs[i] = lark.BitableRecord{Fields: d.Fields}
		}
		created, err := larkC.BatchCreateRecordsInTable(ctx, tableID, recs)
		// Lưu phần đã tạo trước khi surface error (giống saveCreatedMappings).
		saved := saveAdsMappings(ctx, db, mappingModel, create, created)
		res["created"] = saved
		if err != nil {
			return res, fmt.Errorf("lark create: %w", err)
		}
	}
	return res, nil
}

// saveAdsMappings upsert record_key -> lark_record_id cho record vừa tạo (aligned theo index).
func saveAdsMappings(ctx context.Context, db *gorm.DB, mappingModel any, create []adsLarkRow, created []lark.BitableRecord) int {
	n := len(created)
	if len(create) < n {
		n = len(create)
	}
	saved := 0
	for i := 0; i < n; i++ {
		recID := created[i].RecordID
		if recID == "" {
			continue
		}
		row := map[string]any{
			"record_key":     create[i].Key,
			"lark_record_id": recID,
		}
		err := db.WithContext(ctx).Model(mappingModel).
			Clauses(clause.OnConflict{
				Columns:   []clause.Column{{Name: "record_key"}},
				DoUpdates: clause.AssignmentColumns([]string{"lark_record_id", "last_synced_at", "updated_at"}),
			}).
			Create(row).Error
		if err == nil {
			saved++
		}
	}
	return saved
}
```

Lưu ý compile: `db.Model(mappingModel).Create(map)` — GORM cho phép create từ map khi đã `Model(...)`. Nếu GORM bản dùng không hỗ trợ map + OnConflict ổn định, thay bằng switch theo concrete type tạo struct tương ứng (`dbmodel.AdsDailyChannelLarkRecord{RecordKey:..., LarkRecordID:...}`). Trong Step 3 chạy test/integration sẽ phát hiện; nếu lỗi, refactor `saveAdsMappings` nhận `func(key, recID) error` do caller cung cấp (mỗi bảng 1 closure tạo struct cụ thể).

- [ ] **Step 3: Build + chạy toàn bộ test package handlers**

Run:
```bash
docker run --rm -v "$PWD/go-services":/app -w /app golang:1.25 go build ./... && \
docker run --rm -v "$PWD/go-services":/app -w /app golang:1.25 go test ./internal/service/worker/business/handlers/ -run 'TestAggregateAds|TestClassifyUpsert' -v
```
Expected: build OK, tất cả test PASS.

- [ ] **Step 4: Commit**

```bash
git add go-services/internal/service/worker/business/handlers/ads_report_lark_sync.go
git commit -m "feat: ads report lark sync handler"
```

---

## Task 8: Đăng ký handler trong DI + đọc 2 env table id

**Files:**
- Modify: `go-services/internal/service/worker/module.go`

- [ ] **Step 1: Đọc 2 env table id trong provider dispatcher**

Trong hàm `do.Provide(... *business.Dispatcher ...)` (dòng ~119), sau dòng `larkTemplate := os.Getenv("LARK_ORDER_BOT_MESSAGE")` thêm:

```go
		adsChannelTableID := os.Getenv("LARK_BITABLE_ADS_CHANNEL_TABLE_ID")
		adsProductTableID := os.Getenv("LARK_BITABLE_ADS_PRODUCT_TABLE_ID")
```

- [ ] **Step 2: Thêm entry vào reg map**

Trong block "Pattern A native", thêm dòng (cạnh `kv.revenue_sync`):

```go
			"ads.report_lark_sync": handlers.AdsReportLarkSyncNative(gormDB, larkOpenC, adsChannelTableID, adsProductTableID),
```

- [ ] **Step 3: Build**

Run:
```bash
docker run --rm -v "$PWD/go-services":/app -w /app golang:1.25 go build ./...
```
Expected: build OK.

- [ ] **Step 4: Commit**

```bash
git add go-services/internal/service/worker/module.go
git commit -m "feat: register ads report lark sync handler"
```

---

## Task 9: Thêm cron entry `@every 30m`

**Files:**
- Modify: `go-services/config/task-cron.yaml`
- Modify (nếu tồn tại khai báo seed): file `seed_cron_jobs.py` tương ứng

- [ ] **Step 1: Thêm cron vào `task-cron.yaml`, block "Pattern A"**

Sau entry `fb.daily_spend` (dòng 34-36), thêm:

```yaml
  - name: ads.report_lark_sync
    schedule: "@every 30m"
    queue: orders
```

- [ ] **Step 2: Kiểm tra có file seed_cron_jobs khai báo cron không**

Run:
```bash
grep -rn "facebook-ads-sync-daily-spend\|seed_cron_jobs" backend/ go-services/ 2>/dev/null | head
```
Nếu có file định nghĩa danh sách cron (vd seed), thêm entry tương ứng `ads.report_lark_sync` theo đúng format file đó. Nếu cron chỉ điều khiển bởi `task-cron.yaml` (không có bảng seed riêng), bỏ qua bước này.

- [ ] **Step 3: Validate YAML parse được**

Run:
```bash
docker run --rm -v "$PWD/go-services":/app -w /app golang:1.25 go build ./cmd/task-cron/
```
Expected: build OK. (Nếu repo có lệnh validate config riêng, chạy thêm.)

- [ ] **Step 4: Commit**

```bash
git add go-services/config/task-cron.yaml
git commit -m "feat: schedule ads report lark sync every 30m"
```

---

## Task 10: Lint + test toàn bộ + tài liệu env

**Files:**
- Modify: `go-services/...` (chỉ nếu lint báo lỗi)
- Modify: file `.env.example` / docs liệt kê biến môi trường (nếu repo có)

- [ ] **Step 1: golangci-lint**

Run:
```bash
docker run --rm -v "$PWD/go-services":/app -w /app golangci/golangci-lint:latest golangci-lint run ./...
```
Expected: no issues. Sửa inline nếu có (theo CLAUDE.md: idiomatic, value type, ít pointer).

- [ ] **Step 2: Toàn bộ test Go**

Run:
```bash
docker run --rm -v "$PWD/go-services":/app -w /app golang:1.25 go test ./... 
```
Expected: PASS (integration test handlers cần Postgres — chạy qua compose test nếu repo cấu hình; nếu test DB cần biến `TEST_DATABASE_URL`, set khi chạy).

- [ ] **Step 3: Wire 2 biến env mới + tài liệu**

Handler đọc env qua `os.Getenv` trong `task-worker`, nên 2 biến PHẢI được truyền vào service `task-worker` trong compose (giống `LARK_BITABLE_REVENUE_TABLE_ID`). Định vị mọi nơi cần thêm:
```bash
grep -rn "LARK_BITABLE_REVENUE_TABLE_ID" --include="*.env*" --include="*.md" --include="*.yaml" --include="*.yml" docker-compose*.yml .env.example docs/ | head -30
```

Thêm vào: (a) `.env.example`; (b) khối env của service `task-worker` trong `docker-compose.local.yml` **và** `docker-compose.prod.yml` (đặt cạnh `LARK_BITABLE_REVENUE_TABLE_ID` nếu có; nếu task-worker dùng anchor env chung thì thêm vào anchor đó). Giá trị truyền dạng `${LARK_BITABLE_ADS_CHANNEL_TABLE_ID:-}`:

```
LARK_BITABLE_ADS_CHANNEL_TABLE_ID=   # table id bảng Ads_Daily_Channel
LARK_BITABLE_ADS_PRODUCT_TABLE_ID=   # table id bảng Ads_Daily_Product
```

Lưu ý: chỉ `task-worker` cần (nó chạy handler); `task-cron` chỉ enqueue nên không cần.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: document ads lark table env vars"
```

---

## Task 11: Smoke test thủ công (tùy môi trường)

- [ ] **Step 1: Tạo 2 bảng trên Lark Base với đúng field (Phần 4 spec) và lấy table_id**

Trên Lark Base: tạo bảng `Ads_Daily_Channel` (field: `record_key` Text, `Ngày` Date, `Kênh` Text, `Mã kênh` Text, `Doanh thu` Number, `Số đơn chốt` Number, `Chi phí` Number, `Tỷ lệ CP/DT (%)` Number) và `Ads_Daily_Product` (field: `record_key` Text, `Ngày` Date, `Kênh` Text, `Sản phẩm` Text, `SKU` Text, `Số đơn chốt` Number, `Doanh thu SP` Number). Copy 2 table_id vào env.

> Lưu ý: tên field trong handler (`larkAds*`) phải khớp **chính xác** tên cột trên Lark (phân biệt hoa thường, dấu). Nếu Lark trả lỗi field không tồn tại, đối chiếu lại.

- [ ] **Step 2: Chạy 1 lần handler trên môi trường staging**

Enqueue thủ công 1 job `ads.report_lark_sync` (qua công cụ enqueue hiện có / insert vào `jobs`) hoặc chờ cron fire. Kiểm tra log handler trả summary `{created, updated, zeroed}` cho cả 2 bảng, và records xuất hiện trên Lark.

- [ ] **Step 3: Đối chiếu số với trang báo cáo ads**

Mở trang báo cáo ads cùng khoảng ngày, so doanh thu/chi phí theo kênh và số đơn/doanh thu sản phẩm khớp với Lark.

- [ ] **Step 4: Chạy lần 2 để xác nhận idempotent**

Enqueue lại job; xác nhận KHÔNG tạo record trùng (created≈0, updated tăng), record_key vẫn 1-1.

---

## Push & MR

- [ ] **Step cuối: Push branch + tạo MR**

```bash
git push -u origin feat-ads-report-lark-sync
```
Tạo MR (theo CLAUDE.md: commit message tiếng Anh, gửi link MR). Mô tả MR: tóm tắt cron 30', 2 bảng Lark, cửa sổ 7 ngày upsert, mục verify.

---

## Self-Review (đã rà)

- **Spec coverage**: doanh thu đơn chốt (Task 4, assert 150000 không phải delivered) ✓; chi phí ×1.1 (Task 4, assert 55000) ✓; số liệu sản phẩm (Task 5) ✓; 2 bảng riêng (Task 1 + 4/5) ✓; cửa sổ 7 ngày + upsert (Task 7) ✓; zero-out (Task 6) ✓; idempotent/retry (Task 7 trả error) ✓; @every 30m (Task 9) ✓; timezone Asia/Bangkok (Task 4/5/7) ✓.
- **Type consistency**: `aggregateAdsChannel→adsChannelRow`, `aggregateAdsProduct→adsProductRow`, `adsLarkRow{Key,Fields}`, `classifyUpsert`/`upsertAdsTable`/`saveAdsMappings`, `BatchCreateRecordsInTable`/`BatchUpdateRecordsInTable`, `ConfiguredAuth` — dùng nhất quán giữa các task.
- **Placeholder**: không có TODO/“xử lý lỗi phù hợp” chung chung — mọi step có code/command cụ thể.
- **Rủi ro đã ghi chú**: (1) Codebase KHÔNG test tích hợp DB cho handler Go → SQL aggregate dùng integration test gated `TEST_DATABASE_URL` (skip mặc định) + verify ở Task 11; pure-function test luôn chạy. (2) `db.Model(map).Create` + OnConflict — Task 7 Step 2 nêu fallback dùng closure tạo struct cụ thể nếu GORM không hỗ trợ. (3) Tên field Lark phải khớp tuyệt đối — Task 11 Step 1. (4) Django service compose tên `web`, repo chưa có `.env` → Task 1 có fallback viết tay migration. (5) 2 env mới phải wire vào `task-worker` compose — Task 10 Step 3. (6) Build Go qua Docker chậm lần đầu — dùng cache volume `desi-gomod`/`desi-gobuild`.
