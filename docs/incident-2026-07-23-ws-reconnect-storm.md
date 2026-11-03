# Sự cố 2026-07-23 — bão reconnect WebSocket làm cạn connection Postgres

**Triệu chứng:** `/api/orders/reports/ads` trả 500 lúc 14:43:24 (+07) với
`psycopg.OperationalError: FATAL: sorry, too many clients already`. Traceback chết ở
`perform_authentication` → `get_user`, tức là pool đã cạn *trước khi* chạm business logic.

**Thời lượng:** ~2 phút (14:42:43 → ~14:45). **162** FATAL. Tự phục hồi, không ai can thiệp.

**Phạm vi:** mọi request HTTP trong cửa sổ đó, không riêng endpoint ads.

---

## 1. Bằng chứng

| Nguồn | Số liệu |
|---|---|
| `sum(pg_stat_activity_count)` | Baseline cả buổi sáng **2–5**; 07:43:00Z vọt **99**, 07:44:00Z còn 67, 07:45:00Z về **3** |
| Phân rã theo state | `desi/idle` = 94, 96, 97, 95, 65 — **active chỉ đạt 4** |
| `container_oom_events_total` | **Rỗng** trong 24h — không có OOM |
| `pg_settings` | `tcp_keepalives_*` và `idle_in_transaction_session_timeout` đều `source = command line` — lưới an toàn của sự cố 2026-07-21 còn nguyên |
| `docker network inspect` | Chỉ có container desi-order; không có hệ lạ đấu vào Postgres |
| Log `desi-web` | `Worker exiting (pid: 29)` lúc 14:42:12, worker mới boot 14:42:14 — **chỉ 1 trong 3 worker**, đúng chữ ký `--max-requests`, không phải deploy hay crash |

Điểm chốt: FATAL đầu tiên lúc 14:42:43, **cách thời điểm worker chết đúng 29 giây**. Backoff
cũ cộng dồn 1+2+4+8+16 = **31 giây**.

## 2. Chuỗi nguyên nhân

1. Gunicorn recycle một UvicornWorker sau 1000 request (`--max-requests`). Mọi WebSocket
   bám worker đó **đứt cùng một giây**.
2. Frontend reconnect bằng `1000 * 2^(n-1)` — exponential backoff **không jitter**. Toàn bộ
   client rớt cùng lúc nên đi qua từng bậc backoff đồng bộ tuyệt đối, và cùng đập vào lần
   thử thứ 5 tại mốc t+31s.
3. Mỗi lần bắt tay WS chạm DB hai lần: `_get_user` (xác thực JWT ở middleware) và
   `_load_scope` (scope RBAC ở consumer).
4. Channels chạy **mỗi WS connection trong ThreadSensitiveContext riêng** → mỗi connection
   một thread → **một DB connection**. Đây là đường duy nhất trong stack mà số connection
   tỉ lệ với số client: HTTP bị `thread_sensitive=True` ép về 1 view/process (trần 3), Celery
   trần 8.
5. ~96 handshake đồng thời tranh 1.5 CPU của `desi-web`: connection đã mở nhưng nằm chờ tới
   lượt → trạng thái `idle` (khớp: idle 94–97, active ≤ 4). Chạm `max_connections=100`.
6. Backoff giãn dần + `maxReconnectAttempts=5` → bão tan sau ~75 giây.

**Tại sao baseline chỉ 2–3:** `database_sync_to_async` đóng connection sạch sau mỗi thao tác
(`CONN_MAX_AGE=0`), nên WS ở trạng thái ổn định không giữ connection nào. Chỉ khoảnh khắc
bắt tay đồng loạt mới bùng.

**Tại sao không ai được cảnh báo:** `PostgresTooManyConnections` đặt `for: 5m`,
`PostgresConnectionLeak` đặt `for: 15m`. Bão dài 2 phút nên cả hai đều im lặng.

**Tại sao điều tra mất 4 vòng lấy bằng chứng:** `pg_stat_activity.application_name` rỗng ở
mọi connection ứng dụng — không phân biệt được web, worker, hay tiến trình ngoài.

## 3. Bản vá

| Lớp | Thay đổi | Vai trò |
|---|---|---|
| Gốc | `computeReconnectDelay()` — full jitter trong `[250ms, trần bậc hiện tại]`, trần 30s (`frontend/src/services/websocket.ts`) | Phá đồng bộ hoá. 100 client giờ trải trên ~16s thay vì dồn một mili-giây |
| Hỏng âm thầm | Bỏ `maxReconnectAttempts = 5`, thử lại không giới hạn (độ giãn đã bị trần 30s chặn) | Trước đây mọi gián đoạn quá ~31 giây — đúng bằng sự cố này — làm client tắt realtime vĩnh viễn cho tới khi user tự F5, không có dấu hiệu nào trên giao diện |
| Lưới an toàn | `handshake_db_gate()` — semaphore chặn số truy vấn DB đồng thời của handshake (`backend/apps/channels_core/handshake.py`), mặc định 8/worker | Chặn phía server, **không phụ thuộc client**. Tab đang mở còn chạy bundle cũ nhiều ngày sau deploy, và reload nginx / mạng chập đều tái tạo đúng kịch bản |
| Ngòi nổ | `WEB_MAX_REQUESTS` mặc định `0` (tắt) | `--max-requests` sinh cho gunicorn sync worker; với UvicornWorker nó cắt đứt mọi WS client mỗi 1000 request. Rò rỉ RAM đã có `mem_limit` + alert `ContainerOOMKill` lo |
| Chẩn đoán | `application_name` trong `DATABASES.OPTIONS`, đặt riêng từng service qua compose | Lần sau `pg_stat_activity` tự khai ai là ai |
| Giám sát | `PostgresTooManyConnections` hạ `for: 5m` → `1m` | Bão 2 phút không còn vô hình |

Trần connection sau bản vá: web handshake `8 × 3 = 24` + HTTP `3` + Celery `8` + beat `1`
+ exporter `~3` ≈ **39/100**.

## 4. Đã cân nhắc và loại

- **Nâng `max_connections`** — chữa triệu chứng. Số tab tăng theo nhân sự (50 → 100–150),
  bão sẽ đuổi kịp mọi ngưỡng.
- **PgBouncer transaction-pooling** — đúng hướng dài hạn (100 client → ~20 server connection)
  nhưng là thay đổi hạ tầng, không nên gộp vào bản vá sự cố. Xem mục 5.

## 5. Việc còn lại (không nằm trong MR này)

1. **PgBouncer** trước Postgres.
2. **`.env` prod lệch compose:** `CELERY_WORKER_MAX_MEMORY_PER_CHILD=100000` trong khi
   compose mặc định `180000`. Không phải nguyên nhân sự cố (ngưỡng thấp hơn = recycle sớm
   hơn = an toàn hơn) nhưng cho thấy `.env` prod chưa cập nhật theo commit `350dfbda`.
3. **2 test đỏ sẵn trên master:** `test_rbac_resolver.py::test_system_role_short_circuits_to_full`
   và `test_role_admin_api.py::test_cannot_edit_system_role` (seed role `Administrator` đụng
   fixture). Không liên quan sự cố.

## 6. Triển khai

`WS_HANDSHAKE_DB_CONCURRENCY` và `DB_APPLICATION_NAME` nằm trong anchor env — là
**whitelist**, nên phải recreate container mới có hiệu lực. Theo runbook: recreate **tuần tự
từng service** (`stop` → `rm -f` → `up -d` → chờ healthy), beat làm cuối.

```bash
git pull origin master
docker compose -f docker-compose.prod.yml up -d --build frontend   # bản vá jitter
for s in web worker worker-kiotviet beat; do
  docker compose -f docker-compose.prod.yml stop $s
  docker compose -f docker-compose.prod.yml rm -f $s
  docker compose -f docker-compose.prod.yml up -d $s
done
docker exec desi-prometheus kill -HUP 1    # nạp lại alerts.yml
```

Kiểm chứng sau khi lên — `application_name` phải hiện tên service:

```bash
docker exec desi-postgres psql -U desi -d desi -c "
SELECT application_name, state, count(*) FROM pg_stat_activity
WHERE backend_type = 'client backend' GROUP BY 1,2 ORDER BY 3 DESC;"
```
