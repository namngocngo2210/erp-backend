# Rà soát connection Postgres — vá đường khuếch đại broadcast (2026-07-29)

**Bối cảnh:** sự cố `FATAL: sorry, too many clients already` lúc 10:26 ngày 29/07/2026
trên `PATCH /api/orders/549426`. Đây là lần thứ **ba** cùng một triệu chứng
(21/07, 23/07, 29/07). Hai lần trước đều vá đúng nguyên nhân *của lần đó* nhưng
không lần nào hỏi câu tổng quát: **còn đường nào khác mà số connection tỉ lệ với
số client?**

Tài liệu này trả lời câu đó, vá đường vừa tìm được, và siết các lớp phòng thủ
xung quanh để lần sau có sự cố thì phát hiện được ngay.

---

## 1. Phát hiện

### F1 — Broadcast fan-out chạm DB theo từng client (nghiêm trọng, chưa từng vá)

`CRMConsumer.crm_event` chạy trên **mọi** client trong group `crm_updates` mỗi khi
có một order event. Bên trong, `_is_authorized` (`consumers.py:52-78`) chạm DB:

| Scope | Hàm gọi | Chạm DB? |
|---|---|---|
| `all` | — | Không |
| `own` | `_order_belongs_to_user()` | **Luôn luôn** |
| `channels` | `_resolve_order_channel_code()` | Khi payload thiếu `channel_code` |

Với `CONN_MAX_AGE=0` ở prod, mỗi `database_sync_to_async` mở một connection mới:

```
1 event  →  N query đồng thời  →  N connection      (N = số WS client)
```

Và **không có gate** ở đường này — `handshake_db_gate` chỉ bảo vệ lúc bắt tay
(`consumers.py:25`, `jwt_middleware.py:50`).

Biên bản 23/07 mục 2.4 viết *"Đây là đường duy nhất trong stack mà số connection
tỉ lệ với số client"*. Kết luận đó **thiếu**, và đường bị bỏ sót lại nguy hiểm hơn:
handshake chỉ xảy ra khi reconnect, còn broadcast xảy ra mỗi lần có người sửa đơn —
tức là liên tục suốt giờ làm việc.

**Chỗ khuếch đại mạnh nhất** — `order_crud_view.py:406-412`, bulk tạo vận đơn KShip:

```python
for order_id in [job.order_id for job in created_jobs]:
    OrderService.push_event(
        OrderEventContract.build_order_updated(
            order_id=order_id,
            source="orders.views.create_kship_delivery_queued",
        )   # ← thiếu channel_code
    )
```

`channel_code` là optional và chỉ bắt buộc cho `order.created`
(`order_events.py:111-112`). Chọn 50 đơn → 50 event → **50 × N query đồng thời**.

### F2 — `CONN_MAX_AGE` mặc định 600 trong code

`base.py:154` — `int(os.getenv("DJANGO_CONN_MAX_AGE", "600"))`. An toàn của prod phụ
thuộc hoàn toàn vào compose ép `:-0`. Sót biến trong anchor whitelist, chạy container
ngoài compose, hay copy nhầm `.env` đều cho ra 600 — với ASGI + Channels đó là kịch
bản cạn connection, và không có guard nào chặn.

Giá trị đúng suy được từ chính hệ thống: không pooler → 0; `DB_PGBOUNCER=1` → 600.

### F3 — Alert connection Postgres CHƯA BAO GIỜ bắn (đã chứng minh)

`alerts.yml:41`:

```
sum(pg_stat_activity_count) by (instance) > (pg_settings_max_connections * 0.85)
```

Vế trái sau `sum by (instance)` chỉ còn label `instance`; vế phải giữ `job`,
`server`, và exporter thật còn thêm `datname`/`state`. Vector matching mặc định đòi
khớp **toàn bộ** label → phép so sánh trả rỗng, alert im lặng vĩnh viễn. Cú pháp vẫn
hợp lệ hoàn toàn nên `promtool check rules` không hé một lời.

**Đây không phải suy đoán.** `monitoring/alerts_test.yml` dựng series đúng label thật
của postgres-exporter ở mức 92/100 connection. Chạy với expr cũ:

```
alertname: PostgresTooManyConnections, time: 5m
    exp:[ 0: Labels:{...severity="warning"} ]
    got:[]                                        ← không bắn
alertname: PostgresConnectionLeak, time: 17m
    got:[]                                        ← không bắn
```

Hệ quả: từ 23/07 tới nay **cả hai** rule cảnh báo connection đều chết. Việc hạ
`for: 5m → 1m` hôm 23/07 không có tác dụng gì vì rule vốn không bao giờ khớp. Cả ba
sự cố đều không thể được cảnh báo — và sẽ không, cho tới bản vá này.

### F4 — Không có alert tầng ứng dụng cho `too many clients`

Mọi cảnh báo dựa vào `pg_stat_activity_count` — gauge lấy theo scrape interval ~15s.
Bão 10 giây tàng hình hoàn toàn với Prometheus trong khi app đã ném hàng trăm FATAL.
Loki đang chạy sẵn nhưng chưa có rule nào đọc log.

### F5 — `postgres-exporter` không khai `application_name`

`docker-compose.prod.yml:682` — DSN thiếu `application_name`, cộng
`PG_EXPORTER_AUTO_DISCOVER_DATABASES=true` nên mở 2 connection hiện ra là `(rỗng)`
trong `pg_stat_activity`. Đúng loại nhiễu đã ngốn 4 vòng điều tra hồi 23/07.

---

## 2. Thiết kế bản vá

### Nguyên tắc

> Payload phải **tự chứa** đủ thông tin để phân quyền. Consumer không được chạm DB.

Số query cho một event phải là **hằng số**, không phụ thuộc số client.

### Kiến trúc

```
                    TRƯỚC                              SAU
   push_event(payload)                    push_event(payload)
        │                                       │
        │                                  enrich: bổ sung staff_id +
        │                                  channel_code nếu thiếu  ── 1 query
        ▼                                       ▼
   group_send ──┬── client 1 → query DB    group_send ──┬── client 1 → 0 query
                ├── client 2 → query DB                 ├── client 2 → 0 query
                └── client N → query DB                 └── client N → 0 query

   Tổng: N query đồng thời                 Tổng: ≤1 query, không đồng thời
```

Enrichment đặt tại **điểm hội tụ** thay vì sửa 18 call site — call site mới trong
tương lai tự động được bảo vệ, không phụ thuộc người viết nhớ truyền field.

### Thành phần

| # | Thay đổi | File |
|---|---|---|
| 1 | Thêm `staff_id` vào `OrderEvent` + `validate_payload` + 2 builder | `orders/contracts/order_events.py` |
| 2 | Module publisher mới: validate → enrich → group_send | `orders/application/services/order_event_publisher.py` (mới) |
| 3 | 2 bản `push_event` trùng nhau + `_broadcast_payload` gọi vào publisher | `order_service_helpers.py`, `order_service_commands.py`, `restore_tasks.py` |
| 4 | `_is_authorized` thành hàm thuần, 0 DB | `channels_core/consumers.py` |
| 5 | Fallback DB (payload thiếu field) qua gate riêng + log cảnh báo | `channels_core/handshake.py`, `consumers.py` |
| 6 | `CONN_MAX_AGE` mặc định suy từ `DB_PGBOUNCER` | `config/settings_parts/base.py` |
| 7 | Sửa label matching (`on(instance)`) + unit test rule | `monitoring/alerts.yml`, `alerts_test.yml` |
| 8 | Counter tầng app + alert `DbConnectionPoolExhausted` | `common/http_metrics.py`, `common/exceptions.py` |
| 9 | `application_name` cho exporter | `docker-compose.prod.yml` |
| 10 | `load_event_meta` cho luồng bulk (1 query/lô) | `order_crud_view.py`, `order_action_view.py` |

### Vì sao F4 dùng metric ứng dụng thay vì rule Loki

Dự định ban đầu là đếm dòng FATAL trong Loki, nhưng `monitoring/loki-config.yml`
**chưa bật ruler** — Loki hiện chỉ lưu và truy vấn log, không đánh giá alert. Thay vì
mở thêm một thành phần hạ tầng giữa lúc chữa cháy, đếm ngay tại
`api_exception_handler`: nơi đó đã bắt mọi lỗi 500 và là chỗ duy nhất biết chắc một
request THẬT đã hỏng.

Điểm mạnh nằm ở chỗ nó là **counter, không phải gauge**. Ba rule connection hiện có
đều đọc `pg_stat_activity_count` — gauge lấy mỗi ~15s, nên bão ngắn hơn một chu kỳ
scrape là vô hình. Counter tích luỹ nên không bỏ sót, và nó đo đúng thứ người dùng
chịu: số request nhận 500, chứ không phải "số connection đang mở".

### Vì sao giữ fallback DB thay vì fail-closed

Nếu payload thiếu field mà từ chối luôn, user scope `own` sẽ **mất realtime âm thầm** —
đúng loại lỗi mà biên bản 23/07 phê phán ở `maxReconnectAttempts=5`. Nên giữ fallback
nhưng bọc trong gate riêng (`WS_BROADCAST_DB_CONCURRENCY`, mặc định 4/worker) để trần
connection không còn tỉ lệ với số client, kèm `logger.warning` kê tên `source` để truy
ra call site nào còn sót và vá dần.

### Trần connection sau bản vá

| Nguồn | Trần |
|---|---|
| web — handshake (8 × 3 worker) | 24 |
| web — broadcast fallback (4 × 3 worker) | 12 |
| web — HTTP | 3 |
| Celery (3 worker) | ~8 |
| beat + exporter | ~4 |
| **Tổng** | **~51 / 100** |

Quan trọng hơn con số: sau bản vá **không còn thành phần nào tỉ lệ với số client**.

---

## 3. Sprint

### Sprint 1 — Vá root cause + quan sát (MR1) — `fix/ws-broadcast-db-fanout`

Toàn bộ là code + config, rollback bằng revert. Lên prod được ngay.

- [x] T1. `staff_id` vào contract (kèm sentinel `0` = "đã tra, đơn chưa có chủ")
- [x] T2. `order_event_publisher.py` — `enrich_order_event` + `load_event_meta`
- [x] T3. Gom hai bản `push_event` trùng nhau về publisher
- [x] T4. `_is_authorized` thuần + `broadcast_db_gate` + cảnh báo call site sót
- [x] T5. `CONN_MAX_AGE` suy từ `DB_PGBOUNCER`, thêm `WS_BROADCAST_DB_CONCURRENCY`
- [x] T6. Sửa label matching `alerts.yml` + counter `desi_db_connection_errors_total`
      + alert `DbConnectionPoolExhausted` (F4 chuyển từ Loki sang metric app vì Loki
      chưa bật ruler)
- [x] T7. `application_name=desi-pg-exporter` cho exporter
- [x] T8. Vá 3 luồng bulk thiếu `channel_code`/`staff_id`: KShip
      (`order_crud_view`), cancel KiotViet + kiotviet action (`order_action_view`)
- [x] T9. `monitoring/alerts_test.yml` + CI job `monitoring:alert-rules`
- [x] T10. Test: 21 test mới (broadcast authorization + publisher + contract)

**Kết quả kiểm chứng:**

| Hạng mục | Kết quả |
|---|---|
| Test mới + `channels_core` | 71 passed |
| `channels_core` + `common` + orders liên quan | 210 passed |
| `ruff check .` | All checks passed |
| `promtool check rules` | SUCCESS, 25 rules |
| `promtool test rules` — expr MỚI | SUCCESS |
| `promtool test rules` — expr CŨ | **FAILED, `got:[]`** ⇒ F3 được chứng minh |

### Sprint 1b — Tái diễn 2026-07-30 (MR `fix/order-created-rest-fanout`)

**14:36 ngày 30/07 sự cố tái diễn** dù Sprint 1 đã lên prod. Hai nguyên nhân:

1. **Trần bị hạ một nửa đúng cùng chiều 29/07**: commit `860b73d8` (16:09, audit
   DB-4) hạ `max_connections` 100 → 50. Bảng "~51/100" ở mục 2 thành **~48/50**
   ngay khi merge — headroom cho REST gần bằng 0. Hai MR cùng ngày, mỗi cái đúng
   một mình, cộng lại thì sai: không ai chạy lại phép cộng sau khi cả hai vào master.
2. **Fanout thứ hai nằm ở FE, Sprint 1 không đụng tới**: `use-crm-websocket.ts` —
   MỌI tab nhận `order.created` là bắn `GET /api/orders/{id}` ngay lập tức. N tab ×
   M đơn (bulk) request đồng thời, mỗi request một connection (CONN_MAX_AGE=0).
   Log 14:36: một `POST /api/orders` → loạt `GET /api/orders/549845` song song →
   các request khác chết ở bước JWT auth. Fanout "di cư" từ tầng WS-auth sang REST.

Việc đã làm:

- [x] T14. FE: chỉ tab đang xem danh sách đơn mới fetch khi có `order.created`;
      jitter 0.5–2s phá đồng bộ giữa các tab; nhiều event trong cửa sổ jitter gộp
      thành MỘT `loadOrders(force)` thay vì M GET (4 test mới, TDD)
- [x] T15. Sửa comment budget `max_connections=50` trong compose (phép tính cũ
      quên 12 slot broadcast fallback, đặt trần HTTP=3 trong khi HTTP không bị cap)
- [x] T16. Thông đường cutover PgBouncer: compose trước đó **hardcode**
      `POSTGRES_HOST: postgres` và không whitelist `DB_PGBOUNCER` trong anchor env
      — bước `.env` của runbook lặng lẽ vô tác dụng. Giờ passthrough cả ba biến;
      `DJANGO_CONN_MAX_AGE` passthrough rỗng để settings tự suy (0/600)

### Sprint 2 — Cutover PgBouncer (MR2) — tách riêng

Thay đổi hạ tầng, có bẫy prepared statement của psycopg3 → **không gộp vào MR1**.
Gộp thì lúc sự cố không biết revert cái nào.

- [ ] T10. Validate trên staging theo `docs/runbooks/2026-07-24-pgbouncer-cutover.md`
- [ ] T11. Bật `DB_PGBOUNCER=1` + `CONN_MAX_AGE` tự về 600 (T5 lo)
- [x] T12. Migration nối thẳng Postgres, không qua pooler — compose đã ghim override
      `POSTGRES_HOST=postgres DB_PGBOUNCER=0` ngay trong lệnh migrate (2026-07-30);
      migration thủ công qua `docker exec` vẫn phải tự nhớ override
- [ ] T13. Cutover prod + theo dõi 48h

---

## 4. Triển khai

`WS_BROADCAST_DB_CONCURRENCY` nằm trong anchor `x-backend-env` — anchor này là
**whitelist**, biến không khai ở đó không bao giờ tới được container. Nên phải
recreate, `restart` không đủ. Recreate **tuần tự từng service**, beat làm cuối
(theo runbook sự cố 23/07):

```bash
git pull origin master
for s in web worker worker-kiotviet worker-slow beat; do
  docker compose -f docker-compose.prod.yml stop $s
  docker compose -f docker-compose.prod.yml rm -f $s
  docker compose -f docker-compose.prod.yml up -d --build $s
done
```

Metric + alert (`desi_db_connection_errors_total`, sửa label matching) cần nạp lại
Prometheus; `application_name` của exporter cần recreate chính nó:

```bash
docker compose -f docker-compose.prod.yml --profile monitoring up -d postgres-exporter
docker exec desi-prometheus kill -HUP 1
```

Không cần đụng Postgres — bản vá này không đổi tham số server nào.

## 5. Kiểm chứng sau deploy

`application_name` phải hiện đủ tên service, và số connection **không tăng theo số
tab đang mở**:

```
P="docker exec desi-postgres psql -U desi -d desi -tAc"
$P "select application_name,state,count(*) from pg_stat_activity group by 1,2 order by 3 desc"
```

Test thủ công: mở 10 tab, sửa 1 đơn, quan sát `pg_stat_activity` — trước bản vá sẽ
thấy một nhịp tăng ~10 connection, sau bản vá phải phẳng.

Cảnh báo còn sót phải xuất hiện trong log web:

```
docker logs desi-web --since 1h 2>&1 | grep "broadcast payload thiếu"
```

Alert đã sống lại chưa — query này phải trả về kết quả, khác `[]`:

```
curl -s "http://127.0.0.1:9092/api/v1/query?query=sum(pg_stat_activity_count)by(instance)"
curl -s "http://127.0.0.1:9092/api/v1/query?query=desi_db_connection_errors_total"
```

## 6. Việc còn lại

1. `.env` prod lệch compose — biên bản 23/07 mục 5.2 ghi nhận
   `CELERY_WORKER_MAX_MEMORY_PER_CHILD` lệch, chưa xác nhận đã đồng bộ.
2. Phiên `psql` idle 1 ngày phát hiện lúc điều tra — cần thói quen đóng session.
3. Group `crm_updates` là group **toàn cục**: mọi event tới mọi client rồi mới lọc.
   Đúng hơn là group theo channel/user để lọc ngay ở tầng Redis. Là thay đổi lớn
   hơn, ghi nhận cho vòng sau.
