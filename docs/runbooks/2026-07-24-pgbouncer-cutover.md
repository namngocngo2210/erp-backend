# Runbook — Cutover sang PgBouncer

> Audit 2026-07-23 mục C2. **CHƯA THỰC HIỆN — cần validate staging trước.**
> Trạng thái code: service + settings đã sẵn sàng, **mặc định TẮT**
> (`profile: pgbouncer` + `DB_PGBOUNCER=0`). Không có gì đổi cho tới khi làm runbook này.
>
> **2026-07-30:** đường `.env` mới thực sự thông. Trước đó compose hardcode
> `POSTGRES_HOST: postgres` và KHÔNG whitelist `DB_PGBOUNCER` trong anchor env —
> bước 3 bên dưới đặt `.env` xong biến không bao giờ tới container, runbook này
> không thể thực hiện đúng. Compose giờ passthrough cả ba biến, và lệnh `migrate`
> trong command của web đã ghim nối thẳng Postgres (bẫy số 3 tự được xử lý).

## Vì sao

`max_connections=100` là trần cứng đang tới gần với lộ trình 100–150 nhân sự. Cách dập
lửa sự cố 2026-07-23 (`DJANGO_CONN_MAX_AGE=0` + `WS_HANDSHAKE_DB_CONCURRENCY=8`) đúng ở
thời điểm đó nhưng đánh đổi lâu dài: **mỗi request phải bắt tay TCP + auth Postgres lại
từ đầu**, và không tận dụng được prepared statement.

Transaction pooling gỡ cả hai vế: app mở/đóng connection thoải mái, Postgres chỉ thấy
~25 connection thật.

## ⚠️ Ba cái bẫy phải hiểu trước khi làm

### 1. Prepared statement của psycopg3 (bẫy nguy hiểm nhất)

psycopg3 tự chuyển sang **server-side prepared statement** sau 5 lần chạy cùng một câu
lệnh. Với `pool_mode=transaction`, `PREPARE` nằm ở connection server A còn `EXECUTE` có
thể rơi vào connection B → lỗi `prepared statement "_pg3_0" does not exist`.

Đặc tính khiến nó khó phát hiện: **không lỗi lúc khởi động, không lỗi khi test tay** —
chỉ nổ ngẫu nhiên khi đủ tải để pooler bắt đầu ghép nhiều client vào ít connection.

Đã xử lý sẵn trong `config/settings_parts/base.py`: `DB_PGBOUNCER=1` →
`OPTIONS["prepare_threshold"] = None`. **Bật pooler mà quên biến env này = lỗi ngẫu nhiên
trên prod.**

### 2. `statement_timeout` rò rỉ giữa các client

Hiện `statement_timeout=30s` được gửi qua startup parameter `options` — đó là setting
mức **session**. Ở transaction pooling, session server dùng chung nhiều client → timeout
của client này áp lên client khác, kể cả migration đang build index.

Đã xử lý: khi `DB_PGBOUNCER=1`, settings **không** gửi `options` nữa. Bù lại phải đặt
timeout ở tầng role (bước 4 bên dưới).

### 2b. Server-side cursor của `QuerySet.iterator()` (bổ sung 2026-08-04)

`QuerySet.iterator()` mặc định `DECLARE` một named cursor rồi `FETCH` nhiều lượt.
Ở autocommit, mỗi lượt là một transaction riêng → pooler đảo backend connection
giữa chừng → `cursor "_django_curs_..." does not exist`. Đã nổ thật khi chạy
`backfill_landing_confirmed_amount` (2026-08-04); các call site khác cùng rủi ro:
`autoclose_followups` (cron), `auto_sync_invoice_full`, export audit CSV,
`backfill_order_phone_normalized`.

Đã xử lý: `DB_PGBOUNCER=1` → `DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True`
(`config/settings_parts/base.py`). Hệ quả: `iterator()` kéo trọn kết quả về driver
một lần — job quét bảng lớn phải tự phân trang keyset theo `id` (mẫu:
`backfill_landing_confirmed_amount`).

### 3. Migration KHÔNG được đi qua pooler

`migrate` cần session dài, advisory lock, và đôi khi `CREATE INDEX CONCURRENTLY`.
Luôn chạy migration nối **thẳng** Postgres (`POSTGRES_HOST=postgres`, port 5432).
Từ 2026-07-30 lệnh `migrate` trong command của web/staging-web đã tự override
`POSTGRES_HOST=postgres DB_PGBOUNCER=0` — chạy migration THỦ CÔNG qua
`docker exec` thì vẫn phải tự nhớ override như vậy.

## Validate trên staging trước (BẮT BUỘC)

> **2026-08-03:** đã có checklist chi tiết từng phase với tiêu chí PASS/FAIL và
> kịch bản tái hiện burst gây sự cố 03/08 —
> `2026-08-03-pgbouncer-staging-validation.md`. Làm theo checklist đó; phần
> dưới đây giữ lại làm tóm tắt.

1. Bật ở staging:
   ```bash
   docker compose -f docker-compose.staging.yml --profile pgbouncer up -d pgbouncer
   ```
2. Đặt `DB_PGBOUNCER=1`, `DB_TARGET_HOST=pgbouncer`, `DB_TARGET_PORT=6432` trong
   `.env.staging`, **recreate** (không phải restart — đổi env phải recreate) web/worker.
   ⚠️ Là `DB_TARGET_*`, KHÔNG phải `POSTGRES_HOST/PORT` — xem cảnh báo ở bước 3 prod.
   `DJANGO_CONN_MAX_AGE` để trống: settings tự suy 600 khi có pooler.
3. Chạy full test suite qua pooler + tạo tải thật (mở song song nhiều tab, chạy
   report nặng, trigger sync KiotViet).
4. Xác nhận **không** có `does not exist` trong log:
   ```bash
   docker compose logs web worker | grep -i "prepared statement"
   ```
5. Xác nhận Postgres chỉ thấy ít connection:
   ```sql
   SELECT application_name, count(*) FROM pg_stat_activity GROUP BY 1 ORDER BY 2 DESC;
   ```

## Cutover prod

1. Bật service (chưa ai dùng — an toàn):
   ```bash
   docker compose -f docker-compose.prod.yml --profile pgbouncer up -d pgbouncer
   docker exec desi-pgbouncer pg_isready -h 127.0.0.1 -p 6432 -U desi
   ```

2. Đặt timeout ở tầng role (thay cho startup `options`):
   ```sql
   ALTER ROLE desi SET statement_timeout = '30s';
   -- Role riêng cho migration KHÔNG bị timeout (nếu chưa có thì tạo):
   ALTER ROLE desi_migrate SET statement_timeout = 0;
   ```

3. Trong `.env` prod (`DJANGO_CONN_MAX_AGE` để trống — settings tự suy 600):
   ```
   DB_PGBOUNCER=1
   DB_TARGET_HOST=pgbouncer
   DB_TARGET_PORT=6432
   ```

   ⚠️ **ĐỪNG đụng `POSTGRES_HOST`/`POSTGRES_PORT` trong `.env`.** Hai biến đó là
   góc nhìn của HOST (psql, script backup) và thường đang là `localhost`. Bản đầu
   của thay đổi này passthrough thẳng `${POSTGRES_HOST}` vào container và web chết
   ngay lúc boot với `connection to server at 127.0.0.1:5432 refused`
   (sự cố 2026-07-30 23:33). Compose chỉ đọc `DB_TARGET_*`; để trống thì container
   nối service `postgres:5432`.

4. Restart lần lượt, **theo dõi giữa mỗi bước** (đừng recreate nhiều service cùng lúc —
   xem `prod-compose-recreate-gotchas`):
   ```bash
   docker compose -f docker-compose.prod.yml up -d --no-deps worker
   # theo dõi 10 phút, rồi:
   docker compose -f docker-compose.prod.yml up -d --no-deps web
   ```

5. Theo dõi 24h:
   ```bash
   docker exec desi-pgbouncer psql -h 127.0.0.1 -p 6432 -U desi pgbouncer -c "SHOW POOLS;"
   docker compose logs --since 1h web worker | grep -i "prepared statement\|too many clients"
   ```
   `cl_waiting > 0` kéo dài → tăng `PGBOUNCER_DEFAULT_POOL_SIZE`.

## Rollback (nhanh, không mất dữ liệu)

Đổi lại `.env`: `DB_PGBOUNCER=0`, **xoá** `DB_TARGET_HOST`/`DB_TARGET_PORT` (bỏ trống
là compose tự dùng `postgres:5432`), `DJANGO_CONN_MAX_AGE` để trống → recreate
web/worker. PgBouncer chỉ là lớp trung gian, tắt đi là mọi thứ về đúng trạng thái
hiện tại.
