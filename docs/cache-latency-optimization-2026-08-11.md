# Tối ưu cache và latency toàn hệ thống

Ngày: 2026-08-11 · Nhánh: `perf/cache-latency-l0`
Runbook đo: [`docs/runbooks/2026-08-11-cache-latency-measurement.md`](runbooks/2026-08-11-cache-latency-measurement.md)

## 0. Bối cảnh và nguyên tắc

Đợt tối ưu trước (`perf/order-list-fanout-hardening` + `feat/ws-order-created-row`,
04/08) đã xử lý **một** điểm nóng cụ thể: bão `POST /api/orders` (LIST) do WS
`order.created` fanout ra ~40 tab. Kết quả: cache COUNT/status_counts cho 6 trang
list, serializer gọn cho list, lean columns, và WS mang sẵn row để tab không phải
fetch lại.

Tài liệu này lùi lại một bước và nhìn **toàn hệ thống**: hạ tầng cache có đúng
hình dạng không, chỗ nào đang mù, và trục đo nào phải có trước khi tối ưu tiếp.

Ba nguyên tắc xuyên suốt:

1. **Không tối ưu thứ chưa đo.** Xếp hạng theo `p95 × tần suất` (tổng thời gian
   người dùng phải chờ mỗi phút), không theo p95 đơn thuần — một endpoint 8s chạy
   2 lần/giờ ít quan trọng hơn một endpoint 400ms chạy 300 lần/phút.
2. **Cache không bao giờ được là SPOF.** Cache hỏng thì hệ thống chậm, không phải
   hệ thống chết. Hiện trạng đang vi phạm nguyên tắc này (mục 2.1).
3. **Đo được mới gọi là xong.** Mỗi việc trong sprint có một con số để đối chiếu
   trước/sau, lấy bằng lệnh trong runbook.

## 1. Bảy trục đo lường

| Trục | Trả lời câu hỏi | Nguồn số | Runbook |
|---|---|---|---|
| A1 | Endpoint nào ăn nhiều thời gian người dùng nhất? | Prometheus `desi_http_request_duration_seconds` | §1 |
| A2 | Query nào ăn nhiều thời gian DB nhất? Index nào chết? | `pg_stat_statements`, `pg_stat_user_indexes` | §2 |
| A3 | Chậm vì DB hay vì chờ pool? | PgBouncer `SHOW STATS` / `SHOW POOLS` | §3 |
| A4 | Cache có hiệu quả không? Ăn bao nhiêu RAM? | `redis-cli INFO`, `desi_cache_*` | §4 |
| A5 | Trong một request, thời gian nằm ở đâu? | Loki: `list-timing`, `create-timing`, `landing-timing` | §5 |
| A6 | Job nền có đè giờ hành chính không? | Celery queue depth, beat schedule | §6 |
| A7 | Người dùng chờ bao lâu ở trình duyệt? | bundle size, số request/trang, Web Vitals | §7 |

Trục A4 hiện **chưa tồn tại** — xem 2.2. Sprint 1 dựng nó.

## 2. Phát hiện từ audit code

Mỗi phát hiện ghi kèm bằng chứng để người sau kiểm lại được, và mức độ:
🔴 rủi ro sự cố · 🟠 latency thấy được · 🟡 nợ kỹ thuật.

### 2.1 🔴 Redis cache dùng chung instance với Celery broker và WebSocket, `noeviction`, không `maxmemory`

Bằng chứng: `docker-compose.prod.yml` — một service `redis`, ba DB logic
(`REDIS_CACHE_DB=1`, `REDIS_CHANNEL_DB=2`, broker `/3`), khởi động với
`--appendonly yes --appendfsync everysec --maxmemory-policy noeviction` và
**không có `--maxmemory`**.

Hai hệ quả độc lập nhau:

- **Lan sự cố.** `maxmemory-policy noeviction` nghĩa là khi Redis chạm trần bộ nhớ
  (trần cgroup hoặc trần host, vì không đặt `maxmemory` thì không có trần mềm),
  Redis từ chối mọi lệnh ghi. Không chỉ cache chết — **Celery broker và
  channel layer chết theo**. Một đợt cache phình (báo cáo lớn, key không TTL,
  người dùng mở nhiều bộ lọc) đủ để dừng toàn bộ job nền và WebSocket. Đây là
  hạng mục rủi ro, không phải tối ưu.
- **Cache đang bị ghi xuống đĩa.** `appendonly yes` + `appendfsync everysec` áp
  cho cả DB1. Dữ liệu cache theo định nghĩa là tái tạo được; fsync nó là chi phí
  đĩa và độ trễ ghi vô ích. Broker thì **cần** bền vững — nên không thể chỉ tắt
  AOF cho cả instance.

Hai yêu cầu ngược nhau (cache muốn evict + không bền, broker muốn không evict +
bền) trên cùng một instance không thể thoả hiệp bằng cấu hình. **Phải tách.**

Giải pháp: service `redis-cache` riêng — `--maxmemory` + `--maxmemory-policy
allkeys-lru`, **tắt AOF và RDB**. Cache đầy thì tự đẩy key cũ ra, không bao giờ
chạm được vào broker. Xem 3.1.

### 2.2 🟠 Cache đang mù hoàn toàn

Bằng chứng: `grep -rn "cache\.\(get\|set\)" backend/apps` cho ~80 điểm gọi;
`grep -rn "cache" backend/apps/*/metrics.py` cho **0 metric**.

Không có hit rate, không có miss rate, không có error rate. Nghĩa là:

- Không biết `ORDERS_LIST_COUNT_CACHE_TTL=30` đang tiết kiệm được bao nhiêu COUNT.
- Không biết cache RBAC (TTL 30 phút) có bị bump version quá thường xuyên đến mức
  vô dụng không.
- Nếu Redis cache chết, biểu hiện là "hệ thống chậm" mà không có tín hiệu nào chỉ
  vào cache.

Giải pháp: gắn metric ở **tầng backend cache**, không phải ở từng call site — một
lần sửa, phủ cả 80 điểm và mọi điểm thêm về sau. Xem 3.2.

### 2.3 🟡 Không có lớp cache chung

Bằng chứng: bốn kiểu quản lý key song song tồn tại —
`channels_core/cache_utils.py` (version counter), `users/rbac.py` (version counter
bản sao), `OrderRepository._filters_cache_key` (hash bộ lọc), và phần còn lại tự
ghép chuỗi. Chỉ `callcenter/services.py` có lock chống stampede; các chỗ khác khi
key hết hạn thì N request cùng lúc cùng tính lại.

TTL đều là hằng số chẵn (30s, 600s, 10800s) → key sinh cùng lúc sẽ hết hạn cùng
lúc, tạo gai tải định kỳ.

Giải pháp (Sprint 3, không thuộc đợt này): `apps/common/cache.py` với namespace,
versioned key, `get_or_set_locked` (single-flight), TTL jitter. Đợt này chỉ dựng
phần backend + metric để lớp đó có chỗ bám.

### 2.4 🟠 Nén JSON đang tiêu CPU của web worker

Bằng chứng: `django.middleware.gzip.GZipMiddleware` trong `MIDDLEWARE`
(`settings_parts/base.py`), trong khi `desi-web` bị giới hạn 1.5 CPU và nút cổ
chai lúc bão đã được xác định là CPU (sự cố 2026-08-04). Phía nginx:
`grep -rn "gzip_proxied" nginx/` → **không có dòng nào**, nghĩa là nginx mặc định
(`gzip_proxied off`) **không nén** response đến từ upstream — toàn bộ việc nén
JSON đang do Python làm.

Nén ở nginx là code C, không giữ thread ASGI, không tranh CPU với serialize.

Giải pháp: cutover hai bước (xem 3.3) — không đổi một phát, vì nginx prod là
config trên host, **không** nằm trong `scripts/deploy-prod.sh`.

### 2.5 🟡 Cache header của asset FE chưa tối ưu

Bằng chứng: `frontend/nginx.conf` — `location /assets/` đặt `expires 7d`. Nhưng
Vite đã gắn content-hash vào tên file (`index-ByvYMspt.js`), nghĩa là file không
bao giờ đổi nội dung dưới cùng một tên. `7d` khiến trình duyệt quay lại
revalidate mỗi tuần mà không có lý do gì.

Thêm: `vite.config.ts` đặt `sourcemap: "hidden"` và chỉ xoá `.map` khi có
`SENTRY_AUTH_TOKEN` (`filesToDeleteAfterUpload`). Build không token → `.map` nằm
lại trong `dist` và được nginx phục vụ công khai. `frontend/dist/assets/` hiện tại
có `index-ByvYMspt.js.map` 2.1 MB, lớn hơn cả bundle.

Giải pháp: `expires 1y` + `immutable` cho `/assets/`, và chặn `.map` ở nginx
(defence-in-depth, không phụ thuộc việc build có token hay không).

### 2.6 🟠 49 beat task dồn vào các mốc phút chẵn

Bằng chứng: `settings_parts/celery.py` có 49 mục schedule; các mục dùng
`crontab(minute="*/5")`, `"*/15"`, `"*/30"` đều nổ **cùng lúc** tại phút 0, và
lại chồng nhau tại phút 15/30/45. Không mục nào lệch pha.

Hệ quả: mỗi 15 phút có một gai CPU + connection DB do nhiều job khởi động đồng
thời, rơi đúng vào giờ người dùng đang thao tác.

Giải pháp: đẩy lệch pha bằng cách chọn phút bắt đầu khác nhau
(`minute="3,18,33,48"` thay cho `*/15`), giữ nguyên chu kỳ.

### 2.7 🟠 Order list mới cache nửa đường

Bằng chứng: `order_list_view.py` cache `count`, `status_counts`, `sheet_stats`;
phần `page` (rows) không cache. Theo `docs/order-list-fanout-hardening-2026-08-04.md`
đợt 4, `page` vẫn 66–160ms lúc vãn và serialize ~460ms cho 100 rows.

Giải pháp: thuộc Sprint 4, **phải có số đo trước**. Cache rows nguy hiểm hơn cache
count nhiều (payload mang dữ liệu theo scope người dùng — sai key là rò dữ liệu
giữa các sale). Chỉ làm sau khi có lớp cache chuẩn ở 2.3 với key bắt buộc mang
scope.

### 2.8 🟡 Chưa có ETag/304 cho catalog ít đổi

Các GET danh mục (channels, warehouses, users, products) trả 200 với full body mỗi
lần. Với dữ liệu đổi vài lần một ngày, `ETag` + `304 Not Modified` cắt gần hết
payload mà không cần bất kỳ invalidation nào ở tầng ứng dụng.

Thuộc Sprint 4.

## 3. Giải pháp đợt này (L0)

### 3.1 Tách Redis cache thành instance riêng

Service `redis-cache` mới: `maxmemory` (mặc định 256mb), `allkeys-lru`,
`--save ""` (tắt RDB), không AOF. Không dùng chung volume với `redis`.

Phía settings: thêm `REDIS_CACHE_HOST` / `REDIS_CACHE_PORT` /
`REDIS_CACHE_PASSWORD`, **mặc định rơi về giá trị Redis dùng chung**. Không đặt
env mới thì hành vi y hệt hôm nay — đây là điều kiện để rollback chỉ bằng env,
không cần deploy lại.

Trên instance riêng, `REDIS_CACHE_DB` đặt `0` (instance chỉ có cache thì không cần
tách DB). Giữ default `1` cho trường hợp dùng chung.

**Thứ tự deploy bắt buộc**: `redis-cache` phải tồn tại trước khi web/worker lên
với env mới. `scripts/deploy-prod.sh` đã được sửa để tự lo việc này — nó `up -d
--no-deps redis-cache` ngay trước bước deploy app. `up -d` là idempotent nên các
lần deploy sau không đụng tới container đang chạy (không xoá cache mỗi lần).

Nếu deploy tay không qua script, giữ đúng thứ tự đó. Điều 3.2 làm cho tình huống
sai thứ tự **chậm chứ không chết**, nhưng đó là lưới an toàn chứ không phải quy
trình.

### 3.2 Cache backend chống SPOF + có metric

`apps/common/cache_backend.py` — `InstrumentedRedisCache(RedisCache)`:

- **Metric**: `desi_cache_operations_total{operation,result}` với result ∈
  `{hit, miss, error}` và `desi_cache_operation_duration_seconds{operation}`.
  Đặt ở backend nên phủ toàn bộ ~80 call site hiện có lẫn mọi call site tương lai,
  không phải sửa một dòng nào ở tầng nghiệp vụ.
- **Fail-soft** cho đường tăng tốc: `get`/`get_many`/`has_key` nuốt lỗi kết nối
  Redis và trả như cache miss; `set`/`set_many`/`touch`/`delete`/`delete_many`
  nuốt lỗi và bỏ qua. Nghĩa là Redis cache chết → hệ thống chạy chậm bằng đúng
  tốc độ khi không có cache, không trả 500.
- **Fail-loud có chủ đích** cho `add`/`incr`. Đây là các primitive của **khoá
  phân tán và rate limit** (`cache.add(lock_key, ...)` ở `kiotviet/tasks.py`,
  `inventory/tasks.py`, `contact_flow/tasks.py`, `common/db_guards.py`;
  `cache.incr` ở `common/rate_limiter.py`, `callcenter/transcribe.py`). Nuốt lỗi
  ở đây sẽ biến "Redis hỏng" thành "hai worker cùng chạy một job sync" hoặc
  "trần gọi Gemini biến mất" — hỏng dữ liệu còn tệ hơn hỏng dịch vụ. Lỗi phải nổ
  để task retry và để alert nhìn thấy.

  `delete` nằm ở nhóm fail-soft dù thường đứng trong `finally` của khối khoá:
  khoá luôn có TTL nên tự hết hạn, còn ném lỗi từ `finally` thì **che mất**
  exception thật của công việc bên trong. `decr` không override — `RedisCache`
  không tự cài nó, `BaseCache.decr` gọi lại `self.incr`, override thêm sẽ đếm
  trùng.

Ranh giới fail-soft/fail-loud này là quyết định thiết kế chính của module, được
khoá bằng test.

### 3.3 Chuẩn bị cutover nén sang nginx (hai bước)

Bước 1 (đợt này): thêm `gzip_proxied any` vào các config nginx, và đưa
GZipMiddleware ra sau một cờ env `WEB_GZIP_IN_DJANGO` (mặc định `1` = giữ nguyên
hành vi hôm nay).

Khi cả hai cùng bật, không có gì đổi: nginx bỏ qua response đã có
`Content-Encoding`, nên `gzip_proxied` là no-op cho tới khi bước 2 diễn ra.

Bước 2 (thao tác vận hành, không cần deploy code): sau khi xác nhận config nginx
trên host đã có `gzip_proxied`, đặt `WEB_GZIP_IN_DJANGO=0` rồi restart web. Verify
bằng lệnh trong runbook §8. Sai thì trả cờ về `1`.

Tách hai bước vì nginx prod là config **trên host**, không nằm trong
`deploy-prod.sh` — gộp một bước thì có cửa sổ hệ thống trả JSON không nén.

### 3.4 Asset FE

`expires 1y` + `Cache-Control: public, max-age=31536000, immutable` cho
`/assets/` (file đã có content-hash), và `location ~ \.map$ { return 404; }`.

### 3.5 Stagger beat

Đổi `crontab(minute="*/N")` sang danh sách phút lệch pha, giữ nguyên chu kỳ và
giữ nguyên mọi task chạy đúng giờ nghiệp vụ (`crontab(hour=..., minute=...)`
tuyệt đối không đụng).

## 4. Sprint

### Sprint 1 — Đo và bày số (đợt này, phần tài liệu)

- [x] Runbook 7 trục đo với lệnh copy-paste, mỗi dòng < 110 ký tự, không heredoc
      (terminal prod vỡ dòng — xem `prod-ssh-paste-line-wrap`)
- [x] Metric cache (`desi_cache_operations_total`) — có sẵn để Grafana vẽ
- [ ] Chạy runbook trên prod, điền bảng xếp hạng `p95 × rate` vào §9 runbook
- [ ] Panel Grafana cache hit rate

**Tiêu chí xong**: có bảng xếp hạng endpoint theo tổng thời gian chờ/phút, và một
con số hit rate cache thật.

### Sprint 2 — L0 hạ tầng (đợt này, phần code)

- [x] `redis-cache` service + env + settings fallback
- [x] `InstrumentedRedisCache` + test khoá ranh giới fail-soft/fail-loud
- [x] `gzip_proxied` + cờ `WEB_GZIP_IN_DJANGO`
- [x] Asset 1 năm immutable + chặn `.map`
- [x] Stagger beat

**Tiêu chí xong**: sau deploy, `INFO memory` của `redis-cache` tách bạch khỏi
broker; `desi_cache_operations_total` có số; CPU `desi-web` giảm đo được sau khi
bật bước 2 của 3.3.

### Sprint 3 — Lớp cache chuẩn

`apps/common/cache.py`: namespace, versioned key, `get_or_set_locked`
(single-flight), TTL jitter. Migrate dần các điểm ở 2.3 — ưu tiên chỗ có hit rate
thấp theo số của Sprint 1.

**Tiêu chí xong**: hit rate từng namespace hiện trên Grafana; không còn chỗ nào tự
ghép key bằng tay.

### Sprint 4 — Điểm nóng theo số đo

Chọn từ bảng xếp hạng Sprint 1. Ứng viên đã biết: cache page rows của list (2.7),
ETag/304 cho catalog (2.8), top-5 query từ `pg_stat_statements`.

**Tiêu chí xong**: p95 của 3 endpoint đầu bảng giảm ít nhất một nửa.

## 5. Rủi ro và rào chắn

| Rủi ro | Rào chắn |
|---|---|
| Cache rows sai scope → rò dữ liệu giữa sale | Key bắt buộc mang scope user; đã có tiền lệ phải đặt `ORDERS_LIST_COUNT_CACHE_TTL=0` ở dev vì lẫn count giữa test. Không làm ở Sprint 2. |
| `redis-cache` chưa tồn tại lúc web khởi động | Fail-soft ở `get`/`set` (3.2) + thứ tự deploy ghi trong runbook |
| Nuốt lỗi Redis làm hỏng khoá phân tán | `add`/`incr` cố tình fail-loud, khoá bằng test |
| Host nginx chưa có `gzip_proxied` mà đã tắt gzip Django | Cutover 2 bước + lệnh verify (runbook §8), rollback bằng env |
| `maxmemory` quá nhỏ → evict liên tục, hit rate sụp | Metric hit rate + `evicted_keys` trong runbook §4; chỉnh `REDIS_CACHE_MAXMEMORY` |

## 6. Kết quả đo thật — 2026-08-11 (Sprint 1)

Đo lúc **11:40–11:55, tải nhàn**. Số trong burst 09:00–11:00 vẫn còn nợ.

### 6.1 HTTP — ở tải thấp, mean mới là số dùng được

Với ~55 request/10 phút, `histogram_quantile` rơi trọn vào bucket (1.0, 2.5] và
trả 2.2–2.3 cho gần như mọi route — **p95 vô nghĩa ở mức lưu lượng này**. Số dùng
được là `rate(_sum) / rate(_count)`:

| Route | mean |
|---|---|
| `^api/orders/?` (LIST) | **0.78s** |
| `^api/orders/?landing` | 1.05s |
| `^api/orders/?products` | 0.80s |
| `^api/orders/?koc` | 0.72s |
| `^api/orders/?<int:order_id>` | 0.25s |
| `^api/orders/?metrics` | 0.13s |

**Ý nghĩa cho burst:** 4,4 req/s (đo trong bão 04/08) × 0,78s = 3,4 giây CPU cần
mỗi giây, trong khi `desi-web` có 1,5 CPU → quá tải 2,3 lần. Gate 503 không phải
sự cố bất thường mà là hệ quả số học. Mục tiêu định lượng: **mean phải xuống dưới
0,34s** mới chịu nổi lưu lượng đó.

### 6.2 Bên trong một request list (10 mẫu `list-timing`, đều `rows=1`)

| Khúc | Mean | Ghi chú |
|---|---|---|
| `page` | 162ms | Query trang — không co theo số row |
| `ctx` | 103ms | 4–5 query cố định mỗi request (channel scope, reminder map, 2 query KiotViet job, field policy) |
| `count` | 87ms | **4–18ms khi trúng cache / 140–353ms khi trượt** |
| `status_counts` | 76ms | **1–7ms khi trúng / 114–212ms khi trượt** |
| `serialize` | 34ms | Đợt cắt field 04/08 đã ăn (trước đó ~460ms) |
| **tổng trong view** | **461ms** | |

Kết luận dùng được: cache count chạy đúng, chỉ là TTL 30s quá ngắn so với nhịp
truy cập — mọi request 616–785ms đều là request trượt cache; **đã nâng lên 120s**.

> **ĐÍNH CHÍNH — đừng dùng các số tuyệt đối ở bảng trên.** Mười mẫu này rơi vào
> 11:42:59–11:43:00, tức **nằm trọn trong một cụm fanout** (xem §6.5): 7 request
> chen trong nửa giây cuối, tranh nhau 1,5 CPU. Dấu hiệu rõ nhất là
> `serialize=34ms` cho **một** dòng — serialize 1 row không thể tốn ngần ấy, nó
> đang bị phóng đại hàng chục lần vì tranh CPU, và `page`/`ctx` cũng vậy.
>
> Vì thế **không** kết luận được `page + ctx = 265ms` là sàn cứng, và cũng không
> nên tối ưu hai khúc đó dựa trên bảng này — đúng cái bẫy "tối ưu thứ chưa đo" mà
> §0 đặt ra. Đã đọc code hai khúc đó: các query đều nhỏ và **đã có index đúng**
> (`ord_kv_job_or_cr_id_idx`, `sale_reminder_order_status_idx`), càng củng cố giả
> thuyết con số bị nhiễu chứ không phải query tồi.
>
> Muốn số sạch thì lấy mẫu NGOÀI cụm — chọn giây chỉ có 1–2 request:
>
> ```sh
> docker logs --since 30m desi-web 2>&1 | grep list-timing | grep -o '"asctime": "[^"]*"' | cut -c24-31 | uniq -c | sort -n | head
> ```
>
> rồi lọc `list-timing` của đúng những giây đó. Việc cắt `page`/`ctx` chỉ nên bắt
> đầu sau khi có bảng phân rã sạch.

Chênh lệch **461ms (trong view) so với 784ms (Prometheus)** gợi ý ~320ms nằm ngoài
khối đo: JWT auth, audit middleware, gzip, DRF render. Hai số lấy ở hai khung giờ
khác nhau nên **chưa chốt** — phải đo lại cùng khung trước khi tin.

### 6.3 Postgres — gần nửa thời gian DB không phục vụ người dùng

Từ `pg_stat_statements` (tích luỹ từ ~24/07):

| Nhóm | % tổng `total_exec_time` | Chi tiết |
|---|---|---|
| **postgres-exporter** | **20%** | `pg_stat_user_tables` 103.681 lần × 194ms ≈ 5,6 giờ CPU-DB (19%) + `pg_database_size` 414.724 lần (1%) |
| **`rebuild_segment_snapshot`** | **23%** | 5 câu, mỗi câu đúng 80.741 lần; một câu đọc ra **100,5 triệu row** |
| Đường list của người dùng | 22% | 4 biến thể `SELECT orders.*`, 1,24 triệu lần, 17–21ms/lần |

Exporter đắt vì hai thứ cộng hưởng: `PG_EXPORTER_AUTO_DISCOVER_DATABASES=true`
khiến nó chạy mọi collector trên **mọi database của server** (host dùng chung với
ads-crm), và `stock_movements` đã partition nên `pg_stat_user_tables` phình theo
số partition. **Đã sửa cả hai trong MR này.**

`rebuild_segment_snapshot` là hạng mục lớn nhất còn lại: nó rebuild toàn bộ thay
vì tính tăng dần theo customer có đơn thay đổi. Chưa làm — xem Sprint 4.

### 6.4 Cảnh báo về cách đọc `pg_stat_statements`

Câu `UPPER(order_code) LIKE …` hiện ra với mean **2.650ms**, trông như câu chậm
nhất hệ thống. Nhưng bảng này **tích luỹ từ lần reset gần nhất** (~24/07) còn bản
vá `__ilike_contains` deploy **28/07** — nên phần lớn 934 lần gọi đó là dư âm của 4
ngày trước khi vá, không phải bệnh đang sống. Bài học: trước khi kết tội một câu
trong bảng này, đối chiếu ngày commit của bản vá liên quan, hoặc
`pg_stat_statements_reset()` rồi đo lại.

Dù vậy vẫn còn **3 call site** dùng `__icontains` trên đúng những cột có index
trigram (`pending_scan_service`, `return_history_service`, `warranty/claim_repository`)
— bản vá 28/07 mới áp cho `dispatch_history_service`. Đã đổi nốt trong MR này;
chúng không lọt top-20 vì lưu lượng thấp, nhưng vô hiệu hoá index theo đúng cùng
một cơ chế.

### 6.5 Còn treo: 8–10 request list dồn trong cùng một giây

Đếm theo giây trong 2 giờ cho ra các cụm **8–10 `POST /api/orders` trong cùng MỘT
giây**, lặp lại cách nhau vài chục giây, giữa lúc hệ thống nhàn. Mỗi request
~461ms server → một cụm là ~4,5 giây CPU dồn vào 1 giây trên máy 1,5 CPU. Đây
chính là cơ chế bão, thu nhỏ.

Đã loại trừ bằng cách đọc code:

- `order.created` **không** gọi REST khi payload mang `row` (upsert thẳng);
- thiếu `row` thì coalescer gọi `GET /api/orders/{id}` cho một đơn, chỉ gọi
  `loadOrders` (POST list) khi gom được **≥2** đơn trong cửa sổ jitter;
- danh sách đơn **không** có polling/`refetchInterval`, cũng không refetch khi WS
  reconnect.

**Đã khép án bằng cách đọc code (2026-08-11, chiều).** Trong toàn bộ nhánh
`order.updated` của `use-crm-websocket.ts` chỉ có **một** đường gọi fetch:

```ts
const isTransferEvent = payload?.source === "orders.service.transfer_landing_orders";
if (isTransferEvent && sectionRef.current === "orders") scheduleCreatedOrderRefresh(orderId);
```

Mọi `order.updated` khác đều vá tại chỗ bằng `patchOrderFromRealtime` (zero REST).
Chuyển đơn Landing → Đơn hàng là thao tác **hàng loạt**, nên coalescer luôn gom
được ≥2 id — mà nhánh ≥2 id của coalescer gọi `loadOrders`, tức `POST /api/orders`
đầy đủ. M đơn × N tab = đúng chữ ký 8–10 request/giây đo được.

Lý do đường này còn sót: đơn vừa chuyển sang section `orders` nên tab **không có
dòng nào để vá** — nó buộc phải đi lấy. Cách chữa giống hệt `order.created`: cho
event mang sẵn row.

**Đã làm** (`build_order_updated(row=)` + điểm phát `transfer_landing_orders` +
nhánh FE ưu tiên row). Trần `_TRANSFER_ROW_PAYLOAD_MAX = 50`: quá trần thì
serialize N row ở điểm phát đắt hơn một lần refetch của mỗi tab, server chủ động
trả `row=None` và FE rơi về đường cũ. Backend cũ hoặc FE cũ đều vẫn chạy, không
cần deploy đồng bộ.

**Cách xác nhận sau deploy:** chuyển một lô đơn Landing rồi đếm lại — cụm 8–10
`list-timing` trong cùng một giây phải biến mất:

```sh
docker logs --since 30m desi-web 2>&1 | grep list-timing | grep -o '"asctime": "[^"]*"' | cut -c24-31 | uniq -c | sort -rn | head
```

## 7. Tiến độ

- [x] Sprint 1 — runbook + metric cache + **đã chạy đo thật, kết quả ở §6**
- [x] Sprint 2 — L0 hạ tầng (MR !1205)
- [x] Thu hồi thời gian DB: exporter + TTL count + 3 call site `icontains` (MR !1206)
- [x] `rebuild_segment_snapshot` tăng dần (MR !1209) — cắt ~23% thời gian DB
- [x] Khép fanout §6.5: event chuyển đơn mang sẵn row (MR này)
- [ ] Đo lại trong burst 09:00–11:00, điền bảng §9 của runbook
- [ ] Lấy mẫu `list-timing` NGOÀI cụm rồi mới quyết có cắt `page`/`ctx` hay không
      (số cũ bị nhiễu — xem đính chính ở §6.2)
- [ ] Sprint 3 — lớp cache chuẩn `apps/common/cache.py`
