# Runbook — Thu hồi bloat + gieo thống kê (2026-08-17)

**Migration đi kèm:** `audit.0005_drop_fk_index_declare_entity_id_index`,
`customers.0034_customers_fillfactor`
**Thay đổi code đi kèm:** whitelist header cho `curl_command`, `skip_unchanged` cho
full rebuild snapshot.

Runbook này là phần **vận hành tay trên prod** — migration không làm được, vì viết
lại bảng và gieo thống kê đều là thao tác cần chọn thời điểm và theo dõi.

---

## 0. Bối cảnh — số đo prod 2026-08-17

Database `desi`, PostgreSQL 16.14, `shared_buffers = 1GB`, tổng ~7GB.

| Bảng | heap | index | total | Vấn đề |
|---|---:|---:|---:|---|
| `audit_logs` | 3383 MB | 679 MB | 4120 MB | `curl_command` ~1,6GB; retention 90 ngày đã chạy đúng |
| `orders` | 714 MB | **1230 MB** | 1944 MB | 45 index; cache hit 79,15%, 529M block read |
| `customer_segment_snapshot` | **523 MB** | 422 MB | 945 MB | bloat 6,4× (dữ liệu thật ~81MB) |
| `customers` | 128 MB | **432 MB** | 560 MB | HOT chỉ 1,9% trên 16,6M update |

Thứ tự trong runbook này đi từ rẻ/an toàn đến đắt/cần cửa sổ bảo trì.

---

## 1. `ANALYZE` toàn schema — an toàn, làm bất cứ lúc nào

52 bảng chưa từng được `ANALYZE`. Nguyên nhân đã xác định: cấu hình để mặc định
`autovacuum_analyze_threshold = 50` + `scale_factor = 0.1`, nên bảng chưa tích đủ
50 thay đổi thì autovacuum **không bao giờ** chạm tới. Bảng lớn nhất trong nhóm
này chỉ có 34 dòng, nên **rủi ro hiện tại bằng không** — planner sẽ seq scan bất
kể có thống kê hay không. Chạy để sạch sổ và để lần sau còn theo dõi được:

```sql
ANALYZE VERBOSE;
```

Vài giây trên database 7GB. Không khoá bảng (chỉ lấy `SHARE UPDATE EXCLUSIVE`,
không chặn đọc/ghi thường).

Kiểm tra lại:

```sql
SELECT count(*) AS chua_analyze
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND c.reltuples < 0;
-- kỳ vọng: 0
```

> `reltuples = -1` (PG14+) nghĩa là **chưa từng ANALYZE**, KHÔNG phải 0 dòng. Đọc
> nhầm chỗ này sẽ tưởng 52 bảng đều rỗng. Muốn biết số dòng thật của nhóm đó thì
> đọc `pg_stat_user_tables.n_live_tup`.

Ba bảng đáng theo dõi về sau vì có tham gia join trong luồng chấm điểm đơn:
`operation_stages` (34 dòng), `operation_results` (12), `assessment_field_defs` (3).

---

## 2. `customer_segment_snapshot` — thu hồi ~440MB

### Vì sao có bloat

Kích thước một dòng thật: **77 byte** (`pg_column_size` trên mẫu 5%), cộng tuple
header ≈ 105 byte/dòng → 770k dòng đáng ra chỉ ~81MB. Thực tế heap **523MB**.

Số dòng thực ghi mỗi chu kỳ, đo ngày 16–17/08:

| Chu kỳ | Dòng ghi |
|---|---:|
| full rebuild (03:20 giờ VN, 1 lần/ngày) | **755.219** |
| incremental 21:00 | 2 |
| incremental 23:00 | 1 |
| incremental 01:00 | 7 |
| incremental 03:00 | 301 |

Đường tăng dần đã rất gọn. Toàn bộ dead tuple đến từ lần full ghi đè cả bảng để
đổi vài trăm dòng.

**Đã sửa ở tầng code** (`skip_unchanged=True` cho đường full) — từ nay full rebuild
chỉ ghi những dòng thật sự đổi. Nhưng phần đã phồng thì code không thu hồi được,
phải viết lại bảng.

### Thứ tự bắt buộc

1. **Deploy code trước.** Nếu repack trước mà code cũ vẫn chạy, lần full rebuild
   kế tiếp (03:20) sẽ phồng lại ngay.
2. Đợi qua **ít nhất một** chu kỳ full rebuild để xác nhận số dòng ghi đã giảm:

   ```sql
   SELECT date_trunc('hour', updated_at) AS gio, count(*) AS so_dong_ghi
   FROM customer_segment_snapshot
   WHERE updated_at > now() - interval '26 hours'
   GROUP BY 1 ORDER BY 1 DESC;
   -- kỳ vọng: hàng ứng với 20:00 UTC không còn ~755k mà xuống vài trăm
   ```
3. Repack.

### Cách repack

**Ưu tiên `pg_repack`** — không khoá bảng:

```bash
pg_repack -h <host> -U <user> -d desi -t customer_segment_snapshot --no-superuser-check
```

Cần extension `pg_repack` và ~2× dung lượng bảng trống trên đĩa (≈ 1,9GB cho bảng
945MB). Kiểm tra trước:

```sql
SELECT extname FROM pg_extension WHERE extname = 'pg_repack';
SELECT pg_size_pretty(pg_total_relation_size('customer_segment_snapshot'));
```

**Nếu không có `pg_repack`** thì `VACUUM FULL` — nhanh (bảng nhỏ) nhưng **lấy khoá
`ACCESS EXCLUSIVE`**, chặn mọi đọc/ghi trên bảng trong suốt thao tác:

```sql
VACUUM FULL VERBOSE customer_segment_snapshot;
```

⚠️ Bảng này được đọc bởi trang báo cáo khách hàng và job precompute. Chạy vào giờ
thấp điểm, và **tránh khung 03:20 giờ VN** (full rebuild) cùng nhịp 2h ở phút 0.
Dự trù vài chục giây.

Kiểm tra sau:

```sql
SELECT pg_size_pretty(pg_relation_size('customer_segment_snapshot')) AS heap,
       pg_size_pretty(pg_indexes_size('customer_segment_snapshot')) AS idx;
-- kỳ vọng heap: 523MB → ~81–120MB
```

---

## 3. `customers` — áp `fillfactor = 85` cho dữ liệu cũ

Migration `customers.0034` đã đặt `fillfactor = 85`, nhưng tham số đó **chỉ áp cho
page ghi từ đó trở đi**. Page cũ đã nhồi đầy 100% không tự giãn ra, nên tỷ lệ HOT
sẽ chỉ cải thiện dần theo tốc độ bảng tự viết lại. Muốn áp ngay:

```bash
pg_repack -h <host> -U <user> -d desi -t customers --no-superuser-check
```

hoặc `VACUUM FULL customers;` (bảng 560MB, khoá lâu hơn `customer_segment_snapshot`).

### Đo hiệu quả

`n_tup_hot_upd` là bộ đếm tích luỹ, nên so sánh phải lấy **hiệu** giữa hai mốc chứ
không đọc tỷ lệ tuyệt đối (tỷ lệ tuyệt đối bị 16,6M update cũ dìm xuống rất lâu):

```sql
-- Chạy trước repack, lưu lại kết quả; chạy lại sau 24h
SELECT relname, n_tup_upd, n_tup_hot_upd
FROM pg_stat_user_tables WHERE relname = 'customers';
```

Tỷ lệ HOT của riêng khoảng thời gian đó = `Δn_tup_hot_upd / Δn_tup_upd`. Mốc trước
khi sửa: **1,9%**. Kỳ vọng sau: vài chục phần trăm. Nếu vẫn thấp thì nguyên nhân
không phải fillfactor mà là **update đang chạm cột có index** — khi đó phải soi xem
đường ghi nào đụng vào 13 index của bảng, `fillfactor` không cứu được.

---

## 4. `audit_logs` — KHÔNG repack ở đợt này

Bảng 4,1GB, nhưng:

- **Retention 90 ngày đã chạy đúng.** `AUDIT_LOG_RETENTION_DAYS` mặc định 90
  (`config/settings_parts/base.py`), task `purge_old_audit_logs` xoá theo lô 10k
  có `single_flight`. Dòng cũ nhất trên prod là 2026-05-18 — đúng 91 ngày. Bảng
  đang ở **trạng thái ổn định**, không phình vô hạn.
- Dead tuple chỉ 3,6% — không có bloat đáng kể để thu hồi.
- Đây là bảng lớn nhất hệ thống; `VACUUM FULL` sẽ khoá rất lâu và `pg_repack` cần
  ~8GB đĩa trống.

Thay đổi code (whitelist header) chỉ tác động lên **dòng ghi mới**. Dung lượng sẽ
tự giảm dần khi retention cuốn hết dòng cũ — **hết hẳn sau 90 ngày**, khoảng
2026-11-15. Không cần làm gì thêm.

Theo dõi tiến độ:

```sql
SELECT to_char(date_trunc('month', "timestamp"), 'YYYY-MM') AS thang,
       count(*) AS rows,
       pg_size_pretty(avg(pg_column_size(curl_command))::bigint) AS curl_tb
FROM audit_logs GROUP BY 1 ORDER BY 1;
-- kỳ vọng: tháng sau khi deploy có curl_tb giảm mạnh so với 756 byte
```

Nếu muốn thu hồi sớm hơn thay vì đợi 90 ngày, phương án là **partition theo tháng**
để `DROP PARTITION` trả đĩa thẳng về OS — nhưng đó là thay đổi lớn hơn, nên tách
thành đợt riêng, không gộp vào đây.

---

## 5. Thứ tự khuyến nghị

| Bước | Việc | Khoá bảng | Khi nào |
|---|---|---|---|
| 1 | Deploy code + chạy 2 migration | Không (CONCURRENTLY) | Giờ thấp điểm |
| 2 | `ANALYZE VERBOSE` | Không | Ngay sau migrate |
| 3 | Xác nhận full rebuild đã giảm số dòng ghi | — | Sau ≥ 1 chu kỳ full (qua 03:20) |
| 4 | Repack `customer_segment_snapshot` | `pg_repack`: không / `VACUUM FULL`: có | Giờ thấp điểm, tránh 03:20 và phút 0 các giờ chẵn |
| 5 | Repack `customers` | như trên | Cửa sổ bảo trì riêng |
| 6 | Đo lại HOT sau 24h | — | Hôm sau |

## 6. Rollback

Cả hai migration đều có `reverse_sql` đầy đủ:

```bash
python manage.py migrate audit 0004
python manage.py migrate customers 0033
```

Lưu ý migration `audit.0005` dùng `CONCURRENTLY` (`atomic = False`) — áp dụng
nguyên các cảnh báo ở mục 5 của `2026-07-26-drop-dead-indexes.md`: không bọc trong
transaction, một transaction dài đang mở sẽ treo lệnh vô hạn, và fail giữa chừng
để lại index `indisvalid = false` phải dọn tay.

Thao tác repack **không có rollback** — nó chỉ viết lại bảng, không đổi dữ liệu.
Riêng `fillfactor` thì `migrate customers 0033` sẽ `RESET`, nhưng page đã viết lại
vẫn giữ nguyên bố cục cho tới lần repack sau.
