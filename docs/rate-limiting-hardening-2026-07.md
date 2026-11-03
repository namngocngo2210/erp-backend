# Rate limiting toàn hệ thống — thiết kế & triển khai (2026-07-28)

## 1. Hiện trạng trước thay đổi

| Lớp | Có gì | Đánh giá |
|---|---|---|
| Cloudflare | WAF / bot fight | Không phải rate limit theo route (gói hiện tại không bật Rate Limiting Rules) |
| nginx (host prod + container local) | **Không có `limit_req` nào** | Mọi request rác đều tiêu một worker gunicorn rồi mới bị app từ chối |
| DRF | 3 scope: `login` 10/min, `warranty_register` 20/hour, `order_print` 120/min | Chỉ 6 endpoint trên ~200 endpoint có trần |
| Outbound KiotViet | `time.sleep(0.3)` hardcode ở 1 management command | Nhiều worker sync song song vẫn có thể đốt hạn mức KiotViet của cả công ty |
| Quan sát | Chỉ suy ra gián tiếp từ `desi_http_requests_total{status="429"}` | Không biết scope nào bị chặn, không có alert |

Điểm đã làm đúng từ trước và được giữ nguyên: `apps/common/throttling.ClientIpMixin`
lấy IP client thật (`CF-Connecting-IP` → `X-Forwarded-For` hop tin cậy → `REMOTE_ADDR`),
và cache backend là **Redis dùng chung** nên counter có hiệu lực trên mọi gunicorn worker.

## 2. Nguyên tắc thiết kế

1. **Bốn lớp, mỗi lớp một mục đích khác nhau** — không lớp nào thay được lớp nào:
   - *Edge (nginx)*: chặn flood theo IP **trước khi** chạm gunicorn. Bảo vệ tài nguyên.
   - *App (DRF)*: trần theo **danh tính** (user/IP thật) cho nghiệp vụ. Bảo vệ dữ liệu & chi phí.
   - *Outbound*: trần **ra ngoài** để không bị KiotViet cắt quyền cả công ty.
   - *Quan sát*: metric + alert để biết mình đang chặn nhầm ai, trước khi người dùng kêu.
2. **Fail-open, không fail-closed.** Redis chết thì hệ thống mất rate limit chứ không được
   trả 500 cho toàn bộ API. Rate limit là lớp phòng thủ, không phải lớp nghiệp vụ.
3. **Mặc định RỘNG, siết bằng env sau khi có số liệu.** Chặn oan nhân viên đắt hơn nhiều
   so với để lọt một script rác trong vài ngày đầu.
4. **Cẩn thận với NAT.** Toàn bộ nhân viên văn phòng ra internet bằng **một IP công cộng**.
   Mọi trần key-theo-IP đều phải đủ rộng cho cả văn phòng, hoặc phải key theo user.

## 3. Thiết kế chi tiết

### 3.1 Lớp edge — nginx `limit_req`

`limit_req_zone` chỉ khai báo được trong block `http`, còn vhost prod là file rời được
`include` vào — nên tách thành 2 phần:

- `nginx/rate-limit-zones.conf.example` → cài vào `/etc/nginx/conf.d/00-rate-limit-zones.conf`
  của **host nginx** (prod/staging). Chứa `map` sinh key + 3 `limit_req_zone`.
- `nginx/nginx.conf` (nginx **container**, dùng ở local) — nhúng thẳng cùng nội dung.
- `limit_req` đặt ở từng `location` của các vhost.

Key chống spoof: `$http_cf_connecting_ip` nếu có, ngược lại `$binary_remote_addr`.
Không dùng `X-Forwarded-For` ở tầng nginx vì client tự đặt được.

| Zone | Rate | Burst | Áp cho | Lý do chọn |
|---|---|---|---|---|
| `api_general` | 20 r/s | 40 nodelay | `location /api/` | Mở một trang ERP bắn ~10–20 request cùng lúc; 20 r/s **duy trì** thì thao tác tay không thể chạm tới. Cả văn phòng chung NAT ⇒ không đặt thấp hơn. |
| `api_auth` | 60 r/m | 20 nodelay | `/api/users/login`, `/api/users/token/refresh` | 9h sáng cả công ty đăng nhập trong vài phút; burst 20 đủ, 1 r/s duy trì thì brute-force không có cửa. |
| `api_ingest` | 30 r/s | 60 nodelay | `/api/orders/ingest` | Landing page đẩy đơn theo cụm; đây là van chống DDoS chứ không phải trần nghiệp vụ. |

`limit_req_status 429` (mặc định nginx là 503) để client thấy đúng ngữ nghĩa và khớp
với 429 của DRF.

**Cố ý KHÔNG áp**:
- `/ws/` — WebSocket là kết nối dài, `limit_req` vô nghĩa; `limit_conn` thì cả văn phòng
  chung một IP sẽ bị cắt nhầm.
- `/api/orders/metrics` — scraper nội bộ, đã chặn bằng `allow/deny`.
- `/` (SPA + assets) — file tĩnh rẻ, chặn nhầm thì trang trắng.

### 3.2 Lớp app — trần mặc định cho MỌI endpoint DRF

Trước đây `REST_FRAMEWORK` chỉ khai báo `DEFAULT_THROTTLE_RATES` mà **không** có
`DEFAULT_THROTTLE_CLASSES`, nên endpoint không tự gắn `@throttle_classes` thì không có
trần nào. Bổ sung 2 class mặc định trong `apps/common/throttling.py`:

- `DefaultUserRateThrottle` — key theo `user.pk`, mặc định `3000/hour` (env `USER_THROTTLE_RATE`).
- `DefaultAnonRateThrottle` — key theo IP client thật, mặc định `600/hour` (env `ANON_THROTTLE_RATE`).

**Thuật toán: cửa sổ cố định (fixed window) chứ không dùng `SimpleRateThrottle` của DRF.**
`SimpleRateThrottle` lưu **danh sách timestamp** của từng request trong cache: ở mức
3000/hour nghĩa là mỗi user giữ một list 3000 phần tử, **đọc + ghi lại toàn bộ list trên
mỗi request**. Với 100–150 nhân sự thì đó là vài chục MB Redis và một pickle ~50KB đi
qua network mỗi request — trần mặc định không được phép đắt hơn chính công việc nó bảo vệ.
Cửa sổ cố định chỉ tốn 2 lệnh Redis (`SET NX` + `INCR`) và O(1) bộ nhớ.

Đánh đổi đã biết: cửa sổ cố định cho phép gấp đôi rate ở ranh giới cửa sổ (cuối cửa sổ N
+ đầu cửa sổ N+1). Chấp nhận được vì đây là **trần chống chạy loạn**, không phải hạn ngạch
tính tiền. Ba scope nghiệp vụ (`login`, `warranty_register`, `order_print`) giữ nguyên
`SimpleRateThrottle` — rate thấp nên chi phí list không đáng kể, và đổi thuật toán ở đó
là thay đổi hành vi bảo mật đã được test.

**Danh sách miễn trừ** (`THROTTLE_EXEMPT_VIEWS`, khớp theo `resolver_match.view_name`):

| View | Vì sao miễn |
|---|---|
| `orders-metrics` | Prometheus scrape 15s/lần = 240 req/h, vượt trần anon |
| `orders-ingest`, `orders-lark-ingest` | Máy-tới-máy, đã auth bằng token riêng, lưu lượng theo cụm |
| `warranty-ingest` | Máy-tới-máy |
| `best-express-webhook`, `kiotviet-invoice-webhook` | Webhook của đối tác, họ retry theo cụm |
| `users-token-refresh` | **Quan trọng**: cả văn phòng chung một IP, mỗi người refresh ~4 lần/giờ ⇒ 150 nhân sự = 600 req/h chạm đúng trần anon và **đá toàn bộ công ty ra khỏi phiên**. Flood ở đây đã có zone `api_auth` của nginx lo. |

Endpoint tự khai `@throttle_classes` (login/warranty/print) **ghi đè** mặc định — DRF
không cộng dồn — nên không có chuyện bị đếm hai lần.

**Fail-open**: mọi lỗi cache trong đường throttle đều được nuốt + `logger.exception`, và
request được cho qua.

### 3.3 Lớp outbound — token bucket dùng chung cho KiotViet

`apps/common/rate_limiter.py` — cửa sổ cố định trên Redis (cùng primitive với 3.2), API
`acquire(key, limit, window, max_wait)`: hết quota thì **ngủ tới đầu cửa sổ sau** rồi thử
lại, quá `max_wait` thì cho đi kèm cảnh báo (fail-open — chặn hẳn sẽ làm hỏng luồng đồng bộ).

Cắm vào `_kiotviet_request` — choke point duy nhất của KiotViet Public API — key theo
**retailer** (hạn mức của KiotViet tính theo gian hàng, không theo tiến trình của ta).
Mặc định `KIOTVIET_RATE_LIMIT_PER_SECOND=10`, `KIOTVIET_RATE_LIMIT_MAX_WAIT=10`.
Thay luôn `time.sleep(0.3)` hardcode trong `fetch_kiotviet_locations`.

### 3.4 Lớp quan sát

- `desi_throttled_requests_total{scope}` — tăng mỗi lần DRF từ chối (mọi scope, kể cả mặc định).
- `desi_outbound_rate_limit_waits_total{service}` / `..._wait_seconds_total{service}` —
  ta đang tự chờ bao lâu trước khi gọi KiotViet.
- `desi_outbound_rate_limit_exceeded_total{service}` — chạm trần `max_wait`, tức limiter
  đang đặt quá chặt hoặc có job chạy loạn.
- Alert trong `monitoring/alerts.yml`: `ApiThrottleSpike` (chặn nhiều bất thường),
  `LoginThrottleSpike` (dấu hiệu credential stuffing), `OutboundRateLimitSaturated`.

### 3.5 Frontend

DRF trả 429 kèm `detail` **tiếng Anh** ("Request was throttled. Expected available in
N seconds.") và FE hiện hiển thị nguyên văn `detail` cho người dùng. `services/http.ts`
dịch 429 sang thông báo tiếng Việt kèm số giây chờ đọc từ header `Retry-After`.

## 4. Những gì cố ý KHÔNG làm

- **Không** đụng rate của `login`/`warranty_register`/`order_print` — chỉ đưa `login` ra env
  để siết/nới không cần deploy.
- **Không** phủ throttle cho `api/v2` (django-ninja): DRF throttle không chạy ở đó. Hiện
  router v2 gần như rỗng; khi nào đẩy nghiệp vụ thật sang v2 thì làm middleware riêng.
  *(Đính chính 2026-08-20: django-ninja + mount `api/v2` đã gỡ — audit 2026-08-19 mục 4.12
  — nên mục này hết hiệu lực.)*
- **Không** dùng `limit_conn` (lý do NAT ở §3.1).
- **Không** đổi `login` sang key theo username — cần thiết kế riêng chống user-enumeration.

## 5. Rủi ro đã nhận diện

| Rủi ro | Xử lý |
|---|---|
| Trần anon chặn nhầm cả văn phòng chung NAT | Miễn trừ `users-token-refresh`; đặt 600/hour; alert `ApiThrottleSpike` phát hiện trong 10 phút |
| Redis chết ⇒ 500 toàn hệ thống | Fail-open ở cả 3 đường (default throttle, scoped throttle, outbound) |
| nginx zone quá chặt ⇒ 429 hàng loạt | Burst nodelay rộng + `limit_req_log_level warn`; rollback = comment 1 dòng `limit_req` rồi `nginx -s reload` |
| Limiter outbound làm chậm in ấn | In 200 id vốn đã là 200 round-trip tuần tự (~40–80s); trần 10 r/s không phải chỗ nghẽn |
| `SimpleRateThrottle` phình Redis | Trần mặc định dùng fixed-window O(1) (§3.2) |

## 6. Sprint & tiến độ

### Sprint 1 — Edge (nginx) ✅ hoàn thành
- [x] `nginx/rate-limit-zones.conf.example` (zones cho host nginx)
- [x] `nginx/nginx.conf` — map + 3 zone cho nginx container
- [x] `nginx/conf.d/default.conf`, `nginx/default.conf` — `limit_req` theo location
- [x] `nginx/customer.delax.vn.conf.prod-template`, `nginx/customer.delax.vn.host.conf.example`
- [x] `docs/deploy.md` — bước cài file zones lên host
- [x] Kiểm chứng bằng `nginx -t` trên image `nginx:1.27-alpine`

### Sprint 2 — App (DRF default throttle) ✅ hoàn thành
- [x] `apps/common/rate_limiter.py` — fixed-window + fail-open + metrics
- [x] `apps/common/throttling.py` — `DefaultUserRateThrottle`, `DefaultAnonRateThrottle`, miễn trừ, metric, fail-open cho `ClientIpMixin`
- [x] `config/settings_parts/base.py` — `DEFAULT_THROTTLE_CLASSES` + env
- [x] Test: `apps/common/tests/test_rate_limiter.py`, mở rộng `test_throttling.py`

### Sprint 3 — Outbound KiotViet ✅ hoàn thành
- [x] Cắm limiter vào `_kiotviet_request` (key theo retailer)
- [x] Thay `time.sleep(0.3)` trong `fetch_kiotviet_locations`
- [x] Test: `apps/kiotviet/tests/test_outbound_rate_limit.py`

### Sprint 4 — Quan sát + FE ✅ hoàn thành
- [x] Metric throttle (app + outbound)
- [x] Alert `ApiThrottleSpike`, `LoginThrottleSpike`, `OutboundRateLimitSaturated`
- [x] FE: thông báo 429 tiếng Việt kèm `Retry-After`
- [x] `.env.example` — toàn bộ biến mới

## 7. Vận hành

### Bật/siết không cần deploy code
```bash
USER_THROTTLE_RATE=3000/hour        # trần mặc định theo user
ANON_THROTTLE_RATE=600/hour         # trần mặc định theo IP
LOGIN_THROTTLE_RATE=10/min          # giữ nguyên giá trị cũ, nay tinh chỉnh được
ORDER_PRINT_THROTTLE_RATE=120/min
THROTTLE_EXEMPT_VIEWS=view-a,view-b # THÊM vào danh sách mặc định
KIOTVIET_RATE_LIMIT_PER_SECOND=10
KIOTVIET_RATE_LIMIT_MAX_WAIT=10
```
Đặt rate rỗng (`USER_THROTTLE_RATE=`) để **tắt hẳn** trần đó.

### Cài zones lên host nginx (prod/staging)
```bash
sudo cp nginx/rate-limit-zones.conf.example /etc/nginx/conf.d/00-rate-limit-zones.conf
sudo nginx -t && sudo systemctl reload nginx
```
Tên file bắt đầu bằng `00-` để zone được khai báo trước khi vhost dùng tới.

### Xác nhận sau khi deploy
```promql
sum by (scope) (rate(desi_throttled_requests_total[5m]))     # ai đang bị chặn
sum by (service) (rate(desi_outbound_rate_limit_waits_total[5m]))
```
Tuần đầu: nếu `scope="anon"` hoặc `scope="user"` khác 0 một cách đều đặn ⇒ đang chặn oan,
nới env ngay rồi mới điều tra.

## 8. Việc còn nợ (đề xuất cho sprint sau)

1. **`login` 10/min key theo IP là rủi ro có sẵn từ trước**: cả văn phòng chung một IP
   công cộng, 9h sáng hơn 10 người đăng nhập trong một phút sẽ bị chặn nhầm. Đây là
   hành vi đã có trước thay đổi này nên không sửa lẫn vào đây, nhưng nên chuyển sang
   key kép `(IP, username)` — chống brute-force theo username mà không phạt cả văn phòng.
2. **Rate limiting cho `api/v2` (django-ninja)** khi có nghiệp vụ thật.
3. **Cloudflare Rate Limiting Rules** cho `/api/users/login` — chặn ở tầng CF thì tài
   nguyên VPS không bị chạm tới; cần nâng gói.
4. **Grafana dashboard riêng cho throttle** (hiện chỉ có alert).
