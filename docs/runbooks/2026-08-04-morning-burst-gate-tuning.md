# 2026-08-04 09:13 — Bão gate 503 buổi sáng: tuning gate + batch audit

## Bối cảnh

Burst tạo đơn giờ hành chính sáng 04/08 (09:13–09:14) — "bài thi thật" đầu tiên
sau PgBouncer cutover chiều 03/08. Kết quả:

- **PgBouncer đậu:** không một dòng FATAL "too many clients"; Postgres vô sự.
- **Gate đậu nửa bài:** giữ đúng thiết kế (xếp hàng 15s rồi 503) nhưng người
  dùng vẫn ăn 503 hàng loạt, trong đó có `POST /api/users/token/refresh` →
  nguy cơ văng phiên giữa giờ cao điểm, user retry dồn dập tự khuếch đại bão.
- Bệnh gốc không đổi (chẩn đoán 03/08 13:46): p95 POST /api/orders lúc yên bình
  đã 2.3s; 36 thread tranh 1.5 CPU của desi-web lúc burst → request phình
  15–30s → slot không nhả → tràn queue.

## Thay đổi trong MR này

1. **`WEB_HTTP_CONCURRENCY` 12→8** (default ở `base.py` + `docker-compose.prod.yml`).
   Connection đã có PgBouncer cap; nút cổ chai là CPU. 24 thread thay vì 36:
   mỗi request xong nhanh hơn, slot quay vòng nhanh hơn.
2. **Bypass gate cho `/api/users/token/refresh`** (`config/asgi.py`).
   Refresh rẻ (verify JWT + write nhỏ); mất phiên đắt hơn nhiều. KHÔNG bypass
   login (`/api/users/token`) — hash mật khẩu nặng CPU.
3. **Batch audit task** — `write_audit` gom entry per-process, đầy
   `AUDIT_BATCH_SIZE` (20) thì enqueue 1 task `audit.create_batch`, batch lửng
   được timer flush sau `AUDIT_BATCH_MAX_DELAY_SECONDS` (2s). Cắt ~260 task/phút
   trên queue audit lúc burst xuống vài task; worker ghi bằng `bulk_create`.
   Van thoát: `AUDIT_BATCH_SIZE=1` = hành vi cũ. Đánh đổi: process chết đột
   ngột mất tối đa 1 batch (≤20 dòng / ≤2s) audit log.

4. **Dập herd refetch phía FE** (`use-crm-websocket.ts`) — số liệu Loki/Prometheus
   sau bão cho thấy POST tạo đơn chỉ ~0.3–0.9s (không phải thứ giữ slot); gate
   chết vì cú "đồng loạt": 10+ request cùng timeout trong 1 giây = N tab cùng
   refetch một nhịp khi có event. Ba lỗ đã vá:
   - Dashboard `loadSummary({force})` trước đây bắn TỨC THÌ mỗi `order.created`
     × mỗi tab dashboard → nay đi qua cùng coalesce+jitter (0.5–2s).
   - Transfer landing→orders trước đây GET detail từng đơn tức thì (bulk M đơn
     × N tab = M×N request cùng nhịp) → nay đi qua coalescer của order.created.
   - Tab ẨN không fetch nữa: event gom vào pending, chỉ flush (kèm jitter) khi
     tab visible trở lại — phần lớn bầy đàn là tab nền không ai nhìn.
   **Cần deploy cả frontend** thì fix này mới ăn.

## Deploy

```sh
cd ~/desi-order
git pull
grep WEB_HTTP_CONCURRENCY .env
```

- Nếu `.env` host CÓ đặt `WEB_HTTP_CONCURRENCY` → sửa thành 8 (env override
  compose default).
- Deploy web + worker (worker cần image mới để có task `audit.create_batch`
  TRƯỚC khi web bắt đầu enqueue nó — deploy worker trước, web sau):

```sh
scripts/deploy-prod.sh
docker logs desi-worker --tail 20 | grep -i celery
docker logs desi-web --tail 5
```

- Xác nhận trần mới trong container:

```sh
docker exec desi-web env | grep WEB_HTTP_CONCURRENCY
```

## Lấy số create-timing của bão 09:13 (việc còn nợ)

Instrument create-timing live từ deploy 03/08 ~16:15 — bão sáng 04/08 là lần
đầu có số đo trong bão thật. Chạy trên VPS prod (mỗi dòng < 110 ký tự, tránh
bẫy vỡ dòng khi paste):

```sh
cd ~/desi-order
docker logs desi-web --since 2026-08-04T09:05:00 --until 2026-08-04T09:25:00 \
  2>&1 | grep create-timing > /tmp/ct-0804.log
wc -l /tmp/ct-0804.log
sort -t= -k5 -rn /tmp/ct-0804.log | head -20
```

Nếu docker logs đã bị xoay (container restart), lấy từ Loki qua Grafana
(SSH tunnel `ssh -L 3001:127.0.0.1:3001`), query:
`{container="desi-web"} |= "create-timing"` khung 09:05–09:25.

Đọc số: `validate` vs `service` vs `serialize` trong bão — khúc nào phình
mạnh nhất so với nền (validate 162–419ms, service 228–357ms, serialize ~55ms)
là khúc cần tối ưu ở MR kế tiếp.

Kiểm tra thêm (đối chứng):

```sh
curl -s localhost:9092/api/v1/query \
  --data-urlencode 'query=desi_http_gate_timeouts_total' | head -c 600
```

- Counter `desi_http_gate_timeouts_total` (theo path_prefix) phải có số của
  bão 09:13 — nếu rỗng nghĩa là image web đang chạy chưa có counter
  (nhánh ece5016d), deploy lần này sẽ vá luôn.
- PgBouncer giữ trần: Prometheus `pg_stat_activity_count` quanh 09:13 phải
  phẳng (~14 conn), không có spike.

## Sau deploy — theo dõi

- Bài thi kế tiếp: burst sáng 05/08. Kỳ vọng: ít 503 hơn hẳn (slot quay vòng
  nhanh), refresh token không còn 503, queue audit không còn bão task.
- Nếu 503 vẫn dày: đọc create-timing trong bão (ở trên) → tối ưu đúng khúc
  (nghi chính: validate của OrderCreateSerializer — 3 related-field query +
  CPU). Đó là MR "hạ p95 < 0.5s", làm sau khi có số.

## ĐÍNH CHÍNH chiều 04/08 — đọc trước khi trực bão 05/08

Mổ bão 09:13 bằng Loki cho thấy chẩn đoán trên SAI HƯỚNG một nửa:
`POST /api/orders` trong bão là **fetch DANH SÁCH** (list_orders nhận POST filter
body), không phải tạo đơn — tạo tay là `/api/orders/create` và cả bão không có
đơn tạo tay nào (0 dòng create-timing 09:05–09:25). Thủ phạm là fanout
`order.created` → N tab reload list nặng theo sóng ~30s (44 rồi 40 POST/10s).
Chi tiết + fix (cache status_counts, sàn nhịp 10s/tab, backoff 503, log
`list-timing`): xem `docs/order-list-fanout-hardening-2026-08-04.md`.
Sáng 05/08 đọc `list-timing` (thay cho create-timing) trong khung burst.
