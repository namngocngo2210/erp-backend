# Runbook — Đo cache và latency trên prod

Thiết kế: [`docs/cache-latency-optimization-2026-08-11.md`](../cache-latency-optimization-2026-08-11.md)

> **Quy ước gõ lệnh.** Terminal SSH prod cắt dòng khi paste chuỗi dài và luôn vỡ
> heredoc. Mọi lệnh dưới đây giữ dưới ~110 ký tự; câu SQL dài được đóng gói thành
> file trong `scripts/perf/` và nạp bằng `<`. Chạy từ thư mục repo trên prod
> (`cd /opt/desi-order` hoặc nơi đang deploy).
>
> Chuyển hướng log: viết `> file 2>&1`, **không** viết `2>&1 > file` (thứ tự sau
> làm stderr văng ra terminal và mất dòng trong file).

Đo lúc nào: khung **09:00–11:00** là lúc có tải thật (burst ingest buổi sáng).
Đo lúc vãn cho ra số nền, hữu ích để so sánh nhưng không dùng để xếp hạng.

---

## §0 — Deploy MR này (có service mới)

MR thêm service `redis-cache`. `scripts/deploy-prod.sh` đã tự `up` nó trước khi
deploy app, nên đường chuẩn không cần thao tác thêm:

```sh
bash scripts/deploy-prod.sh
```

Deploy tay thì giữ đúng thứ tự — cache phải có trước khi web mang env mới lên:

```sh
docker compose -f docker-compose.prod.yml up -d --no-deps redis-cache
```

```sh
docker compose -f docker-compose.prod.yml up -d --build web worker beat worker-kiotviet worker-slow worker-export
```

Xác nhận web thật sự đang nói chuyện với instance mới (không phải instance cũ):

```sh
docker exec desi-web env | grep REDIS_CACHE
```

```sh
docker exec desi-web python -c "from django.conf import settings; print(settings.CACHES['default']['LOCATION'])"
```

Dòng trên phải in ra host `redis-cache`. In ra `redis` nghĩa là env chưa vào
container — kiểm tra lại anchor `x-app-env` trong compose.

Exporter cho instance mới nằm dưới profile `monitoring`:

```sh
docker compose -f docker-compose.prod.yml --profile monitoring up -d redis-cache-exporter
```

Prometheus đọc target mới từ file mount → **phải force-recreate**, reload không
đủ (bind-mount đổi inode khi `git pull`):

```sh
docker compose -f docker-compose.prod.yml --profile monitoring up -d --force-recreate --no-deps prometheus
```

**Rollback** (quay về dùng chung instance, không cần build lại image): xoá 2 dòng
`REDIS_CACHE_HOST` / `REDIS_CACHE_DB` trong anchor `x-app-env` rồi `up -d web
worker beat`. Settings tự rơi về `REDIS_HOST` + DB 1 như trước 2026-08-11.

## §1 — HTTP: endpoint nào ăn nhiều thời gian người dùng nhất

Prometheus nghe ở `127.0.0.1:${PROM_HOST_PORT}` (prod hiện dùng **9092** vì 9090
đã bị `desi-ads-crm-backend` chiếm).

**Xếp hạng chính.** `rate(..._sum)` cho ra "số giây server bỏ ra mỗi giây" cho mỗi
route — chính là tổng thời gian chờ, đã gộp cả p95 lẫn tần suất:

```sh
Q='topk(10, sum by (route) (rate(desi_http_request_duration_seconds_sum[10m])))'
curl -sG localhost:9092/api/v1/query --data-urlencode "query=$Q" | jq -r '.data.result[]|"\(.value[1])  \(.metric.route)"'
```

Đọc số: giá trị `1.5` nghĩa là route đó chiếm 1.5 giây xử lý mỗi giây trôi qua —
với web bó 1.5 CPU thì một mình nó đã ăn hết một nhân.

**p95 theo route** (sau khi đã biết route nào đáng quan tâm):

```sh
Q='histogram_quantile(0.95, sum by (route,le) (rate(desi_http_request_duration_seconds_bucket[10m])))'
curl -sG localhost:9092/api/v1/query --data-urlencode "query=$Q" | jq -r '.data.result[]|"\(.value[1])  \(.metric.route)"'
```

**Tần suất** (để biết route nào đông người dùng):

```sh
Q='topk(10, sum by (route) (rate(desi_http_requests_total[10m])))'
curl -sG localhost:9092/api/v1/query --data-urlencode "query=$Q" | jq -r '.data.result[]|"\(.value[1])  \(.metric.route)"'
```

**Gate 503 và lỗi kết nối DB** (hai chỉ báo hệ thống đang quá tải, không phải chậm):

```sh
Q='sum by (path_prefix) (increase(desi_http_gate_timeouts_total[1h]))'
curl -sG localhost:9092/api/v1/query --data-urlencode "query=$Q" | jq -r '.data.result[]|"\(.value[1])  \(.metric.path_prefix)"'
```

```sh
Q='sum(increase(desi_db_connection_errors_total[1h]))'
curl -sG localhost:9092/api/v1/query --data-urlencode "query=$Q" | jq -r '.data.result[]|.value[1]'
```

## §2 — Postgres: query nào nặng, index nào chết

```sh
docker exec -i desi-postgres psql -U desi -d desi < scripts/perf/pg-top-queries.sql
```

```sh
docker exec -i desi-postgres psql -U desi -d desi < scripts/perf/pg-index-usage.sql
```

```sh
docker exec -i desi-postgres psql -U desi -d desi < scripts/perf/pg-cache-health.sql
```

Đang có sự cố thì chụp ảnh tức thời:

```sh
docker exec -i desi-postgres psql -U desi -d desi < scripts/perf/pg-live-activity.sql
```

Số liệu `pg_stat_statements` là tích luỹ từ lần reset gần nhất. Muốn đo đúng một
khung giờ thì reset trước khung đó (mất lịch sử, cân nhắc):

```sh
docker exec desi-postgres psql -U desi -d desi -c "select pg_stat_statements_reset()"
```

Cột `stddev_ms` lớn hơn nhiều `mean_ms` nghĩa là câu đó lúc nhanh lúc chậm — thủ
phạm thường là thiếu index cho một nhánh tham số, hoặc bảng bị lock.

## §3 — PgBouncer: chậm vì DB hay vì chờ pool

Đây là câu hỏi phân định quan trọng nhất khi "hệ thống chậm". `avg_query_time`
cao = DB thật sự chậm. `avg_wait_time` cao = DB rảnh nhưng client xếp hàng chờ
slot pool → phải nới pool, không phải tối ưu query.

```sh
docker exec -it desi-pgbouncer psql -h 127.0.0.1 -p 6432 -U desi pgbouncer -c "SHOW STATS"
```

```sh
docker exec -it desi-pgbouncer psql -h 127.0.0.1 -p 6432 -U desi pgbouncer -c "SHOW POOLS"
```

Trong `SHOW POOLS`: `cl_waiting > 0` kéo dài là dấu hiệu pool nhỏ hơn nhu cầu;
`sv_idle` luôn cao trong khi `cl_waiting > 0` là dấu hiệu cấu hình sai chứ không
phải thiếu tài nguyên.

## §4 — Redis cache: hit rate, bộ nhớ, eviction

Tên container Redis prod có thể là container ngoài compose — xác định trước:

```sh
docker ps --format '{{.Names}}' | grep -i redis
```

Đặt biến cho gọn (thay `desi-redis-managed` bằng tên vừa tìm được):

```sh
R="docker exec desi-redis-managed redis-cli -a $REDIS_PASSWORD --no-auth-warning"
```

**Hit rate ở tầng Redis** (toàn instance, mọi DB):

```sh
$R INFO stats | grep -E 'keyspace_hits|keyspace_misses|evicted_keys|expired_keys'
```

`keyspace_hits / (hits + misses)` dưới 0.8 là đáng xem lại TTL hoặc key. Nhưng số
này gộp cả broker và channel layer nên chỉ dùng làm tham chiếu thô — số chuẩn
lấy ở metric ứng dụng bên dưới.

**Bộ nhớ và cấu hình trần** (kiểm tra chính xác phát hiện 2.1 của doc thiết kế):

```sh
$R INFO memory | grep -E 'used_memory_human|used_memory_peak_human|maxmemory'
```

```sh
$R CONFIG GET maxmemory
```

```sh
$R CONFIG GET maxmemory-policy
```

Kỳ vọng **trước** khi tách: `maxmemory` = `0` (không trần) và policy =
`noeviction` — đúng như phát hiện 2.1. Sau khi tách, instance cache phải cho ra
`maxmemory` khác 0 và policy `allkeys-lru`.

**Số key theo DB** (DB1 = cache, DB2 = channels, DB3 = broker):

```sh
$R INFO keyspace
```

**Key cache nào đang chiếm chỗ** — quét không chặn, an toàn trên prod:

```sh
$R --scan --pattern 'desi:*' -n 1 | head -50
```

```sh
$R --scan --pattern 'desi:*' -n 1 | wc -l
```

Key **không có TTL** là nguồn phình bộ nhớ hàng đầu; đếm nhanh một mẫu:

```sh
$R --scan --pattern 'desi:*' -n 1 | head -200 | xargs -n1 -I{} $R -n 1 TTL {} | sort | uniq -c
```

Giá trị `-1` nghĩa là key sống vĩnh viễn. Trong codebase hiện tại chỉ hai key
version (`channels:cache:version`, `rbac:cache:version`) được cố ý đặt
`timeout=None`; ngoài hai key đó mà thấy `-1` là bug.

**Hit rate ở tầng ứng dụng** (có từ MR này trở đi, tách theo thao tác):

```sh
Q='sum by (result) (rate(desi_cache_operations_total{operation="get"}[10m]))'
curl -sG localhost:9092/api/v1/query --data-urlencode "query=$Q" | jq -r '.data.result[]|"\(.metric.result) \(.value[1])"'
```

```sh
Q='sum(rate(desi_cache_operations_total{result="error"}[10m]))'
curl -sG localhost:9092/api/v1/query --data-urlencode "query=$Q" | jq -r '.data.result[]|.value[1]'
```

`result="error"` khác 0 nghĩa là Redis cache đang trục trặc và hệ thống đang chạy
ở chế độ không cache — chậm nhưng còn sống. Đây là tín hiệu phải xử lý ngay.

## §5 — Phân rã thời gian bên trong một request

Backend đã log sẵn ba họ timing. Đọc trực tiếp từ container (nhanh nhất, nhưng
mất khi container bị recreate):

```sh
docker logs --since 30m desi-web 2>&1 | grep list-timing | tail -20
```

```sh
docker logs --since 30m desi-web 2>&1 | grep create-timing | tail -20
```

```sh
docker logs --since 30m desi-web 2>&1 | grep landing-timing | tail -20
```

Đọc số `list-timing`: `count` gần 0 nghĩa là cache count đang ăn; `page` là chi
phí query; `serialize` là chi phí CPU Python. Tổng ba phần cho biết nên tối ưu
tầng nào — cache thêm không cứu được một `serialize` 460ms.

Log cũ hơn (container đã recreate) nằm ở Loki, giữ 14 ngày. Loki không mở ra
host nên gọi từ trong container:

```sh
L='http://localhost:3100/loki/api/v1/query_range'
```

```sh
docker exec desi-loki wget -qO- "$L?query={container=\"desi-web\"}|=\`list-timing\`&limit=50"
```

Lưu ý múi giờ: server prod chạy **+07**, không phải UTC — đối chiếu giờ trong log
với giờ nghiệp vụ theo giờ Việt Nam.

## §6 — Celery: job nền có đè giờ hành chính không

```sh
docker exec desi-beat celery -A config inspect active --timeout 10
```

Độ sâu hàng đợi đọc thẳng từ Redis broker (DB 3):

```sh
$R -n 3 LLEN celery
```

Liệt kê mọi hàng đợi đang có việc:

```sh
$R -n 3 --scan --pattern '*' | head -20
```

Xem có phải gai tải rơi đúng mốc phút chẵn không:

```sh
Q='sum(rate(celery_task_runtime_seconds_sum[5m]))'
curl -sG localhost:9092/api/v1/query --data-urlencode "query=$Q" | jq -r '.data.result[]|.value[1]'
```

Nếu đồ thị của query trên có răng cưa đều đặn mỗi 15 phút thì đúng là phát hiện
2.6 (beat dồn mốc phút chẵn).

## §7 — Frontend: người dùng chờ bao lâu ở trình duyệt

Kích thước bundle thật đang phục vụ:

```sh
docker exec desi-frontend du -sh /usr/share/nginx/html/assets
```

```sh
docker exec desi-frontend ls -lSh /usr/share/nginx/html/assets | head -10
```

Kiểm tra sourcemap có bị đẩy lên prod không (phát hiện 2.5):

```sh
docker exec desi-frontend find /usr/share/nginx/html -name '*.map' | head
```

Kiểm tra header cache của asset (thay tên file bằng file thật ở lệnh trên):

```sh
curl -sI https://delax.com.vn/assets/index-ByvYMspt.js | grep -i -E 'cache-control|content-encoding'
```

Số request mỗi lần mở trang: đo bằng DevTools → Network → lọc `Fetch/XHR`, mở
trang Đơn hàng, đếm. Con số này quyết định nhiều hơn bundle size vì mỗi request
đều đi qua gate concurrency.

## §8 — Verify cutover nén (bước 2 của mục 3.3 trong doc thiết kế)

Trước khi tắt `WEB_GZIP_IN_DJANGO`, xác nhận nginx trên host đã có `gzip_proxied`:

```sh
grep -rn gzip_proxied /etc/nginx/
```

```sh
nginx -t && systemctl reload nginx
```

Sau khi đặt `WEB_GZIP_IN_DJANGO=0` và restart web, response API **phải** vẫn có
`content-encoding: gzip`:

```sh
curl -sI -H 'Accept-Encoding: gzip' https://delax.com.vn/api/health | grep -i content-encoding
```

Không thấy dòng `content-encoding: gzip` → trả `WEB_GZIP_IN_DJANGO=1` và restart
web ngay, rồi sửa nginx.

## §9 — Bảng kết quả (điền sau khi chạy)

| Route | giây/giây (§1) | p95 (§1) | req/s (§1) | Ghi chú |
|---|---|---|---|---|
| | | | | |

| Chỉ số cache | Giá trị | Ngày đo |
|---|---|---|
| `keyspace_hits / (hits+misses)` | | |
| `used_memory_human` (instance cache) | | |
| `evicted_keys` trong 1h | | |
| hit rate ứng dụng (§4) | | |

| Chỉ số DB | Giá trị | Ngày đo |
|---|---|---|
| `buffer_hit_ratio` | | |
| Top 1 query theo `total_ms` | | |
| `avg_wait_time` PgBouncer | | |
