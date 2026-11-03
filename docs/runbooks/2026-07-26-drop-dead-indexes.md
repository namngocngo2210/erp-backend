# Runbook — Dọn index chết trên bảng `orders` (2026-07-26)

**Migration:** `orders.0109_drop_dead_indexes`
**Rollback:** `python manage.py migrate orders 0108`

## 1. Vì sao

`orders` là bảng **ghi nóng nhất** hệ thống: webhook KiotViet cập nhật từng đơn, cron
import/sync/reconcile ghi liên tục. Postgres phải maintain **mọi** index của bảng trên
từng INSERT/UPDATE, nên một index không ai đọc là chi phí ghi thuần tuý — và GIN là loại
đắt nhất khi ghi (mỗi row sinh nhiều khoá trigram).

Bảng đang gánh ~41 index (31 trong `Meta.indexes` + 2 unique constraint + 2 `db_index=True`
+ 5 index FK tự sinh + PK). Đợt này dọn 2 index đã **chứng minh** không có người dùng và
thêm 1 index đang thiếu.

## 2. Thay đổi trong đợt này

| Index | Hành động | Lý do |
|---|---|---|
| `order_product_trgm_idx` (GIN trigram trên `product`) | **DROP** | Query duy nhất trên cột là `product__icontains` → Postgres sinh `UPPER(product) LIKE UPPER(%s)`, biểu thức này do functional index `order_product_upper_trgm_idx` phục vụ. Index bare-column chỉ dùng được cho `__contains` (case-sensitive) — không có call site nào. |
| `order_channel_owner_idx` (B-tree trên `channel_owner`) | **DROP** | `channel_owner` chỉ xuất hiện ở phía ghi (gán khi tạo/upsert đơn) và trong serializer. Không có `filter(channel_owner=…)` nào trong `apps/`. |
| `order_ext_order_id_trgm_idx` (GIN trigram trên `external_order_id`) | **ADD** | `list_kiotviet_jobs` tra đơn sàn bằng `external_order_id__contains=q` (`LIKE '%q%'`) trong khi cột này **không có index nào** → seq-scan toàn bảng mỗi lần gõ ô tìm kiếm. |

### Đã cân nhắc drop nhưng GIỮ LẠI (có call site thật)

| Index | Vì sao giữ |
|---|---|
| `order_phone_trgm_idx` (GIN trên `phone`) | **Vẫn đang được dùng.** `apps/orders/repositories.py` và `apps/customers/customer_resale_service.py` đều chạy `Q(phone__contains=candidate)` trên cột `phone` **thô**, song song với `phone_normalized`. Đây là chủ ý của task C2: đơn có SĐT không chuẩn hoá được (ô nhập thiếu số) sẽ có `phone_normalized` rỗng và **biến mất khỏi tìm kiếm** nếu chỉ dựa vào cột chuẩn hoá. Drop index này = search `phone` quay về seq-scan. |
| `order_phone_idx` (B-tree trên `phone`) | **Vẫn đang được dùng.** `apps/orders/application/services/lead_filter_service.py` chạy `Order.objects.filter(phone__in=lookup, …)` ở **mỗi lần ingest lead** (chống trùng đơn trong ngày) — đây là đường ghi nóng, `IN (…)` cần B-tree. Ngoài ra `import_assessment_csv.py` dùng `Q(phone=phone)` exact. |

## 3. Đo trước khi quyết định drop tiếp

```bash
python manage.py report_index_usage --table orders
python manage.py report_index_usage --table orders --min-scans 100
```

(Command nằm ở `apps/orders/management/commands/report_index_usage.py`. Đặt trong
`apps.orders` chứ không phải `apps.common` vì Django chỉ nạp management command từ app
có trong `INSTALLED_APPS`, mà `apps.common` cố ý là package tiện ích thuần — không
models, không AppConfig — nên không được cài.)

SQL tương đương (chạy tay trên psql prod):

```sql
SELECT
    pg_postmaster_start_time()                     AS pg_started_at,
    now() - pg_postmaster_start_time()             AS stats_window;

SELECT
    s.indexrelname                                 AS index_name,
    s.idx_scan,
    pg_size_pretty(pg_relation_size(s.indexrelid)) AS size,
    i.indisvalid,
    i.indisunique
FROM pg_stat_user_indexes s
JOIN pg_index i ON i.indexrelid = s.indexrelid
WHERE s.relname = 'orders'
ORDER BY s.idx_scan ASC, pg_relation_size(s.indexrelid) DESC;
```

### Cách đọc `idx_scan` — ĐỌC KỸ

`idx_scan` là bộ đếm **tích luỹ**, bị **reset về 0** khi Postgres restart hoặc khi ai đó
gọi `pg_stat_reset()`. Vì vậy:

- **Luôn đọc `idx_scan` cùng `pg_postmaster_start_time()`.** `idx_scan = 0` trên instance
  vừa restart 10 phút trước không chứng minh được gì.
- **Ngưỡng kết luận: uptime ≥ 7 ngày.** Dưới mức đó, bộ đếm chưa chứng kiến đủ chu kỳ
  nghiệp vụ (báo cáo tuần, cron cuối tuần, đối soát cuối kỳ).
- **`idx_scan = 0` là điều kiện CẦN, không phải ĐỦ.** Vẫn phải grep call site trước khi
  drop — một index có thể chưa được dùng vì tính năng mới chưa lên, hoặc vì planner đang
  chọn nhầm plan do thống kê lệch.
- **Không bao giờ drop index `indisunique = true`** dù `idx_scan = 0`: nó đang thực thi
  ràng buộc toàn vẹn dữ liệu, không phải để tăng tốc đọc.

## 4. Danh sách "nghi nhưng CHƯA drop" — ✅ ĐÃ CHỐT 2026-08-17: GIỮ CẢ HAI

| Index | Nghi ngờ | Ngưỡng quyết định |
|---|---|---|
| `status_idx` (B-tree trên `status`) | ~90% đơn ở `status=3` → chọn lọc rất kém, planner nhiều khả năng bỏ qua để seq-scan. Các truy vấn theo status thực tế đã có index composite/partial phục vụ (`order_ch_st_cr_idx`, `ord_act_ch_st_cr_idx`, `ord_resale_delivered_upd_idx`). | Drop nếu sau **≥ 7 ngày uptime** mà `idx_scan < 1000` **và** không có call site `filter(status=…)` đứng một mình (không kèm channel/created_at). |
| `order_created_at_idx` (B-tree trên `created_at`) | Đã có `ord_act_cr_desc_idx` (partial `deleted_at IS NULL`, sắp xếp `-created_at`) phục vụ đúng đường đọc chính của trang danh sách; index full-table này có thể chỉ phục vụ các query hiếm không lọc `deleted_at`. | Drop nếu sau **≥ 7 ngày uptime** mà `idx_scan` **thấp hơn một bậc độ lớn** so với `ord_act_cr_desc_idx`. Kiểm tra thêm các job báo cáo/export chạy theo tháng trước khi quyết. |

### Kết quả đo — 2026-08-17

Điều kiện đo hợp lệ: `pg_postmaster_start_time()` = 2026-08-03 09:42 UTC, tức
**uptime 13,7 ngày** — vượt ngưỡng ≥ 7 ngày mà mục 3 yêu cầu. `pg_stat_database.stats_reset`
= `NULL` (chưa ai gọi `pg_stat_reset()`).

| Index | Ngưỡng | `idx_scan` thật | Kết luận |
|---|---|---:|---|
| `status_idx` | drop nếu `< 1000` | **4.648** | **GIỮ** — cao gấp 4,6× ngưỡng |
| `order_created_at_idx` | drop nếu thấp hơn `ord_act_cr_desc_idx` một bậc độ lớn | **986.477** (so với `ord_act_cr_desc_idx` = 340.992) | **GIỮ** — không thấp hơn mà **cao gấp 2,9 lần**, ngược hẳn giả thuyết |

`order_created_at_idx` đáng chú ý: giả thuyết ban đầu cho rằng `ord_act_cr_desc_idx`
(partial, `deleted_at IS NULL`) đã nuốt trọn đường đọc chính. Số liệu nói ngược lại —
index full-table được dùng **nhiều hơn** bản partial. Nhiều khả năng các job
báo cáo/export theo tháng (vốn cần cả đơn đã xoá mềm để đối soát) mới là hộ tiêu thụ
chính, chứ không phải trang danh sách.

**Cả hai được gạch khỏi danh sách nghi ngờ.** Đợt sau không cần đào lại hai index này
trừ khi đường đọc thay đổi.

### Đã xử lý ở đợt 2026-08-17 (bảng khác)

Xem `apps/audit/migrations/0005_drop_fk_index_declare_entity_id_index.py`:

- `audit_auditlog_user_id_c1cca96c` (24MB) — **DROP**. Index FK một cột Django tự sinh,
  bị `(user_id, timestamp DESC)` phủ hoàn toàn. Đây là dư thừa cấu trúc chứng minh được
  bằng lý thuyết B-tree, không phải suy từ `idx_scan`.
- `audit_entity_id_ts_idx` (83MB) — **GIỮ + khai báo lại**. Index tồn tại trên prod
  nhưng `git grep` toàn repo không ra dòng nào định nghĩa nó → tạo tay thẳng trên prod,
  DB dựng lại từ migration sẽ thiếu. Không drop vì `entity_id` là `filterset_fields`
  độc lập của `AuditLogViewSet`.

**Bài học bổ sung cho mục 3:** ngoài `idx_scan` và call site, còn phải kiểm tra
**index có tồn tại trong repo hay không**. Một index chỉ có trên prod sẽ không bao giờ
bị `makemigrations --check` phát hiện, và sẽ âm thầm biến mất ở môi trường dựng mới.
Câu lệnh soát drift:

```sql
-- Liệt kê index của một bảng, rồi đối chiếu với `git grep <tên>`
SELECT indexname FROM pg_indexes WHERE tablename = '<bảng>' ORDER BY 1;
```

## 5. Chạy migration trên prod

```bash
python manage.py migrate orders 0109
```

### ⚠️ Lưu ý về `CONCURRENTLY`

Migration này dùng `DROP/CREATE INDEX CONCURRENTLY` (và do đó `atomic = False`).

- **Không khoá bảng** — traffic ghi vẫn chạy bình thường trong lúc migrate. Đây là lý do
  chọn CONCURRENTLY: bản thường lấy `ACCESS EXCLUSIVE`, chặn mọi đọc-ghi trên `orders`.
- **Nhưng chạy LÂU** trên bảng lớn: `CREATE INDEX CONCURRENTLY` quét bảng **2 lượt** và
  phải chờ mọi transaction đang mở kết thúc. Trên `orders` cỡ hiện tại hãy dự trù **vài
  phút đến vài chục phút** cho index GIN mới. Chạy trong giờ thấp điểm.
- **Không chạy trong transaction.** Nếu tự chạy SQL tay, đừng bọc trong `BEGIN/COMMIT`.
- **Một transaction dài đang mở sẽ treo lệnh này vô hạn.** Kiểm tra trước:
  ```sql
  SELECT pid, state, now() - xact_start AS xact_age, query
  FROM pg_stat_activity
  WHERE xact_start IS NOT NULL AND now() - xact_start > interval '1 minute'
  ORDER BY xact_age DESC;
  ```

### ⚠️ Fail giữa chừng để lại index INVALID

Nếu `CREATE INDEX CONCURRENTLY` thất bại (timeout, deadlock, kill), Postgres **để lại một
index hỏng** ở trạng thái `indisvalid = false`. Index đó **không được planner dùng** nhưng
**vẫn bị maintain trên mỗi lần ghi** — tức là chỉ có hại. `DROP INDEX CONCURRENTLY` fail
cũng có thể để lại tình trạng tương tự.

**Phát hiện:**

```sql
SELECT i.indexrelid::regclass AS index_name,
       i.indrelid::regclass   AS table_name,
       pg_size_pretty(pg_relation_size(i.indexrelid)) AS size
FROM pg_index i
WHERE NOT i.indisvalid;
```

(`report_index_usage` cũng gắn cờ `INVALID` cho các index này.)

**Dọn:**

```sql
DROP INDEX CONCURRENTLY IF EXISTS <ten_index_invalid>;
```

Sau khi dọn, chạy lại migration. Các câu SQL trong `0109` đều dùng `IF EXISTS` /
`IF NOT EXISTS` nên **chạy lại được an toàn** (idempotent).

## 6. Rollback

```bash
python manage.py migrate orders 0108
```

`reverse_sql` tái tạo **đúng** định nghĩa cũ của cả hai index bị drop (gồm `gin_trgm_ops`
cho `order_product_trgm_idx`) và drop index mới thêm. Rollback cũng dùng `CONCURRENTLY` →
áp dụng nguyên các lưu ý ở mục 5, đặc biệt là thời gian chạy khi tái tạo GIN trigram trên
`product` (`TextField`, index đắt nhất trong nhóm).
