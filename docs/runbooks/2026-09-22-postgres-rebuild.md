# Runbook: Postgres prod dựng lại 22/09/2026 — những gì đã xảy ra và việc còn nợ

- Viết: 2026-09-23 (audit `docs/audit-2026-09-23-full-stack.md` N3, `docs/audit-2026-09-23/database.md` P0-1/P1-1).
- Ai đọc: người trực ops trước khi restore/đổi image Postgres, và người kế thừa muốn hiểu vì sao
  mọi counter `pg_stat_*` trên prod chỉ tính từ 22/09.
- Trạng thái: phần 1 là **bằng chứng đã xác minh chỉ bằng lệnh đọc** (không sửa gì trên prod).
  Phần 2 là **checklist chưa làm** — mỗi mục ghi rõ lệnh, ai chạy, rủi ro.

## 1. Đã xác minh (23/09/2026, chỉ đọc)

| Mục | Bằng chứng | Kết luận |
|---|---|---|
| Thời điểm dựng lại | `docker logs desi-postgres` mở đầu bằng `initdb` — `2026-09-22 06:21:54 UTC` (13:21 +07); `pg_postmaster_start_time()` = 06:21:55Z; container `Created` 06:21:53Z | Cluster MỚI, không phải restart. Volume `desi-order_pg_data` dựng lại từ dump. |
| Image | `docker inspect`: `public.ecr.aws/docker/library/postgres:16-alpine`; `SELECT version()` → `PostgreSQL 16.15 … x86_64-pc-linux-musl` | Đổi từ `postgres:16` (debian/glibc) sang alpine/musl. `docker-compose.prod.yml:334` đã khai alpine. |
| Locale/collation | initdb log: `sh: locale: not found` + `WARNING: no usable system locales were found`; `pg_database`: `datcollate=en_US.utf8`, `datlocprovider=c`, `datcollversion=NULL` | Tên locale ghi `en_US.utf8` nhưng libc musl **không có** locale đó → sort/so sánh chuỗi theo strcmp byte (UTF-8), KHÔNG theo quy tắc tiếng Anh/Việt như glibc. Index B-tree dựng lại từ dump nên tự nhất quán; chỉ **thứ tự ORDER BY chuỗi khác cluster cũ** (ví dụ chữ hoa xếp trước chữ thường, dấu tiếng Việt xếp theo mã điểm). |
| Checksum trang dữ liệu | initdb log: `Data page checksums are disabled`; `SHOW data_checksums` → `off` | Không phát hiện được hỏng đĩa âm thầm. Phải bật ở lần recreate kế (mục 2.3). |
| Role | `pg_roles`: chỉ còn `desi` (superuser). `claude_ro` (audit 08-19, `GRANT pg_read_all_stats`) **mất** | pg_dump không mang theo role (`pg_dumpall --roles-only` mới có). Mọi audit/đọc đang phải dùng superuser với `default_transaction_read_only=on`. |
| Backup | `/opt/desi-backup`: 2 file `desi_20260922-2251.dump.gz`, `desi_20260922-2253.dump.gz` (438MB, có `.sha256`), cùng host; metric textfile `desi_backup_offsite_configured 0`; `/etc/desi-backup.env` chỉ đặt `DESI_BACKUP_HEARTBEAT_URL`; host **không có** `rclone`/`aws` | Chưa có bản nào ngoài VPS. Cron `0 2 * * *` (`/etc/cron.d/desi-backup`) đang chạy. |
| Tham số PG | `pg_settings`: `max_wal_size=1GB`, `random_page_cost=4`, `effective_io_concurrency=1`, `track_io_timing=off`, `log_min_duration_statement=-1`, `log_lock_waits=off`, `shared_buffers=1GB`, `work_mem=16MB`, `max_connections=100` | 6 tham số đầu là mặc định; đã thêm vào `docker-compose.prod.yml` (23/09) — cần recreate mới ăn, xem `2026-09-23-audit-ops.md` mục Database. |
| Storage parameter | `pg_class.reloptions`: `orders` `{autovacuum_*=0.05,fillfactor=85}`, `customers` `{autovacuum_*=0.05}`, `order_assessments` rỗng | `ALTER TABLE … SET` đi theo dump → giữ được. `order_assessments` nay có migration `orders/0144`. |
| Kích thước | `pg_database_size('desi')` = 7130MB; 195 bảng user; top: audit_logs 2,23M dòng, customers 768k, order_items 730k, orders 562k | Dùng làm mốc để verify restore (mục 2.4). |
| Migration | `django_migrations`: có cả `orders.0001_initial` lẫn `orders.0001_squashed_0089_…` (146 dòng app orders) | Squash đã apply → 23/09 xoá 89 file gốc `0001..0089` trong repo (giữ `_squashed_src/` vì file squash import RunPython từ đó). |

## 2. Checklist còn nợ (thứ tự ưu tiên)

### 2.1 Tạo lại role đọc `claude_ro` (5 phút, không downtime)
```sql
-- chạy bằng: docker exec -it desi-postgres psql -U desi -d desi
CREATE ROLE claude_ro LOGIN PASSWORD '<đặt mật khẩu, ghi vào password manager>' NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT pg_read_all_stats, pg_read_all_data TO claude_ro;
ALTER ROLE claude_ro SET default_transaction_read_only = on;
ALTER ROLE claude_ro SET statement_timeout = '60s';
```
Kiểm: `psql -U claude_ro -d desi -c "SELECT count(*) FROM orders"` chạy được, `UPDATE` bị chặn.
Ghi thêm vào `scripts/` nào cần (vd `customer_monthly_report.py`) để không dùng superuser.
**Lưu ý dump/restore lần sau**: chạy thêm `pg_dumpall --roles-only` (hoặc lưu file SQL trên) —
`pg_dump -F c` KHÔNG mang role.

### 2.2 Bật backup offsite (30 phút, không downtime)
`scripts/desi-backup.sh` **đã hỗ trợ** offsite — chỉ thiếu cấu hình + công cụ:
1. Cài `rclone` trên host (`sudo apt install rclone` hoặc bản static), tạo remote tới storage
   KHÁC VPS (B2/S3/Wasabi/Drive…): `rclone config` dưới user `desi` → `/home/desi/.config/rclone/rclone.conf`
   (chmod 600). Bucket nên bật object-lock/versioning để ransomware không xoá được.
2. Thêm vào `/etc/desi-backup.env` (chmod 600, chủ `desi`):
   ```
   DESI_BACKUP_RCLONE_DEST=<remote>:<bucket>/desi-pg
   DESI_BACKUP_REQUIRE_OFFSITE=1
   ```
   (`DESI_BACKUP_S3_DEST` + aws cli là lựa chọn thay thế; `REQUIRE_OFFSITE=1` → thiếu đích thì
   backup FAIL thay vì im lặng chỉ ghi local.)
3. Chạy tay một lần: `. /etc/desi-backup.env && /usr/local/bin/desi-backup.sh`; xem log có
   `offsite (rclone) OK`; `rclone ls <remote>:<bucket>/desi-pg` thấy `.dump.gz` + `.sha256`.
4. Kiểm metric: `cat /var/lib/node_exporter/textfile/desi_backup.prom` → `desi_backup_offsite_configured 1`
   và có `desi_backup_offsite_last_success_timestamp`. Alert `BackupOffsiteNotConfigured` (monitoring) tắt.
5. Retention offsite: `rclone delete --min-age 30d <remote>:<bucket>/desi-pg` thêm vào cron riêng
   hoặc lifecycle rule của bucket.
6. Quý một lần: kéo một bản từ offsite về máy khác, `gunzip -t` + `pg_restore --list` + restore thử
   vào `desi-test-pg` (mục 2.4).

### 2.3 Bật data checksums ở lần recreate kế
Hai đường, chọn MỘT:
- **A. Không cần dump/restore** (khuyên dùng, PG ≥ 12): dừng Postgres rồi chạy `pg_checksums --enable`
  trực tiếp trên data dir. Đọc/ghi lại toàn bộ 7GB (~vài phút SSD). Downtime = thời gian đó.
  ```bash
  cd /home/desi/desi-order && docker compose -f docker-compose.prod.yml stop web worker beat postgres
  docker run --rm -v desi-order_pg_data:/var/lib/postgresql/data \
    public.ecr.aws/docker/library/postgres:16-alpine \
    sh -c 'pg_checksums --enable --progress -D /var/lib/postgresql/data'
  docker compose -f docker-compose.prod.yml up -d postgres && docker compose -f docker-compose.prod.yml up -d web worker beat
  docker exec desi-postgres psql -U desi -d desi -c "SHOW data_checksums"   # phải là on
  ```
  Chạy đúng image đang dùng (cùng bản PG) — pg_checksums từ chối cluster khác version.
- **B. Khi dump/restore vào volume mới** (vd đổi image ở 2.5): đặt trong compose
  `environment: POSTGRES_INITDB_ARGS: "--data-checksums"`. Chỉ có tác dụng khi data dir RỖNG
  (initdb) — đặt vào cluster đang có dữ liệu thì bị bỏ qua im lặng, đó là lý do lần 22/09 không có.
Sau khi bật, thêm alert từ `pg_stat_database.checksum_failures` (postgres-exporter đã scrape).

### 2.4 Verify restore: đếm dòng so với dump
Chạy **trên DB test** (`desi-test-pg`, không đụng prod), sau mỗi lần restore hoặc khi kiểm backup quý:
```bash
# 1) restore dump vào DB tạm trên container test
gunzip -c /opt/desi-backup/desi_YYYYMMDD-HHMM.dump.gz > /tmp/desi.dump
docker exec desi-test-pg psql -U desi_order -d postgres -c "DROP DATABASE IF EXISTS verify_db" -c "CREATE DATABASE verify_db"
docker exec -i desi-test-pg pg_restore -U desi_order -d verify_db --no-owner --no-privileges < /tmp/desi.dump
# 2) đếm dòng THẬT (count(*), không dùng n_live_tup ước lượng) ở hai bên rồi diff
Q="SELECT 'orders',count(*) FROM orders UNION ALL SELECT 'order_items',count(*) FROM order_items
   UNION ALL SELECT 'customers',count(*) FROM customers UNION ALL SELECT 'order_assessments',count(*) FROM order_assessments
   UNION ALL SELECT 'audit_logs',count(*) FROM audit_logs UNION ALL SELECT 'cnv_sync_records',count(*) FROM cnv_sync_records
   UNION ALL SELECT 'django_migrations',count(*) FROM django_migrations ORDER BY 1"
docker exec -e PGOPTIONS='-c default_transaction_read_only=on' desi-postgres psql -U desi -d desi -At -c "$Q" > /tmp/prod.txt
docker exec desi-test-pg psql -U desi_order -d verify_db -At -c "$Q" > /tmp/restore.txt
diff /tmp/prod.txt /tmp/restore.txt && echo "ROW COUNT OK"
# 3) so số object trong TOC với số bảng
gunzip -c /opt/desi-backup/desi_YYYYMMDD-HHMM.dump.gz | docker exec -i desi-test-pg pg_restore --list | grep -c "TABLE DATA"
```
Lệch được phép: dòng ghi giữa lúc dump và lúc đếm (bảng nóng orders/audit_logs). `django_migrations`
phải khớp tuyệt đối.

### 2.5 Quyết định image: giữ alpine hay về debian
KHÔNG tự đổi — ghi hai phương án để chốt trong cuộc họp ops:

| | Giữ `postgres:16-alpine` (hiện tại) | Về `postgres:16` (debian, như trước 22/09) |
|---|---|---|
| Collation | `datlocprovider=c`, sort theo byte. Mọi index đã dựng theo đó → **nhất quán**, không rủi ro corrupt. ORDER BY tên/chuỗi tiếng Việt khác cluster cũ (hoa trước thường). | glibc `en_US.utf8` thật → sort như cluster cũ. **Bắt buộc dump/restore** (không được copy volume: index B-tree collation khác = corrupt âm thầm). |
| Cách làm | Không làm gì. Muốn sort đúng ngôn ngữ ở một vài query thì dùng ICU collation cục bộ (`CREATE COLLATION vi (provider=icu, locale='vi')` — alpine có ICU) và `ORDER BY name COLLATE vi`. | pg_dump prod → volume mới với `POSTGRES_INITDB_ARGS="--data-checksums"` → restore → đổi `image:` trong compose + `docs/deploy-docker.md`. Downtime ≈ thời gian restore 7GB (~15-25 phút) + verify (2.4). Kết hợp luôn 2.3-B và 2.1. |
| Rủi ro | Ứng dụng sắp xếp bằng Postgres (danh sách sản phẩm/khách theo tên) khác thứ tự người dùng quen. Musl chậm hơn glibc ở vài path (strcoll không dùng vì locale C). | Thêm một cửa sổ downtime; phải làm lại mọi bước hậu restore (role, checksum, reloptions tự theo dump). |

Khuyến nghị của audit: **giữ alpine**, ghi nhận sort khác; chỉ về debian nếu nghiệp vụ báo thứ tự
sắp xếp sai. Dù chọn gì, lần dump/restore kế phải theo đúng thứ tự: dump + `pg_dumpall --roles-only`
→ initdb với checksums → restore → verify 2.4 → tạo role → kiểm reloptions → đọc lại runbook này.

## 3. Bài học để không lặp lại
- Dựng lại cluster prod mà không runbook = mất role, mất tham số, mất lịch sử counter, và audit
  sau phải suy luận từ `docker logs`. Bất kỳ thao tác nào chạm `pg_data` phải có một file trong
  `docs/runbooks/` TRƯỚC khi làm.
- `pg_dump` không phải backup toàn cluster: thiếu role, thiếu `postgresql.conf`/`ALTER SYSTEM`.
- Đổi image (glibc ↔ musl) là đổi collation → chỉ được đi qua dump/restore, không copy volume.
