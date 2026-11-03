# Checklist validate staging — PgBouncer cutover

> Bản chi tiết hoá bước "Validate trên staging" của
> `2026-07-24-pgbouncer-cutover.md`, viết sau khi phá án sự cố 2026-08-03
> (burst 75 POST /api/orders trong 3 phút → ~90 connection idle của web đè kịch
> trần `max_connections=100`). Cutover không còn là "nên làm" mà là việc chặn
> tái diễn: burst buổi sáng đã nổ 3 lần trong 1 tuần (30/07, 31/07 ×2, 03/08).
>
> Mọi lệnh đã cắt <110 ký tự/dòng (terminal VPS vỡ dòng khi paste dài hơn).

## Trạng thái

| Phase | Nội dung | Trạng thái | Ghi chú |
|---|---|---|---|
| A | Bật pooler trên staging | ⬜ chưa chạy | |
| B | Chức năng qua pooler | ⬜ chưa chạy | |
| C | Tái hiện burst 2026-08-03 | ⬜ chưa chạy | |
| D | Prepared stmt + timeout role | ⬜ chưa chạy | |
| E | Diễn tập rollback | ⬜ chưa chạy | |
| GO/NO-GO | Quyết định cutover prod | ⬜ | cần A–E đều PASS |

Cập nhật bảng này sau mỗi phase (đúng quy ước theo dõi tiến độ trong docs/).

## Điều kiện tiên quyết

- [ ] Staging đang chạy và dump mới (`scripts/staging-refresh.sh` ≤ 7 ngày).
- [ ] `.env.staging` có `POSTGRES_PASSWORD` (pgbouncer service cần biến này).
- [ ] Code trên staging đã có gate `WEB_HTTP_CONCURRENCY` (MR của nhánh
      `fix/conn-storm-hardening`) — validate pooler + gate CÙNG NHAU, vì prod
      sẽ chạy cả hai.

```bash
cd ~/desi-order
export C="docker compose -f docker-compose.staging.yml --env-file .env.staging"
$C ps --format '{{.Name}} {{.Status}}'
```

## Phase A — Bật pooler

```bash
$C --profile pgbouncer up -d pgbouncer
$C exec pgbouncer pg_isready -h 127.0.0.1 -p 6432
```

Sửa `.env.staging` (KHÔNG đụng `POSTGRES_HOST`/`POSTGRES_PORT` — bẫy 2026-07-30):

```
DB_PGBOUNCER=1
DB_TARGET_HOST=pgbouncer
DB_TARGET_PORT=6432
```

Recreate (đổi env phải recreate, restart không ăn) — từng service một:

```bash
$C up -d --no-deps web
$C up -d --no-deps worker
$C up -d --no-deps beat
```

**PASS khi:** web/worker/beat boot sạch, log không có lỗi kết nối, và:

```bash
$C exec postgres psql -U desi -c "select application_name,count(*) from pg_stat_activity group by 1"
```

thấy connection của app đi qua user pooler (đếm theo `application_name` vẫn
đúng vì `DB_APPLICATION_NAME` truyền qua startup param được whitelist).

## Phase B — Chức năng qua pooler

- [ ] Login UI staging, mở danh sách đơn, tạo 1 đơn tay, sửa, huỷ.
- [ ] Chạy 1 report nặng (overview + ads report cùng lúc).
- [ ] Trigger sync KiotViet (mock mode) + xem tab realtime (WS) có event.
- [ ] `CONN_MAX_AGE` tự suy 600: xác nhận trong log web KHÔNG còn churn
      connect/disconnect mỗi request:

```bash
docker logs desi-staging-postgres 2>&1 | tail -200 | grep -c "connection authorized"
```

(chạy 2 lần cách nhau 1 phút — số tăng chậm hẳn so với trước là đúng.)

**PASS khi:** không lỗi chức năng, không traceback mới trong log web/worker.

## Phase C — Tái hiện burst 2026-08-03 (quan trọng nhất)

Kịch bản thật: ~90 request (75 POST /api/orders + GET phụ) trong ~120 giây,
đỉnh ~50 request trong 15 giây. Tái hiện bằng script bắn đồng thời (chạy NGAY
TRÊN VPS staging để bỏ qua latency mạng):

```bash
TOK=$(curl -s -X POST localhost:8100/api/users/token \
  -H 'Content-Type: application/json' \
  -d '{"username":"<user_staging>","password":"<pass>"}' | python3 -c \
  'import sys,json;print(json.load(sys.stdin)["access"])')
```

(port 8100 = staging web; sửa nếu khác. Script burst — lưu `/tmp/burst.sh`:)

```bash
#!/bin/sh
# 90 request đồng thời, phối POST tạo đơn + GET danh sách như log 03/08
i=0
while [ $i -lt 90 ]; do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -H "Authorization: Bearer $TOK" \
    "localhost:8100/api/orders?limit=50&offset=0" &
  i=$((i+1))
done
wait
```

Trong lúc chạy, ở terminal thứ hai theo dõi connection THẬT vào Postgres:

```bash
S='select state,count(*) from pg_stat_activity where usename=$$desi$$ group by 1'
while true; do $C exec -T postgres psql -U desi -c "$S"; sleep 2; done
```

**PASS khi (đủ CẢ BA):**
- [ ] Không một dòng `too many clients` trong log postgres/web/worker.
- [ ] Backend thật tới Postgres ≤ `DEFAULT_POOL_SIZE` (25) + reserve (5) suốt
      burst — dù client phía app là 90.
- [ ] Tỉ lệ 200 của burst ≥ 95% (503 chỉ được xuất hiện từ gate khi chờ quá
      `WEB_HTTP_QUEUE_TIMEOUT`, không phải lỗi 500).

**FAIL →** ghi lại `SHOW POOLS` (`cl_waiting`, `sv_active`) rồi mới chỉnh
`PGBOUNCER_DEFAULT_POOL_SIZE`; đừng chỉnh mò.

```bash
$C exec pgbouncer psql -h 127.0.0.1 -p 6432 -U desi pgbouncer -c "SHOW POOLS"
```

## Phase D — Prepared statement + timeout role

Bẫy nguy hiểm nhất (chi tiết trong runbook gốc): psycopg3 prepare sau 5 lần
lặp câu lệnh, chỉ nổ khi pooler ghép client dưới TẢI. Phase C chính là tải đó,
nên chạy Phase D **sau** Phase C:

```bash
$C logs web worker 2>&1 | grep -ci "prepared statement"
```

- [ ] Kết quả phải là `0`. Khác 0 = `prepare_threshold=None` không ăn — dừng,
      soi `DB_PGBOUNCER` đã tới container chưa (`$C exec web env | grep DB_`).
- [ ] Timeout áp ở tầng role (thay cho startup options đã bị bỏ khi qua pooler).
      Đọc `pg_roles.rolconfig` — ĐỪNG tin `SHOW statement_timeout` qua exec
      (bẫy tương tự `pg-settings-unix-socket-trap`):

```bash
$C exec postgres psql -U desi -c "select rolname,rolconfig from pg_roles where rolname like $$desi%$$"
```

Phải thấy `statement_timeout=30s` trên role `desi`. Chưa có thì chạy
`ALTER ROLE desi SET statement_timeout='30s'` trên STAGING rồi ghi chú để làm
y hệt ở bước 2 cutover prod.

- [ ] Migration nối thẳng: `$C run --rm --no-deps web sh -c` với
      `POSTGRES_HOST=postgres DB_PGBOUNCER=0 python manage.py migrate --check`
      (command web đã tự ghim, đây là double-check đường thủ công).

## Phase E — Diễn tập rollback

Làm trên staging để lúc prod có sự cố thì tay đã quen:

- [ ] Đảo `.env.staging`: `DB_PGBOUNCER=0`, xoá `DB_TARGET_HOST`/`DB_TARGET_PORT`.
- [ ] Recreate web/worker/beat từng cái (`up -d --no-deps <svc>`).
- [ ] App sống lại, không traceback, đo thời gian thao tác (kỳ vọng < 5 phút).
- [ ] Bật lại pooler (lặp Phase A) — trạng thái cuối của staging là ĐANG QUA
      POOLER, chạy tiếp vài ngày để soak.

## GO/NO-GO cutover prod

GO khi: A–E PASS + staging soak ≥ 48h không có `prepared statement` /
`too many clients` / lỗi 500 bất thường trong log. Sau đó theo đúng phần
"Cutover prod" của `2026-07-24-pgbouncer-cutover.md`, kèm 2 lưu ý mới:

1. Alert `PostgresConnectionSpike` đã live từ 03/08 — sau cutover, sửa ngưỡng
   theo pool (25) thay vì max_connections nếu cần độ nhạy.
2. Nhớ bẫy bind-mount: sửa file `monitoring/` xong phải force-recreate
   prometheus (`--no-deps`), reload/restart KHÔNG ăn file mới.
