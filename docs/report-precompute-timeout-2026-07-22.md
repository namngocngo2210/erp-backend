# Sự cố statement timeout ở cron precompute report khách hàng (2026-07-22)

Trạng thái: **Sprint 1 hoàn thành** — chờ deploy và xác nhận trên prod.

## 1. Triệu chứng

Log prod 16:06:33 và 16:07:06 ngày 2026-07-22:

```
django.db.utils.OperationalError: canceling statement due to statement timeout
psycopg.errors.QueryCanceled: canceling statement due to statement timeout
[2450] ERROR:  canceling statement due to statement timeout
```

Cùng backend PID Postgres `2450` ở cả hai lần, cách nhau 33 giây.

## 2. Quá trình điều tra

Dòng thời gian đầy đủ lấy từ Loki (`{container="desi-worker"}`):

| Thời điểm | Sự kiện | Thời lượng |
|---|---|---|
| 16:00:00 | `rebuild_segment_snapshot` received | |
| 16:04:28 | segment snapshot rebuilt: 749,846 rows | 268s |
| 16:04:33 | cohort retention snapshot rebuilt | 5s |
| 16:06:33 | **trend snapshot FAILED** | 120s (chạm `REPORT_QUERY_TIMEOUT_SECONDS`) |
| 16:06:36 | channel quality snapshot rebuilt | 3s |
| 16:07:06 | **customer type reconcile FAILED** | 30s (chạm `DB_STATEMENT_TIMEOUT_MS`) |
| 16:07:06 | Task **succeeded** in 426.25s | |

Cùng `ForkPoolWorker-591` → cùng process → cùng connection, giải thích trùng PID 2450.

### Các giả thuyết đã bị bác bỏ

Ghi lại để lần sau không mất công điều tra lại:

- **Planner có số dòng sai.** `pg_stat_user_tables.n_live_tup` báo `orders` có 1,840 dòng
  (thực tế 542,496), nhưng đó chỉ là bộ đếm quan sát. `pg_class.reltuples` = 540,426 —
  planner có số đúng.
- **Thiếu index.** Bộ lọc GTC khớp 482,213/542,496 dòng (89% bảng), Seq Scan là lựa chọn
  đúng của planner. Index không giúp ở độ chọn lọc đó.
- **Có gì đó chặn vacuum.** Không có replication slot, không có prepared xact, transaction
  dài nhất 0.1s.
- **Postgres crash.** `exit=0, restarts=0, oom=false` — khởi động sạch do deploy.

### Nguyên nhân gốc

Hai lỗi, **hai nguyên nhân khác nhau**:

| Bước | Nguyên nhân | Bằng chứng |
|---|---|---|
| `customer type reconcile` | Cache nguội sau deploy + thiếu guard | 2.3s khi cache ấm, chết ở 30s khi nguội |
| `trend snapshot` | **Cấu trúc query** | >60s cả khi cache đã ấm và đã `VACUUM ANALYZE` |

Postgres khởi động 15:42 (deploy), worker 15:44, cron nổ 16:00 — chỉ 18 phút sau, khi
`shared_buffers` (1GB) còn trống trơn trong khi riêng `orders` đã 1,962MB.

Với `trend snapshot`, cache nguội chỉ là thứ làm lộ ra sớm; query vốn đã vượt trần 120s
từ trước nhưng không ai biết vì task luôn báo `succeeded`.

## 3. Các thay đổi

### 3.1. Viết lại query trends — `apps/customers/report_trends.py`

Đo trên prod: **>60s → 3.2s**.

- Gộp `gtc` và `first_orders` thành một CTE `base` duy nhất → quét `orders` một lần thay
  vì hai lần (mỗi lần ~406MB đọc đĩa).
- Thay subquery tương quan
  `count(DISTINCT ...) FILTER (WHERE customer_id IN (SELECT ... WHERE first_m = g.m))`
  bằng CTE `new_per_month` + `LEFT JOIN`. Đây là chỗ chiếm gần như toàn bộ thời gian:
  planner giờ đẩy được thành `Filter: min(base.m) >= ...` ngay trong `HashAggregate`.
- Bỏ `GROUP BY g.m, rpm.repeat_buyers` (gộp nhóm theo giá trị đến từ join).

Ngữ nghĩa giữ nguyên: khách có `first_m = M` thì hiển nhiên có đơn trong tháng M nên hai
tập trùng khít. `first_orders` **cố ý** không lọc theo cửa sổ tháng — mốc "khách mới"
phải tính trên toàn bộ lịch sử.

Khoá bằng 5 test mới trong `TrendsRewriteSemanticsTests`, viết và chạy xanh trên code cũ
TRƯỚC khi sửa.

**Đã cân nhắc và loại bỏ:** thêm index phủ `INCLUDE (total_after_discount, invoice_status,
branch_id)`. Seq scan chỉ còn 1.4s trong tổng 3.2s, không đáng thêm index thứ 26 vào bảng
đã mang 1.2GB index trên 714MB dữ liệu.

### 3.2. Guard cho reconcile — `apps/customers/customer_type.py`

`reconcile_customer_type` là bước **duy nhất trong 5 bước** không có `statement_timeout`,
nên rơi xuống trần toàn cục 30s trong khi 4 bước anh em được 120s. Nay dùng chung
`REPORT_QUERY_TIMEOUT_SECONDS`.

### 3.3. Bịt điểm mù — `apps/customers/tasks.py`

Đây là vấn đề nghiêm trọng nhất: sự cố hạ tầng tự khỏi ở chu kỳ sau, điểm mù quan sát thì
không.

`_rebuild_report` nuốt mọi exception, task trả về số dòng của bước đầu → Celery ghi nhận
`succeeded` kể cả khi 2/5 bước chết. Không alert nào nổ, dashboard xu hướng đứng số, drift
`is_new_customer` tích tụ.

- `_rebuild_report` trả về `bool`.
- Task gom các bước hỏng, ném `PrecomputeStepsFailed` **sau khi đã chạy hết** — giữ nguyên
  tính chất "một report chậm không chặn report khác".
- Trả về `dict` summary thay vì `int` khi thành công.
- Thêm rule `CeleryTaskFailing` vào `monitoring/alerts.yml` (dùng `celery-exporter` sẵn có,
  không cần metric mới).

### 3.4. Ngưỡng autovacuum — migration `orders.0107`, `customers.0032`

`orders` **chưa từng được autovacuum/autoanalyze** (18,309 dead tuple); `customers` có
136,566 dead tuple. Với `scale_factor` mặc định 0.2, `orders` phải tích ~108k dead tuple
mới được chạm tới.

Hệ quả đo được trong plan: `Index Only Scan` trên `customers_customer_pkey` phải xuống heap
**705,528/750,729 dòng** vì visibility map cũ — mất sạch ý nghĩa index-only.

Hạ xuống 0.05 cho cả hai bảng. `ALTER TABLE ... SET (storage parameter)` chỉ lấy
`SHARE UPDATE EXCLUSIVE`, an toàn trên prod.

## 4. Việc đã làm thủ công trên prod

`VACUUM (ANALYZE) orders;` và `VACUUM (ANALYZE) customers;` — đã chạy 2026-07-22 để dọn nợ
tồn. Migration ở 3.4 lo phần duy trì về sau.

## 5. Chưa làm

- **Chưa nâng `shared_buffers`.** Đang 1GB trong `mem_limit: 2500m`, trong khi `orders`
  2GB — EXPLAIN cho thấy 57% số page vẫn đọc từ đĩa cả khi cache đã ấm. Nâng lên là đánh
  đổi với rủi ro OOM mà VPS này đã từng gặp. Sau khi trends về 3.2s thì áp lực không còn
  cấp bách; để lại như một quyết định hạ tầng riêng.
- **Chưa dời lịch beat khỏi phút 0.** Deploy rơi trúng cửa sổ 18 phút trước cron là ngẫu
  nhiên; sau khi query nhanh hơn ~37 lần thì biên an toàn đã đủ rộng. Nếu tái diễn thì cân
  nhắc hoãn precompute nặng khi Postgres uptime còn thấp.
- **`SegmentSnapshotService.rebuild()` mất 268s** — chiếm 63% thời lượng cả task, chưa đụng
  tới. Là ứng viên tối ưu tiếp theo nếu chu kỳ 2h trở nên chật.

## 6. Cách xác nhận sau deploy

1. Chu kỳ beat chẵn giờ tiếp theo phải không còn dòng `rebuild failed` trong log worker.
2. `docker logs desi-worker | grep "Task apps.customers.tasks.rebuild_segment_snapshot"` —
   thời lượng phải giảm rõ (bỏ 120s + 30s chết vô ích).
3. Sau ~1 ngày: `SELECT relname, last_autovacuum, last_autoanalyze FROM pg_stat_user_tables
   WHERE relname IN ('orders','customers');` phải có giá trị, không còn NULL.
