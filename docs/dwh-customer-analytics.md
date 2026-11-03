# Spec — Kho phân tích khách hàng (`dwh`) + trang Khám phá dữ liệu + Hồ sơ 360

> Dựng lớp mart phân tích trong schema `dwh`, hợp nhất chân dung khách hàng từ 3 nguồn
> (phiếu đơn, AI bóc từ transcript, hành vi mua), rồi phục vụ 3 mặt tiêu thụ: trang
> Khám phá dữ liệu 2 tab, màn Hồ sơ 360 từng khách, và `CustomerReportPage` hiện có.
>
> - Ngày tạo: 2026-08-20
> - Nhánh: `feat/dwh-customer-analytics`
> - Nền: `apps/customers/` (report_service, report_trends, report_cohort, channel_quality),
>   `apps/callcenter/`, `apps/exports/`, `frontend/src/pages/CustomerReportPage.tsx`.
> - Bài học phải giữ: sự cố `statement timeout` 2026-07-22 — mọi query nặng phải bọc
>   `statement_timeout`, và task **phải để lỗi thoát ra** chứ không nuốt rồi báo `succeeded`.

---

## 0. Hiện trạng đo được (2026-08-20, prod-db)

Mọi con số dưới đây đo trực tiếp trên prod, không phải ước lượng.

### 0.1 Quy mô

| Bảng | Số dòng | Dung lượng |
|---|---:|---:|
| `audit_logs` | 2.176.049 | 4.126 MB |
| `orders` | 547.086 | 1.913 MB |
| `customers` | 757.083 | 465 MB |
| `order_items` | 716.409 | 250 MB |
| `order_assessments` | 485.238 | 131 MB |
| `customer_segment_snapshot` | 750.031 | 112 MB |
| `callcenter_call_records` | 8.229 | 16 MB |
| **Toàn DB `desi`** | 165 bảng | **7.423 MB** |

Tăng trưởng đơn: 5.000–16.000 đơn/tháng (12/2025 → 08/2026), trung bình ~10.000.
Dự phóng 3 năm: ~1,5 triệu đơn. **Đây là small data** — không cần ClickHouse/DuckDB.

### 0.2 Instance Postgres hiện tại

| Tham số | Giá trị |
|---|---|
| `shared_buffers` | **1 GB** |
| `effective_cache_size` | 1,5 GB |
| `work_mem` | 16 MB |
| `max_connections` | 100 |
| `wal_level` | `replica` (logical replication cần restart) |
| Cache hit toàn cluster | **96,17%** (OLTP chuẩn phải >99%) |
| Đã đọc từ đĩa | 697.400.392 block ≈ 5,3 TB |
| pgvector | **không có trong image** |

### 0.3 Độ phủ chân dung — con số quyết định thiết kế

`order_assessments` có 485.242 dòng nhưng **481.523 dòng `age_text` là chuỗi rỗng**, không
phải NULL. `count()` đếm nhầm thành 100%. Độ phủ thật:

| Trường | Toàn lịch sử | Trên đơn từ 17/07 (mốc có ghi âm) |
|---|---:|---:|
| `age_text` | **0,77%** (3.719 dòng) | **10,5%** |
| `occupation_code` | 1,6% | 20–34% |
| `pain_level_code` | 1,0% | 1,4–31% (dao động mạnh theo tháng) |
| `lifestyle_habit_codes` | 0,5% | — |
| `purchase_barrier_codes` | 0,6% | — |

Con số 0,77% bị pha loãng bởi **463.549 phiếu rỗng** do đợt backfill tháng 6/2026.
Con số công bằng để ra quyết định là cột thứ hai.

### 0.4 Pipeline AI đã tồn tại (KHÔNG cần xây lại)

`callcenter_call_records` có sẵn chuỗi `recording_url → transcript → extract_payload → score`:

- 8.229 cuộc gọi, **4.685 có transcript** (57%), **4.298 đã bóc AI**, 4.187 gắn được `order_id`
- Dữ liệu chỉ có **từ 17/07/2026** (mới ~1 tháng)
- Trên 8.781 đơn từ 17/07: **47,1% có ghi âm**

`extract_payload->'clean'` chứa: `evidence` (4.298), `open_insights` (2.551),
`need_type_codes` (2.472), `call_summary` (2.398), `age_text` (800), `family_context` (579),
`used_massage_before` (352), `delax_source_codes` (206), `lifestyle_habit_codes` (96),
`relax_time_codes` (96), `competitors_mentioned` (95), `occupation_code` (90),
`pain_duration_code` (76), `pain_level_code` (65).

**Và AI đã được ghi ngược vào `order_assessments`**: `ai_filled_fields` có dữ liệu ở
**2.726 phiếu** — `needs` (1.588), `call_summary` (1.085), `age_text` (623),
`family_context` (410), `used_massage_before` (265), `pain_level_code` (44).

Hệ quả: **độ phủ 0,77% đã bao gồm đóng góp của AI.** Việc "bơm AI vào chân dung" đã làm
rồi. Đo trực tiếp: trong 3.716 đơn có AI extract, số đơn mà AI bù được chỗ phiếu đang
trống chỉ là **3 đơn** (tuổi), **3 đơn** (nghề), **6 đơn** (mức đau) — AI hầu như chỉ bóc
được ở đúng những đơn sale đã nhập.

### 0.5 Trần của hướng "chân dung khai báo"

Ghi âm phủ 47,1% số đơn; trong 4.298 cuộc đã bóc, AI moi được tuổi ở 798 cuộc (18,6%).
Lý do không phải kỹ thuật: **sale không hỏi tuổi trong cuộc gọi**. Không kỹ thuật nào
bóc được thứ không được nói ra.

⇒ **Quyết định nền tảng của spec này**: ngừng đầu tư lấp lỗ hổng chân dung khai báo bằng
kỹ thuật. Thay bằng (a) đo và hiển thị trung thực độ phủ, (b) dựng **chân dung suy luận
từ hành vi** — độ phủ 100% trên toàn bộ 757.083 khách.

### 0.6 Những gì ĐÃ CÓ, không được làm lại

> **Cập nhật 2026-08-21 — bảng dưới đây giờ là LỊCH SỬ, không phải hiện trạng.**
> Trang báo cáo khách hàng tổng hợp và toàn bộ tầng phía sau nó (4 dòng đầu bảng
> này, cộng `report_fields`/`report_insights`/`customer_behavior_report`/
> `behavior_snapshot_query`/`behavior_classify`) **đã bị gỡ bỏ**. Các phân tích
> tổng hợp của nó — phễu vòng đời, cohort retention, cross-tab, tái kích hoạt,
> xu hướng, chất lượng kênh — **cố ý không được dựng lại**: chủ sở hữu đã quyết
> định chúng không còn cần thiết, đây không phải một khoảng trống bị bỏ sót.
> Nếu bạn đang đọc tài liệu này và thấy tham chiếu tới các module trên ở nơi
> khác, hãy hiểu đó là dấu vết lịch sử đã biết, không phải nợ kỹ thuật cần vá.
> Việc đối chiếu `orders_gtc`/`total_spent` giữa các mart và nguồn giao dịch
> giờ dùng `python manage.py dwh_reconcile` (§10–§11) thay cho endpoint cũ.

| Thứ | Vị trí | Ghi chú |
|---|---|---|
| Trang báo cáo khách hàng tổng hợp | `frontend/src/pages/CustomerReportPage.tsx` (561 dòng, 7 tab) | **Đã gỡ 21/08/2026**, không dựng lại — xem ghi chú trên |
| Backend báo cáo | `apps/customers/report_service.py` + `report_trends` / `report_cohort` / `report_crosstabs` / `channel_quality` / `reactivation_service` | **Đã gỡ 21/08/2026** cùng trang |
| Endpoint | `GET /api/customers/report/summary` | **Đã gỡ 21/08/2026**; đối chiếu số nay dùng `python manage.py dwh_reconcile` |
| Precompute | Celery `apps.customers.tasks.rebuild_segment_snapshot` | Chính task chết timeout 22/07; các bước precompute báo cáo (2-4) và task `warm_report_cache` riêng **đã gỡ 21/08/2026** — task này còn lại, chỉ lo segment snapshot + reconcile loại khách |
| Phân khúc S1–S7 | `customer_segment_categories` + `customer_segment_snapshot` | 46% khách `segment_code` rỗng |
| Danh mục nhu cầu | `need_groups` (8 nhóm) + `need_types` (30+ loại) | 8.150 đơn có ghi nhu cầu |
| Bộ component báo cáo | `frontend/src/components/report/` | `ReportSection`, `DistributionTable`, `DonutChart`, `StackedBarChart`, `CohortHeatmap`, `LifecycleFunnel`, `DataCoverageBadge`… |
| Job xuất Excel nền | `apps/exports/` + `frontend/src/features/exports/` | Registry + WS + dock, không cần code tải file |
| Hộp thoại chọn cột | `frontend/src/components/column-manager/` | Không tự giữ state — trang tự lo persistence |
| Bộ lọc khoảng ngày | `frontend/src/components/ui/date-filter.tsx` | 13 preset + custom; param trên dây là `date_from`/`date_to` |

Ghi chú: nhánh `chore/remove-portrait-section` (*remove customer portrait note rate section*)
**đã merge vào master** — mục chân dung đang bị gỡ dần khỏi báo cáo vì độ phủ quá thấp.
Spec này là hướng thay thế, không phải gỡ tiếp.

---

## 1. Quyết định kiến trúc

| # | Quyết định | Lý do |
|---|---|---|
| **Q1** | **Không dùng ClickHouse/DuckDB.** Kho phân tích là Postgres. | 547k đơn / 7,4 GB; dự phóng 3 năm 1,5 triệu đơn. `customer_360` là bài toán JOIN + upsert liên tục — điểm yếu cố hữu của ClickHouse. Mốc đổi ý ghi ở §7. |
| **Q2** | **Giai đoạn 1 dùng schema `dwh` TRONG database `desi`**, không tạo database mới, không tách instance. | Database trong Postgres **không phải ranh giới tài nguyên** — DB riêng cùng instance mất khả năng JOIN (phải `postgres_fdw`) mà vẫn không được cách ly. Đó là lựa chọn tệ nhất trong 3. Schema riêng giữ được JOIN trực tiếp, không cần đụng `DATABASES` (hiện chỉ có alias `default`, không có router). |
| **Q3** | **Materialized view, không phải bảng + ETL.** | Không cần code đồng bộ, không cần xử lý xoá/sửa, `REFRESH` là một lệnh. Repo chưa có matview nào — spec này tạo tiền lệ đầu tiên. |
| **Q4** | **`REFRESH MATERIALIZED VIEW CONCURRENTLY`**, mỗi matview bắt buộc có **UNIQUE index**. | Không khoá đọc trong lúc refresh. Đổi lại refresh chậm hơn và cần dung lượng tạm gấp đôi — chấp nhận vì chạy giờ thấp điểm. |
| **Q5** | **Không đưa `audit_logs` vào `dwh`.** | 4.126 MB = 56% dung lượng DB, là log kỹ thuật. Giá trị phân tích trong đó (ai đổi trạng thái đơn lúc nào) đã có ở `order_journey_events`. |
| **Q6** | **AI không đè lên số sale nhập.** Mọi trường chân dung có cột `*_src ∈ {declared, ai}` suy ra từ `order_assessments.ai_filled_fields`. | Đo được chính xác AI đóng góp bao nhiêu phần trăm độ phủ. Nếu AI sai nhiều thì đổi quy tắc ưu tiên, không phải sửa dữ liệu. |
| **Q7** | **Chân dung suy luận từ hành vi là trục chính**, chân dung khai báo là trục phụ có ghi rõ độ phủ. | §0.5: trần của chân dung khai báo là 47%. Hành vi phủ 100%. |
| **Q8** | **Filter GTC đồng nhất** với báo cáo hiện có — **5 mệnh đề**, xem §2.0. | Trùng định nghĩa `report_trends` / behavior report — nếu lệch thì số của trang mới sẽ đá nhau với `CustomerReportPage`. |
| **Q9** | **API dùng DRF theo khuôn `apps/exports`, KHÔNG dùng django-ninja `api_v2`.** | `config/api_v2.py` là chỗ greenfield được khuyến nghị, nhưng FE sinh type từ `backend/schema.yaml` (drf-spectacular) qua `npm run gen:api`. Đi ninja là mất typed client ở FE. Ghi nhận đây là nợ kỹ thuật có chủ đích. |
| **Q10** | **`CustomerReportPage` giữ nguyên giao diện**, chỉ đổi nguồn số liệu ở backend (Sprint 5). | Không đụng UI đang chạy tốt. Rủi ro thấp, giá trị (hết timeout) cao. |
| **Q11** | **Ngoài phạm vi**: pgvector/RAG, hợp nhất DB chat, tách instance riêng, logical replication, sửa quy trình sale để tăng độ phủ khai báo. | Xem §7 — đều phụ thuộc kết quả đo của Sprint 1–2. |

---

## 2. Tầng dữ liệu — schema `dwh`

### 2.0 Định nghĩa GTC (Q8) — 5 mệnh đề, sao chép nguyên văn

Nguồn chân lý: `backend/apps/customers/report_trends.py:85-90`. Mệnh đề thứ 5 rất dễ bỏ
sót và bỏ sót là số lệch câm lặng:

```sql
o.deleted_at IS NULL
AND o.status = 3                                     -- OrderStatus.DELIVERED_SUCCESS
AND (o.invoice_status IS DISTINCT FROM 2)            -- hoá đơn huỷ
AND NOT (o.branch_id = ANY(ARRAY['1000000139']))     -- EXCLUDED_BRANCH_IDS
AND (o.order_source IS NULL
     OR o.order_source NOT IN ('foxia', 'cnv'))      -- loại đơn mirror
```

> ⚠️ **Bẫy NULL**: `order_source` là cột nullable và phần lớn đơn cũ để trống. Viết
> `o.order_source <> 'foxia'` sẽ cho ra `NULL` (không phải TRUE) và **mất sạch đơn cũ**
> khỏi mart. Bắt buộc giữ nguyên dạng `IS NULL OR NOT IN (...)`.
>
> Hằng số nằm ở `apps.common.business_constants.EXCLUDED_BRANCH_IDS` và
> `apps.common.order_scope.MIRROR_ORDER_SOURCES` / `sql_exclude_mirror_orders()`. SQL của
> mart **phải sinh mệnh đề từ các hằng số đó**, không chép cứng giá trị — thêm đối tác
> mirror mới thì mart tự phủ theo.

### 2.1 Cây thư mục

App mới `backend/apps/analytics/`, theo khuôn `apps/exports`:

```
backend/apps/analytics/
  __init__.py
  apps.py                     # AnalyticsConfig, name="apps.analytics"
  urls.py
  tasks.py                    # FACADE: from .infrastructure.tasks import *  (BẮT BUỘC)
  application/
    mart_refresh_service.py   # orchestrate refresh, trả thống kê từng matview
    profile_rules.py          # quy tắc suy luận chân dung hành vi (pure, test được)
  infrastructure/
    tasks.py                  # task Celery thật
    mart_queries.py           # SQL đọc mart (không ORM — matview không có model)
  transport/
    views.py  serializers.py
  migrations/
    0001_dwh_schema_and_marts.py
  tests/
    __init__.py  helpers.py
    test_marts.py
    test_migrations_scratch_db.py
```

**Facade `tasks.py` là bắt buộc**: `app.autodiscover_tasks()` chỉ import `apps.<app>.tasks`
ở gốc app, không quét module lồng. Thiếu file này thì task không đăng ký và job kẹt
`PENDING` — đã có tiền lệ sự cố 2026-08-08.

### 2.2 `dwh.dim_customer_profile` — 1 dòng/khách

Gộp mọi `order_assessments` của khách, mỗi trường lấy **giá trị non-empty mới nhất**, kèm
provenance.

| Cột | Kiểu | Nguồn |
|---|---|---|
| `customer_id` | bigint **UNIQUE** | khoá refresh CONCURRENTLY |
| `age_text` / `age_text_src` / `age_text_order_id` | text / text / bigint | giá trị mới nhất + `declared`\|`ai` |
| `occupation_code` / `occupation_src` | text | |
| `pain_level_code` / `pain_level_src` | text | |
| `pain_duration_code` | text | |
| `lifestyle_habit_codes` | text[] | hợp (union) mọi đơn |
| `relax_time_codes`, `purchase_barrier_codes`, `delax_source_codes` | text[] | hợp |
| `used_massage_before` | boolean | |
| `comparing_brand_text` | text | |
| `need_codes` / `need_group_codes` | text[] | từ `order_needs` → `need_types`/`need_groups` |
| `filled_field_count` | smallint | số trường có giá trị — **cột đo độ phủ** |
| `ai_field_count` | smallint | số trường do AI điền |

Quy tắc `*_src`: tên trường nằm trong `order_assessments.ai_filled_fields` của đúng đơn
được chọn → `'ai'`, ngược lại `'declared'`.

**Bẫy phải xử lý**: chuỗi rỗng ≠ NULL (§0.3). Mọi so sánh dùng
`NULLIF(btrim(coalesce(col,'')), '')`, không dùng `col IS NOT NULL`.

### 2.3 `dwh.fct_order_enriched` — 1 dòng/đơn

Bảng rộng phục vụ tab Đơn hàng và mọi phân tích theo thời gian.

- **Đơn**: `order_id` (UNIQUE), `order_code`, `created_at`, `created_date`, `status`,
  `invoice_status`, `is_gtc` (theo Q8), `order_type`, `order_source`, `shipping_type`,
  `payment_method`, `total_after_discount`, `discount_amount`, `shipping_fee`, `quantity`
- **Khách**: `customer_id`, `customer_code`, `gender`, `is_new_customer`, `segment_code`,
  `segment_name`
- **Kênh/nhân sự**: `channel_id`, `channel_name`, `channel_owner`, `staff_id`, `staff_name`,
  `fb_ad_id`
- **Địa lý**: `province`, `district`, `region` (suy từ `province_code`; đối chiếu
  `frontend/src/features/overview-report/regions.ts`)
- **Sản phẩm**: `product_skus` text[], `product_names` text[], `item_count`, `primary_sku`
  (SKU có `line_total` lớn nhất)
- **Đánh giá**: `sales_status_code`, `not_closed_reason_code`,
  `delivery_business_status_code`, `bomb_reason_group_code`, `customer_classification_code`
- **Chân dung**: `age_text`, `age_text_src`, `occupation_code`, `pain_level_code`,
  `need_codes` text[], `need_group_codes` text[]
- **Cuộc gọi**: `call_count`, `call_total_seconds`, `has_transcript`, `ai_extracted`,
  `quality_total_score`, `ai_not_closed_reason`

> **Dung lượng — CHƯA ĐO ĐƯỢC, vẫn là ước lượng.** 547k dòng × ~45 cột (nhiều mảng)
> ≈ **250–350 MB**; cộng `customer_360` và `dim_customer_profile` ≈ tổng **~500 MB** thêm
> vào DB 7,4 GB. Refresh CONCURRENTLY cần thêm chừng đó dung lượng tạm trong lúc chạy.
> Con số thật chỉ có sau khi chạy trên môi trường có dữ liệu — xem §8.

### 2.4 `dwh.customer_360` — 1 dòng/khách

- **Nhận dạng**: `customer_id` (UNIQUE), `code`, `gender`, `created_at`, `is_new_customer`
- **Phân khúc**: `segment_code`, `segment_name`, `care_priority`, `has_problem`,
  `latest_sales_status_code`
- **Hành vi mua** (chỉ đơn GTC): `orders_gtc`, `orders_all`, `orders_cancelled`,
  `total_spent`, `avg_order_value`, `first_purchase_at`, `last_purchase_at`,
  `days_since_last_purchase`, `repurchase_gap_median_days`, `channels` text[],
  `provinces` text[], `top_skus` text[], `distinct_products`
- **Chân dung** (từ §2.2): mọi trường + `filled_field_count` + `ai_field_count`
- **Hội thoại**: `call_count`, `last_call_at`, `avg_quality_score`,
  `competitors_mentioned` text[], `contraindications` jsonb
- **Chân dung suy luận** (S2b, phủ 100%): `buyer_role`, `price_tier`, `product_affinity`,
  `purchase_rhythm`, `channel_affinity`, `recency_bucket`

### 2.5 Migration

Một migration `0001_dwh_schema_and_marts.py`:

- `migrations.SeparateDatabaseAndState(database_operations=[migrations.RunSQL(FORWARD_SQL, reverse_sql=REVERSE_SQL)])`
  — theo tiền lệ `apps/inventory/migrations/0008_partition_stock_movements.py:146`.
  Matview không có model Django nên không có `state_operations`.
- `FORWARD_SQL`: `CREATE SCHEMA IF NOT EXISTS dwh;` → 3 `CREATE MATERIALIZED VIEW … WITH NO DATA`
  → `CREATE UNIQUE INDEX` cho mỗi matview → index phụ (`created_date`, `customer_id`,
  `segment_code`).
- **`WITH NO DATA`** để `migrate` không treo hàng phút trên prod. Lần nạp đầu do task
  refresh làm. Hệ quả bắt buộc: matview chưa refresh thì **query sẽ lỗi** → xem R4.
- `atomic = False` — cần cho `CREATE INDEX CONCURRENTLY` ở các index phụ.
- **`dependencies` phải khai chéo app**: `("orders", "0133_drop_old_id_index")`, `("customers", "0035_search_trgm_codify_drop_dead")`,
  `("callcenter", "0003_callqualityreview_callqualityscore_and_more")` — RunSQL chạm bảng của các app đó; thiếu thì test dựng DB
  theo thứ tự khác sẽ nổ vì bảng chưa tồn tại. Tiền lệ:
  `apps/customers/migrations/0033_incremental_segment_snapshot_indexes.py:30-33`.
- Idempotent: `IF NOT EXISTS` / `IF EXISTS` ở cả hai chiều.

### 2.6 Import-linter

`backend/.importlinter` có `exhaustive = true` — **app mới không khai tầng thì contract FAIL
ngay**. Trong contract `App layering`, đặt `analytics` thành **một dòng riêng, nằm trên
cùng**, ngay phía trên dòng `callcenter | contact_flow | warranty_codes`:

```
analytics
callcenter | contact_flow | warranty_codes
warranty
kiotviet | facebook_ads | google_ads | best_express | cnv_loyalty | foxia
customers | orders | inventory | hr
…
```

Lý do chọn dòng riêng thay vì nhập chung dòng cao nhất: các app cùng một dòng là **độc lập
và cấm import nhau**. `analytics` cần đọc dữ liệu của `callcenter`, nên nhập chung dòng sẽ
tự chặn mình. Đặt riêng ở trên cùng thì `analytics` import được mọi tầng dưới, và không app
nào import ngược lại được — đúng ý đồ: mart là nơi tiêu thụ dữ liệu, không ai phụ thuộc nó.

Test của app sẽ import `apps.orders.models` / `apps.customers.models` để dựng dữ liệu →
thêm entry wildcard vào **KHỐI A1b** (`apps.analytics.tests.* -> apps.orders.models`), kèm
comment lý do. **Không** thêm dòng nào vào KHỐI B (nợ, chỉ được giảm).

---

## 3. Refresh

Task `apps.analytics.tasks.refresh_dwh_marts`, theo khuôn `rebuild_segment_snapshot`
(`backend/apps/customers/tasks.py`):

```python
_MART_LOCK_KEY = "lock:refresh_dwh_marts"
_MART_LOCK_TTL = 2400          # > time_limit=1800

@shared_task(name="apps.analytics.tasks.refresh_dwh_marts",
             soft_time_limit=1500, time_limit=1800)
```

- `single_flight(_MART_LOCK_KEY, _MART_LOCK_TTL)` — bỏ lượt nếu đang chạy, log `warning`.
- Mỗi `REFRESH MATERIALIZED VIEW CONCURRENTLY dwh.<name>` bọc
  `statement_timeout(settings.DWH_REFRESH_TIMEOUT_SECONDS)` (**env mới**, mặc định 900 —
  phải cập nhật cả `docker-compose` và `.env.example`).
- **Thứ tự bắt buộc**: `dim_customer_profile` → `fct_order_enriched` → `customer_360`.
- **Task phải để lỗi thoát ra.** Gom lỗi từng bước rồi `raise MartRefreshFailed(...)` ở
  cuối — bài học 22/07: nuốt lỗi khiến Celery báo `succeeded` trong khi 2/5 bước chết,
  `celery-exporter` không đếm được, alert `CeleryTaskFailing` không nổ.

### 3.1 Lịch — chọn khung trống

Các khung đã bị chiếm (`backend/config/settings_parts/celery.py`):

| Giờ | Task |
|---|---|
| 02:30 | `hr-purge-old-attendance-photos` |
| 03:20 | `customers-rebuild-segment-snapshot-full` |
| giờ **lẻ**, phút 00 | `customers-rebuild-segment-snapshot` (nhịp 2h) |
| giờ **lẻ**, phút 10 | `customers-warm-report-cache` |

⇒ Chọn **`crontab(hour=4, minute=25)`** — giờ chẵn, phút lẻ, sau
`rebuild-segment-snapshot-full` 65 phút (lần đo 22/07 nó mất 426s), không chồng nhịp giờ lẻ.
Tuân thủ **nguyên tắc lệch pha** (`celery.py:135-146`): không dùng `crontab(minute="*/N")`.

### 3.2 Routing — bắt buộc, có test gác

```python
"apps.analytics.tasks.refresh_dwh_marts": {"queue": CELERY_SLOW_QUEUE},
```

`backend/apps/common/tests/test_celery_routes.py` sẽ **đỏ** nếu thiếu route rõ ràng
(`test_every_beat_task_has_explicit_route_or_is_known_default`).

---

## 4. API (DRF)

| Endpoint | Mục đích |
|---|---|
| `GET /api/analytics/customers?q=` | Ô tìm khách của màn Hồ sơ 360. Tối thiểu 2 ký tự; khớp mã, tên, hoặc SĐT; trần 20 dòng |
| `GET /api/analytics/customers/{id}` | Hồ sơ 360: chân dung + dòng thời gian đơn (trần 500 đơn, kèm cờ `timeline_truncated`) |
| `GET /api/analytics/coverage` | Độ phủ từng trường chân dung, tách `declared` / `ai` |

> **Đổi so với bản đầu (2026-08-21).** Hai endpoint list (`/orders` và `/customers` phân
> trang) đã gỡ cùng hai tab bảng. `/customers` nay là ô tìm kiếm chứ không phải bảng; hồ sơ
> chuyển từ `/customers/{id}/profile` về thẳng `/customers/{id}`.

Ô tìm khách lọc trên bảng `customers` trước rồi mới JOIN vào mart theo khoá chính — mart
không giữ tên lẫn SĐT nên quét nó trước là quét vô ích 473k dòng. Ba index trigram GIN trên
`code`/`full_name`/`phone_raw` (migration `customers/0035`) phục vụ đúng ba cột này.

Param trên dây **snake_case**. Mọi query đọc mart bọc `statement_timeout`. Đăng ký urls
**trước** dòng catch-all `re_path(r"^api/", include("apps.audit.urls"))` tại
`backend/config/urls.py:31`.

Sau khi thêm endpoint: chạy `npm run gen:api` rồi **`prettier --write` ngay** — nếu không,
job drift-check `schema.yaml` sẽ đỏ.

---

## 5. Frontend

### 5.1 Trang Hồ sơ khách hàng 360 — `/reports/customer-explorer`

> **Đổi hướng 2026-08-21.** Bản thiết kế cũ mô tả một trang hai tab bảng (Đơn hàng /
> Khách hàng) kèm xuất Excel. Trang đó đã dựng xong và **đã gỡ** theo yêu cầu chủ sở hữu:
> nó trả lời câu "dữ liệu có những gì" chứ không trả lời câu người dùng thật sự hỏi —
> "khách hàng của tôi trông như thế nào". Đường dẫn và mã section RBAC giữ nguyên; chỉ
> nội dung và nhãn hiển thị đổi.

**Vì sao giữ mã section `customer-explorer`:** đổi mã kéo theo migration quyền + cập nhật
`VALID_SECTIONS`, chỗ đã ba lần khoá nhầm người dùng ra ngoài trong dự án này. Mã section
không ai nhìn thấy; nhãn thì đổi được miễn phí.

Cấu trúc `frontend/src/features/customer-profile/`:

```
queries.ts                          # useCustomerSearch / useCustomerProfile
profile-labels.ts                   # nhãn tiếng Việt cho era / recency / price tier / nguồn trường
components/CustomerSearchBox.tsx    # ô tìm khách, tối thiểu 2 ký tự
components/ProfileSummary.tsx       # tên, phân khúc, 4 ô số liệu
components/ProfileBehaviour.tsx     # thế hệ SP, nhịp mua, kênh/tỉnh/SKU
components/ProfileDeclared.tsx      # chân dung khai báo — ẨN HẲN khi rỗng
components/ProfileTimeline.tsx      # dòng thời gian, GIỮ đơn huỷ
pages/CustomerProfilePage.tsx
```

**Nguyên tắc bố cục:** các khối xếp theo độ phủ dữ liệu giảm dần, và khối rỗng thì ẩn hẳn
chứ không hiện khung trống. Chân dung khai báo chỉ có ở khoảng 2/100 khách (xem §0.3) —
nếu để nó làm trung tâm thì hầu hết lần mở trang sẽ là một trang trống, và đó là cách
nhanh nhất để một màn hình mới bị bỏ không sau hai tuần.

**Hai chỗ màn hình cố ý nói ra sự thật khó chịu:**

- Dòng thời gian **giữ đơn huỷ và đơn hoàn** (làm mờ + gắn nhãn trạng thái). Lọc chúng đi
  khiến một khách huỷ năm đơn liên tiếp trông y hệt khách chưa từng đặt gì — đúng lúc sale
  cần thấy điều ngược lại.
- Ô "Đã chi" chỉ cộng đơn giao thành công nên có thể bằng 0 trong khi ô "Đơn" bên cạnh
  khác 0. Lý do được ghi ngay dưới con số; hai ô mâu thuẫn mà không giải thích là cách
  nhanh nhất để người xem kết luận cả báo cáo sai.

### 5.2 Đã gỡ: xuất Excel hai cấp độ

Chủ sở hữu xác nhận 2026-08-21 không dùng. Gỡ `apps/analytics/exports_definitions.py`,
`application/explorer_sort.py`, hai endpoint list, và quyền `customer-explorer:export`
(migration `users/0041`). Muốn có lại thì phải dựng lại từ đầu — ghi ở đây để quyết định
này không bị quên mất lý do.

### 5.3 Test FE

`vitest` + testing-library, mock `vi.mock("@/services/http", ...)` (không có MSW).
**Bắt buộc** `mockViewport(false)` + `restoreViewport()` trong `afterEach` khi assert bảng
desktop — stub `matchMedia` mặc định trả `matches:false` khiến `ResponsiveList` luôn render
nhánh thẻ mobile (`frontend/src/test/setup.ts:7-21`, `frontend/src/test/viewport.ts`).

---

## 6. Rủi ro

| # | Rủi ro | Xử lý |
|---|---|---|
| **R1** | Matview nằm **cùng instance** nên lúc refresh vẫn ăn chung `shared_buffers` 1 GB — đúng cơ chế gây timeout 22/07. | Chạy 04:25 (thấp điểm) là **giảm thiểu, không phải giải quyết**. S1 phải **đo thời gian refresh thật** và ghi lại; con số đó quyết định thời điểm tách instance (§7). |
| **R2** | PgBouncer bật `DISABLE_SERVER_SIDE_CURSORS`; `REFRESH … CONCURRENTLY` là lệnh dài. | Task chạy trên worker queue `slow`; S1 xác minh connection không bị pooler cắt giữa chừng. |
| **R3** | Dung lượng DB tăng ~500 MB + tương đương dung lượng tạm lúc refresh. | Kiểm tra disk trống trước khi chạy lần đầu trên prod. |
| **R4** | Matview tạo `WITH NO DATA` → query trước lần refresh đầu sẽ **lỗi**. | `mart_queries` bắt lỗi và trả thông báo rõ; runbook ghi bước "chạy refresh ngay sau migrate". |
| **R5** | `backend/conftest.py` có `_isolate_hardcoded_channel_ids` đẩy `KOC_CHANNEL_ID` và `SALE_FB_CHANNEL_IDS` sang giá trị giả. | **Không hard-code id kênh trong SQL của mart** — nếu có, test sẽ lệch câm lặng. |
| **R6** | Test matview là DDL → phải chạy scratch DB. | Đặt riêng ở `tests/test_migrations_scratch_db.py`, không trộn test thường. |
| **R7** | Số của trang mới có thể đá nhau với `CustomerReportPage`. | Q8 (filter GTC đồng nhất) + S1 có bước **đối chiếu số** giữa mart và `dwh_reconcile` **trước khi** làm UI. (`CustomerReportPage` đã gỡ 21/08/2026 — xem §0.6.) |
| **R8** | ~~Quyền `customer-explorer:export`~~ — đã thu hồi 21/08/2026 (migration `users/0041`) cùng lúc gỡ xuất Excel. Còn lại `customer-explorer:view`, **vẫn chưa role nào có**. | Gán `:view` ngay sau deploy, nếu không trang mới không ai vào được. |

**Rủi ro đã loại bỏ sau khi đo**: identity resolution qua `phone_normalized` — **0/547.910
đơn** thiếu `customer_id`, không cần.

---

## 7. Sprint

Mỗi sprint đứng độc lập; dừng ở đâu cũng có giá trị.

| Sprint | Nội dung | Tiêu chí hoàn thành |
|---|---|---|
| **S1** | App `analytics`, migration schema `dwh` + 3 matview + index, task refresh + route Celery + beat 04:25, khai `.importlinter`, test | Refresh chạy được trên dữ liệu thật; **ghi lại thời gian refresh và dung lượng thực tế**; `orders_gtc`/`total_spent` khớp kết quả `python manage.py dwh_reconcile` |
| **S2a** | Cột provenance `*_src` + `filled_field_count`/`ai_field_count` + endpoint `/coverage` | Có bảng độ phủ từng trường tách `declared`/`ai` — trả lời được "AI đóng góp bao nhiêu %" |
| **S2b** | Chân dung suy luận từ hành vi trong `application/profile_rules.py` | Phủ **100%** trên 757k khách; phân bố từng chiều hợp lý khi soi mắt |
| **S3** | API `orders`/`customers` + trang `/reports/customer-explorer` 2 tab + ColumnManager + 2 `ExportDefinition` | Lọc theo khoảng ngày, chọn cột, xuất Excel chạy thật |
| **S4** | Màn hồ sơ 360 `/crm/customers/:id` | Mở đúng hồ sơ như bản demo `KH746487`: hành trình + chân dung + hội thoại |
| **S5** | `CustomerReportPage` đổi nguồn sang `dwh.*`, gỡ phần precompute đã thừa | Trang giữ nguyên giao diện; hết `statement timeout`; thời gian phản hồi giảm đo được |

### Mốc đổi ý về hạ tầng

Tách instance Postgres riêng (VPS riêng, image `pgvector/pgvector:pg16`, `shared_buffers`
2 GB — toàn bộ DWH ~500 MB nằm gọn trong RAM) khi **một trong hai** điều xảy ra:

- thời gian refresh đo ở S1 vượt ~10 phút, hoặc gây tụt cache hit của OLTP quan sát được;
- bắt đầu cần pgvector/RAG trên transcript + chat (Q11).

Chuyển sang ClickHouse **chỉ khi** thêm tracking event hành vi vượt ~50–100 triệu dòng/năm,
và khi đó dựng **bên cạnh** cho riêng tầng event, giữ Postgres cho `customer_360`.

---

## 8. Nghiệm thu sau deploy — BƯỚC BẮT BUỘC, CHƯA LÀM

Khối 1 hoàn thành phần **code**. Hai con số quyết định của S1 (§7) **chưa đo được**, vì
không tồn tại môi trường để đo: DB test rỗng nên refresh mất ~0 giây (vô nghĩa), còn trên
prod chỉ được chạy `SELECT` — không được tạo materialized view. Không có số nào ở đây được
bịa ra.

### 8.1 Thứ đã có: ước lượng từ `EXPLAIN` trên prod (2026-08-20)

Chạy `EXPLAIN` (KHÔNG `ANALYZE` — chỉ đọc kế hoạch, không thực thi, an toàn trong giờ làm):

| Phần | Cost | Ghi nhận |
|---|---|---|
| CTE `items` (gộp `order_items` theo đơn) | `0.52..70439` | **Index Scan** + Incremental Sort, không quét bảng. 718.632 dòng → 418.257 nhóm |
| `orders` + 6 LEFT JOIN chiều | `135933..190909` | Parallel (2 workers), toàn Hash Join. Seq scan trên `orders`/`customers`/`customer_segment_snapshot` — đúng kỳ vọng ở độ chọn lọc này |

Không có nested loop nguy hiểm, không có seq scan bất thường. Tổng cost cả 3 matview ước
chừng 300–400k ⇒ refresh nhiều khả năng ở mức **hàng chục giây**, không phải hàng chục
phút. Nghĩa là ngưỡng "vượt ~10 phút thì tách instance" (§7) **gần như chắc chắn không bị
chạm**. Nhưng đây là ước lượng của planner, không phải phép đo.

### 8.2 Runbook: chạy ngay sau khi migration lên môi trường có dữ liệu

```bash
# 1. Áp migration (nhanh — matview tạo WITH NO DATA, không nạp dữ liệu)
python manage.py migrate analytics

# 2. Nạp lần đầu VÀ ĐO. Lần đầu tự động dùng REFRESH thường (không CONCURRENTLY)
#    vì matview chưa từng được nạp — service tự chọn nhánh qua pg_class.relispopulated.
python manage.py shell -c "
from apps.analytics.application.mart_refresh_service import refresh_all_marts
print(refresh_all_marts())"      # -> {'dim_customer_profile': s, 'fct_order_enriched': s, 'customer_360': s}

# 3. Đo dung lượng thật
python manage.py shell -c "
from django.db import connection
c=connection.cursor(); c.execute('''
  SELECT cl.relname, pg_size_pretty(pg_total_relation_size(cl.oid))
  FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace
  WHERE n.nspname='dwh' ORDER BY pg_total_relation_size(cl.oid) DESC''')
print(c.fetchall())"

# 4. ĐỐI CHIẾU SỐ — đây là cổng nghiệm thu thật sự
python manage.py dwh_reconcile
```

Bước 4 phải in `Mart khớp nguồn.` Nếu báo LỆCH thì **dừng lại** — mọi màn hình dựng trên
mart sẽ hiển thị số sai.

**Giới hạn của `dwh_reconcile` (phải biết trước khi tin nó):** hai vế của phép đối chiếu
dùng CHUNG helper `gtc_where()` / `sql_exclude_mirror_orders()`. Nên nó bắt được mart cũ,
refresh dở dang, join làm mất hoặc nhân dòng, cột mart lệch nguồn — nhưng **không** bắt
được trường hợp chính định nghĩa GTC dùng chung bị sai (ví dụ `GTC_STATUS` hay
`CANCELLED_INVOICE` sai giá trị): khi đó hai vế cùng sai và cùng đồng ý với nhau. Đây là
giới hạn cố hữu của mọi reconcile gate, không phải khiếm khuyết của lệnh này.

### 8.3 Ghi lại kết quả

Sau khi chạy, cập nhật §2.3 (dung lượng thật), §7 mốc đổi ý (thời gian refresh thật so với
ngưỡng 10 phút), và §9 dưới đây.

### 8.4 Nợ vận hành khác

- `DWH_REFRESH_TIMEOUT_SECONDS` là **env mới**. Env chỉ có tác dụng khi container được
  **recreate**, không phải restart — bẫy đã biết của repo này.
- `backend/schema.yaml` **cố ý chưa regen** cho endpoint `/api/analytics/coverage`: file
  đang mang sẵn ~444 dòng lệch từ trước, regen sẽ kéo nhiễu của feature khác vào. Trả nợ
  bằng MR chore riêng, hoặc ở đầu khối S3 khi frontend thật sự gọi endpoint.

---

## 9. Tiến độ

| Sprint | Trạng thái | Ghi chú |
|---|---|---|
| S1 | ✅ **XONG, nghiệm thu prod 20/08** | Schema `dwh` + 3 matview + task refresh 04:25 (queue `slow`) + `dwh_reconcile`. 44 test. Số đo thật ở §10: refresh 119 s, mart 589 MB → KHÔNG cần tách instance. |
| S2a | ✅ **XONG, nghiệm thu prod 20/08** | Cột provenance `*_src` + `filled_field_count`/`ai_field_count` + `GET /api/analytics/coverage`. Chưa regen `schema.yaml` (nợ có chủ đích, §8.4). |
| R1 | ✅ **XONG** (khối 2A) | `dwh.mart_refresh_log` + `dwh_reconcile` đối chiếu as-of mốc refresh (§11.4). |
| S2b | ✅ **XONG** (khối 2A) | `era` + 6 cột chân dung suy luận từ hành vi (§11). 53 test. |
| S3 | ⛔ **ĐÃ GỠ 21/08** | Dựng xong và chạy prod: API `orders`/`customers` + trang 2 tab + `ColumnManager` + Xuất Excel + sort server-side (§12). Chủ sở hữu quyết định bỏ: trang trả lời "dữ liệu có những gì" chứ không trả lời "khách hàng trông như thế nào". |
| S4 | ✅ **XONG 21/08** | Màn Hồ sơ khách hàng 360 tại `/reports/customer-explorer` (§5.1) — thay chỗ S3, không phải `/crm/customers/:id` như thiết kế đầu. 19 test (12 backend + 7 FE). Nợ: quyền `customer-explorer:view` chưa gán role nào. |
| S5 | ⛔ **KHÔNG CÒN** | `CustomerReportPage` đã xoá hẳn (MR !1331) thay vì đổi nguồn. |

### Đổi tên đã thực hiện trong quá trình làm

`declared_field_count` → **`filled_field_count`**. Tên cũ nói sai nội dung: nó đếm mọi
trường có giá trị, **kể cả trường do AI điền**, trong khi `ai_field_count` là **tập con**
của chính nó chứ không phải hạng mục ngang hàng. Vì cả spec này tồn tại để đo trung thực
phần khai báo so với phần AI suy luận, một cột mang tên nói ngược nội dung ở đúng chỗ đó
là mìn tạo báo cáo sai. Đổi lúc migration chưa deploy nên chi phí bằng 0.

`price_tier` → **`price_tier_lifetime`**, `purchase_rhythm` → **`purchase_rhythm_lifetime`**
(fix wave review khối 2A, migration `0006`). Cả hai gộp TOÀN BỘ đơn GTC bất kể `era`, trong
khi `era` ngay cạnh tách legacy/current — 485 khách "both" (§11.1) sẽ bị đọc nhầm nhãn trọn
đời thành nhãn của riêng kỳ hiện tại nếu tên cột không nói rõ điều đó. Không đổi công thức,
chỉ đổi tên; đổi trước khi khối 2B đưa lên API nên chi phí bằng 0.

---

## 10. Kết quả nghiệm thu thật (2026-08-20, sau deploy prod)

Runbook §8.2 đã chạy trên prod. Đây là SỐ ĐO, không phải ước lượng.

### 10.1 Thời gian refresh lần đầu (không CONCURRENTLY, vì matview chưa từng nạp)

| Matview | Thời gian | Số dòng | Dung lượng |
|---|---:|---:|---:|
| `dim_customer_profile` | **50,5 s** | 440.524 | 67 MB |
| `fct_order_enriched` | **37,1 s** | 548.015 | 260 MB |
| `customer_360` | **31,6 s** | 473.082 | 200 MB |
| **Tổng** | **119,2 s** | | **589 MB** |

DB tăng 7.423 MB → 7.956 MB.

Đối chiếu ngưỡng §7: trần là 10 phút, thực tế **2 phút** ⇒ **KHÔNG cần tách instance
Postgres riêng.** Câu hỏi hạ tầng lớn nhất của spec này đã ngã ngũ bằng số đo.

Ngân sách timeout cũng rộng: mart chậm nhất 50,5 s so với trần 480 s/lệnh; tổng 119 s so
với `time_limit` 1800 s — dư gấp ~9 lần, chịu được dữ liệu tăng gấp 3 trong 3 năm.

Ghi chú: `dim_customer_profile` CHẬM NHẤT dù NHỎ NHẤT — nó là mart duy nhất phải gộp mảng
và tìm giá trị mới nhất qua mọi phiếu của từng khách (nhiều sort + group), không phải
quét-và-ghi tuyến tính.

### 10.2 Đối chiếu số

| Phép đo | Nguồn | Mart | |
|---|---:|---:|---|
| Số đơn GTC | 445.333 | 445.333 | ✅ |
| Doanh thu GTC | 153.184.302.402 | 153.184.302.402 | ✅ khớp từng đồng |
| Số khách có đơn | 473.083 | 473.082 | ⚠️ lệch 1 — xem 10.3 |

### 10.3 KHIẾM KHUYẾT của `dwh_reconcile` — phải sửa (khối 2)

Lệch 1 khách KHÔNG phải lỗi mart. Truy ra khách `758542` có đúng 1 đơn tạo lúc
08:41:37 UTC, trong khi refresh khởi động lúc ~08:41:30 UTC — đơn sinh ra 7 giây SAU khi
matview đã chụp ảnh dữ liệu. Kiểm chứng: chốt mốc tại thời điểm refresh thì nguồn = mart =
473.082, khớp tuyệt đối.

Gốc rễ: lệnh so **ảnh chụp lúc T** với **nguồn lúc T+n**. Hệ thống tạo ~10.000 đơn/tháng
nên gần như LẦN NÀO CHẠY CŨNG BÁO LỆCH.

Đây là kiểu hỏng tệ hơn không có công cụ: một cổng an toàn báo động giả liên tục sẽ bị
phớt lờ, và đến lúc nó báo động THẬT thì không ai nhìn.

**Cách sửa (khối 2):** thêm bảng `dwh.mart_refresh_log(mart_name, refreshed_at,
duration_seconds)`; service ghi vào sau mỗi lần refresh; `dwh_reconcile` lấy mốc
`min(refreshed_at)` của 3 mart và lọc nguồn `created_at < mốc`. Lợi ích kèm theo: có lịch
sử thời gian refresh để theo dõi hiệu năng — thứ lần này phải đọc thủ công từ log container.

### 10.4 Nợ vận hành phát hiện khi deploy

**Quyền đọc schema.** Schema `dwh` thuộc role `desi`; mọi role read-only (`claude_ro`,
và sau này là BI tool / tài khoản analyst) KHÔNG đọc được cho tới khi cấp quyền. Spec ban
đầu thiếu bước này. Lệnh đã chạy trên prod:

```sql
GRANT USAGE ON SCHEMA dwh TO claude_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA dwh TO claude_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE desi IN SCHEMA dwh GRANT SELECT ON TABLES TO claude_ro;
```

Dòng thứ ba khiến matview tạo về sau tự có quyền — khỏi phải nhớ cấp lại mỗi lần thêm mart.

**PgBouncer:** rủi ro R2 (§6) KHÔNG xảy ra — refresh qua `manage.py shell` trong `desi-web`
chạy trót lọt, `statement_timeout` 480 s giữ được qua pooler.

---

## 11. Thời kỳ ngành hàng và chân dung suy luận (khối 2A)

### 11.1 Vì sao cần `era` — phát hiện từ chính kho dữ liệu này

Đo trên prod 20/08 bằng `dwh.fct_order_enriched`:

| Nhóm khách | Số lượng | Tỉ lệ |
|---|---:|---:|
| Chỉ mua **ngành hàng cũ** (trước 2025) | **353.967** | 86,9% |
| Chỉ mua **máy massage** (2025+) | 52.985 | 13,0% |
| **Mua cả hai** | **485** | **0,12%** |

Tỉ lệ chuyển đổi khách ngành cũ sang sản phẩm hiện tại: **0,14%**.

Giá trị đơn trung bình theo năm cho thấy đứt gãy rõ: 75k (2020) · 198k (2021) · 163k (2022)
· 184k (2023) · 297k (2024) → **1.455k (2025)** · 1.240k (2026).

Hệ quả: 87% khách trong kho KHÔNG phải khách của sản phẩm công ty đang bán. Mọi phân tích
chân dung, mọi phân khúc, mọi danh sách remarketing chạy trên toàn tập đều bị pha loãng bởi
nhóm này. Ví dụ cụ thể: 353.967 khách ngành cũ hiện đang được xếp "Ngủ đông — tái kích hoạt
ngay" trong phân khúc S1–S7; chạy chiến dịch lên tệp đó là đổ tiền vào nhóm có xác suất
chuyển đổi 0,14%.

Mốc `2025-01-01` **suy từ dữ liệu**, không phải quy ước. Khai ở
`apps/analytics/sql/eras.py::CURRENT_ERA_START`.

### 11.2 Bảy cột mới trên `dwh.customer_360`

| Cột | Giá trị | Ghi chú |
|---|---|---|
| `era` | `legacy` / `current` / `both` / NULL | NULL khi khách chưa có đơn GTC nào |
| `price_tier_lifetime` | `low` / `mid` / `high` / NULL | Ngưỡng 1tr / 2tr. Gộp **toàn bộ** đơn GTC bất kể era |
| `purchase_rhythm_lifetime` | `one_time` / `repeat` / NULL | ≥2 đơn GTC = `repeat`. Gộp **toàn bộ** đơn GTC bất kể era |
| `recency_bucket` | `active` / `cooling` / `at_risk` / `dormant` / NULL | 90 / 180 / 365 ngày, tính theo `BUSINESS_TZ` — khớp `days_since_last_purchase` |
| `channel_affinity` | tên kênh | Kênh của đơn GTC **gần nhất** (tie-break: `order_id` lớn hơn thắng) |
| `product_affinity` | SKU | SKU chính của đơn GTC **gần nhất** (tie-break: `order_id` lớn hơn thắng) |
| `buyer_role` | `mixed` / `gift` / `self` / NULL | `mixed` được kiểm TRƯỚC hai nhánh đơn |

**`_lifetime` không phải lỗi đặt tên thiếu nhất quán** — đây là cảnh báo có chủ đích.
`price_tier_lifetime`/`purchase_rhythm_lifetime` gộp TOÀN BỘ lịch sử mua của khách, trong
khi `era` ngay cạnh đó tách rời legacy/current. 485 khách "both" (0,14% chuyển đổi, xem
§11.1) có thể có 20 đơn ngành cũ 100k lẫn 1 đơn massage 2tr — nếu cột tên trần `price_tier`,
lọc `era='current'` rồi hiển thị cạnh nhau sẽ trông như một chỉ số MÔ TẢ hành vi hiện tại,
trong khi nó thực ra là trung bình trọn đời. Đổi tên trước khi khối 2B đưa lên API (fix wave
khối 2A, xem "Đổi tên đã thực hiện" bên dưới).

**Quy tắc xuyên suốt: khách không có đơn thành công thì nhận NULL, KHÔNG nhận nhãn.**
Gán `legacy`/`low`/`one_time` cho khách chỉ có đơn huỷ là khẳng định một hành vi chưa từng
xảy ra. Nhánh `orders_gtc = 0` phải đứng ĐẦU trong mọi biểu thức CASE.

### 11.3 Ba quyết định và lý do

**Ngưỡng giá CỐ ĐỊNH, không dùng phân vị động.** Phân vị động nghe thông minh hơn nhưng làm
khách nhảy nhóm giữa các lần refresh dù bản thân họ không đổi gì — một người bị đẩy từ "chi
cao" xuống "chi trung bình" chỉ vì tháng đó có nhiều khách giàu hơn mua. Ngưỡng 1tr/2tr suy
từ phân bố thật của thời kỳ hiện tại (53.470 khách): dưới 1tr 29,6% · 1–2tr 64,2% · từ 2tr
6,2%.

**`recency_bucket` tính theo thời điểm REFRESH, không phải thời điểm đọc.** Nhãn có thể cũ
tối đa 24 giờ vì mart refresh hằng đêm. Chấp nhận được, và rẻ hơn nhiều so với tính động
mỗi lần query.

**`buyer_role` có độ phủ thấp** — chỉ suy được cho khách có ghi nhu cầu (8.150 đơn). Đó là
cột phụ; `era` mới là chiều phân tích chính của khối này.

### 11.4 `dwh_reconcile` đã sửa

Lệnh giờ đối chiếu **tính đến mốc refresh** thay vì so với hiện tại, dùng bảng mới
`dwh.mart_refresh_log`. Nó in dòng `Đối chiếu tính đến: <mốc>` đầu output.

**Thứ tự bắt buộc: refresh TRƯỚC, reconcile SAU.** Nếu chưa từng refresh — hoặc thiếu bản
ghi log của bất kỳ mart nào — lệnh **từ chối chạy** và báo phải refresh trước. Từ chối trả
lời là đúng khi câu trả lời trung thực là "không biết dữ liệu mới đến đâu"; đưa ra một mốc
sai trong im lặng thì tệ hơn nhiều.

Mốc lấy là `started_at` chứ không phải `finished_at`: matview chụp ảnh dữ liệu tại thời
điểm BẮT ĐẦU, nên bản ghi sinh ra giữa lúc refresh chạy sẽ không lọt vào — đúng ca đã xảy
ra trên prod (§10.3).

**Lỗ hổng phát hiện ở fix wave sau đó (§11.5): "một phần coverage = không coverage" chỉ
đúng ở cold start.** Một khi mỗi mart đã log ít nhất một lần, quy tắc cũ (MIN trên dòng
MỚI NHẤT của TỪNG mart) không còn bảo vệ được gì — nếu job 04:25 chết nhiều đêm liên tiếp,
mỗi mart vẫn có "dòng mới nhất của riêng nó" từ nhiều đêm trước, MIN trên 3 dòng đó vẫn ra
một mốc "đủ coverage" nhưng đã cũ. Hai lớp vá:

- **`run_id`** (migration `0007`): mỗi `refresh_all_marts()` sinh MỘT `run_id`, đóng dấu
  lên mọi dòng nó ghi. `last_refresh_boundary()` chỉ chấp nhận nếu `run_id` MỚI NHẤT có đủ
  dòng `status='ok'` cho MỌI mart trong `MART_ORDER` — không được trộn dòng của các lần
  chạy khác nhau nữa.
- **`DWH_RECONCILE_MAX_BOUNDARY_AGE_HOURS`** (mặc định 36, xem §11.5): dù `run_id` hợp lệ,
  lượt chạy cố kết cuối cùng đó vẫn có thể CŨ — job chết nhiều đêm mà không có lượt mới nào
  để so sánh. `dwh_reconcile` từ chối chạy nếu mốc cũ hơn trần này.

`last_refresh_boundary()` cũng chặn hố mà chính migration `0006` đào ra: matview
`customer_360` bị DROP + CREATE lại `WITH NO DATA`, nhưng `mart_refresh_log` là bảng thật,
giữ nguyên dòng log 'ok' từ trước migration — hàm giờ đòi hỏi cả `relispopulated` (qua
`is_populated()`) cho mọi mart, không chỉ có dòng log. Thiếu điều kiện này, ngay sau
`migrate` lệnh sẽ đọc thẳng vào matview rỗng và ném `OperationalError` thô thay vì
`CommandError` rõ ràng.

### 11.5 Deploy khối 2A — runbook

Migration `0006` `DROP + CREATE ... WITH NO DATA` cho `customer_360`. Từ lúc code lên
môi trường đến lúc mart được nạp lại lần đầu, có một cửa sổ **API và reconcile đều hỏng
theo đúng thiết kế**:

- `GET /api/analytics/coverage` → 503 `MartNotReady` (xem `mart_queries.py`, đã có từ S2a).
- `dwh_reconcile` → `CommandError` "chưa refresh đủ" (§11.4 — `last_refresh_boundary()` trả
  `None` vì `customer_360` không `relispopulated`).

**Đây là hành vi ĐÚNG, không phải hồi quy** — thà từ chối rõ ràng còn hơn âm thầm trả số
sai. Nhưng nó có nghĩa: nếu deploy migration mà KHÔNG refresh ngay sau đó, endpoint và lệnh
đối chiếu hỏng liên tục cho tới lượt chạy 04:25 hôm sau — với người dùng cuối, "hỏng từ 10h
sáng tới 4h25 sáng hôm sau" trông y hệt một sự cố, dù về mặt kỹ thuật đó là "đang chờ lịch".

**Runbook bắt buộc — chạy migrate và refresh trong CÙNG một khung giờ vắng người, migrate
TRƯỚC refresh SAU:**

```bash
# 1. Áp migration (nhanh — matview tạo WITH NO DATA, không nạp dữ liệu ngay)
python manage.py migrate analytics

# 2. Nạp lại NGAY trong cùng khung giờ. Khối 1 đo 119s cho cả 3 mart (§8.2/§10.1)
#    — refresh_all_marts() lần này REFRESH THƯỜNG (không CONCURRENTLY) cho
#    customer_360 vì matview vừa WITH NO DATA, service tự chọn nhánh qua
#    pg_class.relispopulated (is_populated()).
python manage.py shell -c "from apps.analytics.application.mart_refresh_service import refresh_all_marts; print(refresh_all_marts())"

# 3. Xác nhận mart khớp nguồn trước khi coi deploy là xong
python manage.py dwh_reconcile
```

Deploy lúc 10:00 sáng mà KHÔNG chạy bước 2 ngay = `/api/analytics/coverage` và
`dwh_reconcile` hỏng liên tục **tới tận lượt chạy 04:25 sáng hôm sau** (job theo lịch tự
nạp lại). Đừng deploy migration này giữa giờ cao điểm rồi để đó.

**Setting mới cần biết khi deploy (fix wave §11.4):**

- `DWH_RECONCILE_MAX_BOUNDARY_AGE_HOURS` (mặc định `36`) — trần tuổi mốc refresh mà
  `dwh_reconcile` chấp nhận. Đã thêm ở `.env.example`, `docker-compose.local.yml`,
  `docker-compose.prod.yml`; không cần hành động gì thêm nếu dùng mặc định.
- Đổi tên `price_tier` → `price_tier_lifetime`, `purchase_rhythm` → `purchase_rhythm_lifetime`
  trên `dwh.customer_360` (xem §11.2, và "Đổi tên đã thực hiện" ở §9) — nằm TRONG migration
  `0006` (chưa deploy prod khi fix wave này chạy, chi phí đổi tên bằng 0). Khối 2B phải dùng
  tên mới ngay từ đầu khi lên API/FE.

---

## 12. Trang Khám phá dữ liệu — S3 (khối 2B, Task 4-6) — ĐÃ THAY THẾ

> ⚠️ **Mục này là hồ sơ lịch sử, không còn mô tả hệ thống hiện tại.** Trang hai tab bảng
> và xuất Excel mô tả bên dưới đã dựng xong, chạy trên prod, rồi **gỡ ngày 21/08/2026** và
> thay bằng màn Hồ sơ khách hàng 360 (§5.1). Giữ lại vì các số đo `EXPLAIN` và quyết định
> về mart trong đây vẫn đúng và vẫn được viện dẫn ở chỗ khác; đừng đọc nó như đặc tả của
> trang đang chạy.

### 12.1 Trang là gì, nằm ở đâu

`/reports/customer-explorer` (`frontend/src/pages/CustomerExplorerPage.tsx`) — hai tab
**Đơn hàng** / **Khách hàng** dùng chung một bộ lọc khoảng ngày, mỗi tab tự chọn cột hiển
thị (`ColumnManager`, lưu `localStorage`) và tự sắp xếp. Nguồn dữ liệu là `list_orders`/
`list_customers` (`apps/analytics/infrastructure/mart_queries.py`, đọc `dwh.fct_order_enriched`
/ `dwh.customer_360`), qua hai endpoint `GET /api/analytics/orders` và
`GET /api/analytics/customers`. Cấu trúc FE thật (khác bản nháp ở §5.1 — mục đó viết TRƯỚC
khi implement, xem ghi chú cuối §12.5):

```
frontend/src/features/customer-explorer/
  queries.ts                    # useOrderExplorer / useCustomerExplorer (react-query, có `enabled`)
  explorer-columns.ts           # ColumnOption[] + groups + DEFAULT_*_COLUMNS (cột hiển thị;
                                 # whitelist cột XUẤT nằm ở backend — _ORDER_EXPORT_COLUMNS/
                                 # _CUSTOMER_EXPORT_COLUMNS trong exports_definitions.py, §12.5)
  explorer-storage.ts           # localStorage cột/khoá/thứ tự theo kind
  useExplorerColumns.ts         # state ColumnManager dùng chung cho 2 tab
  components/{ExplorerTable,ExplorerFilterBar,ExplorerColumnPicker}.tsx
  __tests__/
frontend/src/pages/CustomerExplorerPage.tsx   # trang thật (không phải features/.../pages/)
```

### 12.2 Quy ước `sort` / `sort_dir` — MỚI của repo

Đây là trang **đầu tiên** trong repo có sắp xếp theo cột chạy **server-side** qua hai tham
số `sort` (tên cột) / `sort_dir` (`asc`/`desc`), thay vì client-side `Array.sort()` trên
dữ liệu trang hiện có (khuôn cũ ở các bảng khác) hoặc không cho sắp xếp gì cả. Whitelist cột
được phép sort nằm ở `apps/analytics/application/explorer_sort.py`
(`ORDER_SORT_COLUMNS`/`CUSTOMER_SORT_COLUMNS`) — cột ngoài whitelist bị server trả 400,
không âm thầm rơi về mặc định; FE mirror whitelist đó ở `OrderSortColumn`/`CustomerSortColumn`
(`frontend/src/lib/api/customer-explorer.ts`) nên chỉ header nào có `sortKey` khớp whitelist
mới bấm sắp xếp được.

**Trang sau nên đi theo quy ước này, không phát minh kiểu thứ ba.** Repo hiện có ít nhất
hai khuôn sort khác nhau ở các trang cũ (client-side trên trang hiện có, hoặc không có gì);
thêm một khuôn thứ ba (ví dụ sort qua `page`/`page_size` kiểu DRF pagination chuẩn, hoặc
sort đa cột) sẽ khiến FE phải tự hỏi "trang này dùng khuôn nào" mỗi lần — chi phí nhận thức
đó lớn hơn nhiều so với việc một khuôn không hoàn hảo nhưng nhất quán.

### 12.3 `build_order_by` luôn thêm tiebreaker duy nhất

`build_order_by()` (`apps/analytics/application/explorer_sort.py`) nhận `sort`, `sort_dir`,
whitelist, cột mặc định, và một **tiebreaker bắt buộc** (`f.order_id` cho tab Đơn hàng,
`c.customer_id` cho tab Khách hàng) — luôn nối tiebreaker đó vào cuối mệnh đề `ORDER BY`,
kể cả khi người dùng đã chọn sort theo cột khác.

**Lý do:** phân trang (`LIMIT`/`OFFSET`) trên một `ORDER BY` không duy nhất là không xác
định thứ tự giữa các dòng có giá trị bằng nhau ở cột sort — Postgres được phép trả chúng ở
bất kỳ thứ tự nào giữa hai lần gọi. Với cột như `status` hay `total_after_discount` (nhiều
đơn cùng giá trị), phân trang qua nhiều trang có thể **bỏ sót hoặc lặp lại dòng** giữa trang
1 và trang 2 — không phải lỗi hiếm, mà là hành vi mặc định của SQL không có tiebreaker.
Điều này đúng ở **cả bảng lẫn file xuất Excel**: `write()` của `_AnalyticsExplorerExport`
(§4.1, `apps/analytics/exports_definitions.py`) phân trang qua `list_orders`/`list_customers`
y hệt màn hình, dùng chung `build_order_by`, nên thiếu tiebreaker sẽ làm file xuất thiếu/lặp
dòng theo đúng cách bảng trên màn hình sẽ thiếu/lặp khi lật trang.

### 12.4 `era` mặc định `current`

Mặc định `CustomerEra = "current"` ở `CustomerExplorerPage` (không phải `"all"`/không lọc).
Lý do là số đo thật ở §11.1: **353.967 khách chỉ từng mua dòng cũ** (trước 2025), **52.985
khách chỉ mua dòng hiện tại** (máy massage, 2025+), **485 khách mua cả hai** — tỉ lệ chuyển
đổi ngành cũ sang sản phẩm hiện tại chỉ **0,14%**. Nếu mặc định không lọc (`era=all`), màn
hình mở ra sẽ hiện một bức tranh bị pha loãng bởi 87% người trong bảng **không còn là khách
hàng của những gì công ty đang bán hôm nay** — đúng lỗi đã ghi ở §11.1 cho phân khúc S1–S7.
`era` chỉ áp cho tab Khách hàng (`ExplorerFilterBar` chỉ hiện dropdown này khi tab đó active);
tab Đơn hàng không có khái niệm "thời kỳ" vì mỗi đơn tự thân đã thuộc về một thời kỳ cụ thể
qua sản phẩm nó chứa.

### 12.5 Nợ vận hành — NỔI BẬT

> **Quyền `customer-explorer:view` và `customer-explorer:export` là quyền MỚI, CHƯA role
> nào có.** Sau khi deploy, nếu không có ai chủ động gán hai quyền này cho role phù hợp,
> KHÔNG AI xem được trang (`:view` gác route ở `Guard section="customer-explorer"`,
> `frontend/src/app/routes.tsx:161`) và nút Xuất Excel sẽ luôn ẩn hoặc 403 (`:export`, gác ở
> `check_permission()` của `_AnalyticsExplorerExport`). Repo đã bị việc y hệt này cắn trước
> đây với `contacts:export` (MR !1253) — quyền mới tạo ra nhưng không gán role nào, tính
> năng coi như không tồn tại với người dùng cho tới khi có ai phát hiện ra và gán tay.
> **Việc cần làm ngay sau deploy S3: gán `customer-explorer:view`/`customer-explorer:export`
> cho (các) role phù hợp**, đừng đợi ai đó báo "sao tôi không thấy trang mới".

Các khoảng trống có chủ đích khác, để lại cho vòng sau:

- **Bộ lọc `channel`/`status`/`segment`/`recency_bucket` chưa dựng ở FE.** Backend
  (`OrdersExplorerExport.validate()`/`CustomersExplorerExport.validate()`,
  `apps/analytics/exports_definitions.py`) đã đọc được các tham số này, nhưng
  `ExplorerFilterBar` hiện chỉ có khoảng ngày (+ `era` cho tab Khách hàng) — người dùng
  chưa lọc được theo kênh, trạng thái đơn, phân khúc, hay nhóm "gần đây" ngay trên màn hình.
- **`schema.yaml` chưa regen** cho `GET /api/analytics/orders`/`GET /api/analytics/customers`
  — cùng khoản nợ có chủ đích đã ghi ở §8.4/§9 cho `/api/analytics/coverage`: file đang
  lệch sẵn, regen giữa chừng sẽ kéo nhiễu feature khác vào diff. FE khai type tay ở
  `frontend/src/lib/api/customer-explorer.ts`, phải tự đồng bộ tay với serializer backend
  cho tới khi có MR chore riêng regen toàn bộ.
- ~~File xuất KHÔNG theo cột đang ẩn/hiện trên màn hình~~ — **ĐÃ VÁ** (commit `a3057c92`,
  whole-branch review khối 2B, 2026-08-20). Nút Xuất Excel gửi kèm `columns` (danh sách cột
  đang hiện của tab, đúng thứ tự — `orderCols.orderedColumns`/`customerCols.orderedColumns`
  ở `CustomerExplorerPage.tsx`); `validate()` của cả hai `ExportDefinition` (§4.1) giờ ĐỌC
  khoá đó qua `_validate_columns_param`, kiểm theo whitelist `_ORDER_EXPORT_COLUMNS`/
  `_CUSTOMER_EXPORT_COLUMNS` (cố định ở backend — FE **không** có hằng số `*_EXPORT_COLUMNS`
  nào, xem ghi chú ở cây thư mục §12.1), và raise `ValidationError` nếu gặp key lạ. `write()`
  chỉ ghi đúng những cột đã validate theo đúng thứ tự client gửi — khớp màn hình. Hai trạng
  thái tham số `columns` được xử lý khác nhau CÓ CHỦ ĐÍCH: **vắng mặt** (job cũ, hoặc caller
  không gửi) fallback về trọn bộ cột mặc định; **list rỗng gửi tường minh** (bỏ hết cột trên
  UI rồi bấm Xuất Excel — `ColumnManager` không có ràng buộc tối thiểu-một-cột) bị từ chối
  bằng `ValidationError`, KHÔNG được coi là "xuất hết" — nếu không sẽ mở lại đúng lỗ hổng lộ
  `customer_id`/`staff_id` mà fix này vừa đóng, chỉ khác cửa vào (whole-branch review
  Finding 2).
- **Huỷ export giữa lúc đang ghi trang có thể báo FAILED thay vì CANCELLED.** `write()` của
  `_AnalyticsExplorerExport` bọc toàn bộ vòng lặp phân trang trong một transaction
  `REPEATABLE READ` (để tránh `REFRESH MATERIALIZED VIEW CONCURRENTLY` đổi dữ liệu giữa hai
  lần đọc trang — xem comment tại `write()`). Cái giá của fix đó: `progress()` — vừa cập
  nhật tiến độ vừa là điểm kiểm tra huỷ — chạy UPDATE cùng dòng `ExportJob` TRONG chính
  transaction đó. Nếu người dùng bấm Huỷ đúng lúc UPDATE `status=CANCELLED` từ session khác
  commit giữa hai lần gọi `progress()`, Postgres có thể ném lỗi serialization
  (`SQLSTATE 40001`) thay vì để job dừng êm qua `ExportCancelled` — job kết thúc ở trạng
  thái FAILED thay vì CANCELLED. Cửa sổ hẹp (phải trúng đúng lúc giữa hai trang, mỗi trang
  2.000 dòng) nhưng có thật và MỚI PHÁT SINH từ chính fix REPEATABLE READ này. Giải triệt để
  cần một connection Postgres thứ hai riêng cho `progress()`, tách khỏi transaction đọc mart
  — vượt phạm vi S3.

Ghi chú thêm: §5.1 (viết trước khi implement S3) mô tả một số chi tiết đã đổi khi thực làm —
params trên dây thật là `date_from`/`date_to`/`sort`/`sort_dir` (không có `page`/`page_size`,
dùng `limit`/`offset` như mọi endpoint phân trang khác trong repo), thư mục export không có
`pages/`/`OrdersTab`/`CustomersTab` riêng, và `validate()` của hai `ExportDefinition` đọc
tham số PHẲNG ở gốc `params` (không lồng trong khoá `filters` như mô tả ở §4.1) — xem
`apps/analytics/exports_definitions.py` là nguồn sự thật, không phải §4/§4.1/§5.1.

## 13. Triển khai — gỡ report khách hàng (`chore/drop-customer-report`)

Nhánh này xoá trang report khách hàng cũ (`report_doc/`, service layer, hai bảng cache
snapshot, task `warm_report_cache`) và migration đi kèm. Có migration DDL (drop bảng) +
đổi code Celery (bớt task, route) trong cùng đợt, nên **thứ tự roll ra prod bắt buộc phải
theo đúng trình tự dưới đây** — làm sai thứ tự không mất dữ liệu, nhưng tự dàn dựng ra một
sự cố giả mà người trực phải mất công điều tra lúc nửa đêm:

1. **Roll code trước** (image mới cho `desi-web`/`desi-worker`/`desi-worker-slow`/`desi-beat`),
   **CHƯA chạy migrate**.
2. **Restart `worker` VÀ `beat` CÙNG LÚC** (không tách rời hai bước này), rồi mới
3. **Chạy `migrate`** để drop bảng cache snapshot.

Vì sao đúng thứ tự này quan trọng — hai kịch bản tự chữa lành nhưng đều trông như sự cố:

- **Nếu `beat` restart SAU worker:** trong lúc `beat` cũ còn sống, nó vẫn enqueue
  `apps.customers.tasks.warm_report_cache` vào queue `slow` theo lịch cũ trong
  `CELERY_BEAT_SCHEDULE` — worker mới (đã bỏ import task này) trả `NotRegistered` cho từng
  message đó. Tự hết khi `beat` restart xong (không còn ai enqueue task đó nữa), nhưng
  `NotRegistered` xuất hiện dồn dập trên dashboard trong lúc gián đoạn thì trông y hệt lỗi
  triển khai thật.
- **Nếu migration chạy TRƯỚC khi worker roll xong:** một worker cũ vẫn đang chạy dở
  `rebuild_segment_snapshot` bước 2-4 (giờ đã xoá theo report cũ) sẽ ghi vào bảng vừa bị
  DROP → `PrecomputeStepsFailed` → alert `CeleryTaskFailing` nổ. Tự hết ở chu kỳ kế tiếp
  sau khi mọi worker đã lên code mới, nhưng một alert đỏ giữa cửa sổ deploy dễ bị hiểu nhầm
  là migration/code mới có bug.

Cả hai đều tự lành trong vòng một chu kỳ beat (xem `CELERY_BEAT_SCHEDULE`), không cần can
thiệp tay và không mất dữ liệu — nhưng biết trước thứ tự này thì người trực không phải dừng
lại suy luận từ một dashboard đỏ lúc 2h sáng.
